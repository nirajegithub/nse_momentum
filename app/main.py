import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import SETTINGS
from app.dhan_client import DhanClient
from app.state import load, save
from app.nse_universe import build_universe

IST = ZoneInfo("Asia/Kolkata")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger(__name__)


def get_scan_time() -> datetime:
    """
    Return scanner time in IST.

    TEST_DATETIME example:
    2026-09-04 09:25
    """
    test_datetime = os.getenv("TEST_DATETIME", "").strip()

    if test_datetime:
        return datetime.strptime(
            test_datetime,
            "%Y-%m-%d %H:%M",
        ).replace(tzinfo=IST)

    return datetime.now(IST)


def get_completed_5m_cutoff(scan_time: datetime) -> datetime:
    """
    5M candles are labelled at the right edge.

    09:25 scan -> latest completed candle = 09:25
    09:30 scan -> latest completed candle = 09:30
    """
    minute = (scan_time.minute // 5) * 5

    return scan_time.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )


def get_completed_15m_cutoff(scan_time: datetime):
    """
    15M candles are labelled at the right edge.

    [09:15, 09:30) -> 09:30
    [09:30, 09:45) -> 09:45

    Therefore:
    09:25 -> no current-day completed 15M candle
    09:30 -> first completed 15M candle = 09:30
    """
    if scan_time.hour == 9 and scan_time.minute < 30:
        return None

    minute = (scan_time.minute // 15) * 15

    return scan_time.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )


def validate_scan_time(scan_time: datetime):
    market_open = scan_time.replace(
        hour=9,
        minute=15,
        second=0,
        microsecond=0,
    )

    market_close = scan_time.replace(
        hour=15,
        minute=30,
        second=0,
        microsecond=0,
    )

    if scan_time < market_open or scan_time >= market_close:
        raise ValueError(
            f"Scan time outside NSE session: {scan_time}"
        )


def get_scan_day(scan_date: str):
    """Convert YYYY-MM-DD string to datetime.date for state.py."""
    return datetime.strptime(
        scan_date,
        "%Y-%m-%d",
    ).date()


def create_universe(dhan: DhanClient, scan_date: str):
    logger.info("Creating daily universe for %s", scan_date)

    scan_day = get_scan_day(scan_date)
    state = load(scan_day)

    universe = build_universe(
        dhan,
        as_of_date=scan_date,
    )

    state["date"] = scan_date
    state["universe"] = universe
    state["signals"] = {}

    save(state)

    logger.info(
        "Daily universe created | count=%d",
        len(universe),
    )

    return universe


def get_symbol_and_security_id(item):
    symbol = (
        item.get("symbol")
        or item.get("SYMBOL")
        or item.get("underlying_symbol")
        or item.get("UNDERLYING_SYMBOL")
    )

    security_id = (
        item.get("security_id")
        or item.get("securityId")
        or item.get("SECURITY_ID")
    )

    return symbol, security_id


def get_intraday_date_range(scan_date: str):
    scan_dt = datetime.strptime(
        scan_date,
        "%Y-%m-%d",
    )

    from_date = (
        scan_dt - timedelta(days=7)
    ).strftime("%Y-%m-%d")

    # Dhan toDate is non-inclusive.
    # Use next calendar day so scan_date is included.
    to_date = (
        scan_dt + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    return from_date, to_date


def to_naive_ist(index):
    """
    Convert a DatetimeIndex to naive IST timestamps.
    """
    if getattr(index, "tz", None) is not None:
        return index.tz_convert(IST).tz_localize(None)

    return index


def filter_completed_candles(
    df,
    scan_date: str,
    cutoff,
    timeframe: str,
):
    """
    Keep only candles from the scan date and only completed candles.

    This prevents previous-day candles from being treated as the
    current scan's latest candle.
    """
    if df is None or df.empty:
        return df

    df = df.copy()

    df.index = to_naive_ist(df.index)

    scan_day = datetime.strptime(
        scan_date,
        "%Y-%m-%d",
    ).date()

    # Current scan day only.
    df = df[df.index.date == scan_day]

    if cutoff is not None:
        cutoff_naive = cutoff.replace(
            tzinfo=None
        )

        df = df[df.index <= cutoff_naive]

    logger.info(
        "%s completed candles | date=%s | cutoff=%s | rows=%d | latest=%s",
        timeframe,
        scan_date,
        cutoff.strftime("%H:%M") if cutoff else "NONE",
        len(df),
        df.index[-1] if not df.empty else "NONE",
    )

    return df


def fetch_symbol_data(
    dhan: DhanClient,
    security_id,
    scan_date: str,
):
    from_date, to_date = get_intraday_date_range(scan_date)

    df_5m = dhan.intraday_df(
        security_id=security_id,
        interval=5,
        from_date=from_date,
        to_date=to_date,
    )

    df_15m = dhan.intraday_df(
        security_id=security_id,
        interval=15,
        from_date=from_date,
        to_date=to_date,
    )

    return df_5m, df_15m


def run_scan(
    dhan: DhanClient,
    scan_time: datetime,
):
    scan_date = scan_time.strftime("%Y-%m-%d")
    scan_day = get_scan_day(scan_date)

    cutoff_5m = get_completed_5m_cutoff(
        scan_time
    )

    cutoff_15m = get_completed_15m_cutoff(
        scan_time
    )

    logger.info(
        "SCAN TIME | %s",
        scan_time.strftime("%Y-%m-%d %H:%M:%S %Z"),
    )

    logger.info(
        "5M latest allowed | %s",
        cutoff_5m.strftime("%Y-%m-%d %H:%M"),
    )

    if cutoff_15m:
        logger.info(
            "15M latest allowed | %s",
            cutoff_15m.strftime("%Y-%m-%d %H:%M"),
        )
    else:
        logger.info(
            "15M latest allowed | NONE "
            "(first 15M candle completes at 09:30)"
        )

    state = load(scan_day)

    universe = state.get("universe", [])

    if not universe:
        logger.warning(
            "Universe is empty. Creating universe."
        )

        universe = create_universe(
            dhan,
            scan_date,
        )

    logger.info(
        "Scanning fixed daily universe | count=%d",
        len(universe),
    )

    successful = 0
    skipped_15m = 0

    for item in universe:
        symbol, security_id = get_symbol_and_security_id(
            item
        )

        if not symbol or not security_id:
            logger.warning(
                "Skipping invalid universe item: %s",
                item,
            )
            continue

        try:
            df_5m, df_15m = fetch_symbol_data(
                dhan,
                security_id,
                scan_date,
            )

            df_5m = filter_completed_candles(
                df_5m,
                scan_date,
                cutoff_5m,
                "5M",
            )

            df_15m = filter_completed_candles(
                df_15m,
                scan_date,
                cutoff_15m,
                "15M",
            )

            latest_5m = (
                df_5m.index[-1]
                if df_5m is not None and not df_5m.empty
                else None
            )

            latest_15m = (
                df_15m.index[-1]
                if df_15m is not None and not df_15m.empty
                else None
            )

            logger.info(
                "%s | security_id=%s | "
                "5M latest=%s | 15M latest=%s",
                symbol,
                security_id,
                latest_5m,
                latest_15m,
            )

            successful += 1

            if cutoff_15m is None or latest_15m is None:
                skipped_15m += 1

        except Exception:
            logger.exception(
                "Failed processing %s | security_id=%s",
                symbol,
                security_id,
            )

    logger.info(
        "SCAN COMPLETE | successful=%d | "
        "15M unavailable=%d",
        successful,
        skipped_15m,
    )


def run_monitor(
    dhan: DhanClient,
    scan_time: datetime,
):
    scan_date = scan_time.strftime("%Y-%m-%d")
    scan_day = get_scan_day(scan_date)

    logger.info(
        "MONITOR | %s",
        scan_time.strftime("%Y-%m-%d %H:%M"),
    )

    state = load(scan_day)

    logger.info(
        "Active signals=%d",
        len(state.get("signals", {})),
    )


def run_summary(
    dhan: DhanClient,
    scan_time: datetime,
):
    scan_date = scan_time.strftime("%Y-%m-%d")
    scan_day = get_scan_day(scan_date)

    logger.info(
        "SUMMARY | %s",
        scan_time.strftime("%Y-%m-%d %H:%M"),
    )

    state = load(scan_day)

    logger.info(
        "Summary | universe=%d | signals=%d",
        len(state.get("universe", [])),
        len(state.get("signals", {})),
    )

    # Clear same-day runtime state after summary.
    state["date"] = ""
    state["universe"] = []
    state["signals"] = {}

    save(state)

    logger.info("Runtime state cleared.")


def main():
    scan_time = get_scan_time()

    validate_scan_time(scan_time)

    action = os.getenv(
        "SCANNER_ACTION",
        "scan",
    ).strip().lower()

    logger.info(
        "Scanner action=%s | scan_time=%s",
        action,
        scan_time,
    )

    dhan = DhanClient()

    scan_date = scan_time.strftime("%Y-%m-%d")

    if action == "universe":
        create_universe(
            dhan,
            scan_date,
        )

    elif action == "scan":
        run_scan(
            dhan,
            scan_time,
        )

    elif action == "monitor":
        run_monitor(
            dhan,
            scan_time,
        )

    elif action == "summary":
        run_summary(
            dhan,
            scan_time,
        )

    else:
        raise ValueError(
            f"Unknown SCANNER_ACTION: {action}"
        )


if __name__ == "__main__":
    main()
