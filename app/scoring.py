from __future__ import annotations

from dataclasses import dataclass
import math
import pandas as pd


@dataclass(frozen=True)
class TradeQuality:
    departure: float
    freshness: float
    base_candles: float
    total: float
    base_count: int
    tested_count: int
    zone_low: float | None
    zone_high: float | None
    curve_context: str


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _exciting(row: pd.Series, median_body: float, median_range: float) -> bool:
    body = abs(float(row["close"]) - float(row["open"]))
    rng = max(float(row["high"]) - float(row["low"]), 0.0)
    if rng <= 0:
        return False
    close_pos = (float(row["close"]) - float(row["low"])) / rng
    open_pos = (float(row["open"]) - float(row["low"])) / rng
    directional_body = body >= max(median_body * 1.25, median_range * 0.35)
    close_near_extreme = close_pos >= 0.70 or open_pos >= 0.55 or close_pos <= 0.30 or open_pos <= 0.45
    return directional_body and close_near_extreme


def _detect_base(df: pd.DataFrame) -> tuple[int, float | None, float | None]:
    """Conservative OHLC proxy for a Demand/Supply base.

    The repository has no explicit zone object. We therefore derive the most
    recent compact base from completed candles without inventing a numerical
    curve model. This is used only for the 0-7 trade-quality score.
    """
    if len(df) < 7:
        return 0, None, None

    ranges = (df["high"] - df["low"]).abs()
    bodies = (df["close"] - df["open"]).abs()
    median_range = float(ranges.iloc[-21:-1].median()) if len(df) > 21 else float(ranges.iloc[:-1].median())
    median_body = float(bodies.iloc[-21:-1].median()) if len(df) > 21 else float(bodies.iloc[:-1].median())
    if not _finite(median_range) or median_range <= 0:
        return 0, None, None

    # Look immediately behind the current setup candle. A base candle is a
    # relatively compact candle whose range is <= 0.9x the recent median.
    count = 0
    for i in range(len(df) - 2, max(-1, len(df) - 7), -1):
        r = float(ranges.iloc[i])
        if r <= median_range * 0.90:
            count += 1
        else:
            break

    if count <= 0:
        return 0, None, None

    start = len(df) - 1 - count - 0
    base = df.iloc[start:len(df) - 1]
    return count, float(base["low"].min()), float(base["high"].max())


def _freshness(df: pd.DataFrame, zone_low: float, zone_high: float, direction: str, base_count: int) -> tuple[float, int]:
    if base_count <= 0:
        return 0.0, 0

    prior = df.iloc[:-base_count-1] if len(df) > base_count + 1 else df.iloc[0:0]
    if prior.empty:
        return 2.0, 0

    tests = 0
    tol = 0.0
    # A test is counted only when a completed candle CLOSES back inside the
    # zone. Merely approaching the zone while forming the base is not a test.
    for _, row in prior.tail(30).iterrows():
        close = float(row["close"])
        if zone_low - tol <= close <= zone_high + tol:
            tests += 1

    if tests == 0:
        return 2.0, tests
    if tests == 1:
        return 1.0, tests
    return 0.0, tests


def _departure(df: pd.DataFrame, direction: str) -> float:
    if len(df) < 5:
        return 0.0

    ranges = (df["high"] - df["low"]).abs()
    bodies = (df["close"] - df["open"]).abs()
    median_range = float(ranges.iloc[-21:-1].median()) if len(df) > 21 else float(ranges.iloc[:-1].median())
    median_body = float(bodies.iloc[-21:-1].median()) if len(df) > 21 else float(bodies.iloc[:-1].median())
    if median_range <= 0 or median_body <= 0:
        return 0.0

    current = df.iloc[-1]
    previous = df.iloc[-2]
    exciting_now = _exciting(current, median_body, median_range)
    exciting_prev = _exciting(previous, median_body, median_range)

    if direction == "BUY":
        gap = float(current["open"]) > float(previous["high"])
        aligned = float(current["close"]) > float(current["open"])
    else:
        gap = float(current["open"]) < float(previous["low"])
        aligned = float(current["close"]) < float(current["open"])

    if aligned and (gap or (exciting_now and exciting_prev)):
        return 3.0
    if aligned and exciting_now:
        return 1.5
    return 0.0


def _curve_context(df: pd.DataFrame, direction: str) -> str:
    """Non-numeric context only; never blocks the score."""
    if len(df) < 20:
        return "NOT_AVAILABLE"
    close = float(df.iloc[-1]["close"])
    lo = float(df["low"].tail(20).min())
    hi = float(df["high"].tail(20).max())
    if hi <= lo:
        return "NOT_AVAILABLE"
    pos = (close - lo) / (hi - lo)
    if pos <= 0.30:
        return "LOW"
    if pos >= 0.70:
        return "HIGH"
    return "EQUILIBRIUM"


def score_trade_quality(df15: pd.DataFrame, direction: str) -> dict:
    base_count, zone_low, zone_high = _detect_base(df15)

    if base_count <= 0:
        base_score = 0.0
        freshness_score = 0.0
        tested_count = 0
    elif base_count <= 3:
        base_score = 2.0
        freshness_score, tested_count = _freshness(df15, zone_low, zone_high, direction, base_count)
    elif base_count <= 5:
        base_score = 1.0
        freshness_score, tested_count = _freshness(df15, zone_low, zone_high, direction, base_count)
    else:
        base_score = 0.0
        freshness_score, tested_count = _freshness(df15, zone_low, zone_high, direction, min(base_count, 5))

    departure_score = _departure(df15, direction)
    total = round(departure_score + freshness_score + base_score, 1)

    return {
        "departure_score": departure_score,
        "freshness_score": freshness_score,
        "base_candle_score": base_score,
        "trade_quality_score": total,
        "base_candle_count": base_count,
        "zone_test_count": tested_count,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "curve_context": _curve_context(df15, direction),
    }


def score_signal(*args, **kwargs):
    """Compatibility wrapper for callers that previously imported score_signal."""
    if kwargs.get("df15") is not None and kwargs.get("direction") is not None:
        return score_trade_quality(kwargs["df15"], kwargs["direction"])
    return {"trade_quality_score": 0.0, "departure_score": 0.0, "freshness_score": 0.0, "base_candle_score": 0.0}
