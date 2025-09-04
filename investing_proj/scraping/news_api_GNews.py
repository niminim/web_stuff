### 2) News APIs (Bing News / NewsAPI / GNews / etc.)
"""
GNews fetcher (company-agnostic) — token auth, retries, pagination, de-dup
----------------------------------------------------------------------------

Quick start:
  1) Paste your GNews token into GNEWS_TOKEN below.
  2) Run this file directly:  python gnews_fetch.py
  3) Start small (max_results=10, per_page=10) if you're on a free tier.

What this module does:
  • Builds a Boolean query for any company (with optional tickers/synonyms)
  • Calls GNews /search with a UTC date window (from/to)
  • Uses a persistent requests.Session with retry + connection pooling
  • Paginates until max_results are collected (or results end)
  • Canonicalizes URLs (removes tracking params) and de-dupes
  • Normalizes each item: {title, url, published, source}

Why these design choices:
  • Server-side filtering (q/from/to/lang) reduces local post-processing
  • Canonical URLs + (title, url) dedupe stabilizes downstream scoring
  • Modest retry/backoff avoids transient 429/5xx failures
  • Explicit 403 handling surfaces helpful diagnostics when plan/params mismatch
"""

from __future__ import annotations
import time
import requests
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from datetime import datetime, timedelta, timezone

# ───────────────────────────────────────────────────────────────────────────────
# 1) YOUR GNEWS TOKEN (INLINE FOR SIMPLICITY)
#    ⚠ For production, prefer environment variables or a secrets manager
#       (e.g., os.environ['GNEWS_TOKEN'], AWS/GCP secret stores, etc.).
# ───────────────────────────────────────────────────────────────────────────────
GNEWS_TOKEN = "PASTE_YOUR_REAL_TOKEN_HERE"   # ← replace with your token


# ───────────────────────────────────────────────────────────────────────────────
# 2) SMALL, REUSABLE HELPERS
# ───────────────────────────────────────────────────────────────────────────────

def canonical_url(url: str) -> str:
    """
    Remove common tracking parameters so the *same* article URL from different
    sources (or with UTM tags) collapses to a single canonical form.
    This improves de-duplication and prevents double-counting.
    """
    if not url:
        return url
    u = urlparse(url)
    # Common analytics / campaign params that don't change the content
    drop = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_name",
        "gclid", "gbraid", "wbraid", "fbclid", "mc_cid", "mc_eid"
    }
    # Rebuild the query string while skipping tracking params (case-insensitive)
    qs = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k.lower() not in drop]
    return urlunparse(u._replace(query=urlencode(qs)))


def iso8601_utc(dt: datetime) -> str:
    """
    Convert naive/aware datetimes to strict Zulu ISO8601 (e.g., 2025-09-04T10:20:00Z).
    GNews accepts ISO8601; using Z avoids timezone ambiguity.
    """
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_company_query(
    company: str,
    tickers: Optional[List[str]] = None,
    synonyms: Optional[List[str]] = None,
    extra_terms: Optional[List[str]] = None,
) -> str:
    """
    Build a robust Boolean query for GNews search.

    Rules:
      • Company & synonyms are quoted for exact phrase matching
      • Tickers are left unquoted (short tokens—better match rate)
      • extra_terms (quoted) are optional relevance tightenings (“earnings”, “GPU”)

    Example:
      company="NVIDIA", tickers=["NVDA"], synonyms=["NVIDIA Corporation"]
      -> '"NVIDIA" OR "NVIDIA Corporation" OR NVDA'
    """
    tickers = tickers or []
    synonyms = synonyms or []
    extra_terms = extra_terms or []

    parts: List[str] = []
    if company and company.strip():
        parts.append(f'"{company.strip()}"')
    parts += [f'"{s.strip()}"' for s in synonyms if s and s.strip()]
    parts += [t.strip() for t in tickers if t and t.strip()]           # keep tickers unquoted
    parts += [f'"{t.strip()}"' for t in extra_terms if t and t.strip()]

    # De-duplicate while preserving order to avoid bloated queries:
    seen, deduped = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return " OR ".join(deduped) if deduped else (company or "")


def make_session(total_retries: int = 2, backoff: float = 0.3) -> requests.Session:
    """
    Create a persistent HTTP session with:
      • Connection pooling (faster than new connection per request)
      • Modest retries on transient errors (429/5xx) with exponential backoff

    Keep retries conservative to respect rate limits and provider TOS.
    """
    from requests.adapters import HTTPAdapter
    try:
        from urllib3.util.retry import Retry
    except Exception:
        # For older vendored urllib3 in some requests builds
        from requests.packages.urllib3.util.retry import Retry  # type: ignore

    retry = Retry(
        total=total_retries,
        read=total_retries,
        connect=total_retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),  # typical transient conditions
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,  # don't raise inside urllib3; we handle after .get()
    )
    sess = requests.Session()
    adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=32)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    sess.headers.update({"User-Agent": "gnews-fetcher/1.2"})  # friendly UA
    return sess


# ───────────────────────────────────────────────────────────────────────────────
# 3) MAIN FETCHER
# ───────────────────────────────────────────────────────────────────────────────

def fetch_gnews(
    company: str,
    *,
    tickers: Optional[List[str]] = None,
    synonyms: Optional[List[str]] = None,
    lang: str = "en",
    country: str = "us",
    days_back: int = 2,
    max_results: int = 40,        # 👍 safe overall cap; adjust to your plan
    per_page: int = 10,           # 👍 free tiers often allow up to 10 per call
    search_in: str = "title,description",  # add ",content" on paid plans if needed
    sortby: str = "publishedAt",  # or "relevance"
    expand_content: bool = False, # paid feature for full content
    session: Optional[requests.Session] = None,
    dedupe: bool = True,
    inter_page_sleep: float = 0.15,  # polite pause between pages
    timeout: int = 20,               # network timeout per request (seconds)
    token: Optional[str] = None,     # override inline token if desired
) -> Dict[str, object]:
    """
    Query GNews for company-related articles and return normalized items.

    Parameters (high-impact):
      • company/tickers/synonyms: shape the Boolean `q` for relevance
      • days_back: recency window; kept server-side (fewer items to filter locally)
      • max_results/per_page: total vs per-page fetch limits (respect plan caps)
      • search_in: scope fields to search; narrower = less noise, faster
      • sortby: "publishedAt" for freshness or "relevance" for topicality
      • dedupe: enable to avoid source reprints double-counting

    Returns:
      {
        "provider": "gnews",
        "query": <final Boolean query used>,
        "request_url": <last request URL for debugging>,
        "count": <number of unique items>,
        "items": [
          {"title": str, "url": str, "published": str(ISO8601 UTC), "source": str},
          ...
        ]
      }
    """
    # Resolve token (prefer passed arg; fallback to inline constant)
    token = (token or GNEWS_TOKEN).strip()
    if not token or token == "PASTE_YOUR_REAL_TOKEN_HERE":
        raise RuntimeError("GNews token missing. Set GNEWS_TOKEN or pass token=...")

    # Build Boolean query + time window (convert to Zulu ISO8601)
    q = build_company_query(company, tickers=tickers, synonyms=synonyms)
    now = datetime.now(timezone.utc)
    dt_from = iso8601_utc(now - timedelta(days=days_back))
    dt_to   = iso8601_utc(now)

    # Normalize caps defensively (provider may enforce stricter per-request max)
    per_page = max(1, min(int(per_page), 100))
    max_results = max(1, int(max_results))

    s = session or make_session()
    endpoint = "https://gnews.io/api/v4/search"

    # Track seen articles for de-duplication using (lower(title), lower(canonical_url))
    seen: set[Tuple[str, str]] = set()
    out: List[Dict[str, str]] = []

    page = 1              # GNews uses 1-based pagination
    fetched = 0           # number of unique items collected so far
    last_url = endpoint   # last URL called (useful for debugging errors)

    # Loop pages until we reach max_results or results run out
    while fetched < max_results:
        n_to_fetch = min(per_page, max_results - fetched)
        params = {
            "q": q or company,            # fallback to plain company if query is empty
            "lang": lang,
            "country": country,
            "from": dt_from,
            "to": dt_to,
            "max": n_to_fetch,            # per-page limit (respect plan cap)
            "in": search_in,              # which fields to search in
            "sortby": sortby,             # freshness or relevance
            "page": page,                 # current page (1-based)
            "token": token,               # ✅ GNews expects 'token', not 'apikey'
        }
        if expand_content:
            params["expand"] = "content"  # only on paid plans

        # Make request with timeout; keep last_url for diagnostics
        r = s.get(endpoint, params=params, timeout=timeout)
        last_url = r.url

        # GNews often returns 403 for token/plan issues; show precise diagnostics
        if r.status_code == 403:
            try:
                detail = r.json()
            except Exception:
                detail = {"raw": r.text}
            raise RuntimeError(
                "GNews returned 403 Forbidden.\n"
                f"- URL tried: {last_url}\n"
                f"- Response: {detail}\n"
                "Quick checks:\n"
                "  • Is the token valid/active?\n"
                "  • Does your plan allow these params (e.g., max/per_page, expand)?\n"
                "  • Try smaller max/per_page (e.g., 10) and remove 'expand'/'in' temporarily."
            )

        # Raise for other HTTP errors (4xx/5xx) after retries
        r.raise_for_status()
        payload = r.json()

        # GNews wraps results under 'articles'
        articles = payload.get("articles", []) or []
        if not articles:
            break  # no more results/pages

        for a in articles:
            # Normalize and skip malformed rows early
            title = (a.get("title") or "").strip()
            url = canonical_url(a.get("url") or "")
            if not url or not title:
                continue

            # (title, url) de-dup keeps near-identical reposts from double-counting
            key = (title.lower(), url.lower())
            if dedupe and key in seen:
                continue
            seen.add(key)

            out.append({
                "title": title,
                "url": url,
                "published": a.get("publishedAt"),                 # UTC string per GNews docs
                "source": (a.get("source") or {}).get("name", ""),  # publisher label
            })

            fetched += 1
            if fetched >= max_results:
                break  # stop early if we hit the cap

        page += 1
        if inter_page_sleep:
            time.sleep(inter_page_sleep)  # small courtesy delay to be polite

    # Return a compact, pipeline-friendly structure
    return {
        "provider": "gnews",
        "query": q,
        "request_url": last_url,
        "count": len(out),
        "items": out,
    }


# ───────────────────────────────────────────────────────────────────────────────
# 4) EXAMPLE USAGE (run this file directly)
# ───────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Example 1: NVIDIA — keep caps within free-tier defaults first
    res_nvda = fetch_gnews(
        company="NVIDIA",
        tickers=["NVDA"],
        synonyms=["NVIDIA Corporation"],
        days_back=2,
        max_results=10,         # try 10 first; raise later if your plan allows
        per_page=10,
        search_in="title,description",
        sortby="publishedAt",
        token="a0a597344c0c69d79f50ddb483743b7f",
    )
    print("[NVIDIA]", res_nvda["count"], "items")
    print("Sample:", res_nvda["items"][:2], "\n")

    # Example 2: Apple — demonstrate 'relevance' sort
    res_aapl = fetch_gnews(
        company="Apple Inc.",
        tickers=["AAPL"],
        synonyms=["Apple"],
        days_back=3,
        max_results=10,
        per_page=10,
        sortby="relevance",
        token="a0a597344c0c69d79f50ddb483743b7f",
    )
    print("[Apple]", res_aapl["count"], "items")
    print("Sample:", res_aapl["items"][:2])

