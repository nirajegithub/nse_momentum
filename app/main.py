import logging
import os
from datetime import datetime, time

from .calendar import is_nse_trading_day
from .config import SETTINGS
from .dhan_client import DhanClient
from .state import load, save
from .nse_universe import build_universe


LOG = logging.getLogger("nse_momentum")

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def get_scan_time():
    """
    Return the effective scan datetime.

    TEST_DATETIME is used for testing.

    Example:
        TEST_DATETIME=2026-09-04 09:25

    If TEST_DATETIME is not supplied,
    current IST time is used.
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

    try:
        from zoneinfo import ZoneInfo

        return datetime.now(
            ZoneInfo("Asia/Kolkata")
        ).replace(tzinfo=None)

    except Exception:
        return datetime.now()


def get_completed_5m_cutoff(scan_time):
    """
    Return the latest completed 5M candle timestamp.

    Example:
        09:25 -> 09:25
        09:26 -> 09:25
        09:30 -> 09:30

    A candle labelled 09:25 represents
    the 09:20-09:25 interval.
    """
    minute = (scan_time.minute // 5) * 5

    return scan_time.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )


def get_completed_15m_cutoff(scan_time):
    """
    Return the latest completed 15M candle timestamp.

    Examples:
        09:25 -> 09:15
        09:30 -> 09:30
        09:35 -> 09:30
        15:25 -> 15:15
        15:30 -> 15:30

    A candle labelled 09:30 represents
    the 09:15-09:30 interval.
    """
    minute = scan_time.minute

    completed_minute = (minute // 15) * 15

    return scan_time.replace(
        minute=completed_minute,
        second=0,
        microsecond=0,
    )


def log_candle_cutoffs(scan_time):
    """
    Log the latest candles allowed for the strategy.
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
    """
    Create the fixed daily universe.

    Universe is created only once for the day.
    """
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
    """
    Validate that scan time is within NSE market hours.
    """
    current_time = scan_time.time()

    if current_time < MARKET_OPEN:
        LOG.info(
            "Before market open: %s",
            scan_time.strftime("%H:%M"),
        )
        return False

    if current_time > MARKET_CLOSE:
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

    # Existing project configuration.
    config = SETTINGS

    scan_time = get_scan_time()

    if scan_time is None:
        return

    # TEST_DATE allows historical trading-day testing.
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

    # Do not run on NSE holidays/weekends.
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

    dhan = DhanClient()
    
    state = load()

    # ---------------------------------------------------------
    # UNIVERSE
    # ---------------------------------------------------------
    if action == "universe":
        create_universe(
            dhan,
            state,
            as_of_date=today,
        )
        return

    # ---------------------------------------------------------
    # SCAN / MONITOR
    # ---------------------------------------------------------
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

        # Strategy integration will be added next.
        #
        # For now this test verifies that the scanner
        # correctly determines which completed 5M and
        # 15M candles are allowed at the requested scan time.

        return

    # ---------------------------------------------------------
    # SUMMARY
    # ---------------------------------------------------------
    if action == "summary":
        LOG.info(
            "Summary action requested."
        )
        return

    # ---------------------------------------------------------
    # UNKNOWN ACTION
    # ---------------------------------------------------------
    LOG.error(
        "Unknown SCANNER_ACTION=%s",
        action,
    )


if __name__ == "__main__":
    main()
