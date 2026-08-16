"""Self-check for the exclude filter — the namesake case (Tesla vs Nikola Tesla).

Run: python -m p0stsoc.test_filters
"""
from unittest.mock import patch

from p0stsoc.news import build_rss_url, passes_filter, resolve_url


def test_exclude():
    ex = ["Nikola Tesla"]
    assert passes_filter("Tesla, vendite in calo in Europa", "", ex)
    assert not passes_filter("Nikola Tesla, il genio dimenticato", "", ex)
    assert not passes_filter("Scienza", "Le invenzioni di Nikola Tesla", ex)   # snippet too
    assert not passes_filter("NIKOLA TESLA oggi", "", ex)                      # case-insensitive
    assert passes_filter("Qualsiasi cosa", "", [])                             # no exclusions


def test_rss_url():
    url = build_rss_url("Tesla", ["Nikola Tesla"], "it", "IT")
    assert "news.google.com/rss/search" in url
    assert "Nikola" in url                 # exclusion baked into the query
    assert "ceid=IT%3Ait" in url


def test_resolve_url():
    GN = "https://news.google.com/rss/articles/CBMiX"
    with patch("googlenewsdecoder.gnewsdecoder",
               return_value={"status": True, "decoded_url": "https://editore.it/a"}):
        assert resolve_url(GN) == "https://editore.it/a"
    with patch("googlenewsdecoder.gnewsdecoder", return_value={"status": False}):
        assert resolve_url(GN) == GN            # decoder refused -> fallback
    with patch("googlenewsdecoder.gnewsdecoder", side_effect=Exception("broken")):
        assert resolve_url(GN) == GN            # decoder blew up -> fallback
    # hung decoder (no timeout inside the library) must not block the caller
    import time
    with patch("googlenewsdecoder.gnewsdecoder", side_effect=lambda *a, **k: time.sleep(60)):
        t0 = time.monotonic()
        assert resolve_url(GN, timeout=0.2) == GN
        assert time.monotonic() - t0 < 5        # returned on timeout, not after 60s


if __name__ == "__main__":
    test_exclude()
    test_rss_url()
    test_resolve_url()
    print("ok")
