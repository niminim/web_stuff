# -*- coding: utf-8 -*-
"""
GuruFocus summary scraper with requests -> Playwright fallback.

What this script does
---------------------
1) Fetches a ticker's GuruFocus "summary" page (requests first; optional Playwright fallback).
2) Parses the 5 main rank bars (0–10) + GF Score (0–100) and the "other indicators" table.
3) Converts results into a one-row DataFrame per ticker and appends to CSV.
4) Writes a per-ticker JSON file of the 5 ranks with **bolded** keys for nicer display in Markdown viewers.

Notes & Caveats
---------------
- Scraping may be restricted by the site's ToS. Prefer official APIs if available.
- DOM/classes may change; logic is best-effort.
- Playwright requires:
    pip install playwright
    python -m playwright install
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from random import uniform
from typing import Dict, Tuple, Optional, List

import pandas as pd
import requests
from bs4 import BeautifulSoup

# =========================
# Configuration / Globals
# =========================

BASE_URL = "https://www.gurufocus.com/stock/{ticker}/summary"

# Browser-like headers to reduce the chance of bot blocking
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}

# A single session improves reuse of HTTP connection + headers
SESSION = requests.Session()
SESSION.headers.update(BASE_HEADERS)
# `requests` timeout (seconds) for connect + read (tuple) or a single number
SESSION.timeout = 20  # type: ignore[attr-defined]  # (kept for readability; requests.Session doesn't use it directly)


# =========================
# Small utilities
# =========================

def _sleep_jitter(low: float = 0.35, high: float = 1.0) -> None:
    """Sleep a small random time to look less bot-like."""
    time.sleep(uniform(low, high))


def _soup(html: str) -> BeautifulSoup:
    """Create a BeautifulSoup object with a forgiving parser."""
    # If you have lxml installed, you can switch to 'lxml' for speed:
    # return BeautifulSoup(html, 'lxml')
    return BeautifulSoup(html, "html.parser")


# =========================
# Network fetchers
# =========================

def fetch_html_requests(url: str, max_retries: int = 3, timeout: float = 20.0) -> Tuple[Optional[str], Optional[int]]:
    """
    Try fetching HTML via `requests`.
    Returns: (html_text or None, status_code or None)
    """
    last_status: Optional[int] = None
    for attempt in range(1, max_retries + 1):
        try:
            _sleep_jitter()
            resp = SESSION.get(url, allow_redirects=True, timeout=timeout)
            last_status = resp.status_code

            if resp.status_code == 200 and resp.text:
                return resp.text, resp.status_code

            # Mild backoff on common "come back later" statuses
            if resp.status_code in (403, 429, 503):
                time.sleep(1.2 * attempt)
            else:
                break
        except requests.RequestException:
            time.sleep(1.0 * attempt)
    return None, last_status


def fetch_html_playwright(url: str, headless: bool = True, wait_selector: Optional[str] = None, wait_ms: int = 1500) -> str:
    """
    Fetch rendered HTML with Playwright/Chromium (executes JS).
    Only import Playwright if requested to keep lightweight by default.
    """
    from playwright.sync_api import sync_playwright  # local import to avoid hard dependency

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=BASE_HEADERS["User-Agent"],
            viewport={"width": 1366, "height": 768},
            java_script_enabled=True,
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")

        # Allow time for async widgets to render
        page.wait_for_timeout(wait_ms)

        # Optionally wait for a stable anchor element (best-effort)
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=5000)
            except Exception:
                pass

        html = page.content()
        context.close()
        browser.close()
        return html


# =========================
# Parsers (ranks, GF score, indicators)
# =========================

def extract_rank_score(soup: BeautifulSoup, rank_name: str, identifier: str) -> str:
    """
    Extract a 0–10 rank score.
    Logic:
      1) Find an <a> tag whose href contains `identifier`
      2) Find the next div.indicator-progress-bar-header
      3) Read the inner <div style="width: NN%;"> → score ≈ int(NN / 10)
    """
    anchor = soup.find("a", href=lambda href: href and identifier in href)
    if anchor:
        score_div = anchor.find_next("div", class_="indicator-progress-bar-header")
        if score_div and score_div.div:
            style = score_div.div.get("style", "")
            if "width:" in style:
                try:
                    pct = style.split("width:")[1].split("%")[0].strip()
                    score = int(float(pct) / 10.0)
                    return f"{score}/10"
                except (ValueError, IndexError):
                    pass
    return f"{rank_name} score not found."


def extract_gf_score(html_text: str) -> str:
    """
    Extract GF Score (0–100) from inline JS like: gf_score: 69
    Returns 'NN/100' or a not-found message.
    """
    if not html_text:
        return "GF Score not found."
    m = re.search(r"gf_score\s*:\s*(\d+)", html_text)
    if m:
        try:
            return f"{int(m.group(1))}/100"
        except ValueError:
            pass
    return "GF Score not found."


def extract_financial_data(soup: BeautifulSoup) -> Dict[str, str]:
    """
    Parse the "other indicators" table (best-effort).
    Returns a dict: {metric_name: value_text}
    """
    data: Dict[str, str] = {}
    rows = soup.find_all("tr", class_="stock-indicators-table-row")
    for row in rows:
        name_tag = row.find("td", class_="t-caption p-v-sm semi-bold")
        val_tag = row.find("span", class_="p-l-sm")
        if name_tag and val_tag:
            name = name_tag.get_text(strip=True)
            val = val_tag.get_text(strip=True)
            if name:
                data[name] = val
    return data


# =========================
# Data shaping / IO
# =========================

def _to_number(x) -> Optional[float] | str:
    """
    Convert numeric-like strings to float when possible.
    - '71.5%' → 71.5
    - '5/9'   → keep as '5/9' (rank fraction)
    - 'N/A', 'No Debt', '' → None
    """
    if x is None:
        return None
    s = str(x).strip()
    if not s or s.lower() in {"no", "none", "n/a", "na", "no debt"}:
        return None
    # Keep exact rank fractions (e.g., '5/9') as-is
    if re.match(r"^\d+\s*/\s*\d+$", s):
        return s
    # Remove thousands separators and '%' to parse numerics
    s_clean = s.replace(",", "").rstrip("%")
    try:
        return float(s_clean)
    except ValueError:
        return x  # leave original string


def dicts_to_df(ticker: str, main_scores: Dict[str, str], other_data: Dict[str, str]) -> pd.DataFrame:
    """
    Build a single-row DataFrame for a ticker.
      - 'score_*' columns keep string form (e.g., '8/10', '68/100')
      - 'metric_*' columns attempt numeric conversion
      - Adds 'ticker' and 'scraped_at'
    """
    row = {
        "ticker": ticker.upper(),
        "scraped_at": datetime.utcnow().isoformat(timespec="seconds"),
    }
    # Main (string) scores
    row.update({f"score_{k}": v for k, v in main_scores.items()})
    # Other indicators (numeric where possible)
    for k, v in other_data.items():
        row[f"metric_{k}"] = _to_number(v)
    return pd.DataFrame([row])


def append_csv(path: str, df: pd.DataFrame) -> None:
    """
    Append df to CSV (create if missing). Keeps all columns.
    """
    try:
        existing = pd.read_csv(path)
        out = pd.concat([existing, df], ignore_index=True)
    except FileNotFoundError:
        out = df
    out.to_csv(path, index=False)


def save_scores_json(path: str, ticker: str, main_scores: Dict[str, str]) -> None:
    """
    Save the 5 main rank scores to a JSON file with **bolded** keys
    (nice in Markdown-aware viewers).
    """
    data = {
        "**Financial Strength**": main_scores.get("financial_str"),
        "**Profitability Rank**": main_scores.get("profit"),
        "**Growth Rank**": main_scores.get("growth"),
        "**GF Value Rank**": main_scores.get("gf_value"),
        "**Momentum Rank**": main_scores.get("momentum"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump({ticker.upper(): data}, f, indent=2, ensure_ascii=False)


# =========================
# Orchestrator
# =========================

def get_financial_data_for_ticker(
    ticker: str,
    print_all_data: bool = False,
    headless: bool = True,
    use_playwright_fallback: bool = True,
    wait_selector: Optional[str] = None,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """
    Orchestrate fetching and parsing for a single ticker.
    Returns:
      (main_scores, all_other_data)
    """
    url = BASE_URL.format(ticker=ticker)

    # 1) Try fast path: requests
    html, status = fetch_html_requests(url)
    if not html or status != 200:
        # 2) Fallback: Playwright (rendered HTML)
        if use_playwright_fallback:
            try:
                html = fetch_html_playwright(url, headless=headless, wait_selector=wait_selector)
            except Exception as e:
                print(f"[{ticker}] Playwright failed: {e}")
                return {}, {}
        else:
            return {}, {}

    soup = _soup(html)

    # Extract the 5 main rank scores + GF Score
    main_scores = {
        "financial_str": extract_rank_score(soup, "Financial Strength", "rank-balancesheet"),
        "profit":        extract_rank_score(soup, "Profitability Rank", "rank-profitability"),
        "growth":        extract_rank_score(soup, "Growth Rank", "rank-growth"),
        "gf_value":      extract_rank_score(soup, "GF Value Rank", "rank-gf-value"),
        "momentum":      extract_rank_score(soup, "Momentum Rank", "rank-momentum"),
        "GF_score":      extract_gf_score(html),
    }

    # Parse the "other indicators" table
    all_data = extract_financial_data(soup)

    # Console log (optional)
    print(f"\n=== {ticker.upper()} | Main Scores ===")
    for k, v in main_scores.items():
        print(f"{k:15s}: {v}")

    if print_all_data:
        print("\n=== Other Financial Data ===")
        if all_data:
            for k, v in all_data.items():
                print(f"{k}: {v}")
        else:
            print("No additional financial data found.")

    return main_scores, all_data


# =========================
# CLI-style usage
# =========================

if __name__ == "__main__":
    # --- Set your tickers here ---
    tickers: List[str] = ["SMR", "NVDA"]

    # Where to append structured results
    out_csv = "/home/nim/Downloads/gurufocus_scrapes.csv"

    # Collect each 1-row DF so we can preview a combined view at the end
    all_rows: List[pd.DataFrame] = []

    for t in tickers:
        main_scores, other = get_financial_data_for_ticker(
            t,
            print_all_data=True,       # print parsed "other indicators" to console
            headless=True,             # set False to watch the browser if Playwright is used
            use_playwright_fallback=True,
            wait_selector=None,        # optionally wait for a stable element
        )

        # Turn results into a single-row DataFrame and append to CSV
        df_row = dicts_to_df(t, main_scores, other)
        append_csv(out_csv, df_row)
        all_rows.append(df_row)

        # Save a per-ticker JSON summarizing the 5 key ranks (bold keys)
        save_scores_json(f"{t}_scores.json", t, main_scores)

        print(f"\nSaved {t.upper()} data to {out_csv} and {t}_scores.json\n" + "-" * 50)

    # ---------- Console preview (cleaned) ----------
    if all_rows:
        combined_df = pd.concat(all_rows, ignore_index=True)

        # Create a preview copy:
        #   - drop 'scraped_at'
        #   - rename score_* columns by removing 'score_' prefix
        preview_df = combined_df.copy()
        preview_df = preview_df.drop(columns=["scraped_at"], errors="ignore")
        preview_df = preview_df.rename(
            columns={c: c.replace("score_", "") for c in preview_df.columns if c.startswith("score_")}
        )

        print("\nCombined DataFrame preview (first 7 columns, cleaned):")
        print(preview_df.iloc[:, :7].head())
