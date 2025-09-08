# main.py
"""
Simple runner (no argparse).
Fetch data → compute indicators → analyze (incl. candles) → single clean report → plots.
"""

from __future__ import annotations
import os
import sys
from typing import Optional, Dict

# Ensure project root is importable
PROJECT_ROOT = "/home/nim/venv/web_stuff/investing_proj"
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Local imports
from technical_analysis.indicators import prepare_from_data
from technical_analysis.plotting import plot_bollinger, plot_rsi, plot_macd
from technical_analysis.analysis import analyze_all
from technical_analysis.textual_explanations import generate_textual_explanations
from scrape_fin_data.stockanalysis_data import get_company_data


def run_pipeline(
    ticker: str,
    get_company_data_fn,
    *,
    history_pages: int = 3,
    show_plots: bool = True,
    save_dir: Optional[str] = None,
    annotate_plots: bool = True,
    show_reasons: bool = True  # controls '(reasons)' footer in the report
) -> Dict[str, object]:
    """
    End-to-end pipeline:
      - fetch data
      - compute indicators
      - run analysis (incl. candlestick patterns)
      - print single consolidated report
      - (optionally) plot
    """
    # --- scrape ---
    data = get_company_data_fn(ticker, history_pages=history_pages)

    # --- compute indicators ---
    dfs = prepare_from_data(data)
    df_asc, df_desc = dfs["indicators_asc"], dfs["indicators_desc"]

    # --- analysis ---
    analysis = analyze_all(df_asc)  # returns parts + combined + summary (unused now)

    # --- single clean report ---
    text_report = generate_textual_explanations(ticker, df_asc, analysis, show_reasons=show_reasons)
    print(text_report)

    # --- plots (optional) ---
    if show_plots:
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            bb_path   = os.path.join(save_dir, f"{ticker}_bollinger.png")
            rsi_path  = os.path.join(save_dir, f"{ticker}_rsi.png")
            macd_path = os.path.join(save_dir, f"{ticker}_macd.png")
        else:
            bb_path = rsi_path = macd_path = None

        ann = analysis.get("parts", {}) if annotate_plots else {}
        plot_bollinger(df_asc, title=f"{ticker} — Bollinger Bands", save_path=bb_path,   annotate=ann)
        plot_rsi(df_asc,       title=f"{ticker} — RSI",              save_path=rsi_path)
        plot_macd(df_asc,      title=f"{ticker} — MACD",             save_path=macd_path)

    return {"indicators_asc": df_asc, "indicators_desc": df_desc, "analysis": analysis}


# ------------------------------- direct call ---------------------------------
if __name__ == "__main__":
    results = run_pipeline(
        ticker="NVDA",
        get_company_data_fn=get_company_data,
        history_pages=3,
        show_plots=True,
        save_dir=None,
        annotate_plots=True,
        show_reasons=True,  # set False to hide the '(reasons)' footer
    )
