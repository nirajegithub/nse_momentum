from __future__ import annotations

import html
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from .config import DISCLAIMER


IST = ZoneInfo("Asia/Kolkata")

logger = logging.getLogger(__name__)


def _clean(value):
    if value is None:
        return ""
    return str(value).strip()


def _price(value):
    try:
        return f"₹{float(value):.2f}"
    except (TypeError, ValueError):
        return "Not provided"


def _number(value, decimals=2):
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "Not provided"


def _format_time(value):
    if value is None:
        return "Not provided"

    try:
        if isinstance(value, datetime):
            dt = value
        else:
            text = str(value).strip()
            dt = datetime.fromisoformat(text)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        else:
            dt = dt.astimezone(IST)

        return dt.strftime("%d-%b-%Y %H:%M:%S IST")

    except Exception:
        return str(value)


def _get_risk(signal):
    risk = signal.get("risk")

    if not isinstance(risk, dict):
        return {}

    return risk


def send(text):
    """
    Send Telegram message.

    DRY_RUN=true:
        Print message only.

    DRY_RUN=false:
        Send to configured Telegram chat.
    """

    full = text.rstrip() + "\n\n" + DISCLAIMER

    if os.getenv("DRY_RUN", "true").strip().lower() == "true":
        print(full)
        return True

    token = _clean(os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_id = _clean(os.getenv("TELEGRAM_CHAT_ID"))

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID is not configured")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": full,
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=20,
        )

        if not response.ok:
            logger.error(
                "Telegram API error | status=%s | response=%s",
                response.status_code,
                response.text,
            )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok"):
            raise RuntimeError(
                f"Telegram API returned ok=false: {data}"
            )

        return True

    except Exception:
        logger.exception("Telegram request failed")
        raise


def signal_message(signal):
    """
    Build a new-signal Telegram message.

    Signal fields come from app.strategy.py / app.main.py:
      signal_price
      ltp
      candle_time / signal_time
      risk.entry
      risk.sl
      risk.t1
      risk.t2
      risk.t3
      regime.direction
      setup
      rsi
      rvol
      score
      grade
    """

    risk = _get_risk(signal)

    symbol = html.escape(
        _clean(signal.get("symbol")) or "UNKNOWN"
    )

    direction = _clean(signal.get("direction")).upper()
    grade = html.escape(
        _clean(signal.get("grade")) or "N/A"
    )

    if direction == "BUY":
        header = "🚀 BUY"
    elif direction == "SELL":
        header = "🔻 SELL"
    else:
        header = "📊 SIGNAL"

    signal_close = signal.get("signal_price")

    # During scan, ltp is currently initialized from signal_price.
    current_ltp = signal.get("ltp", signal_close)

    regime = signal.get("regime")

    if isinstance(regime, dict):
        regime_direction = regime.get("direction")
    else:
        regime_direction = regime

    regime_direction = html.escape(
        _clean(regime_direction) or "N/A"
    )

    setup = html.escape(
        _clean(signal.get("setup")) or "N/A"
    )

    score = signal.get("score")

    try:
        score_text = f"{int(score)}/100"
    except (TypeError, ValueError):
        score_text = "Not provided"

    return (
        f"{header} — {grade}\n\n"
        f"📌 {symbol}\n\n"
        f"Signal Candle Close: {_price(signal_close)}\n"
        f"Current LTP: {_price(current_ltp)}\n\n"
        f"Entry: {_price(risk.get('entry'))}\n"
        f"Stop Loss: {_price(risk.get('sl'))}\n"
        f"T1: {_price(risk.get('t1'))}\n"
        f"T2: {_price(risk.get('t2'))}\n"
        f"T3: {_price(risk.get('t3'))}\n\n"
        f"15M: {regime_direction}\n"
        f"5M: {setup}\n"
        f"RSI: {_number(signal.get('rsi'), 2)}\n"
        f"RVOL: {_number(signal.get('rvol'), 2)}x\n"
        f"Score: {score_text}\n"
        f"Signal Time: {_format_time(signal.get('candle_time') or signal.get('signal_time'))}"
    )


def exit_message(signal, exit_price, reason, exit_time):
    """
    Build EXIT Telegram message.
    """

    risk = _get_risk(signal)

    direction = _clean(signal.get("direction")).upper()

    entry = risk.get("entry")

    try:
        entry_value = float(entry)
        exit_value = float(exit_price)

        if direction == "BUY":
            move = (
                (exit_value - entry_value)
                / entry_value
                * 100
            )
        else:
            move = (
                (entry_value - exit_value)
                / entry_value
                * 100
            )

        move_text = f"{move:+.2f}%"

    except (TypeError, ValueError, ZeroDivisionError):
        move_text = "Not available"

    symbol = html.escape(
        _clean(signal.get("symbol")) or "UNKNOWN"
    )

    direction_text = html.escape(
        direction or "N/A"
    )

    reason_text = html.escape(
        _clean(reason) or "Not provided"
    )

    grade = html.escape(
        _clean(signal.get("grade")) or "N/A"
    )

    score = signal.get("score")

    try:
        score_text = f"{int(score)}/100"
    except (TypeError, ValueError):
        score_text = "Not provided"

    return (
        f"⚠️ EXIT — {symbol}\n\n"
        f"Direction: {direction_text}\n"
        f"Entry: {_price(entry)}\n"
        f"Exit: {_price(exit_price)}\n"
        f"Move: {move_text}\n\n"
        f"Reason: {reason_text}\n"
        f"Original Signal: {grade}\n"
        f"Original Score: {score_text}\n"
        f"Entry Time: {_format_time(signal.get('candle_time') or signal.get('signal_time'))}\n"
        f"Exit Time: {_format_time(exit_time)}"
    )
