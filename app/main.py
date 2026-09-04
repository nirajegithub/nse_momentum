from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .calendar import is_nse_trading_day
from .config import SETTINGS
from .dhan_client import DhanClient
from .indicators import add_indicators
from .nse_universe import build_universe
from .scoring import score_signal
from .state import load, save, key, active_for_symbol
from .strategy import evaluate
from .summary import build_summary
from .telegram import send, signal_message, exit_message


IST = ZoneInfo("Asia/Kolkata")
LOG = logging.getLogger(__name__)


def now():
    return datetime.now(IST)


def ltp_batch(dhan, ids):
    if not ids:
        return {}

    response = dhan.dhan.ohlc_data(
        securities={
            "NSE_EQ": [
                int(x) if str(x).isdigit() else str(x)
                for x in ids
            ]
        }
    )

    data = (
        response.get("data", response)
        if isinstance(response, dict)
        else {}
    )

    block = (
        data.get("NSE_EQ", {})
        if isinstance(data, dict)
        else {}
    )

    return block if isinstance(block, dict) else {}


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


def scan(dhan, state, ts):
    start = ts - timedelta(days=7)
    changed = False

    for item in state["universe"]:
        symbol = item["symbol"]

        try:
            df5 = add_indicators(
                dhan.intraday_df(
                    item["security_id"],
                    5,
                    start.strftime("%Y-%m-%d"),
                    ts.strftime("%Y-%m-%d"),
                ),
                SETTINGS.rvol_lookback,
            )

            df15 = add_indicators(
                dhan.intraday_df(
                    item["security_id"],
                    15,
                    start.strftime("%Y-%m-%d"),
                    ts.strftime("%Y-%m-%d"),
                ),
                SETTINGS.rvol_lookback,
            )

            result = evaluate(df5, df15)

            if not result:
                continue

            score, grade = score_signal(
                result["regime"],
                result,
            )

            if grade not in {"A", "A+"}:
                continue

            if active_for_symbol(state, symbol):
                continue

            q = ltp_batch(
                dhan,
                [item["security_id"]],
            ).get(
                str(item["security_id"]),
                {},
            )

            ltp = q.get(
                "last_price",
                q.get("ltp"),
            )

            if ltp is None:
                continue

            s = {
                "symbol": symbol,
                "security_id": item["security_id"],
                "direction": result["direction"],
                "setup": result["setup"],
                "signal_time": result["candle_time"],
                "signal_price": result["signal_price"],
                "ltp": float(ltp),
                "score": score,
                "grade": grade,
                "status": "ACTIVE",
                "risk": result["risk"],
                "rvol": result["rvol"],
                "rsi": result["rsi"],
                "regime": result["regime"],
            }

            k = key(
                symbol,
                s["direction"],
                s["setup"],
                s["signal_time"],
            )

            if k in state["signals"]:
                continue

            if send(signal_message(s)):
                state["signals"][k] = s
                changed = True

        except Exception:
            LOG.exception(
                "Scan failed: %s",
                symbol,
            )

    if changed:
        save(state)


def monitor(dhan, state, ts):
    active = [
        s
        for s in state["signals"].values()
        if s.get("status") == "ACTIVE"
    ]

    if not active:
        return

    quotes = ltp_batch(
        dhan,
        [s["security_id"] for s in active],
    )

    changed = False

    for s in active:
        q = quotes.get(
            str(s["security_id"]),
            {},
        )

        px = q.get(
            "last_price",
            q.get("ltp"),
        )

        if px is None:
            continue

        px = float(px)
        reason = None

        if (
            s["direction"] == "BUY"
            and px <= s["risk"]["sl"]
        ):
            reason = "Stop loss reached"

        if (
            s["direction"] == "SELL"
            and px >= s["risk"]["sl"]
        ):
            reason = "Stop loss reached"

        if reason:
            if send(
                exit_message(
                    s,
                    px,
                    reason,
                    ts.strftime("%H:%M:%S"),
                )
            ):
                s["status"] = "EXITED"
                s["exit_price"] = px
                s["exit_time"] = ts.isoformat()
                s["exit_reason"] = reason
                changed = True

    if changed:
        save(state)


def summary(dhan, state, ts):
    active = [
        s
        for s in state["signals"].values()
        if s.get("status") == "ACTIVE"
    ]

    quotes = ltp_batch(
        dhan,
        [s["security_id"] for s in active],
    )

    prices = {}

    for s in active:
        q = quotes.get(
            str(s["security_id"]),
            {},
        )

        px = q.get(
            "last_price",
            q.get("ltp"),
        )

        if px is not None:
            s["status"] = "CLOSED_EOD"
            s["exit_price"] = float(px)
            s["exit_time"] = ts.isoformat()
            s["exit_reason"] = "END_OF_DAY"

            prices[s["symbol"]] = float(px)

    send(
        build_summary(
            state,
            prices,
        )
    )

    save(
        {
            "date": "",
            "universe": [],
            "signals": {},
        }
    )


def main():
    logging.basicConfig(
        level=os.getenv(
            "LOG_LEVEL",
            "INFO",
        )
    )

    ts = now()

    action = os.getenv(
        "SCANNER_ACTION",
        "auto",
    ).lower()

    hhmm = ts.hour * 100 + ts.minute

    # ---------------------------------------------------------
    # TEST DATE OVERRIDE
    # ---------------------------------------------------------

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
                "Invalid TEST_DATE=%s. Expected YYYY-MM-DD.",
                test_date,
            )
            return
    else:
        today = ts.date()

    # ---------------------------------------------------------
    # NSE TRADING DAY CHECK
    # ---------------------------------------------------------

    if not is_nse_trading_day(today):
        LOG.info(
            "Not an NSE trading day: %s",
            today.isoformat(),
        )
        return

    LOG.info(
        "NSE trading date: %s",
        today.isoformat(),
    )

    # ---------------------------------------------------------
    # CLIENT + STATE
    # ---------------------------------------------------------

    dhan = DhanClient()

    state = load(today)

    # ---------------------------------------------------------
    # ACTION: UNIVERSE
    # ---------------------------------------------------------

    if action == "universe":
        create_universe(
            dhan,
            state,
            today,
        )
        return

    # ---------------------------------------------------------
    # SAFETY: UNIVERSE MUST EXIST
    # ---------------------------------------------------------

    if not state["universe"]:
        LOG.warning(
            "Universe missing; refusing to scan"
        )
        return

    # ---------------------------------------------------------
    # SCAN
    # ---------------------------------------------------------

    if (
        action == "scan"
        or (
            action == "auto"
            and 925 <= hhmm <= 1505
        )
    ):
        scan(
            dhan,
            state,
            ts,
        )

    # ---------------------------------------------------------
    # MONITOR
    # ---------------------------------------------------------

    elif (
        action == "monitor"
        or (
            action == "auto"
            and 1510 <= hhmm <= 1525
        )
    ):
        monitor(
            dhan,
            state,
            ts,
        )

    # ---------------------------------------------------------
    # SUMMARY
    # ---------------------------------------------------------

    elif (
        action == "summary"
        or (
            action == "auto"
            and hhmm == 1530
        )
    ):
        summary(
            dhan,
            state,
            ts,
        )


if __name__ == "__main__":
    main()
