"""Post a link to a Facebook Page via the Graph API."""
import requests

GRAPH = "https://graph.facebook.com/v21.0"


def post_link(page_id, token, message, link, dry_run=False, timeout=30):
    """Post message+link to the page feed. Returns the FB post id.

    Raises RuntimeError on any failure (network or API) so callers can catch
    a single exception type.
    """
    if dry_run:
        print(f"[dry-run] would post to page {page_id}: {message!r} link={link}")
        return "dry-run"
    if not token:
        raise RuntimeError("P0STSOC_FB_TOKEN not set")
    if not page_id or page_id == "REPLACE_WITH_PAGE_ID":
        raise RuntimeError("fb_page_id not configured")
    try:
        resp = requests.post(
            f"{GRAPH}/{page_id}/feed",
            data={"message": message, "link": link, "access_token": token},
            timeout=timeout,
        )
    except requests.RequestException as e:
        raise RuntimeError(f"FB request failed: {e}") from e
    if not resp.ok:
        raise RuntimeError(f"FB post failed ({resp.status_code}): {resp.text}")
    try:
        return resp.json().get("id", "")
    except ValueError:
        return ""    # 2xx but body wasn't JSON: treat as accepted (post exists, id unknown)
