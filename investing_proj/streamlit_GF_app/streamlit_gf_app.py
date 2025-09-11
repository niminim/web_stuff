# streamlit_gf_app.py
"""
GuruFocus Scores — Streamlit dashboard (max 6 tickers)

- Enter 1–6 tickers
- Two aligned, side-by-side tables with a single header row:
    1) Main Scores   (header: Metric/Ticker + tickers)
    2) Other Indicators (header: Indicator/Ticker + tickers)
- FIXED column widths (px) and FIXED font sizes (px), defined in code
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
import html
from collections import OrderedDict
from typing import Dict, List

import pandas as pd
import streamlit as st

# Try absolute import first; fall back to package-local if running from investing_proj/
try:
    from investing_proj.scrape_fin_data.gf_analyze_ticker import get_financial_data_for_ticker
except Exception:  # noqa: BLE001
    from scrape_fin_data.gf_analyze_ticker import get_financial_data_for_ticker  # type: ignore

# ----------------------------- FIXED SIZES (px) -----------------------------
LABEL_COL_PX = 200                 # "Metric/Ticker" / "Indicator/Ticker" column width
TICKER_COL_PX = 100                # each ticker column width
MAIN_FONT_PX  = 18                 # Main Scores table font size
OTHER_FONT_PX = 15                 # Other Indicators table font size
# ---------------------------------------------------------------------------

# ----------------------------- Page setup + blue background -----------------------------
st.set_page_config(page_title="GuruFocus Scores Dashboard", page_icon="📊", layout="wide")
st.markdown("""
<style>
/* Blue background */
.stApp, [data-testid="stAppViewContainer"] { background: #e8f1ff; }
[data-testid="stSidebar"] > div:first-child { background: rgba(255,255,255,0.75); backdrop-filter: blur(6px); }
[data-testid="stHeader"] { background: transparent; }

/* Base table styling (shared) */
.aligned-table { border-collapse: collapse; table-layout: fixed; width: max-content; }
.aligned-table th, .aligned-table td {
  text-align: left; padding: 6px 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  border-bottom: 1px solid rgba(0,0,0,0.06);
}
.aligned-table thead th { position: sticky; top: 0; background: rgba(255,255,255,0.9); }

/* Scroll wrapper to avoid overflowing the page */
.table-scroll { overflow-x: auto; }

/* Ensure tables don't break layout */
.block-container { padding-top: 1rem; }
</style>
""", unsafe_allow_html=True)

st.title("📊 GuruFocus Main Scores — Quick Dashboard")

# ----------------------------- Sidebar controls (no size toggles) -----------------------------
with st.sidebar:
    st.markdown("### Settings")
    headless = st.toggle(
        "Headless (Playwright fallback)",
        value=True,
        help="Scraper uses requests first; Playwright only if needed."
    )
    show_other_indicators = st.toggle("Show 'Other Indicators' table", value=True)
    st.caption("Tip: Enter up to 6 tickers (comma or space separated).")

# ----------------------------- Helpers -----------------------------
def normalize_tickers(raw: str, max_n: int = 6):
    """Split on commas/whitespace, uppercase, de-dup (preserve order), cap to max_n."""
    parts = re.split(r"[,\s]+", raw)
    dedup, seen = [], set()
    for p in parts:
        t = p.strip().upper()
        if t and t not in seen:
            seen.add(t); dedup.append(t)
    truncated = len(dedup) > max_n
    return dedup[:max_n], truncated

@st.cache_data(show_spinner=False)
def fetch_one(ticker: str, headless_flag: bool):
    """Cached wrapper to keep Streamlit snappy."""
    return get_financial_data_for_ticker(ticker, headless=headless_flag, print_all_data=False)

def inject_fixed_css(n_tickers: int):
    """
    Apply FIXED pixel widths & fixed font sizes to BOTH tables (keeps them aligned):
      - Column 1 (Metric/Ticker or Indicator/Ticker) uses LABEL_COL_PX
      - Each ticker column uses TICKER_COL_PX
      - Main/Other tables use their fixed font sizes (px)
    """
    lines = [
        f".main-scores-table {{ font-size: {MAIN_FONT_PX}px; }}",
        f".other-table {{ font-size: {OTHER_FONT_PX}px; }}",
        # First (label) column width for both tables:
        f".main-scores-table thead th:nth-child(1), .main-scores-table tbody th, "
        f".main-scores-table tbody td:nth-child(1) "
        f"{{ width:{LABEL_COL_PX}px; min-width:{LABEL_COL_PX}px; max-width:{LABEL_COL_PX}px; }}",
        f".other-table thead th:nth-child(1), .other-table tbody th, "
        f".other-table tbody td:nth-child(1) "
        f"{{ width:{LABEL_COL_PX}px; min-width:{LABEL_COL_PX}px; max-width:{LABEL_COL_PX}px; }}",
    ]
    # Ticker columns (same width on both tables)
    # +1: because the first column is the label; header uses thead nth-child; body uses td nth-child
    for i in range(2, n_tickers + 2):  # 1..(1+n_tickers)
        lines.append(
            f".main-scores-table thead th:nth-child({i}), .main-scores-table tbody td:nth-child({i}) "
            f"{{ width:{TICKER_COL_PX}px; min-width:{TICKER_COL_PX}px; max-width:{TICKER_COL_PX}px; }}"
        )
        lines.append(
            f".other-table thead th:nth-child({i}), .other-table tbody td:nth-child({i}) "
            f"{{ width:{TICKER_COL_PX}px; min-width:{TICKER_COL_PX}px; max-width:{TICKER_COL_PX}px; }}"
        )
    st.markdown("<style>\n" + "\n".join(lines) + "\n</style>", unsafe_allow_html=True)

def df_to_single_header_html(df: pd.DataFrame, label_header: str, table_classes: str, wrap_class: str = "table-scroll") -> str:
    """
    Render a DataFrame to HTML with a SINGLE header row:
      <th>{label_header}</th> + one <th> per ticker,
    and the first column in the body is the row label (scope='row').
    """
    # Columns are tickers
    tickers = list(df.columns)
    # Rows are metrics/indicators
    row_labels = [str(i) for i in df.index.tolist()]

    # Build thead (single row)
    thead_cells = ['<th scope="col">{}</th>'.format(html.escape(label_header))]
    thead_cells += ['<th scope="col">{}</th>'.format(html.escape(str(t))) for t in tickers]
    thead_html = "<thead><tr>{}</tr></thead>".format("".join(thead_cells))

    # Build tbody
    body_rows = []
    for r_label in row_labels:
        row_html = ['<th scope="row">{}</th>'.format(html.escape(r_label))]
        values = df.loc[r_label].tolist()
        row_html += ['<td>{}</td>'.format(html.escape("" if v is None else str(v))) for v in values]
        body_rows.append("<tr>{}</tr>".format("".join(row_html)))
    tbody_html = "<tbody>{}</tbody>".format("".join(body_rows))

    table_html = f'<table class="aligned-table {table_classes}">{thead_html}{tbody_html}</table>'
    return f'<div class="{wrap_class}">{table_html}</div>'

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
st.markdown("Enter **1–6 tickers** and click **Run**:")

tickers_input = st.text_input("Ticker(s)", value="NVDA", help="Enter up to 6 tickers.")
run = st.button("Run")

if run:
    tickers, truncated = normalize_tickers(tickers_input, max_n=6)
    if not tickers:
        st.warning("Please enter 1–6 tickers.")
        st.stop()
    if truncated:
        st.info(f"Using only the first 6 tickers: {', '.join(tickers)}")

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

    # ---------------- Build dataframes ----------------
    # Main Scores
    metric_map = [
        ("financial_str", "Financial Strength"),
        ("profit",        "Profitability Rank"),
        ("growth",        "Growth Rank"),
        ("momentum",      "Momentum Rank"),
        ("gf_value",      "GF Value Rank"),
        ("GF_score",      "GF Score"),
    ]
    main_rows = []
    for key, label in metric_map:
        row = {"Metric/Ticker": label}
        for tk in tickers:
            row[tk] = fetched.get(tk, {}).get("main", {}).get(key, "—")
        main_rows.append(row)
    df_main = pd.DataFrame(main_rows).set_index("Metric/Ticker")

    # Other Indicators
    other_rows = []
    for indicator in WHITELIST_OTHER:
        row = {"Indicator/Ticker": indicator}
        for tk in tickers:
            filtered = filter_other_indicators(fetched.get(tk, {}).get("other", {}))
            row[tk] = filtered.get(indicator, "—")
        other_rows.append(row)
    df_other = pd.DataFrame(other_rows).set_index("Indicator/Ticker")

    # ---------------- Apply FIXED sizes (px) & render ----------------
    inject_fixed_css(n_tickers=len(tickers))

    # Main Scores
    st.subheader("Main Scores")
    main_html = df_to_single_header_html(
        df_main,
        label_header="Metric/Ticker",
        table_classes="main-scores-table",
    )
    st.markdown(main_html, unsafe_allow_html=True)
    st.download_button(
        "Download Main Scores CSV",
        data=df_main.to_csv().encode("utf-8"),
        file_name="main_scores.csv",
        mime="text/csv",
    )

    # Other Indicators
    if show_other_indicators:
        st.subheader("Other Indicators")
        other_html = df_to_single_header_html(
            df_other,
            label_header="Indicator/Ticker",
            table_classes="other-table",
        )
        st.markdown(other_html, unsafe_allow_html=True)
        st.download_button(
            "Download Other Indicators CSV",
            data=df_other.to_csv().encode("utf-8"),
            file_name="other_indicators_selected.csv",
            mime="text/csv",
        )

    st.caption("Note: If the requests phase misses some values, the scraper auto-falls back to Playwright.")
