"""p0stsoc command-line interface."""
import argparse
import fcntl
import json
import os
import sys
import tempfile
import time

from . import config, db, facebook, news


def _effective_mode(kw_mode, default_mode):
    return kw_mode or default_mode or "review"


def _run_lock(name):
    """Exclusive non-blocking lock for the whole run: overlapping cron jobs
    would double-post. Returns the lock fd, or None if another run holds it.
    In-code instead of flock(1) so it also works where flock is missing (macOS)."""
    fd = os.open(os.path.join(tempfile.gettempdir(), f"p0stsoc-{name}.lock"),
                 os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd  # held open (and locked) until process exit


def post_article(conn, a, dry_run=False):
    """Render the template and post one article; mark it posted on success.

    Shared by the CLI and the web console so the two never drift. Claims the
    row atomically before talking to Facebook so overlapping callers cannot
    double-post. Template and network errors are RuntimeError so both callers
    catch one type. dry_run skips claim/mark (preview only).
    """
    claimed = False
    if not dry_run:
        if not db.claim_for_post(conn, a["id"]):
            raise RuntimeError(
                f"article #{a['id']} not postable (already posting/posted/skipped)")
        claimed = True
    try:
        token = config.get_token()
        page_id = db.get_setting(conn, "fb_page_id", "")
        template = db.get_setting(conn, "post_template", config.DEFAULTS["post_template"])
        url = news.resolve_url(a["url"])   # Google redirect -> publisher URL (fallback: redirect)
        try:
            message = config.render_post(
                template, title=a["title"], url=url, source=a["source"] or "")
        except ValueError as e:
            raise RuntimeError(
                f"bad post_template ({e}); allowed placeholders: {{title}} {{url}} {{source}}"
            ) from e
        post_id = facebook.post_link(page_id, token, message, url, dry_run=dry_run)
        # FB accepted the post: never release_claim (that would allow a double-post
        # retry). If mark_posted fails the row stays 'posting' until requeue/crash
        # recovery — rarer and safer than posting twice.
        if not dry_run:
            claimed = False
            db.mark_posted(conn, a["id"], post_id)
        return post_id
    except Exception:
        if claimed:
            db.release_claim(conn, a["id"])
        raise


def cmd_initdb(args):
    conn = db.connect()
    db.init_schema(conn)
    print("schema ready")
    try:
        config.import_yaml(conn)
        print(f"imported {config.CONFIG_PATH}")
    except FileNotFoundError:
        print(f"no {config.CONFIG_PATH} found (skipped import)")
    except (ValueError, TypeError, KeyError) as e:
        print(f"import failed: {e}", file=sys.stderr)
        raise SystemExit(1)


def cmd_import(args):
    conn = db.connect()
    db.init_schema(conn)
    try:
        config.import_yaml(conn, args.path, replace_keywords=args.replace)
    except (ValueError, TypeError, KeyError) as e:
        print(f"import failed: {e}", file=sys.stderr)
        raise SystemExit(1)
    print(f"imported {args.path or config.CONFIG_PATH}")


def cmd_export(args):
    conn = db.connect()
    db.init_schema(conn)
    print(f"exported {config.export_yaml(conn, args.path)}")


def cmd_fetch(args):
    lock = _run_lock("fetch")
    if lock is None:
        print("another 'p0stsoc fetch' is running; exiting", file=sys.stderr)
        return
    conn = db.connect()
    db.init_schema(conn)
    default_mode = db.get_setting(conn, "default_mode", "review")
    keywords = db.list_keywords(conn, only_enabled=True)
    if args.keyword:
        keywords = [k for k in keywords if k["query"] == args.keyword]
    total_new = 0
    for k in keywords:
        exclude = json.loads(k["exclude"] or "[]")
        mode = _effective_mode(k["mode"], default_mode)
        status = "approved" if mode == "auto" else "new"
        try:
            articles = news.fetch(k["query"], exclude, k["lang"], k["country"])
        except RuntimeError as e:
            print(f"! {e}", file=sys.stderr)
            continue
        new = sum(
            db.insert_article(conn, a["guid"], k["id"], a["title"], a["url"],
                              a["source"], a["published"], a["snippet"], status)
            for a in articles
        )
        conn.commit()          # one commit per keyword, not per article
        total_new += new
        print(f"{k['query']}: {len(articles)} matched, {new} new ({mode})")
    print(f"done: {total_new} new articles")


def cmd_post(args):
    lock = _run_lock("post")
    if lock is None:
        print("another 'p0stsoc post' is running; exiting", file=sys.stderr)
        return
    conn = db.connect()
    db.init_schema(conn)
    if not args.dry_run:
        n = db.requeue_posting(conn)   # crash mid-post left rows in 'posting'
        if n:
            print(f"requeued {n} stale posting article(s)", file=sys.stderr)
    approved = db.list_articles(conn, status="approved", limit=None)
    if not approved:
        print("nothing to post")
        return
    for i, a in enumerate(approved):
        if i and not args.dry_run:
            time.sleep(args.delay)   # pause between real posts, not before the first / in dry-run
        try:
            post_id = post_article(conn, a, dry_run=args.dry_run)
        except Exception as e:   # not just RuntimeError: a sqlite error in
            print(f"! post failed for #{a['id']}: {e}", file=sys.stderr)
            continue             # mark_posted must not kill the whole batch
        print(f"posted #{a['id']} -> {post_id}")


def cmd_prune(args):
    conn = db.connect()
    db.init_schema(conn)
    n = db.prune_articles(conn, args.days)
    print(f"pruned {n} posted/skipped article(s) older than {args.days} days")


def cmd_serve(args):
    from .web import create_app
    create_app().run(host=args.host, port=args.port, debug=args.debug)


def build_parser():
    p = argparse.ArgumentParser(prog="p0stsoc", description="Google News -> social reposter")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("initdb", help="create schema and import config.yaml if present"
                   ).set_defaults(func=cmd_initdb)

    pi = sub.add_parser("import", help="load config.yaml into the DB")
    pi.add_argument("path", nargs="?")
    pi.add_argument("--replace", action="store_true",
                    help="delete keywords that are not in the yaml")
    pi.set_defaults(func=cmd_import)

    pe = sub.add_parser("export", help="dump the DB to config.exported.yaml")
    pe.add_argument("path", nargs="?")
    pe.set_defaults(func=cmd_export)

    pf = sub.add_parser("fetch", help="fetch news for enabled keywords")
    pf.add_argument("--keyword", help="only this query")
    pf.set_defaults(func=cmd_fetch)

    pp = sub.add_parser("post", help="post approved articles to Facebook")
    pp.add_argument("--dry-run", action="store_true")
    pp.add_argument("--delay", type=float, default=2.0, help="seconds between posts")
    pp.set_defaults(func=cmd_post)

    pr = sub.add_parser("prune", help="delete old posted/skipped articles")
    pr.add_argument("--days", type=int, default=90, help="keep the last N days (default 90)")
    pr.set_defaults(func=cmd_prune)

    ps = sub.add_parser("serve", help="run the web console")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8000)
    ps.add_argument("--debug", action="store_true")
    ps.set_defaults(func=cmd_serve)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
