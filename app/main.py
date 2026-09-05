import logging
import os
from datetime import datetime, time

from .calendar import is_nse_trading_day
from .config import load_config
from .dhan_client import DhanClient
from .state import load, save
from .nse_universe import build_universe


LOG = logging.getLogger("nse_momentum")


MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def get_scan_time():
    """
    Return the effective scan datetime.

    TEST_DATETIME can be used for testing, for example:
        2026-09-04 09:25

    If TEST_DATETIME is not supplied, use current IST time.
    """
    test_datetime = os.getenv("TEST_DATETIME", "").strip()

    if test_datetime:
        try:
            return datetime.strptime(
                test_datetime,
                "%Y-%m-%d %H:%M",
            )
        except ValueError:
            LOG.error(
                "Invalid TEST_DATETIME=%s. "
                "Expected YYYY-MM-DD HH:MM.",
                test_datetime,
            )
            return None

    from datetime import datetime as dt

    try:
        from zoneinfo import ZoneInfo

        return dt.now(ZoneInfo("Asia/Kolkata")).replace(tzinfo=None)
    except Exception:
        return dt.now()


def get_completed_5m_cutoff(scan_time):
    """
    A 5M candle labelled 09:25 represents 09:20-09:25.

    Therefore at 09:25, the 09:25 candle is complete and usable.
    """
    minute = (scan_time.minute // 5) * 5

    return scan_time.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )


def get_completed_15m_cutoff(scan_time):
    """
    A 15M candle labelled 09:30 represents 09:15-09:30.

    Therefore:
      09:25 -> latest completed 15M candle is BEFORE 09:25
      09:30 -> 09:30 candle is completed
      15:25 -> latest completed 15M candle is 15:15
      15:30 -> 15:30 candle is completed, but scanner does not scan then
    """
    minute = scan_time.minute

    if minute % 15 == 0:
        return scan_time.replace(
            minute=minute,
            second=0,
            microsecond=0,
        )

    completed_minute = (minute // 15) * 15

    return scan_time.replace(
        minute=completed_minute,
        second=0,
        microsecond=0,
    )


def log_candle_cutoffs(scan_time):
    """
    Log the candles that strategy is allowed to use.
    """
    cutoff_5m = get_completed_5m_cutoff(scan_time)
    cutoff_15m = get_completed_15m_cutoff(scan_time)

    LOG.info(
        "Scan time: %s",
        scan_time.strftime("%Y-%m-%d %H:%M"),
    )

    LOG.info(
        "5M latest allowed candle: %s",
        cutoff_5m.strftime("%Y-%m-%d %H:%M"),
    )

    LOG.info(
        "15M latest allowed candle: %s",
        cutoff_15m.strftime("%Y-%m-%d %H:%M"),
    )

    return cutoff_5m, cutoff_15m


def create_universe(dhan, state, as_of_date):
    if state["universe"]:
        LOG.info(
            "Universe already exists: %d symbols",
            len(state["universe"]),
        )
        return

    state["universe"] = build_universe(
        dhan,
        as_of_date=as_of_date,
    )

    save(state)

    LOG.info(
        "Universe size: %d",
        len(state["universe"]),
    )


def validate_scan_time(scan_time):
    if scan_time.time() < MARKET_OPEN:
        LOG.info(
            "Before market open: %s",
            scan_time.strftime("%H:%M"),
        )
        return False

    if scan_time.time() > MARKET_CLOSE:
        LOG.info(
            "After market close: %s",
            scan_time.strftime("%H:%M"),
        )
        return False

    return True


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    config = load_config()

    scan_time = get_scan_time()

    if scan_time is None:
        return

    test_date = os.getenv("TEST_DATE", "").strip()

    if test_date:
        try:
            today = datetime.strptime(
                test_date,
                "%Y-%m-%d",
            ).date()

            LOG.info(
                "TEST_DATE override active: %s",
                today.isoformat(),
            )

        except ValueError:
            LOG.error(
                "Invalid TEST_DATE=%s. Expected YYYY-MM-DD.",
                test_date,
            )
            return
    else:
        today = scan_time.date()

    if not is_nse_trading_day(today):
        LOG.info(
            "Not an NSE trading day: %s",
            today.isoformat(),
        )
        return

    action = os.getenv(
        "SCANNER_ACTION",
        "scan",
    ).strip().lower()

    LOG.info(
        "Scanner action: %s",
        action,
    )

    dhan = DhanClient(config)

    state = load()

    if action == "universe":
        create_universe(
            dhan,
            state,
            as_of_date=today,
        )
        return

    if action in {"scan", "monitor"}:
        if not validate_scan_time(scan_time):
            return

        cutoff_5m, cutoff_15m = log_candle_cutoffs(
            scan_time,
        )

        LOG.info(
            "Completed-candle validation active."
        )

        LOG.info(
            "Strategy data must not use candles after "
            "5M=%s or 15M=%s",
            cutoff_5m.strftime("%H:%M"),
            cutoff_15m.strftime("%H:%M"),
        )

        # Strategy scan will be connected here next.
        #
        # IMPORTANT:
        # Do not call strategy yet until the cutoff
        # values are passed into the strategy/data layer.

        return

    if action == "summary":
        LOG.info("Summary action requested.")
        return

    LOG.error(
        "Unknown SCANNER_ACTION=%s",
        action,
    )


if __name__ == "__main__":
    main()
