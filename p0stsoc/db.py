"""SQLite storage: keywords, articles queue, settings. No ORM."""
import json
import os
import sqlite3

DB_PATH = os.environ.get("P0STSOC_DB", "p0stsoc.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY,
    query TEXT NOT NULL,
    exclude TEXT DEFAULT '[]',      -- JSON list of phrases
    mode TEXT,                      -- review | auto | NULL (=inherit default_mode)
    lang TEXT DEFAULT 'it',
    country TEXT DEFAULT 'IT',
    enabled INTEGER DEFAULT 1,
    UNIQUE(query, lang, country)
);

CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    guid TEXT UNIQUE,               -- rss guid/link: dedup
    keyword_id INTEGER,
    title TEXT, url TEXT, source TEXT, published TEXT, snippet TEXT,
    status TEXT DEFAULT 'new',      -- new | approved | posting | posted | skipped
    fb_post_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    posted_at TEXT,
    posting_since TEXT              -- when claim_for_post set 'posting' (stale detection)
);

CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);

CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
"""


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn):
    conn.executescript(SCHEMA)
    # migrate DBs created before posting_since existed
    cols = [r[1] for r in conn.execute("PRAGMA table_info(articles)")]
    if "posting_since" not in cols:
        conn.execute("ALTER TABLE articles ADD COLUMN posting_since TEXT")
    conn.commit()


# --- settings ---
def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)))
    conn.commit()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


# --- keywords ---
def upsert_keyword(conn, query, exclude=None, mode=None, lang="it", country="IT", enabled=1):
    conn.execute(
        "INSERT INTO keywords(query,exclude,mode,lang,country,enabled) "
        "VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(query,lang,country) DO UPDATE SET "
        "exclude=excluded.exclude, mode=excluded.mode, enabled=excluded.enabled",
        (query, json.dumps(exclude or []), mode, lang, country, int(enabled)))
    conn.commit()


def update_keyword(conn, kid, query, exclude=None, mode=None, lang="it", country="IT", enabled=1):
    """Update a keyword in place by id — rename-safe (no duplicate row).

    Raises sqlite3.IntegrityError if (query,lang,country) collides with another
    keyword; the caller should surface that rather than 500.
    """
    conn.execute(
        "UPDATE keywords SET query=?, exclude=?, mode=?, lang=?, country=?, enabled=? "
        "WHERE id=?",
        (query, json.dumps(exclude or []), mode, lang, country, int(enabled), kid))
    conn.commit()


def list_keywords(conn, only_enabled=False):
    sql = "SELECT * FROM keywords"
    if only_enabled:
        sql += " WHERE enabled=1"
    return conn.execute(sql + " ORDER BY query").fetchall()


def get_keyword(conn, kid):
    return conn.execute("SELECT * FROM keywords WHERE id=?", (kid,)).fetchone()


def delete_keyword(conn, kid):
    conn.execute("DELETE FROM keywords WHERE id=?", (kid,))
    conn.commit()


# --- articles ---
def insert_article(conn, guid, keyword_id, title, url, source, published, snippet, status="new"):
    """Insert, ignoring duplicates by guid. Returns True if a new row was inserted.

    Does NOT commit — the caller batches commit() per fetch.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO articles"
        "(guid,keyword_id,title,url,source,published,snippet,status) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (guid, keyword_id, title, url, source, published, snippet, status))
    return cur.rowcount > 0


def list_articles(conn, status=None, limit=200):
    """Newest first. `limit=None` returns the whole match (used by `post`)."""
    base = ("SELECT a.*, k.query AS keyword FROM articles a "
            "LEFT JOIN keywords k ON k.id = a.keyword_id ")
    where = "WHERE a.status=? " if status else ""
    order = "ORDER BY a.id DESC"
    sql = base + where + order
    if limit is None:
        args = (status,) if status else ()
        return conn.execute(sql, args).fetchall()
    sql += " LIMIT ?"
    args = (status, limit) if status else (limit,)
    return conn.execute(sql, args).fetchall()


def get_article(conn, aid):
    return conn.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone()


def set_status(conn, aid, status):
    """Move an article to a new status only if it is still in the queue.

    Guards against a stale console page approving/skipping a row that already
    moved on (posting/posted/skipped) — which would double-post or lose the
    post id. Returns True if the row was actually changed.
    """
    cur = conn.execute(
        "UPDATE articles SET status=? WHERE id=? AND status IN ('new','approved')",
        (status, aid))
    conn.commit()
    return cur.rowcount > 0


def claim_for_post(conn, aid):
    """Atomically take an article for posting. True if this caller owns it.

    Only new/approved can be claimed — prevents double-post from overlapping
    cron, web "Posta ora", or a second click on the same row.
    """
    cur = conn.execute(
        "UPDATE articles SET status='posting', posting_since=CURRENT_TIMESTAMP "
        "WHERE id=? AND status IN ('approved', 'new')",
        (aid,))
    conn.commit()
    return cur.rowcount > 0


def release_claim(conn, aid):
    """FB/network failed after claim: put back in the approved queue for retry."""
    conn.execute(
        "UPDATE articles SET status='approved' WHERE id=? AND status='posting'",
        (aid,))
    conn.commit()


def requeue_posting(conn, stale_seconds=900):
    """Recover rows stuck in 'posting' after a crash mid-post.

    Only rows claimed more than `stale_seconds` ago are reset. A fresh claim
    that another poster is actively working on — the web console "Posta ora"
    is NOT under the CLI post lock — is left alone; otherwise it would be
    requeued here and double-posted. Returns how many rows moved to approved.
    """
    cur = conn.execute(
        "UPDATE articles SET status='approved' "
        "WHERE status='posting' "
        "AND (posting_since IS NULL OR posting_since <= datetime('now', ?))",
        (f"-{int(stale_seconds)} seconds",))
    conn.commit()
    return cur.rowcount


def mark_posted(conn, aid, fb_post_id):
    cur = conn.execute(
        "UPDATE articles SET status='posted', fb_post_id=?, "
        "posted_at=CURRENT_TIMESTAMP WHERE id=? AND status='posting'",
        (fb_post_id, aid))
    conn.commit()
    if cur.rowcount == 0:
        raise RuntimeError(f"article #{aid} was not in 'posting' state at mark_posted")


def prune_articles(conn, days):
    """Delete terminal (posted/skipped) rows older than `days`, to bound growth.

    Leaves the live queue (new/approved/posting) untouched. Returns rows deleted.
    Freed pages are reused by future inserts, so the file plateaus (no VACUUM).
    """
    cur = conn.execute(
        "DELETE FROM articles "
        "WHERE status IN ('posted', 'skipped') "
        "AND created_at <= datetime('now', ?)",
        (f"-{int(days)} days",))
    conn.commit()
    return cur.rowcount
