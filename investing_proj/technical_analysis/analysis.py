# analysis.py
"""
Lightweight logic to interpret indicators and fuse them into one verdict.
Works on the ascending DataFrame produced by indicators.prepare_from_data().
"""

from __future__ import annotations
from typing import Dict, Optional, Tuple
import numpy as np
import pandas as pd

# ----------------------------- helpers --------------------------------------
def _safe_last_two(s: pd.Series) -> tuple[float, float]:
    if len(s) >= 2:
        return float(s.iloc[-2]), float(s.iloc[-1])
    v = float(s.iloc[-1])
    return v, v

def _ensure_volume_features(df: pd.DataFrame) -> None:
    """Add VOL_Z, OBV, OBV_SLOPE if Volume exists (idempotent)."""
    if "Volume" not in df.columns or "VOL_Z" in df.columns:
        return
    v = df["Volume"].astype(float)
    mu = v.rolling(20, min_periods=20).mean()
    sd = v.rolling(20, min_periods=20).std(ddof=0)
    df["VOL_Z"] = (v - mu) / (sd + 1e-9)

    delta = df["Close"].astype(float).diff().fillna(0.0)
    obv = (np.sign(delta).replace({0: np.nan}).fillna(0) * v).cumsum()
    df["OBV"] = obv
    df["OBV_SLOPE"] = df["OBV"].diff(14)

# === NEW: regime, levels, ATR, slopes =======================================
def _ensure_regime_features(df: pd.DataFrame) -> None:
    """
    Adds:
      SMA50, SMA200, ATR14, trend slope (Close EMA20 slope), 20D high/low + proximity,
      daily return stats (20D).
    """
    close = df["Close"].astype(float)
    high  = df["High"].astype(float)
    low   = df["Low"].astype(float)

    if "SMA50" not in df.columns:
        df["SMA50"]  = close.rolling(50, min_periods=50).mean()
    if "SMA200" not in df.columns:
        df["SMA200"] = close.rolling(200, min_periods=200).mean()

    # ATR(14)
    if "ATR14" not in df.columns:
        prev_close = close.shift(1)
        tr = pd.concat([
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ], axis=1).max(axis=1)
        df["ATR14"] = tr.rolling(14, min_periods=14).mean()

    # Trend slope via EMA20 derivative (units: price/day)
    if "EMA20" not in df.columns:
        df["EMA20"] = close.ewm(span=20, adjust=False).mean()
    df["EMA20_SLOPE"] = df["EMA20"].diff()

    # 20D breakout levels + proximity
    if "HH20" not in df.columns:
        df["HH20"] = close.rolling(20, min_periods=20).max()
        df["LL20"] = close.rolling(20, min_periods=20).min()
    last_close = float(close.iloc[-1])
    last_hh20  = float(df["HH20"].iloc[-1]) if not np.isnan(df["HH20"].iloc[-1]) else np.nan
    last_ll20  = float(df["LL20"].iloc[-1]) if not np.isnan(df["LL20"].iloc[-1]) else np.nan

    # proximity in %
    df["PROX_HH20_PCT"] = (close / df["HH20"] - 1.0) * 100.0
    df["PROX_LL20_PCT"] = (close / df["LL20"] - 1.0) * 100.0

    # Return stats
    ret = close.pct_change()
    df["RET20_MEAN"] = ret.rolling(20, min_periods=5).mean()
    df["RET20_STD"]  = ret.rolling(20, min_periods=5).std(ddof=0)

# --------------------------- per-indicator -----------------------------------
def analyze_bollinger(df: pd.DataFrame) -> dict:
    req = {"Close","BB_UPPER","BB_MIDDLE","BB_LOWER","BB_BANDWIDTH"}
    if not req.issubset(df.columns):
        return {"name":"bollinger","score":0.0,"short_note":"BB: n/a","note":"Missing columns."}

    # Squeeze via bandwidth percentile over last ~1y
    bw = df["BB_BANDWIDTH"].iloc[-252:] if len(df) >= 20 else df["BB_BANDWIDTH"]
    bw_rank = bw.rank(pct=True).iloc[-1] if bw.notna().any() else np.nan
    squeeze = bool(bw_rank <= 0.15) if np.isfinite(bw_rank) else False

    prev_c, last_c = _safe_last_two(df["Close"])
    mid_prev, mid_last = _safe_last_two(df["BB_MIDDLE"])

    up = float(df["BB_UPPER"].iloc[-1])
    lo = float(df["BB_LOWER"].iloc[-1])

    touch_up = last_c >= up * 0.999
    touch_lo = last_c <= lo * 1.001
    cross_up = prev_c <= mid_prev and last_c > mid_last
    cross_dn = prev_c >= mid_prev and last_c < mid_last

    score = (0.6 if cross_up else 0.0) + (-0.6 if cross_dn else 0.0) + \
            (0.5 if touch_up else 0.0) + (-0.8 if touch_lo else 0.0)

    short = "BB:"
    short += " touchU;" if touch_up else ""
    short += " touchL;" if touch_lo else ""
    short += " cross↑;" if cross_up else ""
    short += " cross↓;" if cross_dn else ""
    short += " squeeze" if squeeze else ""

    note = f"Price={last_c:.2f}, Mid={mid_last:.2f}, Up={up:.2f}, Low={lo:.2f}. " \
           f"{'Low-vol squeeze. ' if squeeze else ''}".strip()

    return {"name":"bollinger","score":score,"short_note":short.strip("; "),
            "note":note, "signals":{"touch_upper":touch_up,"touch_lower":touch_lo,
                                    "cross_up":cross_up,"cross_dn":cross_dn,"squeeze":squeeze}}

def analyze_rsi(df: pd.DataFrame) -> dict:
    if "RSI" not in df.columns:
        return {"name":"rsi","score":0.0,"short_note":"RSI: n/a","note":"Missing RSI."}
    prev, last = _safe_last_two(df["RSI"])
    overbought = last >= 70
    oversold   = last <= 30
    turning_dn = prev >= 70 and last < prev
    turning_up = prev <= 30 and last > prev

    score = (1.0 if oversold else 0.0) + (-1.0 if overbought else 0.0) + \
            (0.6 if turning_up else 0.0) + (-0.6 if turning_dn else 0.0)

    short = f"RSI:{last:.1f}"
    short += " OB" if overbought else ""
    short += " OS" if oversold else ""
    short += " ↑turn" if turning_up else ""
    short += " ↓turn" if turning_dn else ""

    note = f"RSI={last:.1f}. " \
           f"{'Overbought. ' if overbought else ''}" \
           f"{'Oversold. ' if oversold else ''}" \
           f"{'Turning up from OS. ' if turning_up else ''}" \
           f"{'Turning down from OB. ' if turning_dn else ''}".strip()

    return {"name":"rsi","score":score,"short_note":short,"note":note,
            "signals":{"overbought":overbought,"oversold":oversold,
                       "turning_up":turning_up,"turning_dn":turning_dn}}

def analyze_macd(df: pd.DataFrame) -> dict:
    req = {"MACD","MACD_SIGNAL","MACD_HIST"}
    if not req.issubset(df.columns):
        return {"name":"macd","score":0.0,"short_note":"MACD: n/a","note":"Missing MACD."}
    prev_m, m = _safe_last_two(df["MACD"])
    prev_s, s = _safe_last_two(df["MACD_SIGNAL"])
    prev_h, h = _safe_last_two(df["MACD_HIST"])

    bull_x = prev_m <= prev_s and m > s
    bear_x = prev_m >= prev_s and m < s
    hist_up = h > prev_h

    score = (1.0 if bull_x else 0.0) + (-1.0 if bear_x else 0.0) + \
            (0.4 if (h > 0 and hist_up) else 0.0) + (-0.4 if (h < 0 and not hist_up) else 0.0)

    short = "MACD:"
    short += " bullX;" if bull_x else ""
    short += " bearX;" if bear_x else ""
    short += " hist↑" if hist_up else " hist↓"

    note = f"MACD={m:.2f}, Signal={s:.2f}, Hist={h:.2f}. " \
           f"{'Bullish crossover. ' if bull_x else ''}" \
           f"{'Bearish crossover. ' if bear_x else ''}" \
           f"{'Momentum increasing.' if hist_up else 'Momentum fading.'}"

    return {"name":"macd","score":score,"short_note":short.strip('; '),"note":note,
            "signals":{"bull_cross":bull_x,"bear_cross":bear_x,"hist_growing":hist_up}}

def analyze_volume(df: pd.DataFrame) -> dict:
    if "Volume" not in df.columns:
        return {"name":"volume","score":0.0,"short_note":"VOL: n/a","note":"No volume."}
    _ensure_volume_features(df)
    prev_c, last_c = _safe_last_two(df["Close"])
    vz = float(df["VOL_Z"].iloc[-1]) if "VOL_Z" in df.columns else np.nan
    obv_slope = float(df["OBV_SLOPE"].iloc[-1]) if "OBV_SLOPE" in df.columns else 0.0

    price_up = last_c > prev_c
    obv_up = obv_slope > 0

    score = (0.7 if (vz > 1.0 and price_up) else 0.0) + \
            (-0.7 if (vz > 1.0 and not price_up) else 0.0) + \
            (0.3 if obv_up else -0.3)

    short = f"VOL:z={vz:.2f}" if np.isfinite(vz) else "VOL:n/a"
    short += " +OBV" if obv_up else " -OBV"
    note = f"Volume z-score={vz:.2f}. " \
           f"{'OBV rising. ' if obv_up else 'OBV falling. '}" \
           f"{'Unusually high volume. ' if np.isfinite(vz) and vz>2 else ''}" \
           f"{'Unusually low volume. ' if np.isfinite(vz) and vz<-2 else ''}".strip()

    return {"name":"volume","score":score,"short_note":short,"note":note,
            "signals":{"price_up":price_up,"obv_up":obv_up,"vz":vz}}

# === NEW: simple divergence detection (efficient) ============================
def _last_swing(series: pd.Series, lookback: int = 20, mode: str = "high") -> Tuple[int, float]:
    """
    Find index of last swing high/low in last `lookback` bars.
    mode: 'high' -> argmax, 'low' -> argmin.
    Returns (idx, value) in absolute df index coordinates; if NA, returns (-1, nan).
    """
    window = series.iloc[-lookback:]
    if window.empty or window.isna().all():
        return -1, np.nan
    pos = int(window.values.argmax()) if mode == "high" else int(window.values.argmin())
    val = float(window.iloc[pos])
    return (series.index[-lookback + pos], val)

def analyze_divergence(df: pd.DataFrame) -> dict:
    """
    Basic divergence: compare direction of price vs RSI/MACD around recent swings.
    - Bearish div: price higher high, oscillator lower high.
    - Bullish div: price lower low, oscillator higher low.
    """
    if "RSI" not in df.columns or "MACD" not in df.columns:
        return {"name":"divergence","note":"Not enough data.","signals":{}}

    # last swing highs/lows over 30 bars
    look = min(len(df), 30)
    if look < 10:
        return {"name":"divergence","note":"Insufficient history.","signals":{}}

    # indices and values
    i_h, p_h = _last_swing(df["Close"], look, "high")
    i_l, p_l = _last_swing(df["Close"], look, "low")

    i_hrsi, rsi_h = _last_swing(df["RSI"], look, "high")
    i_lrsi, rsi_l = _last_swing(df["RSI"], look, "low")

    i_hmacd, m_h = _last_swing(df["MACD"], look, "high")
    i_lmacd, m_l = _last_swing(df["MACD"], look, "low")

    bear_rsi = (i_h != -1 and i_hrsi != -1) and (p_h >= df["Close"].iloc[-1]) and (rsi_h < df["RSI"].iloc[-1])
    bull_rsi = (i_l != -1 and i_lrsi != -1) and (p_l <= df["Close"].iloc[-1]) and (rsi_l > df["RSI"].iloc[-1])

    bear_macd = (i_h != -1 and i_hmacd != -1) and (p_h >= df["Close"].iloc[-1]) and (m_h < df["MACD"].iloc[-1])
    bull_macd = (i_l != -1 and i_lmacd != -1) and (p_l <= df["Close"].iloc[-1]) and (m_l > df["MACD"].iloc[-1])

    note = []
    if bear_rsi or bear_macd: note.append("Potential bearish divergence.")
    if bull_rsi or bull_macd: note.append("Potential bullish divergence.")
    if not note: note.append("No clear divergence.")

    return {"name":"divergence","note":" ".join(note),
            "signals":{"bear_rsi":bear_rsi,"bull_rsi":bull_rsi,"bear_macd":bear_macd,"bull_macd":bull_macd}}

# ------------------------------ fusion ---------------------------------------
def combine(parts: Dict[str, dict], weights: Optional[dict] = None) -> dict:
    """
    Fuse scores into a single verdict.
    Default weights: BB/MACD (trend) > RSI (momentum) > Volume (confirmation).
    """
    if weights is None:
        weights = {"bollinger":0.30, "macd":0.30, "rsi":0.25, "volume":0.15}

    total, wsum = 0.0, 0.0
    reasons = []
    for k, res in parts.items():
        w = float(weights.get(k, 0.0))
        s = float(res.get("score", 0.0))
        total += w * s
        wsum  += w
        reasons.append(f"{k}:{s:+.2f}(w={w:.2f})")

    score = total / max(wsum, 1e-9)
    verdict = "bullish" if score >= 0.30 else ("bearish" if score <= -0.30 else "neutral")
    sign = np.sign(score)
    agree = sum(1 for r in parts.values() if np.sign(r.get("score",0.0)) == sign and r.get("score",0.0) != 0)
    confidence = agree / max(len(parts), 1)

    return {"score":score, "verdict":verdict, "confidence":float(confidence), "reasons":"; ".join(reasons)}

# === NEW: headline + rich summary ===========================================
def _headline(verdict: str, score: float, regime: dict) -> str:
    """Compose a short headline string."""
    bias = []
    if regime.get("above_200dma"): bias.append("above 200DMA")
    else: bias.append("below 200DMA")
    if regime.get("above_50dma"): bias.append("above 50DMA")
    slope = regime.get("ema20_slope", 0.0)
    if slope > 0: bias.append("short-term slope ↑")
    elif slope < 0: bias.append("short-term slope ↓")
    return f"{verdict.upper()} (score {score:+.2f}) — " + ", ".join(bias)

def _format_pct(x: float) -> str:
    try:
        return f"{x*100:.2f}%"
    except Exception:
        return "n/a"

def build_rich_summary(df: pd.DataFrame, parts: Dict[str,dict], combined: dict) -> dict:
    """
    Build a human-friendly pack:
      - headline
      - bullets (key reasons)
      - levels (20D high/low proximity)
      - metrics (ATR%, ret mean/std)
      - risk box (non-advice): example stop/target bands via ATR
    """
    _ensure_regime_features(df)
    last = df.iloc[-1]
    close = float(last["Close"])
    sma50  = float(last.get("SMA50", np.nan))
    sma200 = float(last.get("SMA200", np.nan))
    atr    = float(last.get("ATR14", np.nan))
    ema20_slope = float(last.get("EMA20_SLOPE", 0.0))

    above_50 = np.isfinite(sma50)  and close >= sma50
    above_200= np.isfinite(sma200) and close >= sma200
    atr_pct  = atr / close if (np.isfinite(atr) and atr>0) else np.nan

    hh20 = float(last.get("HH20", np.nan))
    ll20 = float(last.get("LL20", np.nan))
    prox_hh = float(last.get("PROX_HH20_PCT", np.nan))
    prox_ll = float(last.get("PROX_LL20_PCT", np.nan))

    regime = {
        "above_50dma": bool(above_50),
        "above_200dma": bool(above_200),
        "ema20_slope": ema20_slope,
        "atr_pct": atr_pct,
    }

    # bullets from parts
    bullets = []
    bullets.append(parts["bollinger"]["short_note"])
    bullets.append(parts["macd"]["short_note"])
    bullets.append(parts["rsi"]["short_note"])
    if "volume" in parts: bullets.append(parts["volume"]["short_note"])

    # divergences
    div = analyze_divergence(df)
    if "divergence" not in parts:
        parts["divergence"] = div
    if div["signals"]:
        if any(div["signals"].values()):
            bullets.append(div["note"])

    # risk box (illustrative only, not advice)
    risk = {}
    if np.isfinite(atr) and atr > 0:
        risk = {
            "illustrative_stop": round(close - 1.5*atr, 2),
            "illustrative_target": round(close + 2.0*atr, 2),
            "atr_pct": None if np.isnan(atr_pct) else round(atr_pct*100, 2)
        }

    metrics = {
        "price": round(close, 2),
        "sma50": None if not np.isfinite(sma50) else round(sma50, 2),
        "sma200": None if not np.isfinite(sma200) else round(sma200, 2),
        "atr14": None if not np.isfinite(atr) else round(atr, 2),
        "atr_as_pct": None if np.isnan(atr_pct) else round(atr_pct*100, 2),
        "ret20_mean_pct": None if not np.isfinite(float(last.get("RET20_MEAN", np.nan))) else round(float(last["RET20_MEAN"])*100, 2),
        "ret20_std_pct":  None if not np.isfinite(float(last.get("RET20_STD", np.nan)))  else round(float(last["RET20_STD"])*100, 2),
    }

    levels = {
        "hh20": None if not np.isfinite(hh20) else round(hh20, 2),
        "ll20": None if not np.isfinite(ll20) else round(ll20, 2),
        "prox_hh20_pct": None if not np.isfinite(prox_hh) else round(prox_hh, 2),
        "prox_ll20_pct": None if not np.isfinite(prox_ll) else round(prox_ll, 2)
    }

    headline = _headline(combined["verdict"], combined["score"], regime)

    return {
        "headline": headline,
        "bullets": bullets,
        "levels": levels,
        "metrics": metrics,
        "risk_box": risk,
        "regime": regime
    }

# ------------------------------ public API -----------------------------------
def analyze_all(df_asc: pd.DataFrame) -> dict:
    """Run all per-indicator analyses + fusion on the ascending DF, with rich summary."""
    _ensure_regime_features(df_asc)  # ensures ATR/DMAs/levels exist for summary
    bb = analyze_bollinger(df_asc)
    rsi = analyze_rsi(df_asc)
    macd = analyze_macd(df_asc)
    vol = analyze_volume(df_asc)
    parts = {"bollinger": bb, "rsi": rsi, "macd": macd, "volume": vol}
    fused = combine(parts)
    summary = build_rich_summary(df_asc, parts, fused)
    return {"parts": parts, "combined": fused, "summary": summary}
