import json
from datetime import date, datetime

from app import state
from app.calendar import is_nse_trading_day
from app.nse_universe import extract_symbols, fetch_volume_gainer_symbols


class AlertSettings:
    alert_min_score = 80
    alert_min_rvol = 0.5
    alert_max_rvol = 20.0
    alert_require_structure = True
    alert_reversal_min_score = 85
    alert_cooldown_minutes = 30
    alert_score_improvement = 10

def test_weekend_is_not_trading_day():
    assert not is_nse_trading_day(date(2026, 9, 5))


def test_nse_gainer_payload_uses_last_price_and_total_volume(monkeypatch):
    class Response:
        def json(self):
            return {
                "data": [
                    {
                        "symbol": "GRAPHITE",
                        "lastPrice": 843,
                        "totalTradedVolume": 36292952,
                    }
                ]
            }

    monkeypatch.setattr(
        "app.nse_universe.nse_get",
        lambda *args, **kwargs: Response(),
    )

    assert fetch_volume_gainer_symbols(object()) == ["GRAPHITE"]


def test_selected_variation_sections_are_symbol_extractable():
    payload = {
        "NIFTY": {"data": [{"symbol": "ADANIENT"}]},
        "NIFTYNEXT50": {"data": [{"symbol": "CUMMINSIND"}]},
        "FOSec": {"data": [{"symbol": "GRAPHITE"}]},
    }

    assert extract_symbols(payload["NIFTY"]) == ["ADANIENT"]
    assert extract_symbols(payload["NIFTYNEXT50"]) == ["CUMMINSIND"]
    assert extract_symbols(payload["FOSec"]) == ["GRAPHITE"]


def test_backup_and_clear(monkeypatch, tmp_path):
    runtime_state = tmp_path / "runtime_state.json"
    backup_dir = tmp_path / "backups"
    monkeypatch.setattr(state, "STATE", runtime_state)
    monkeypatch.setattr(state, "BACKUP_DIR", backup_dir)

    completed = {
        "date": "2026-09-09",
        "universe": [{"symbol": "TEST"}],
        "signals": {"signal": {"status": "CLOSED_EOD"}},
        "alert_state": {},
    }

    state.backup_and_clear(completed, date(2026, 9, 9))

    assert json.loads(
        (backup_dir / "runtime_state_2026-09-09.json").read_text()
    ) == completed
    assert json.loads(runtime_state.read_text()) == {
        "date": "",
        "universe": [],
        "signals": {},
        "alert_state": {},
    }


def test_alert_rejects_unconfirmed_structure():
    signal = {
        "symbol": "HINDCOPPER",
        "direction": "BUY",
        "score": 85,
        "grade": "A",
        "rvol": 1.4,
        "regime": {"direction": "BUY", "structure_ok": False},
    }

    allowed, reason = state.alert_allowed(
        {"alert_state": {}, "signals": {}},
        signal,
        datetime.now(),
        AlertSettings(),
    )

    assert not allowed
    assert reason == "15M structure not confirmed"


def test_alert_rejects_extreme_rvol():
    signal = {
        "symbol": "NOVARTIND",
        "direction": "BUY",
        "score": 80,
        "grade": "A",
        "rvol": 301.92,
        "regime": {"direction": "BUY", "structure_ok": True},
    }

    allowed, reason = state.alert_allowed(
        {"alert_state": {}, "signals": {}},
        signal,
        datetime.now(),
        AlertSettings(),
    )

    assert not allowed
    assert reason == "extreme RVOL requires review"


def test_alert_rejects_entry_that_chased_breakout():
    signal = {
        "symbol": "TEST",
        "direction": "BUY",
        "score": 80,
        "grade": "A",
        "rvol": 1.0,
        "ltp": 106.0,
        "max_entry": 105.0,
        "regime": {"direction": "BUY", "structure_ok": True},
    }

    allowed, reason = state.alert_allowed(
        {"alert_state": {}, "signals": {}},
        signal,
        datetime.now(),
        AlertSettings(),
    )

    assert not allowed
    assert reason == "entry is too far from breakout"


def test_alert_rejects_invalid_rvol():
    signal = {
        "symbol": "TEST",
        "direction": "BUY",
        "score": 80,
        "grade": "A",
        "rvol": float("nan"),
        "regime": {"direction": "BUY", "structure_ok": True},
    }

    allowed, reason = state.alert_allowed(
        {"alert_state": {}, "signals": {}},
        signal,
        datetime.now(),
        AlertSettings(),
    )

    assert not allowed
    assert reason == "invalid RVOL"


def test_reversal_records_exit_price_and_r_multiple():
    state_data = {
        "signals": {
            "old": {
                "symbol": "TEST",
                "direction": "BUY",
                "status": "ACTIVE",
                "risk": {"entry": 100.0, "risk": 5.0},
            }
        }
    }

    state.reverse_active_signal(
        state_data,
        "TEST",
        "SELL",
        datetime(2026, 9, 9, 10, 0),
        90.0,
    )

    signal = state_data["signals"]["old"]
    assert signal["status"] == "REVERSED"
    assert signal["exit_price"] == 90.0
    assert signal["r_multiple"] == -2.0
