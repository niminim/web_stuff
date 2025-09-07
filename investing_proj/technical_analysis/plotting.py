# plotting.py
"""
Matplotlib plots for indicators. GUI-safe:
- If interactive backend → show
- Otherwise → save to ./plots/ or a provided folder
"""

from __future__ import annotations
from typing import Optional, Dict
import os
import matplotlib
matplotlib.use("Qt5Agg")  # you can switch if needed
import matplotlib.pyplot as plt
import pandas as pd


def _ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    if "Date" in d.columns:
        d = d.sort_values("Date").set_index("Date")
    return d

def _is_interactive_backend() -> bool:
    b = plt.get_backend().lower()
    return any(k in b for k in ("qt", "tk", "wx", "gtk", "macosx"))

def _finalize_plot(save_path: Optional[str], default_name: str) -> None:
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

def plot_bollinger(df: pd.DataFrame, *, title: str, save_path: Optional[str] = None, annotate: Optional[Dict] = None):
    d = _ensure_datetime_index(df)
    plt.figure(figsize=(11, 5))
    d["Close"].plot()
    d["BB_UPPER"].plot()
    d["BB_MIDDLE"].plot()
    d["BB_LOWER"].plot()
    if annotate and "short_note" in annotate:
        x, y = d.index[-1], d["Close"].iloc[-1]
        plt.annotate(annotate["short_note"], (x, y), xytext=(0, 15),
                     textcoords="offset points", ha="right", fontsize=9,
                     arrowprops=dict(arrowstyle="->", lw=0.8))
    plt.title(title)
    plt.xlabel("Date")
    plt.ylabel("Price")
    plt.legend(["Close","BB Upper","BB Middle","BB Lower"])
    plt.tight_layout()
    _finalize_plot(save_path, default_name="bollinger.png")

def plot_rsi(df: pd.DataFrame, *, title: str, save_path: Optional[str] = None, annotate: Optional[Dict] = None):
    d = _ensure_datetime_index(df)
    plt.figure(figsize=(11, 3.2))
    d["RSI"].plot()
    plt.axhline(70, linestyle="--")
    plt.axhline(30, linestyle="--")
    if annotate and "short_note" in annotate:
        x, y = d.index[-1], d["RSI"].iloc[-1]
        plt.annotate(annotate["short_note"], (x, y), xytext=(0, 12),
                     textcoords="offset points", ha="right", fontsize=9,
                     arrowprops=dict(arrowstyle="->", lw=0.8))
    plt.title(title)
    plt.xlabel("Date")
    plt.ylabel("RSI")
    plt.tight_layout()
    _finalize_plot(save_path, default_name="rsi.png")

def plot_macd(df: pd.DataFrame, *, title: str, save_path: Optional[str] = None, annotate: Optional[Dict] = None):
    d = _ensure_datetime_index(df)
    plt.figure(figsize=(11, 4))
    d["MACD"].plot()
    d["MACD_SIGNAL"].plot()
    plt.bar(d.index, d["MACD_HIST"], width=1.0)
    if annotate and "short_note" in annotate:
        x, y = d.index[-1], d["MACD"].iloc[-1]
        plt.annotate(annotate["short_note"], (x, y), xytext=(0, 12),
                     textcoords="offset points", ha="right", fontsize=9,
                     arrowprops=dict(arrowstyle="->", lw=0.8))
    plt.title(title)
    plt.xlabel("Date")
    plt.ylabel("MACD")
    plt.legend(["MACD","Signal","Hist"])
    plt.tight_layout()
    _finalize_plot(save_path, default_name="macd.png")
