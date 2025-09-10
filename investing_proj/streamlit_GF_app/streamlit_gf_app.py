# streamlit_gf_app.py
"""
GuruFocus Scores — Streamlit dashboard (max 5 tickers)

- Enter 1–5 tickers
- Show companies SIDE-BY-SIDE as tables (wide-friendly, scrollable)
- Main Scores (text) + selected 'Other Indicators'
- CSV downloads for both tables
"""

# ---------- robust import bootstrap (works from web_stuff/ or investing_proj/) ----------
import os, sys
HERE = os.path.abspath(os.path.dirname(__file__))
ROOT_PROJECT = os.path.abspath(os.path.join(HERE, "..", ".."))       # .../web_stuff
ROOT_PACKAGE = os.path.abspath(os.path.join(HERE, ".."))              # .../investing_proj
for p in (ROOT_PROJECT, ROOT_PACKAGE):
    if p not in sys.path:
        sys.path.insert(0, p)
# ---------------------------------------------------------------------------------------

import re
from collections import OrderedDict
from typing import Dict, List

import pandas as pd
import streamlit as st

# Try absolute import first; fall back to package-local if running from investing_proj/
try:
    from investing_proj.scrape_fin_data.gf_analyze_ticker import get_financial_data_for_ticker
except Exception:  # noqa: BLE001
    from scrape_fin_data.gf_analyze_ticker import get_financial_data_for_ticker  # type: ignore

# ----------------------------- Page setup + blue background -----------------------------
st.set_page_config(page_title="GuruFocus Scores Dashboard", page_icon="📊", layout="wide")
st.markdown("""
<style>
.stApp, [data-testid="stAppViewContainer"] { background: #e8f1ff; }
[data-testid="stSidebar"] > div:first-child { background: rgba(255,255,255,0.75); backdrop-filter: blur(6px); }
[data-testid="stHeader"] { background: transparent; }
</style>
""", unsafe_allow_html=True)

st.title("📊 GuruFocus Main Scores — Quick Dashboard")

# ----------------------------- Sidebar controls -----------------------------
with st.sidebar:
    st.markdown("### Settings")
    headless = st.toggle(
        "Headless (Playwright fallback)",
        value=True,
        help="Scraper uses requests first; Playwright only if needed."
    )
    show_other_indicators = st.toggle("Show 'Other Indicators' table", value=True)
    st.caption("Tip: Enter up to 5 tickers (comma or space separated).")

# ----------------------------- Helpers -----------------------------
def normalize_tickers(raw: str, max_n: int = 5):
    """Split on commas/whitespace, uppercase, de-dup (preserve order), cap to max_n."""
    parts = re.split(r"[,\s]+", raw)
    dedup, seen = [], set()
    for p in parts:
        t = p.strip().upper()
        if t and t not in seen:
            seen.add(t)
            dedup.append(t)
    truncated = len(dedup) > max_n
    return dedup[:max_n], truncated

def _parse_score_to_num(s: str) -> float | None:
    """Parse '7/10' or '85/100' into a 0–10 float for comparison; None on failure."""
    if not isinstance(s, str):
        return None
    s = s.strip()
    m10 = re.match(r"^\s*(\d+(?:\.\d+)?)\s*/\s*10\s*$", s)
    m100 = re.match(r"^\s*(\d+(?:\.\d+)?)\s*/\s*100\s*$", s)
    if m10:
        try:
            return float(m10.group(1))
        except ValueError:
            return None
    if m100:
        try:
            return round(float(m100.group(1)) / 10.0, 2)
        except ValueError:
            return None
    return None

@st.cache_data(show_spinner=False)
def fetch_one(ticker: str, headless_flag: bool):
    """Cached wrapper to keep Streamlit snappy."""
    return get_financial_data_for_ticker(ticker, headless=headless_flag, print_all_data=False)

# ----- Whitelist & filtering for 'Other Indicators' -----
WHITELIST_OTHER = [
    "Cash-To-Debt",
    "Equity-to-Asset",
    "Debt-to-Equity",
    "Debt-to-EBITDA",
    "Piotroski F-Score",
    "Altman Z-Score",
    "ROE %",
    "ROA %",
    "ROIC %",
    "Moat Score",
    "3-1 Month Momentum %",
    "Current Ratio",
    "Quick Ratio",
    "Forward PE Ratio",
    "PS Ratio",
    "PB Ratio",
]

def _norm_key(s: str) -> str:
    return re.sub(r"[ \-\(\),.%/]+", "", s.strip().lower())

ALIASES = {
    "Cash-To-Debt": ["Cash-To-Debt", "Cash to Debt"],
    "Equity-to-Asset": ["Equity-to-Asset", "Equity to Asset", "Equity-to-Assets"],
    "Debt-to-Equity": ["Debt-to-Equity", "Debt to Equity", "D/E"],
    "Debt-to-EBITDA": ["Debt-to-EBITDA", "Debt / EBITDA", "Debt to EBITDA"],
    "Piotroski F-Score": ["Piotroski F-Score", "Piotroski F Score", "F-Score"],
    "Altman Z-Score": ["Altman Z-Score", "Altman Z Score", "Z-Score"],
    "ROE %": ["ROE %", "Return on Equity %", "ROE"],
    "ROA %": ["ROA %", "Return on Assets %", "ROA"],
    "ROIC %": ["ROIC %", "Return on Invested Capital %", "ROIC"],
    "Moat Score": ["Moat Score", "Moat Rank", "Business Predictability Rank"],
    "3-1 Month Momentum %": ["3-1 Month Momentum %", "3-1 Month Momentum", "3/1 Month Momentum %"],
    "Current Ratio": ["Current Ratio"],
    "Quick Ratio": ["Quick Ratio"],
    "Forward PE Ratio": ["Forward PE Ratio", "Forward P/E", "Forward PE"],
    "PS Ratio": ["PS Ratio", "P/S", "Price-to-Sales"],
    "PB Ratio": ["PB Ratio", "P/B", "Price-to-Book"],
}
ALIAS_NORM = {canon: [_norm_key(a) for a in aliases] for canon, aliases in ALIASES.items()}

def filter_other_indicators(other: Dict[str, str]) -> "OrderedDict[str, str]":
    """Pick only the whitelisted indicators, preserving the requested order."""
    if not other:
        return OrderedDict((k, "—") for k in WHITELIST_OTHER)
    scraped_norm = {_norm_key(k): (k, v) for k, v in other.items()}
    out = OrderedDict()
    for canon in WHITELIST_OTHER:
        val = "—"
        for norm_alias in ALIAS_NORM.get(canon, [_norm_key(canon)]):
            if norm_alias in scraped_norm:
                _, v = scraped_norm[norm_alias]
                val = v
                break
        out[canon] = val
    return out

# ----------------------------- Main UI -----------------------------
st.markdown("Enter **1–5 tickers** and click **Run**:")

tickers_input = st.text_input("Ticker(s)", value="NVDA", help="Enter up to 5 tickers.")
run = st.button("Run")

if run:
    tickers, truncated = normalize_tickers(tickers_input, max_n=5)
    if not tickers:
        st.warning("Please enter 1–5 tickers.")
        st.stop()
    if truncated:
        st.info(f"Using only the first 5 tickers: {', '.join(tickers)}")

    # ---------------- Fetch all tickers ----------------
    fetched: Dict[str, Dict[str, Dict[str, str]]] = {}
    errors: List[str] = []
    for tk in tickers:
        try:
            main_scores, other = fetch_one(tk, headless)
            fetched[tk] = {"main": main_scores, "other": other}
        except Exception as e:  # noqa: BLE001
            errors.append(f"{tk}: {e}")

    if errors:
        st.error("Some tickers failed:\n\n" + "\n".join(errors))

    if not fetched:
        st.stop()

    # ---------------- Build 'Main Scores' table (side-by-side) ----------------
    # Map scraper keys -> pretty labels
    metric_map = [
        ("financial_str", "Financial Strength"),
        ("profit",        "Profitability Rank"),
        ("growth",        "Growth Rank"),
        ("momentum",      "Momentum Rank"),
        ("gf_value",      "GF Value Rank"),
        ("GF_score",      "GF Score"),
    ]

    # Text version (7/10, 85/100, etc.)
    main_rows_text = []
    for key, label in metric_map:
        row = {"Metric": label}
        for tk in tickers:
            val = fetched.get(tk, {}).get("main", {}).get(key, "—")
            row[tk] = val
        main_rows_text.append(row)
    df_main_text = pd.DataFrame(main_rows_text).set_index("Metric")

    st.subheader("Main Scores (text)")
    st.dataframe(df_main_text, use_container_width=True)
    st.download_button(
        "Download Main Scores (text) CSV",
        data=df_main_text.to_csv().encode("utf-8"),
        file_name="main_scores_text.csv",
        mime="text/csv",
    )

    # ---------------- Build 'Other Indicators' table (side-by-side) ----------------
    if show_other_indicators:
        other_rows = []
        # Start with whitelist order
        for indicator in WHITELIST_OTHER:
            row = {"Indicator": indicator}
            for tk in tickers:
                filtered = filter_other_indicators(fetched.get(tk, {}).get("other", {}))
                row[tk] = filtered.get(indicator, "—")
            other_rows.append(row)
        df_other = pd.DataFrame(other_rows).set_index("Indicator")

        st.subheader("Other Indicators (selected)")
        st.dataframe(df_other, use_container_width=True)
        st.download_button(
            "Download Other Indicators CSV",
            data=df_other.to_csv().encode("utf-8"),
            file_name="other_indicators_selected.csv",
            mime="text/csv",
        )

    st.caption("Note: If the requests phase misses some values, the scraper auto-falls back to Playwright.")
