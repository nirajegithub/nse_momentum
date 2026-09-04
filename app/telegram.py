from __future__ import annotations
import os
import requests
from .config import DISCLAIMER

def send(text):
    full = text.rstrip() + "\n\n" + DISCLAIMER
    if os.getenv("DRY_RUN", "true").lower() == "true":
        print(full); return True
    r=requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage", json={"chat_id":os.environ["TELEGRAM_CHAT_ID"],"text":full}, timeout=20)
    r.raise_for_status(); return True

def signal_message(s):
    r=s["risk"]; arrow="🚀 BUY" if s["direction"]=="BUY" else "🔻 SELL"
    return (f"{arrow} — {s['grade']}\n\n{s['symbol']}\nSignal Candle Close: ₹{s['signal_price']:.2f}\nCurrent LTP: ₹{s['ltp']:.2f}\n"
            f"SL: ₹{r['sl']:.2f}\nT1: ₹{r['t1']:.2f}\nT2: ₹{r['t2']:.2f}\nT3: ₹{r['t3']:.2f}\n\n"
            f"15M: {s['regime']['direction']}\n5M: {s['setup']}\nRSI: {s['rsi']:.1f}\nRVOL: {s['rvol']:.2f}x\nScore: {s['score']}/100\nSignal Time: {s['signal_time']}")

def exit_message(s, exit_price, reason, exit_time):
    entry=s["risk"]["entry"]; move=(exit_price-entry)/entry*100 if s["direction"]=="BUY" else (entry-exit_price)/entry*100
    return (f"⚠️ EXIT — {s['symbol']}\n\nDirection: {s['direction']}\nEntry: ₹{entry:.2f}\nExit: ₹{exit_price:.2f}\nMove: {move:+.2f}%\n\n"
            f"Reason: {reason}\nOriginal Signal: {s['grade']}\nOriginal Score: {s['score']}/100\nEntry Time: {s['signal_time']}\nExit Time: {exit_time}")
