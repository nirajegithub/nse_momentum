from __future__ import annotations

from .config import SETTINGS


def structure(df, lookback=8):
    """
    Determine basic market structure from two consecutive windows.

    Returns:
        BULLISH_HH_HL
        BEARISH_LH_LL
        NEUTRAL
    """
    if len(df) < lookback * 2 + 1:
        return "NEUTRAL"

    recent = df.iloc[-lookback:]
    previous = df.iloc[-2 * lookback:-lookback]

    recent_high = float(recent.high.max())
    previous_high = float(previous.high.max())

    recent_low = float(recent.low.min())
    previous_low = float(previous.low.min())

    if recent_high > previous_high and recent_low > previous_low:
        return "BULLISH_HH_HL"

    if recent_high < previous_high and recent_low < previous_low:
        return "BEARISH_LH_LL"

    return "NEUTRAL"


def regime(df):
    """
    15M regime.

    The previous implementation required every condition to be true
    simultaneously before returning BUY/SELL.

    V1 now uses a 5-point directional confirmation model:
        1. Price vs VWAP
        2. EMA9 vs EMA20
        3. EMA20 slope
        4. RSI + RSI EMA
        5. Market structure

    A direction becomes active when at least 4/5 conditions agree.
    The individual conditions are still returned for scoring.
    """

    if len(df) < SETTINGS.min_15m_candles:
        return {
            "direction": "NEUTRAL",
            "structure": "NEUTRAL",
            "price_above_vwap": False,
            "ema9_gt_ema20": False,
            "ema20_rising": False,
            "rsi_ok": False,
            "structure_ok": False,
            "bullish_points": 0,
            "bearish_points": 0,
        }

    c = df.iloc[-1]
    st = structure(df)

    price_above_vwap = bool(c.close > c.vwap)
    ema9_gt_ema20 = bool(c.ema9 > c.ema20)
    ema20_rising = bool(c.ema20_slope > 0)

    bullish_rsi = bool(
        c.rsi14 > 55 and
        c.rsi14 > c.rsi_ema9
    )

    bearish_rsi = bool(
        c.rsi14 < 45 and
        c.rsi14 < c.rsi_ema9
    )

    bullish_structure = st == "BULLISH_HH_HL"
    bearish_structure = st == "BEARISH_LH_LL"

    bullish_points = sum(
        [
            price_above_vwap,
            ema9_gt_ema20,
            ema20_rising,
            bullish_rsi,
            bullish_structure,
        ]
    )

    bearish_points = sum(
        [
            not price_above_vwap,
            not ema9_gt_ema20,
            not ema20_rising,
            bearish_rsi,
            bearish_structure,
        ]
    )

    if bullish_points >= 4 and bullish_points > bearish_points:
        direction = "BUY"
    elif bearish_points >= 4 and bearish_points > bullish_points:
        direction = "SELL"
    else:
        direction = "NEUTRAL"

    if direction == "BUY":
        rsi_ok = bullish_rsi
        structure_ok = bullish_structure
    elif direction == "SELL":
        rsi_ok = bearish_rsi
        structure_ok = bearish_structure
    else:
        rsi_ok = False
        structure_ok = False

    return {
        "direction": direction,
        "structure": st,
        "price_above_vwap": price_above_vwap,
        "ema9_gt_ema20": ema9_gt_ema20,
        "ema20_rising": ema20_rising,
        "rsi_ok": rsi_ok,
        "structure_ok": structure_ok,
        "bullish_points": bullish_points,
        "bearish_points": bearish_points,
    }


def breakout(df, lookback=20):
    """
    Detect a completed-candle breakout.
    """
    if len(df) < lookback + 1:
        return None

    c = df.iloc[-1]
    previous = df.iloc[-lookback - 1:-1]

    if c.close > previous.high.max():
        return "BUY"

    if c.close < previous.low.min():
        return "SELL"

    return None


def evaluate(df5, df15):
    """
    Evaluate a 5M trade setup using the 15M regime.

    15M = regime / directional confirmation
    5M  = trade entry
    """

    if (
        len(df5) < SETTINGS.min_5m_candles
        or len(df15) < SETTINGS.min_15m_candles
    ):
        return None

    r15 = regime(df15)

    if r15["direction"] == "NEUTRAL":
        return None

    c = df5.iloc[-1]
    direction = r15["direction"]

    # 5M EMA confirmation
    ema_ok = (
        bool(c.ema9 > c.ema20)
        if direction == "BUY"
        else bool(c.ema9 < c.ema20)
    )

    # 5M VWAP confirmation
    vwap_ok = (
        bool(c.close > c.vwap)
        if direction == "BUY"
        else bool(c.close < c.vwap)
    )

    # 5M RSI confirmation
    if direction == "BUY":
        rsi_ok = bool(
            c.rsi14 > 55 and
            c.rsi14 > c.rsi_ema9
        )
    else:
        rsi_ok = bool(
            c.rsi14 < 45 and
            c.rsi14 < c.rsi_ema9
        )

    # Breakout confirmation
    bo = breakout(df5)

    if bo == direction:
        setup = "BREAKOUT"
    elif ema_ok and vwap_ok and rsi_ok:
        setup = "CONTINUATION"
    else:
        setup = None

    if not setup:
        return None

    atr = float(c.atr14)

    if atr <= 0:
        return None

    # Technical stop based on confirmed recent 5M structure
    if direction == "BUY":
        swing = float(df5.low.tail(8).min())
        sl = swing - SETTINGS.atr_buffer * atr
        risk = float(c.close - sl)
    else:
        swing = float(df5.high.tail(8).max())
        sl = swing + SETTINGS.atr_buffer * atr
        risk = float(sl - c.close)

    # Invalid risk
    if risk <= 0:
        return None

    # Stop too wide
    if risk > SETTINGS.max_stop_atr * atr:
        return None

    entry = float(c.close)

    # Current V1 targets remain unchanged:
    # T1 = 1.5R
    # T2 = 2.5R
    # T3 = 3.5R
    if direction == "BUY":
        t1 = entry + 1.5 * risk
        t2 = entry + 2.5 * risk
        t3 = entry + 3.5 * risk
    else:
        t1 = entry - 1.5 * risk
        t2 = entry - 2.5 * risk
        t3 = entry - 3.5 * risk

    return {
        "direction": direction,
        "setup": setup,
        "candle_time": df5.index[-1].isoformat(),
        "signal_price": entry,
        "rvol": (
            float(c.rvol)
            if c.rvol == c.rvol
            else 0.0
        ),
        "rsi": float(c.rsi14),
        "ema_ok": ema_ok,
        "vwap_ok": vwap_ok,
        "rsi_ok": rsi_ok,
        "regime": r15,
        "risk": {
            "entry": entry,
            "sl": float(sl),
            "risk": risk,
            "t1": float(t1),
            "t2": float(t2),
            "t3": float(t3),
        },
    }
