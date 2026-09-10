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


def explain_alert_reason(signal, reason):
    """Convert the gate reason into a clear log message."""
    score = int(signal.get("score", 0))
    rvol = float(signal.get("rvol", 0.0))
    direction = str(signal.get("direction", "")).upper()
    regime_direction = str((signal.get("regime") or {}).get("direction", "")).upper()
    max_entry = signal.get("max_entry")
    ltp = signal.get("ltp")

    if reason == "below alert score/grade threshold":
        return f"score<{SETTINGS.alert_min_score}"
    if reason == "below minimum RVOL":
        return f"RVOL<{SETTINGS.alert_min_rvol:.2f}"
    if reason == "extreme RVOL requires review":
        return f"RVOL>{SETTINGS.alert_max_rvol:.2f}"
    if reason == "signal/regime direction mismatch":
        return f"direction={direction} vs regime={regime_direction}"
    if reason == "15M structure not confirmed":
        return "15M structure not confirmed"
    if reason == "entry is too far from breakout":
        return f"ltp={ltp} vs max_entry={max_entry}"
    if reason == "same-direction cooldown":
        return "same-direction cooldown"
    if reason == "no meaningful score improvement":
        return "no meaningful score improvement"
    if reason == "reversal score too low":
        return f"reversal score<{SETTINGS.alert_reversal_min_score}"
    if reason == "invalid RVOL":
        return "RVOL is invalid"
    if reason == "approved":
        return "approved"
    if reason == "duplicate signal":
        return "duplicate signal"
    return reason


def format_alert_decision(symbol, signal, allowed, reason):
    score = int(signal.get("score", 0))
    verdict = "ALERT=QUALIFIED" if allowed else "ALERT=SUPPRESSED"
    action = "qualified and sent" if allowed else "rejected"

    if not allowed and reason == "below alert score/grade threshold":
        detail = f"score<{SETTINGS.alert_min_score}"
    elif allowed and score >= SETTINGS.alert_min_score:
        detail = f"score>={SETTINGS.alert_min_score}"
    else:
        detail = explain_alert_reason(signal, reason)

    return (
        f"{symbol} | SIGNAL | score={score} | "
        f"{verdict} | {detail} - {action}"
    )


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


def live_ltp(dhan, security_id):
    quote = ltp_batch(dhan, [security_id]).get(str(security_id), {})
    value = quote.get("last_price", quote.get("ltp"))
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def completed_candles(df, ts, interval):
    if df.empty:
        return df

    cutoff = ts.replace(second=0, microsecond=0)
    if interval == 1:
        return df[df.index < cutoff]
    return df[df.index <= cutoff]


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
            df1 = completed_candles(
                add_indicators(
                    dhan.intraday_df(
                        item["security_id"],
                        1,
                        start.strftime("%Y-%m-%d"),
                        ts.strftime("%Y-%m-%d"),
                    ),
                    SETTINGS.rvol_lookback,
                ),
                ts,
                1,
            )

            df5 = completed_candles(
                add_indicators(
                    dhan.intraday_df(
                        item["security_id"],
                        5,
                        start.strftime("%Y-%m-%d"),
                        ts.strftime("%Y-%m-%d"),
                    ),
                    SETTINGS.rvol_lookback,
                ),
                ts,
                5,
            )

            df15 = completed_candles(
                add_indicators(
                    dhan.intraday_df(
                        item["security_id"],
                        15,
                        start.strftime("%Y-%m-%d"),
                        ts.strftime("%Y-%m-%d"),
                    ),
                    SETTINGS.rvol_lookback,
                ),
                ts,
                15,
            )

            rejection = []
            result = evaluate(df1, df5, df15, rejection)
            if not result:
                if (
                    len(df1) < SETTINGS.min_1m_candles
                    or len(df5) < SETTINGS.min_5m_candles
                    or len(df15) < SETTINGS.min_15m_candles
                ):
                    LOG.warning(
                        "%s | NO SIGNAL | insufficient candles | "
                        "1M=%d/%d | 5M=%d/%d | 15M=%d/%d",
                        symbol,
                        len(df1),
                        SETTINGS.min_1m_candles,
                        len(df5),
                        SETTINGS.min_5m_candles,
                        len(df15),
                        SETTINGS.min_15m_candles,
                    )
                    continue

                LOG.info(
                    "%s | NO SIGNAL | %s",
                    symbol,
                    rejection[0] if rejection else "evaluation rejected",
                )
                continue

            score, grade = score_signal(result["regime"], result)
            LOG.info(
                "%s | SIGNAL CANDIDATE | score=%s | grade=%s | direction=%s | setup=%s | RVOL=%.2f",
                symbol,
                score,
                grade,
                result["direction"],
                result["setup"],
                result["rvol"],
            )

            signal = {
                "symbol": symbol,
                "security_id": item["security_id"],
                "direction": result["direction"],
                "setup": result["setup"],
                "signal_time": format_signal_time(result["entry_candle_time"]),
                "signal_price": result["signal_price"],
                "score": score,
                "grade": grade,
                "status": "ACTIVE",
                "risk": result["risk"],
                "rvol": result["rvol"],
                "entry_rvol": result["entry_rvol"],
                "rsi": result["rsi"],
                "atr": result["atr"],
                "risk_atr_ratio": result["risk_atr_ratio"],
                "max_entry": result["max_entry"],
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
                    "%s | PRE_ALERT_GATE | score=%s | grade=%s | RVOL=%.2f | reason=%s",
                    symbol,
                    signal.get("score"),
                    signal.get("grade"),
                    signal.get("rvol", 0.0),
                    reason,
                )
                LOG.info(
                    "%s",
                    format_alert_decision(symbol, signal, False, reason),
                )
                continue

            ltp = live_ltp(dhan, item["security_id"])
            if ltp is None:
                LOG.info(
                    "%s | POST_LTP_ALERT_GATE | live LTP unavailable | alert suppressed",
                    symbol,
                )
                continue
            signal["ltp"] = ltp

            allowed, reason = alert_allowed(
                state,
                signal,
                ts,
                SETTINGS,
            )
            if not allowed:
                LOG.info(
                    "%s | POST_LTP_ALERT_GATE | score=%s | grade=%s | ltp=%s | max_entry=%s | reason=%s",
                    symbol,
                    signal.get("score"),
                    signal.get("grade"),
                    signal.get("ltp"),
                    signal.get("max_entry"),
                    reason,
                )
                LOG.info(
                    "%s",
                    format_alert_decision(symbol, signal, False, reason),
                )
                continue

            k = key(
                symbol,
                signal["direction"],
                signal["setup"],
                signal["signal_time"],
            )

            if k in state["signals"]:
                LOG.info(
                    "%s",
                    format_alert_decision(symbol, signal, False, "duplicate signal"),
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
                        signal.get("ltp"),
                    )

                state["signals"][k] = signal
                record_alert(state, signal, ts)
                changed = True

                LOG.info(
                    "%s",
                    format_alert_decision(symbol, signal, True, "approved"),
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

        if (
            reason is None
            and s["direction"] == "BUY"
            and px >= s["risk"]["t1"]
        ):
            reason = "Target 1 reached"

        if (
            reason is None
            and s["direction"] == "SELL"
            and px <= s["risk"]["t1"]
        ):
            reason = "Target 1 reached"

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
            risk = float(s["risk"]["risk"])
            entry = float(s["risk"]["entry"])
            move = px - entry if s["direction"] == "BUY" else entry - px
            s["r_multiple"] = move / risk if risk > 0 else None
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

    scan_window = (
        SETTINGS.scan_start_hhmm
        <= hhmm
        <= SETTINGS.scan_end_hhmm
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
    if action != "universe" and scan_window:
        refresh_dynamic_volume_gainers(dhan, state, ts)
        save(state)

    if action == "universe":
        create_universe(dhan, state)
        return

    if action == "summary" or (
        action == "auto" and hhmm == 1525
    ):
        summary(dhan, state, ts)
        return

    if not state["universe"]:
        LOG.warning(
            "Universe missing; refusing to scan"
        )
        return

    if action in {"scan", "auto"} and scan_window:
        monitor(dhan, state, ts)
        scan(dhan, state, ts)

    elif action == "scan":
        LOG.info(
            "Scan skipped outside window %04d-%04d",
            SETTINGS.scan_start_hhmm,
            SETTINGS.scan_end_hhmm,
        )

    elif action == "monitor" or (
        action == "auto" and 1510 <= hhmm <= 1525
    ):
        monitor(dhan, state, ts)

if __name__ == "__main__":
    main()
