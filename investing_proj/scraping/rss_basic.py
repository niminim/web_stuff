### 1) RSS feeds (zero-auth, super fast)

"""
Company-agnostic Google News RSS fetcher (English by default)

- Builds a boolean query like:  "NVIDIA" OR NVDA OR "NVIDIA Corporation"
- Generates a Google News RSS search URL (lang/country default to en/US)
- Fetches and parses items via feedparser
- Optionally filters by recency (days_back)
- Canonicalizes URLs (strips utm/gclid/fbclid etc.)
- De-dupes by (title, url)

Requirements:
  pip install feedparser python-dateutil
"""

import feedparser
from urllib.parse import quote_plus, urlparse, urlunparse, parse_qsl, urlencode
from datetime import datetime, timedelta, timezone
from dateutil import parser as dateparser
from typing import List, Dict, Optional


def _canonical_url(url: str) -> str:
    """
    Return a canonical form of the URL by removing common tracking parameters.
    This improves de-duplication because the same article often appears with
    different tracking query strings.
    """
    if not url:
        return url
    u = urlparse(url)
    # Remove typical tracking params
    remove = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_name",
        "gclid", "gbraid", "wbraid", "fbclid", "mc_cid", "mc_eid"
    }
    clean_qs = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k.lower() not in remove]
    return urlunparse(u._replace(query=urlencode(clean_qs)))


def build_company_query(
    company: str,
    tickers: Optional[List[str]] = None,
    synonyms: Optional[List[str]] = None,
    extra_terms: Optional[List[str]] = None,
) -> str:
    """
    Build a robust boolean query for Google News.

    Example:
      company="NVIDIA", tickers=["NVDA"], synonyms=["NVIDIA Corporation"]
      -> '"NVIDIA" OR "NVIDIA Corporation" OR NVDA'

    Notes:
      - We quote company names and synonyms to match exact phrases.
      - Tickers are usually left unquoted (short tokens).
      - You can add extra_terms (e.g., ["AI", "GPU"]) to tighten relevance.
    """
    tickers = tickers or []
    synonyms = synonyms or []
    extra_terms = extra_terms or []

    parts = []
    if company.strip():
        parts.append(f'"{company.strip()}"')
    parts += [f'"{s.strip()}"' for s in synonyms if s and s.strip()]
    parts += [t.strip() for t in tickers if t and t.strip()]
    parts += [f'"{t.strip()}"' for t in extra_terms if t and t.strip()]

    # Deduplicate while preserving order
    parts = list(dict.fromkeys(parts))
    return " OR ".join(parts) if parts else ""


def build_google_news_rss_url(query: str, lang: str = "en", country: str = "US") -> str:
    """
    Construct the Google News RSS search URL.

    Parameters:
      lang:    UI/content language (e.g., "en")
      country: region (e.g., "US")

    Google News expects:
      hl=<lang-country>, gl=<country>, ceid=<country>:<lang>

    Example: hl=en-US, gl=US, ceid=US:en
    """
    hl = f"{lang}-{country}"
    ceid = f"{country}:{lang}"
    return (
        "https://news.google.com/rss/search?"
        f"q={quote_plus(query)}&hl={hl}&gl={country}&ceid={ceid}"
    )


def _parse_published(published_str: str) -> Optional[datetime]:
    """
    Convert feed 'published'/'updated' strings into timezone-aware UTC datetimes.
    Returns None if parsing fails.
    """
    if not published_str:
        return None
    try:
        dt = dateparser.parse(published_str)
        if dt and not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def fetch_company_news(
    company: str,
    tickers: Optional[List[str]] = None,
    synonyms: Optional[List[str]] = None,
    lang: str = "en",          # <-- English default
    country: str = "US",       # <-- United States default
    days_back: Optional[int] = None,
    dedupe: bool = True,
) -> Dict[str, object]:
    """
    Fetch Google News RSS items for any company/ticker(s).

    Returns:
      {
        "query": <final boolean query used>,
        "rss_url": <generated RSS URL>,
        "count": <number of returned items>,
        "items": [
          {"title":..., "url":..., "published": <ISO8601 or None>, "source": ...},
          ...
        ]
      }
    """
    # Build query (quoted company/synonyms + unquoted tickers)
    q = build_company_query(company, tickers=tickers, synonyms=synonyms)

    # Generate the Google News RSS endpoint (English/US by default)
    rss_url = build_google_news_rss_url(q, lang=lang, country=country)

    # Parse the feed
    feed = feedparser.parse(rss_url)

    items: List[Dict[str, Optional[str]]] = []
    seen = set()  # for de-duplication
    cutoff = None
    if days_back is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)

    for e in feed.entries:
        title = (e.get("title") or "").strip()
        url = _canonical_url(e.get("link") or "")
        if not url:
            continue

        # Prefer 'published', fallback to 'updated'
        published_raw = e.get("published") or e.get("updated") or ""
        dt = _parse_published(published_raw)

        # Optional recency filter
        if cutoff and dt and dt < cutoff:
            continue

        # Source/publisher label if present
        src = "Google News"
        if isinstance(e.get("source"), dict):
            src = (e.get("source") or {}).get("title") or src

        record = {
            "title": title,
            "url": url,
            "published": dt.isoformat() if dt else None,
            "source": src,
        }

        # Simple de-dup on (title, canonical_url)
        if dedupe:
            key = (record["title"].lower(), record["url"].lower())
            if key in seen:
                continue
            seen.add(key)

        items.append(record)

    return {
        "query": q,
        "rss_url": rss_url,
        "count": len(items),
        "items": items,
    }


# -----------------------
# Example usage (English/US)
# -----------------------
if __name__ == "__main__":
    # 1) NVIDIA example (English, US)
    out_nvda = fetch_company_news("NVIDIA", tickers=["NVDA"], days_back=2)
    print(out_nvda["rss_url"])
    print("Items:", out_nvda["count"])

    # 2) Apple example (English, US)
    out_aapl = fetch_company_news("Apple Inc.", tickers=["AAPL"], days_back=2)
    print(out_aapl["rss_url"])
    print("Items:", out_aapl["count"])