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
from .state import (
    load,
    save,
    backup_and_clear,
    key,
    active_signal_for_symbol,
    reverse_active_signal,
)
from .strategy import evaluate_setup, confirm_setup
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
    """Evaluate each new completed 5M candle and confirm pending setups every minute."""
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

            if df5.empty or df1.empty:
                LOG.info("%s | NO SIGNAL | insufficient completed candles", symbol)
                continue
            setup_timestamp = df5.index[-1].isoformat()
            if state["processed_5m_candles"].get(symbol) != setup_timestamp:
                old = state["pending_setups"].pop(symbol, None)
                if old:
                    LOG.info("%s | SETUP_EXPIRED | direction=%s | setup_time=%s | reason=NEXT_5M_CANDLE", symbol, old["direction"], old["setup_5m_timestamp"])
                state["processed_5m_candles"][symbol] = setup_timestamp
                rejection = []
                setup = evaluate_setup(df5, item.get("prev_close"), item.get("prev_volume"), rejection)
                if setup:
                    setup.update({"symbol": symbol, "security_id": item["security_id"]})
                    state["pending_setups"][symbol] = setup
                    LOG.info("%s | 5M_SETUP | direction=%s | candle=%s | ema9=%.2f | ema20=%.2f | rsi=%.2f | prev_rsi=%.2f | vwap=%.2f | volume=%.0f | avg_volume20=%.0f | rvol=%.2f | daily_close=%.2f | daily_volume=%.0f | high=%.2f | low=%.2f | close=%.2f", symbol, setup["direction"], setup_timestamp, setup["setup_5m_ema9"], setup["setup_5m_ema20"], setup["setup_5m_rsi14"], setup["setup_5m_previous_rsi14"], setup["setup_5m_vwap"], setup["setup_5m_volume"], setup["setup_5m_avg_volume"], setup["setup_5m_rvol"], setup["daily_close"], setup["daily_volume"], setup["setup_5m_high"], setup["setup_5m_low"], setup["setup_5m_close"])
                elif rejection:
                    LOG.info("%s | NO_5M_SETUP | %s", symbol, rejection[0])
                changed = True

            setup = state["pending_setups"].get(symbol)
            if not setup:
                continue
            candle = df1.iloc[-1]
            confirmation_time = (df1.index[-1] + timedelta(minutes=1)).isoformat()
            confirmed = confirm_setup(setup, candle, confirmation_time)
            if not confirmed:
                LOG.info("%s | NO_1M_CONFIRMATION | close=%.2f | required=%s%.2f", symbol, candle.close, ">" if setup["direction"] == "BUY" else "<", setup["breakout_level"])
                LOG.info("%s | WAITING_1M_CONFIRMATION | direction=%s | setup_time=%s | breakout_level=%.2f | stop_loss=%.2f", symbol, setup["direction"], setup["setup_5m_timestamp"], setup["breakout_level"], setup["stop_loss"])
                continue
            k = key(symbol, setup["direction"], "5M_QUALITY", setup["setup_5m_timestamp"])
            if k in state["signals"]:
                state["pending_setups"].pop(symbol, None)
                changed = True
                continue
            entry, sl = confirmed["entry"], confirmed["stop_loss"]
            risk = abs(entry - sl)
            targets = [entry + (1.5 + step) * risk if setup["direction"] == "BUY" else entry - (1.5 + step) * risk for step in (0, 1, 2)]
            signal = {**setup, "setup": "5M_QUALITY", "signal_time": format_signal_time(confirmed["confirmation_time"]), "setup_time": format_signal_time(setup["setup_5m_timestamp"]), "signal_price": entry, "status": "ACTIVE", "score": 0, "grade": "QUALITY", "rvol": setup["setup_5m_rvol"], "risk": {"entry": entry, "sl": sl, "risk": risk, "t1": targets[0], "t2": targets[1], "t3": targets[2]}}
            if send(signal_message(signal)):
                previous = active_signal_for_symbol(state, symbol)
                if previous and previous.get("direction") != signal["direction"]:
                    reverse_active_signal(state, symbol, signal["direction"], ts, entry)
                state["signals"][k] = signal
                state["pending_setups"].pop(symbol, None)
                changed = True
                LOG.info("%s | 1M_CONFIRMATION | direction=%s | setup_time=%s | confirmation_time=%s | entry=%.2f | stop_loss=%.2f", symbol, signal["direction"], setup["setup_5m_timestamp"], confirmed["confirmation_time"], entry, sl)

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
