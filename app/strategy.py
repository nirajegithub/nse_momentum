"""Completed-candle 5M setup and 1M confirmation strategy."""
from __future__ import annotations

import math

from .config import SETTINGS


def _finite(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def evaluate_setup(df5, daily_close, daily_volume, rejection=None):
    """Return a BUY/SELL pending setup for the last completed 5M candle."""
    def reject(reason):
        if rejection is not None:
            rejection.append(reason)
        return None

    if len(df5) < max(2, SETTINGS.rvol_lookback + 1):
        return reject("insufficient completed 5M candles")
    current, previous = df5.iloc[-1], df5.iloc[-2]
    fields = ("ema9", "ema20", "rsi14", "vwap", "volume", "avg_volume", "rvol")
    if not all(_finite(current.get(field)) for field in fields) or not _finite(previous.get("rsi14")):
        return reject("invalid 5M indicators")
    if not (float(daily_close) > SETTINGS.min_price and float(daily_volume) > SETTINGS.min_prev_volume):
        return reject("daily liquidity filter failed")

    bullish = (
        current.ema9 > current.ema20
        and SETTINGS.buy_rsi_min < current.rsi14 < SETTINGS.buy_rsi_max
        and current.rsi14 > previous.rsi14
        and current.close > current.vwap
        and current.close > current.ema20
        and current.rvol >= SETTINGS.min_5m_rvol
    )
    bearish = (
        current.ema9 < current.ema20
        and SETTINGS.sell_rsi_min < current.rsi14 < SETTINGS.sell_rsi_max
        and current.rsi14 < previous.rsi14
        and current.close < current.vwap
        and current.close < current.ema20
        and current.rvol >= SETTINGS.min_5m_rvol
    )
    if SETTINGS.require_ema_crossover:
        bullish = bullish and previous.ema9 <= previous.ema20
        bearish = bearish and previous.ema9 >= previous.ema20
    if not bullish and not bearish:
        return reject("5M quality filters failed")

    direction = "BUY" if bullish else "SELL"
    return {
        "direction": direction,
        "setup_5m_timestamp": df5.index[-1].isoformat(),
        "setup_5m_open": float(current.open), "setup_5m_high": float(current.high),
        "setup_5m_low": float(current.low), "setup_5m_close": float(current.close),
        "setup_5m_ema9": float(current.ema9), "setup_5m_ema20": float(current.ema20),
        "setup_5m_rsi14": float(current.rsi14), "setup_5m_previous_rsi14": float(previous.rsi14),
        "setup_5m_vwap": float(current.vwap), "setup_5m_volume": float(current.volume),
        "setup_5m_avg_volume": float(current.avg_volume), "setup_5m_rvol": float(current.rvol),
        "daily_close": float(daily_close), "daily_volume": float(daily_volume),
        "breakout_level": float(current.high if direction == "BUY" else current.low),
        "stop_loss": float(current.close),
    }


def confirm_setup(setup, candle, confirmation_time):
    """Return entry data only for a later completed 1M close beyond the setup."""
    if confirmation_time <= setup["setup_5m_timestamp"]:
        return None
    close = float(candle.close)
    level = float(setup["breakout_level"])
    direction = setup["direction"]
    if (direction == "BUY" and close > level) or (direction == "SELL" and close < level):
        return {"entry": close, "confirmation_time": confirmation_time, "stop_loss": float(setup["stop_loss"])}
    return None
