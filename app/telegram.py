from __future__ import annotations

import os
import requests

from .config import DISCLAIMER


TELEGRAM_API_URL = "https://api.telegram.org/bot"


def send(text):
    """
    Send a Telegram message.

    DRY_RUN=true:
        Print message only.

    DRY_RUN=false:
        Send message to configured Telegram chat.
    """
    full = text.rstrip() + "\n\n" + DISCLAIMER

    if os.getenv("DRY_RUN", "true").lower() == "true":
        print(full)
        return True

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    response = requests.post(
        f"{TELEGRAM_API_URL}{token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": full,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {result}"
        )

    return True


def signal_message(s):
    """
    Build formatted signal message.
    """

    risk = s["risk"]

    if s["direction"] == "BUY":
        header = "🚀 <b>BUY — " + str(s["grade"]) + "</b>"
    else:
        header = "🔻 <b>SELL — " + str(s["grade"]) + "</b>"

    return (
        f"{header}\n\n"

        f"<b>{s['symbol']}</b>\n\n"

        f"📌 <b>Signal Candle Close:</b> "
        f"₹{s['signal_price']:,.2f}\n"

        f"💰 <b>Current LTP:</b> "
        f"₹{s['ltp']:,.2f}\n"

        f"🕐 <b>Signal Time:</b> "
        f"{s['signal_time']}\n\n"

        f"🛡 <b>Risk Management</b>\n"

        f"<b>Entry:</b> "
        f"₹{risk['entry']:,.2f}\n"

        f"<b>SL:</b> "
        f"₹{risk['sl']:,.2f}\n"

        f"<b>T1:</b> "
        f"₹{risk['t1']:,.2f}\n"

        f"<b>T2:</b> "
        f"₹{risk['t2']:,.2f}\n"

        f"<b>T3:</b> "
        f"₹{risk['t3']:,.2f}\n\n"

        f"📊 <b>Technical Setup</b>\n"

        f"<b>15M Regime:</b> "
        f"{s['regime']['direction']}\n"

        f"<b>5M Setup:</b> "
        f"{s['setup']}\n"

        f"<b>RSI:</b> "
        f"{s['rsi']:.1f}\n"

        f"<b>RVOL:</b> "
        f"{s['rvol']:.2f}x\n"

        f"<b>Score:</b> "
        f"<b>{s['score']}/100</b>"
    )


def exit_message(s, exit_price, reason, exit_time):
    """
    Build formatted EXIT message.
    """

    entry = s["risk"]["entry"]

    if s["direction"] == "BUY":
        move = (exit_price - entry) / entry * 100
    else:
        move = (entry - exit_price) / entry * 100

    return (
        f"⚠️ <b>EXIT — {s['symbol']}</b>\n\n"

        f"<b>Direction:</b> {s['direction']}\n"

        f"<b>Entry:</b> "
        f"₹{entry:,.2f}\n"

        f"<b>Exit:</b> "
        f"₹{exit_price:,.2f}\n"

        f"<b>Move:</b> "
        f"{move:+.2f}%\n\n"

        f"<b>Reason:</b> {reason}\n"

        f"<b>Original Signal:</b> "
        f"{s['grade']}\n"

        f"<b>Original Score:</b> "
        f"{s['score']}/100\n"

        f"<b>Entry Time:</b> "
        f"{s['signal_time']}\n"

        f"<b>Exit Time:</b> "
        f"{exit_time}"
    )
