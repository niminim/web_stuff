"""
StockAnalysis scraper — overview (robust) + financial tables

Run:
    data = get_company_data("NVDA")
    print(pd.Series(data["overview"]))
"""

from __future__ import annotations
import json, re
from typing import Dict, Optional
import pandas as pd
import requests
from requests.adapters import HTTPAdapter, Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup, NavigableString, Tag

# =============================================================================
# Networking
# =============================================================================
def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/115.0 Safari/537.36"
        )
    })
    retry = Retry(total=3, backoff_factor=0.4,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET"]))
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s

def fetch_html(url: str, session: requests.Session, timeout: int = 12) -> str:
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text

# =============================================================================
# Table parser
# =============================================================================
def parse_table_to_df(html: str) -> Optional[pd.DataFrame]:
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
# Financials
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

def dfs_to_latest(financials_dfs: Dict[str, Optional[pd.DataFrame]]) -> Dict[str, Dict[str, Optional[str]]]:
    def label_col(df: pd.DataFrame) -> str:
        for c in ("Fiscal Year", "Year Ending", "Period Ending",
                  "Metric", "Breakdown", "Category", "Item"):
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
# Overview – canonical fields & helpers
# =============================================================================
CANONICAL_FIELDS = [
    "Market Cap", "Revenue (ttm)", "Net Income (ttm)", "Shares Out",
    "EPS (ttm)", "PE Ratio", "Forward PE", "Dividend", "Ex-Dividend Date",
    "Volume", "Open", "Previous Close", "Day's Range", "52-Week Range",
    "Beta", "Analysts", "Price Target", "Earnings Date"
]

def _normalize_text(s: str) -> str:
    s = re.sub(r"[^\w\s%-./]", "", s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def _canonize_label(label: str) -> str | None:
    lab = _normalize_text(label).lower()
    canon_map = {re.sub(r"[^\w\s]", "", k).lower(): k for k in CANONICAL_FIELDS}
    return canon_map.get(re.sub(r"[^\w\s]", "", lab))

def _format_range(lo, hi):
    if lo is None or hi is None:
        return None
    return f"{lo} - {hi}"

def _find_first_key(obj, candidates):
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

# ---- DOM value finder (class-agnostic) ----
def _find_value_next_to_label(root: Tag, label_text: str) -> Optional[str]:
    """
    Find the text value visually 'next to' a label anywhere in the DOM:
    - search any element whose normalized text matches label_text
    - within the same parent/row, pick the nearest sibling text that looks like the value
    This tolerates changing class names and grid systems.
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

        # 1) Try immediate next siblings in the same row/container
        for sib in lab.next_siblings:
            if isinstance(sib, NavigableString):
                continue
            if isinstance(sib, Tag):
                val = _normalize_text(sib.get_text(" ", strip=True))
                if val and val.lower() != target_norm:
                    return val

        # 2) Try other children in the same parent (label/value pairs)
        children = [c for c in parent.children if isinstance(c, Tag)]
        if len(children) >= 2:
            for i, c in enumerate(children):
                if c is lab and i + 1 < len(children):
                    val = _normalize_text(children[i + 1].get_text(" ", strip=True))
                    if val and val.lower() != target_norm:
                        return val

        # 3) Go one level up and scan siblings (two-column rows)
        gp = parent.parent
        if isinstance(gp, Tag):
            elems = [e for e in gp.children if isinstance(e, Tag)]
            if len(elems) >= 2:
                for i, e in enumerate(elems):
                    if lab in e.descendants and i + 1 < len(elems):
                        val = _normalize_text(elems[i + 1].get_text(" ", strip=True))
                        if val and val.lower() != target_norm:
                            return val
    return None

# =============================================================================
# Overview scraper (hardened)
# =============================================================================
def get_company_overview(ticker: str, session: requests.Session) -> Optional[Dict[str, str]]:
    """
    Extract the 18 canonical overview fields using multiple strategies:
      A) __NEXT_DATA__ JSON keys (if present)
      B) any <script type="application/ld+json"> blocks
      C) class-agnostic DOM pairing of label -> nearest value
    Returns a dict with only the 18 fields (any missing are omitted).
    """
    url = f"https://stockanalysis.com/stocks/{ticker.lower().strip()}/"
    html = fetch_html(url, session)
    soup = BeautifulSoup(html, "html.parser")
    out: Dict[str, str] = {}

    # --- A) Next.js boot JSON ---
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

            for label, candidates in keymap.items():
                val = _find_first_key(boot, candidates)
                if isinstance(val, dict) and {"low", "high"} & set(val.keys()):
                    val = _format_range(val.get("low"), val.get("high"))
                elif label in {"Day's Range", "52-Week Range"} and isinstance(val, (list, tuple)) and len(val) >= 2:
                    val = _format_range(val[0], val[1])
                if val is not None:
                    out[label] = _normalize_text(str(val))
    except Exception:
        pass

    # --- B) ld+json fallbacks (sometimes sites mirror snapshot there) ---
    if len(out) < len(CANONICAL_FIELDS):
        for s in soup.find_all("script", attrs={"type": "application/ld+json"}):
            try:
                blob = json.loads(s.string or "")
            except Exception:
                continue
            if not isinstance(blob, (dict, list)):
                continue

            def get_from_blob(label: str, candidates: set[str]):
                val = _find_first_key(blob, candidates)
                if isinstance(val, dict) and {"low", "high"} & set(val.keys()):
                    val = _format_range(val.get("low"), val.get("high"))
                elif label in {"Day's Range", "52-Week Range"} and isinstance(val, (list, tuple)) and len(val) >= 2:
                    val = _format_range(val[0], val[1])
                return val

            # try a couple of useful fields
            mapping = {
                "Market Cap": {"marketCap"},
                "Beta": {"beta"},
            }
            for lab, cands in mapping.items():
                if lab not in out:
                    v = get_from_blob(lab, cands)
                    if v is not None:
                        out[lab] = _normalize_text(str(v))

    # --- C) DOM pairing by label text (class-agnostic) ---
    if len(out) < len(CANONICAL_FIELDS):
        for lab in CANONICAL_FIELDS:
            if lab in out:
                continue
            val = _find_value_next_to_label(soup, lab)
            if val:
                out[lab] = val

    # return only the canonical fields, in order
    if not out:
        return None
    return {k: out[k] for k in CANONICAL_FIELDS if k in out}

# =============================================================================
# Public API
# =============================================================================
def get_company_data(ticker: str) -> Dict[str, object]:
    session = make_session()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_overview = ex.submit(get_company_overview, ticker, session)
        f_fin = ex.submit(fetch_financial_dfs, ticker, session)
        overview = f_overview.result()
        financials_dfs = f_fin.result()
    financials_dict = dfs_to_latest(financials_dfs)
    return {
        "overview": overview,
        "financials_dfs": financials_dfs,
        "financials_dict": financials_dict,
    }

# =============================================================================
# Example
# =============================================================================
if __name__ == "__main__":
    ticker = "NVDA"
    data = get_company_data(ticker)

    print("\n=== OVERVIEW (filtered) ===")
    print(pd.Series(data["overview"]) if data["overview"] else "No overview found.")

    print("\n=== LATEST RATIOS ===")
    for key in ["PE Ratio", "Forward PE", "Debt / Equity Ratio", "EPS (ttm)"]:
        print(f"{key}: {data['financials_dict']['ratios'].get(key)}")

    print("\n=== DATAFRAME SHAPES ===")
    for k, df in data["financials_dfs"].items():
        print(f"{k}: {None if df is None else df.shape}")
