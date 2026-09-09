from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .calendar import is_nse_trading_day
from .config import SETTINGS
from .dhan_client import DhanClient
from .indicators import add_indicators
from .nse_universe import build_universe, refresh_dynamic_volume_gainers
from .scoring import score_signal
from .state import (
    load,
    save,
    backup_and_clear,
    key,
    alert_allowed,
    record_alert,
    active_signal_for_symbol,
    reverse_active_signal,
)
from .strategy import evaluate
from .summary import build_summary
from .telegram import send, signal_message, exit_message

IST = ZoneInfo("Asia/Kolkata")
LOG = logging.getLogger(__name__)


def now():
    return datetime.now(IST)


def format_signal_time(value):
    """Convert a candle timestamp to a readable IST display string."""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value)
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)

    dt = dt.astimezone(IST)
    return dt.strftime("%d %b %Y, %I:%M %p IST")


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

    data = response.get("data", response) if isinstance(response, dict) else {}
    block = data.get("NSE_EQ", {}) if isinstance(data, dict) else {}
    return block if isinstance(block, dict) else {}


def create_universe(dhan, state):
    LOG.info(
        "CREATE_UNIVERSE START | existing universe=%d",
        len(state.get("universe", [])),
    )
    if state["universe"]:
        LOG.info("CREATE_UNIVERSE SKIPPED | universe already exists")
        return

    state["universe"] = build_universe(dhan)
    save(state)
    LOG.info("Universe size: %d", len(state["universe"]))


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

            score, grade = score_signal(result["regime"], result)

            signal = {
                "symbol": symbol,
                "security_id": item["security_id"],
                "direction": result["direction"],
                "setup": result["setup"],
                "signal_time": format_signal_time(result["candle_time"]),
                "signal_price": result["signal_price"],
                "score": score,
                "grade": grade,
                "status": "ACTIVE",
                "risk": result["risk"],
                "rvol": result["rvol"],
                "rsi": result["rsi"],
                "regime": result["regime"],
            }

            # The state.py alert filter requires the current timestamp
            # and the Settings object.
            allowed, reason = alert_allowed(
                state,
                signal,
                ts,
                SETTINGS,
            )

            if not allowed:
                LOG.info(
                    "%s | ALERT SUPPRESSED | %s",
                    symbol,
                    reason,
                )
                continue

            # Prefer live LTP. If the separate quote endpoint does not
            # return an LTP, use the completed 5M candle close.
            q = ltp_batch(
                dhan,
                [item["security_id"]],
            ).get(str(item["security_id"]), {})

            ltp = q.get("last_price", q.get("ltp"))

            if ltp is not None:
                signal["ltp"] = float(ltp)
            else:
                signal["ltp"] = float(result["signal_price"])
                LOG.info(
                    "%s | LTP unavailable | using 5M close %.2f",
                    symbol,
                    signal["ltp"],
                )

            k = key(
                symbol,
                signal["direction"],
                signal["setup"],
                signal["signal_time"],
            )

            if k in state["signals"]:
                LOG.info(
                    "%s | ALERT SUPPRESSED | duplicate signal",
                    symbol,
                )
                continue

            # Send first. Change the previous active signal only after
            # Telegram successfully accepts the new alert.
            if send(signal_message(signal)):
                previous = active_signal_for_symbol(
                    state,
                    symbol,
                )

                if (
                    previous
                    and previous.get("direction") != signal["direction"]
                ):
                    reverse_active_signal(
                        state,
                        symbol,
                        signal["direction"],
                        ts,
                    )

                state["signals"][k] = signal
                record_alert(state, signal, ts)
                changed = True

                LOG.info(
                    "%s | ALERT SENT | %s | score=%s | RVOL=%.2f",
                    symbol,
                    signal["direction"],
                    signal["score"],
                    signal["rvol"],
                )

        except Exception:
            LOG.exception("Scan failed: %s", symbol)

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

    quote_ids = [
        s.get("security_id")
        for s in active
        if s.get("security_id") is not None
    ]
    quotes = ltp_batch(dhan, quote_ids) if quote_ids else {}

    changed = False

    for s in active:
        security_id = s.get("security_id")
        q = quotes.get(str(security_id), {}) if security_id is not None else {}
        px = q.get("last_price", q.get("ltp"))

        if px is None:
            # Legacy signals have no security_id; do not crash the monitor.
            continue

        px = float(px)
        reason = None

        if s["direction"] == "BUY" and px <= s["risk"]["sl"]:
            reason = "Stop loss reached"

        if s["direction"] == "SELL" and px >= s["risk"]["sl"]:
            reason = "Stop loss reached"

        if reason and send(
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

    # Backward compatibility: older ACTIVE signals may not have a
    # security_id because that field was added in a later version.
    # Never let one legacy signal crash the EOD summary.
    quote_ids = [
        s.get("security_id")
        for s in active
        if s.get("security_id") is not None
    ]
    quotes = ltp_batch(dhan, quote_ids) if quote_ids else {}

    prices = {}

    for s in active:
        security_id = s.get("security_id")
        q = quotes.get(str(security_id), {}) if security_id is not None else {}
        px = q.get("last_price", q.get("ltp"))

        # Legacy signals: use the last stored price if live LTP is
        # unavailable. This keeps the summary useful and prevents a crash.
        if px is None:
            px = s.get("ltp", s.get("signal_price"))

        if px is not None:
            px = float(px)
            s["status"] = "CLOSED_EOD"
            s["exit_price"] = px
            s["exit_time"] = ts.isoformat()
            s["exit_reason"] = "END_OF_DAY"
            prices[s["symbol"]] = px

    send(build_summary(state, prices))

    backup_and_clear(state, ts.date())


def main():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO")
    )

    ts = now()
    action = os.getenv("SCANNER_ACTION", "auto").lower()
    hhmm = ts.hour * 100 + ts.minute

    LOG.info(
        "MAIN START | date=%s | time=%s | action=%s",
        ts.date(),
        ts.strftime("%H:%M:%S"),
        action,
    )
    
    if not is_nse_trading_day(ts.date()):
        LOG.info("Not an NSE trading day")
        return

    dhan = DhanClient()
    state = load(ts.date())

    LOG.info(
        "STATE LOADED | date=%s | universe=%d | signals=%d",
        state.get("date"),
        len(state.get("universe", [])),
        len(state.get("signals", {})),
    )
    
    # Refresh NSE Volume Gainers every 10 minutes and append newly
    # qualifying stocks to today's existing universe.
    if action != "universe" and hhmm >= 925 and hhmm <= 1505:
        refresh_dynamic_volume_gainers(dhan, state, ts)
        save(state)

    if action == "universe":
        create_universe(dhan, state)
        return

    if not state["universe"]:
        LOG.warning(
            "Universe missing; refusing to scan"
        )
        return

    if action == "scan" or (
        action == "auto" and 925 <= hhmm <= 1505
    ):
        scan(dhan, state, ts)

    elif action == "monitor" or (
        action == "auto" and 1510 <= hhmm <= 1525
    ):
        monitor(dhan, state, ts)

    elif action == "summary" or (
        action == "auto" and hhmm == 1530
    ):
        summary(dhan, state, ts)


if __name__ == "__main__":
    main()
