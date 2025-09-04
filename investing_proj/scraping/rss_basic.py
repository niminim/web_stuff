### 1) RSS feeds (zero-auth, super fast)
"""
basic_rss.py — Company-agnostic Google News RSS fetcher (zero-auth)

What this module does
---------------------
• Builds a Boolean-style query for Google News (quoted company & synonyms, unquoted tickers)
• Generates a Google News RSS URL (defaults: English, US region)
• Fetches & parses items via feedparser (fast; no API key required)
• Optionally filters by recency (days_back)
• Canonicalizes URLs (removes tracking params) to improve de-duplication
• De-dupes items by (lower(title), lower(canonical_url))
• Returns a normalized structure: {title, url, published, source}

Why these choices
-----------------
• RSS is extremely fast and requires no auth—great as a first-pass "free" provider
• Canonical URLs collapse reprints with different tracking params
• De-dupe by (title, url) is robust enough for news feeds while keeping logic simple
• Skipping blank titles/links keeps downstream consumers stable (no `[None]` surprises)

Quick start
-----------
    pip install feedparser python-dateutil

    from basic_rss import fetch_company_news
    res = fetch_company_news("NVIDIA", tickers=["NVDA"], days_back=2, max_items=40)
    print(res["count"], "items")
    print(res["items"][:2])
"""

from __future__ import annotations

import feedparser
from urllib.parse import quote_plus, urlparse, urlunparse, parse_qsl, urlencode
from datetime import datetime, timedelta, timezone
from dateutil import parser as dateparser
from typing import List, Dict, Optional, Tuple


# ───────────────────────────────────────────────────────────────────────────────
# URL canonicalization
# ───────────────────────────────────────────────────────────────────────────────

def _canonical_url(url: str) -> str:
    """
    Return a canonical form of the URL by removing common tracking parameters.
    This improves de-duplication because the same article often appears with
    different campaign/query strings across sources.

    Example:
        https://site.com/post?id=123&utm_source=twitter&gclid=ABC
    →      https://site.com/post?id=123

    We purposely *keep* unknown parameters—only drop a well-known allowlist of
    tracking keys to avoid altering meaningful query semantics.
    """
    if not url:
        return url

    u = urlparse(url)

    # Tracking params that don’t change content identity.
    # Case-insensitive handling: compare on .lower().
    remove = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_name",
        "gclid", "gbraid", "wbraid", "fbclid", "mc_cid", "mc_eid"
    }

    # Rebuild the query string *without* the tracking keys.
    clean_qs = [
        (k, v) for (k, v) in parse_qsl(u.query, keep_blank_values=True)
        if k.lower() not in remove
    ]
    return urlunparse(u._replace(query=urlencode(clean_qs)))


# ───────────────────────────────────────────────────────────────────────────────
# Query builders & helpers
# ───────────────────────────────────────────────────────────────────────────────

def build_company_query(
    company: str,
    tickers: Optional[List[str]] = None,
    synonyms: Optional[List[str]] = None,
    extra_terms: Optional[List[str]] = None,
) -> str:
    """
    Build a robust boolean-ish query string for Google News.

    Rules:
      • Company & synonyms are quoted for exact phrase matching.
      • Tickers are left unquoted (they’re short tokens; quoting can reduce matches).
      • extra_terms, if provided, are quoted (use to tighten relevance: ["earnings"]).

    Example:
        company="NVIDIA", tickers=["NVDA"], synonyms=["NVIDIA Corporation"]
        -> '"NVIDIA" OR "NVIDIA Corporation" OR NVDA'

    Implementation detail:
      • We preserve insertion order while de-duplicating.
    """
    tickers = tickers or []
    synonyms = synonyms or []
    extra_terms = extra_terms or []

    parts: List[str] = []
    if company and company.strip():
        parts.append(f'"{company.strip()}"')                # exact phrase
    parts += [f'"{s.strip()}"' for s in synonyms if s and s.strip()]
    parts += [t.strip() for t in tickers if t and t.strip()]  # unquoted
    parts += [f'"{t.strip()}"' for t in extra_terms if t and t.strip()]

    # Deduplicate while preserving order (list(dict.fromkeys(...)) trick).
    parts = list(dict.fromkeys(parts))
    return " OR ".join(parts) if parts else ""


def build_google_news_rss_url(query: str, lang: str = "en", country: str = "US") -> str:
    """
    Construct the Google News RSS search URL.

    Important params:
      • hl : UI/content language + region (e.g. "en-US")
      • gl : Country (e.g. "US")
      • ceid: "<COUNTRY>:<LANG>" (e.g. "US:en")

    Notes:
      • Google News *search* RSS endpoint expects a URL-encoded `q` parameter.
      • The language/region trio makes results more consistent and predictable.
    """
    hl = f"{lang}-{country}"
    ceid = f"{country}:{lang}"
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl={hl}&gl={country}&ceid={ceid}"
    )


def _parse_published(published_str: str) -> Optional[datetime]:
    """
    Convert feed 'published'/'updated' strings into aware UTC datetimes.
    Returns None if parsing fails.

    Why UTC here?
      • Normalizing to UTC simplifies downstream time-window filtering and
        cross-provider merges where some APIs already return Zulu timestamps.
    """
    if not published_str:
        return None
    try:
        dt = dateparser.parse(published_str)
        if dt and not dt.tzinfo:
            # If the string lacked timezone info, assume UTC (conservative).
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _entry_source(entry) -> str:
    """
    Extract a readable publisher label from a feed entry.

    Google News typically provides a <source> element; feedparser exposes it
    under entry.source as a dict-like with 'title' and 'href'. If missing, we
    fallback to "Google News".

    Returning a non-empty string helps your UI avoid awkward blanks.
    """
    src = None
    s = entry.get("source")
    if isinstance(s, dict):
        src = s.get("title") or s.get("href")
    if not src:
        # Additional defensive fallbacks, just in case.
        src = entry.get("source") or ""
        if isinstance(src, dict):
            src = src.get("title") or ""
    return (src or "Google News").strip()


# ───────────────────────────────────────────────────────────────────────────────
# Main fetcher
# ───────────────────────────────────────────────────────────────────────────────

def fetch_company_news(
    company: str,
    *,
    tickers: Optional[List[str]] = None,
    synonyms: Optional[List[str]] = None,
    extra_terms: Optional[List[str]] = None,
    lang: str = "en",                 # language for results (content/UI)
    country: str = "US",              # region bias
    days_back: Optional[int] = None,  # if provided, filter out items older than now - days_back
    dedupe: bool = True,              # de-dup by (title,url) after canonicalization
    max_items: int = 100,             # unified cap for this provider
    max_results: Optional[int] = None # alias; if provided, overrides max_items
) -> Dict[str, object]:
    """
    Fetch Google News RSS items for the given company/tickers.

    Parameters:
      company      : Company name (free-form; will be quoted in the query)
      tickers      : Optional ticker symbols (kept unquoted for better matches)
      synonyms     : Optional alt names ("NVIDIA Corporation")
      extra_terms  : Optional quoted terms to further constrain results
      lang,country : Language/region hints for Google News
      days_back    : If not None, only keep items with published >= now - days_back
      dedupe       : If True, drop duplicates by (title, canonical_url)
      max_items    : Upper bound on results returned (defensive cap)
      max_results  : Alias for compatibility with other callers; overrides max_items if set

    Returns a consistent dict:
      {
        "provider": "google_news_rss",
        "query": <final boolean query>,
        "rss_url": <generated feed URL>,
        "count": <unique items>,
        "items": [
          {"title": str, "url": str, "published": str|None (ISO8601), "source": str},
          ...
        ]
      }
    """
    # Respect either max_results (alias) or max_items; enforce a reasonable upper bound.
    cap = int(max_results) if (max_results is not None) else int(max_items)
    cap = max(1, min(cap, 500))  # Google feeds are shallow; 500 is a very safe ceiling.

    # Build the boolean-ish query. If empty (unexpected), we'll still pass the company later.
    q = build_company_query(company, tickers=tickers, synonyms=synonyms, extra_terms=extra_terms)

    # Generate the Google News RSS endpoint. We pass q if non-empty; otherwise company.
    rss_url = build_google_news_rss_url(q or company, lang=lang, country=country)

    # Parse the feed. feedparser returns a normalized structure across RSS/Atom variants.
    feed = feedparser.parse(rss_url)

    items: List[Dict[str, str]] = []
    seen: set[Tuple[str, str]] = set()

    # Compute cutoff if a recency filter was requested.
    cutoff = None
    if days_back is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    # Iterate entries in feed order. Stop early if we hit the cap.
    for e in feed.entries:
        # Normalize required fields up front; skip malformed rows early.
        title = (e.get("title") or "").strip()
        url = _canonical_url(e.get("link") or "")
        if not title or not url:
            # Avoid leaking None/empty into downstream code.
            continue

        # Parse published/updated → aware UTC datetime; may still be None for some feeds.
        dt = _parse_published(e.get("published") or e.get("updated") or "")

        # Optional recency filter: only drop when we can compare a parsed timestamp.
        if cutoff and dt and dt < cutoff:
            continue

        rec = {
            "title": title,
            "url": url,
            "published": dt.replace(microsecond=0).isoformat() if dt else None,
            "source": _entry_source(e),
        }

        # Lightweight de-dupe: (lower(title), lower(url)) after canonicalization.
        key = (rec["title"].lower(), rec["url"].lower())
        if dedupe and key in seen:
            continue
        seen.add(key)

        items.append(rec)
        if len(items) >= cap:
            break

    return {
        "provider": "google_news_rss",
        "query": q,
        "rss_url": rss_url,
        "count": len(items),
        "items": items,
    }


# ───────────────────────────────────────────────────────────────────────────────
# Example usage (English/US)
# ───────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Example 1: NVIDIA — fetch recent items (up to 40) in English/US.
    # Example 1: NVIDIA — fetch recent items with all options set
    out_nvda = fetch_company_news(
        company="NVIDIA",  # required
        tickers=["NVDA"],  # optional: unquoted tokens
        synonyms=["NVIDIA Corporation"],  # optional: quoted phrases
        extra_terms=["GPU", "AI"],  # optional: quoted phrases to tighten relevance
        lang="en",  # UI/content language (default: "en")
        country="US",  # region bias (default: "US")
        days_back=3,  # optional recency filter (None = no filter)
        dedupe=True,  # de-dup on (title, canonical_url) (default: True)
        max_items=100,  # upper bound for this provider (default: 100)
        # max_results=100,                 # alias; if set, it overrides max_items
    )

    print(out_nvda["rss_url"])
    print("Items:", out_nvda["count"])
    print(out_nvda["items"][:2])
