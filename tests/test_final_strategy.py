from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app.config import SETTINGS
from app.risk import build_risk_and_targets
from app.scoring import score_trade_quality
from app.strategy import evaluate_15m_setup, confirm_5m_breakout

IST = ZoneInfo("Asia/Kolkata")


def _frame(direction="BUY", rows=35):
    idx = pd.date_range("2026-09-10 09:30", periods=rows, freq="15min", tz=IST)
    bullish = direction == "BUY"
    step = 0.8 if bullish else -0.8
    close = pd.Series([500 + i * step for i in range(rows)], index=idx, dtype=float)
    open_ = close - 0.8 if bullish else close + 0.8
    high = close + 1.2
    low = close - 1.2
    ema20 = close - 1.0 if bullish else close + 1.0
    ema9 = close + 1.0 if bullish else close - 1.0
    rsi = pd.Series([60.0 if bullish else 40.0] * rows, index=idx)
    rsi.iloc[-2] = 58.0 if bullish else 42.0
    volume = pd.Series([1000.0] * rows, index=idx)
    volume.iloc[-1] = 2200.0
    # Make the final setup candle a clear departure candle.
    if bullish:
        open_.iloc[-1] = close.iloc[-1] - 1.5
        high.iloc[-1] = close.iloc[-1] + 0.4
        low.iloc[-1] = open_.iloc[-1] - 0.2
    else:
        open_.iloc[-1] = close.iloc[-1] + 1.5
        high.iloc[-1] = open_.iloc[-1] + 0.2
        low.iloc[-1] = close.iloc[-1] - 0.4
    vwap = close - 1.0 if bullish else close + 1.0
    # Three compact base candles immediately before the setup candle.
    for i in range(rows - 4, rows - 1):
        high.iloc[i] = close.iloc[i] + 0.70
        low.iloc[i] = close.iloc[i] - 0.70
        open_.iloc[i] = close.iloc[i] - 0.30 if bullish else close.iloc[i] + 0.30
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume, "ema9": ema9, "ema20": ema20,
        "rsi14": rsi, "vwap": vwap,
    }, index=idx)


def test_buy_15m_setup_passes():
    result = evaluate_15m_setup(_frame("BUY"), 500, 600_000)
    assert result is not None
    assert result["direction"] == "BUY"
    assert result["trade_quality_score"] >= SETTINGS.min_trade_score
    assert result["stop_loss"] == result["setup_15m_close"]


def test_sell_15m_setup_passes():
    result = evaluate_15m_setup(_frame("SELL"), 500, 600_000)
    assert result is not None
    assert result["direction"] == "SELL"
    assert result["stop_loss"] == result["setup_15m_close"]


def test_buy_rsi_boundary_rejected():
    frame = _frame("BUY")
    frame.iloc[-1, frame.columns.get_loc("rsi14")] = 55
    assert evaluate_15m_setup(frame, 500, 600_000) is None


def test_sell_rsi_boundary_rejected():
    frame = _frame("SELL")
    frame.iloc[-1, frame.columns.get_loc("rsi14")] = 45
    assert evaluate_15m_setup(frame, 500, 600_000) is None


def test_daily_filters_are_strict():
    frame = _frame("BUY")
    assert evaluate_15m_setup(frame, 350, 600_000) is None
    assert evaluate_15m_setup(frame, 500, 500_000) is None


def test_rvol_is_previous_20_candles_only():
    frame = _frame("BUY")
    result = evaluate_15m_setup(frame, 500, 600_000)
    assert result is not None
    assert result["setup_15m_rvol"] > 1.5
    assert result["setup_15m_avg_volume"] == 1000.0


def test_score_range_and_components():
    result = score_trade_quality(_frame("BUY"), "BUY")
    assert 0 <= result["trade_quality_score"] <= 7
    assert result["departure_score"] in {0.0, 1.5, 3.0}
    assert result["freshness_score"] in {0.0, 1.0, 2.0}
    assert result["base_candle_score"] in {0.0, 1.0, 2.0}


def test_buy_confirmation_strictly_above_15m_high():
    pending = {
        "direction": "BUY",
        "setup_15m_timestamp": "2026-09-10T10:00:00+05:30",
        "setup_15m_high": 520.0,
    }
    assert not confirm_5m_breakout(pending, "2026-09-10T10:00:00+05:30", 521)
    assert not confirm_5m_breakout(pending, "2026-09-10T10:05:00+05:30", 520)
    assert confirm_5m_breakout(pending, "2026-09-10T10:05:00+05:30", 521)


def test_sell_confirmation_strictly_below_15m_low():
    pending = {
        "direction": "SELL",
        "setup_15m_timestamp": "2026-09-10T10:00:00+05:30",
        "setup_15m_low": 500.0,
    }
    assert not confirm_5m_breakout(pending, "2026-09-10T10:05:00+05:30", 500)
    assert confirm_5m_breakout(pending, "2026-09-10T10:05:00+05:30", 499)


def test_small_stop_examples_are_rejected():
    examples = [
        ("SELL", 1037.00, 1037.90),
        ("BUY", 1259.90, 1259.30),
        ("SELL", 1038.40, 1038.80),
        ("BUY", 457.90, 457.00),
    ]
    for direction, entry, sl in examples:
        risk, reason = build_risk_and_targets(direction, entry, sl, 0.50, 2, 3, 4, 2)
        assert risk is None
        assert reason == "STOP_DISTANCE_TOO_SMALL"


def test_exact_half_percent_stop_is_accepted():
    risk, reason = build_risk_and_targets("BUY", 1000, 995, 0.50, 2, 3, 4, 2)
    assert reason is None
    assert risk["risk"] == 5
    assert risk["t1"] == 1010
    assert risk["t2"] == 1015
    assert risk["t3"] == 1020


def test_sell_targets_are_symmetric():
    risk, reason = build_risk_and_targets("SELL", 1000, 1005, 0.50, 2, 3, 4, 2)
    assert reason is None
    assert risk["t1"] == 990
    assert risk["t2"] == 985
    assert risk["t3"] == 980
