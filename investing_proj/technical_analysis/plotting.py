# technical_analysis/plotting.py
"""
Plotting utilities for indicators. Each chart has its own figure.
If no interactive backend is available, figures are saved.

Functions:
    plot_bollinger(df, title=None, save_path=None, annotate=None)
    plot_rsi(df, title=None, save_path=None)
    plot_macd(df, title=None, save_path=None)

`annotate` (optional): pass a dict like analysis["parts"] to enable
last-bar candlestick labels on the Bollinger plot.
"""

from __future__ import annotations
import os
from typing import Optional, Dict

import matplotlib
# Use a GUI backend if available; otherwise Agg will be selected by MPL.
# (Leave backend choice to user/env; don't force 'Qt5Agg' here.)
import matplotlib
matplotlib.use('Qt5Agg')  # or 'Qt5Agg' depending on your system
import matplotlib.pyplot as plt
import pandas as pd


# ------------------------------ helpers --------------------------------------
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


# --- NEW: annotate last-bar candlestick matches on Bollinger chart -----------
def _annotate_last_candle_patterns(ax, df: pd.DataFrame, candles_part: Dict, max_labels: int = 2) -> None:
    """
    Annotate up to `max_labels` candlestick names near the last bar.
    candles_part is typically analysis['parts']['candles'].
    """
    if not candles_part:
        return
    names = candles_part.get("signals", {}).get("matched", []) or []
    if not names:
        return
    x = df.index[-1]
    y = float(df["Close"].iloc[-1])
    label = ", ".join(names[:max_labels])
    ax.text(x, y, f" {label}", va="center", ha="left", fontsize=9)


# ------------------------------- plots ---------------------------------------
def plot_bollinger(
    df: pd.DataFrame,
    title: Optional[str] = None,
    save_path: Optional[str] = None,
    annotate: Optional[Dict] = None
):
    """
    Plot Close with Bollinger Bands: BB_UPPER, BB_MIDDLE, BB_LOWER.
    Requires columns: Close, BB_UPPER, BB_MIDDLE, BB_LOWER.
    If `annotate` contains parts['candles'], labels will be drawn for last-bar patterns.
    """
    d = _ensure_datetime_index(df)
    req = {"Close", "BB_UPPER", "BB_MIDDLE", "BB_LOWER"}
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
    plt.legend(["Close", "BB Upper", "BB Middle", "BB Lower"])

    # NEW: draw candle labels if provided via `annotate`
    if annotate and isinstance(annotate, dict):
        candles_part = annotate.get("candles")
        _annotate_last_candle_patterns(plt.gca(), d, candles_part)

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
    req = {"MACD", "MACD_SIGNAL", "MACD_HIST"}
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
    plt.legend(["MACD", "Signal", "Hist"])
    plt.tight_layout()
    _finalize_plot(save_path, default_name="macd.png")
