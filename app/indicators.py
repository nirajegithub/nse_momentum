from __future__ import annotations

import numpy as np
import pandas as pd


def _session_vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    session = pd.Series(df.index.date, index=df.index)
    pv = typical * df["volume"]
    return pv.groupby(session).cumsum() / df["volume"].groupby(session).cumsum().replace(0, np.nan)


def add_indicators(df: pd.DataFrame, rvol_lookback=20) -> pd.DataFrame:
    x = df.copy()
    if x.empty:
        return x
    x = x.sort_index()
    x["ema9"] = x["close"].ewm(span=9, adjust=False).mean()
    x["ema20"] = x["close"].ewm(span=20, adjust=False).mean()
    x["vwap"] = _session_vwap(x)
    delta = x["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    x["rsi14"] = 100 - 100 / (1 + rs)
    prev_close = x["close"].shift(1)
    tr = pd.concat([
        x["high"] - x["low"],
        (x["high"] - prev_close).abs(),
        (x["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    x["atr14"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    x["avg_volume"] = x["volume"].shift(1).rolling(rvol_lookback, min_periods=rvol_lookback).mean()
    x["rvol"] = x["volume"] / x["avg_volume"].replace(0, np.nan)
    return x.dropna(subset=["ema9", "ema20", "vwap", "rsi14", "avg_volume"])
