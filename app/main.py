from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .calendar import is_nse_trading_day, previous_trading_day
from .config import SETTINGS
from .dhan_client import DhanClient
from .indicators import add_indicators
from .nse_universe import build_universe
from .state import load, save, backup_and_clear, signal_key, reverse_active_signal, record_alert
from .strategy import evaluate_15m_setup, confirm_5m_breakout
from .telegram import send, signal_message, exit_message
from .summary import build_summary
from .risk import build_risk_and_targets

IST = ZoneInfo("Asia/Kolkata")
LOG = logging.getLogger(__name__)


def now():
    return datetime.now(IST)


def _as_ist_index(df):
    if df is None or df.empty:
        return df
    x = df.copy()
    idx = pd.DatetimeIndex(x.index)
    if idx.tz is None:
        idx = idx.tz_localize(IST)
    else:
        idx = idx.tz_convert(IST)
    x.index = idx
    return x.sort_index()


def completed_candles(df, ts, interval):
    """Return only completed candles; Dhan timestamps are treated as right-edge labels."""
    x = _as_ist_index(df)
    if x is None or x.empty:
        return x
    cutoff = pd.Timestamp(ts)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize(IST)
    else:
        cutoff = cutoff.tz_convert(IST)
    cutoff = cutoff.floor("min")
    if interval == 1:
        return x[x.index < cutoff]
    return x[x.index <= cutoff]


def _intraday_range(ts):
    start = ts - timedelta(days=7)
    return start.strftime("%Y-%m-%d"), (ts + timedelta(days=1)).strftime("%Y-%m-%d")


def _prepare(df, scan_date, cutoff):
    x = _as_ist_index(df)
    if x is None or x.empty:
        return pd.DataFrame()
    day = scan_date.date()
    cutoff = pd.Timestamp(cutoff)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize(IST)
    else:
        cutoff = cutoff.tz_convert(IST)
    x = x[(x.index.date < day) | ((x.index.date == day) & (x.index <= cutoff))]
    x = x[~x.index.duplicated(keep="last")]
    return x


def _previous_daily_values(dhan, security_id, ts, state, symbol):
    cache = state.setdefault("daily_filters", {})
    cached = cache.get(symbol)
    if cached and cached.get("date") == ts.date().isoformat():
        return cached["close"], cached["volume"]

    prev = previous_trading_day(ts.date())
    from_date = (prev - timedelta(days=7)).strftime("%Y-%m-%d")
    to_date = (prev + timedelta(days=1)).strftime("%Y-%m-%d")
    df = dhan.historical_daily_df(security_id, from_date, to_date)
    if df is None or df.empty:
        return None, None
    x = _as_ist_index(df)
    x = x[x.index.date <= prev]
    if x.empty:
        return None, None
    row = x.iloc[-1]
    close = float(row["close"])
    volume = float(row["volume"])
    cache[symbol] = {"date": ts.date().isoformat(), "close": close, "volume": volume}
    return close, volume


def ltp_batch(dhan, ids):
    if not ids:
        return {}
    response = dhan.dhan.ohlc_data(securities={"NSE_EQ": [int(x) if str(x).isdigit() else str(x) for x in ids]})
    data = response.get("data", response) if isinstance(response, dict) else {}
    block = data.get("NSE_EQ", {}) if isinstance(data, dict) else {}
    return block if isinstance(block, dict) else {}


def _format_ts(value):
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize(IST)
    else:
        ts = ts.tz_convert(IST)
    return ts.strftime("%d %b %Y, %I:%M %p IST")


def _expire_previous_setup(state, symbol, new_15m_ts):
    pending = state.get("pending_setups", {}).get(symbol)
    if not pending:
        return
    old = pending.get("setup_15m_timestamp")
    if old and str(old) != str(new_15m_ts):
        LOG.info(
            "%s | SETUP_EXPIRED | direction=%s | setup_time=%s | reason=NEXT_15M_CANDLE",
            symbol, pending.get("direction"), old,
        )
        state["pending_setups"].pop(symbol, None)


def _store_setup(state, item, setup):
    symbol = item["symbol"]
    setup = dict(setup)
    setup.update({
        "symbol": symbol,
        "security_id": item["security_id"],
        "consumed": False,
        "status": "PENDING",
    })
    state.setdefault("pending_setups", {})[symbol] = setup
    LOG.info(
        "%s | 15M_SETUP | direction=%s | candle=%s | score=%.1f | departure=%.1f | freshness=%.1f | base=%.1f | rvol=%.2f",
        symbol, setup["direction"], setup["setup_15m_timestamp"], setup["trade_quality_score"],
        setup["departure_score"], setup["freshness_score"], setup["base_candle_score"], setup["setup_15m_rvol"],
    )
    LOG.info(
        "%s | WAITING_5M_CONFIRMATION | breakout_level=%.2f | stop_loss=%.2f",
        symbol, setup["breakout_level"], setup["stop_loss"],
    )


def _risk_and_targets(pending, entry):
    return build_risk_and_targets(
        pending["direction"],
        entry,
        float(pending["setup_15m_close"]),
        SETTINGS.min_stop_distance_percent,
        SETTINGS.t1_rr,
        SETTINGS.t2_rr,
        SETTINGS.t3_rr,
        SETTINGS.min_rr,
    )


def _t1_blocked(df5, entry, t1, direction):
    if df5 is None or df5.empty:
        return False
    history = df5.iloc[:-1].tail(20)
    if history.empty:
        return False
    if direction == "BUY":
        levels = history["high"]
        return bool(((levels > entry) & (levels < t1)).any())
    levels = history["low"]
    return bool(((levels < entry) & (levels > t1)).any())


def _build_active_signal(pending, confirmation_ts, entry, risk):
    return {
        "symbol": pending["symbol"],
        "security_id": pending["security_id"],
        "direction": pending["direction"],
        "setup": "15M_QUALITY_5M_CONFIRMATION",
        "signal_key": signal_key(pending["symbol"], pending["direction"], pending["setup_15m_timestamp"]),
        "setup_15m_timestamp": pending["setup_15m_timestamp"],
        "confirmation_5m_timestamp": pd.Timestamp(confirmation_ts).isoformat(),
        "signal_time": pd.Timestamp(confirmation_ts).isoformat(),
        "signal_price": float(entry),
        "status": "ACTIVE",
        "risk": risk,
        "breakout_level": pending["breakout_level"],
        "setup_15m_high": pending["setup_15m_high"],
        "setup_15m_low": pending["setup_15m_low"],
        "setup_15m_close": pending["setup_15m_close"],
        "trade_quality_score": pending["trade_quality_score"],
        "departure_score": pending["departure_score"],
        "freshness_score": pending["freshness_score"],
        "base_candle_score": pending["base_candle_score"],
        "setup_15m_rvol": pending["setup_15m_rvol"],
        "daily_close": pending["daily_close"],
        "daily_volume": pending["daily_volume"],
        "highest_target_hit": None,
    }


def _confirm_pending(state, item, df5):
    symbol = item["symbol"]
    pending = state.get("pending_setups", {}).get(symbol)
    if not pending or pending.get("consumed") or df5.empty:
        return False

    confirmation_ts = df5.index[-1]
    if not confirm_5m_breakout(pending, confirmation_ts, float(df5.iloc[-1]["close"])):
        LOG.info(
            "%s | WAITING_5M_CONFIRMATION | latest_close=%.2f | required_%s=%.2f",
            symbol,
            float(df5.iloc[-1]["close"]),
            "above" if pending["direction"] == "BUY" else "below",
            float(pending["setup_15m_high"] if pending["direction"] == "BUY" else pending["setup_15m_low"]),
        )
        return False

    entry = float(df5.iloc[-1]["close"])
    risk, reject = _risk_and_targets(pending, entry)
    if reject:
        LOG.info(
            "%s | SIGNAL_REJECTED | reason=%s | entry=%.2f | sl=%.2f | risk=%.2f | risk_percent=%s | minimum_required=%.2f%%",
            symbol, reject, entry, float(pending["setup_15m_close"]),
            abs(entry - float(pending["setup_15m_close"])),
            "%.3f" % (abs(entry - float(pending["setup_15m_close"])) / entry * 100.0),
            SETTINGS.min_stop_distance_percent,
        )
        pending["consumed"] = True
        return False

    if risk["rr_t1"] < SETTINGS.min_rr:
        LOG.info("%s | SIGNAL_REJECTED | reason=T1_RR_BELOW_2 | rr=%.2f", symbol, risk["rr_t1"])
        pending["consumed"] = True
        return False

    if _t1_blocked(df5, entry, risk["t1"], pending["direction"]):
        LOG.info("%s | SIGNAL_REJECTED | reason=T1_BLOCKED | entry=%.2f | t1=%.2f", symbol, entry, risk["t1"])
        pending["consumed"] = True
        return False

    signal = _build_active_signal(pending, confirmation_ts, entry, risk)
    k = signal["signal_key"]
    if k in state.get("signals", {}) or k in state.get("alert_state", {}):
        pending["consumed"] = True
        LOG.info("%s | DUPLICATE_ALERT_BLOCKED | key=%s", symbol, k)
        return False

    if send(signal_message(signal)):
        previous = next((s for s in state.get("signals", {}).values() if s.get("symbol") == symbol and s.get("status") == "ACTIVE"), None)
        if previous and previous.get("direction") != signal["direction"]:
            reverse_active_signal(state, symbol, signal["direction"], pd.Timestamp(confirmation_ts).to_pydatetime(), entry)
        state.setdefault("signals", {})[k] = signal
        record_alert(state, signal, pd.Timestamp(confirmation_ts).to_pydatetime())
        pending["consumed"] = True
        LOG.info(
            "%s | 5M_CONFIRMATION | direction=%s | setup_time=%s | confirmation_time=%s | entry=%.2f | sl=%.2f | risk=%.2f | score=%.1f",
            symbol, signal["direction"], pending["setup_15m_timestamp"], signal["confirmation_5m_timestamp"],
            entry, risk["sl"], risk["risk"], signal["trade_quality_score"],
        )
        return True
    return False


def _monitor(dhan, state, ts):
    active = [s for s in state.get("signals", {}).values() if s.get("status") == "ACTIVE"]
    if not active:
        return False
    quotes = ltp_batch(dhan, [s["security_id"] for s in active])
    changed = False
    for s in active:
        q = quotes.get(str(s["security_id"]), {})
        px = q.get("last_price", q.get("ltp"))
        if px is None:
            continue
        px = float(px)
        direction = s["direction"]
        risk = s["risk"]
        previous = s.get("highest_target_hit")

        target_hit = None
        if direction == "BUY":
            if px >= risk["t3"]:
                target_hit = "T3_HIT"
            elif px >= risk["t2"]:
                target_hit = "T2_HIT"
            elif px >= risk["t1"]:
                target_hit = "T1_HIT"
        else:
            if px <= risk["t3"]:
                target_hit = "T3_HIT"
            elif px <= risk["t2"]:
                target_hit = "T2_HIT"
            elif px <= risk["t1"]:
                target_hit = "T1_HIT"

        rank = {None: 0, "T1_HIT": 1, "T2_HIT": 2, "T3_HIT": 3}
        if target_hit and rank[target_hit] > rank.get(previous, 0):
            s["highest_target_hit"] = target_hit
            LOG.info(
                "%s | %s | target=%.2f | points=%+.2f",
                s["symbol"], target_hit,
                risk[target_hit.replace("_HIT", "").lower()],
                _result_points(direction, risk[target_hit.replace("_HIT", "").lower()], risk["entry"]),
            )
            changed = True

        # A target does not close the alert; continue monitoring for a higher
        # target. T3 is the final target.
        if target_hit == "T3_HIT":
            s["status"] = "EXITED"
            s["exit_price"] = float(risk["t3"])
            s["exit_time"] = ts.isoformat()
            s["exit_reason"] = "T3_HIT"
            try:
                send(exit_message(s, risk["t3"], "T3_HIT", ts.strftime("%H:%M:%S")))
            except Exception:
                LOG.exception("%s | T3 Telegram failed", s["symbol"])
            changed = True
            continue

        sl_hit = (direction == "BUY" and px <= risk["sl"]) or (direction == "SELL" and px >= risk["sl"])
        if sl_hit:
            s["status"] = "EXITED"
            s["exit_price"] = float(risk["sl"])
            s["exit_time"] = ts.isoformat()
            s["exit_reason"] = "SL_HIT"
            try:
                send(exit_message(s, risk["sl"], "SL_HIT", ts.strftime("%H:%M:%S")))
            except Exception:
                LOG.exception("%s | SL Telegram failed", s["symbol"])
            changed = True

    return changed


def _result_points(direction, price, entry):
    return price - entry if direction == "BUY" else entry - price


def _append_universe(dhan, state):
    candidates = build_universe(dhan)
    existing = {str(x.get("security_id")) for x in state.get("universe", [])}
    added = 0
    for item in candidates:
        sid = str(item.get("security_id"))
        if sid and sid not in existing:
            state.setdefault("universe", []).append(item)
            existing.add(sid)
            added += 1
    LOG.info("UNIVERSE_REFRESH | discovered=%d | added=%d | total=%d", len(candidates), added, len(state.get("universe", [])))
    return added


def create_universe(dhan, state):
    state["universe"] = build_universe(dhan)
    state["pending_setups"] = {}
    state["signals"] = {}
    state["alert_state"] = {}
    state["daily_filters"] = {}
    save(state)
    LOG.info("UNIVERSE_INITIAL | size=%d", len(state["universe"]))


def refresh_universe(dhan, state):
    _append_universe(dhan, state)
    save(state)


def scan(dhan, state, ts):
    if not state.get("universe"):
        LOG.warning("Universe missing; refusing to scan")
        return

    changed = False
    start_date, end_date = _intraday_range(ts)

    for item in list(state["universe"]):
        symbol = item.get("symbol")
        sid = item.get("security_id")
        if not symbol or not sid:
            continue
        try:
            raw5 = dhan.intraday_df(sid, SETTINGS.entry_timeframe, start_date, end_date)
            raw15 = dhan.intraday_df(sid, SETTINGS.setup_timeframe, start_date, end_date)
            df5 = completed_candles(raw5, ts, 5)
            df15 = completed_candles(raw15, ts, 15)
            if df5.empty or df15.empty:
                continue

            df5i = add_indicators(_prepare(raw5, ts, df5.index[-1]), SETTINGS.rvol_lookback)
            df15i = add_indicators(_prepare(raw15, ts, df15.index[-1]), SETTINGS.rvol_lookback)
            if df5i.empty or df15i.empty:
                continue

            latest15 = pd.Timestamp(df15i.index[-1]).isoformat()
            previous_pending = state.get("pending_setups", {}).get(symbol)
            if not previous_pending or previous_pending.get("setup_15m_timestamp") != latest15:
                _expire_previous_setup(state, symbol, latest15)
                daily_close, daily_volume = _previous_daily_values(dhan, sid, ts, state, symbol)
                if daily_close is None or daily_volume is None:
                    LOG.info("%s | SETUP_REJECTED | reason=DAILY_FILTER_DATA_UNAVAILABLE", symbol)
                else:
                    setup = evaluate_15m_setup(df15i, daily_close, daily_volume)
                    if setup:
                        _store_setup(state, item, setup)
                        changed = True
                    else:
                        LOG.info("%s | SETUP_REJECTED | candle=%s | reason=15M_FILTER_OR_SCORE", symbol, latest15)

            if _confirm_pending(state, item, df5i):
                changed = True

        except Exception:
            LOG.exception("Scan failed: %s", symbol)

    if changed:
        save(state)


def summary(dhan, state, ts):
    active = [s for s in state.get("signals", {}).values() if s.get("status") == "ACTIVE"]
    prices = {}
    if active:
        quotes = ltp_batch(dhan, [s["security_id"] for s in active])
        for s in active:
            q = quotes.get(str(s["security_id"]), {})
            px = q.get("last_price", q.get("ltp"))
            if px is not None:
                s["status"] = "CLOSED_EOD"
                s["exit_price"] = float(px)
                s["exit_time"] = ts.isoformat()
                s["exit_reason"] = "END_OF_DAY"
                prices[s["symbol"]] = float(px)
    message = build_summary(state, prices)
    send(message)
    backup_and_clear(state, ts.date())


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s | %(levelname)s | %(message)s")
    ts = now()
    action = os.getenv("SCANNER_ACTION", "scan").strip().lower()

    if not is_nse_trading_day(ts.date()):
        LOG.info("Not an NSE trading day")
        return

    dhan = DhanClient()
    state = load(ts.date())

    if action == "universe":
        create_universe(dhan, state)
        return
    if action == "universe_refresh":
        if not state.get("universe"):
            create_universe(dhan, state)
        else:
            refresh_universe(dhan, state)
        return
    if action == "summary":
        summary(dhan, state, ts)
        return
    if action == "monitor":
        if _monitor(dhan, state, ts):
            save(state)
        return
    if action == "scan":
        if not (SETTINGS.scan_start_hhmm <= ts.hour * 100 + ts.minute <= SETTINGS.scan_end_hhmm):
            LOG.info("Outside scanner window")
            return
        changed = _monitor(dhan, state, ts)
        scan(dhan, state, ts)
        if changed:
            save(state)
        return
    raise ValueError(f"Unknown SCANNER_ACTION: {action}")


if __name__ == "__main__":
    main()
