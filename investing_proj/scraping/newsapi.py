"""
NewsAPI.org fetcher (company-agnostic)
--------------------------------------
- Endpoint used: /v2/everything
- Auth: must pass API key via api_key=... argument.

Quick start:
  1) Put your NewsAPI key in an env var (e.g. NEWSAPI_KEY).
  2) Call fetch_newsapi(company, ..., api_key=os.getenv("NEWSAPI_KEY")).
  3) Start with modest caps (page_size<=100, max_results<=200) to be polite.

What you get back:
  dict {
    provider: "newsapi",
    query: <final boolean query>,
    request_url: <last page URL>,
    count: <unique items>,
    items: [ {title, url, published, source}, ... ],
    rate_limited: bool,                   # NEW: True if a 429 occurred
    rate_limit_retry_after: str|None      # NEW: Retry-After (or similar) header if present
  }
"""

from __future__ import annotations
import time, requests
from typing import List, Dict, Optional, Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from datetime import datetime, timedelta, timezone


# ───────────────────────────────────────────────────────────────────────────────
# HELPERS
# ───────────────────────────────────────────────────────────────────────────────

def canonical_url(url: str) -> str:
    """Remove tracking params so the *same* article collapses to one URL."""
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
    """Strict Zulu ISO8601 for NewsAPI (e.g., 2025-09-04T10:20:00Z)."""
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z")

def build_company_query(company: str,
                        tickers: Optional[List[str]] = None,
                        synonyms: Optional[List[str]] = None,
                        extra_terms: Optional[List[str]] = None) -> str:
    """Build a robust Boolean query: quoted company/synonyms, unquoted tickers, quoted extra_terms."""
    tickers = tickers or []
    synonyms = synonyms or []
    extra_terms = extra_terms or []

    parts: List[str] = []
    if company and company.strip():
        parts.append(f'"{company.strip()}"')
    parts += [f'"{s.strip()}"' for s in synonyms if s and s.strip()]
    parts += [t.strip() for t in tickers if t and t.strip()]
    parts += [f'"{t.strip()}"' for t in extra_terms if t and t.strip()]

    seen, deduped = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p); deduped.append(p)
    return " OR ".join(deduped) if deduped else (company or "")

def make_session(total_retries: int = 2, backoff: float = 0.3) -> requests.Session:
    """Persistent Session with pooling and modest retries."""
    from requests.adapters import HTTPAdapter
    try:
        from urllib3.util.retry import Retry
    except Exception:
        from requests.packages.urllib3.util.retry import Retry  # type: ignore
    retry = Retry(
        total=total_retries, read=total_retries, connect=total_retries,
        backoff_factor=backoff, status_forcelist=(429,500,502,503,504),
        allowed_methods=frozenset(["GET"]), raise_on_status=False,
        # If available in your urllib3, this respects server Retry-After headers:
        respect_retry_after_header=True,  # safe no-op on older versions
    )
    s = requests.Session()
    a = HTTPAdapter(max_retries=retry, pool_connections=16, pool_maxsize=32)
    s.mount("http://", a); s.mount("https://", a)
    s.headers.update({"User-Agent": "newsapi-fetcher/1.1"})
    return s


# ───────────────────────────────────────────────────────────────────────────────
# MAIN FETCHER
# ───────────────────────────────────────────────────────────────────────────────

def fetch_newsapi(company: str,
                  *,
                  tickers: Optional[List[str]] = None,
                  synonyms: Optional[List[str]] = None,
                  extra_terms: Optional[List[str]] = None,
                  language: str = "en",
                  days_back: int = 2,
                  max_results: int = 50,
                  page_size: int = 10,
                  search_in: str = "title,description",
                  sort_by: str = "publishedAt",
                  sources: Optional[List[str]] = None,
                  domains: Optional[List[str]] = None,
                  exclude_domains: Optional[List[str]] = None,
                  session: Optional[requests.Session] = None,
                  dedupe: bool = True,
                  inter_page_sleep: float = 0.15,
                  timeout: int = 20,
                  api_key: Optional[str] = None) -> Dict[str, object]:
    """
    Query NewsAPI /v2/everything for company-related articles.

    Parameters
    ----------
    api_key : str
        Required NewsAPI key (pass from env or caller).

    Soft 429 handling
    -----------------
    • On HTTP 429, returns partial items collected so far and sets:
        rate_limited=True, rate_limit_retry_after=<header if any>
    """
    key = (api_key or "").strip()
    if not key:
        raise RuntimeError("NewsAPI key missing. Pass api_key=...")

    q = build_company_query(company, tickers=tickers, synonyms=synonyms, extra_terms=extra_terms)
    now = datetime.now(timezone.utc)
    dt_from = iso8601_utc(now - timedelta(days=days_back))
    dt_to   = iso8601_utc(now)

    page_size = max(1, min(int(page_size), 100))
    max_results = max(1, int(max_results))

    s = session or make_session()
    endpoint = "https://newsapi.org/v2/everything"
    headers = {"X-Api-Key": key}

    seen: set[Tuple[str, str]] = set()
    out: List[Dict[str, str]] = []
    page, fetched, last_url = 1, 0, endpoint
    rate_limited = False
    retry_after_hdr: Optional[str] = None

    while fetched < max_results:
        n_to_fetch = min(page_size, max_results - fetched)

        params = {
            "q": q or company,
            "language": language,
            "from": dt_from,
            "to": dt_to,
            "searchIn": search_in,
            "sortBy": sort_by,
            "pageSize": n_to_fetch,
            "page": page,
        }
        if sources:
            params["sources"] = ",".join(sources)
        if domains:
            params["domains"] = ",".join(domains)
        if exclude_domains:
            params["excludeDomains"] = ",".join(exclude_domains)

        r = s.get(endpoint, headers=headers, params=params, timeout=timeout)
        last_url = r.url

        # Handle specific errors explicitly
        if r.status_code == 401:
            try:
                detail = r.json()
            except Exception:
                detail = {"raw": r.text}
            raise RuntimeError(
                f"NewsAPI error 401.\n- URL tried: {last_url}\n- Response: {detail}\n"
                "Checks:\n  • Is your API key valid/active?"
            )

        if r.status_code == 426:
            try:
                detail = r.json()
            except Exception:
                detail = {"raw": r.text}
            raise RuntimeError(
                f"NewsAPI error 426.\n- URL tried: {last_url}\n- Response: {detail}\n"
                "Checks:\n  • Endpoint/plan not available on your tier."
            )

        if r.status_code == 429:
            # Soft handling: keep partial results and exit loop gracefully.
            rate_limited = True
            retry_after_hdr = r.headers.get("Retry-After") or r.headers.get("X-RateLimit-Reset")
            break

        # Other errors after retries
        r.raise_for_status()
        payload = r.json()
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
                "published": a.get("publishedAt"),
                "source": (a.get("source") or {}).get("name",""),
            })
            fetched += 1
            if fetched >= max_results:
                break

        page += 1
        if inter_page_sleep:
            time.sleep(inter_page_sleep)

    return {
        "provider": "newsapi",
        "query": q,
        "request_url": last_url,
        "count": len(out),
        "items": out,
        "rate_limited": rate_limited,
        "rate_limit_retry_after": retry_after_hdr,
    }


# ───────────────────────────────────────────────────────────────────────────────
# EXAMPLE USAGE
# ───────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    res = fetch_newsapi(
        company="NVIDIA",
        tickers=["NVDA"],
        synonyms=["NVIDIA Corporation"],
        language="en",
        days_back=2,
        max_results=20,
        page_size=10,
        search_in="title,description",
        sort_by="publishedAt",
        api_key="TOKEN",
    )
    print("[NVIDIA]", res["count"], "items")
    print("rate_limited:", res.get("rate_limited"), "retry_after:", res.get("rate_limit_retry_after"))
    print("Sample:", res["items"][:2])
