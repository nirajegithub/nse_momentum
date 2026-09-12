def _points(signal, price):
    entry = float(signal["risk"]["entry"])
    px = float(price)
    return px - entry if signal["direction"] == "BUY" else entry - px


def _result_price(signal, final_prices):
    # If a target was reached, summarize the highest achieved target rather
    # than allowing a later EOD pullback to erase the achieved target result.
    hit = signal.get("highest_target_hit")
    if hit == "T3_HIT":
        return float(signal["risk"]["t3"])
    if hit == "T2_HIT":
        return float(signal["risk"]["t2"])
    if hit == "T1_HIT":
        return float(signal["risk"]["t1"])
    if signal.get("status") == "EXITED" and signal.get("exit_price") is not None:
        return float(signal["exit_price"])
    return float(final_prices.get(signal["symbol"], signal["risk"]["entry"]))


def build_summary(state, final_prices):
    lines = ["📊 NSE MOMENTUM SUMMARY", ""]
    count = 0
    for s in state.get("signals", {}).values():
        if s.get("status") not in {"ACTIVE", "EXITED", "CLOSED_EOD", "REVERSED"}:
            continue
        price = _result_price(s, final_prices)
        points = _points(s, price)
        lines.append(f"{s['symbol']} Stocks {points:+.2f} points")
        count += 1
    if count == 0:
        lines.append("No completed alerts.")
    return "\n".join(lines)
