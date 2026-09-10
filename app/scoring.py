from __future__ import annotations


def score_signal(regime, entry):
    """
    V1 scoring model.

    Maximum score = 100.

    15M regime:
      - Price vs VWAP       = 10
      - EMA9 vs EMA20       = 10
      - EMA20 slope         = 10
      - RSI + RSI EMA9      = 10
      - Structure           = 10

    5M entry:
      - EMA9 vs EMA20       = 10
      - Price vs VWAP       = 10
      - RSI + RSI EMA9      = 10

    Volume:
      - RVOL                = 10

        Setup:
            - BREAKOUT            = 10
            - CONTINUATION        = 10
    """

    buy = entry["direction"] == "BUY"

    score = 0

    # ---------------------------------------------------------
    # 15M REGIME
    # ---------------------------------------------------------

    checks = [
        regime["price_above_vwap"]
        if buy
        else not regime["price_above_vwap"],

        regime["ema9_gt_ema20"]
        if buy
        else not regime["ema9_gt_ema20"],

        regime["ema20_rising"]
        if buy
        else not regime["ema20_rising"],

        regime["rsi_ok"],

        regime["structure_ok"],

        # -----------------------------------------------------
        # 5M ENTRY
        # -----------------------------------------------------

        entry["ema_ok"],
        entry["vwap_ok"],
        entry["rsi_ok"],
    ]

    score += sum(
        10
        for check in checks
        if check
    )

    # ---------------------------------------------------------
    # RVOL
    # ---------------------------------------------------------

    rvol = float(
        entry.get("rvol", 0.0)
    )

    if rvol >= 2.0:
        score += 10
    elif rvol >= 1.5:
        score += 8
    elif rvol >= 1.2:
        score += 5
    elif rvol >= 1.0:
        score += 2

    # ---------------------------------------------------------
    # SETUP QUALITY
    # ---------------------------------------------------------

    setup = str(
        entry.get("setup", "")
    ).upper()

    if setup in {"BREAKOUT", "CONTINUATION"}:
        score += 10

    # ---------------------------------------------------------
    # GRADE
    # ---------------------------------------------------------

    if score >= 90:
        grade = "A+"
    elif score >= 80:
        grade = "A"
    elif score >= 70:
        grade = "WATCH"
    else:
        grade = "IGNORE"

    return score, grade
