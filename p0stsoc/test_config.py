"""Self-check for YAML import sync and post template rendering.

Run: python -m p0stsoc.test_config
"""
import os
import tempfile

import yaml

from p0stsoc import config, db


def _conn():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    return conn


def _write_yaml(data):
    fd, path = tempfile.mkstemp(suffix=".yaml")
    os.close(fd)
    with open(path, "w") as f:
        yaml.safe_dump(data, f, allow_unicode=True)
    return path


def test_render_post_ignores_braces_in_values():
    out = config.render_post(
        "{title}\n{url}", title="Foo {bar}", url="http://x", source="")
    assert out == "Foo {bar}\nhttp://x"
    # values are never re-scanned: a title containing {url} stays literal
    out = config.render_post(
        "{title}", title="has {url} in title", url="http://x", source="")
    assert out == "has {url} in title"


def test_render_post_rejects_unknown_placeholder():
    for bad in ("{titel}", "{{title}}", "{title:.100}", "{title"):
        try:
            config.render_post(bad, title="t", url="u")
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_import_upserts_without_deleting():
    conn = _conn()
    db.upsert_keyword(conn, "Keep")
    db.upsert_keyword(conn, "Stay")
    path = _write_yaml({
        "keywords": [{"query": "Keep", "lang": "it", "country": "IT"}],
    })
    try:
        config.import_yaml(conn, path)
    finally:
        os.remove(path)
    assert {r["query"] for r in db.list_keywords(conn)} == {"Keep", "Stay"}


def test_import_replace_deletes_missing_keywords():
    conn = _conn()
    db.upsert_keyword(conn, "Keep")
    db.upsert_keyword(conn, "Drop")
    path = _write_yaml({
        "keywords": [{"query": "Keep", "lang": "it", "country": "IT"}],
    })
    try:
        config.import_yaml(conn, path, replace_keywords=True)
    finally:
        os.remove(path)
    assert [r["query"] for r in db.list_keywords(conn)] == ["Keep"]


def test_import_rejects_null_keywords():
    conn = _conn()
    db.upsert_keyword(conn, "Stay")
    path = _write_yaml({"keywords": None})
    try:
        try:
            config.import_yaml(conn, path, replace_keywords=True)
            assert False, "expected ValueError"
        except ValueError as e:
            assert "keywords" in str(e)
    finally:
        os.remove(path)
    assert [r["query"] for r in db.list_keywords(conn)] == ["Stay"]


def test_import_without_keywords_key_keeps_existing():
    conn = _conn()
    db.upsert_keyword(conn, "Stay")
    path = _write_yaml({"default_mode": "auto"})
    try:
        config.import_yaml(conn, path)
    finally:
        os.remove(path)
    assert [r["query"] for r in db.list_keywords(conn)] == ["Stay"]
    assert db.get_setting(conn, "default_mode") == "auto"


def test_console_saves_settings():
    from p0stsoc.web import create_app
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        client = create_app(path).test_client()
        r = client.post("/settings", data={
            "default_mode": "auto",
            "fb_page_id": "123",
            "post_template": "{title}\n{url}",
        }, follow_redirects=True)
        assert r.status_code == 200
        conn = db.connect(path)
        assert db.get_setting(conn, "default_mode") == "auto"
        assert db.get_setting(conn, "fb_page_id") == "123"
        conn.close()
        bad = client.post("/settings", data={
            "default_mode": "auto",
            "fb_page_id": "123",
            "post_template": "{titel}",
        }, follow_redirects=True)
        assert b"non valido" in bad.data
        conn = db.connect(path)
        assert db.get_setting(conn, "post_template") == "{title}\n{url}"
        conn.close()
        empty = client.post("/settings", data={
            "default_mode": "auto",
            "fb_page_id": "123",
            "post_template": "   ",
        }, follow_redirects=True)
        assert empty.status_code == 200
        conn = db.connect(path)
        assert db.get_setting(conn, "post_template") == config.DEFAULTS["post_template"]
        conn.close()
    finally:
        os.remove(path)


def test_export_default_path_is_exported_yaml():
    conn = _conn()
    old = os.getcwd()
    tmp = tempfile.mkdtemp()
    try:
        os.chdir(tmp)
        path = config.export_yaml(conn)
        assert path == "config.exported.yaml"
        assert os.path.isfile("config.exported.yaml")
        assert not os.path.isfile("config.yaml")
    finally:
        os.chdir(old)
        for name in ("config.exported.yaml",):
            p = os.path.join(tmp, name)
            if os.path.isfile(p):
                os.remove(p)
        os.rmdir(tmp)


def test_import_rejects_bad_default_mode():
    conn = _conn()
    path = _write_yaml({"default_mode": "maybe"})
    try:
        try:
            config.import_yaml(conn, path)
            assert False, "expected ValueError"
        except ValueError as e:
            assert "default_mode" in str(e)
    finally:
        os.remove(path)


if __name__ == "__main__":
    test_render_post_ignores_braces_in_values()
    test_render_post_rejects_unknown_placeholder()
    test_import_upserts_without_deleting()
    test_import_replace_deletes_missing_keywords()
    test_import_rejects_null_keywords()
    test_import_without_keywords_key_keeps_existing()
    test_console_saves_settings()
    test_export_default_path_is_exported_yaml()
    test_import_rejects_bad_default_mode()
    print("ok")
