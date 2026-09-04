def score_signal(regime, entry):
    buy = entry["direction"] == "BUY"; score = 0
    checks = [
        regime["price_above_vwap"] if buy else not regime["price_above_vwap"],
        regime["ema9_gt_ema20"] if buy else not regime["ema9_gt_ema20"],
        regime["ema20_rising"] if buy else not regime["ema20_rising"],
        regime["rsi_ok"], regime["structure_ok"], entry["ema_ok"], entry["vwap_ok"], entry["rsi_ok"]]
    score += sum(10 for x in checks if x)
    r = entry["rvol"]
    score += 10 if r >= 2 else 8 if r >= 1.5 else 5 if r >= 1.2 else 2 if r >= 1 else 0
    score += 10 if entry["setup"] in {"BREAKOUT", "CONTINUATION"} else 0
    grade = "A+" if score >= 90 else "A" if score >= 80 else "WATCH" if score >= 70 else "IGNORE"
    return score, grade
