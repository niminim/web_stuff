
# indicators.py
"""
Compute technical indicators on OHLC(V) data.

Input:
    - DataFrame with (at least) columns: Date, Open, High, Low, Close
    - Optional: Volume

Key functions:
    - prepare_from_data(data)  -> dict with 'indicators_asc' and 'indicators_desc'
    - compute_indicators(df)   -> adds RSI, BB, MACD, STOCH, CCI (in-place)
"""

from __future__ import annotations
from typing import Dict
import numpy as np
import pandas as pd


# ----------------------------- Core compute ---------------------------------
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

    Required numeric columns: Close, High, Low
    Optional: Volume (not required for these indicators)

    Adds columns:
      RSI, STOCH_%K, STOCH_%D, BB_MIDDLE, BB_UPPER, BB_LOWER, BB_%B, BB_BANDWIDTH,
      MACD, MACD_SIGNAL, MACD_HIST, CCI
    """
    # Ensure numeric
    close = df["Close"].astype(float)
    high  = df["High"].astype(float)
    low   = df["Low"].astype(float)

    # ---- RSI (Wilder) ----
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1/rsi_period, adjust=False, min_periods=rsi_period).mean()
    avg_loss = loss.ewm(alpha=1/rsi_period, adjust=False, min_periods=rsi_period).mean()
    rs = avg_gain / (avg_loss + 1e-12)
    df["RSI"] = 100 - (100 / (1 + rs))

    # ---- Stochastic Oscillator ----
    ll = low.rolling(stoch_period, min_periods=stoch_period).min()
    hh = high.rolling(stoch_period, min_periods=stoch_period).max()
    stoch_k = (close - ll) / (hh - ll) * 100.0
    df["STOCH_%K"] = stoch_k
    df["STOCH_%D"] = stoch_k.rolling(stoch_smooth, min_periods=stoch_smooth).mean()

    # ---- Bollinger Bands ----
    ma  = close.rolling(bb_period, min_periods=bb_period).mean()
    std = close.rolling(bb_period, min_periods=bb_period).std(ddof=0)
    df["BB_MIDDLE"] = ma
    df["BB_UPPER"]  = ma + bb_std * std
    df["BB_LOWER"]  = ma - bb_std * std
    df["BB_%B"]        = (close - df["BB_LOWER"]) / (df["BB_UPPER"] - df["BB_LOWER"])
    df["BB_BANDWIDTH"] = (df["BB_UPPER"] - df["BB_LOWER"]) / (ma + 1e-12)

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
    df["CCI"] = (tp - sma_tp) / (0.015 * (mad + 1e-12))

    return df


# ------------------------- Pipeline convenience ------------------------------
def prepare_from_data(
    data: Dict[str, object],
    *,
    parse_dates: bool = True
) -> Dict[str, pd.DataFrame]:
    """
    Convert your scraper output to ascending DF, compute indicators,
    and also return a descending copy for display.

    data['historical_data'] example (descending, latest first):
        Date, Open, High, Low, Close, Change, Volume

    Returns:
        {"indicators_asc": df_asc, "indicators_desc": df_desc}
    """
    hist = data.get("historical_data")
    if hist is None or len(hist) == 0:
        raise ValueError("data['historical_data'] is missing or empty")

    df = hist.copy()
    if parse_dates:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")

    # Compute requires ascending chronological order
    df_asc = df.sort_values("Date").reset_index(drop=True)

    needed = {"Close", "High", "Low"}
    missing = needed - set(df_asc.columns)
    if missing:
        raise ValueError(f"historical_data missing columns: {missing}")

    compute_indicators(df_asc)

    df_desc = df_asc.sort_values("Date", ascending=False).reset_index(drop=True)
    return {"indicators_asc": df_asc, "indicators_desc": df_desc}



