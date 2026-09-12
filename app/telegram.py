from __future__ import annotations

import os
import requests

from .config import DISCLAIMER


def send(text):
    full = text.rstrip() + "\n\n" + DISCLAIMER
    if os.getenv("DRY_RUN", "true").lower() == "true":
        print(full)
        return True
    r = requests.post(
        f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
        json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": full},
        timeout=20,
    )
    r.raise_for_status()
    if not r.json().get("ok", True):
        raise RuntimeError("Telegram API returned failure")
    return True


def _money(value):
    return f"₹{float(value):,.2f}"


def signal_message(s):
    header = "🚀 BUY ALERT" if s["direction"] == "BUY" else "🔻 SELL ALERT"
    return "\n".join([
        header,
        "",
        str(s["symbol"]),
        "",
        f"Entry: {_money(s['risk']['entry'])}",
        f"SL: {_money(s['risk']['sl'])}",
        "",
        f"T1: {_money(s['risk']['t1'])}",
        f"T2: {_money(s['risk']['t2'])}",
        f"T3: {_money(s['risk']['t3'])}",
        "",
        f"Setup Quality: {float(s.get('trade_quality_score', 0)):.1f}/7",
    ])


def stop_update_message(s, new_stop, stage, basis):
    labels = {1: "BREAK-EVEN", 2: "+0.5R LOCKED", 3: "+1R LOCKED", 4: "15M STRUCTURE TRAIL"}
    label = labels.get(int(stage), basis)
    return "\n".join([
        "🔒 STOP UPDATE",
        "",
        str(s["symbol"]),
        "",
        f"Entry: {_money(s['risk']['entry'])}",
        f"New SL: {_money(new_stop)}",
        f"Status: {label}",
    ])


def exit_message(s, exit_price, reason, exit_time):
    entry = float(s["risk"]["entry"])
    points = float(exit_price) - entry if s["direction"] == "BUY" else entry - float(exit_price)
    return (
        f"⚠️ {s['symbol']} {reason}\n"
        f"Points: {points:+.2f}\n"
        f"Entry: {_money(entry)}\n"
        f"Exit: {_money(exit_price)}\n"
        f"Time: {exit_time}"
    )
