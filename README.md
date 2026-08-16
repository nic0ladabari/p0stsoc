# p0stsoc

English · [Italiano](README.it.md)

> Vagabondo, vagabondo  
> Qualche santo mi guiderà  
> Ho venduto le mie scarpe  
> Per un miglio di libertà  
> Da soli non si vive  
> Senza amore non morirò  
> Vagabondo, sto sognando, delirando

Fetch Google News by keyword (with exclusions), store hits in SQLite, and
post them to social pages. Facebook is first; other networks can follow.
Human moderation queue by default; full-auto or per-keyword hybrid once
you trust the filter.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
p0stsoc initdb          # create the DB and import config.yaml
```

Configure keywords and settings (`default_mode`, `fb_page_id`, `post_template`)
in [`config.yaml`](config.yaml) **or** from the web console.
The Facebook token does **not** go in the file: pass it via env var (below).

## Usage

```bash
p0stsoc fetch                 # download news (put this in cron)
p0stsoc post --dry-run        # preview what would be posted (no FB call)
p0stsoc serve                 # console at http://127.0.0.1:8000
p0stsoc export                # DB -> config.yaml
p0stsoc import                # config.yaml -> DB (upsert; --replace syncs keywords)
p0stsoc prune --days 90       # delete posted/skipped rows older than N days
python -m p0stsoc.test_filters  # exclusion-filter self-check
python -m p0stsoc.test_post     # anti double-post claim self-check
python -m p0stsoc.test_config   # import sync + post-template self-check
```

Queue: open the console and, for each `new` story, pick **Post now / Approve / Skip**.
`approved` articles are published on the next `p0stsoc post`.

## Moderation modes

- Default = **review**: approve by hand (`default_mode: review`).
- **Full auto**: set `default_mode: auto` → everything that passes the filter
  lands in the `approved` queue.
- **Hybrid**: `mode: auto` on a single keyword, the rest stays in review.

## Facebook: the token (the awkward part)

The code is simple; the friction is getting a **long-lived Page Access Token**.

1. Create an app at https://developers.facebook.com/ (type "Business").
2. Add the **Facebook Login** product and request
   `pages_manage_posts` and `pages_read_engagement`.
3. In **Graph API Explorer** select the app and your Page, generate a
   *User Token*, then exchange it for a **long-lived** Page token
   (`/oauth/access_token` with `grant_type=fb_exchange_token`, then `/me/accounts`).
4. Take the Page `id` → put it in `fb_page_id` (config.yaml, or
   console → Keyword → Settings).
5. Export the token:

```bash
export P0STSOC_FB_TOKEN="EAAB...your-page-token"
p0stsoc post           # now it actually publishes
```

> For production (posting to a Page you do not own, or with more users) Meta
> requires **App Review** + business verification. In dev, on your own Page,
> the token from Graph API Explorer is enough.

## Cron

```cron
*/15 * * * *  cd /path/p0stsoc && P0STSOC_FB_TOKEN=$TOK .venv/bin/p0stsoc fetch
*/15 * * * *  cd /path/p0stsoc && P0STSOC_FB_TOKEN=$TOK .venv/bin/p0stsoc post
```

The second job is only needed if you use `auto` keywords / `default_mode: auto`.
In pure review you post from the console and only need the `fetch` job.

`fetch` and `post` take an **internal lockfile** (`$TMPDIR/p0stsoc-{fetch,post}.lock`):
if a run is still active, the next one exits immediately — no overlapping runs.
On top of that each article is **claimed** (`status=posting`) before the
Facebook call, so cron and “Post now” cannot publish the same row twice.

## Known limits

- At post time the Google News redirect is **resolved to the publisher URL**
  (`googlenewsdecoder`, an internal Google endpoint). It is fragile by
  definition: if the decoder fails or Google changes the format, the redirect
  is posted as fallback — Facebook still shows a preview.
- `p0stsoc export` **rewrites `config.yaml` and drops comments** (it is a DB dump).
  If you use the commented file as documentation, export to another path
  (`p0stsoc export config.exported.yaml`) or keep the comments elsewhere.
- `p0stsoc import` **upserts** settings and keywords. To delete DB keywords
  that are not in the yaml: `p0stsoc import --replace`.
  `initdb` never deletes keywords already in the DB.
- The exclusion filter is a **substring** match (title+snippet). A short
  phrase like `ai` also matches inside other words; use specific exclusions.
- The console has **no authentication**: serve it on localhost only, or
  behind a reverse-proxy that handles access.

## License

[MIT](LICENSE) © 2026 N1kola Di Bari
