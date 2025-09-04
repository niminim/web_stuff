"""
NewsAPI.org fetcher (company-agnostic)
--------------------------------------
- Endpoint used: /v2/everything
- Auth: pass API key via 'X-Api-Key' header (preferred)

Quick start:
  1) Paste your NewsAPI key into NEWSAPI_KEY below.
  2) python newsapi_fetch.py
  3) Start with modest caps (page_size<=100, max_results<=200) to be polite.

What you get back:
  dict {
    provider: "newsapi",
    query: <final boolean query>,
    request_url: <last page URL>,
    count: <unique items>,
    items: [ {title, url, published, source}, ... ]
  }

Notes:
  • /v2/everything supports: q, searchIn, from, to, language, sortBy, pageSize, page
  • sortBy: 'publishedAt' | 'relevancy' | 'popularity'
  • searchIn: 'title', 'description', 'content' (comma-separated)
  • Free plan & date windows have constraints—check your plan limits.
"""

from __future__ import annotations
import time, requests
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from datetime import datetime, timedelta, timezone

# ───────────────────────────────────────────────────────────────────────────────
# 1) YOUR NEWSAPI KEY (INLINE FOR SIMPLICITY)
#    ⚠ For production: use env vars or a secrets manager, not inline strings.
# ───────────────────────────────────────────────────────────────────────────────
NEWSAPI_KEY = "61b3e308bbf043789e83e53288f294be"   # ← replace me


# ───────────────────────────────────────────────────────────────────────────────
# 2) HELPERS (canonical URL, ISO8601 UTC, boolean query builder, session)
# ───────────────────────────────────────────────────────────────────────────────

def canonical_url(url: str) -> str:
    """
    Remove common tracking params so the *same* article collapses to one URL.
    This reduces duplicates (e.g., ?utm_source=..., ?gclid=...).
    """
    if not url:
        return url
    u = urlparse(url)
    drop = {
        "utm_source","utm_medium","utm_campaign","utm_term","utm_content","utm_name",
        "gclid","gbraid","wbraid","fbclid","mc_cid","mc_eid"
    }
    qs = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k.lower() not in drop]
    return urlunparse(u._replace(query=urlencode(qs)))

def iso8601_utc(dt: datetime) -> str:
    """
    Strict Zulu ISO8601 for NewsAPI (e.g., 2025-09-04T10:20:00Z).
    """
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")

def build_company_query(company: str,
                        tickers: Optional[List[str]] = None,
                        synonyms: Optional[List[str]] = None,
                        extra_terms: Optional[List[str]] = None) -> str:
    """
    Build a robust Boolean query:
      - company & synonyms quoted (exact phrase)
      - tickers unquoted (better headline match)
      - extra_terms quoted (optional relevance tightening)
    Example:
      '"NVIDIA" OR "NVIDIA Corporation" OR NVDA'
    """
    tickers = tickers or []
    synonyms = synonyms or []
    extra_terms = extra_terms or []

    parts: List[str] = []
    if company and company.strip():
        parts.append(f'"{company.strip()}"')
    parts += [f'"{s.strip()}"' for s in synonyms if s and s.strip()]
    parts += [t.strip() for t in tickers if t and t.strip()]           # unquoted tickers
    parts += [f'"{t.strip()}"' for t in extra_terms if t and t.strip()]

    # De-dup while preserving order
    seen, deduped = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p); deduped.append(p)
    return " OR ".join(deduped) if deduped else (company or "")

def make_session(total_retries: int = 2, backoff: float = 0.3) -> requests.Session:
    """
    Create a persistent Session:
      • Connection pooling
      • Light retry on 429/5xx with exponential backoff
    """
    from requests.adapters import HTTPAdapter
    try:
        from urllib3.util.retry import Retry
    except Exception:
        from requests.packages.urllib3.util.retry import Retry  # type: ignore
    retry = Retry(
        total=total_retries, read=total_retries, connect=total_retries,
        backoff_factor=backoff, status_forcelist=(429,500,502,503,504),
        allowed_methods=frozenset(["GET"]), raise_on_status=False
    )
    s = requests.Session()
    a = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=32)
    s.mount("http://", a); s.mount("https://", a)
    s.headers.update({"User-Agent": "newsapi-fetcher/1.0"})
    return s


# ───────────────────────────────────────────────────────────────────────────────
# 3) MAIN FETCHER (company-agnostic)
# ───────────────────────────────────────────────────────────────────────────────

def fetch_newsapi(company: str,
                  *,
                  tickers: Optional[List[str]] = None,
                  synonyms: Optional[List[str]] = None,
                  extra_terms: Optional[List[str]] = None,
                  language: str = "en",
                  days_back: int = 2,
                  max_results: int = 100,       # total items to collect
                  page_size: int = 100,         # per-page cap (NewsAPI max is 100)
                  search_in: str = "title,description",  # add ',content' if desired
                  sort_by: str = "publishedAt", # 'publishedAt' | 'relevancy' | 'popularity'
                  sources: Optional[List[str]] = None,   # e.g., ["bbc-news","reuters"]
                  domains: Optional[List[str]] = None,   # e.g., ["reuters.com","wsj.com"]
                  exclude_domains: Optional[List[str]] = None,
                  session: Optional[requests.Session] = None,
                  dedupe: bool = True,
                  inter_page_sleep: float = 0.15,
                  timeout: int = 20,
                  api_key: Optional[str] = None) -> Dict[str, object]:
    """
    Query NewsAPI /v2/everything for company-related articles.

    Parameters of interest:
      - language: 'en' recommended for English-only sentiment models
      - search_in: 'title,description' (fast) or include 'content' for broader recall
      - sort_by: 'publishedAt' for freshness, 'relevancy' for topical ranking
      - sources/domains/exclude_domains: optional inclusion/exclusion filters

    Returns:
      {
        "provider": "newsapi",
        "query": <final Boolean query used>,
        "request_url": <last page URL>,
        "count": <unique items>,
        "items": [{title, url, published, source}, ...]
      }
    """
    key = (api_key or NEWSAPI_KEY).strip()
    if not key or key == "PASTE_YOUR_REAL_NEWSAPI_KEY_HERE":
        raise RuntimeError("NewsAPI key missing. Set NEWSAPI_KEY or pass api_key=...")

    # Build boolean query + time window
    q = build_company_query(company, tickers=tickers, synonyms=synonyms, extra_terms=extra_terms)
    now = datetime.now(timezone.utc)
    dt_from = iso8601_utc(now - timedelta(days=days_back))
    dt_to   = iso8601_utc(now)

    # Defensive caps
    page_size = max(1, min(int(page_size), 100))
    max_results = max(1, int(max_results))

    s = session or make_session()
    endpoint = "https://newsapi.org/v2/everything"
    headers = {"X-Api-Key": key}

    seen: set[Tuple[str, str]] = set()  # (lower(title), lower(canonical_url))
    out: List[Dict[str, str]] = []
    page, fetched, last_url = 1, 0, endpoint

    while fetched < max_results:
        n_to_fetch = min(page_size, max_results - fetched)

        params = {
            "q": q or company,                # NewsAPI requires at least q/sources/domains
            "language": language,
            "from": dt_from,
            "to": dt_to,
            "searchIn": search_in,            # 'title,description' or include 'content'
            "sortBy": sort_by,                # 'publishedAt' | 'relevancy' | 'popularity'
            "pageSize": n_to_fetch,           # <= 100
            "page": page,                     # 1-based
        }
        if sources:
            params["sources"] = ",".join(sources)
        if domains:
            params["domains"] = ",".join(domains)
        if exclude_domains:
            params["excludeDomains"] = ",".join(exclude_domains)

        r = s.get(endpoint, headers=headers, params=params, timeout=timeout)
        last_url = r.url

        # Handle common error cases explicitly to aid debugging
        if r.status_code in (401, 426, 429):  # unauthorized / upgrade required / rate limit
            try:
                detail = r.json()
            except Exception:
                detail = {"raw": r.text}
            raise RuntimeError(
                f"NewsAPI error {r.status_code}.\n"
                f"- URL tried: {last_url}\n"
                f"- Response: {detail}\n"
                "Checks:\n"
                "  • Is your API key valid/active? (401)\n"
                "  • Are you exceeding your plan limits or endpoint access? (429/426)\n"
                "  • Try smaller pageSize, fewer pages, or a shorter date window."
            )

        r.raise_for_status()
        payload = r.json()

        # NewsAPI wraps responses with 'status' and 'articles'
        if payload.get("status") != "ok":
            raise RuntimeError(f"NewsAPI returned non-ok status: {payload}")

        articles = payload.get("articles", []) or []
        if not articles:
            break

        for a in articles:
            title = (a.get("title") or "").strip()
            url = canonical_url(a.get("url") or "")
            if not url or not title:
                continue
            key_ = (title.lower(), url.lower())
            if dedupe and key_ in seen:
                continue
            seen.add(key_)
            out.append({
                "title": title,
                "url": url,
                "published": a.get("publishedAt"),                 # ISO8601 UTC string
                "source": (a.get("source") or {}).get("name",""),  # publisher label
            })
            fetched += 1
            if fetched >= max_results:
                break

        page += 1
        if inter_page_sleep:
            time.sleep(inter_page_sleep)

    return {"provider":"newsapi","query":q,"request_url":last_url,"count":len(out),"items":out}


# ───────────────────────────────────────────────────────────────────────────────
# 4) EXAMPLE USAGE
# ───────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Example 1: NVIDIA — fresh news, English only
    res_nvda = fetch_newsapi(
        company="NVIDIA",
        tickers=["NVDA"],
        synonyms=["NVIDIA Corporation"],
        language="en",
        days_back=2,
        max_results=100,
        page_size=100,
        search_in="title,description",
        sort_by="publishedAt",
        api_key=NEWSAPI_KEY,
    )
    print("[NVIDIA]", res_nvda["count"], "items")
    print("Sample:", res_nvda["items"][:2], "\n")

    # Example 2: Apple — relevancy sort, include 'content' in search scope
    res_aapl = fetch_newsapi(
        company="Apple Inc.",
        tickers=["AAPL"],
        synonyms=["Apple"],
        language="en",
        days_back=3,
        max_results=80,
        page_size=80,
        search_in="title,description,content",
        sort_by="relevancy",
        api_key=NEWSAPI_KEY,
    )
    print("[Apple]", res_aapl["count"], "items")
    print("Sample:", res_aapl["items"][:2])
