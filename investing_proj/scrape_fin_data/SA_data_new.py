# --- everything above is identical to your last “original code” until the overview helpers ---

# =============================================================================
# Overview snapshot (18 canonical fields) — STOCK
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
# NEW: ETF overview snapshot (ETF-specific labels)
# =============================================================================
_ETF_OVERVIEW_FIELDS = [
    "Assets", "Expense Ratio", "PE Ratio", "Shares Out",
    "Dividend (ttm)", "Dividend Yield", "Ex-Dividend Date", "Payout Frequency",
    "Payout Ratio", "Volume", "Open", "Previous Close", "Day's Range",
    "52-Week Low", "52-Week High", "Beta", "Holdings", "Inception Date",
]

def get_company_overview_etf_from_base(base_url: str, session: requests.Session) -> Optional[Dict[str, str]]:
    """
    Parse ETF overview card (values like in your screenshot).
    Uses the same class-agnostic label->value finder.
    """
    html = fetch_html(base_url + "/", session)
    soup = BeautifulSoup(html, "html.parser")
    out: Dict[str, str] = {}

    # Try direct DOM scan for each ETF label
    for lab in _ETF_OVERVIEW_FIELDS:
        v = _find_value_next_to_label(soup, lab)
        if v:
            out[lab] = v

    # Some pages present 52-week as a range; split into low/high if needed
    if "52-Week Range" in out and ("52-Week Low" not in out or "52-Week High" not in out):
        low, high = None, None
        rng = out.get("52-Week Range")
        if rng and "-" in rng:
            parts = [p.strip() for p in rng.split("-")]
            if len(parts) >= 2:
                low, high = parts[0], parts[1]
        if low:  out["52-Week Low"]  = low
        if high: out["52-Week High"] = high
        out.pop("52-Week Range", None)

    # Return only the ETF overview fields, in this order
    if not out:
        return None
    return {k: out[k] for k in _ETF_OVERVIEW_FIELDS if k in out}

# --- the rest of your original code (statistics for stocks, history, etc.) stays the same ---

# =============================================================================
# Public API (STOCK unchanged, ETF uses ETF overview; adds top-level security_type)
# =============================================================================
def get_company_data(
    ticker: str,
    *,
    history_pages: int = 1
) -> Dict[str, object]:
    """
    STOCK: behavior unchanged.
    ETF  : overview + history; other sections neutral.
    Adds top-level 'security_type' = 'stock' | 'etf' (historical_data remains the last key).
    """
    session = make_session()
    security_type, symbol, base_url = _resolve_entity(ticker, session)

    if security_type == "stock":
        with ThreadPoolExecutor(max_workers=4) as ex:
            fut_overview   = ex.submit(get_company_overview_stock, symbol, session)
            fut_financials = ex.submit(fetch_financial_dfs, symbol, session, security_type)
            fut_stats      = ex.submit(get_statistics_blocks_stock, symbol, session)
            fut_history    = ex.submit(get_historical_data_from_base, base_url, session, history_pages)

            overview     = fut_overview.result()
            fin_dfs      = fut_financials.result()
            stats_blocks = fut_stats.result() or {"technicals": {}, "short_info": {}}
            history_df   = fut_history.result()

        fin_ttm = build_financials_ttm_dict(fin_dfs)

        # Keep original key order; add security_type before historical_data
        return {
            "overview": overview,
            "financials_dfs": fin_dfs,
            "financials_ttm_dict": fin_ttm,
            "technicals": stats_blocks.get("technicals", {}),
            "short_info": stats_blocks.get("short_info", {}),
            "security_type": "stock",
            "historical_data": history_df,
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
            "security_type": "etf",        # <-- added as requested
            "historical_data": history_df, # keep last
        }


# =============================================================================
# Example run — quick checks for a STOCK and an ETF
# =============================================================================
if __name__ == "__main__":
    # -------- STOCK example --------
    ticker = "NVDA"
    data = get_company_data(ticker, history_pages=3)  # pull 3 pages of history

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

    # -------- ETF example --------
    etf = "SPY"  # or use the full URL: "https://stockanalysis.com/etf/spy/"
    data_etf = get_company_data(etf, history_pages=3)

    print("\n\n######## ETF CHECK:", etf, "########")
    print("security_type:", data_etf.get("security_type"))
    print("\n=== DATA KEYS ===")
    print(data_etf.keys())

    print("\n=== OVERVIEW (subset) ===")
    if data_etf["overview"]:
        for k in [
            "Assets", "Expense Ratio", "PE Ratio", "Dividend (ttm)",
            "Dividend Yield", "Ex-Dividend Date", "Payout Frequency",
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
