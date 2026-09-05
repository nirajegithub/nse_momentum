from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, time

import pandas as pd

from .calendar import is_nse_trading_day
from .config import SETTINGS
from .dhan_client import DhanClient
from .state import load, save
from .nse_universe import build_universe


LOG = logging.getLogger("nse_momentum")

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


# ----------------------------------------------------------------------
# Test / scan datetime
# ----------------------------------------------------------------------

def get_scan_time():
    """
    Return the effective scan datetime.

    TEST_DATETIME example:
        2026-09-04 09:25

    If TEST_DATETIME is not supplied,
    use current IST time.
    """

    test_datetime = os.getenv(
        "TEST_DATETIME",
        "",
    ).strip()

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


# ----------------------------------------------------------------------
# Completed candle cutoffs
# ----------------------------------------------------------------------

def get_completed_5m_cutoff(
    scan_time: datetime,
) -> datetime:

    minute = (
        scan_time.minute // 5
    ) * 5

    return scan_time.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )


def get_completed_15m_cutoff(
    scan_time: datetime,
) -> datetime:

    minute = (
        scan_time.minute // 15
    ) * 15

    return scan_time.replace(
        minute=minute,
        second=0,
        microsecond=0,
    )


# ----------------------------------------------------------------------
# Scan-time validation
# ----------------------------------------------------------------------

def validate_scan_time(
    scan_time: datetime,
) -> bool:

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


# ----------------------------------------------------------------------
# Daily universe
# ----------------------------------------------------------------------

def create_universe(
    dhan: DhanClient,
    state: dict,
    as_of_date,
) -> None:

    if state["universe"]:

        LOG.info(
            "Universe already exists: %d symbols",
            len(state["universe"]),
        )

        return

    LOG.info(
        "Building daily universe for %s",
        as_of_date.isoformat(),
    )

    state["universe"] = build_universe(
        dhan,
        as_of_date=as_of_date,
    )

    save(state)

    LOG.info(
        "Universe created: %d symbols",
        len(state["universe"]),
    )


# ----------------------------------------------------------------------
# Universe item helpers
# ----------------------------------------------------------------------

def get_symbol_and_security_id(
    item: dict,
):
    """
    Support the expected universe structure.

    Example:

    {
        "symbol": "TCS",
        "security_id": "11536",
        "indices": ["M50"],
        "membership_count": 1
    }
    """

    symbol = item.get("symbol")

    security_id = (
        item.get("security_id")
        or item.get("securityId")
        or item.get("SECURITY_ID")
    )

    return symbol, security_id


# ----------------------------------------------------------------------
# Historical date range
# ----------------------------------------------------------------------

def get_intraday_date_range(
    scan_date,
):
    """
    Fetch several calendar days so that the strategy has
    sufficient candles for EMA / RSI / ATR / RVOL calculations.

    We intentionally fetch more than one trading day.
    """

    from_date = (
        scan_date - timedelta(days=7)
    )

    to_date = scan_date

    return (
        from_date.isoformat(),
        to_date.isoformat(),
    )


# ----------------------------------------------------------------------
# Fetch 5M / 15M data
# ----------------------------------------------------------------------

def fetch_symbol_data(
    dhan: DhanClient,
    symbol: str,
    security_id: str,
    scan_time: datetime,
):
    """
    Fetch 5M and 15M historical candles using the
    existing DhanClient.intraday_df() method.
    """

    from_date, to_date = (
        get_intraday_date_range(
            scan_time.date()
        )
    )

    LOG.debug(
        "Fetching candles | symbol=%s security_id=%s "
        "from=%s to=%s",
        symbol,
        security_id,
        from_date,
        to_date,
    )

    df_5m = dhan.intraday_df(
        security_id=str(security_id),
        interval=5,
        from_date=from_date,
        to_date=to_date,
    )

    df_15m = dhan.intraday_df(
        security_id=str(security_id),
        interval=15,
        from_date=from_date,
        to_date=to_date,
    )

    return {
        "5m": df_5m,
        "15m": df_15m,
    }


# ----------------------------------------------------------------------
# Completed candle filtering
# ----------------------------------------------------------------------

def filter_completed_candles(
    df: pd.DataFrame,
    cutoff: datetime,
    timeframe: str,
) -> pd.DataFrame:

    if df is None or df.empty:
        return pd.DataFrame()

    result = df.copy()

    # DhanClient returns timezone-aware IST timestamps.
    if getattr(
        result.index,
        "tz",
        None,
    ) is not None:

        index = (
            result.index
            .tz_convert("Asia/Kolkata")
            .tz_localize(None)
        )

        result.index = index

    else:
        result.index = pd.to_datetime(
            result.index
        )

    result = result.sort_index()

    result = result[
        result.index <= cutoff
    ]

    LOG.debug(
        "%s completed candle filter: "
        "rows=%d cutoff=%s",
        timeframe,
        len(result),
        cutoff.strftime("%Y-%m-%d %H:%M"),
    )

    return result


# ----------------------------------------------------------------------
# Candle diagnostics
# ----------------------------------------------------------------------

def log_dataframe_summary(
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
    cutoff: datetime,
) -> None:

    if df is None or df.empty:

        LOG.warning(
            "%s | %s | NO COMPLETED CANDLES | cutoff=%s",
            symbol,
            timeframe,
            cutoff.strftime("%H:%M"),
        )

        return

    latest = df.iloc[-1]

    LOG.info(
        "%s | %s | candles=%d | "
        "latest=%s | "
        "O=%.2f H=%.2f L=%.2f C=%.2f V=%.0f",
        symbol,
        timeframe,
        len(df),
        df.index[-1].strftime(
            "%Y-%m-%d %H:%M"
        ),
        float(latest["open"]),
        float(latest["high"]),
        float(latest["low"]),
        float(latest["close"]),
        float(latest["volume"]),
    )


# ----------------------------------------------------------------------
# Run diagnostic data scan
# ----------------------------------------------------------------------

def run_scan(
    dhan: DhanClient,
    state: dict,
    scan_time: datetime,
) -> None:

    cutoff_5m = (
        get_completed_5m_cutoff(
            scan_time
        )
    )

    cutoff_15m = (
        get_completed_15m_cutoff(
            scan_time
        )
    )

    LOG.info(
        "Starting candle data scan"
    )

    LOG.info(
        "Universe size: %d",
        len(state["universe"]),
    )

    LOG.info(
        "Scan datetime: %s",
        scan_time.strftime(
            "%Y-%m-%d %H:%M"
        ),
    )

    LOG.info(
        "5M latest allowed candle: %s",
        cutoff_5m.strftime(
            "%Y-%m-%d %H:%M"
        ),
    )

    LOG.info(
        "15M latest allowed candle: %s",
        cutoff_15m.strftime(
            "%Y-%m-%d %H:%M"
        ),
    )

    success = 0
    failed = 0

    for item in state["universe"]:

        symbol, security_id = (
            get_symbol_and_security_id(
                item
            )
        )

        if not symbol:

            LOG.warning(
                "Universe item missing symbol: %s",
                item,
            )

            failed += 1
            continue

        if not security_id:

            LOG.warning(
                "Universe item missing security_id: "
                "symbol=%s item=%s",
                symbol,
                item,
            )

            failed += 1
            continue

        try:

            data = fetch_symbol_data(
                dhan=dhan,
                symbol=symbol,
                security_id=security_id,
                scan_time=scan_time,
            )

            df_5m = filter_completed_candles(
                data["5m"],
                cutoff_5m,
                "5M",
            )

            df_15m = filter_completed_candles(
                data["15m"],
                cutoff_15m,
                "15M",
            )

            log_dataframe_summary(
                symbol,
                "5M",
                df_5m,
                cutoff_5m,
            )

            log_dataframe_summary(
                symbol,
                "15M",
                df_15m,
                cutoff_15m,
            )

            success += 1

        except Exception as exc:

            LOG.exception(
                "Candle processing failed: "
                "symbol=%s security_id=%s error=%s",
                symbol,
                security_id,
                exc,
            )

            failed += 1

    LOG.info(
        "Candle data scan completed | "
        "success=%d | failed=%d | total=%d",
        success,
        failed,
        len(state["universe"]),
    )


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(message)s"
        ),
    )

    config = SETTINGS

    scan_time = get_scan_time()

    if scan_time is None:
        return

    # --------------------------------------------------------------
    # Test date
    # --------------------------------------------------------------

    test_date = os.getenv(
        "TEST_DATE",
        "",
    ).strip()

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
                "Invalid TEST_DATE=%s. "
                "Expected YYYY-MM-DD.",
                test_date,
            )

            return

    else:
        today = scan_time.date()

    # --------------------------------------------------------------
    # NSE trading day
    # --------------------------------------------------------------

    if not is_nse_trading_day(today):

        LOG.info(
            "Not an NSE trading day: %s",
            today.isoformat(),
        )

        return

    # --------------------------------------------------------------
    # Action
    # --------------------------------------------------------------

    action = os.getenv(
        "SCANNER_ACTION",
        "scan",
    ).strip().lower()

    LOG.info(
        "Scanner action: %s",
        action,
    )

    # --------------------------------------------------------------
    # Dhan
    # --------------------------------------------------------------

    dhan = DhanClient()

    # --------------------------------------------------------------
    # Same-day state
    # --------------------------------------------------------------

    state = load(today)

    # --------------------------------------------------------------
    # Universe
    # --------------------------------------------------------------

    if action == "universe":

        create_universe(
            dhan,
            state,
            as_of_date=today,
        )

        return

    # --------------------------------------------------------------
    # Scan / Monitor
    # --------------------------------------------------------------

    if action in {
        "scan",
        "monitor",
    }:

        if not validate_scan_time(
            scan_time
        ):
            return

        if not state["universe"]:

            LOG.info(
                "Universe is empty. "
                "Creating daily universe."
            )

            create_universe(
                dhan,
                state,
                as_of_date=today,
            )

            state = load(today)

        if not state["universe"]:

            LOG.error(
                "Universe creation returned zero symbols."
            )

            return

        run_scan(
            dhan=dhan,
            state=state,
            scan_time=scan_time,
        )

        return

    # --------------------------------------------------------------
    # Summary
    # --------------------------------------------------------------

    if action == "summary":

        LOG.info(
            "Summary action requested."
        )

        return

    # --------------------------------------------------------------
    # Unknown action
    # --------------------------------------------------------------

    LOG.error(
        "Unknown SCANNER_ACTION=%s",
        action,
    )


if __name__ == "__main__":
    main()
