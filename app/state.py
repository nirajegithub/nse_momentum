from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / "state" / "runtime_state.json"
BACKUP_DIR = STATE.parent / "backups"


def blank(day):
    return {
        "date": day.isoformat(),
        "universe": [],
        "signals": {},
        "alert_state": {},
    }


def load(day):
    if not STATE.exists():
        return blank(day)
    try:
        s = json.loads(STATE.read_text())
    except Exception:
        return blank(day)

    if s.get("date") != day.isoformat():
        return blank(day)

    # Backward compatibility with older runtime_state.json files.
    s.setdefault("universe", [])
    s.setdefault("signals", {})
    s.setdefault("alert_state", {})
    return s


def save(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False))


def backup_and_clear(s, day):
    """Back up the completed day's state, then clear the live state file."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"runtime_state_{day.isoformat()}.json"
    backup.write_text(json.dumps(s, indent=2, ensure_ascii=False))
    save({
        "date": "",
        "universe": [],
        "signals": {},
        "alert_state": {},
    })


def key(symbol, direction, setup, candle):
    return f"{symbol}|{direction}|{setup}|{candle}"


def active_for_symbol(s, symbol):
    return any(
        v.get("symbol") == symbol and v.get("status") == "ACTIVE"
        for v in s["signals"].values()
    )


def active_signal_for_symbol(s, symbol):
    for value in s["signals"].values():
        if value.get("symbol") == symbol and value.get("status") == "ACTIVE":
            return value
    return None


def _parse_alert_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def alert_allowed(s, signal, now, settings):
    """Return (allowed, reason) for Telegram alerting.

    Signal generation and scoring remain untouched. This is only the
    notification gate.
    """
    score = int(signal.get("score", 0))
    rvol = float(signal.get("rvol", 0.0))
    grade = str(signal.get("grade", "")).upper()
    direction = str(signal.get("direction", "")).upper()
    symbol = signal.get("symbol", "")
    regime = signal.get("regime") or {}

    if grade not in {"A", "A+"} or score < settings.alert_min_score:
        return False, "below alert score/grade threshold"

    if rvol < settings.alert_min_rvol:
        return False, "below minimum RVOL"

    # Direction must agree with the 15M regime.
    if regime.get("direction") != direction:
        return False, "signal/regime direction mismatch"

    previous = s.get("alert_state", {}).get(symbol)
    active = active_signal_for_symbol(s, symbol)

    # A strong opposite-direction signal is a reversal and may be alerted.
    if active and active.get("direction") != direction:
        if score < settings.alert_reversal_min_score:
            return False, "reversal score too low"
        return True, "strong reversal"

    if previous:
        previous_direction = previous.get("direction")
        previous_setup = previous.get("setup")
        previous_score = int(previous.get("score", 0))
        alerted_at = _parse_alert_time(previous.get("alerted_at"))

        if previous_direction == direction and alerted_at is not None:
            if now - alerted_at < timedelta(minutes=settings.alert_cooldown_minutes):
                return False, "same-direction cooldown"

            # After cooldown, require a meaningful improvement unless setup changed.
            if (
                previous_setup == signal.get("setup")
                and score < previous_score + settings.alert_score_improvement
            ):
                return False, "no meaningful score improvement"

    return True, "approved"


def record_alert(s, signal, now):
    symbol = signal["symbol"]
    s.setdefault("alert_state", {})[symbol] = {
        "direction": signal["direction"],
        "setup": signal["setup"],
        "score": int(signal["score"]),
        "rvol": float(signal.get("rvol", 0.0)),
        "alerted_at": now.isoformat(),
        "candle_time": signal.get("signal_time"),
    }


def reverse_active_signal(s, symbol, direction, now):
    """Close an older active signal when a confirmed opposite signal fires."""
    for value in s["signals"].values():
        if (
            value.get("symbol") == symbol
            and value.get("status") == "ACTIVE"
            and value.get("direction") != direction
        ):
            value["status"] = "REVERSED"
            value["exit_time"] = now.isoformat()
            value["exit_reason"] = "Confirmed opposite-direction signal"
