import logging
import os
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import SETTINGS
from app.dhan_client import DhanClient
from app.indicators import add_indicators
from app.strategy import evaluate
from app.scoring import score_signal
from app.state import load, save
from app.nse_universe import build_universe
from app.telegram import send, signal_message, exit_message

IST = ZoneInfo("Asia/Kolkata")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def get_scan_time() -> datetime:
    test_datetime = os.getenv("TEST_DATETIME", "").strip()
    if test_datetime:
        return datetime.strptime(
            test_datetime, "%Y-%m-%d %H:%M"
        ).replace(tzinfo=IST)
    return datetime.now(IST)


def get_completed_5m_cutoff(scan_time: datetime) -> datetime:
    minute = (scan_time.minute // 5) * 5
    return scan_time.replace(
        minute=minute, second=0, microsecond=0
    )


def get_completed_15m_cutoff(scan_time: datetime):
    if scan_time.hour == 9 and scan_time.minute < 30:
        return None

    minute = (scan_time.minute // 15) * 15
    return scan_time.replace(
        minute=minute, second=0, microsecond=0
    )


def validate_scan_time(scan_time: datetime) -> bool:
    """
    Validate the invocation time without failing the GitHub/cron job.

    If the workflow is manually triggered or accidentally invoked outside
    the NSE session, log a clean SKIP and return False so GitHub Actions
    finishes successfully with exit code 0.

    Valid application window:
      09:15 IST through 15:29 IST

    15:30 IST is handled by the summary workflow.
    """
    market_open = scan_time.replace(
        hour=9, minute=15, second=0, microsecond=0
    )
    market_close = scan_time.replace(
        hour=15, minute=30, second=0, microsecond=0
    )

    if scan_time < market_open or scan_time >= market_close:
        logger.info(
            "SKIP | Outside NSE session | scan_time=%s | "
            "valid_window=09:15-15:30 IST",
            scan_time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        )
        return False

    return True


def get_scan_day(scan_date: str):
    return datetime.strptime(scan_date, "%Y-%m-%d").date()


def create_universe(dhan: DhanClient, scan_date: str):
    logger.info("Creating daily universe for %s", scan_date)

    scan_day = get_scan_day(scan_date)
    state = load(scan_day)

    universe = build_universe(dhan, as_of_date=scan_date)

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
    scan_dt = datetime.strptime(scan_date, "%Y-%m-%d")

    from_date = (
        scan_dt - timedelta(days=7)
    ).strftime("%Y-%m-%d")

    # Dhan toDate is non-inclusive.
    to_date = (
        scan_dt + timedelta(days=1)
    ).strftime("%Y-%m-%d")

    return from_date, to_date


def to_naive_ist(index):
    if getattr(index, "tz", None) is not None:
        return index.tz_convert(IST).tz_localize(None)
    return index


def filter_completed_candles(df, scan_date: str, cutoff, timeframe: str):
    if df is None or df.empty:
        return df

    df = df.copy()
    df.index = to_naive_ist(df.index)

    scan_day = get_scan_day(scan_date)

    # Never allow candles from another date into this scan.
    df = df[df.index.date == scan_day]

    # At 09:25 there is no completed current-day 15M candle.
    if cutoff is None:
        logger.info(
            "%s completed candles | date=%s | cutoff=NONE | rows=0 | latest=NONE",
            timeframe,
            scan_date,
        )
        return df.iloc[0:0].copy()

    cutoff_naive = cutoff.replace(tzinfo=None)
    df = df[df.index <= cutoff_naive]

    logger.info(
        "%s completed candles | date=%s | cutoff=%s | rows=%d | latest=%s",
        timeframe,
        scan_date,
        cutoff.strftime("%H:%M"),
        len(df),
        df.index[-1] if not df.empty else "NONE",
    )
    return df


def prepare_indicator_data(df, scan_date, cutoff, timeframe):
    """
    Keep historical candles for indicator warm-up, while never allowing
    current-day candles beyond the completed-candle cutoff.

    Indicators are calculated over historical + allowed current-day candles.
    VWAP is reset per trading session.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    x = df.copy()
    x.index = to_naive_ist(x.index)
    x = x.sort_index()
    x = x[~x.index.duplicated(keep="last")]

    scan_day = datetime.strptime(
        scan_date,
        "%Y-%m-%d",
    ).date()

    if cutoff is None:
        x = x[x.index.date < scan_day]
    else:
        cutoff_naive = cutoff.replace(tzinfo=None)
        x = x[
            (x.index.date < scan_day)
            | (
                (x.index.date == scan_day)
                & (x.index <= cutoff_naive)
            )
        ]

    if x.empty:
        return x

    result = add_indicators(
        x,
        rvol_lookback=SETTINGS.rvol_lookback,
    )

    if result.empty:
        return result

    session_date = pd.Series(
        result.index.date,
        index=result.index,
    )

    typical = (
        result["high"]
        + result["low"]
        + result["close"]
    ) / 3

    result["vwap"] = (
        (typical * result["volume"]).groupby(session_date).cumsum()
        / result["volume"].groupby(session_date).cumsum().replace(0, float("nan"))
    )

    return result


def fetch_symbol_data(dhan: DhanClient, security_id, scan_date: str):
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


def signal_key(symbol, signal):
    return (
        f"{symbol}|{signal['direction']}|{signal['setup']}|"
        f"{signal['candle_time']}"
    )


def build_signal(symbol, signal, score, grade):
    result = dict(signal)
    result["symbol"] = symbol
    result["score"] = int(score)
    result["grade"] = grade
    result["ltp"] = result["signal_price"]
    result["signal_time"] = result["candle_time"]
    result["status"] = "ACTIVE"
    result["setup_key"] = signal_key(symbol, result)
    return result



def process_signal(symbol, df5, df15):
    if df5 is None or df15 is None or df5.empty or df15.empty:
        logger.info("%s | indicator data unavailable", symbol)
        return None

    if (
        len(df5) < SETTINGS.min_5m_candles
        or len(df15) < SETTINGS.min_15m_candles
    ):
        logger.info(
            "%s | insufficient indicator candles | 5M=%d | 15M=%d",
            symbol,
            len(df5),
            len(df15),
        )
        return None

    signal = evaluate(df5, df15)
    if not signal:
        logger.info(
            "%s | no valid setup | 15M regime=%s",
            symbol,
            df15.iloc[-1].get("direction", "N/A")
            if not df15.empty else "N/A",
        )
        return None

    score, grade = score_signal(
        signal["regime"],
        signal,
    )

    # Build the complete signal object expected by Telegram/state.
    signal = build_signal(
        symbol,
        signal,
        score,
        grade,
    )

    logger.info(
        "%s | SIGNAL | direction=%s | setup=%s | "
        "score=%d | grade=%s | candle=%s | "
        "rsi=%.2f | rvol=%.2f | entry=%.2f | sl=%.2f",
        symbol,
        signal["direction"],
        signal["setup"],
        score,
        grade,
        signal["candle_time"],
        signal["rsi"],
        signal["rvol"],
        signal["risk"]["entry"],
        signal["risk"]["sl"],
    )

    return signal


def run_scan(dhan: DhanClient, scan_time: datetime):
    scan_date = scan_time.strftime("%Y-%m-%d")
    scan_day = get_scan_day(scan_date)

    cutoff_5m = get_completed_5m_cutoff(scan_time)
    cutoff_15m = get_completed_15m_cutoff(scan_time)

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
            "(first 15M candle completes at 09:30)",
        )

    state = load(scan_day)
    universe = state.get("universe", [])

    if not universe:
        logger.warning("Universe is empty. Creating universe.")
        universe = create_universe(dhan, scan_date)

    logger.info(
        "Scanning fixed daily universe | count=%d",
        len(universe),
    )

    successful = 0
    skipped_15m = 0
    signals_sent = 0

    for item in universe:
        symbol, security_id = get_symbol_and_security_id(item)

        if not symbol or not security_id:
            logger.warning(
                "Skipping invalid universe item: %s",
                item,
            )
            continue

        try:
            df_5m_raw, df_15m_raw = fetch_symbol_data(
                dhan,
                security_id,
                scan_date,
            )

            # Current-day completed candles: used only to identify the latest
            # completed signal candle and enforce the cutoff.
            df_5m = filter_completed_candles(
                df_5m_raw,
                scan_date,
                cutoff_5m,
                "5M",
            )
            df_15m = filter_completed_candles(
                df_15m_raw,
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

            # Before 09:30 there is no completed current-day 15M candle,
            # therefore no technical signal can be generated yet.
            if cutoff_15m is None or latest_15m is None:
                skipped_15m += 1
                continue

            # Build indicator-ready history: prior trading sessions plus
            # today's completed candles only.
            df_5m_ind = prepare_indicator_data(
                df_5m_raw,
                scan_date,
                cutoff_5m,
                "5M",
            )
            df_15m_ind = prepare_indicator_data(
                df_15m_raw,
                scan_date,
                cutoff_15m,
                "15M",
            )

            logger.info(
                "%s | indicator-ready | 5M=%d | 15M=%d | "
                "5M latest=%s | 15M latest=%s",
                symbol,
                len(df_5m_ind),
                len(df_15m_ind),
                df_5m_ind.index[-1] if not df_5m_ind.empty else "NONE",
                df_15m_ind.index[-1] if not df_15m_ind.empty else "NONE",
            )

            signal = process_signal(
                symbol,
                df_5m_ind,
                df_15m_ind,
            )

            if not signal:
                continue

            # Keep the existing duplicate/state behavior in the baseline if
            # present in the generated main.py.
            state_key = (
                f"{symbol}|{signal['direction']}|"
                f"{signal['setup']}|{signal['candle_time']}"
            )

            if state_key in state.get("signals", {}):
                logger.info(
                    "%s | duplicate signal skipped | key=%s",
                    symbol,
                    state_key,
                )
                continue

            state.setdefault("signals", {})[state_key] = signal

            try:
                send(signal_message(signal))
                signals_sent += 1
            except Exception:
                logger.exception(
                    "%s | Telegram send failed",
                    symbol,
                )

        except Exception:
            logger.exception(
                "Failed processing %s | security_id=%s",
                symbol,
                security_id,
            )

    save(state)

    logger.info(
        "SCAN COMPLETE | successful=%d | "
        "15M unavailable=%d | signals_sent=%d",
        successful,
        skipped_15m,
        signals_sent,
    )

def run_monitor(dhan: DhanClient, scan_time: datetime):
    """Monitor active signals. No new signal creation here."""
    scan_date = scan_time.strftime("%Y-%m-%d")
    scan_day = get_scan_day(scan_date)

    logger.info(
        "MONITOR | %s",
        scan_time.strftime("%Y-%m-%d %H:%M"),
    )

    state = load(scan_day)
    signals = state.get("signals", {})

    logger.info("Active signals=%d", len(signals))

    if not signals:
        return

    # V1 monitor keeps active signals in state. Exit automation can be
    # added once the live LTP/quote path is enabled.
    for key, signal in signals.items():
        if signal.get("status") == "ACTIVE":
            logger.info(
                "ACTIVE | %s | %s | entry=%.2f | SL=%.2f | key=%s",
                signal.get("symbol"),
                signal.get("direction"),
                signal["risk"]["entry"],
                signal["risk"]["sl"],
                key,
            )


def run_summary(dhan: DhanClient, scan_time: datetime):
    scan_date = scan_time.strftime("%Y-%m-%d")
    scan_day = get_scan_day(scan_date)

    logger.info(
        "SUMMARY | %s",
        scan_time.strftime("%Y-%m-%d %H:%M"),
    )

    state = load(scan_day)
    signals = state.get("signals", {})

    logger.info(
        "Summary | universe=%d | signals=%d",
        len(state.get("universe", [])),
        len(signals),
    )

    # Keep summary simple until live exit/quote monitoring is enabled.
    for signal in signals.values():
        logger.info(
            "SIGNAL | %s | %s | %s | score=%s | entry=%.2f",
            signal.get("symbol"),
            signal.get("direction"),
            signal.get("grade"),
            signal.get("score"),
            signal["risk"]["entry"],
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
        create_universe(dhan, scan_date)

    elif action == "scan":
        run_scan(dhan, scan_time)

    elif action == "monitor":
        run_monitor(dhan, scan_time)

    elif action == "summary":
        run_summary(dhan, scan_time)

    else:
        raise ValueError(
            f"Unknown SCANNER_ACTION: {action}"
        )


if __name__ == "__main__":
    main()
