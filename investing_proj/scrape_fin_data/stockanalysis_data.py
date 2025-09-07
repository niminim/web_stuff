"""
StockAnalysis scraper — Overview + Financials + TTM dict + Technicals + Short Info + Historical Data
----------------------------------------------------------------------------------------------------

Public API:
    bundle = get_company_bundle("NVDA")

    bundle.keys() ->
        dict_keys([
            'overview',
            'financials_dfs',
            'financials_ttm_dict',
            'technicals',
            'short_info',
            'historical_data',   # <-- NEW last key
        ])

Contents:
    - overview: dict (18 snapshot fields)
    - financials_dfs: dict[str, DataFrame] -> income / balance_sheet / cash_flow / ratios
    - financials_ttm_dict: dict[str, dict[str, str|None]] -> latest (TTM/Current) per row label
    - technicals: dict (price stats from /statistics/)
    - short_info: dict (short selling from /statistics/)
    - historical_data: DataFrame (Date, Open, High, Low, Close, Volume, Change)

Run:
    python this_file.py
"""

from __future__ import annotations
import json
import re
from typing import Dict, Optional, Tuple, List
import pandas as pd
import requests
from requests.adapters import HTTPAdapter, Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup, Tag, NavigableString
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

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
# Historical Data — robust table + pagination
# =============================================================================
HIST_HEADERS_MUST_HAVE = {"Date", "Open", "High", "Low", "Close", "Volume"}  # Change is optional but expected

def _find_history_table(soup: BeautifulSoup) -> Optional[Tag]:
    """Locate the historical data table by checking for expected headers."""
    for tbl in soup.find_all("table"):
        ths = [th.get_text(strip=True) for th in tbl.find_all("th")]
        if not ths:
            continue
        if HIST_HEADERS_MUST_HAVE.issubset(set(ths)):
            return tbl
    return None

def _parse_history_table(tbl: Tag) -> pd.DataFrame:
    """Parse a historical data table (<table> Tag) into a DataFrame with header/rows."""
    headers = [th.get_text(strip=True) for th in tbl.find_all("th")]
    rows: List[List[str]] = []
    for tr in tbl.find_all("tr")[1:]:
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if cells:
            # normalize row length to header count
            if len(cells) < len(headers):
                cells += [""] * (len(headers) - len(cells))
            elif len(cells) > len(headers):
                cells = cells[:len(headers)]
            rows.append(cells)
    df = pd.DataFrame(rows, columns=headers)
    return df

def _clean_history_df(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize dtypes for historical data."""
    out = df.copy()

    # Keep only known columns (if site adds extras, we ignore them)
    keep_cols = [c for c in out.columns if c in {"Date","Open","High","Low","Close","Volume","Change"}]
    out = out[keep_cols]

    # Date → datetime
    if "Date" in out.columns:
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")

    # Prices → float
    for c in ["Open","High","Low","Close"]:
        if c in out.columns:
            out[c] = (
                out[c].str.replace("$","", regex=False)
                      .str.replace(",","", regex=False)
                      .replace({"": None, "—": None, "-": None})
            )
            out[c] = pd.to_numeric(out[c], errors="coerce")

    # Volume → Int64
    if "Volume" in out.columns:
        out["Volume"] = (
            out["Volume"].str.replace(",","", regex=False)
                         .replace({"": None, "—": None, "-": None})
        )
        out["Volume"] = pd.to_numeric(out["Volume"], errors="coerce").astype("Int64")

    # Change (percentage) → fraction float
    if "Change" in out.columns:
        s = out["Change"].astype(str)
        s = s.str.replace("%","", regex=False).str.replace(",","", regex=False)
        s = s.replace({"": None, "—": None, "-": None})
        out["Change"] = pd.to_numeric(s, errors="coerce")/100.0

    return out

def _find_next_page_url(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    """
    Detect a 'Next' page link for history pagination. We look for:
      - rel="next"
      - an <a> with '?p=N+1' relative to current page
    """
    # rel="next"
    a = soup.find("a", attrs={"rel": "next"})
    if a and a.get("href"):
        return urljoin(base_url, a["href"])

    # heuristic: look for links with '?p=' and pick the next number
    candidates = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "?p=" in href:
            candidates.append(urljoin(base_url, href))
    if not candidates:
        return None
    # Choose the highest page number link greater than current
    # Parse current p
    parsed = urlparse(base_url)
    q = parse_qs(parsed.query)
    cur_p = int(q.get("p", [1])[0])
    nexts = []
    for u in candidates:
        pu = urlparse(u)
        pq = parse_qs(pu.query)
        pnum = int(pq.get("p", [0])[0]) if "p" in pq else 0
        if pnum > cur_p:
            nexts.append((pnum, u))
    if not nexts:
        return None
    nexts.sort()
    return nexts[0][1]

def _ensure_page(url: str, page: int) -> str:
    """Ensure URL has ?p=page; if already has query params, replace p; else add it."""
    pu = urlparse(url)
    q = parse_qs(pu.query)
    q["p"] = [str(page)]
    new_q = urlencode({k: v[0] for k, v in q.items()})
    return urlunparse((pu.scheme, pu.netloc, pu.path, pu.params, new_q, pu.fragment))

def get_historical_data(
    ticker: str,
    session: requests.Session,
    max_pages: int = 1
) -> Optional[pd.DataFrame]:
    """
    Scrape historical OHLCV table with pagination.

    Strategy:
      - Try /history/ first, then /historical/ fallback
      - For each page, locate the history table by header check (class-agnostic)
      - Append rows across pages up to max_pages or until no next page
      - Clean dtypes & return a single DataFrame sorted DESC by Date
    """
    base_candidates = [
        f"https://stockanalysis.com/stocks/{ticker.lower().strip()}/history/",
        f"https://stockanalysis.com/stocks/{ticker.lower().strip()}/historical/",
    ]

    all_pages: List[pd.DataFrame] = []

    for base in base_candidates:
        try:
            # page 1
            html = fetch_html(base, session)
            soup = BeautifulSoup(html, "html.parser")
            tbl = _find_history_table(soup)
            if tbl is None:
                # Try next candidate base
                continue

            # first page
            df1 = _parse_history_table(tbl)
            if not df1.empty:
                all_pages.append(df1)

            # follow ?p=2..N up to max_pages
            page = 1
            while page < max_pages:
                # Prefer explicit p=page+1 if possible
                page += 1
                next_url = _ensure_page(base, page)

                try:
                    html_n = fetch_html(next_url, session)
                except Exception:
                    # If explicit p=N fails, try discoverable "next" link
                    next_url = _find_next_page_url(soup, base)
                    if not next_url:
                        break
                    html_n = fetch_html(next_url, session)

                soup = BeautifulSoup(html_n, "html.parser")
                tbl = _find_history_table(soup)
                if tbl is None:
                    break
                dfn = _parse_history_table(tbl)
                if dfn is None or dfn.empty:
                    break
                all_pages.append(dfn)

            # If we reached here with at least one page, stop trying fallbacks
            if all_pages:
                break

        except Exception:
            # Try the next candidate base URL
            continue

    if not all_pages:
        return None

    raw = pd.concat(all_pages, ignore_index=True)
    cleaned = _clean_history_df(raw)

    # Drop rows without a valid Date; keep unique rows; sort by Date DESC (site style)
    if "Date" in cleaned.columns:
        cleaned = cleaned.dropna(subset=["Date"]).drop_duplicates().sort_values("Date", ascending=False).reset_index(drop=True)

    return cleaned

# =============================================================================
# Public API: one call that fetches everything in parallel (+ historical last)
# =============================================================================
def get_company_data(
    ticker: str,
    *,
    history_pages: int = 1  # increase to pull more pages of historical data
) -> Dict[str, object]:
    """
    Fetch overview + financials + TTM dict + technicals + short info + historical data.

    Returns dict with keys (in this order):
        ['overview', 'financials_dfs', 'financials_ttm_dict', 'technicals', 'short_info', 'historical_data']
    """
    session = make_session()
    with ThreadPoolExecutor(max_workers=4) as ex:
        fut_overview   = ex.submit(get_company_overview, ticker, session)
        fut_financials = ex.submit(fetch_financial_dfs, ticker, session)
        fut_stats      = ex.submit(get_statistics_blocks, ticker, session)
        fut_history    = ex.submit(get_historical_data, ticker, session, history_pages)

        overview     = fut_overview.result()
        fin_dfs      = fut_financials.result()
        stats_blocks = fut_stats.result() or {"technicals": {}, "short_info": {}}
        history_df   = fut_history.result()

    fin_ttm = build_financials_ttm_dict(fin_dfs)

    # Important: ensure 'historical_data' is the LAST key when printed (Python 3.7+ preserves insertion order)
    return {
        "overview": overview,
        "financials_dfs": fin_dfs,
        "financials_ttm_dict": fin_ttm,
        "technicals": stats_blocks.get("technicals", {}),
        "short_info": stats_blocks.get("short_info", {}),
        "historical_data": history_df,
    }

# =============================================================================
# Example run
# =============================================================================
if __name__ == "__main__":
    ticker = "NVDA"
    data = get_company_data(ticker, history_pages=3)  # pull 3 pages of history

    print("\n=== DATA KEYS ===")
    print(data.keys())

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

    print("\n=== HISTORICAL_DATA (head) ===")
    hist = data["historical_data"]
    if hist is None or hist.empty:
        print("No historical data found.")
    else:
        print(hist.head())