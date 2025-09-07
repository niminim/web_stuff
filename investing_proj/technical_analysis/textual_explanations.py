# technical_analysis/textual_explanations.py
"""
Create human-readable summaries and strategy suggestions
from (df_asc, analysis) produced by your pipeline.

Public API:
    generate_textual_explanations(ticker, df_asc, analysis) -> str
"""

from __future__ import annotations
from typing import Dict, List, Optional
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------
def _fmt(x: Optional[float], nd: int = 2) -> str:
    """Format a numeric value or None/NaN safely."""
    if x is None:
        return "n/a"
    try:
        xf = float(x)
    except Exception:
        return "n/a"
    if not np.isfinite(xf):
        return "n/a"
    return f"{xf:.{nd}f}"


def _pct(x: Optional[float], nd: int = 2) -> str:
    """Format as percentage safely."""
    if x is None:
        return "n/a"
    try:
        xf = float(x)
    except Exception:
        return "n/a"
    if not np.isfinite(xf):
        return "n/a"
    return f"{xf * 100:.{nd}f}%"


def _ensure_regime_features(df: pd.DataFrame) -> None:
    """
    Ensure a minimal set of regime/level features exists on df.
    Idempotent; computes only if missing.
    """
    # Basic safety
    for col in ("Close", "High", "Low"):
        if col not in df.columns:
            raise ValueError(f"DataFrame missing required column: {col}")

    close = df["Close"].astype(float)
    high  = df["High"].astype(float)
    low   = df["Low"].astype(float)

    if "SMA50" not in df.columns:
        df["SMA50"]  = close.rolling(50, min_periods=50).mean()
    if "SMA200" not in df.columns:
        df["SMA200"] = close.rolling(200, min_periods=200).mean()

    if "EMA20" not in df.columns:
        df["EMA20"] = close.ewm(span=20, adjust=False).mean()

    if "HH20" not in df.columns:
        df["HH20"] = close.rolling(20, min_periods=20).max()
    if "LL20" not in df.columns:
        df["LL20"] = close.rolling(20, min_periods=20).min()

    if "ATR14" not in df.columns:
        prev_close = close.shift(1)
        tr = pd.concat(
            [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        df["ATR14"] = tr.rolling(14, min_periods=14).mean()

    if "PROX_HH20_PCT" not in df.columns:
        with np.errstate(divide="ignore", invalid="ignore"):
            df["PROX_HH20_PCT"] = (close / df["HH20"] - 1.0) * 100.0
    if "PROX_LL20_PCT" not in df.columns:
        with np.errstate(divide="ignore", invalid="ignore"):
            df["PROX_LL20_PCT"] = (close / df["LL20"] - 1.0) * 100.0

    if "EMA20_SLOPE" not in df.columns:
        df["EMA20_SLOPE"] = df["EMA20"].diff()

    if "RET20_MEAN" not in df.columns or "RET20_STD" not in df.columns:
        ret = close.pct_change()
        df["RET20_MEAN"] = ret.rolling(20, min_periods=5).mean()
        df["RET20_STD"]  = ret.rolling(20, min_periods=5).std(ddof=0)


# ---------------------------------------------------------------------------
# Text builder
# ---------------------------------------------------------------------------
def _compose_overview(ticker: str, df: pd.DataFrame, combined: Dict, parts: Dict) -> List[str]:
    last = df.iloc[-1]
    close = float(last["Close"])
    sma50  = float(last.get("SMA50", np.nan))
    sma200 = float(last.get("SMA200", np.nan))
    ema20_slope = float(last.get("EMA20_SLOPE", 0.0))
    atr = float(last.get("ATR14", np.nan))
    atr_pct = atr / close if (np.isfinite(atr) and atr > 0 and close != 0) else np.nan

    verdict = combined.get("verdict", "neutral")
    score   = float(combined.get("score", 0.0))
    conf    = float(combined.get("confidence", 0.0))

    above_50  = np.isfinite(sma50)  and close >= sma50
    above_200 = np.isfinite(sma200) and close >= sma200

    bullets = []
    headline_bits = [
        f"{ticker} — {verdict.upper()} (score {score:+.2f}, conf {conf:.2f})",
        "above 50DMA" if above_50 else "below 50DMA",
        "above 200DMA" if above_200 else "below 200DMA",
        "short-term slope ↑" if ema20_slope > 0 else ("short-term slope ↓" if ema20_slope < 0 else "slope flat"),
        f"ATR≈{_fmt(atr)} (~{_fmt(atr_pct * 100) if np.isfinite(atr_pct) else 'n/a'}% of price)",
    ]
    bullets.append(", ".join(headline_bits))

    # Add key indicator one-liners
    for key in ("bollinger", "macd", "rsi", "volume"):
        if key in parts and parts[key].get("short_note"):
            bullets.append(parts[key]["short_note"])

    return bullets


def _strategy_playbook(df: pd.DataFrame, parts: Dict, combined: Dict) -> List[str]:
    """
    Generate actionable, conditional strategies based on regime + signals.
    Non-advice; for educational/backtesting purposes.
    """
    last = df.iloc[-1]
    close = float(last["Close"])
    sma50  = float(last.get("SMA50", np.nan))
    sma200 = float(last.get("SMA200", np.nan))
    ema20  = float(last.get("EMA20", np.nan))
    atr    = float(last.get("ATR14", np.nan))
    hh20   = float(last.get("HH20", np.nan))
    ll20   = float(last.get("LL20", np.nan))

    above_50  = np.isfinite(sma50)  and close >= sma50
    above_200 = np.isfinite(sma200) and close >= sma200
    atr_ok    = np.isfinite(atr) and atr > 0

    bb       = parts.get("bollinger", {}).get("signals", {}) or {}
    rsi_sig  = parts.get("rsi", {}).get("signals", {}) or {}
    macd_sig = parts.get("macd", {}).get("signals", {}) or {}
    vol_sig  = parts.get("volume", {}).get("signals", {}) or {}

    verdict = combined.get("verdict", "neutral")

    out: List[str] = []

    # 1) Trend-following (LONG)
    if verdict == "bullish" and above_200:
        msg = "Trend-follow (long): consider buying pullbacks toward EMA20/SMA50, or breakouts above HH20"
        vz = vol_sig.get("vz", np.nan)
        if np.isfinite(vz) and vz > 1:
            msg += " with volume confirmation"
        if atr_ok:
            msg += f"; initial stop ≈ Close - 1.5×ATR ({_fmt(close - 1.5 * atr)}), scale-out at +1×ATR / +2×ATR"
        out.append(msg + ".")

    # 2) Trend-following (SHORT)
    if verdict == "bearish" and not above_200:
        msg = "Trend-follow (short): consider selling rallies toward EMA20/SMA50, or breakdowns below LL20"
        vz = vol_sig.get("vz", np.nan)
        if np.isfinite(vz) and vz > 1:
            msg += " with volume confirmation"
        if atr_ok:
            msg += f"; initial stop ≈ Close + 1.5×ATR ({_fmt(close + 1.5 * atr)}), cover at -1×ATR / -2×ATR"
        out.append(msg + ".")

    # 3) Breakout / Squeeze
    if bb.get("squeeze", False):
        msg = "Squeeze breakout: set brackets above HH20 and below LL20; enter on close outside bands"
        if atr_ok:
            msg += f"; protective stop ≈ middle band/EMA20 (~{_fmt(ema20)}), targets at ±1–2×ATR"
        out.append(msg + ".")

    # 4) Mean-reversion LONG
    if rsi_sig.get("oversold", False) or bb.get("touch_lower", False):
        caution = "" if above_200 else " (counter-trend)"
        msg = f"Mean-reversion long{caution}: look for reversal near lower band / RSI<30, target mid/upper band"
        if atr_ok and np.isfinite(ll20):
            msg += f"; stop ≈ recent swing low or LL20 - 0.5×ATR (~{_fmt(ll20 - 0.5 * atr)})"
        out.append(msg + ".")

    # 5) Mean-reversion SHORT
    if rsi_sig.get("overbought", False) or bb.get("touch_upper", False):
        caution = "" if not above_200 else " (counter-trend)"
        msg = f"Mean-reversion short{caution}: fade strength near upper band / RSI>70, target mid/lower band"
        if atr_ok and np.isfinite(hh20):
            msg += f"; stop ≈ recent swing high or HH20 + 0.5×ATR (~{_fmt(hh20 + 0.5 * atr)})"
        out.append(msg + ".")

    # 6) Trigger-based upgrades/downgrades
    trig = []
    if np.isfinite(hh20):
        trig.append(f"Close > HH20 ({_fmt(hh20)}) → bullish continuation")
    if np.isfinite(ll20):
        trig.append(f"Close < LL20 ({_fmt(ll20)}) → bearish continuation")
    if macd_sig.get("bull_cross", False):
        trig.append("MACD bull crossover → momentum turning up")
    if macd_sig.get("bear_cross", False):
        trig.append("MACD bear crossover → momentum turning down")
    if trig:
        out.append("Triggers to watch: " + "; ".join(trig) + ".")

    return out


def _risk_and_invalidation(df: pd.DataFrame, parts: Dict) -> List[str]:
    last = df.iloc[-1]
    close = float(last["Close"])
    atr   = float(last.get("ATR14", np.nan))
    mid   = float(last.get("EMA20", np.nan))
    ll20  = float(last.get("LL20", np.nan))
    hh20  = float(last.get("HH20", np.nan))

    notes = []
    if np.isfinite(atr) and atr > 0:
        notes.append(f"Use ATR for sizing: 1×ATR ≈ {_fmt(atr)}; consider 1–2×ATR stops depending on setup.")
    if np.isfinite(mid):
        notes.append(f"Closing back through EMA20 (~{_fmt(mid)}) can invalidate short-term momentum.")
    if np.isfinite(ll20) and np.isfinite(hh20):
        notes.append(f"Range context: HH20={_fmt(hh20)}, LL20={_fmt(ll20)}; breaks often lead to expansion.")

    vol = parts.get("volume", {}).get("signals", {}) or {}
    vz = vol.get("vz", None)
    if vz is not None:
        try:
            vzf = float(vz)
            if np.isfinite(vzf):
                if vzf > 2:
                    notes.append("Volume z-score > 2 → elevated conviction on breakouts/breakdowns.")
                elif vzf < -2:
                    notes.append("Volume z-score < -2 → weak participation; be selective.")
        except Exception:
            pass

    return notes


def generate_textual_explanations(ticker: str, df_asc: pd.DataFrame, analysis: Dict[str, object]) -> str:
    """
    Build a final human-readable report from the current analysis.
    Returns a multi-line string suitable for console, logs, or a UI text box.
    """
    # Ensure features (safe, idempotent)
    _ensure_regime_features(df_asc)

    combined = analysis.get("combined", {})
    parts    = analysis.get("parts", {})
    if not combined or not parts:
        return f"{ticker}: No analysis parts available."

    # Compose sections
    overview = _compose_overview(ticker, df_asc, combined, parts)
    playbook = _strategy_playbook(df_asc, parts, combined)
    riskbox  = _risk_and_invalidation(df_asc, parts)

    # Optional extras (if upstream produced a summary)
    extra_levels = {}
    if "summary" in analysis and isinstance(analysis["summary"], dict):
        extra_levels = analysis["summary"].get("levels", {}) or {}

    # Format final text
    lines: List[str] = []
    lines.append("=" * 80)
    lines.append(overview[0])  # headline line
    lines.append("-" * 80)

    if len(overview) > 1:
        lines.append("Key drivers:")
        for b in overview[1:]:
            if b:
                lines.append(f"  • {b}")

    if extra_levels:
        lines.append("\nLevels:")
        lines.append(f"  HH20: {extra_levels.get('hh20')}  (prox: {extra_levels.get('prox_hh20_pct')}%)")
        lines.append(f"  LL20: {extra_levels.get('ll20')}  (prox: {extra_levels.get('prox_ll20_pct')}%)")

    if playbook:
        lines.append("\nStrategy playbook (educational, not advice):")
        for s in playbook:
            if s:
                lines.append(f"  • {s}")

    if riskbox:
        lines.append("\nRisk & invalidation notes:")
        for r in riskbox:
            if r:
                lines.append(f"  • {r}")

    lines.append("=" * 80)
    return "\n".join(lines)
