from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

import pandas as pd

from .calendar import is_nse_trading_day
from .config import SETTINGS
from .dhan_client import DhanClient
from .indicators import add_indicators
from .nse_universe import build_universe
from .risk import build_risk_and_targets
from .state import load, save, signal_key, record_alert, backup_and_clear
from .summary import build_summary
from .telegram import send, signal_message, stop_update_message, exit_message

IST = ZoneInfo("Asia/Kolkata")
LOG = logging.getLogger(__name__)

ORB_TIME = time(9, 15)


def now():
    return datetime.now(IST)


def _as_ist_index(df):
    if df is None or df.empty:
        return pd.DataFrame()
    x = df.copy()
    x.index = pd.to_datetime(x.index)
    if x.index.tz is None:
        x.index = x.index.tz_localize(IST)
    else:
        x.index = x.index.tz_convert(IST)
    return x.sort_index()


def completed_candles(df, ts):
    x = _as_ist_index(df)
    if x.empty:
        return x
    cutoff = pd.Timestamp(ts)
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize(IST)
    else:
        cutoff = cutoff.tz_convert(IST)
    return x[x.index <= cutoff.floor("min")]


def ltp_batch(dhan, ids):
    if not ids:
        return {}
    response = dhan.dhan.ohlc_data(securities={"NSE_EQ": [int(x) if str(x).isdigit() else str(x) for x in ids]})
    data = response.get("data", response) if isinstance(response, dict) else {}
    block = data.get("NSE_EQ", {}) if isinstance(data, dict) else {}
    return block if isinstance(block, dict) else {}


def _intraday_range(ts):
    return (ts - timedelta(days=7)).strftime("%Y-%m-%d"), (ts + timedelta(days=1)).strftime("%Y-%m-%d")


def _first_orb(df15, day):
    x = df15[df15.index.date == day]
    x = x[x.index.time == ORB_TIME]
    if x.empty:
        return None
    r = x.iloc[-1]
    return {"timestamp": x.index[-1].isoformat(), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close)}


def _existing_trade(state, symbol):
    return next((s for s in state.get("signals", {}).values() if s.get("symbol") == symbol and s.get("status") == "ACTIVE"), None)


def _build_signal(item, direction, candle_ts, entry, orb, risk):
    return {
        "symbol": item["symbol"], "security_id": item["security_id"], "direction": direction,
        "setup": "09:15_ORB_B1", "signal_key": signal_key(item["symbol"], direction, candle_ts.isoformat()),
        "setup_15m_timestamp": candle_ts.isoformat(), "confirmation_5m_timestamp": None,
        "signal_time": candle_ts.isoformat(), "signal_price": entry, "status": "ACTIVE", "risk": risk, "initial_risk": risk["risk"],
        "orb_timestamp": orb["timestamp"], "orb_high": orb["high"], "orb_low": orb["low"], "orb_close": orb["close"],
        "highest_target_hit": None, "ts_stage": 0, "ts_stop": risk["sl"], "ts_events": [],
    }


def _try_orb_entry(state, item, df15, ts):
    symbol = item["symbol"]
    orb = _first_orb(df15, ts.date())
    if not orb:
        return False
    state.setdefault("orb", {}).setdefault(symbol, {"orb": orb, "consumed": False})
    rec = state["orb"][symbol]
    if rec.get("consumed"):
        return False
    if _existing_trade(state, symbol):
        rec["consumed"] = True
        return False

    after = df15[(df15.index.date == ts.date()) & (df15.index > pd.Timestamp(orb["timestamp"]))]
    if after.empty:
        return False
    candle_ts = after.index[-1]
    row = after.iloc[-1]
    close = float(row.close)
    if close > orb["high"]:
        direction = "BUY"
    elif close < orb["low"]:
        direction = "SELL"
    else:
        return False

    # The first ORB break is consumed regardless of whether the risk check accepts it.
    rec["consumed"] = True
    rec["breakout_timestamp"] = candle_ts.isoformat()
    rec["direction"] = direction
    rec["entry"] = close

    risk, reject = build_risk_and_targets(direction, close, orb["close"], SETTINGS.min_stop_distance_percent, SETTINGS.t1_rr, SETTINGS.t2_rr, SETTINGS.t3_rr, SETTINGS.min_rr)
    if reject:
        LOG.info("%s | ORB_FIRST_BREAKOUT | SIGNAL_REJECTED | reason=%s | entry=%.2f | sl=%.2f", symbol, reject, close, orb["close"])
        rec["result"] = "REJECTED"
        return False

    signal = _build_signal(item, direction, candle_ts, close, orb, risk)
    if signal["signal_key"] in state.get("signals", {}):
        return False
    if send(signal_message(signal)):
        state.setdefault("signals", {})[signal["signal_key"]] = signal
        record_alert(state, signal, candle_ts.to_pydatetime())
        rec["result"] = "ACCEPTED"
        LOG.info("%s | B1_ALERT_SENT | %s | entry=%.2f | sl=%.2f | risk=%.3f%%", symbol, direction, close, orb["close"], risk["risk_percent"])
        return True
    rec["consumed"] = False
    return False


def _latest_completed_15m(df15, before_ts):
    x = df15[df15.index < before_ts]
    return x.iloc[-1] if not x.empty else None


def _monitor_trade(dhan, state, signal, df5, df15, ts):
    if signal.get("status") != "ACTIVE" or df5.empty:
        return False
    direction = signal["direction"]
    risk = signal["risk"]
    entry = float(risk["entry"])
    initial_risk = float(signal.get("initial_risk", risk["risk"]))
    stop = float(signal.get("ts_stop", risk["sl"]))
    stage = int(signal.get("ts_stage", 0))
    changed = False

    latest5 = df5.iloc[-1]
    latest5_ts = df5.index[-1]
    close = float(latest5.close)
    favorable_r = (close - entry) / initial_risk if direction == "BUY" else (entry - close) / initial_risk

    # Progressive milestones. A newly earned stop becomes active from the next 5M candle.
    new_stage = stage
    if favorable_r >= 3.0:
        new_stage = max(new_stage, 4)
    elif favorable_r >= 2.0:
        new_stage = max(new_stage, 3)
    elif favorable_r >= 1.5:
        new_stage = max(new_stage, 2)
    elif favorable_r >= 1.0:
        new_stage = max(new_stage, 1)

    stage_stop = {
        1: entry,
        2: entry + 0.5 * initial_risk if direction == "BUY" else entry - 0.5 * initial_risk,
        3: entry + 1.0 * initial_risk if direction == "BUY" else entry - 1.0 * initial_risk,
        4: entry + 2.0 * initial_risk if direction == "BUY" else entry - 2.0 * initial_risk,
    }

    # Apply the milestone stop only when the stage advances.
    if new_stage > stage:
        proposed = stage_stop[new_stage]
        if new_stage == 4:
            structure = _latest_completed_15m(df15, latest5_ts)
            if structure is not None:
                proposed = float(structure.low if direction == "BUY" else structure.high)

        old_stop = stop
        if direction == "BUY":
            proposed = max(stop, proposed)
        else:
            proposed = min(stop, proposed)

        signal["ts_stage"] = new_stage
        stage = new_stage
        if proposed != old_stop:
            stop = proposed
            signal["ts_stop"] = proposed
            signal["risk"]["sl"] = proposed
            signal.setdefault("ts_events", []).append({
                "time": latest5_ts.isoformat(),
                "stage": new_stage,
                "stop": proposed,
                "reason": "PROGRESSIVE_TS",
            })
            changed = True
            try:
                send(stop_update_message(signal, proposed, new_stage, "15M STRUCTURE TRAIL" if new_stage == 4 else "PROGRESSIVE TS"))
            except Exception:
                LOG.exception("%s | stop-update Telegram failed", signal["symbol"])
            LOG.info("%s | TS_UPDATE | stage=%d | close=%.2f | new_sl=%.2f", signal["symbol"], new_stage, close, proposed)

    # Once +3R is reached, continuously trail the latest completed 15M structure.
    # The stop can only tighten; it is never loosened.
    if stage >= 4:
        structure = _latest_completed_15m(df15, latest5_ts)
        if structure is not None:
            proposed = float(structure.low if direction == "BUY" else structure.high)
            old_stop = stop
            if direction == "BUY":
                proposed = max(old_stop, proposed)
            else:
                proposed = min(old_stop, proposed)
            if proposed != old_stop:
                stop = proposed
                signal["ts_stop"] = proposed
                signal["risk"]["sl"] = proposed
                signal.setdefault("ts_events", []).append({
                    "time": latest5_ts.isoformat(),
                    "stage": 4,
                    "stop": proposed,
                    "reason": "15M_STRUCTURE_TRAIL",
                })
                changed = True
                try:
                    send(stop_update_message(signal, proposed, 4, "15M STRUCTURE TRAIL"))
                except Exception:
                    LOG.exception("%s | structure-stop Telegram failed", signal["symbol"])
                LOG.info("%s | TS_STRUCTURE_UPDATE | close=%.2f | new_sl=%.2f", signal["symbol"], close, proposed)

    # Target milestones are informational. T3 does not force an exit.
    hit = None
    px = close
    if direction == "BUY":
        if px >= risk["t3"]: hit = "T3_HIT"
        elif px >= risk["t2"]: hit = "T2_HIT"
        elif px >= risk["t1"]: hit = "T1_HIT"
    else:
        if px <= risk["t3"]: hit = "T3_HIT"
        elif px <= risk["t2"]: hit = "T2_HIT"
        elif px <= risk["t1"]: hit = "T1_HIT"
    rank = {None: 0, "T1_HIT": 1, "T2_HIT": 2, "T3_HIT": 3}
    if hit and rank[hit] > rank.get(signal.get("highest_target_hit"), 0):
        signal["highest_target_hit"] = hit
        changed = True
        LOG.info("%s | %s | target milestone", signal["symbol"], hit)

    quotes = ltp_batch(dhan, [signal["security_id"]])
    q = quotes.get(str(signal["security_id"]), {})
    ltp = q.get("last_price", q.get("ltp"))
    if ltp is None:
        return changed
    ltp = float(ltp)
    sl_hit = (direction == "BUY" and ltp <= stop) or (direction == "SELL" and ltp >= stop)
    if sl_hit:
        signal["status"] = "EXITED"
        signal["exit_price"] = stop
        signal["exit_time"] = ts.isoformat()
        signal["exit_reason"] = "TRAILING_SL_HIT" if stage > 0 else "SL_HIT"
        signal["r_multiple"] = ((stop - entry) / initial_risk if direction == "BUY" else (entry - stop) / initial_risk)
        try:
            send(exit_message(signal, stop, signal["exit_reason"], ts.strftime("%H:%M:%S")))
        except Exception:
            LOG.exception("%s | exit Telegram failed", signal["symbol"])
        return True
    return changed


def create_universe(dhan, state):
    state["universe"] = build_universe(dhan)
    state["signals"] = {}; state["orb"] = {}; state["daily_filters"] = {}; state["alert_state"] = {}; state["pending_setups"] = {}
    save(state); LOG.info("UNIVERSE_INITIAL | size=%d", len(state["universe"]))


def refresh_universe(dhan, state):
    candidates = build_universe(dhan)
    existing = {str(x.get("security_id")) for x in state.get("universe", [])}
    for item in candidates:
        if str(item.get("security_id")) not in existing:
            state["universe"].append(item); existing.add(str(item.get("security_id")))
    save(state)
    LOG.info("UNIVERSE_REFRESH | total=%d", len(state["universe"]))


def scan(dhan, state, ts):
    if not state.get("universe"):
        LOG.warning("Universe missing; refusing to scan"); return
    changed = False
    start, end = _intraday_range(ts)
    for item in list(state["universe"]):
        try:
            raw5 = dhan.intraday_df(item["security_id"], SETTINGS.entry_timeframe, start, end)
            raw15 = dhan.intraday_df(item["security_id"], SETTINGS.setup_timeframe, start, end)
            df5 = completed_candles(raw5, ts); df15 = completed_candles(raw15, ts)
            if df5.empty or df15.empty: continue
            # Monitor existing trade on every 5-minute scanner run.
            for signal in list(state.get("signals", {}).values()):
                if signal.get("symbol") == item["symbol"] and signal.get("status") == "ACTIVE":
                    changed |= _monitor_trade(dhan, state, signal, df5, df15, ts)
            hhmm = ts.hour*100 + ts.minute
            if 930 <= hhmm <= 1500 and not _existing_trade(state, item["symbol"]):
                changed |= _try_orb_entry(state, item, df15, ts)
        except Exception:
            LOG.exception("Scan failed: %s", item.get("symbol"))
    if changed: save(state)


def summary(dhan, state, ts):
    active = [s for s in state.get("signals", {}).values() if s.get("status") == "ACTIVE"]
    prices = {}
    if active:
        quotes = ltp_batch(dhan, [s["security_id"] for s in active])
        for s in active:
            q = quotes.get(str(s["security_id"]), {}); px = q.get("last_price", q.get("ltp"))
            if px is not None:
                px=float(px); s["status"]="CLOSED_EOD"; s["exit_price"]=px; s["exit_time"]=ts.isoformat(); s["exit_reason"]="END_OF_DAY"; prices[s["symbol"]]=px
    send(build_summary(state, prices)); backup_and_clear(state, ts.date())


def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s | %(levelname)s | %(message)s")
    ts=now()
    if not is_nse_trading_day(ts.date()): LOG.info("Not an NSE trading day"); return
    dhan=DhanClient(); state=load(ts.date()); action=os.getenv("SCANNER_ACTION","scan").strip().lower()
    if action == "universe": create_universe(dhan,state); return
    if action == "universe_refresh": refresh_universe(dhan,state) if state.get("universe") else create_universe(dhan,state); return
    if action == "summary": summary(dhan,state,ts); return
    if action == "monitor":
        # monitor is intentionally the same live management path as scan
        scan(dhan,state,ts); return
    if action == "scan": scan(dhan,state,ts); return
    LOG.warning("Unknown SCANNER_ACTION=%s", action)

if __name__ == "__main__": main()
