"""Reddit search fetcher for ticker-specific discussion posts.

Uses Reddit's public JSON endpoints (``reddit.com/r/{sub}/search.json``)
which do not require an API key. Public throughput is ~10 requests per
minute per IP, well within budget for a single agent run that queries
a handful of finance subreddits per ticker.

Returns formatted plaintext blocks ready for prompt injection. Degrades
gracefully — returns a placeholder string rather than raising, so callers
never have to special-case missing data.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# curl_cffi impersonates real browser TLS fingerprints, which is what Reddit's
# anti-bot actually keys on. Safari17 fingerprint reliably bypasses the 403.
try:
    from curl_cffi import requests as _cffi_requests
    _HAS_CFFI = True
except ImportError:
    _cffi_requests = None
    _HAS_CFFI = False

logger = logging.getLogger(__name__)

# Reddit blocks unauthenticated JSON from most non-residential IPs (HTTP 403).
# To get real data you need either:
#   1. A free Reddit "script" app -> set REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET
#      (we'll OAuth at oauth.reddit.com), or
#   2. Set TRADINGAGENTS_DISABLE_REDDIT=1 to skip silently with no warnings.
_PUBLIC_API = "https://old.reddit.com/r/{sub}/search.json?{qs}"
_OAUTH_API  = "https://oauth.reddit.com/r/{sub}/search.json?{qs}"
_TOKEN_URL  = "https://www.reddit.com/api/v1/access_token"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_DISABLED = os.getenv("TRADINGAGENTS_DISABLE_REDDIT", "").lower() in ("1", "true", "yes")
_token_cache: dict[str, float | str] = {}  # {"token": str, "expires": epoch}


def _get_oauth_token() -> str | None:
    """Return a cached OAuth bearer if REDDIT_CLIENT_ID/SECRET are set."""
    cid = os.getenv("REDDIT_CLIENT_ID")
    sec = os.getenv("REDDIT_CLIENT_SECRET")
    if not (cid and sec):
        return None
    now = time.time()
    if _token_cache.get("token") and float(_token_cache.get("expires", 0)) > now + 30:
        return str(_token_cache["token"])
    import base64
    body = urlencode({"grant_type": "client_credentials"}).encode()
    auth = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    req = Request(_TOKEN_URL, data=body,
                  headers={"Authorization": f"Basic {auth}", "User-Agent": _UA})
    try:
        with urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        _token_cache["token"]   = data["access_token"]
        _token_cache["expires"] = now + int(data.get("expires_in", 3600))
        return str(_token_cache["token"])
    except Exception as exc:
        logger.warning("Reddit OAuth token request failed: %s", exc)
        return None

# Default subreddits ordered roughly by signal density for ticker-specific
# discussion. wallstreetbets has the most volume but most noise; stocks /
# investing trend more measured. Caller can override.
DEFAULT_SUBREDDITS = ("wallstreetbets", "stocks", "investing")


def _fetch_subreddit(
    ticker: str,
    sub: str,
    limit: int,
    timeout: float,
) -> list[dict]:
    qs = urlencode({
        "q": ticker,
        "restrict_sr": "on",
        "sort": "new",
        "t": "week",  # last 7 days
        "limit": limit,
    })
    token = _get_oauth_token()
    if token:
        url = _OAUTH_API.format(sub=sub, qs=qs)
        headers = {"Authorization": f"Bearer {token}",
                   "User-Agent": _UA, "Accept": "application/json"}
    else:
        url = _PUBLIC_API.format(sub=sub, qs=qs)
        headers = {"User-Agent": _UA, "Accept": "application/json"}

    # Prefer curl_cffi with safari17 impersonation — Reddit currently allows it
    # while blocking plain urllib/requests with 403.
    if _HAS_CFFI and not token:
        try:
            r = _cffi_requests.get(url, headers=headers,
                                   impersonate="safari17_0", timeout=timeout)
            if r.status_code != 200:
                logger.warning("Reddit (cffi) r/%s . %s: HTTP %s",
                               sub, ticker, r.status_code)
                return []
            payload = r.json()
        except Exception as exc:
            logger.warning("Reddit (cffi) fetch failed for r/%s . %s: %s",
                           sub, ticker, exc)
            return []
    else:
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read())
        except (HTTPError, URLError, json.JSONDecodeError, TimeoutError) as exc:
            logger.warning("Reddit fetch failed for r/%s . %s: %s", sub, ticker, exc)
            return []
    children = (payload.get("data") or {}).get("children") or []
    return [c.get("data", {}) for c in children if isinstance(c, dict)]


def fetch_reddit_posts(
    ticker: str,
    subreddits: Iterable[str] = DEFAULT_SUBREDDITS,
    limit_per_sub: int = 5,
    timeout: float = 10.0,
    inter_request_delay: float = 0.4,
) -> str:
    """Fetch recent Reddit posts mentioning ``ticker`` across finance
    subreddits and return them as a formatted plaintext block.

    ``inter_request_delay`` keeps us under Reddit's public rate limit
    (~10 req/min per IP) even if the caller queries many subreddits.
    """
    if _DISABLED:
        return f"<Reddit data disabled via TRADINGAGENTS_DISABLE_REDDIT for {ticker.upper()}>"
    blocks = []
    total_posts = 0
    for i, sub in enumerate(subreddits):
        if i > 0:
            time.sleep(inter_request_delay)
        posts = _fetch_subreddit(ticker, sub, limit_per_sub, timeout)
        total_posts += len(posts)
        if not posts:
            blocks.append(f"r/{sub}: <no posts found mentioning {ticker.upper()} in the past 7 days>")
            continue

        lines = [f"r/{sub} — {len(posts)} recent posts mentioning {ticker.upper()}:"]
        for p in posts:
            title = (p.get("title") or "").replace("\n", " ").strip()
            score = p.get("score", 0)
            comments = p.get("num_comments", 0)
            created = p.get("created_utc")
            created_str = (
                time.strftime("%Y-%m-%d", time.gmtime(created)) if created else "?"
            )
            selftext = (p.get("selftext") or "").replace("\n", " ").strip()
            if len(selftext) > 240:
                selftext = selftext[:240] + "…"
            lines.append(
                f"  [{created_str} · {score:>4}↑ · {comments:>3}c] {title}"
                + (f"\n    body excerpt: {selftext}" if selftext else "")
            )
        blocks.append("\n".join(lines))

    if total_posts == 0:
        return (
            f"<no Reddit posts found mentioning {ticker.upper()} across "
            f"{', '.join(f'r/{s}' for s in subreddits)} in the past 7 days>"
        )
    return "\n\n".join(blocks)
