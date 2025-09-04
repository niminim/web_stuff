"""
GNews fetcher (company-agnostic) — token auth, retries, pagination, de-dup
----------------------------------------------------------------------------

Quick start:
  1) Pass your GNews token via token=... (e.g., from env vars).
  2) Run this file directly:  python news_api_GNews.py
  3) Start small (max_results=10, per_page=10) if you're on a free tier.

What this module does:
  • Builds a Boolean query for any company (with optional tickers/synonyms/extra_terms)
  • Calls GNews /search with a UTC date window (from/to)
  • Uses a persistent requests.Session with retry + connection pooling
  • Paginates until max_results are collected (or results end)
  • Canonicalizes URLs (removes tracking params) and de-dupes
  • Normalizes each item: {title, url, published, source}
  • On HTTP 429 (rate limit), returns partial results with rate_limited=True

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
# Small, reusable helpers
# ───────────────────────────────────────────────────────────────────────────────

def canonical_url(url: str) -> str:
    """Remove tracking params to improve de-duplication."""
    if not url:
        return url
    u = urlparse(url)
    drop = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_name",
        "gclid", "gbraid", "wbraid", "fbclid", "mc_cid", "mc_eid"
    }
    qs = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k.lower() not in drop]
    return urlunparse(u._replace(query=urlencode(qs)))


def iso8601_utc(dt: datetime) -> str:
    """Convert datetime to strict ISO8601 Zulu string (UTC)."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_company_query(
    company: str,
    tickers: Optional[List[str]] = None,
    synonyms: Optional[List[str]] = None,
    extra_terms: Optional[List[str]] = None,
) -> str:
    """Build a robust Boolean query for GNews search."""
    tickers = tickers or []
    synonyms = synonyms or []
    extra_terms = extra_terms or []

    parts: List[str] = []
    if company and company.strip():
        parts.append(f'"{company.strip()}"')
    parts += [f'"{s.strip()}"' for s in synonyms if s and s.strip()]
    parts += [t.strip() for t in tickers if t and t.strip()]           # unquoted tickers
    parts += [f'"{t.strip()}"' for t in extra_terms if t and t.strip()]

    seen, deduped = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            deduped.append(p)
    return " OR ".join(deduped) if deduped else (company or "")


def make_session(total_retries: int = 2, backoff: float = 0.3) -> requests.Session:
    """Persistent session with connection pooling + modest retries."""
    from requests.adapters import HTTPAdapter
    try:
        from urllib3.util.retry import Retry
    except Exception:
        from requests.packages.urllib3.util.retry import Retry  # type: ignore

    retry = Retry(
        total=total_retries,
        read=total_retries,
        connect=total_retries,
        backoff_factor=backoff,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    sess = requests.Session()
    adapter = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=32)
    sess.mount("http://", adapter)
    sess.mount("https://", adapter)
    sess.headers.update({"User-Agent": "gnews-fetcher/1.3"})
    return sess


# ───────────────────────────────────────────────────────────────────────────────
# Main fetcher
# ───────────────────────────────────────────────────────────────────────────────

def fetch_gnews(
    company: str,
    *,
    tickers: Optional[List[str]] = None,
    synonyms: Optional[List[str]] = None,
    extra_terms: Optional[List[str]] = None,   # parity with other fetchers
    lang: str = "en",
    country: str = "us",
    days_back: int = 2,
    max_results: int = 20,
    per_page: int = 10,
    search_in: str = "title,description",
    sortby: str = "publishedAt",
    expand_content: bool = False,
    session: Optional[requests.Session] = None,
    dedupe: bool = True,
    inter_page_sleep: float = 0.15,
    timeout: int = 20,
    token: Optional[str] = None,               # must be passed in
) -> Dict[str, object]:
    """
    Query GNews for company-related articles.

    Parameters (high-impact):
      • company/tickers/synonyms/extra_terms: shape the Boolean `q`
      • days_back: recency window
      • max_results/per_page: fetch caps (respect plan)
      • sortby: "publishedAt" (freshness) or "relevance"
      • token: required GNews API token (pass from env or caller)

    On HTTP 429:
      • Returns the partial items gathered so far
      • Adds 'rate_limited': True to the return dict
    """
    token = (token or "").strip()
    if not token:
        raise RuntimeError("GNews token missing. Pass token=...")

    q = build_company_query(company, tickers=tickers, synonyms=synonyms, extra_terms=extra_terms)
    now = datetime.now(timezone.utc)
    dt_from = iso8601_utc(now - timedelta(days=days_back))
    dt_to   = iso8601_utc(now)

    per_page = max(1, min(int(per_page), 100))
    max_results = max(1, int(max_results))

    s = session or make_session()
    endpoint = "https://gnews.io/api/v4/search"

    seen: set[Tuple[str, str]] = set()
    out: List[Dict[str, str]] = []

    page = 1
    fetched = 0
    last_url = endpoint
    rate_limited = False
    retry_after_hdr: Optional[str] = None

    while fetched < max_results:
        n_to_fetch = min(per_page, max_results - fetched)
        params = {
            "q": q or company,
            "lang": lang,
            "country": country,
            "from": dt_from,
            "to": dt_to,
            "max": n_to_fetch,
            "in": search_in,
            "sortby": sortby,
            "page": page,
            "token": token,
        }
        if expand_content:
            params["expand"] = "content"

        r = s.get(endpoint, params=params, timeout=timeout)
        last_url = r.url

        # 403: token/plan issue — raise with diagnostics
        if r.status_code == 403:
            try:
                detail = r.json()
            except Exception:
                detail = {"raw": r.text}
            raise RuntimeError(
                "GNews returned 403 Forbidden.\n"
                f"- URL tried: {last_url}\n"
                f"- Response: {detail}\n"
                "Checks:\n"
                "  • Is the token valid/active?\n"
                "  • Does your plan allow these params?\n"
            )

        # 429: rate limited — return partial items gracefully
        if r.status_code == 429:
            rate_limited = True
            retry_after_hdr = r.headers.get("Retry-After")
            break

        # Other errors (after urllib3 retries)
        r.raise_for_status()
        payload = r.json()

        articles = payload.get("articles", []) or []
        if not articles:
            break

        for a in articles:
            title = (a.get("title") or "").strip()
            url = canonical_url(a.get("url") or "")
            if not title or not url:
                continue

            key = (title.lower(), url.lower())
            if dedupe and key in seen:
                continue
            seen.add(key)

            out.append({
                "title": title,
                "url": url,
                "published": a.get("publishedAt"),                  # ISO8601 UTC
                "source": (a.get("source") or {}).get("name", ""),  # publisher label
            })

            fetched += 1
            if fetched >= max_results:
                break

        page += 1
        if inter_page_sleep:
            time.sleep(inter_page_sleep)

    return {
        "provider": "gnews",
        "query": q,
        "request_url": last_url,
        "count": len(out),
        "items": out,
        "rate_limited": rate_limited,
        "rate_limit_retry_after": retry_after_hdr,  # may be None
    }


# ───────────────────────────────────────────────────────────────────────────────
# Example usage
# ───────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    token = os.getenv("GNEWS_TOKEN")  # best practice: load from env

    res = fetch_gnews(
        company="NVIDIA",
        tickers=["NVDA"],
        synonyms=["NVIDIA Corporation"],
        extra_terms=["GPU", "AI"],     # optional
        days_back=2,
        max_results=30,
        per_page=10,
        sortby="publishedAt",
        token="a0a597344c0c69d79f50ddb483743b7f",
    )
    print("[NVIDIA]", res["count"], "items")
    print("rate_limited:", res.get("rate_limited"), "retry_after:", res.get("rate_limit_retry_after"))
    print("Sample:", res["items"][:2])