# main.py
from __future__ import annotations
import os
import sys
import argparse
from typing import List, Optional

# =============================================================================
# Minimal import bootstrap (fastest fix)
# =============================================================================
# You can also override via env: PROJECT_ROOT=/abs/path/to/investing_proj
PROJECT_ROOT = os.getenv("PROJECT_ROOT") or os.path.expanduser("~/venv/web_stuff/investing_proj")

# Fail fast if the path is wrong
assert os.path.isdir(PROJECT_ROOT), f"Not a directory: {PROJECT_ROOT}"
assert os.path.isfile(os.path.join(PROJECT_ROOT, "extract_articles.py")), \
       f"extract_articles.py not found at {PROJECT_ROOT}"
assert os.path.isfile(os.path.join(PROJECT_ROOT, "read_articles.py")), \
       f"read_articles.py not found at {PROJECT_ROOT}"

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from extract_articles import fetch_all_news
from read_articles import enrich_res_with_text


# =============================================================================
# Helpers
# =============================================================================
def _split_csv(val: Optional[str]) -> List[str]:
    if not val:
        return []
    return [x.strip() for x in val.split(",") if x.strip()]

def print_quick_stats(res: dict) -> None:
    print("Attempted:", res.get("attempted"))
    print("Success flags:", res.get("ok"))
    print("Errors:", res.get("errors"))

    prov = res.get("providers", {})
    def _count(name: str) -> int:
        obj = prov.get(name)
        return obj.get("count", 0) if obj else 0

    print("RSS count:",     _count("google_news_rss"))
    print("GNews count:",   _count("gnews"))
    print("NewsAPI count:", _count("newsapi"))

    merged = res.get("merged", {})
    items = merged.get("items", []) or []
    print("Merged count:", merged.get("count", len(items)))

    for i, it in enumerate(items[:5], 1):
        print(f"{i}. [{it.get('provider')}] {it.get('source')} — {it.get('title')} ({it.get('published')})")

    gitems = [x for x in items if x.get("provider") == "gnews"]
    print("GNews (merged) count:", len(gitems))
    for i, it in enumerate(gitems[:5], 1):
        print(f"{i}. [gnews] {it.get('source')} — {it.get('title')} ({it.get('published')})")

    nitems = [x for x in items if x.get('provider') == 'newsapi']
    print("NewsAPI (merged) count:", len(nitems))
    for i, it in enumerate(nitems[:5], 1):
        print(f"{i}. [newsapi] {it.get('source')} — {it.get('title')} ({it.get('published')})")

def save_output(kind: Optional[str], out_base: str, res: dict) -> None:
    if not kind:
        return
    merged = res.get("merged", {})
    items = merged.get("items", []) or []
    if not items:
        print("Nothing to save.")
        return

    import pandas as pd  # lazy
    df = pd.DataFrame(items)
    path = f"{out_base}.{kind}"
    if kind == "csv":
        df.to_csv(path, index=False)
    elif kind == "json":
        df.to_json(path, orient="records", force_ascii=False)
    elif kind == "parquet":
        df.to_parquet(path, index=False)
    print(f"Saved {len(df)} rows to {path}")


# =============================================================================
# Core job
# =============================================================================
def run_job(
    *,
    company: Optional[str],
    tickers: List[str],
    synonyms: List[str],
    extra_terms: List[str],
    gnews_token: Optional[str],
    newsapi_key: Optional[str],
    enrich: bool,
    use_playwright: bool,
    max_workers: int,
    save: Optional[str],
    out: str,
) -> dict:
    # --- fetch ---
    res = fetch_all_news(
        company=company,
        tickers=tickers,
        synonyms=synonyms,
        extra_terms=extra_terms,
        gnews_token=gnews_token,
        newsapi_key=newsapi_key,
    )

    # --- quick stats ---
    print_quick_stats(res)

    # --- enrich (optional) ---
    if enrich:
        res = enrich_res_with_text(
            res,
            max_workers=max_workers,
            use_playwright=use_playwright,
        )

    # --- save (optional) ---
    save_output(save, out, res)
    return res


# =============================================================================
# CLI
# =============================================================================
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fetch & enrich company news (Google News RSS / GNews / NewsAPI)."
    )
    # Inputs
    p.add_argument("--company", type=str, help="Company name (e.g., 'Apple').")
    p.add_argument("--tickers", type=str, help="Comma-separated tickers (e.g., 'AAPL,APPL34').")
    p.add_argument("--synonyms", type=str, help="Comma-separated synonyms / legal names.")
    p.add_argument("--extra-terms", type=str, help="Comma-separated extra search terms (e.g., 'AI,GPU').")

    # API keys (env fallback)
    p.add_argument("--gnews-token", type=str, help="GNews token (fallback: $GNEWS_TOKEN).")
    p.add_argument("--newsapi-key", type=str, help="NewsAPI key (fallback: $NEWSAPI_KEY).")

    # Enrichment & performance
    p.add_argument("--enrich", action="store_true", help="Fetch article body text.")
    p.add_argument("--no-playwright", action="store_true", help="Disable Playwright during enrichment.")
    p.add_argument("--max-workers", type=int, default=16, help="Threads for enrichment (default: 16).")

    # Output
    p.add_argument("--save", choices=["csv", "json", "parquet"], help="Save merged items.")
    p.add_argument("--out", type=str, default="news_dump", help="Output filename without extension.")
    return p

def resolve_inputs_from_cli(args: argparse.Namespace) -> dict:
    company = args.company
    tickers = _split_csv(args.tickers)
    synonyms = _split_csv(args.synonyms)
    extra_terms = _split_csv(args.extra_terms)

    if not company and not tickers and not synonyms:
        raise SystemExit("Provide at least --company or --tickers/--synonyms.")

    gnews_token = args.gnews_token or os.getenv("GNEWS_TOKEN")
    newsapi_key = args.newsapi_key or os.getenv("NEWSAPI_KEY")

    return {
        "company": company,
        "tickers": tickers,
        "synonyms": synonyms,
        "extra_terms": extra_terms,
        "gnews_token": gnews_token,
        "newsapi_key": newsapi_key,
        "enrich": args.enrich,
        "use_playwright": not args.no_playwright,
        "max_workers": args.max_workers,
        "save": args.save,
        "out": args.out,
    }

def main_cli() -> None:
    parser = build_parser()
    args = parser.parse_args()
    inputs = resolve_inputs_from_cli(args)
    run_job(**inputs)


# =============================================================================
# Dual-mode entry
# =============================================================================
if __name__ == "__main__":
    # Detect PyCharm / debugger console (has --mode / --host / --port args)
    pydev_args = {"--mode", "--host", "--port"}
    has_debugger_args = any(a.split("=")[0] in pydev_args for a in sys.argv[1:])

    if len(sys.argv) > 1 and not has_debugger_args:
        # CLI mode
        main_cli()
    else:
        # IDE mode (ignore debugger args from PyCharm console)
        company: Optional[str] = "Apple"
        tickers: List[str] = ["AAPL"]
        synonyms: List[str] = ["Apple Inc."]
        extra_terms: List[str] = ["iPhone", "Mac"]

        gnews_token: Optional[str] = os.getenv("GNEWS_TOKEN") or "TOKEN"
        newsapi_key: Optional[str] = os.getenv("NEWSAPI_KEY") or "TOKEN"

        enrich: bool = True
        use_playwright: bool = True
        max_workers: int = 12
        save: Optional[str] = None
        out: str = "news_dump_ide"

        res = run_job(
            company = "NVIDIA",
            tickers = ["NVDA"],
            synonyms = ["NVIDIA Corporation"],
            extra_terms = [],
            gnews_token = "a0a597344c0c69d79f50ddb483743b7f",
            newsapi_key = "61b3e308bbf043789e83e53288f294be",
            enrich=enrich,
            use_playwright=use_playwright,
            max_workers=max_workers,
            save=save,
            out=out,
        )

        from pprint import pprint
        pprint((res.get("merged") or {}).get("items", [])[:3])
