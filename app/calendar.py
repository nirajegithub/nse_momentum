from __future__ import annotations
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
HOLIDAY_FILE = Path(__file__).resolve().parents[1] / "data" / "nse_holidays.json"

def load_holidays() -> set[date]:
    if not HOLIDAY_FILE.exists():
        return set()
    try:
        payload = json.loads(HOLIDAY_FILE.read_text(encoding="utf-8"))
        return {date.fromisoformat(x) for x in payload.get("holidays", [])}
    except Exception:
        return set()

def is_nse_trading_day(day: date | None = None) -> bool:
    day = day or datetime.now(IST).date()
    return day.weekday() < 5 and day not in load_holidays()

def previous_trading_day(day: date) -> date:
    d = day - timedelta(days=1)
    holidays = load_holidays()
    while d.weekday() >= 5 or d in holidays:
        d -= timedelta(days=1)
    return d
