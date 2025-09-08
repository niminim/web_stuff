# technical_analysis/candlestick_patterns.py
"""
Vectorized candlestick pattern detection for OHLCV data.

Public API
----------
- compute_candle_features(df) -> adds reusable columns (idempotent)
- detect_patterns(df) -> returns dict {pattern_name: pd.Series[bool]}
- analyze_candles(df) -> returns analysis dict like your other indicators:
    {
      "name": "candles",
      "score": float,
      "short_note": str,
      "note": str,
      "signals": {
          "matched": [list of last-bar patterns],
          "bullish": bool,
          "bearish": bool
      }
    }

All functions expect an ascending (oldest→newest) DataFrame with
columns: Date, Open, High, Low, Close, (optional) Volume.
"""

from __future__ import annotations
from typing import Dict, List
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Feature engineering (idempotent)
# ---------------------------------------------------------------------------
def compute_candle_features(df: pd.DataFrame) -> None:
    """
    Adds basic candle geometry columns used by many patterns.
    Safe to call repeatedly (only computes if missing).
    """
    need = ("Open", "High", "Low", "Close")
    for c in need:
        if c not in df.columns:
            raise ValueError(f"DataFrame missing required column: {c}")

    o = df["Open"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    c = df["Close"].astype(float)

    if "BODY" not in df.columns:
        df["BODY"] = (c - o).abs()
    if "RANGE" not in df.columns:
        df["RANGE"] = (h - l).abs()
    if "UP_SHADOW" not in df.columns:
        df["UP_SHADOW"] = (h - np.maximum(o, c)).clip(lower=0.0)
    if "LO_SHADOW" not in df.columns:
        df["LO_SHADOW"] = (np.minimum(o, c) - l).clip(lower=0.0)
    if "IS_BULL" not in df.columns:
        df["IS_BULL"] = (c > o).astype(int)
    if "IS_BEAR" not in df.columns:
        df["IS_BEAR"] = (c < o).astype(int)

    # Relative measures (avoid /0 with small epsilon)
    eps = 1e-12
    if "BODY_PCT_RANGE" not in df.columns:
        df["BODY_PCT_RANGE"] = df["BODY"] / (df["RANGE"] + eps)
    if "UP_PCT_RANGE" not in df.columns:
        df["UP_PCT_RANGE"] = df["UP_SHADOW"] / (df["RANGE"] + eps)
    if "LO_PCT_RANGE" not in df.columns:
        df["LO_PCT_RANGE"] = df["LO_SHADOW"] / (df["RANGE"] + eps)


# ---------------------------------------------------------------------------
# Pattern detectors (fully vectorized)
# Tweak thresholds to taste; defaults are common in practice.
# ---------------------------------------------------------------------------
def detect_patterns(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """
    Return a dict of boolean Series keyed by pattern name.
    Only reads columns; it won't mutate df (besides features if missing).
    """
    compute_candle_features(df)

    o = df["Open"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    c = df["Close"].astype(float)
    body_pct = df["BODY_PCT_RANGE"]
    up_pct   = df["UP_PCT_RANGE"]
    lo_pct   = df["LO_PCT_RANGE"]

    # 1-bar patterns
    doji = (body_pct <= 0.1) & (df["RANGE"] > 0)
    marubozu_bull = (df["IS_BULL"] == 1) & (up_pct <= 0.05) & (lo_pct <= 0.05) & (body_pct >= 0.8)
    marubozu_bear = (df["IS_BEAR"] == 1) & (up_pct <= 0.05) & (lo_pct <= 0.05) & (body_pct >= 0.8)

    hammer = (body_pct <= 0.35) & (lo_pct >= 0.5) & (up_pct <= 0.15)
    shooting_star = (body_pct <= 0.35) & (up_pct >= 0.5) & (lo_pct <= 0.15)

    # 2-bar patterns (use shift)
    o1, c1 = o.shift(1), c.shift(1)
    is_bull1, is_bear1 = (c1 > o1), (c1 < o1)

    bullish_engulf = (is_bear1) & (c > o) & (c >= o1) & (o <= c1)
    bearish_engulf = (is_bull1) & (c < o) & (c <= o1) & (o >= c1)

    piercing = (is_bear1) & (o < c1) & (c < o1) & (c > (o1 + c1) / 2)
    dark_cloud = (is_bull1) & (o > c1) & (c > o1) & (c < (o1 + c1) / 2)

    inside_bar  = (h <= h.shift(1)) & (l >= l.shift(1))
    outside_bar = (h >= h.shift(1)) & (l <= l.shift(1))

    # 3-bar stars
    o2, c2 = o.shift(2), c.shift(2)
    small_mid = (df["BODY_PCT_RANGE"] <= 0.25)
    morning_star = ( (c2 < o2) & small_mid.shift(1) & (c > o) & (c >= (o2 + c2)/2) )
    evening_star = ( (c2 > o2) & small_mid.shift(1) & (c < o) & (c <= (o2 + c2)/2) )

    return {
        "doji": doji.fillna(False),
        "marubozu_bull": marubozu_bull.fillna(False),
        "marubozu_bear": marubozu_bear.fillna(False),
        "hammer": hammer.fillna(False),
        "shooting_star": shooting_star.fillna(False),
        "bullish_engulfing": bullish_engulf.fillna(False),
        "bearish_engulfing": bearish_engulf.fillna(False),
        "piercing": piercing.fillna(False),
        "dark_cloud_cover": dark_cloud.fillna(False),
        "inside_bar": inside_bar.fillna(False),
        "outside_bar": outside_bar.fillna(False),
        "morning_star": morning_star.fillna(False),
        "evening_star": evening_star.fillna(False),
    }


# ---------------------------------------------------------------------------
# Scoring & summary (compatible with your 'parts' contract)
# ---------------------------------------------------------------------------
_PATTERN_WEIGHTS: Dict[str, float] = {
    # Bullish
    "hammer": +0.9,
    "bullish_engulfing": +1.0,
    "piercing": +0.6,
    "morning_star": +1.0,
    "marubozu_bull": +0.7,

    # Bearish
    "shooting_star": -0.9,
    "bearish_engulfing": -1.0,
    "dark_cloud_cover": -0.6,
    "evening_star": -1.0,
    "marubozu_bear": -0.7,

    # Contextual (neutral/indecision)
    "doji": 0.0,
    "inside_bar": 0.0,
    "outside_bar": 0.0,
}


def analyze_candles(df: pd.DataFrame, lookback: int = 8) -> Dict[str, object]:
    """
    Inspect the last `lookback` bars for patterns, combine into a score.
    Adds light volume confirmation if 'VOL_Z' exists (>1 boosts, <-1 tempers).
    """
    pats = detect_patterns(df)
    end_slice = {k: v.iloc[-lookback:] for k, v in pats.items()}

    matched: List[str] = [name for name, s in end_slice.items() if bool(s.iloc[-1])]
    # Also include any strong signals inside the lookback (limit to avoid verbosity)
    recent_hits: List[str] = []
    for name, s in end_slice.items():
        if s.any():
            recent_hits.append(name)
    recent_hits = sorted(set(recent_hits))[:5]

    # Score: sum weights for patterns on the very last bar; small bonus for consistency
    score = sum(_PATTERN_WEIGHTS.get(m, 0.0) for m in matched)
    score += 0.2 * sum(np.sign(_PATTERN_WEIGHTS.get(n, 0.0)) for n in matched if n in recent_hits)

    # Volume confirmation (if available)
    if "VOL_Z" in df.columns:
        vz = float(df["VOL_Z"].iloc[-1])
        if np.isfinite(vz):
            if vz > 1:
                score *= 1.10  # conviction boost
            elif vz < -1:
                score *= 0.90  # weak participation

    # Bound the score
    score = float(np.clip(score, -2.0, 2.0))

    # Polished one-liners
    bull = [m for m in matched if _PATTERN_WEIGHTS.get(m, 0) > 0]
    bear = [m for m in matched if _PATTERN_WEIGHTS.get(m, 0) < 0]

    # >>> UPDATED short_note: show recent hits when last bar has none <<<
    if matched:
        short = "Candles: " + ", ".join(matched)
    else:
        recent = ", ".join(recent_hits) if recent_hits else ""
        short = "Candles: none" + (f" (recent: {recent})" if recent else "")

    note = "Recent patterns: " + (", ".join(recent_hits) if recent_hits else "none")

    return {
        "name": "candles",
        "score": score,
        "short_note": short,
        "note": note,
        "signals": {
            "matched": matched,
            "bullish": len(bull) > 0 and len(bear) == 0,
            "bearish": len(bear) > 0 and len(bull) == 0,
        },
    }
