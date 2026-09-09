import json
from datetime import date

from app import state
from app.calendar import is_nse_trading_day

def test_weekend_is_not_trading_day():
    assert not is_nse_trading_day(date(2026, 9, 5))


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
