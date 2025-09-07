
"""
# ---------------------------------------------------------------------
# Unified wrapper over three providers:
#   - Google News RSS (zero auth)
#   - GNews (token)
#   - NewsAPI (key)
#
# Design goals:
#   • Efficiency: run providers concurrently with a small thread pool.
#   • Zero extra caps: do NOT override provider defaults; just pass company/tickers/synonyms/extra_terms.
#   • Transparency: return full provider payloads + a merged view with per-item 'provider' and 'site'.
#   • Minimal assumptions: no dedupe and no re-sorting unless you choose to do so upstream.
#
# Usage:
#   from news_fetchers import fetch_all_news
#   res = fetch_all_news("NVIDIA", tickers=["NVDA"], synonyms=["NVIDIA Corporation"])
#   print(res["merged"]["count"])
#   for it in res["merged"]["items"][:5]:
#       print(it["provider"], "-", it["site"], "-", it["title"])
"""


from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional, Iterable, Tuple


import os
import sys

project_root = os.path.abspath("/investing_proj/scrape_articles")
sys.path.append(project_root)
print(sys.path)

# Import your existing provider modules
from news_api_GNews import fetch_gnews as _fetch_gnews
from newsapi import fetch_newsapi as _fetch_newsapi
from rss_basic import fetch_company_news as _fetch_rss


# ————————————————————————————————————————————————————————————————————————————
# Internal helpers
# ————————————————————————————————————————————————————————————————————————————

def _call_provider(
    provider: str,
    company: str,
    *,
    tickers=None,
    synonyms=None,
    extra_terms=None,
    gnews_token: str | None = None,
    newsapi_key: str | None = None,
):
    if provider == "rss":
        res = _fetch_rss(company, tickers=tickers, synonyms=synonyms, extra_terms=extra_terms)
        return "google_news_rss", res

    if provider == "gnews":
        res = _fetch_gnews(
            company,
            tickers=tickers,
            synonyms=synonyms,
            extra_terms=extra_terms,   # if you added extra_terms support in GNews
            token=gnews_token,         # ✅ forward token here
        )
        return "gnews", res

    if provider == "newsapi":
        res = _fetch_newsapi(
            company,
            tickers=tickers,
            synonyms=synonyms,
            extra_terms=extra_terms,
            api_key=newsapi_key,       # ✅ forward api_key here (NO constants)
        )
        return "newsapi", res

    raise ValueError(f"Unknown provider: {provider}")


# ————————————————————————————————————————————————————————————————————————————
# Public API
# ————————————————————————————————————————————————————————————————————————————

def fetch_all_news(
    company: str,
    *,
    tickers: Optional[List[str]] = None,
    synonyms: Optional[List[str]] = None,
    extra_terms: Optional[List[str]] = None,
    providers: Iterable[str] = ("rss", "gnews", "newsapi"),
    max_workers: int = 3,
    gnews_token: Optional[str] = None,     # passed through to GNews
    newsapi_key: Optional[str] = None,     # passed through to NewsAPI
) -> Dict[str, Any]:
    """
    Fetch news from all requested providers concurrently.
    - Does NOT change any provider defaults (no extra caps/filters).
    - Forwards tokens/keys only via arguments (no module-level secrets).
    - Returns both full per-provider payloads and a merged list with 'provider' set.

    Returns:
      {
        "company": str,
        "attempted": List[str],   # providers actually executed (order preserved)
        "ok": {"google_news_rss": bool, "gnews": bool, "newsapi": bool},
        "errors": {"google_news_rss": str|None, "gnews": str|None, "newsapi": str|None},
        "providers": {
            "google_news_rss": dict|None,
            "gnews": dict|None,
            "newsapi": dict|None
        },
        "merged": {
            "count": int,
            "items": [ {title,url,published,source,provider,...}, ... ]
        }
      }
    """
    # Normalize requested providers to a list and keep order
    requested = list(providers)

    ok = {"google_news_rss": False, "gnews": False, "newsapi": False}
    errors: Dict[str, Optional[str]] = {"google_news_rss": None, "gnews": None, "newsapi": None}
    results: Dict[str, Optional[Dict[str, Any]]] = {"google_news_rss": None, "gnews": None, "newsapi": None}

    # Submit tasks
    futures = {}
    attempted: List[str] = []
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for p in requested:
            # Schedule exactly what the caller asked for; let providers error if creds missing.
            fut = ex.submit(
                _call_provider,
                p,
                company,
                tickers=tickers,
                synonyms=synonyms,
                extra_terms=extra_terms,
                gnews_token=gnews_token,     # ✅ forward creds (no constants)
                newsapi_key=newsapi_key,     # ✅ forward creds (no constants)
            )
            futures[fut] = p
            attempted.append(p)

        for fut in as_completed(futures):
            req_p = futures[fut]
            try:
                provider_name, payload = fut.result()
                results[provider_name] = payload
                ok[provider_name] = True
            except Exception as e:
                # Record error against canonical provider key
                if req_p == "rss":
                    errors["google_news_rss"] = repr(e)
                elif req_p == "gnews":
                    errors["gnews"] = repr(e)
                elif req_p == "newsapi":
                    errors["newsapi"] = repr(e)

    # Build merged list in the requested order, annotate with provider, and ensure no 'site'
    merged_items: List[Dict[str, Any]] = []
    if "rss" in attempted and results["google_news_rss"]:
        for it in results["google_news_rss"].get("items", []) or []:
            if isinstance(it, dict):
                row = dict(it)
                row["provider"] = "google_news_rss"
                row.pop("site", None)  # ensure only 'source' remains
                merged_items.append(row)

    if "gnews" in attempted and results["gnews"]:
        for it in results["gnews"].get("items", []) or []:
            if isinstance(it, dict):
                row = dict(it)
                row["provider"] = "gnews"
                row.pop("site", None)
                merged_items.append(row)

    if "newsapi" in attempted and results["newsapi"]:
        for it in results["newsapi"].get("items", []) or []:
            if isinstance(it, dict):
                row = dict(it)
                row["provider"] = "newsapi"
                row.pop("site", None)
                merged_items.append(row)

    merged = {"count": len(merged_items), "items": merged_items}

    return {
        "company": company,
        "attempted": attempted,     # only what actually ran
        "ok": ok,
        "errors": errors,
        "providers": results,       # full raw payloads
        "merged": merged,           # flat view
    }


# So extra_terms are just extra words or phrases you want ANDed at the top level with ORs, quoted for phrase matching.
# They make queries more topically constrained. For example:
#
# extra_terms=["earnings"] → bias results toward financial coverage.
#
# extra_terms=["GPU","AI"] → bias toward product/technology context.
#
# They’re optional. If you don’t supply them, the query is just company + synonyms + tickers.