"""Minimal Flask admin console: moderation queue + keyword editor.

No auth: serve on localhost or behind a reverse proxy.
"""
import json
import sqlite3
from urllib.parse import urlsplit

from flask import (Flask, abort, flash, g, redirect, render_template_string,
                   request, url_for)

from . import config, db
from .cli import post_article

HEADER = """
<!doctype html><html lang=it><meta charset=utf-8>
<title>p0stsoc</title>
<style>
 body{font:15px system-ui,sans-serif;margin:2rem auto;max-width:960px;padding:0 1rem}
 a{color:#06c;text-decoration:none} nav a{margin-right:1rem;font-weight:600}
 table{border-collapse:collapse;width:100%;margin:1rem 0}
 td,th{border-bottom:1px solid #ddd;padding:.45rem;text-align:left;vertical-align:top}
 .muted{color:#888;font-size:.85em} form.inline{display:inline}
 button{cursor:pointer;padding:.25rem .6rem} .tag{background:#eee;border-radius:3px;padding:0 .35rem;font-size:.8em}
 input,select,textarea{padding:.3rem} textarea{width:100%;max-width:36rem}
 .flash{background:#e6f4ea;border:1px solid #9ad;padding:.5rem;border-radius:4px}
 h2{margin-top:2rem}
</style>
<nav><a href="{{ url_for('queue') }}">Coda</a><a href="{{ url_for('keywords') }}">Keyword</a></nav>
{% with msgs = get_flashed_messages() %}{% for m in msgs %}<p class=flash>{{ m }}</p>{% endfor %}{% endwith %}
"""

QUEUE = HEADER + """
<h2>Da revisionare ({{ new|length }})</h2>
{% if not new %}<p class=muted>Niente in coda. Lancia <code>p0stsoc fetch</code>.</p>{% endif %}
<table>
{% for a in new %}
<tr>
 <td><a href="{{ a['url'] }}" target=_blank>{{ a['title'] }}</a>
     <div class=muted>{{ a['source'] }} · <span class=tag>{{ a['keyword'] }}</span> · {{ a['published'] }}</div></td>
 <td>
  <form class=inline method=post action="{{ url_for('do_post', aid=a['id']) }}"><button>Posta ora</button></form>
  <form class=inline method=post action="{{ url_for('do_approve', aid=a['id']) }}"><button>Approva</button></form>
  <form class=inline method=post action="{{ url_for('do_skip', aid=a['id']) }}"><button>Scarta</button></form>
 </td>
</tr>
{% endfor %}
</table>

<h2>Approvati, in attesa di post ({{ approved|length }})</h2>
<table>
{% for a in approved %}
<tr><td><a href="{{ a['url'] }}" target=_blank>{{ a['title'] }}</a>
    <span class=muted>{{ a['source'] }}</span></td>
 <td><form class=inline method=post action="{{ url_for('do_post', aid=a['id']) }}"><button>Posta ora</button></form>
     <form class=inline method=post action="{{ url_for('do_skip', aid=a['id']) }}"><button>Scarta</button></form></td></tr>
{% endfor %}
</table>

<h2>Ultimi postati</h2>
<table>
{% for a in posted %}
<tr><td><a href="{{ a['url'] }}" target=_blank>{{ a['title'] }}</a></td>
    <td class=muted>{{ a['posted_at'] }} · {{ a['fb_post_id'] }}</td></tr>
{% endfor %}
</table>
</html>
"""

# Forms live OUTSIDE the table (a <form> inside <tr> is invalid and gets
# foster-parented, detaching the inputs). Inputs in the cells bind via form="...".
KEYWORDS = HEADER + """
<h2>Impostazioni</h2>
<form method=post action="{{ url_for('do_settings') }}">
<p>default_mode
 <select name=default_mode>
  <option value=review {{ 'selected' if default_mode=='review' }}>review</option>
  <option value=auto {{ 'selected' if default_mode=='auto' }}>auto</option>
 </select>
</p>
<p>fb_page_id <input name=fb_page_id value="{{ fb_page_id }}" size=28></p>
<p>post_template<br>
<textarea name=post_template rows=3 cols=50>{{ post_template }}</textarea>
<div class=muted>placeholder: {title} {url} {source} · il token FB resta in P0STSOC_FB_TOKEN</div></p>
<p><button>Salva impostazioni</button></p>
</form>

<h2>Keyword</h2>
{% for k in kws %}
<form id="kwsave{{ k['id'] }}" method=post action="{{ url_for('keywords') }}">
 <input type=hidden name=action value=save><input type=hidden name=id value="{{ k['id'] }}"></form>
<form id="kwdel{{ k['id'] }}" method=post action="{{ url_for('keywords') }}">
 <input type=hidden name=action value=delete><input type=hidden name=id value="{{ k['id'] }}"></form>
{% endfor %}
<form id="kwnew" method=post action="{{ url_for('keywords') }}"><input type=hidden name=action value=save></form>
<table>
<tr><th>Query</th><th>Escludi (virgola)</th><th>Mode</th><th>Lang</th><th>Paese</th><th>On</th><th></th></tr>
{% for k in kws %}
<tr>
 <td><input form="kwsave{{ k['id'] }}" name=query value="{{ k['query'] }}" required></td>
 <td><input form="kwsave{{ k['id'] }}" name=exclude value="{{ k['exclude_csv'] }}" size=28></td>
 <td><select form="kwsave{{ k['id'] }}" name=mode>
   <option value="" {{ 'selected' if not k['mode'] }}>(default)</option>
   <option value=review {{ 'selected' if k['mode']=='review' }}>review</option>
   <option value=auto {{ 'selected' if k['mode']=='auto' }}>auto</option>
 </select></td>
 <td><input form="kwsave{{ k['id'] }}" name=lang value="{{ k['lang'] }}" size=3></td>
 <td><input form="kwsave{{ k['id'] }}" name=country value="{{ k['country'] }}" size=3></td>
 <td><input form="kwsave{{ k['id'] }}" type=checkbox name=enabled {{ 'checked' if k['enabled'] }}></td>
 <td><button form="kwsave{{ k['id'] }}">Salva</button>
     <button form="kwdel{{ k['id'] }}" onclick='return confirm({{ ("Eliminare la keyword " ~ k["query"] ~ "?") | tojson }})'>X</button></td>
</tr>
{% endfor %}
<tr>
 <td><input form=kwnew name=query placeholder="nuova query" required></td>
 <td><input form=kwnew name=exclude placeholder="frase1, frase2" size=28></td>
 <td><select form=kwnew name=mode><option value="">(default)</option><option value=review>review</option><option value=auto>auto</option></select></td>
 <td><input form=kwnew name=lang value=it size=3></td>
 <td><input form=kwnew name=country value=IT size=3></td>
 <td><input form=kwnew type=checkbox name=enabled checked></td>
 <td><button form=kwnew>Aggiungi</button></td>
</tr>
</table>
<p class=muted>
<form class=inline method=post action="{{ url_for('do_export') }}"><button>Esporta in config.yaml</button></form>
</p>
</html>
"""


def create_app(db_path=None):
    app = Flask(__name__)
    app.secret_key = "p0stsoc-local"  # only used to sign flash cookies on a localhost tool

    boot = db.connect(db_path)       # ensure schema once, not per request
    db.init_schema(boot)
    boot.close()

    def conn():
        if "db" not in g:
            g.db = db.connect(db_path)
        return g.db

    @app.teardown_appcontext
    def _close(exc):
        c = g.pop("db", None)
        if c is not None:
            c.close()

    @app.before_request
    def _same_origin_only():
        """No auth, so at least refuse cross-origin POSTs: any web page open in
        the admin's browser could otherwise auto-submit a form to localhost.
        Cross-origin form POSTs always carry Origin (or Referer)."""
        if request.method == "POST":
            origin = request.headers.get("Origin") or request.headers.get("Referer")
            if origin and urlsplit(origin).netloc != request.host:
                abort(403)

    @app.get("/")
    def queue():
        c = conn()
        return render_template_string(
            QUEUE,
            new=db.list_articles(c, "new"),
            approved=db.list_articles(c, "approved"),
            posted=db.list_articles(c, "posted", limit=20),
        )

    @app.post("/post/<int:aid>")
    def do_post(aid):
        c = conn()
        a = db.get_article(c, aid)
        if a is None:
            abort(404)
        try:
            flash(f"Postato: {post_article(c, a)}")
        except Exception as e:   # sqlite errors too, same as cmd_post
            flash(f"Errore post: {e}")
        return redirect(url_for("queue"))

    @app.post("/approve/<int:aid>")
    def do_approve(aid):
        if not db.set_status(conn(), aid, "approved"):
            flash("Articolo non più in coda (già in post/postato/scartato).")
        return redirect(url_for("queue"))

    @app.post("/skip/<int:aid>")
    def do_skip(aid):
        if not db.set_status(conn(), aid, "skipped"):
            flash("Articolo non più in coda (già in post/postato/scartato).")
        return redirect(url_for("queue"))

    @app.route("/keywords", methods=["GET", "POST"])
    def keywords():
        c = conn()
        if request.method == "POST":
            f = request.form
            if f.get("action") == "delete":
                db.delete_keyword(c, int(f["id"]))
            else:
                exclude = [x.strip() for x in f.get("exclude", "").split(",") if x.strip()]
                kw = dict(
                    query=f["query"].strip(), exclude=exclude,
                    mode=f.get("mode") or None,
                    lang=f.get("lang", "it").strip() or "it",
                    country=f.get("country", "IT").strip() or "IT",
                    enabled=1 if f.get("enabled") else 0,
                )
                try:
                    if f.get("id"):
                        db.update_keyword(c, int(f["id"]), **kw)   # rename in place, no dup
                    else:
                        db.upsert_keyword(c, **kw)
                except sqlite3.IntegrityError:
                    flash(f"Esiste già una keyword «{kw['query']}» ({kw['lang']}/{kw['country']}).")
            return redirect(url_for("keywords"))
        kws = []
        for r in db.list_keywords(c):
            d = dict(r)
            d["exclude_csv"] = ", ".join(json.loads(r["exclude"] or "[]"))
            kws.append(d)
        return render_template_string(
            KEYWORDS, kws=kws,
            default_mode=db.get_setting(c, "default_mode", "review"),
            fb_page_id=db.get_setting(c, "fb_page_id", ""),
            post_template=db.get_setting(c, "post_template", config.DEFAULTS["post_template"]),
        )

    @app.post("/settings")
    def do_settings():
        c = conn()
        f = request.form
        mode = f.get("default_mode", "review")
        if mode not in config.MODES:
            flash("default_mode deve essere review o auto.")
            return redirect(url_for("keywords"))
        template = f.get("post_template", "").strip() or config.DEFAULTS["post_template"]
        try:
            config.render_post(template, title="t", url="u", source="s")
        except ValueError as e:
            flash(f"post_template non valido: {e}")
            return redirect(url_for("keywords"))
        db.set_setting(c, "default_mode", mode)
        db.set_setting(c, "fb_page_id", f.get("fb_page_id", "").strip())
        db.set_setting(c, "post_template", template)
        flash("Impostazioni salvate.")
        return redirect(url_for("keywords"))

    @app.post("/export")
    def do_export():
        flash(f"Esportato in {config.export_yaml(conn())}")
        return redirect(url_for("keywords"))

    return app
