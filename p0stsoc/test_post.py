"""Claim / post_article: one article, one Facebook call.

Run: python -m p0stsoc.test_post
"""
from unittest.mock import MagicMock, patch

from p0stsoc import db, facebook
from p0stsoc.cli import post_article


def _conn():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    db.set_setting(conn, "fb_page_id", "page1")
    db.upsert_keyword(conn, "Tesla")
    kid = db.list_keywords(conn)[0]["id"]
    db.insert_article(
        conn, "guid-1", kid, "Titolo", "https://news.google.com/x",
        "Fonte", "today", "snippet", status="approved")
    conn.commit()
    return conn


def test_claim_is_exclusive():
    conn = _conn()
    aid = db.list_articles(conn, "approved")[0]["id"]
    assert db.claim_for_post(conn, aid) is True
    assert db.claim_for_post(conn, aid) is False
    assert db.get_article(conn, aid)["status"] == "posting"
    db.release_claim(conn, aid)
    assert db.get_article(conn, aid)["status"] == "approved"
    assert db.claim_for_post(conn, aid) is True


def test_second_post_skips_facebook():
    conn = _conn()
    a = db.list_articles(conn, "approved")[0]
    with patch("p0stsoc.cli.news.resolve_url", side_effect=lambda u: u), \
         patch("p0stsoc.cli.facebook.post_link", return_value="fb_1") as fb, \
         patch("p0stsoc.cli.config.get_token", return_value="tok"):
        assert post_article(conn, a) == "fb_1"
        assert fb.call_count == 1
        try:
            post_article(conn, a)
            assert False, "expected RuntimeError on second post"
        except RuntimeError as e:
            assert "not postable" in str(e)
        assert fb.call_count == 1   # Graph API not called again
    assert db.get_article(conn, a["id"])["status"] == "posted"


def test_post_survives_braces_in_title():
    conn = _conn()
    aid = db.list_articles(conn, "approved")[0]["id"]
    conn.execute("UPDATE articles SET title=? WHERE id=?", ("Foo {bar} {url}", aid))
    conn.commit()
    a = db.get_article(conn, aid)
    with patch("p0stsoc.cli.news.resolve_url", side_effect=lambda u: u), \
         patch("p0stsoc.cli.facebook.post_link", return_value="fb_1") as fb, \
         patch("p0stsoc.cli.config.get_token", return_value="tok"):
        assert post_article(conn, a) == "fb_1"
        message = fb.call_args[0][2]
        assert message.startswith("Foo {bar} {url}")


def test_failed_post_releases_claim():
    conn = _conn()
    a = db.list_articles(conn, "approved")[0]
    with patch("p0stsoc.cli.news.resolve_url", side_effect=lambda u: u), \
         patch("p0stsoc.cli.facebook.post_link", side_effect=RuntimeError("FB down")), \
         patch("p0stsoc.cli.config.get_token", return_value="tok"):
        try:
            post_article(conn, a)
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "FB down" in str(e)
    assert db.get_article(conn, a["id"])["status"] == "approved"
    # can retry
    with patch("p0stsoc.cli.news.resolve_url", side_effect=lambda u: u), \
         patch("p0stsoc.cli.facebook.post_link", return_value="fb_2") as fb, \
         patch("p0stsoc.cli.config.get_token", return_value="tok"):
        assert post_article(conn, a) == "fb_2"
        assert fb.call_count == 1


def test_requeue_only_stale():
    conn = _conn()
    aid = db.list_articles(conn, "approved")[0]["id"]
    assert db.claim_for_post(conn, aid)
    # fresh claim (web console may be mid-post): must NOT be requeued
    assert db.requeue_posting(conn) == 0
    assert db.get_article(conn, aid)["status"] == "posting"
    # backdate the claim -> looks like a crashed post
    conn.execute("UPDATE articles SET posting_since = datetime('now', '-1 hour') WHERE id=?", (aid,))
    conn.commit()
    assert db.requeue_posting(conn) == 1
    assert db.get_article(conn, aid)["status"] == "approved"


def test_prune_keeps_live_and_recent():
    conn = _conn()                                 # 'guid-1' is approved (live)
    kid = db.list_keywords(conn)[0]["id"]
    for g, st in [("old-posted", "posted"), ("old-skipped", "skipped")]:
        db.insert_article(conn, g, kid, "T", "u", "s", "p", "sn", status=st)
    conn.execute("UPDATE articles SET created_at = datetime('now','-200 days') "
                 "WHERE guid IN ('old-posted','old-skipped')")
    db.insert_article(conn, "new-posted", kid, "T", "u", "s", "p", "sn", status="posted")
    conn.commit()
    assert db.prune_articles(conn, 90) == 2         # only the two old terminal rows
    left = {r["guid"] for r in db.list_articles(conn)}
    assert "guid-1" in left and "new-posted" in left            # live + recent kept
    assert "old-posted" not in left and "old-skipped" not in left


def test_set_status_guards_stale():
    conn = _conn()                                 # 'guid-1' is approved
    aid = db.list_articles(conn, "approved")[0]["id"]
    assert db.set_status(conn, aid, "skipped") is True          # approved -> skipped ok
    # a stale console page must not drag a row that already left the queue back in
    for terminal in ("skipped", "posting", "posted"):
        conn.execute("UPDATE articles SET status=? WHERE id=?", (terminal, aid))
        conn.commit()
        assert db.set_status(conn, aid, "approved") is False
        assert db.get_article(conn, aid)["status"] == terminal


def test_update_keyword_renames_in_place():
    conn = _conn()                                 # keyword 'Tesla'
    kid = db.list_keywords(conn)[0]["id"]
    db.update_keyword(conn, kid, query="Tesla Motors", exclude=["Nikola"], mode="auto")
    rows = db.list_keywords(conn)
    assert len(rows) == 1                          # renamed, NOT duplicated
    assert rows[0]["query"] == "Tesla Motors" and rows[0]["mode"] == "auto"


def test_init_schema_adds_posting_since():
    """Old DBs created before posting_since must migrate on init_schema.

    cmd_post now calls init_schema so a post-only run on a pre-claim DB
    does not blow up in requeue_posting.
    """
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE articles (id INTEGER PRIMARY KEY, guid TEXT UNIQUE, "
        "status TEXT, created_at TEXT);"
    )
    db.init_schema(conn)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(articles)")]
    assert "posting_since" in cols


def test_list_articles_unlimited():
    conn = _conn()
    kid = db.list_keywords(conn)[0]["id"]
    for i in range(5):
        db.insert_article(conn, f"g-{i}", kid, "T", "u", "s", "p", "sn", status="approved")
    conn.commit()
    assert len(db.list_articles(conn, "approved", limit=2)) == 2
    assert len(db.list_articles(conn, "approved", limit=None)) >= 6  # guid-1 + 5


def test_fb_accepts_non_json_body():
    resp = MagicMock(ok=True)
    resp.json.side_effect = ValueError("not json")
    with patch("p0stsoc.facebook.requests.post", return_value=resp):
        assert facebook.post_link("page", "tok", "msg", "http://x") == ""


if __name__ == "__main__":
    test_claim_is_exclusive()
    test_second_post_skips_facebook()
    test_post_survives_braces_in_title()
    test_failed_post_releases_claim()
    test_requeue_only_stale()
    test_prune_keeps_live_and_recent()
    test_set_status_guards_stale()
    test_update_keyword_renames_in_place()
    test_init_schema_adds_posting_since()
    test_list_articles_unlimited()
    test_fb_accepts_non_json_body()
    print("ok")
