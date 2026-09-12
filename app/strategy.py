from __future__ import annotations

import math
import pandas as pd

from .config import SETTINGS
from .scoring import score_trade_quality

IST = "Asia/Kolkata"


def _finite(value) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize(IST)
    return ts.tz_convert(IST)


def evaluate_15m_setup(df15: pd.DataFrame, daily_close: float, daily_volume: float) -> dict | None:
    """Evaluate one completed 15M candle using the final strategy filters."""
    if df15 is None or df15.empty:
        return None
    minimum = max(SETTINGS.min_15m_candles, SETTINGS.rvol_lookback + 1, 2)
    if len(df15) < minimum:
        return None

    required = {"open", "high", "low", "close", "volume", "ema9", "ema20", "rsi14", "vwap"}
    missing = required.difference(df15.columns)
    if missing:
        raise ValueError(f"15M dataframe missing columns: {sorted(missing)}")

    c = df15.iloc[-1]
    p = df15.iloc[-2]
    avg_volume = pd.to_numeric(df15["volume"].iloc[-SETTINGS.rvol_lookback-1:-1], errors="coerce").mean()
    if not _finite(avg_volume) or float(avg_volume) <= 0:
        return None
    rvol = float(c["volume"]) / float(avg_volume)

    values = [c["close"], c["ema9"], c["ema20"], c["rsi14"], c["vwap"], p["rsi14"], daily_close, daily_volume]
    if not all(_finite(v) for v in values):
        return None

    direction = None
    if (
        c["ema9"] > c["ema20"]
        and SETTINGS.buy_rsi_min < c["rsi14"] < SETTINGS.buy_rsi_max
        and c["rsi14"] > p["rsi14"]
        and c["close"] > c["vwap"]
        and c["close"] > c["ema20"]
        and float(daily_close) > SETTINGS.min_price
        and float(daily_volume) > SETTINGS.min_daily_volume
        and rvol >= SETTINGS.min_15m_rvol
    ):
        direction = "BUY"
    elif (
        c["ema9"] < c["ema20"]
        and SETTINGS.sell_rsi_min < c["rsi14"] < SETTINGS.sell_rsi_max
        and c["rsi14"] < p["rsi14"]
        and c["close"] < c["vwap"]
        and c["close"] < c["ema20"]
        and float(daily_close) > SETTINGS.min_price
        and float(daily_volume) > SETTINGS.min_daily_volume
        and rvol >= SETTINGS.min_15m_rvol
    ):
        direction = "SELL"

    if SETTINGS.require_ema_crossover and direction:
        if direction == "BUY" and not (p["ema9"] <= p["ema20"] and c["ema9"] > c["ema20"]):
            direction = None
        elif direction == "SELL" and not (p["ema9"] >= p["ema20"] and c["ema9"] < c["ema20"]):
            direction = None

    if direction is None:
        return None

    quality = score_trade_quality(df15, direction)
    if quality["trade_quality_score"] < SETTINGS.min_trade_score:
        return None

    result = {
        "direction": direction,
        "setup_15m_timestamp": _timestamp(df15.index[-1]).isoformat(),
        "setup_15m_open": float(c["open"]),
        "setup_15m_high": float(c["high"]),
        "setup_15m_low": float(c["low"]),
        "setup_15m_close": float(c["close"]),
        "setup_15m_ema9": float(c["ema9"]),
        "setup_15m_ema20": float(c["ema20"]),
        "setup_15m_rsi14": float(c["rsi14"]),
        "setup_15m_previous_rsi14": float(p["rsi14"]),
        "setup_15m_vwap": float(c["vwap"]),
        "setup_15m_volume": float(c["volume"]),
        "setup_15m_avg_volume": float(avg_volume),
        "setup_15m_rvol": float(rvol),
        "daily_close": float(daily_close),
        "daily_volume": float(daily_volume),
        "breakout_level": float(c["high"] if direction == "BUY" else c["low"]),
        "stop_loss": float(c["close"]),
        **quality,
        "status": "PENDING",
    }
    return result


def confirm_5m_breakout(pending_setup: dict, candle_timestamp, candle_close: float) -> bool:
    if not pending_setup or not _finite(candle_close):
        return False
    ts = _timestamp(candle_timestamp)
    setup_ts = _timestamp(pending_setup["setup_15m_timestamp"])
    if ts <= setup_ts:
        return False
    close = float(candle_close)
    if pending_setup["direction"] == "BUY":
        return close > float(pending_setup["setup_15m_high"])
    if pending_setup["direction"] == "SELL":
        return close < float(pending_setup["setup_15m_low"])
    return False


# Compatibility alias for any existing imports.
def evaluate(*args, **kwargs):
    if args:
        df15 = args[0]
        daily_close = kwargs.get("daily_close")
        daily_volume = kwargs.get("daily_volume")
        if daily_close is not None and daily_volume is not None:
            return evaluate_15m_setup(df15, daily_close, daily_volume)
    return None
