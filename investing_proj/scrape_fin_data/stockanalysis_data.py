"""
StockAnalysis scraper — Overview + Financials + TTM dict + Technicals + Short Info + Historical Data + (Stocks) Forecast
------------------------------------------------------------------------------------------------------------------------

Public API:
    bundle = get_company_data("NVDA")

    bundle.keys() ->
        dict_keys([
            'overview',
            'financials_dfs',
            'financials_ttm_dict',
            'technicals',
            'short_info',
            'security_type',     # 'stock' | 'etf'
            'historical_data',   # last
            'forecast',          # stock-only: price-target summary + current summary + monthly ratings trend
        ])
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

BASE = "https://stockanalysis.com"

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
    r = session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.text

# =============================================================================
# Security-type / base-URL resolution
# =============================================================================
def _resolve_entity(ticker_or_url: str, session: requests.Session) -> Tuple[str, str, str]:
    """
    Return (security_type, symbol, base_url)
      - security_type in {"stock","etf"}
      - symbol uppercased
      - base_url like https://stockanalysis.com/stocks/NVDA or https://stockanalysis.com/etf/SPY
    """
    s = ticker_or_url.strip()
    if s.lower().startswith("http"):
        u = s.rstrip("/")
        m = re.search(r"https?://stockanalysis\.com/(stocks|etf)/([A-Za-z0-9\.\-]+)", u, re.I)
        if m:
            sec = "stock" if m.group(1).lower() == "stocks" else "etf"
            sym = m.group(2).upper()
            return sec, sym, f"{BASE}/{m.group(1).lower()}/{sym}"
        path = urlparse(u).path.strip("/").split("/")
        sym = (path[-1] if path else s).upper()
        return "stock", sym, f"{BASE}/stocks/{sym}"

    sym = s.upper()
    stock_url = f"{BASE}/stocks/{sym}"
    etf_url   = f"{BASE}/etf/{sym}"
    try:
        r = session.head(stock_url, timeout=6, allow_redirects=True)
        if r.status_code == 405:
            r = session.get(stock_url, timeout=6, allow_redirects=True)
        if r.ok:
            return "stock", sym, stock_url
    except Exception:
        pass
    try:
        r = session.head(etf_url, timeout=6, allow_redirects=True)
        if r.status_code == 405:
            r = session.get(etf_url, timeout=6, allow_redirects=True)
        if r.ok:
            return "etf", sym, etf_url
    except Exception:
        pass
    return "stock", sym, stock_url

# =============================================================================
# Text helpers + label→value finder
# =============================================================================
MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
FREQ_WORDS = {"monthly", "quarterly", "annually", "annual", "semi-annual", "semiannual", "biannual"}

def _normalize_text(s: str) -> str:
    s = re.sub(r"[^\w\s%-./,$']", "", s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s

def _is_value_like(text: str) -> bool:
    t = _normalize_text(text)
    if not t:
        return False
    return any(ch.isdigit() for ch in t) or any(ch in "$%–-/" for ch in t)

def _looks_like_value_for_label(label: str, value: str) -> bool:
    t = _normalize_text(value)
    if not t or t.lower() == _normalize_text(label).lower():
        return False
    if label.lower().endswith("date"):
        return bool(re.search(rf"\b({MONTHS})\b\s+\d{{1,2}},\s*\d{{4}}", t, re.I)) or bool(re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", t))
    if "frequency" in label.lower():
        return t.lower() in FREQ_WORDS
    if label.lower() in {"holdings"}:
        return any(ch.isdigit() for ch in t)
    if "range" in label.lower():
        return "-" in t and any(ch.isdigit() for ch in t)
    return _is_value_like(t)

def _find_value_next_to_label(root: Tag, label_text: str) -> Optional[str]:
    target_norm = _normalize_text(label_text).lower()

    def is_label_text(txt: str) -> bool:
        txtn = _normalize_text(txt).lower()
        if txtn == target_norm:
            return True
        if txtn.startswith(target_norm) and (len(txtn) - len(target_norm)) <= 3:
            return True
        return False

    def match_label(node: Tag) -> bool:
        if not isinstance(node, Tag):
            return False
        txt = node.get_text(" ", strip=True)
        return is_label_text(txt)

    candidates = root.find_all(match_label)
    for lab in candidates:
        parent = lab.parent
        if not isinstance(parent, Tag):
            continue

        # immediate next siblings
        for sib in lab.next_siblings:
            if isinstance(sib, NavigableString):
                continue
            if isinstance(sib, Tag):
                val = _normalize_text(sib.get_text(" ", strip=True))
                if _looks_like_value_for_label(label_text, val):
                    return val

        # adjacent child in same parent
        kids = [c for c in parent.children if isinstance(c, Tag)]
        for i, c in enumerate(kids):
            if c is lab and i + 1 < len(kids):
                val = _normalize_text(kids[i + 1].get_text(" ", strip=True))
                if _looks_like_value_for_label(label_text, val):
                    return val

        # one level up
        gp = parent.parent
        if isinstance(gp, Tag):
            elems = [e for e in gp.children if isinstance(e, Tag)]
            for i, e in enumerate(elems):
                if lab in e.descendants and i + 1 < len(elems):
                    val = _normalize_text(elems[i + 1].get_text(" ", strip=True))
                    if _looks_like_value_for_label(label_text, val):
                        return val
    return None

# =============================================================================
# Financial table parsing
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
# Financials (stocks only)
# =============================================================================
FIN_URLS_STOCK = {
    "income":        "https://stockanalysis.com/stocks/{t}/financials/",
    "balance_sheet": "https://stockanalysis.com/stocks/{t}/financials/balance-sheet/",
    "cash_flow":     "https://stockanalysis.com/stocks/{t}/financials/cash-flow-statement/",
    "ratios":        "https://stockanalysis.com/stocks/{t}/financials/ratios/",
}

def fetch_financial_dfs(ticker: str, session: requests.Session, security_type: str) -> Dict[str, Optional[pd.DataFrame]]:
    if security_type == "etf":
        return {k: None for k in ["income", "balance_sheet", "cash_flow", "ratios"]}

    t = ticker.lower().strip()
    out: Dict[str, Optional[pd.DataFrame]] = {k: None for k in FIN_URLS_STOCK}
    with ThreadPoolExecutor(max_workers=4) as ex:
        fut2key = {
            ex.submit(get_data_table, url.format(t=t), session): k
            for k, url in FIN_URLS_STOCK.items()
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
# Overview — STOCK (JSON-first, DOM fallback)
# =============================================================================
CANONICAL_OVERVIEW_FIELDS = [
    "Market Cap", "Revenue (ttm)", "Net Income (ttm)", "Shares Out",
    "EPS (ttm)", "PE Ratio", "Forward PE", "Dividend", "Ex-Dividend Date",
    "Volume", "Open", "Previous Close", "Day's Range", "52-Week Range",
    "Beta", "Analysts", "Price Target", "Earnings Date",
]

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

def get_company_overview_from_base(base_url: str, session: requests.Session) -> Optional[Dict[str, str]]:
    html = fetch_html(base_url + "/", session)
    soup = BeautifulSoup(html, "html.parser")
    out: Dict[str, str] = {}

    try:
        tag = soup.find("script", id="__NEXT_DATA__")
        if tag and tag.string:
            boot = json.loads(tag.string)
            keymap = {
                "Market Cap":        {"marketCap","marketcap","market_cap"},
                "Revenue (ttm)":     {"revenueTtm","revenueTTM","revenueTrailing12M"},
                "Net Income (ttm)":  {"netIncomeTtm","netIncomeTTM","netIncomeTrailing12M"},
                "Shares Out":        {"sharesOutstanding","sharesOut"},
                "EPS (ttm)":         {"epsTtm","epsTrailingTwelveMonths"},
                "PE Ratio":          {"peRatio","pe"},
                "Forward PE":        {"forwardPE","forwardPe"},
                "Dividend":          {"dividendYield","dividendRate","dividend"},
                "Ex-Dividend Date":  {"exDividendDate"},
                "Volume":            {"volume"},
                "Open":              {"open"},
                "Previous Close":    {"previousClose","prevClose"},
                "Day's Range":       {"daysRange","dayRange"},
                "52-Week Range":     {"fiftyTwoWeekRange","52WeekRange"},
                "Beta":              {"beta"},
                "Analysts":          {"analystRating","analystRecommendation"},
                "Price Target":      {"priceTarget","targetMeanPrice"},
                "Earnings Date":     {"earningsDate","nextEarningsDate"},
            }
            for label, cands in keymap.items():
                val = _find_first_key(boot, cands)
                if isinstance(val, dict) and {"low","high"} & set(val.keys()):
                    val = f"{val.get('low')} - {val.get('high')}"
                elif label in {"Day's Range","52-Week Range"} and isinstance(val, (list, tuple)) and len(val) >= 2:
                    val = f"{val[0]} - {val[1]}"
                if val is not None:
                    out[label] = _normalize_text(str(val))
    except Exception:
        pass

    missing = [lab for lab in CANONICAL_OVERVIEW_FIELDS if lab not in out]
    if missing:
        for lab in missing:
            val = _find_value_next_to_label(soup, lab)
            if val:
                out[lab] = val

    if not out:
        return None
    return {k: out[k] for k in CANONICAL_OVERVIEW_FIELDS if k in out}

def get_company_overview_stock(ticker: str, session: requests.Session) -> Optional[Dict[str, str]]:
    base = f"{BASE}/stocks/{ticker.lower().strip()}"
    return get_company_overview_from_base(base, session)

# =============================================================================
# Overview — ETF (JSON-first, DOM-fallback; COMPLETE field list)
# =============================================================================
ETF_OVERVIEW_FIELDS = [
    "Assets", "Expense Ratio", "PE Ratio", "Shares Out",
    "Dividend (ttm)", "Dividend Yield", "Ex-Dividend Date", "Payout Frequency",
    "Payout Ratio", "Volume", "Open", "Previous Close", "Day's Range",
    "52-Week Low", "52-Week High", "Beta", "Holdings", "Inception Date",
]

ETF_KEYMAP = {
    "Assets": {"assets","assetsUnderManagement","totalAssets","aum"},
    "Expense Ratio": {"expenseRatio","totalExpenseRatio"},
    "PE Ratio": {"pe","peRatio"},
    "Shares Out": {"sharesOut","sharesOutstanding","outstandingShares"},
    "Dividend (ttm)": {"dividendTtm","dividendTTM","ttmDividend","dividendTtmAmount"},
    "Dividend Yield": {"dividendYield","yield"},
    "Ex-Dividend Date": {"exDividendDate"},
    "Payout Frequency": {"payoutFrequency","distributionFrequency","frequency"},
    "Payout Ratio": {"payoutRatio","distributionPayoutRatio"},
    "Volume": {"volume"},
    "Open": {"open"},
    "Previous Close": {"previousClose","prevClose"},
    "Day's Range": {"daysRange","dayRange"},
    "52-Week Low": {"fiftyTwoWeekLow","52WeekLow"},
    "52-Week High": {"fiftyTwoWeekHigh","52WeekHigh"},
    "Beta": {"beta"},
    "Holdings": {"holdings","numberOfHoldings","numHoldings"},
    "Inception Date": {"inceptionDate","fundInceptionDate"},
}

def get_company_overview_etf_from_base(base_url: str, session: requests.Session) -> Optional[Dict[str, str]]:
    html = fetch_html(base_url + "/", session)
    soup = BeautifulSoup(html, "html.parser")
    out: Dict[str, str] = {}

    try:
        tag = soup.find("script", id="__NEXT_DATA__")
        if tag and tag.string:
            boot = json.loads(tag.string)
            for label, cands in ETF_KEYMAP.items():
                val = _find_first_key(boot, cands)
                if label == "Day's Range" and isinstance(val, (list, tuple)) and len(val) >= 2:
                    val = f"{val[0]} - {val[1]}"
                if val is not None:
                    out[label] = _normalize_text(str(val))
    except Exception:
        pass

    missing = [lab for lab in ETF_OVERVIEW_FIELDS if lab not in out]
    if missing:
        for lab in missing:
            val = _find_value_next_to_label(soup, lab)
            if val and _looks_like_value_for_label(lab, val):
                out[lab] = val

    if "52-Week Range" in out and ("52-Week Low" not in out or "52-Week High" not in out):
        rng = out.get("52-Week Range")
        if rng and "-" in rng:
            parts = [p.strip() for p in rng.split("-")]
            if len(parts) >= 2:
                out["52-Week Low"], out["52-Week High"] = parts[0], parts[1]
        out.pop("52-Week Range", None)

    if not out:
        return None
    return {k: out[k] for k in ETF_OVERVIEW_FIELDS if k in out}

# =============================================================================
# /statistics/ (stocks only)
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

def get_statistics_blocks_stock(ticker: str, session: requests.Session) -> dict[str, dict] | None:
    url = f"{BASE}/stocks/{ticker.lower().strip()}/statistics/"
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
# Forecast — stock only (/forecast/)
#   - Price/Change table across Low/Average/Median/High (your screenshot)
#   - Current vs Low/Average/Median/High summary block (if present)
#   - Monthly analyst ratings trend table
# =============================================================================
def _parse_simple_table(tbl: Tag) -> pd.DataFrame:
    """Generic HTML table -> DataFrame helper (keeps all rows/cols as text)."""
    headers = [th.get_text(strip=True) for th in tbl.find_all("th")]
    if not headers:
        first_tr = tbl.find("tr")
        headers = [td.get_text(strip=True) for td in (first_tr.find_all("td") if first_tr else [])]
    rows: List[List[str]] = []
    for tr in tbl.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if cells and cells != headers:
            rows.append(cells)
    n = len(headers) if headers else max((len(r) for r in rows), default=0)
    norm_rows = [(r + [""] * (n - len(r)))[:n] for r in rows]
    if not headers:
        headers = [f"C{i+1}" for i in range(n)]
    df = pd.DataFrame(norm_rows, columns=headers)
    return df

def get_forecast_stock(ticker: str, session: requests.Session) -> Optional[dict]:
    """
    Returns (when any present):
      {
        'price_targets_table': {           # the two-row table with Price / Change
            'Price': {'Low':..., 'Average':..., 'Median':..., 'High':...},
            'Change': {'Low':..., 'Average':..., 'Median':..., 'High':...}
        },
        'price_targets_current': {         # optional small summary with 'Current' + columns
            'Current': <str> or None,
            'Low': <str> or None,
            'Average': <str> or None,
            'Median': <str> or None,
            'High': <str> or None
        },
        'ratings_trend_df': pd.DataFrame   # table with rows: Strong Buy/Buy/Hold/Sell/Strong Sell/Total
      }
    """
    url = f"{BASE}/stocks/{ticker.lower().strip()}/forecast/"
    html = fetch_html(url, session)
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return None

    price_table_dict = None
    price_current_dict = None
    ratings_df = None

    # helpers
    def has_cols(cols: List[str], ths_lower: List[str]) -> bool:
        s = set(ths_lower)
        return all(c.lower() in s for c in cols)

    month_like = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+'?\d{2}$", re.I)

    for tbl in tables:
        ths = [t.get_text(strip=True) for t in tbl.find_all("th")]
        ths_lower = [t.lower() for t in ths]

        # Ratings trend table
        if ths and _normalize_text(ths[0]).lower() in {"rating", "ratings"} and any(month_like.match(h) for h in ths[1:]):
            ratings_df = _parse_simple_table(tbl)
            ratings_df.rename(columns={ratings_df.columns[0]: "Rating"}, inplace=True)
            continue

        # Price/Change table (Target | Low | Average | Median | High) with rows Price/Change
        if ths and ("target" in ths_lower[0]) and has_cols(["low", "average", "median", "high"], ths_lower):
            df = _parse_simple_table(tbl)
            first_col = df.columns[0]  # "Target"
            # Build dict keyed by row labels
            row_map: dict[str, dict] = {}
            for _, row in df.iterrows():
                label = _normalize_text(str(row[first_col])).lower()
                per_col = {}
                for c in df.columns[1:]:
                    per_col[_normalize_text(c).capitalize()] = _normalize_text(str(row[c]))
                row_map[label] = per_col
            # Extract canonical rows if present
            out = {}
            if "price" in row_map:
                out["Price"] = row_map["price"]
            if "change" in row_map:
                out["Change"] = row_map["change"]
            if out:
                price_table_dict = out
            continue

        # Current-vs-targets summary: headers Low/Average/Median/High (and sometimes 'Current')
        if ths and has_cols(["low", "average", "median", "high"], ths_lower) and ("target" not in ths_lower[0]) and (_normalize_text(ths[0]).lower() not in {"rating", "ratings"}):
            df = _parse_simple_table(tbl)
            # This block often has only one data row (e.g., 'Current') or no row label.
            # We'll collect by header names.
            cur = {k.capitalize(): None for k in ["Current", "Low", "Average", "Median", "High"]}
            # If there is a 'Current' header, great. Otherwise, try first column as row label.
            col_map = { _normalize_text(c).capitalize(): c for c in df.columns }
            # Row-wise attempt: take the first row
            if not df.empty:
                first_row = df.iloc[0]
                # If table includes 'Current' as a header
                for want in ["Current", "Low", "Average", "Median", "High"]:
                    if want in col_map:
                        cur[want] = _normalize_text(str(first_row[col_map[want]]))
                # If 'Current' wasn't in headers, but first column looks like 'Current'
                if cur["Current"] in (None, ""):
                    # assume first cell (of first row) is the 'Current' value or label
                    cur["Current"] = _normalize_text(str(first_row.iloc[0]))
            price_current_dict = cur
            continue

    if price_table_dict is None and ratings_df is None and price_current_dict is None:
        return None

    return {
        "price_targets_table": price_table_dict or {},
        "price_targets_current": price_current_dict or {},
        "ratings_trend_df": ratings_df,  # may be None if not found
    }

# =============================================================================
# Historical Data (stock or ETF)
# =============================================================================
HIST_HEADERS_MUST_HAVE = {"Date", "Open", "High", "Low", "Close", "Volume"}

def _find_history_table(soup: BeautifulSoup) -> Optional[Tag]:
    for tbl in soup.find_all("table"):
        ths = [th.get_text(strip=True) for th in tbl.find_all("th")]
        if not ths:
            continue
        if HIST_HEADERS_MUST_HAVE.issubset(set(ths)):
            return tbl
    return None

def _parse_history_table(tbl: Tag) -> pd.DataFrame:
    headers = [th.get_text(strip=True) for th in tbl.find_all("th")]
    rows: List[List[str]] = []
    for tr in tbl.find_all("tr")[1:]:
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if cells:
            if len(cells) < len(headers):
                cells += [""] * (len(headers) - len(cells))
            elif len(cells) > len(headers):
                cells = cells[:len(headers)]
            rows.append(cells)
    df = pd.DataFrame(rows, columns=headers)
    return df

def _clean_history_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    keep_cols = [c for c in out.columns if c in {"Date","Open","High","Low","Close","Volume","Change"}]
    out = out[keep_cols]

    if "Date" in out.columns:
        out["Date"] = pd.to_datetime(out["Date"], errors="coerce")

    for c in ["Open","High","Low","Close"]:
        if c in out.columns:
            out[c] = (
                out[c].str.replace("$","", regex=False)
                      .str.replace(",","", regex=False)
                      .replace({"": None, "—": None, "-": None})
            )
            out[c] = pd.to_numeric(out[c], errors="coerce")

    if "Volume" in out.columns:
        out["Volume"] = (
            out["Volume"].str.replace(",","", regex=False)
                         .replace({"": None, "—": None, "-": None})
        )
        out["Volume"] = pd.to_numeric(out["Volume"], errors="coerce").astype("Int64")

    if "Change" in out.columns:
        s = out["Change"].astype(str)
        s = s.str.replace("%","", regex=False).str.replace(",", "", regex=False)
        s = s.replace({"": None, "—": None, "-": None})
        out["Change"] = pd.to_numeric(s, errors="coerce")/100.0

    return out

def _find_next_page_url(soup: BeautifulSoup, base_url: str) -> Optional[str]:
    a = soup.find("a", attrs={"rel": "next"})
    if a and a.get("href"):
        return urljoin(base_url, a["href"])

    candidates = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "?p=" in href:
            candidates.append(urljoin(base_url, href))
    if not candidates:
        return None

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
    pu = urlparse(url)
    q = parse_qs(pu.query)
    q["p"] = [str(page)]
    new_q = urlencode({k: v[0] for k, v in q.items()})
    return urlunparse((pu.scheme, pu.netloc, pu.path, pu.params, new_q, pu.fragment))

def get_historical_data_from_base(
    base_url: str,
    session: requests.Session,
    max_pages: int = 1
) -> Optional[pd.DataFrame]:
    base_candidates = [
        base_url.rstrip("/") + "/history/",
        base_url.rstrip("/") + "/historical/",
    ]

    all_pages: List[pd.DataFrame] = []

    for base in base_candidates:
        try:
            html = fetch_html(base, session)
            soup = BeautifulSoup(html, "html.parser")
            tbl = _find_history_table(soup)
            if tbl is None:
                continue

            df1 = _parse_history_table(tbl)
            if not df1.empty:
                all_pages.append(df1)

            page = 1
            while page < max_pages:
                page += 1
                next_url = _ensure_page(base, page)
                try:
                    html_n = fetch_html(next_url, session)
                except Exception:
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

            if all_pages:
                break

        except Exception:
            continue

    if not all_pages:
        return None

    raw = pd.concat(all_pages, ignore_index=True)
    cleaned = _clean_history_df(raw)

    if "Date" in cleaned.columns:
        cleaned = (cleaned.dropna(subset=["Date"])
                           .drop_duplicates()
                           .sort_values("Date", ascending=False)
                           .reset_index(drop=True))
    return cleaned

# =============================================================================
# Public API
# =============================================================================
def get_company_data(
    ticker: str,
    *,
    history_pages: int = 1
) -> Dict[str, object]:
    session = make_session()
    security_type, symbol, base_url = _resolve_entity(ticker, session)

    if security_type == "stock":
        with ThreadPoolExecutor(max_workers=5) as ex:
            fut_overview   = ex.submit(get_company_overview_stock, symbol, session)
            fut_financials = ex.submit(fetch_financial_dfs, symbol, session, security_type)
            fut_stats      = ex.submit(get_statistics_blocks_stock, symbol, session)
            fut_forecast   = ex.submit(get_forecast_stock, symbol, session)
            fut_history    = ex.submit(get_historical_data_from_base, base_url, session, history_pages)

            overview     = fut_overview.result()
            fin_dfs      = fut_financials.result()
            stats_blocks = fut_stats.result() or {"technicals": {}, "short_info": {}}
            forecast     = fut_forecast.result() or {}
            history_df   = fut_history.result()

        fin_ttm = build_financials_ttm_dict(fin_dfs)

        return {
            "overview": overview,
            "financials_dfs": fin_dfs,
            "financials_ttm_dict": fin_ttm,
            "technicals": stats_blocks.get("technicals", {}),
            "short_info": stats_blocks.get("short_info", {}),
            "security_type": "stock",
            "historical_data": history_df,
            "forecast": forecast,
        }

    else:
        with ThreadPoolExecutor(max_workers=2) as ex:
            fut_overview = ex.submit(get_company_overview_etf_from_base, base_url, session)
            fut_history  = ex.submit(get_historical_data_from_base, base_url, session, history_pages)
            overview   = fut_overview.result()
            history_df = fut_history.result()

        fin_dfs = {k: None for k in ["income", "balance_sheet", "cash_flow", "ratios"]}
        fin_ttm = {}
        technicals = {}
        short_info = {}

        return {
            "overview": overview,
            "financials_dfs": fin_dfs,
            "financials_ttm_dict": fin_ttm,
            "technicals": technicals,
            "short_info": short_info,
            "security_type": "etf",
            "historical_data": history_df,
            "forecast": {},  # ETFs: empty
        }

# =============================================================================
# Example run — quick checks
# =============================================================================
if __name__ == "__main__":
    # STOCK
    ticker = "NVDA"
    data = get_company_data(ticker, history_pages=3)

    print("\n=== STOCK CHECK:", ticker, "===")
    print("security_type:", data.get("security_type"))
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

    print("\n=== FORECAST (stock) ===")
    fc = data.get("forecast", {})
    print("Price targets table:", fc.get("price_targets_table"))
    pct = fc.get("price_targets_current")
    print("Price targets current:", pct if pct else "{}")
    rtdf = fc.get("ratings_trend_df")
    if isinstance(rtdf, pd.DataFrame):
        print("Ratings trend (head):")
        print(rtdf.head())
    else:
        print("Ratings trend: None")

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

    # ETF
    etf = "SPY"
    data_etf = get_company_data(etf, history_pages=3)

    print("\n\n######## ETF CHECK:", etf, "########")
    print("security_type:", data_etf.get("security_type"))
    print("\n=== DATA KEYS ===")
    print(data_etf.keys())

    print("\n=== OVERVIEW (subset) ===")
    if data_etf["overview"]:
        for k in [
            "Assets", "Expense Ratio", "PE Ratio", "Shares Out",
            "Dividend (ttm)", "Dividend Yield", "Ex-Dividend Date", "Payout Frequency",
            "Payout Ratio", "Holdings", "Inception Date",
            "Volume", "Open", "Previous Close", "Day's Range",
            "52-Week Low", "52-Week High", "Beta",
        ]:
            if k in data_etf["overview"]:
                print(k, ":", data_etf["overview"].get(k))
    else:
        print("No overview.")

    print("\n=== TECHNICALS (ETF) ===")
    if data_etf["technicals"]:
        for k, v in data_etf["technicals"].items():
            print(f"{k}: {v}")
    else:
        print("{}")

    print("\n=== SHORT INFO (ETF) ===")
    if data_etf["short_info"]:
        for k, v in data_etf["short_info"].items():
            print(f"{k}: {v}")
    else:
        print("{}")

    print("\n=== FINANCIALS_DFS shapes (ETF) ===")
    for k, df in data_etf["financials_dfs"].items():
        print(f"{k}: {None if df is None else df.shape}")

    print("\n=== FINANCIALS_TTM_DICT (ETF) ===")
    print(data_etf["financials_ttm_dict"])

    print("\n=== HISTORICAL_DATA (head) — ETF ===")
    hist_etf = data_etf["historical_data"]
    if hist_etf is None or hist_etf.empty:
        print("No historical data found.")
    else:
        print(hist_etf.head())
