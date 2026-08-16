"""YAML <-> DB config sync. The FB token comes from the environment only."""
import json
import os
import re

import yaml

from . import db

CONFIG_PATH = os.environ.get("P0STSOC_CONFIG", "config.yaml")
SETTING_KEYS = ("default_mode", "fb_page_id", "post_template")
DEFAULTS = {
    "default_mode": "review",
    "fb_page_id": "",
    "post_template": "{title}\n\n{url}",
}
MODES = ("review", "auto")
_PLACEHOLDER_RE = re.compile(r"\{(title|url|source)\}")


def get_token():
    return os.environ.get("P0STSOC_FB_TOKEN", "")


def render_post(template, title, url, source=""):
    """Fill {title} {url} {source} only. Braces in the values stay literal.

    Any other `{`/`}` in the template (typos, format specs, `{{escape}}`)
    is rejected so a bad template never reaches Facebook as raw text.
    """
    if not isinstance(template, str):
        raise ValueError("post_template must be a string")
    stripped = _PLACEHOLDER_RE.sub("", template)
    if "{" in stripped or "}" in stripped:
        raise ValueError("braces must be exactly {title} {url} {source}")
    fields = {"title": title, "url": url, "source": source or ""}
    return _PLACEHOLDER_RE.sub(lambda m: fields[m.group(1)], template)


def load_yaml(path=None):
    with open(path or CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def import_yaml(conn, path=None, replace_keywords=False):
    """Load config.yaml into the DB.

    Settings present in the file overwrite DB values. Keywords are upserted.
    With `replace_keywords=True` (CLI `--replace`), keywords absent from the
    file are deleted so yaml and DB match. `initdb` never replaces — a second
    init must not wipe keywords added from the console.
    """
    cfg = load_yaml(path)
    if "default_mode" in cfg:
        mode = cfg["default_mode"]
        if mode not in MODES:
            raise ValueError(f"default_mode must be review or auto, not {mode!r}")
    if "post_template" in cfg:
        t = cfg["post_template"]
        if not isinstance(t, str) or not t.strip():
            raise ValueError("post_template must be a non-empty string")
        render_post(t, title="t", url="u", source="s")
    keywords = None
    if "keywords" in cfg:
        keywords = cfg["keywords"]
        if keywords is None:
            raise ValueError("keywords must be a list, not null")
        if not isinstance(keywords, list):
            raise ValueError("keywords must be a list")
        for i, kw in enumerate(keywords):
            if not isinstance(kw, dict) or not kw.get("query"):
                raise ValueError(f"keywords[{i}] missing query")

    if "default_mode" in cfg:
        db.set_setting(conn, "default_mode", cfg["default_mode"])
    if "fb_page_id" in cfg:
        db.set_setting(conn, "fb_page_id", cfg["fb_page_id"])
    if "post_template" in cfg:
        db.set_setting(conn, "post_template", cfg["post_template"])
    if keywords is not None:
        wanted = set()
        for kw in keywords:
            query = kw["query"]
            lang = kw.get("lang", "it")
            country = kw.get("country", "IT")
            db.upsert_keyword(
                conn,
                query=query,
                exclude=kw.get("exclude", []),
                mode=kw.get("mode"),
                lang=lang,
                country=country,
                enabled=1 if kw.get("enabled", True) else 0,
            )
            wanted.add((query, lang, country))
        if replace_keywords:
            for r in db.list_keywords(conn):
                if (r["query"], r["lang"], r["country"]) not in wanted:
                    db.delete_keyword(conn, r["id"])
    return cfg


EXPORT_PATH = "config.exported.yaml"


def export_yaml(conn, path=None):
    """Dump DB settings + keywords to yaml.

    Default path is `config.exported.yaml` so a commented `config.yaml` is
    not overwritten by accident. Pass a path to write elsewhere.
    """
    path = path or EXPORT_PATH
    data = {k: db.get_setting(conn, k, DEFAULTS[k]) for k in SETTING_KEYS}
    data["keywords"] = [
        {
            "query": r["query"],
            "exclude": json.loads(r["exclude"] or "[]"),
            "mode": r["mode"] or None,
            "lang": r["lang"],
            "country": r["country"],
            "enabled": bool(r["enabled"]),
        }
        for r in db.list_keywords(conn)
    ]
    with open(path, "w") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return path
