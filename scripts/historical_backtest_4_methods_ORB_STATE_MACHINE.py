from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd

from app.calendar import previous_trading_day
from app.config import SETTINGS
from app.dhan_client import DhanClient
from app.indicators import add_indicators
from app.risk import build_risk_and_targets
from app.strategy import confirm_5m_breakout, evaluate_15m_setup

IST = ZoneInfo("Asia/Kolkata")
LOG = logging.getLogger("historical_backtest_compare")

METHODS = {
    "A": "CURRENT_15M_SETUP_5M_BREAKOUT",
    "B": "FIRST_09:15_ORB_15M_CLOSE_ONE_TRADE_PER_SYMBOL",
    "C": "QUALIFYING_15M_SETUP_5M_ORB_BREAKOUT",
    "D": "FIRST_09:15_ORB_15M_DIAGNOSTIC",
}

# Explicit Method-B/Method-D ORB state machine.
ORB_WAITING = "ORB_WAITING"
ORB_FIRST_BREAKOUT = "ORB_FIRST_BREAKOUT"
ORB_CONSUMED = "ORB_CONSUMED"
SIGNAL_ACCEPTED = "SIGNAL_ACCEPTED"
SIGNAL_REJECTED = "SIGNAL_REJECTED"


def parse_args():
    p = argparse.ArgumentParser(
        description="Corrected NSE Momentum A/B/C/D historical comparison; Method B uses one first ORB breakout per symbol/day."
    )
    p.add_argument("--date", required=True, help="Trading date, YYYY-MM-DD")
    p.add_argument(
        "--universe-json",
        default="state/runtime_state.json",
        help="JSON universe/state file containing symbol/security_id records",
    )
    p.add_argument(
        "--symbols",
        default="",
        help="Comma-separated symbols; security IDs are resolved from --universe-json",
    )
    p.add_argument("--limit", type=int, default=0, help="Limit symbols; 0 = all")
    p.add_argument("--output-dir", default="backtest_compare_results")
    p.add_argument(
        "--no-fetch",
        action="store_true",
        help="Use cached CSV candles under data/backtest_cache",
    )
    return p.parse_args()


def as_ist(ts):
    t = pd.Timestamp(ts)
    return t.tz_localize(IST) if t.tzinfo is None else t.tz_convert(IST)


def load_universe(path: str, symbols_arg: str):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("universe", []) if isinstance(payload, dict) else payload

    wanted = (
        {s.strip().upper() for s in symbols_arg.split(",") if s.strip()}
        if symbols_arg
        else None
    )

    result = []
    for row in rows:
        symbol = str(row.get("symbol", "")).strip().upper()
        security_id = str(row.get("security_id", "")).strip()
        if not symbol or not security_id:
            continue
        if wanted and symbol not in wanted:
            continue
        result.append({"symbol": symbol, "security_id": security_id})

    seen = set()
    out = []
    for row in result:
        if row["security_id"] not in seen:
            seen.add(row["security_id"])
            out.append(row)
    return out


def fetch_daily_filter(dhan, security_id, trade_day):
    prev = previous_trading_day(trade_day)
    df = dhan.historical_daily_df(
        security_id,
        prev.isoformat(),
        (prev + timedelta(days=1)).isoformat(),
    )
    if df.empty:
        return None, None, prev

    x = df[df.index.date <= prev]
    if x.empty:
        return None, None, prev

    row = x.iloc[-1]
    return float(row["close"]), float(row["volume"]), prev


def fetch_intraday(dhan, security_id, trade_day, interval):
    start = trade_day - timedelta(days=10)
    end = trade_day + timedelta(days=1)
    return dhan.historical_intraday_df(
        security_id,
        f"{start.isoformat()} 09:15:00",
        f"{end.isoformat()} 15:30:00",
        interval,
    )


def cache_paths(cache_dir, symbol, interval, trade_day):
    return Path(cache_dir) / f"{symbol}_{interval}m_{trade_day.isoformat()}.csv"


def get_candles(dhan, row, trade_day, interval, no_fetch, cache_dir):
    path = cache_paths(cache_dir, row["symbol"], interval, trade_day)

    if path.exists():
        df = pd.read_csv(path, parse_dates=["timestamp"], index_col="timestamp")
        if df.index.tz is None:
            df.index = df.index.tz_localize(IST)
        else:
            df.index = df.index.tz_convert(IST)
        return df.sort_index()

    if no_fetch:
        raise FileNotFoundError(f"Missing cache: {path}")

    df = fetch_intraday(dhan, row["security_id"], trade_day, interval)
    if not df.empty:
        path.parent.mkdir(parents=True, exist_ok=True)
        df.reset_index().to_csv(path, index=False)
    return df


def t1_blocked(df5, signal_ts, entry, t1, direction):
    history = df5[df5.index < signal_ts].tail(20)
    if history.empty:
        return False

    if direction == "BUY":
        return bool(((history["high"] > entry) & (history["high"] < t1)).any())

    return bool(((history["low"] < entry) & (history["low"] > t1)).any())


def simulate_outcome(signal, candles5):
    start = as_ist(signal["confirmation_time"])
    future = candles5[candles5.index > start]

    direction = signal["direction"]
    levels = [signal["t1"], signal["t2"], signal["t3"]]

    hit = []
    exit_reason = "EOD"

    if future.empty:
        exit_price = float(signal["entry"])
        exit_time = signal["confirmation_time"]
    else:
        exit_price = float(future.iloc[-1]["close"])
        exit_time = future.index[-1].isoformat()

    for ts, row in future.iterrows():
        high = float(row["high"])
        low = float(row["low"])

        sl_hit = (
            low <= signal["sl"] if direction == "BUY"
            else high >= signal["sl"]
        )
        targets_hit = (
            [high >= x for x in levels] if direction == "BUY"
            else [low <= x for x in levels]
        )

        if sl_hit:
            exit_reason = "SL"
            exit_price = float(signal["sl"])
            exit_time = ts.isoformat()
            break

        for i, ok in enumerate(targets_hit):
            if ok and i + 1 not in hit:
                hit.append(i + 1)

        if 3 in hit:
            exit_reason = "T3"
            exit_price = float(signal["t3"])
            exit_time = ts.isoformat()
            break

    pnl_points = (
        exit_price - signal["entry"]
        if direction == "BUY"
        else signal["entry"] - exit_price
    )

    signal["targets_hit"] = ",".join(f"T{x}" for x in hit) if hit else "NONE"
    signal["exit_reason"] = exit_reason
    signal["exit_time"] = exit_time
    signal["exit_price"] = exit_price
    signal["pnl_points"] = pnl_points
    signal["pnl_r"] = pnl_points / signal["risk"] if signal["risk"] else 0.0
    return signal


def build_signal(method, symbol, security_id, direction, confirmation_ts,
                 entry, sl, score, df5, t1_block=True):
    risk, reject = build_risk_and_targets(
        direction,
        float(entry),
        float(sl),
        SETTINGS.min_stop_distance_percent,
        SETTINGS.t1_rr,
        SETTINGS.t2_rr,
        SETTINGS.t3_rr,
        SETTINGS.min_rr,
    )

    if reject:
        return None, reject

    if t1_block and t1_blocked(
        df5, confirmation_ts, risk["entry"], risk["t1"], direction
    ):
        return None, "T1_BLOCKED"

    signal = {
        "method": method,
        "method_name": METHODS[method],
        "symbol": symbol,
        "security_id": security_id,
        "direction": direction,
        "confirmation_time": as_ist(confirmation_ts).isoformat(),
        "entry": risk["entry"],
        "sl": risk["sl"],
        "risk": risk["risk"],
        "risk_percent": risk["risk_percent"],
        "t1": risk["t1"],
        "t2": risk["t2"],
        "t3": risk["t3"],
        "score": float(score) if score is not None else None,
    }

    return simulate_outcome(signal, df5), None


def opening_range(df15):
    """Return the completed 09:15 15M candle used as the fixed ORB."""
    x = df15[df15.index.strftime("%H:%M") == "09:15"]
    if x.empty:
        return None

    row = x.iloc[0]
    ts = x.index[0]

    return {
        "timestamp": ts,
        "open": float(row["open"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "close": float(row["close"]),
    }


def process_symbol(dhan, row, trade_day, no_fetch, cache_dir):
    symbol = row["symbol"]

    daily_close, daily_volume, prev_day = fetch_daily_filter(
        dhan, row["security_id"], trade_day
    )

    result = {
        "symbol": symbol,
        "security_id": row["security_id"],
        "date": trade_day.isoformat(),
        "daily_close": daily_close,
        "daily_volume": daily_volume,
        "prev_trading_day": prev_day.isoformat(),
        "signals": [],
        "events": [],
        "method_counts": {m: 0 for m in METHODS},
    }

    if daily_close is None or daily_volume is None:
        result["events"].append({
            "time": "",
            "method": "ALL",
            "stage": "UNIVERSE",
            "status": "REJECTED",
            "reason": "DAILY_DATA_UNAVAILABLE",
        })
        return result

    if (
        daily_close <= SETTINGS.min_price
        or daily_volume <= SETTINGS.min_daily_volume
    ):
        result["events"].append({
            "time": "",
            "method": "ALL",
            "stage": "UNIVERSE",
            "status": "REJECTED",
            "reason": "DAILY_FILTER",
            "daily_close": daily_close,
            "daily_volume": daily_volume,
        })
        return result

    full5 = get_candles(dhan, row, trade_day, 5, no_fetch, cache_dir)
    full15 = get_candles(dhan, row, trade_day, 15, no_fetch, cache_dir)

    if full5.empty or full15.empty:
        result["events"].append({
            "time": "",
            "method": "ALL",
            "stage": "DATA",
            "status": "REJECTED",
            "reason": "INTRADAY_DATA_UNAVAILABLE",
        })
        return result

    full5 = add_indicators(full5, SETTINGS.rvol_lookback)
    full15 = add_indicators(full15, SETTINGS.rvol_lookback)

    full5 = full5[full5.index.date == trade_day]
    full15 = full15[full15.index.date == trade_day]

    orb = opening_range(full15)
    if orb is None:
        result["events"].append({
            "time": "",
            "method": "B/C/D",
            "stage": "ORB",
            "status": "REJECTED",
            "reason": "09:15_CANDLE_UNAVAILABLE",
        })
        return result

    result["orb"] = orb

    # ---------------------------
    # Method B / D: 15M ORB
    # ---------------------------
    # Method B/D: consume the first ORB breakout per symbol/day.
    # Do NOT use the A strategy active-trade state here; B must be independent.
    b_state = ORB_WAITING
    d_state = ORB_WAITING
    b_consumed = False
    d_consumed = False

    # Record that the fixed 09:15 ORB is armed. This is an audit event only;
    # it does not create a trade signal.
    result["events"].append({
        "time": str(orb["timestamp"]),
        "method": "B/D",
        "stage": "ORB_STATE",
        "status": ORB_WAITING,
        "orb_high": orb["high"],
        "orb_low": orb["low"],
        "orb_close": orb["close"],
        "reason": "FIXED_09:15_ORB_ARMED",
    })

    # ---------------------------
    # Method A / C state
    # ---------------------------
    pending_a = None
    pending_c = None
    active_until_a = None
    active_until_c = None
    last_setup_ts = None

    # Process completed 15M candles chronologically and check all 5M closes.
    for ts5, candle5 in full5.iterrows():
        hhmm = int(ts5.strftime("%H%M"))
        if hhmm < SETTINGS.scan_start_hhmm or hhmm > SETTINGS.scan_end_hhmm:
            continue

        # Clear active trade after simulated exit.
        if active_until_a is not None and as_ist(ts5) >= active_until_a:
            active_until_a = None

        if active_until_c is not None and as_ist(ts5) >= active_until_c:
            active_until_c = None

        eligible15 = full15[full15.index <= ts5]
        if eligible15.empty:
            continue

        latest15_ts = eligible15.index[-1]

        # Expire A/C pending setup when the next 15M candle completes.
        if pending_a is not None and as_ist(latest15_ts) > as_ist(
            pending_a["setup_15m_timestamp"]
        ):
            result["events"].append({
                "time": str(ts5),
                "method": "A",
                "stage": "SETUP_EXPIRED",
                "status": "EXPIRED",
                "setup_time": pending_a["setup_15m_timestamp"],
                "reason": "NEXT_15M_CANDLE",
            })
            pending_a = None

        if pending_c is not None and as_ist(latest15_ts) > as_ist(
            pending_c["setup_15m_timestamp"]
        ):
            result["events"].append({
                "time": str(ts5),
                "method": "C",
                "stage": "SETUP_EXPIRED",
                "status": "EXPIRED",
                "setup_time": pending_c["setup_15m_timestamp"],
                "reason": "NEXT_15M_CANDLE",
            })
            pending_c = None

        # New completed 15M setup evaluation for A/C.
        if last_setup_ts is None or latest15_ts != last_setup_ts:
            last_setup_ts = latest15_ts

            setup_frame = full15[full15.index <= latest15_ts]
            setup = evaluate_15m_setup(
                setup_frame,
                daily_close,
                daily_volume,
            )

            if setup:
                # A: current production-style setup.
                if active_until_a is None:
                    pending_a = setup
                    result["events"].append({
                        "time": str(latest15_ts),
                        "method": "A",
                        "stage": "15M_SETUP",
                        "status": "ACCEPTED",
                        "direction": setup["direction"],
                        "score": setup["trade_quality_score"],
                        "rvol": setup["setup_15m_rvol"],
                        "high": setup["setup_15m_high"],
                        "low": setup["setup_15m_low"],
                        "close": setup["setup_15m_close"],
                    })
                else:
                    result["events"].append({
                        "time": str(latest15_ts),
                        "method": "A",
                        "stage": "15M_SETUP",
                        "status": "SUPPRESSED",
                        "reason": "ACTIVE_SIGNAL_EXISTS",
                    })

                # C: same qualifying setup, but confirmation uses fixed ORB.
                if active_until_c is None:
                    pending_c = setup
                    result["events"].append({
                        "time": str(latest15_ts),
                        "method": "C",
                        "stage": "15M_SETUP",
                        "status": "ACCEPTED",
                        "direction": setup["direction"],
                        "score": setup["trade_quality_score"],
                        "rvol": setup["setup_15m_rvol"],
                        "high": setup["setup_15m_high"],
                        "low": setup["setup_15m_low"],
                        "close": setup["setup_15m_close"],
                    })

            else:
                result["events"].append({
                    "time": str(latest15_ts),
                    "method": "A/C",
                    "stage": "15M_SETUP",
                    "status": "REJECTED",
                    "reason": "15M_FILTERS_OR_SCORE",
                })

        close5 = float(candle5["close"])

        # ---------------------------
        # Method A: current strategy
        # ---------------------------
        if pending_a is not None and as_ist(ts5) > as_ist(
            pending_a["setup_15m_timestamp"]
        ):
            direction = pending_a["direction"]
            threshold = (
                float(pending_a["setup_15m_high"])
                if direction == "BUY"
                else float(pending_a["setup_15m_low"])
            )

            met = close5 > threshold if direction == "BUY" else close5 < threshold

            if met:
                if confirm_5m_breakout(pending_a, ts5, close5):
                    signal, reject = build_signal(
                        "A",
                        symbol,
                        row["security_id"],
                        direction,
                        ts5,
                        close5,
                        pending_a["setup_15m_close"],
                        pending_a["trade_quality_score"],
                        full5,
                    )
                    if signal:
                        signal["setup_time"] = pending_a["setup_15m_timestamp"]
                        result["signals"].append(signal)
                        result["method_counts"]["A"] += 1
                        active_until_a = as_ist(signal["exit_time"])
                        pending_a = None
                    else:
                        result["events"].append({
                            "time": str(ts5),
                            "method": "A",
                            "stage": "SIGNAL_REJECTED",
                            "status": "REJECTED",
                            "reason": reject,
                        })

        # ---------------------------
        # Method C: qualifying setup + 09:15 ORB
        # ---------------------------
        if pending_c is not None and as_ist(ts5) > as_ist(
            pending_c["setup_15m_timestamp"]
        ):
            direction = pending_c["direction"]
            orb_threshold = (
                orb["high"] if direction == "BUY" else orb["low"]
            )
            met = (
                close5 > orb_threshold
                if direction == "BUY"
                else close5 < orb_threshold
            )

            if met:
                signal, reject = build_signal(
                    "C",
                    symbol,
                    row["security_id"],
                    direction,
                    ts5,
                    close5,
                    pending_c["setup_15m_close"],
                    pending_c["trade_quality_score"],
                    full5,
                )
                if signal:
                    signal["setup_time"] = pending_c["setup_15m_timestamp"]
                    signal["orb_high"] = orb["high"]
                    signal["orb_low"] = orb["low"]
                    result["signals"].append(signal)
                    result["method_counts"]["C"] += 1
                    active_until_c = as_ist(signal["exit_time"])
                    pending_c = None
                else:
                    result["events"].append({
                        "time": str(ts5),
                        "method": "C",
                        "stage": "SIGNAL_REJECTED",
                        "status": "REJECTED",
                        "reason": reject,
                    })

        # ---------------------------
        # Methods B / D:
        # fixed 09:15 ORB, 15M close
        #
        # A signal can occur only when a NEW completed 15M candle exists.
        # We use the 15M close itself as the confirmation/entry.
        # ---------------------------
        if latest15_ts == ts5:
            # Only evaluate at the 15M right edge.
            candle15 = full15.loc[latest15_ts]
            close15 = float(candle15["close"])

            # BUY OR SELL direction is determined by which side of the
            # opening range the completed 15M close breaks.
            if close15 > orb["high"]:
                direction = "BUY"
            elif close15 < orb["low"]:
                direction = "SELL"
            else:
                direction = None

            if direction:
                # Method B: FIRST 09:15 ORB breakout only.
                # Once the first completed 15M close breaks the ORB, the
                # symbol's B opportunity is consumed for the rest of the day.
                if not b_consumed:
                    b_consumed = True
                    b_state = ORB_FIRST_BREAKOUT
                    result["events"].append({
                        "time": str(latest15_ts),
                        "method": "B",
                        "stage": "ORB_BREAKOUT",
                        "status": "CANDIDATE",
                        "direction": direction,
                        "close_15m": close15,
                        "orb_high": orb["high"],
                        "orb_low": orb["low"],
                        "comparison": (
                            "CLOSE_GT_ORB_HIGH"
                            if direction == "BUY"
                            else "CLOSE_LT_ORB_LOW"
                        ),
                        "reason": "FIRST_ORB_BREAKOUT_CONSUMED",
                    })
                    b_state = ORB_CONSUMED
                    result["events"].append({
                        "time": str(latest15_ts),
                        "method": "B",
                        "stage": "ORB_STATE",
                        "status": ORB_CONSUMED,
                        "direction": direction,
                        "reason": "NO_MORE_B_BREAKOUTS_TODAY",
                    })

                    signal, reject = build_signal(
                        "B",
                        symbol,
                        row["security_id"],
                        direction,
                        latest15_ts,
                        close15,
                        orb["close"],
                        None,
                        full5,
                    )
                    if signal:
                        signal["orb_time"] = orb["timestamp"]
                        signal["orb_high"] = orb["high"]
                        signal["orb_low"] = orb["low"]
                        signal["orb_breakout"] = "FIRST"
                        result["signals"].append(signal)
                        result["method_counts"]["B"] += 1
                        result["events"].append({
                            "time": str(latest15_ts),
                            "method": "B",
                            "stage": "SIGNAL_STATE",
                            "status": SIGNAL_ACCEPTED,
                            "direction": direction,
                            "reason": "FIRST_ORB_SIGNAL_ACCEPTED",
                        })
                    else:
                        result["events"].append({
                            "time": str(latest15_ts),
                            "method": "B",
                            "stage": "SIGNAL_REJECTED",
                            "status": SIGNAL_REJECTED,
                            "reason": reject,
                            "direction": direction,
                            "entry": close15,
                            "sl": orb["close"],
                        })

                # Method D: first ORB breakout diagnostic only.
                # D remains a diagnostic benchmark, not a re-entry strategy.
                if not d_consumed:
                    d_consumed = True
                    d_state = ORB_FIRST_BREAKOUT
                    result["events"].append({
                        "time": str(latest15_ts),
                        "method": "D",
                        "stage": "ORB_STATE",
                        "status": ORB_CONSUMED,
                        "direction": direction,
                        "reason": "FIRST_ORB_BREAKOUT_CONSUMED",
                    })
                    d_state = ORB_CONSUMED
                    signal, reject = build_signal(
                        "D",
                        symbol,
                        row["security_id"],
                        direction,
                        latest15_ts,
                        close15,
                        orb["close"],
                        None,
                        full5,
                        t1_block=False,
                    )
                    if signal:
                        signal["orb_time"] = orb["timestamp"]
                        signal["orb_high"] = orb["high"]
                        signal["orb_low"] = orb["low"]
                        signal["orb_breakout"] = "FIRST"
                        result["signals"].append(signal)
                        result["method_counts"]["D"] += 1
                    else:
                        result["events"].append({
                            "time": str(latest15_ts),
                            "method": "D",
                            "stage": "ORB_CANDIDATE_REJECTED",
                            "status": "REJECTED",
                            "reason": reject,
                            "direction": direction,
                            "entry": close15,
                            "sl": orb["close"],
                        })

    return result


def write_outputs(out, trade_day, all_signals, all_events, summaries, orb_rows):
    out.mkdir(parents=True, exist_ok=True)

    signals_csv = out / f"signals_compare_{trade_day}.csv"
    events_csv = out / f"events_compare_{trade_day}.csv"
    summary_csv = out / f"symbol_method_summary_{trade_day}.csv"
    orb_csv = out / f"opening_range_{trade_day}.csv"
    report = out / f"comparison_report_{trade_day}.md"

    pd.DataFrame(all_signals).to_csv(signals_csv, index=False)
    pd.DataFrame(all_events).to_csv(events_csv, index=False)
    pd.DataFrame(summaries).to_csv(summary_csv, index=False)
    pd.DataFrame(orb_rows).to_csv(orb_csv, index=False)

    df = pd.DataFrame(all_signals)

    lines = [
        f"# NSE Momentum — Side-by-Side Backtest — {trade_day}",
        "",
        "## Methods",
        "",
        "| Method | Definition |",
        "|---|---|",
        f"| A | {METHODS['A']} |",
        f"| B | {METHODS['B']} |",
        f"| C | {METHODS['C']} |",
        f"| D | {METHODS['D']} |",
        "",
        "## Backtest rule",
        "",
        "Method B takes only the FIRST completed 15M candle whose close breaks the fixed 09:15 opening range for each symbol/day.",
        "Later continuation closes are ignored. The first breakout opportunity is consumed even when risk/T1 validation rejects the trade. Event logs expose ORB_WAITING → ORB_FIRST_BREAKOUT → ORB_CONSUMED and SIGNAL_ACCEPTED/SIGNAL_REJECTED.",
        "Method B stop remains the 09:15 candle close (B1).",
        "",
        "## Overall",
        "",
        "| Method | Signals | Symbols | BUY | SELL | Total R | Avg R | T1+ | T2+ | T3 | SL | EOD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]

    for method in METHODS:
        x = df[df["method"] == method] if not df.empty else pd.DataFrame()

        signals = len(x)
        symbols = x["symbol"].nunique() if not x.empty else 0
        buy = int((x["direction"] == "BUY").sum()) if not x.empty else 0
        sell = int((x["direction"] == "SELL").sum()) if not x.empty else 0
        total_r = float(x["pnl_r"].sum()) if not x.empty else 0.0
        avg_r = float(x["pnl_r"].mean()) if not x.empty else 0.0
        t1 = int(x["targets_hit"].str.contains("T1", na=False).sum()) if not x.empty else 0
        t2 = int(x["targets_hit"].str.contains("T2", na=False).sum()) if not x.empty else 0
        t3 = int(x["targets_hit"].str.contains("T3", na=False).sum()) if not x.empty else 0
        sl = int((x["exit_reason"] == "SL").sum()) if not x.empty else 0
        eod = int((x["exit_reason"] == "EOD").sum()) if not x.empty else 0

        lines.append(
            f"| {method} | {signals} | {symbols} | {buy} | {sell} | "
            f"{total_r:.2f} | {avg_r:.2f} | {t1} | {t2} | {t3} | {sl} | {eod} |"
        )

    lines += [
        "",
        "## Symbol-by-method comparison",
        "",
        "| Symbol | A | B | C | D | Best by R |",
        "|---|---:|---:|---:|---:|---|",
    ]

    for s in sorted({x["symbol"] for x in summaries}):
        vals = {}
        for method in METHODS:
            x = df[(df["symbol"] == s) & (df["method"] == method)] if not df.empty else pd.DataFrame()
            vals[method] = float(x["pnl_r"].sum()) if not x.empty else 0.0

        best = max(vals, key=vals.get) if any(vals.values()) else "-"
        lines.append(
            f"| {s} | {vals['A']:.2f} | {vals['B']:.2f} | "
            f"{vals['C']:.2f} | {vals['D']:.2f} | {best} |"
        )

    lines += [
        "",
        "## Signals",
        "",
    ]

    if df.empty:
        lines.append("No signals were produced.")
    else:
        cols = [
            "method", "symbol", "direction", "confirmation_time",
            "entry", "sl", "t1", "t2", "t3", "score",
            "risk_percent", "targets_hit", "exit_reason",
            "pnl_points", "pnl_r",
        ]
        available = [c for c in cols if c in df.columns]
        lines.append(df[available].to_markdown(index=False))

    lines += [
        "",
        "## Files",
        "",
        f"- Signals: `{signals_csv}`",
        f"- Events: `{events_csv}`",
        f"- Symbol/method summary: `{summary_csv}`",
        f"- 09:15 opening ranges: `{orb_csv}`",
    ]

    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return signals_csv, events_csv, summary_csv, orb_csv, report


def main():
    args = parse_args()
    trade_day = date.fromisoformat(args.date)

    if trade_day.weekday() >= 5:
        raise SystemExit(f"{trade_day} is a weekend; choose an NSE trading day")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

    universe = load_universe(args.universe_json, args.symbols)
    if args.limit:
        universe = universe[:args.limit]

    if not universe:
        raise SystemExit("No symbols found. Supply --universe-json or --symbols.")

    out = Path(args.output_dir)
    dhan = None if args.no_fetch else DhanClient()

    all_signals = []
    all_events = []
    summaries = []
    orb_rows = []

    for i, row in enumerate(universe, 1):
        LOG.info(
            "[%d/%d] %s (%s)",
            i,
            len(universe),
            row["symbol"],
            row["security_id"],
        )

        try:
            result = process_symbol(
                dhan,
                row,
                trade_day,
                args.no_fetch,
                "data/backtest_cache",
            )
        except Exception as exc:
            LOG.exception("%s failed: %s", row["symbol"], exc)
            result = {
                "symbol": row["symbol"],
                "security_id": row["security_id"],
                "date": trade_day.isoformat(),
                "signals": [],
                "events": [{
                    "time": "",
                    "method": "ALL",
                    "stage": "ERROR",
                    "status": "ERROR",
                    "reason": str(exc),
                }],
                "method_counts": {m: 0 for m in METHODS},
            }

        all_signals.extend(result.get("signals", []))

        for event in result.get("events", []):
            event = dict(event)
            event.setdefault("symbol", row["symbol"])
            all_events.append(event)

        orb = result.get("orb")
        if orb:
            orb_rows.append({
                "symbol": row["symbol"],
                "security_id": row["security_id"],
                "date": trade_day.isoformat(),
                "orb_time": str(orb["timestamp"]),
                "orb_open": orb["open"],
                "orb_high": orb["high"],
                "orb_low": orb["low"],
                "orb_close": orb["close"],
            })

        summary = {
            "symbol": row["symbol"],
            "security_id": row["security_id"],
            "daily_close": result.get("daily_close"),
            "daily_volume": result.get("daily_volume"),
        }
        for method in METHODS:
            summary[f"{method}_signals"] = result["method_counts"][method]
        summaries.append(summary)

    files = write_outputs(
        out,
        trade_day,
        all_signals,
        all_events,
        summaries,
        orb_rows,
    )

    df = pd.DataFrame(all_signals)

    print()
    print(f"BACKTEST COMPARISON COMPLETE: {trade_day}")
    print(f"Symbols tested: {len(universe)}")
    print()

    for method, name in METHODS.items():
        x = df[df["method"] == method] if not df.empty else pd.DataFrame()
        print(
            f"Method {method}: {name} | "
            f"Signals={len(x)} | "
            f"Total R={x['pnl_r'].sum():.2f} | "
            f"Avg R={x['pnl_r'].mean():.2f}" if not x.empty
            else f"Method {method}: {name} | Signals=0 | Total R=0.00 | Avg R=0.00"
        )

    print()
    print(f"Signals CSV: {files[0]}")
    print(f"Events CSV:  {files[1]}")
    print(f"Summary CSV: {files[2]}")
    print(f"ORB CSV:     {files[3]}")
    print(f"Report:      {files[4]}")


if __name__ == "__main__":
    main()
