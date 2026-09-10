from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.main import completed_candles
from app.strategy import confirm_setup, evaluate_setup


IST = ZoneInfo("Asia/Kolkata")


def frame(direction="BUY"):
    index = pd.date_range("2026-09-10 09:20", periods=21, freq="5min", tz="Asia/Kolkata")
    buy = direction == "BUY"
    x = pd.DataFrame({
        "open": [500.0] * 21, "high": [520.0] * 21, "low": [510.0] * 21,
        "close": [518.0 if buy else 502.0] * 21, "volume": [200.0] * 20 + [400.0],
        "avg_volume": [200.0] * 21, "rvol": [1.0] * 20 + [2.0],
        "ema9": [510.0 if buy else 490.0] * 21, "ema20": [500.0] * 21,
        "vwap": [510.0 if buy else 510.0] * 21,
        "rsi14": [60.0 if buy else 40.0] * 20 + [65.0 if buy else 35.0],
    }, index=index)
    if not buy:
        x.loc[x.index[-1], ["high", "low", "close"]] = [505.0, 498.0, 502.0]
    return x


def setup(direction="BUY"):
    result = evaluate_setup(frame(direction), 351, 500001)
    assert result
    return result


def candle(close, high=999, low=1):
    return pd.Series({"close": close, "high": high, "low": low})


def test_buy_and_sell_quality_setups_pass_and_stop_is_5m_close():
    for direction in ("BUY", "SELL"):
        result = setup(direction)
        assert result["direction"] == direction
        assert result["stop_loss"] == result["setup_5m_close"]


@pytest.mark.parametrize("field,value", [
    ("ema9", 500), ("rsi14", 55), ("rsi14", 70), ("vwap", 518),
    ("ema20", 518), ("rvol", 1.5),
])
def test_buy_quality_rejects_each_core_filter(field, value):
    x = frame("BUY")
    x.loc[x.index[-1], field] = value
    assert evaluate_setup(x, 351, 500001) is None


def test_buy_rejects_non_rising_rsi_and_daily_boundaries():
    x = frame("BUY"); x.loc[x.index[-2], "rsi14"] = 65
    assert evaluate_setup(x, 351, 500001) is None
    assert evaluate_setup(frame(), 350, 500001) is None
    assert evaluate_setup(frame(), 351, 500000) is None


@pytest.mark.parametrize("field,value", [
    ("ema9", 500), ("rsi14", 45), ("rsi14", 30), ("vwap", 502),
    ("ema20", 502), ("rvol", 1.5),
])
def test_sell_quality_rejects_each_core_filter(field, value):
    x = frame("SELL")
    x.loc[x.index[-1], field] = value
    assert evaluate_setup(x, 351, 500001) is None


def test_sell_rejects_non_falling_rsi():
    x = frame("SELL"); x.loc[x.index[-2], "rsi14"] = 35
    assert evaluate_setup(x, 351, 500001) is None


@pytest.mark.parametrize("close,expected", [(519, False), (520, False), (521, True)])
def test_buy_confirmation_uses_close_not_high(close, expected):
    s = setup("BUY")
    # high crosses the level in every case: only close is decisive.
    got = confirm_setup(s, candle(close, high=530), (pd.Timestamp(s["setup_5m_timestamp"]) + timedelta(minutes=1)).isoformat())
    assert bool(got) is expected


@pytest.mark.parametrize("close,expected", [(499, False), (498, False), (497, True)])
def test_sell_confirmation_uses_close_not_low(close, expected):
    s = setup("SELL")
    got = confirm_setup(s, candle(close, low=490), (pd.Timestamp(s["setup_5m_timestamp"]) + timedelta(minutes=1)).isoformat())
    assert bool(got) is expected


def test_confirmation_must_be_strictly_after_setup_close():
    s = setup()
    assert confirm_setup(s, candle(521), s["setup_5m_timestamp"]) is None


def test_completed_candles_excludes_in_progress_one_minute_candle():
    index = pd.date_range("2026-09-10 10:24", periods=3, freq="min", tz="Asia/Kolkata")
    x = pd.DataFrame({"close": [1, 2, 3]}, index=index)
    now = datetime(2026, 9, 10, 10, 26, 30, tzinfo=IST)
    assert completed_candles(x, now, 1).index.tolist() == list(index[:2])
