# main.py
"""
Simple runner (no argparse).
Fetches data, computes indicators, runs analysis, plots,
and prints both a narrative summary (with strategies) and a structured report.
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


# --------------------------- pretty printer (structured) ----------------------
def _fallback_headline(ticker: str, combined: dict) -> str:
    return f"{ticker}: {combined['verdict'].upper()} (score {combined['score']:+.2f}, confidence {combined['confidence']:.2f})"

def _try_build_summary(df_asc, analysis_dict: Dict[str, object]) -> Optional[Dict[str, object]]:
    # Optional: if your analysis module exposes build_rich_summary, use it dynamically
    try:
        from technical_analysis.analysis import build_rich_summary  # type: ignore
    except Exception:
        return None
    try:
        parts = analysis_dict["parts"]
        combined = analysis_dict["combined"]
        return build_rich_summary(df_asc, parts, combined)
    except Exception:
        return None

def pretty_print_analysis(ticker: str, df_asc, analysis: Dict[str, object]) -> None:
    """
    Nicely print the analysis. Uses 'summary' if available; otherwise minimal fallback.
    """
    combined = analysis.get("combined", {})
    parts    = analysis.get("parts", {})

    summary = analysis.get("summary")
    if summary is None:
        summary = _try_build_summary(df_asc, analysis)

    if summary is None:
        print("\n" + "=" * 80)
        print(_fallback_headline(ticker, combined))
        print("-" * 80)
        reasons = combined.get("reasons", "")
        if reasons:
            print(f"Reasons: {reasons}")
        if parts:
            print("\nKey points:")
            for key in ("bollinger", "macd", "rsi", "volume"):
                if key in parts and parts[key].get("short_note"):
                    print(f"  • {parts[key]['short_note']}")
        print("=" * 80 + "\n")
        return

    # Rich printing path
    print("\n" + "=" * 80)
    print(summary["headline"])
    print("-" * 80)
    print(f"Reasons: {combined.get('reasons','')}")
    print("\nKey points:")
    for b in summary.get("bullets", []):
        if b:
            print(f"  • {b}")

    lv = summary.get("levels", {})
    mt = summary.get("metrics", {})

    print("\nLevels:")
    print(f"  HH20: {lv.get('hh20')}  (proximity: {lv.get('prox_hh20_pct')}%)")
    print(f"  LL20: {lv.get('ll20')}  (proximity: {lv.get('prox_ll20_pct')}%)")

    print("\nMetrics:")
    print(f"  Price: {mt.get('price')} | SMA50: {mt.get('sma50')} | SMA200: {mt.get('sma200')}")
    print(f"  ATR14: {mt.get('atr14')}  (~{mt.get('atr_as_pct')}% of price)")
    if mt.get('ret20_mean_pct') is not None:
        print(f"  20D mean return: {mt.get('ret20_mean_pct')}% | 20D vol: {mt.get('ret20_std_pct')}%")

    rb = summary.get("risk_box", {})
    if rb:
        print("\nIllustrative risk box (not advice):")
        print(f"  Stop ≈ {rb.get('illustrative_stop')} | Target ≈ {rb.get('illustrative_target')} | ATR% ≈ {rb.get('atr_pct')}%")

    print("=" * 80 + "\n")


# --------------------------------- pipeline ----------------------------------
def run_pipeline(
    ticker: str,
    get_company_data_fn,
    *,
    history_pages: int = 3,
    show_plots: bool = True,
    save_dir: Optional[str] = None,
    annotate_plots: bool = True
) -> Dict[str, object]:
    """
    End-to-end pipeline:
      - fetch data
      - compute indicators
      - run analysis
      - generate textual summary + strategies
      - (optionally) plot
      - print narrative and structured reports
    """
    # --- scrape ---
    data = get_company_data_fn(ticker, history_pages=history_pages)

    # --- compute indicators ---
    dfs = prepare_from_data(data)
    df_asc, df_desc = dfs["indicators_asc"], dfs["indicators_desc"]

    # --- analysis ---
    analysis = analyze_all(df_asc)  # may or may not include 'summary'

    # --- textual report (narrative + strategies) ---
    text_report = generate_textual_explanations(ticker, df_asc, analysis)
    print(text_report)

    # --- plots ---
    if show_plots:
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            bb_path   = os.path.join(save_dir, f"{ticker}_bollinger.png")
            rsi_path  = os.path.join(save_dir, f"{ticker}_rsi.png")
            macd_path = os.path.join(save_dir, f"{ticker}_macd.png")
        else:
            bb_path = rsi_path = macd_path = None

        ann = analysis.get("parts", {}) if annotate_plots else {}
        plot_bollinger(df_asc, title=f"{ticker} — Bollinger Bands", save_path=bb_path,   annotate=ann.get("bollinger"))
        plot_rsi(df_asc,       title=f"{ticker} — RSI",              save_path=rsi_path,  annotate=ann.get("rsi"))
        plot_macd(df_asc,      title=f"{ticker} — MACD",             save_path=macd_path, annotate=ann.get("macd"))

    # --- structured console summary (nice table-ish) ---
    pretty_print_analysis(ticker, df_asc, analysis)

    return {"indicators_asc": df_asc, "indicators_desc": df_desc, "analysis": analysis}


# ------------------------------- direct call ---------------------------------
if __name__ == "__main__":
    results = run_pipeline(
        ticker="NVDA",
        get_company_data_fn=get_company_data,
        history_pages=3,
        show_plots=True,        # show plots by default
        save_dir=None,          # set a folder to force saving
        annotate_plots=True,    # annotate plots by default
    )
