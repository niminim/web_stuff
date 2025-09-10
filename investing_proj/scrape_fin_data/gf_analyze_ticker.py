# -*- coding: utf-8 -*-
"""
GuruFocus summary scraper — high-efficiency version + single-ticker wrapper.

Features
--------
- Requests-first pipeline with Playwright fallback (reused browser).
- Extracts 5 ranks + GF Score + "other indicators" table.
- Prints all parsed data to console (not just counts).
- Saves per-ticker JSON (main ranks) and a combined CSV of all tickers.

Setup
-----
pip install playwright
python -m playwright install
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from random import uniform
from typing import Dict, Tuple, Optional, List
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

try:
    from bs4 import BeautifulSoup  # type: ignore
    HAVE_BS4 = True
except Exception:
    HAVE_BS4 = False


# =========================
# Configuration / Globals
# =========================

BASE_URL = "https://www.gurufocus.com/stock/{ticker}/summary"

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
    "Referer": "https://www.gurufocus.com/",
}

SESSION = requests.Session()
SESSION.headers.update(BASE_HEADERS)
SESSION.timeout = 20  # type: ignore[attr-defined]

BS_PARSER = "lxml" if HAVE_BS4 else "html.parser"
DEBUG_ARTIFACTS = False


# =========================
# Utilities
# =========================

def _sleep_jitter(low: float = 0.15, high: float = 0.45) -> None:
    time.sleep(uniform(low, high))


def _soup(html: str):
    if not HAVE_BS4:
        raise RuntimeError("BeautifulSoup (bs4) is required.")
    return BeautifulSoup(html, BS_PARSER)


# =========================
# Fetchers
# =========================

def fetch_html_requests(url: str, max_retries: int = 3, timeout: float = 20.0) -> Tuple[Optional[str], Optional[int]]:
    last_status: Optional[int] = None
    for attempt in range(1, max_retries + 1):
        try:
            _sleep_jitter()
            resp = SESSION.get(url, allow_redirects=True, timeout=timeout)
            last_status = resp.status_code
            if resp.status_code == 200 and resp.text:
                return resp.text, resp.status_code
            if resp.status_code in (403, 429, 503):
                time.sleep(0.6 * attempt)
            else:
                break
        except requests.RequestException:
            time.sleep(0.6 * attempt)
    return None, last_status


# =========================
# Parsers
# =========================

def extract_rank_score(soup, rank_name: str, identifier: str) -> str:
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


def extract_gf_score_from_html(html_text: str) -> Optional[int]:
    if not html_text:
        return None
    for pat in (
        r"gf_score\s*:\s*(\d+)",
        r'"gf_score"\s*:\s*(\d+)',
        r'"gfScore"\s*:\s*(\d+)',
        r"data-gf-score\s*=\s*\"?(\d+)\"?",
    ):
        m = re.search(pat, html_text, flags=re.I)
        if m:
            try:
                val = int(m.group(1))
                if 0 <= val <= 100:
                    return val
            except ValueError:
                pass
    return None


def extract_financial_data(soup) -> Dict[str, str]:
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
    if x is None:
        return None
    s = str(x).strip()
    if not s or s.lower() in {"no", "none", "n/a", "na", "no debt"}:
        return None
    if re.match(r"^\d+\s*/\s*\d+$", s):
        return s
    s_clean = s.replace(",", "").rstrip("%")
    try:
        return float(s_clean)
    except ValueError:
        return x


def dicts_to_df(ticker: str, main_scores: Dict[str, str], other_data: Dict[str, str]) -> pd.DataFrame:
    row = {
        "ticker": ticker.upper(),
        "scraped_at": datetime.utcnow().isoformat(timespec="seconds"),
    }
    row.update({f"score_{k}": v for k, v in main_scores.items()})
    for k, v in other_data.items():
        row[f"metric_{k}"] = _to_number(v)
    return pd.DataFrame([row])


def save_scores_json(path: str, ticker: str, main_scores: Dict[str, str]) -> None:
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
# Playwright client
# =========================

class PlaywrightClient:
    def __init__(self, headless: bool = True):
        from playwright.sync_api import sync_playwright
        self._p = sync_playwright().start()
        self._browser = self._p.chromium.launch(headless=headless)
        self._context = self._browser.new_context(
            user_agent=BASE_HEADERS["User-Agent"],
            viewport={"width": 1366, "height": 768},
            java_script_enabled=True,
        )

    def close(self):
        try:
            self._context.close()
        finally:
            try:
                self._browser.close()
            finally:
                self._p.stop()

    @staticmethod
    def _extract_from_blob(blob: str) -> Optional[int]:
        if not blob:
            return None
        m = re.search(r'"gf_score"\s*:\s*(\d+)', blob, flags=re.I)
        if not m:
            m = re.search(r'"gfScore"\s*:\s*(\d+)', blob, flags=re.I)
        if m:
            try:
                x = int(m.group(1))
                if 0 <= x <= 100:
                    return x
            except ValueError:
                pass
        return None

    def fetch(self, url: str, wait_selector: Optional[str] = None, wait_ms: int = 1200,
              debug_name: Optional[str] = None) -> Tuple[str, Optional[int]]:
        page = self._context.new_page()
        gf_holder = {"val": None}

        def on_response(resp):
            if gf_holder["val"] is not None:
                return
            try:
                ctype = (resp.headers.get("content-type") or "").lower()
                if "application/json" in ctype or "text/plain" in ctype or "application/javascript" in ctype:
                    body = resp.text()
                    if body and len(body) <= 2_000_000:
                        v = self._extract_from_blob(body)
                        if v is not None:
                            gf_holder["val"] = v
            except Exception:
                pass

        page.on("response", on_response)

        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(wait_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=4000)
            except Exception:
                pass

        # Inspect window app state
        if gf_holder["val"] is None:
            try:
                bag = page.evaluate("""
                () => {
                  function safe(v){try{return JSON.stringify(v)}catch(e){return null}}
                  const out = {};
                  try { if (window.__NUXT__) out.__NUXT__ = safe(window.__NUXT__); } catch(e){}
                  try { if (window.__NEXT_DATA__) out.__NEXT_DATA__ = safe(window.__NEXT_DATA__); } catch(e){}
                  try { if (window.__APOLLO_STATE__) out.__APOLLO_STATE__ = safe(window.__APOLLO_STATE__); } catch(e){}
                  try { if (window.__INITIAL_STATE__) out.__INITIAL_STATE__ = safe(window.__INITIAL_STATE__); } catch(e){}
                  try { if (window.__DATA__) out.__DATA__ = safe(window.__DATA__); } catch(e){}
                  try { if (window.__STATE__) out.__STATE__ = safe(window.__STATE__); } catch(e){}
                  return out;
                }
                """)
            except Exception:
                bag = {}
            blob = "".join(v for v in (bag or {}).values() if isinstance(v, str))
            if blob:
                v2 = self._extract_from_blob(blob)
                if v2 is not None:
                    gf_holder["val"] = v2

        html = page.content()
        if gf_holder["val"] is None:
            v3 = extract_gf_score_from_html(html)
            if v3 is not None:
                gf_holder["val"] = v3

        page.close()
        return html, gf_holder["val"]


# =========================
# Orchestrator
# =========================

@dataclass
class TickerResult:
    ticker: str
    main_scores: Dict[str, str]
    other_data: Dict[str, str]


def _parse_all_from_html(ticker: str, html: str, gf_hint: Optional[int] = None) -> TickerResult:
    s = _soup(html)
    main = {
        "financial_str": extract_rank_score(s, "Financial Strength", "rank-balancesheet"),
        "profit":        extract_rank_score(s, "Profitability Rank", "rank-profitability"),
        "growth":        extract_rank_score(s, "Growth Rank", "rank-growth"),
        "momentum": extract_rank_score(s, "Momentum Rank", "rank-momentum"),
        "gf_value":      extract_rank_score(s, "GF Value Rank", "rank-gf-value"),
        "GF_score":      "GF Score not found.",
    }
    if gf_hint is not None:
        main["GF_score"] = f"{gf_hint}/100"
    else:
        gf_try = extract_gf_score_from_html(html)
        if gf_try is not None:
            main["GF_score"] = f"{gf_try}/100"
    other = extract_financial_data(s)
    return TickerResult(ticker=ticker, main_scores=main, other_data=other)


def _core_missing(main_scores: Dict[str, str]) -> bool:
    if not main_scores:
        return True
    not_found = {
        "GF Score not found.",
        "Financial Strength score not found.",
        "Profitability Rank score not found.",
        "Growth Rank score not found.",
        "Momentum Rank score not found.",
        "GF Value Rank score not found.",
    }
    gf_missing = (main_scores.get("GF_score") in not_found)
    ranks = [main_scores.get(k, "") for k in ("financial_str", "profit", "growth", "momentum", "gf_value")]
    ranks_missing = not any(re.match(r"^\d+\s*/\s*10$", r or "") for r in ranks)
    return gf_missing or ranks_missing


def scrape_tickers(tickers: List[str],
                   max_workers: int = 8,
                   headless: bool = True,
                   wait_selector: Optional[str] = None) -> List[TickerResult]:
    tickers = [t.upper() for t in tickers]
    results: Dict[str, TickerResult] = {}
    need_fallback: List[str] = []

    def _worker(tk: str) -> Tuple[str, Optional[TickerResult]]:
        url = BASE_URL.format(ticker=tk)
        html, status = fetch_html_requests(url)
        if not html or status != 200:
            return tk, None
        try:
            res = _parse_all_from_html(tk, html)
            return tk, res
        except Exception:
            return tk, None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_worker, t): t for t in tickers}
        for fut in as_completed(futures):
            tkr = futures[fut]
            res = fut.result()[1]
            if res is None or _core_missing(res.main_scores):
                need_fallback.append(tkr)
            else:
                results[tkr] = res

    if need_fallback:
        pw = PlaywrightClient(headless=headless)
        try:
            for tk in need_fallback:
                url = BASE_URL.format(ticker=tk)
                html2, gf_val = pw.fetch(url, wait_selector=wait_selector, wait_ms=1200)
                res2 = _parse_all_from_html(tk, html2, gf_hint=gf_val)
                results[tk] = res2
        finally:
            pw.close()

    return [results[t] for t in tickers]


# =========================
# Wrapper for compatibility
# =========================

def get_financial_data_for_ticker(
    ticker: str,
    headless: bool = True,
    print_all_data: bool = True
) -> tuple[dict, dict]:
    """
    Scrape a single ticker and (optionally) print results.

    Args:
        ticker: Stock ticker (str).
        headless: Playwright headless mode.
        print_all_data: If True, print main_scores and other_data;
                        If False, print only main_scores.

    Returns:
        (main_scores, other_data)
    """
    results = scrape_tickers([ticker], max_workers=1, headless=headless)
    r = results[0]

    # --- Printing is centralized here only ---
    if print_all_data:
        print(f"\n=== {r.ticker} | Main Scores ===")
        for k, v in r.main_scores.items():
            print(f"{k:15s}: {v}")

        if r.other_data:
            print("\n--- Other Indicators ---")
            for k, v in r.other_data.items():
                print(f"{k:40s}: {v}")
        else:
            print("\n(No additional indicators found)")
    else:
        print(f"\n=== {r.ticker} | Main Scores ===")
        for k, v in r.main_scores.items():
            print(f"{k:15s}: {v}")
    # ----------------------------------------

    return r.main_scores, r.other_data


# =========================
# CLI
# =========================

if __name__ == "__main__":
    tickers: List[str] = ["AAPL", "NVDA",  "GOOGL",  "MSFT",  "META", "AMZN"]
    # tickers: List[str] = ["AAPL", "NVDA"]
    tickers: List[str] = ["QS", "RKLB", "CLOV", "SMR"]
    tickers: List[str] = ["CRSR"]

    out_csv = "/home/nim/Downloads/gurufocus_scrapes.csv"
    jsons_dir = "/home/nim/Downloads/GF_companies"
    os.makedirs(jsons_dir, exist_ok=True)

    # Scrape tickers (no per-ticker prints here)
    out: List[TickerResult] = scrape_tickers(tickers, max_workers=8, headless=True)

    # Build DataFrame and save artifacts quietly
    all_rows: List[pd.DataFrame] = []
    for item in out:
        # no printing here
        df_row = dicts_to_df(item.ticker, item.main_scores, item.other_data)
        all_rows.append(df_row)
        save_scores_json(f"{jsons_dir}/{item.ticker}_scores.json", item.ticker, item.main_scores)

    # ✅ Keep the combined preview print
    if all_rows:
        combined = pd.concat(all_rows, ignore_index=True)
        # Save CSV (keeps your gurufocus_scrapes file updated)
        combined.to_csv(out_csv, index=False)

        preview_df = combined.copy()
        preview_df = preview_df.drop(columns=["scraped_at"], errors="ignore")
        preview_df = preview_df.rename(
            columns={c: c.replace("score_", "") for c in preview_df.columns if c.startswith("score_")}
        )
        print("\nCombined DataFrame preview (first 7 columns, cleaned):")
        print(preview_df.iloc[:, :7].head())

    print(f"\nSaved data for {len(all_rows)} tickers to {out_csv}")

# no specific need for using this wrapper, could use scrape_tickers only.
# this one is just more straight-forward
ticker = 'CRSR'
main_scores, other_indicators = get_financial_data_for_ticker(ticker, print_all_data=True)