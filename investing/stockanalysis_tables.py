"""
StockAnalysis scraper — Overview + Financials + TTM dict + Technicals + Short Info
----------------------------------------------------------------------------------

Public API:
    bundle = get_company_bundle("NVDA")

    bundle.keys() ->
        dict_keys(['overview', 'financials_dfs', 'financials_ttm_dict', 'technicals', 'short_info'])

Contents:
    - overview: dict (exact 18 snapshot fields)
    - financials_dfs: dict[str, DataFrame] -> income / balance_sheet / cash_flow / ratios
    - financials_ttm_dict: dict[str, dict[str, str|None]] -> latest (TTM/Current) per row label
    - technicals: dict (price stats block from /statistics/)
    - short_info: dict (short selling block from /statistics/)

Run:
    python this_file.py
"""

from __future__ import annotations
import json
import re
from typing import Dict, Optional
import pandas as pd
import requests
from requests.adapters import HTTPAdapter, Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup, Tag, NavigableString


# =============================================================================
# Networking (fast & robust)
# =============================================================================
def make_session() -> requests.Session:
    """
    Create a persistent HTTP session with:
      - Browser UA
      - Connection pooling
      - Automatic retries with backoff for 429/5xx
    """
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/115.0 Safari/537.36"
        )
    })
    retry = Retry(
        total=3,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def fetch_html(url: str, session: requests.Session, timeout: int = 12) -> str:
    """GET a page and return its HTML."""
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text


# =============================================================================
# Text helpers + label→value DOM finder (class-agnostic)
# =============================================================================
def _normalize_text(s: str) -> str:
    s = re.sub(r"[^\w\s%-./,$]", "", s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s


def _canon_map(keys: list[str]) -> dict[str, str]:
    """Map 'normalized' label -> canonical label."""
    return {re.sub(r"[^\w\s]", "", k).lower(): k for k in keys}


def _find_value_next_to_label(root: Tag, label_text: str) -> Optional[str]:
    """
    Find the text value visually 'next to' a label anywhere in the DOM.
    Works without relying on CSS classes (robust to layout changes).
    """
    target_norm = _normalize_text(label_text).lower()

    def match_label(node: Tag) -> bool:
        if not isinstance(node, Tag):
            return False
        txt = _normalize_text(node.get_text(" ", strip=True)).lower()
        return txt == target_norm

    candidates = root.find_all(match_label)
    for lab in candidates:
        parent = lab.parent
        if not isinstance(parent, Tag):
            continue

        # 1) Immediate next siblings
        for sib in lab.next_siblings:
            if isinstance(sib, NavigableString):
                continue
            if isinstance(sib, Tag):
                val = _normalize_text(sib.get_text(" ", strip=True))
                if val and val.lower() != target_norm:
                    return val

        # 2) Adjacent child in same parent
        kids = [c for c in parent.children if isinstance(c, Tag)]
        for i, c in enumerate(kids):
            if c is lab and i + 1 < len(kids):
                val = _normalize_text(kids[i + 1].get_text(" ", strip=True))
                if val and val.lower() != target_norm:
                    return val

        # 3) One level up (two-column rows)
        gp = parent.parent
        if isinstance(gp, Tag):
            elems = [e for e in gp.children if isinstance(e, Tag)]
            for i, e in enumerate(elems):
                if lab in e.descendants and i + 1 < len(elems):
                    val = _normalize_text(elems[i + 1].get_text(" ", strip=True))
                    if val and val.lower() != target_norm:
                        return val
    return None


# =============================================================================
# Financial table parsing (hardened)
# =============================================================================
def parse_table_to_df(html: str) -> Optional[pd.DataFrame]:
    """
    Parse StockAnalysis tables with fixed column counts:
      - Build headers from the first header row (<thead><tr> or first <tr>)
      - Clip at 'Period Ending' (drop that and everything to the right)
      - Read both <th> and <td> (row labels often live in <th>)
      - Slice/pad rows to header length to avoid column mismatches
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return None

    thead = table.find("thead")
    hrow = thead.find("tr") if thead else table.find("tr")
    hdr_cells = hrow.find_all(["th", "td"]) if hrow else []
    headers = [c.get_text(strip=True) for c in hdr_cells]

    if "Period Ending" in headers:
        headers = headers[: headers.index("Period Ending")]

    n_cols = len(headers)
    if n_cols == 0:
        return None

    rows = []
    for tr in table.find_all("tr"):
        if tr.find_parent("thead"):
            continue
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if not cells:
            continue
        if len(cells) >= n_cols:
            cells = cells[:n_cols]
        else:
            cells += [""] * (n_cols - len(cells))
        if cells == headers:
            continue
        rows.append(cells)

    if not rows:
        return None

    df = pd.DataFrame(rows, columns=headers)
    if "Company Name" in df.columns:
        df.rename(columns={"Company Name": "Company"}, inplace=True)
    return df


def get_data_table(url: str, session: requests.Session) -> Optional[pd.DataFrame]:
    html = fetch_html(url, session)
    return parse_table_to_df(html)


# =============================================================================
# Financials: fetch 4 tables in parallel
# =============================================================================
FIN_URLS = {
    "income":        "https://stockanalysis.com/stocks/{t}/financials/",
    "balance_sheet": "https://stockanalysis.com/stocks/{t}/financials/balance-sheet/",
    "cash_flow":     "https://stockanalysis.com/stocks/{t}/financials/cash-flow-statement/",
    "ratios":        "https://stockanalysis.com/stocks/{t}/financials/ratios/",
}

def fetch_financial_dfs(ticker: str, session: requests.Session) -> Dict[str, Optional[pd.DataFrame]]:
    t = ticker.lower().strip()
    out: Dict[str, Optional[pd.DataFrame]] = {k: None for k in FIN_URLS}
    with ThreadPoolExecutor(max_workers=4) as ex:
        fut2key = {
            ex.submit(get_data_table, url.format(t=t), session): k
            for k, url in FIN_URLS.items()
        }
        for fut in as_completed(fut2key):
            k = fut2key[fut]
            try:
                out[k] = fut.result()
            except Exception as e:
                print(f"[warn] {ticker} {k}: {e}")
                out[k] = None
    return out


def build_financials_ttm_dict(financials_dfs: Dict[str, Optional[pd.DataFrame]]) -> Dict[str, Dict[str, Optional[str]]]:
    """
    Convert the financial DataFrames into a compact 'latest values' dictionary:
      - ratios  -> uses 'Current'
      - others  -> use 'TTM'
      - if missing, fall back to the last non-label column
    Structure:
        {'income': {'Revenue': '...', ...}, 'ratios': {'PE Ratio': '...', ...}, ...}
    """
    def label_col(df: pd.DataFrame) -> str:
        for c in ("Fiscal Year", "Year Ending", "Period Ending", "Metric", "Breakdown", "Category", "Item"):
            if c in df.columns:
                return c
        return df.columns[0]

    latest: Dict[str, Dict[str, Optional[str]]] = {}
    for key, df in financials_dfs.items():
        if df is None or df.empty:
            latest[key] = {}
            continue

        lbl = label_col(df)
        val_col = "Current" if key == "ratios" else "TTM"
        if val_col not in df.columns:
            nonlbl = [c for c in df.columns if c != lbl]
            val_col = nonlbl[-1] if nonlbl else df.columns[-1]

        d: Dict[str, Optional[str]] = {}
        for category in df[lbl].dropna().astype(str).tolist():
            row = df[df[lbl] == category]
            d[category] = row.iloc[0][val_col] if (not row.empty and val_col in row.columns) else None
        latest[key] = d
    return latest


# =============================================================================
# Overview snapshot (18 canonical fields) — robust JSON + DOM fallback
# =============================================================================
CANONICAL_OVERVIEW_FIELDS = [
    "Market Cap", "Revenue (ttm)", "Net Income (ttm)", "Shares Out",
    "EPS (ttm)", "PE Ratio", "Forward PE", "Dividend", "Ex-Dividend Date",
    "Volume", "Open", "Previous Close", "Day's Range", "52-Week Range",
    "Beta", "Analysts", "Price Target", "Earnings Date",
]

def _find_first_key(obj, candidates):
    """DFS in nested dict/list: first value found for any key in 'candidates'."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in candidates:
                return v
        for v in obj.values():
            hit = _find_first_key(v, candidates)
            if hit is not None:
                return hit
    elif isinstance(obj, list):
        for it in obj:
            hit = _find_first_key(it, candidates)
            if hit is not None:
                return hit
    return None


def get_company_overview(ticker: str, session: requests.Session) -> Optional[Dict[str, str]]:
    """
    Extract the 18 overview fields from the main stock page:
      1) Try Next.js __NEXT_DATA__ JSON (structure-agnostic key search)
      2) Fill any misses via class-agnostic DOM pairing of label->value
    """
    url = f"https://stockanalysis.com/stocks/{ticker.lower().strip()}/"
    html = fetch_html(url, session)
    soup = BeautifulSoup(html, "html.parser")
    out: Dict[str, str] = {}

    # (1) JSON boot data
    try:
        tag = soup.find("script", id="__NEXT_DATA__")
        if tag and tag.string:
            boot = json.loads(tag.string)
            keymap = {
                "Market Cap":        {"marketCap", "marketcap", "market_cap"},
                "Revenue (ttm)":     {"revenueTtm", "revenueTTM", "revenueTrailing12M"},
                "Net Income (ttm)":  {"netIncomeTtm", "netIncomeTTM", "netIncomeTrailing12M"},
                "Shares Out":        {"sharesOutstanding", "sharesOut"},
                "EPS (ttm)":         {"epsTtm", "epsTrailingTwelveMonths"},
                "PE Ratio":          {"peRatio", "pe"},
                "Forward PE":        {"forwardPE", "forwardPe"},
                "Dividend":          {"dividendYield", "dividendRate", "dividend"},
                "Ex-Dividend Date":  {"exDividendDate"},
                "Volume":            {"volume"},
                "Open":              {"open"},
                "Previous Close":    {"previousClose", "prevClose"},
                "Day's Range":       {"daysRange", "dayRange"},
                "52-Week Range":     {"fiftyTwoWeekRange", "52WeekRange"},
                "Beta":              {"beta"},
                "Analysts":          {"analystRating", "analystRecommendation"},
                "Price Target":      {"priceTarget", "targetMeanPrice"},
                "Earnings Date":     {"earningsDate", "nextEarningsDate"},
            }
            for label, cands in keymap.items():
                val = _find_first_key(boot, cands)
                # Range handling
                if isinstance(val, dict) and {"low", "high"} & set(val.keys()):
                    val = f"{val.get('low')} - {val.get('high')}"
                elif label in {"Day's Range", "52-Week Range"} and isinstance(val, (list, tuple)) and len(val) >= 2:
                    val = f"{val[0]} - {val[1]}"
                if val is not None:
                    out[label] = _normalize_text(str(val))
    except Exception:
        pass

    # (2) DOM pairing for any missing labels
    missing = [lab for lab in CANONICAL_OVERVIEW_FIELDS if lab not in out]
    if missing:
        for lab in missing:
            val = _find_value_next_to_label(soup, lab)
            if val:
                out[lab] = val

    if not out:
        return None
    # Return only the canonical fields, in order
    return {k: out[k] for k in CANONICAL_OVERVIEW_FIELDS if k in out}


# =============================================================================
# /statistics/: Technicals + Short info
# =============================================================================
TECHNICALS_FIELDS = [
    "Beta (5Y)",
    "52-Week Price Change",
    "50-Day Moving Average",
    "200-Day Moving Average",
    "Relative Strength Index (RSI)",
    "Average Volume (20 Days)",
]

SHORT_INFO_FIELDS = [
    "Short Interest",
    "Short Previous Month",
    "Short % of Shares Out",
    "Short % of Float",
    "Short Ratio (days to cover)",
]

def get_statistics_blocks(ticker: str, session: requests.Session) -> dict[str, dict] | None:
    """
    Scrape the two cards from /statistics/:
      - 'Stock Price Statistics'  -> returned as 'technicals'
      - 'Short Selling Information' -> returned as 'short_info'
    Missing fields are omitted.
    """
    url = f"https://stockanalysis.com/stocks/{ticker.lower().strip()}/statistics/"
    html = fetch_html(url, session)
    soup = BeautifulSoup(html, "html.parser")

    technicals: dict[str, str] = {}
    for lab in TECHNICALS_FIELDS:
        val = _find_value_next_to_label(soup, lab)
        if val:
            technicals[lab] = val

    short_info: dict[str, str] = {}
    for lab in SHORT_INFO_FIELDS:
        val = _find_value_next_to_label(soup, lab)
        if val:
            short_info[lab] = val

    if not technicals and not short_info:
        return None
    return {"technicals": technicals, "short_info": short_info}


# =============================================================================
# Public API: one call that fetches everything in parallel
# =============================================================================
def get_company_bundle(ticker: str) -> Dict[str, object]:
    """
    Fetch overview + financials + TTM dict + technicals + short info.

    Returns dict with keys:
        ['overview', 'financials_dfs', 'financials_ttm_dict', 'technicals', 'short_info']
    """
    session = make_session()
    with ThreadPoolExecutor(max_workers=3) as ex:
        fut_overview   = ex.submit(get_company_overview, ticker, session)
        fut_financials = ex.submit(fetch_financial_dfs, ticker, session)
        fut_stats      = ex.submit(get_statistics_blocks, ticker, session)

        overview     = fut_overview.result()
        fin_dfs      = fut_financials.result()
        stats_blocks = fut_stats.result() or {"technicals": {}, "short_info": {}}

    fin_ttm = build_financials_ttm_dict(fin_dfs)

    return {
        "overview": overview,
        "financials_dfs": fin_dfs,
        "financials_ttm_dict": fin_ttm,
        "technicals": stats_blocks.get("technicals", {}),
        "short_info": stats_blocks.get("short_info", {}),
    }


# =============================================================================
# Example run
# =============================================================================
if __name__ == "__main__":
    ticker = "NVDA"
    data = get_company_bundle(ticker)

    print("\n=== BUNDLE KEYS ===")
    print(data.keys())  # dict_keys(['overview','financials_dfs','financials_ttm_dict','technicals','short_info'])

    print("\n=== OVERVIEW (subset) ===")
    if data["overview"]:
        for k in ["Market Cap", "PE Ratio", "Price Target", "Earnings Date"]:
            print(k, ":", data["overview"].get(k))
    else:
        print("No overview.")

    print("\n=== TECHNICALS ===")
    for k, v in data["technicals"].items():
        print(f"{k}: {v}")

    print("\n=== SHORT INFO ===")
    for k, v in data["short_info"].items():
        print(f"{k}: {v}")

    print("\n=== FINANCIALS_DFS shapes ===")
    for k, df in data["financials_dfs"].items():
        print(f"{k}: {None if df is None else df.shape}")

    print("\n=== FINANCIALS_TTM_DICT (sample) ===")
    print("Ratios -> PE Ratio:", data["financials_ttm_dict"].get("ratios", {}).get("PE Ratio"))
