# extract_articles.py
from __future__ import annotations

import os
import sys
from typing import List


def _ensure_project_root_for_script():
    """
    Add project root to sys.path ONLY when this file is run directly or from a random CWD.
    When imported from main.py or another module with correct working dir, this is a no-op.
    """
    here = os.path.abspath(os.path.dirname(__file__))                  # .../investing_proj
    if here not in sys.path:
        sys.path.insert(0, here)

# Import-time: assume proper package context; fall back for direct script exec
try:
    from scrape_articles.news_fetchers import fetch_all_news
except ModuleNotFoundError:
    _ensure_project_root_for_script()
    from scrape_articles.news_fetchers import fetch_all_news  # retry

def run_fetch(
    company: str,
    *,
    tickers: List[str] | None = None,
    synonyms: List[str] | None = None,
    extra_terms: List[str] | None = None,
    gnews_token: str | None = None,
    newsapi_key: str | None = None,
):
    res = fetch_all_news(
        company,
        tickers=tickers,
        synonyms=synonyms,
        extra_terms=extra_terms,
        gnews_token=gnews_token,
        newsapi_key=newsapi_key,
    )

    # What providers were attempted / status
    print("Attempted:", res["attempted"])
    print("Success flags:", res["ok"])
    print("Errors:", res["errors"])

    # Provider-specific counts
    prov = res["providers"]
    print("RSS count:", prov["google_news_rss"]["count"] if prov["google_news_rss"] else 0)
    print("GNews count:",     prov["gnews"]["count"]     if prov["gnews"]     else 0)
    print("NewsAPI count:",   prov["newsapi"]["count"]   if prov["newsapi"]   else 0)

    # Merged view (preview)
    print("Merged count:", res["merged"]["count"])
    for i, it in enumerate(res["merged"]["items"][:5], 1):
        print(f"{i}. [{it.get('provider')}] {it.get('source')} — {it.get('title')} ({it.get('published')})")

    # Only GNews
    gitems = [x for x in res["merged"]["items"] if x.get("provider") == "gnews"]
    print("GNews (merged) count:", len(gitems))
    for i, it in enumerate(gitems[:5], 1):
        print(f"{i}. [gnews] {it.get('source')} — {it.get('title')} ({it.get('published')})")

    # Only NewsAPI
    nitems = [x for x in res["merged"]["items"] if x.get("provider") == "newsapi"]
    print("NewsAPI (merged) count:", len(nitems))
    for i, it in enumerate(nitems[:5], 1):
        print(f"{i}. [newsapi] {it.get('source')} — {it.get('title')} ({it.get('published')})")

    return res

def main():
    # Prefer env vars over hardcoding
    company = os.getenv("COMPANY", "NVIDIA")
    tickers = ["NVDA"] if company.upper() == "NVIDIA" else None
    synonyms = [f"{company} Corporation"]

    gnews_token = os.getenv("GNEWS_TOKEN")
    newsapi_key = os.getenv("NEWSAPI_KEY")

    # If you insist on hardcoding during local dev, do it like this (fallback only):
    # gnews_token = gnews_token or "YOUR_GNEWS_TOKEN"
    # newsapi_key = newsapi_key or "YOUR_NEWSAPI_KEY"

    run_fetch(
        company,
        tickers=tickers,
        synonyms=synonyms,
        # extra_terms=["GPU", "AI"],
        gnews_token="a0a597344c0c69d79f50ddb483743b7f",
        newsapi_key="61b3e308bbf043789e83e53288f294be",
    )

if __name__ == "__main__":
    main()
