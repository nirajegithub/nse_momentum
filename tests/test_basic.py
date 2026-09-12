from datetime import date
from app.calendar import is_nse_trading_day

def test_weekend_is_not_trading_day():
    assert not is_nse_trading_day(date(2026, 9, 5))
