"""Fetch Google News RSS search results and apply the exclude filter."""
import re
import threading
import urllib.parse

import feedparser
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")

_TAG_RE = re.compile(r"<[^>]+>")


def build_rss_url(query, exclude=None, lang="it", country="IT"):
    q = query.strip()
    for phrase in (exclude or []):
        q += f' -"{phrase}"'          # Google-side first-pass exclusion
    params = urllib.parse.urlencode({
        "q": q, "hl": lang, "gl": country, "ceid": f"{country}:{lang}",
    })
    return f"https://news.google.com/rss/search?{params}"


def _strip_html(text):
    return _TAG_RE.sub("", text or "").strip()


def passes_filter(title, snippet, exclude):
    """False if any excluded phrase matches title+snippet (case-insensitive).

    Multi-word phrases stay substring matches (`Nikola Tesla`). A single
    token uses word boundaries so `ai` does not match inside `said`.
    """
    haystack = f"{title} {snippet}"
    for raw in (exclude or []):
        phrase = (raw or "").strip()
        if not phrase:
            continue
        if " " in phrase:
            if phrase.lower() in haystack.lower():
                return False
        elif re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", haystack, re.I):
            return False
    return True


def fetch(query, exclude=None, lang="it", country="IT", timeout=20):
    """Return a list of article dicts that pass the exclude filter."""
    url = build_rss_url(query, exclude, lang, country)
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Google News fetch failed for '{query}': {e}") from e

    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        raise RuntimeError(
            f"Google News returned unparseable content for '{query}' "
            f"(consent wall / rate-limit?): {feed.bozo_exception}")
    # (an empty-but-valid feed just means no news for this query; cmd_fetch already
    #  reports "0 matched", so no separate warning here.)
    out = []
    for e in feed.entries:
        link = e.get("link")
        if not link:
            continue          # need a link to post; skip
        guid = e.get("id") or link   # stable dedup key (NULL never conflicts on UNIQUE)
        title = e.get("title", "")
        snippet = _strip_html(e.get("summary", ""))
        if not passes_filter(title, snippet, exclude):
            continue          # redundant guard on top of the query filter
        # url stays the Google News redirect here: it's a stable dedup key and FB
        # renders a preview anyway. It is resolved to the publisher URL just
        # before posting — see resolve_url().
        out.append({
            "guid": guid,
            "title": title,
            "url": link,
            "source": (e.get("source") or {}).get("title", ""),
            "published": e.get("published", ""),
            "snippet": snippet,
        })
    return out


def resolve_url(url, timeout=10):
    """Best-effort: Google News redirect -> publisher URL (batchexecute decoder).

    Any failure — decoder broken, rate-limited, package missing — returns the
    original redirect, which FB still renders fine. The decoder is a moving
    target, so it must never become a posting blocker. Resolved at post time,
    not fetch time: skipped articles are never resolved (fewer requests, less
    429 risk).

    googlenewsdecoder issues requests WITHOUT any timeout, so a hung Google
    endpoint would block forever (in cron: wedging the batch AND the run lock).
    The decode therefore runs in a daemon thread with a hard timeout; on
    timeout the orphan thread is left to die at process exit and the original
    redirect is used.
    """
    try:
        from googlenewsdecoder import gnewsdecoder
    except ImportError:
        return url

    result = {}

    def _decode():
        try:
            r = gnewsdecoder(url, interval=1)
            if r.get("status"):
                result["url"] = r["decoded_url"]
        except Exception:
            pass

    t = threading.Thread(target=_decode, daemon=True)
    t.start()
    t.join(timeout)
    return result.get("url") or url
