from __future__ import annotations


def build_risk_and_targets(direction: str, entry: float, sl: float, min_stop_distance_percent: float, t1_rr: float, t2_rr: float, t3_rr: float, min_rr: float):
    entry = float(entry)
    sl = float(sl)
    risk = entry - sl if direction == "BUY" else sl - entry
    if risk <= 0:
        return None, "INVALID_RISK"
    risk_pct = risk / entry * 100.0
    if risk_pct < float(min_stop_distance_percent):
        return None, "STOP_DISTANCE_TOO_SMALL"
    if direction == "BUY":
        t1 = entry + float(t1_rr) * risk
        t2 = entry + float(t2_rr) * risk
        t3 = entry + float(t3_rr) * risk
    else:
        t1 = entry - float(t1_rr) * risk
        t2 = entry - float(t2_rr) * risk
        t3 = entry - float(t3_rr) * risk
    if float(t1_rr) < float(min_rr):
        return None, "T1_RR_BELOW_MIN"
    return {
        "entry": entry,
        "sl": sl,
        "risk": risk,
        "risk_percent": risk_pct,
        "t1": t1,
        "t2": t2,
        "t3": t3,
        "rr_t1": float(t1_rr),
    }, None
