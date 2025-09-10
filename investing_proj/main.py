# main.py
from __future__ import annotations
import os, sys
from typing import List

def _ensure_project_root():
    """Add project root to sys.path only if needed (when run from a random CWD)."""
    here = os.path.abspath(os.path.dirname(__file__))           # .../investing_proj
    if here not in sys.path:
        sys.path.insert(0, here)

try:
    from extract_articles import fetch_all_news
    from read_articles import enrich_res_with_text
except ModuleNotFoundError:
    _ensure_project_root()
    from extract_articles import fetch_all_news
    from read_articles import enrich_res_with_text

def main():
    # --- config ---
    company = "NVIDIA"
    tickers: List[str] = ["NVDA"]
    synonyms: List[str] = ["NVIDIA Corporation"]

    # Prefer env vars over hardcoding
    gnews_token = os.getenv("GNEWS_TOKEN") or "a0a597344c0c69d79f50ddb483743b7f"   # dev fallback
    newsapi_key = os.getenv("NEWSAPI_KEY") or "61b3e308bbf043789e83e53288f294be"   # dev fallback

    # --- fetch ---
    res = fetch_all_news(
        company,
        tickers=tickers,
        synonyms=synonyms,
        # extra_terms=["GPU", "AI"],
        gnews_token=gnews_token,
        newsapi_key=newsapi_key,
    )

    # --- quick stats ---
    print("Attempted:", res["attempted"])
    print("Success flags:", res["ok"])
    print("Errors:", res["errors"])

    prov = res["providers"]
    print("RSS count:",     prov["google_news_rss"]["count"] if prov["google_news_rss"] else 0)
    print("GNews count:",   prov["gnews"]["count"]           if prov["gnews"]           else 0)
    print("NewsAPI count:", prov["newsapi"]["count"]         if prov["newsapi"]         else 0)

    print("Merged count:", res["merged"]["count"])
    for i, it in enumerate(res["merged"]["items"][:5], 1):
        print(f"{i}. [{it.get('provider')}] {it.get('source')} — {it.get('title')} ({it.get('published')})")

    # Provider-specific previews
    gitems = [x for x in res["merged"]["items"] if x.get("provider") == "gnews"]
    print("GNews (merged) count:", len(gitems))
    for i, it in enumerate(gitems[:5], 1):
        print(f"{i}. [gnews] {it.get('source')} — {it.get('title')} ({it.get('published')})")

    nitems = [x for x in res["merged"]["items"] if x.get("provider") == "newsapi"]
    print("NewsAPI (merged) count:", len(nitems))
    for i, it in enumerate(nitems[:5], 1):
        print(f"{i}. [newsapi] {it.get('source')} — {it.get('title')} ({it.get('published')})")

    # --- enrich ---
    res = enrich_res_with_text(res, max_workers=16, use_playwright=True)
    # (optionally: save to CSV/Parquet here)

if __name__ == "__main__":
    main()
