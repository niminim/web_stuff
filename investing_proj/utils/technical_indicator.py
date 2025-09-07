"""
Indicators + Plotting (GUI-safe)
--------------------------------

Usage with your scraper:

    from scrape_fin_data.stockanalysis_data import get_company_data
    from technical_indicator import analyze_ticker_indicators

    results = analyze_ticker_indicators(
        ticker="MSFT",
        get_company_data_fn=get_company_data,
        history_pages=3,
        show_plots=True,                # if no GUI, images will be saved
    )

    df_asc  = results["indicators_asc"]   # math-correct (oldest -> newest)
    df_desc = results["indicators_desc"]  # display (latest first)
"""

from __future__ import annotations
import os
from typing import Callable, Dict, Optional

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use('Qt5Agg')  # or 'Qt5Agg' depending on your system
import matplotlib.pyplot as plt


# =============================================================================
# Core indicator computations (vectorized; no external deps)
# =============================================================================
def compute_indicators(
    df: pd.DataFrame,
    *,
    rsi_period: int = 14,
    stoch_period: int = 14, stoch_smooth: int = 3,
    bb_period: int = 20, bb_std: float = 2.0,
    macd_fast: int = 12, macd_slow: int = 26, macd_signal: int = 9,
    cci_period: int = 20
) -> pd.DataFrame:
    """
    Append indicators to an OHLC DataFrame. Operates IN-PLACE and returns df.

    Requires numeric columns: 'Close', 'High', 'Low'
    Adds: RSI, STOCH_%K, STOCH_%D, BB_MIDDLE, BB_UPPER, BB_LOWER,
          BB_%B, BB_BANDWIDTH, MACD, MACD_SIGNAL, MACD_HIST, CCI
    """
    close = df["Close"].astype(float)
    high  = df["High"].astype(float)
    low   = df["Low"].astype(float)

    # ---- RSI (Wilder's smoothing) ----
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1/rsi_period, adjust=False, min_periods=rsi_period).mean()
    avg_loss = loss.ewm(alpha=1/rsi_period, adjust=False, min_periods=rsi_period).mean()
    rs = avg_gain / avg_loss
    df["RSI"] = 100 - (100 / (1 + rs))

    # ---- Stochastic Oscillator ----
    lowest_low   = low.rolling(stoch_period,  min_periods=stoch_period).min()
    highest_high = high.rolling(stoch_period, min_periods=stoch_period).max()
    stoch_k = (close - lowest_low) / (highest_high - lowest_low) * 100
    df["STOCH_%K"] = stoch_k
    df["STOCH_%D"] = stoch_k.rolling(stoch_smooth, min_periods=stoch_smooth).mean()

    # ---- Bollinger Bands ----
    ma  = close.rolling(bb_period, min_periods=bb_period).mean()
    std = close.rolling(bb_period, min_periods=bb_period).std(ddof=0)
    df["BB_MIDDLE"] = ma
    df["BB_UPPER"]  = ma + bb_std * std
    df["BB_LOWER"]  = ma - bb_std * std
    df["BB_%B"]        = (close - df["BB_LOWER"]) / (df["BB_UPPER"] - df["BB_LOWER"])
    df["BB_BANDWIDTH"] = (df["BB_UPPER"] - df["BB_LOWER"]) / ma

    # ---- MACD (12,26,9) ----
    ema_fast = close.ewm(span=macd_fast, adjust=False).mean()
    ema_slow = close.ewm(span=macd_slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    df["MACD"]        = macd_line
    df["MACD_SIGNAL"] = macd_line.ewm(span=macd_signal, adjust=False).mean()
    df["MACD_HIST"]   = df["MACD"] - df["MACD_SIGNAL"]

    # ---- CCI ----
    tp = (high + low + close) / 3.0
    sma_tp = tp.rolling(cci_period, min_periods=cci_period).mean()
    mad = (tp - sma_tp).abs().rolling(cci_period, min_periods=cci_period).mean()
    df["CCI"] = (tp - sma_tp) / (0.015 * mad)

    return df


# =============================================================================
# Convenience wrapper: take your 'data' dict and compute indicators
# =============================================================================
def add_indicators_from_data(
    data: Dict[str, object],
    *,
    return_desc: bool = True
) -> Dict[str, pd.DataFrame] | pd.DataFrame:
    """
    Compute indicators from data['historical_data'] (your scraper output).

    - Ensures chronological ascending order (oldest → newest) for correct math.
    - Optionally returns a descending copy (latest at top) for display.

    Returns:
        If return_desc=True:
            {"indicators_asc": df_asc, "indicators_desc": df_desc}
        else:
            df_asc
    """
    hist = data.get("historical_data")
    if hist is None or len(hist) == 0:
        raise ValueError("data['historical_data'] is missing or empty")

    df_asc = hist.sort_values("Date").reset_index(drop=True).copy()

    needed = {"Close", "High", "Low"}
    missing = needed - set(df_asc.columns)
    if missing:
        raise ValueError(f"historical_data missing columns: {missing}")

    compute_indicators(df_asc)

    if return_desc:
        df_desc = df_asc.sort_values("Date", ascending=False).reset_index(drop=True)
        return {"indicators_asc": df_asc, "indicators_desc": df_desc}
    else:
        return df_asc


# =============================================================================
# Plotting (each chart its own figure; backend-safe)
# =============================================================================
def _ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "Date" in out.columns:
        out = out.sort_values("Date").set_index("Date")
    return out

def _is_interactive_backend() -> bool:
    b = plt.get_backend().lower()
    return any(k in b for k in ("qt", "tk", "wx", "gtk", "macosx"))

def _finalize_plot(save_path: Optional[str], default_name: str) -> None:
    """
    Show when interactive; otherwise save to disk.
    If save_path is None and backend is non-interactive, save under ./plots/.
    """
    if save_path:
        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        plt.savefig(save_path, dpi=120, bbox_inches="tight")
    elif _is_interactive_backend():
        plt.show()
    else:
        os.makedirs("plots", exist_ok=True)
        out = os.path.join("plots", default_name)
        plt.savefig(out, dpi=120, bbox_inches="tight")
        print(f"[saved] {out}")

def plot_price_bollinger(df: pd.DataFrame, title: Optional[str] = None, save_path: Optional[str] = None):
    """
    Plot Close with Bollinger Bands: BB_UPPER, BB_MIDDLE, BB_LOWER.
    Requires columns: Close, BB_UPPER, BB_MIDDLE, BB_LOWER.
    """
    d = _ensure_datetime_index(df)
    req = {"Close","BB_UPPER","BB_MIDDLE","BB_LOWER"}
    missing = req - set(d.columns)
    if missing:
        raise ValueError(f"Missing columns for Bollinger plot: {missing}")

    plt.figure(figsize=(11, 5))
    d["Close"].plot()
    d["BB_UPPER"].plot()
    d["BB_MIDDLE"].plot()
    d["BB_LOWER"].plot()
    plt.title(title or "Price with Bollinger Bands")
    plt.xlabel("Date")
    plt.ylabel("Price")
    plt.legend(["Close","BB Upper","BB Middle","BB Lower"])
    plt.tight_layout()
    _finalize_plot(save_path, default_name="bollinger.png")

def plot_rsi(df: pd.DataFrame, title: Optional[str] = None, save_path: Optional[str] = None):
    """
    Plot RSI with 30/70 reference lines. Requires column: RSI.
    """
    d = _ensure_datetime_index(df)
    if "RSI" not in d.columns:
        raise ValueError("Missing column 'RSI' for RSI plot.")

    plt.figure(figsize=(11, 3.2))
    d["RSI"].plot()
    plt.axhline(70, linestyle="--")
    plt.axhline(30, linestyle="--")
    plt.title(title or "RSI (14)")
    plt.xlabel("Date")
    plt.ylabel("RSI")
    plt.tight_layout()
    _finalize_plot(save_path, default_name="rsi.png")

def plot_macd(df: pd.DataFrame, title: Optional[str] = None, save_path: Optional[str] = None):
    """
    Plot MACD line, signal line, and histogram.
    Requires columns: MACD, MACD_SIGNAL, MACD_HIST.
    """
    d = _ensure_datetime_index(df)
    req = {"MACD","MACD_SIGNAL","MACD_HIST"}
    missing = req - set(d.columns)
    if missing:
        raise ValueError(f"Missing columns for MACD plot: {missing}")

    plt.figure(figsize=(11, 4))
    d["MACD"].plot()
    d["MACD_SIGNAL"].plot()
    plt.bar(d.index, d["MACD_HIST"], width=1.0)
    plt.title(title or "MACD (12,26,9)")
    plt.xlabel("Date")
    plt.ylabel("MACD")
    plt.legend(["MACD","Signal","Hist"])
    plt.tight_layout()
    _finalize_plot(save_path, default_name="macd.png")


# =============================================================================
# End–to–end convenience: scrape (via your function) → compute → plot
# =============================================================================
def analyze_ticker_indicators(
    ticker: str,
    get_company_data_fn: Callable[..., Dict[str, object]],
    *,
    history_pages: int = 3,
    show_plots: bool = True,
    save_dir: Optional[str] = None
) -> Dict[str, pd.DataFrame]:
    """
    1) calls your scraper (get_company_data_fn) for `ticker`
    2) computes indicators on data['historical_data']
    3) optionally shows/saves plots (backend-safe)
    4) returns both ascending and descending DataFrames

    save_dir:
        If provided, figures are saved there, e.g., f"{save_dir}/{ticker}_rsi.png"
        Overrides default ./plots/ path for headless runs.
    """
    # --- scrape ---
    data = get_company_data_fn(ticker, history_pages=history_pages)

    # --- compute ---
    out = add_indicators_from_data(data, return_desc=True)
    df_asc  = out["indicators_asc"]
    df_desc = out["indicators_desc"]

    # --- plot ---
    if show_plots:
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
            bb_path  = os.path.join(save_dir, f"{ticker}_bollinger.png")
            rsi_path = os.path.join(save_dir, f"{ticker}_rsi.png")
            macd_path= os.path.join(save_dir, f"{ticker}_macd.png")
        else:
            bb_path = rsi_path = macd_path = None

        plot_price_bollinger(df_asc, title=f"{ticker} — Bollinger Bands", save_path=bb_path)
        plot_rsi(df_asc,             title=f"{ticker} — RSI",              save_path=rsi_path)
        plot_macd(df_asc,            title=f"{ticker} — MACD",             save_path=macd_path)

    return out


# =============================================================================
# Example (uncomment & adapt)
# =============================================================================
if __name__ == "__main__":
    import os, sys
    project_root = "/home/nim/venv/web_stuff/investing_proj"
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from scrape_fin_data.stockanalysis_data import get_company_data

    results = analyze_ticker_indicators(
        ticker="NVDA",
        get_company_data_fn=get_company_data,
        history_pages=3,
        show_plots=True,          # if no GUI, images saved to ./plots/
        save_dir=None,            # or set a custom folder path
    )
    print(results["indicators_desc"].head())
