# technical_analysis/textual_explanations.py
"""
Create a single, clean technical report that merges:
- Headline
- Key drivers (incl. candles)
- Context (metrics + levels)
- Strategy playbook
- Risk & invalidation
- Optional '(reasons)' footer with weighted parts

Public API:
    generate_textual_explanations(ticker, df_asc, analysis, *, show_reasons=True) -> str
"""

from __future__ import annotations
from typing import Dict, List, Optional
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------
def _fmt(x: Optional[float], nd: int = 2) -> str:
    if x is None:
        return "n/a"
    try:
        xf = float(x)
    except Exception:
        return "n/a"
    if not np.isfinite(xf):
        return "n/a"
    return f"{xf:.{nd}f}"


def _ensure_regime_features(df: pd.DataFrame) -> None:
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
    if "EMA20_SLOPE" not in df.columns:
        df["EMA20_SLOPE"] = df["EMA20"].diff()

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

    # Light stats for context
    if "RET20_MEAN" not in df.columns or "RET20_STD" not in df.columns:
        ret = close.pct_change()
        df["RET20_MEAN"] = ret.rolling(20, min_periods=5).mean()
        df["RET20_STD"]  = ret.rolling(20, min_periods=5).std(ddof=0)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _headline_line(ticker: str, df: pd.DataFrame, combined: Dict) -> str:
    last = df.iloc[-1]
    close = float(last["Close"])
    atr   = float(last.get("ATR14", np.nan))
    atr_pct = (atr / close) * 100 if (np.isfinite(atr) and atr > 0 and close != 0) else np.nan

    sma50  = float(last.get("SMA50", np.nan))
    sma200 = float(last.get("SMA200", np.nan))
    ema20_slope = float(last.get("EMA20_SLOPE", 0.0))

    above_50  = np.isfinite(sma50)  and close >= sma50
    above_200 = np.isfinite(sma200) and close >= sma200

    where = []
    where.append("above 200DMA" if above_200 else "below 200DMA")
    where.append("above 50DMA"  if above_50  else "below 50DMA")
    where.append("short-term slope ↑" if ema20_slope > 0 else "short-term slope ↓" if ema20_slope < 0 else "slope flat")

    verdict = combined.get("verdict", "neutral").upper()
    score   = _fmt(combined.get("score", 0.0), 2)
    conf    = _fmt(combined.get("confidence", 0.0), 2)
    atr_str = f"ATR≈{_fmt(atr)} (~{_fmt(atr_pct, 2)}%)" if np.isfinite(atr_pct) else "ATR≈n/a"

    return f"{ticker} — {verdict} (score {score}, conf {conf}) · {', '.join(where)}, {atr_str}"


def _key_drivers(parts: Dict) -> List[str]:
    bullets: List[str] = []
    for key in ("bollinger", "macd", "rsi", "volume", "candles"):
        if key in parts:
            sn = parts[key].get("short_note")
            if sn:
                # normalize minor naming for consistency
                sn = sn.replace("VOL:", "VOL:").replace("RSI:", "RSI: ").replace("MACD:", "MACD: ")
                bullets.append(sn)
    return bullets


def _context_block(df: pd.DataFrame) -> List[str]:
    last = df.iloc[-1]
    price = _fmt(last["Close"])
    sma50 = _fmt(last.get("SMA50"))
    sma200 = _fmt(last.get("SMA200"))
    atr = _fmt(last.get("ATR14"))
    hh20 = _fmt(last.get("HH20"))
    ll20 = _fmt(last.get("LL20"))
    prox_hh = _fmt(last.get("PROX_HH20_PCT"))
    prox_ll = _fmt(last.get("PROX_LL20_PCT"))
    ret20m = _fmt(last.get("RET20_MEAN") * 100 if pd.notna(last.get("RET20_MEAN")) else None, 2)
    ret20s = _fmt(last.get("RET20_STD") * 100 if pd.notna(last.get("RET20_STD")) else None, 2)

    out = []
    out.append(f"• Price: {price} | SMA50: {sma50} | SMA200: {sma200} | ATR14: {atr}")
    out.append(f"• HH20: {hh20}  (prox: {prox_hh}%) | LL20: {ll20}  (prox: {prox_ll}%)")
    out.append(f"• 20D mean: {ret20m}% | 20D vol: {ret20s}%")
    return out


def _playbook(df: pd.DataFrame, parts: Dict, combined: Dict) -> List[str]:
    last = df.iloc[-1]
    close = float(last["Close"])
    atr   = float(last.get("ATR14", np.nan))
    ema20 = float(last.get("EMA20", np.nan))
    hh20  = float(last.get("HH20", np.nan))
    ll20  = float(last.get("LL20", np.nan))

    atr_ok = np.isfinite(atr) and atr > 0
    verdict = combined.get("verdict", "neutral")

    vol_sig  = parts.get("volume", {}).get("signals", {}) or {}
    vz = vol_sig.get("vz", np.nan)

    msgs: List[str] = []

    if verdict == "bearish":
        msg = "Trend-follow (short): sell rallies toward EMA20/SMA50, or breakdowns below LL20"
        if np.isfinite(vz) and vz > 1:
            msg += " with volume confirmation"
        if atr_ok:
            msg += f"; initial stop ≈ Close + 1.5×ATR ({_fmt(close + 1.5 * atr)}), cover at -1×ATR / -2×ATR"
        msgs.append(msg + ".")
    elif verdict == "bullish":
        msg = "Trend-follow (long): buy pullbacks toward EMA20/SMA50, or breakouts above HH20"
        if np.isfinite(vz) and vz > 1:
            msg += " with volume confirmation"
        if atr_ok:
            msg += f"; initial stop ≈ Close - 1.5×ATR ({_fmt(close - 1.5 * atr)}), scale-out at +1×ATR / +2×ATR"
        msgs.append(msg + ".")

    # Mean reversion (both sides possible)
    bb = parts.get("bollinger", {}).get("signals", {}) or {}
    rsi = parts.get("rsi", {}).get("signals", {}) or {}
    if rsi.get("oversold") or bb.get("touch_lower"):
        msg = "Mean-reversion long (counter-trend): reversal near lower band / RSI<30, target mid/upper band"
        if atr_ok and np.isfinite(ll20):
            msg += f"; stop ≈ swing low or LL20 - 0.5×ATR (~{_fmt(ll20 - 0.5 * atr)})"
        msgs.append(msg + ".")
    if rsi.get("overbought") or bb.get("touch_upper"):
        msg = "Mean-reversion short (counter-trend): fade strength near upper band / RSI>70, target mid/lower band"
        if atr_ok and np.isfinite(hh20):
            msg += f"; stop ≈ swing high or HH20 + 0.5×ATR (~{_fmt(hh20 + 0.5 * atr)})"
        msgs.append(msg + ".")

    # Triggers to watch
    trig = []
    if np.isfinite(hh20):
        trig.append(f"Close > HH20 ({_fmt(hh20)}) → bullish continuation")
    if np.isfinite(ll20):
        trig.append(f"Close < LL20 ({_fmt(ll20)}) → bearish continuation")
    if trig:
        msgs.append("Triggers: " + "; ".join(trig) + ".")

    return msgs


def _risk_notes(df: pd.DataFrame, parts: Dict) -> List[str]:
    last = df.iloc[-1]
    close = float(last["Close"])
    atr   = float(last.get("ATR14", np.nan))
    ema20 = float(last.get("EMA20", np.nan))
    hh20  = float(last.get("HH20", np.nan))
    ll20  = float(last.get("LL20", np.nan))

    notes: List[str] = []
    if np.isfinite(atr) and atr > 0:
        notes.append(f"Use ATR for sizing: ~{_fmt(atr)} (1–2×ATR stops typical)")
    if np.isfinite(ema20):
        notes.append(f"Closing back through EMA20 (~{_fmt(ema20)}) can invalidate short-term momentum")
    if np.isfinite(hh20) and np.isfinite(ll20):
        notes.append(f"Range context: HH20={_fmt(hh20)}, LL20={_fmt(ll20)} → breaks often expand")
    return notes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def generate_textual_explanations(
    ticker: str,
    df_asc: pd.DataFrame,
    analysis: Dict[str, object],
    *,
    show_reasons: bool = True
) -> str:
    """
    Build the final single-block human-readable report.
    """
    _ensure_regime_features(df_asc)

    parts    = analysis.get("parts", {})
    combined = analysis.get("combined", {})

    lines: List[str] = []
    lines.append("=" * 80)
    lines.append(_headline_line(ticker, df_asc, combined))
    lines.append("-" * 80)

    # Key drivers (includes candles one-liner)
    kd = _key_drivers(parts)
    if kd:
        lines.append("Key drivers")
        for b in kd:
            lines.append(f"  • {b}")

    # Context (metrics + levels)
    ctx = _context_block(df_asc)
    if ctx:
        lines.append("\nContext")
        lines.extend(ctx)

    # Strategy playbook
    play = _playbook(df_asc, parts, combined)
    if play:
        lines.append("\nStrategy playbook (educational, not advice)")
        for s in play:
            lines.append(f"  • {s}")

    # Risk & invalidation
    risk = _risk_notes(df_asc, parts)
    if risk:
        lines.append("\nRisk & invalidation")
        for r in risk:
            lines.append(f"  • {r}")

    # Optional weighted reasons footer (compact, not repeated elsewhere)
    if show_reasons and combined.get("reasons"):
        lines.append(f"\n(reasons) {combined['reasons']}")

    lines.append("=" * 80)
    return "\n".join(lines)
