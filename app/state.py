from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date, datetime
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / "state" / "runtime_state.json"
BACKUP_DIR = STATE.parent / "backups"
LOG = logging.getLogger(__name__)


def blank(day):
    return {
        "date": day.isoformat(),
        "universe": [],
        "signals": {},
        "pending_setups": {},
        "daily_filters": {},
        "alert_state": {},
    }


def load(day):
    if not STATE.exists():
        return blank(day)
    try:
        s = json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        LOG.exception("State file is unreadable: %s", STATE)
        return blank(day)

    if s.get("date") != day.isoformat():
        return blank(day)

    s.setdefault("universe", [])
    s.setdefault("signals", {})
    s.setdefault("pending_setups", {})
    s.setdefault("daily_filters", {})
    s.setdefault("alert_state", {})
    return s


def save(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(s, indent=2, ensure_ascii=False)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=STATE.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = handle.name
    os.replace(temporary, STATE)


def key(symbol, direction, setup, candle):
    return f"{symbol}|{direction}|{setup}|{candle}"


def signal_key(symbol, direction, setup_5m_timestamp):
    return f"{symbol}|{direction}|{setup_5m_timestamp}"


def active_for_symbol(state, symbol):
    return any(
        v.get("symbol") == symbol and v.get("status") == "ACTIVE"
        for v in state.get("signals", {}).values()
    )


# Compatibility alias used by older code.
active_signal_for_symbol = active_for_symbol


def reverse_active_signal(state, symbol, new_direction, ts, exit_price):
    changed = False
    for signal in state.get("signals", {}).values():
        if signal.get("symbol") != symbol or signal.get("status") != "ACTIVE":
            continue
        if signal.get("direction") == new_direction:
            continue

        entry = float(signal.get("risk", {}).get("entry", 0))
        risk = float(signal.get("risk", {}).get("risk", 0))
        px = float(exit_price)
        move = px - entry if signal["direction"] == "BUY" else entry - px

        signal["status"] = "REVERSED"
        signal["exit_price"] = px
        signal["exit_time"] = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        signal["exit_reason"] = f"Reversed by {new_direction}"
        signal["r_multiple"] = move / risk if risk > 0 else None
        changed = True
    return changed


def record_alert(state, signal, ts):
    key_value = signal.get("signal_key") or signal_key(
        signal["symbol"], signal["direction"], signal["setup_5m_timestamp"]
    )
    state.setdefault("alert_state", {})[key_value] = {
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "setup_5m_timestamp": signal["setup_5m_timestamp"],
        "confirmation_5m_timestamp": signal.get("confirmation_5m_timestamp"),
        "recorded_at": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
    }


def backup_and_clear(s, day):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup = BACKUP_DIR / f"runtime_state_{day.isoformat()}.json"
    backup.write_text(
        json.dumps(s, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    cleared = blank(day)
    # Existing workflow expects runtime state to be cleared after EOD.
    cleared["date"] = ""
    save(cleared)
