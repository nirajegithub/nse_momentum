from __future__ import annotations
import numpy as np
import pandas as pd

def add_indicators(df: pd.DataFrame, rvol_lookback=20) -> pd.DataFrame:
    x = df.copy()
    if x.empty: return x
    x["ema9"] = x["close"].ewm(span=9, adjust=False).mean()
    x["ema20"] = x["close"].ewm(span=20, adjust=False).mean()
    typical = (x["high"] + x["low"] + x["close"]) / 3
    x["vwap"] = (typical * x["volume"]).cumsum() / x["volume"].cumsum().replace(0, np.nan)
    delta = x["close"].diff()
    gain = delta.clip(lower=0); loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    x["rsi14"] = 100 - 100 / (1 + rs)
    x["rsi_ema9"] = x["rsi14"].ewm(span=9, adjust=False).mean()
    prev = x["close"].shift(1)
    tr = pd.concat([x["high"]-x["low"], (x["high"]-prev).abs(), (x["low"]-prev).abs()], axis=1).max(axis=1)
    x["atr14"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    x["avg_volume"] = x["volume"].shift(1).rolling(rvol_lookback, min_periods=5).mean()
    x["rvol"] = x["volume"] / x["avg_volume"].replace(0, np.nan)
    x["ema20_slope"] = x["ema20"].diff(3)
    return x.dropna(subset=["ema9","ema20","vwap","rsi14","rsi_ema9","atr14"])
