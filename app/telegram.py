from __future__ import annotations

import os
import requests
from .config import DISCLAIMER

TELEGRAM_API_URL = "https://api.telegram.org/bot"

def send(text):
    full = text.rstrip() + "\n\n" + DISCLAIMER
    if os.getenv("DRY_RUN", "true").lower() == "true":
        print(full)
        return True
    response = requests.post(f"{TELEGRAM_API_URL}{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage", json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": full, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
    response.raise_for_status()
    if not response.json().get("ok"):
        raise RuntimeError("Telegram API returned failure")
    return True

def signal_message(s):
    icon = "🚀" if s["direction"] == "BUY" else "🔻"
    level_label = "5M High" if s["direction"] == "BUY" else "5M Low"
    relation = "above" if s["direction"] == "BUY" else "below"
    return (f"{icon} <b>{s['direction']} ALERT</b>\n\n<b>{s['symbol']}</b>\n\n"
            f"<b>5M Setup:</b> {s['setup_time']}\n<b>1M Confirmation:</b> {s['signal_time']}\n\n"
            f"<b>Entry:</b> ₹{s['risk']['entry']:,.2f}\n<b>Stop Loss:</b> ₹{s['risk']['sl']:,.2f}\n\n"
            f"<b>{level_label}:</b> ₹{s['breakout_level']:,.2f}\n<b>5M Close:</b> ₹{s['setup_5m_close']:,.2f}\n\n"
            f"<b>5M EMA9:</b> {s['setup_5m_ema9']:.2f}\n<b>5M EMA20:</b> {s['setup_5m_ema20']:.2f}\n"
            f"<b>5M RSI:</b> {s['setup_5m_rsi14']:.2f}\n<b>5M VWAP:</b> {s['setup_5m_vwap']:.2f}\n\n"
            f"<b>5M Volume:</b> {s['setup_5m_volume']:,.0f}\n<b>5M Avg Volume(20):</b> {s['setup_5m_avg_volume']:,.0f}\n<b>RVOL:</b> {s['rvol']:.2f}\n\n"
            f"<b>Daily Close:</b> {s['daily_close']:.2f}\n<b>Daily Volume:</b> {s['daily_volume']:,.0f}\n\n"
            f"<b>Reason:</b> 5M quality setup + 1M close {relation} 5M {level_label.split()[-1].lower()}")

def exit_message(s, exit_price, reason, exit_time):
    entry = s["risk"]["entry"]
    move = (exit_price - entry) / entry * 100 if s["direction"] == "BUY" else (entry - exit_price) / entry * 100
    return f"⚠️ <b>EXIT — {s['symbol']}</b>\n\n<b>Direction:</b> {s['direction']}\n<b>Entry:</b> ₹{entry:,.2f}\n<b>Exit:</b> ₹{exit_price:,.2f}\n<b>Move:</b> {move:+.2f}%\n\n<b>Reason:</b> {reason}\n<b>Exit Time:</b> {exit_time}"
