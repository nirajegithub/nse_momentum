from __future__ import annotations


def score_signal(regime, entry):
    """
    V1 signal score = 100 points.

    15M regime:
        Price vs VWAP       10
        EMA9 vs EMA20       10
        EMA20 slope         10
        RSI + RSI EMA       10
        Structure           10

    5M entry:
        EMA9 vs EMA20       10
        Price vs VWAP       10
        RSI + RSI EMA       10
        RVOL                10
        Breakout/Continue   10

    Total = 100
    """

    buy = entry["direction"] == "BUY"

    # -------------------------
    # 15M score
    # -------------------------

    if buy:
        price_vwap = bool(regime["price_above_vwap"])
        ema = bool(regime["ema9_gt_ema20"])
        slope = bool(regime["ema20_rising"])
    else:
        price_vwap = not bool(regime["price_above_vwap"])
        ema = not bool(regime["ema9_gt_ema20"])
        slope = not bool(regime["ema20_rising"])

    rsi_15 = bool(regime["rsi_ok"])
    structure_15 = bool(regime["structure_ok"])

    # -------------------------
    # 5M score
    # -------------------------

    ema_5 = bool(entry["ema_ok"])
    vwap_5 = bool(entry["vwap_ok"])
    rsi_5 = bool(entry["rsi_ok"])

    score = 0

    checks = [
        ("15M Price vs VWAP", price_vwap),
        ("15M EMA9 vs EMA20", ema),
        ("15M EMA20 slope", slope),
        ("15M RSI", rsi_15),
        ("15M Structure", structure_15),
        ("5M EMA9 vs EMA20", ema_5),
        ("5M Price vs VWAP", vwap_5),
        ("5M RSI", rsi_5),
    ]

    for _, passed in checks:
        if passed:
            score += 10

    # -------------------------
    # RVOL
    # -------------------------

    rvol = float(entry.get("rvol", 0.0))

    if rvol >= 2.0:
        score += 10
    elif rvol >= 1.5:
        score += 8
    elif rvol >= 1.2:
        score += 5
    elif rvol >= 1.0:
        score += 2
    else:
        score += 0

    # -------------------------
    # Setup
    # -------------------------

    setup = entry.get("setup")

    if setup in {"BREAKOUT", "CONTINUATION"}:
        score += 10

    # -------------------------
    # Grade
    # -------------------------

    if score >= 90:
        grade = "A+"
    elif score >= 80:
        grade = "A"
    elif score >= 70:
        grade = "WATCH"
    else:
        grade = "IGNORE"

    return int(score), grade
