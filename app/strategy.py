from __future__ import annotations
from .config import SETTINGS

def structure(df, lookback=8):
    if len(df) < lookback * 2 + 1: return "NEUTRAL"
    a = df.iloc[-lookback:]; b = df.iloc[-2*lookback:-lookback]
    if a.high.max() > b.high.max() and a.low.min() > b.low.min(): return "BULLISH_HH_HL"
    if a.high.max() < b.high.max() and a.low.min() < b.low.min(): return "BEARISH_LH_LL"
    return "NEUTRAL"

def regime(df):
    c = df.iloc[-1]; st = structure(df)
    buy = c.close > c.vwap and c.ema9 > c.ema20 and c.ema20_slope > 0 and c.rsi14 > 55 and c.rsi14 > c.rsi_ema9 and st == "BULLISH_HH_HL"
    sell = c.close < c.vwap and c.ema9 < c.ema20 and c.ema20_slope < 0 and c.rsi14 < 45 and c.rsi14 < c.rsi_ema9 and st == "BEARISH_LH_LL"
    return {"direction": "BUY" if buy else "SELL" if sell else "NEUTRAL", "structure": st,
            "price_above_vwap": bool(c.close > c.vwap), "ema9_gt_ema20": bool(c.ema9 > c.ema20),
            "ema20_rising": bool(c.ema20_slope > 0), "rsi_ok": bool((c.rsi14 > 55 and c.rsi14 > c.rsi_ema9) if buy else (c.rsi14 < 45 and c.rsi14 < c.rsi_ema9) if sell else False),
            "structure_ok": buy or sell}

def breakout(df, lookback=20):
    if len(df) < lookback + 1: return None
    c = df.iloc[-1]; p = df.iloc[-lookback-1:-1]
    if c.close > p.high.max(): return "BUY"
    if c.close < p.low.min(): return "SELL"
    return None

def evaluate(df5, df15):
    if len(df5) < SETTINGS.min_5m_candles or len(df15) < SETTINGS.min_15m_candles: return None
    r15 = regime(df15)
    if r15["direction"] == "NEUTRAL": return None
    c = df5.iloc[-1]; direction = r15["direction"]
    ema_ok = c.ema9 > c.ema20 if direction == "BUY" else c.ema9 < c.ema20
    vwap_ok = c.close > c.vwap if direction == "BUY" else c.close < c.vwap
    rsi_ok = c.rsi14 > 55 and c.rsi14 > c.rsi_ema9 if direction == "BUY" else c.rsi14 < 45 and c.rsi14 < c.rsi_ema9
    bo = breakout(df5)
    setup = "BREAKOUT" if bo == direction else "CONTINUATION" if ema_ok and vwap_ok and rsi_ok else None
    if not setup: return None
    atr = float(c.atr14); swing = float(df5.low.tail(8).min()) if direction == "BUY" else float(df5.high.tail(8).max())
    sl = swing - SETTINGS.atr_buffer*atr if direction == "BUY" else swing + SETTINGS.atr_buffer*atr
    risk = float(c.close-sl) if direction == "BUY" else float(sl-c.close)
    if risk <= 0 or risk > SETTINGS.max_stop_atr*atr: return None
    return {"direction": direction, "setup": setup, "candle_time": df5.index[-1].isoformat(), "signal_price": float(c.close),
            "rvol": float(c.rvol) if c.rvol == c.rvol else 0.0, "rsi": float(c.rsi14), "ema_ok": bool(ema_ok), "vwap_ok": bool(vwap_ok), "rsi_ok": bool(rsi_ok),
            "regime": r15, "risk": {"entry": float(c.close), "sl": sl, "risk": risk,
            "t1": float(c.close + 1.5*risk) if direction == "BUY" else float(c.close - 1.5*risk),
            "t2": float(c.close + 2.5*risk) if direction == "BUY" else float(c.close - 2.5*risk),
            "t3": float(c.close + 3.5*risk) if direction == "BUY" else float(c.close - 3.5*risk)}}
