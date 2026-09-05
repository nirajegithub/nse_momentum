from __future__ import annotations

import logging
import os
import unicodedata
from typing import Any

import requests

from .config import DISCLAIMER

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


def _clean_env(value: str | None) -> str:
    """Remove invisible/control characters from environment values."""
    if value is None:
        return ""

    value = unicodedata.normalize("NFKC", value)

    cleaned = "".join(
        ch
        for ch in value.strip()
        if unicodedata.category(ch) not in {"Cf", "Cc"}
    )

    return cleaned.strip()


def _get_credentials() -> tuple[str, str]:
    token = _clean_env(os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_id = _clean_env(os.getenv("TELEGRAM_CHAT_ID"))

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID is missing")

    return token, chat_id


def send(message: str) -> bool:
    """
    Send an HTML-formatted Telegram message.
    """
    token, chat_id = _get_credentials()

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=15,
        )

        if response.ok:
            logger.info("Telegram message sent successfully")
            return True

        try:
            body: Any = response.json()
        except ValueError:
            body = response.text[:1000]

        logger.error(
            "Telegram API error | status=%s | response=%s",
            response.status_code,
            body,
        )

        response.raise_for_status()
        return False

    except requests.RequestException as exc:
        logger.error("Telegram request failed | error=%s", exc)
        raise


def _get(signal: Any, *keys: str, default: Any = "") -> Any:
    """
    Read the first available field from either a dict or object.
    """
    for key in keys:
        if isinstance(signal, dict):
            value = signal.get(key)
        else:
            value = getattr(signal, key, None)

        if value is not None and value != "":
            return value

    return default


def _price(value: Any) -> str:
    """Format price to 2 decimal places."""
    if value is None or value == "":
        return ""

    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _number(value: Any) -> str:
    """Format numeric indicator to 2 decimal places."""
    if value is None or value == "":
        return ""

    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _time(value: Any) -> str:
    """Format timestamp for Telegram."""
    if value is None or value == "":
        return ""

    text = str(value)

    # Convert ISO timestamp to a cleaner display.
    if "T" in text:
        text = text.replace("T", " ")

    # Remove seconds if present.
    if len(text) >= 19 and text[10] == " ":
        text = text[:16]

    if "IST" not in text:
        text += " IST"

    return text


def _html(value: Any) -> str:
    """
    Basic HTML escaping.
    """
    if value is None:
        return ""

    text = str(value)

    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def signal_message(signal: Any) -> str:
    """
    Build formatted Telegram message for a new signal.
    """

    symbol = _get(signal, "symbol")
    direction = _get(signal, "direction", "action")
    setup = _get(signal, "setup")

    score = _get(signal, "score")
    grade = _get(signal, "grade")

    # Signal candle timestamp
    signal_time = _get(
        signal,
        "signal_time",
        "timestamp",
        "candle_time",
        "candle",
    )

    # Signal candle CLOSE PRICE
    candle_close = _get(
        signal,
        "signal_candle_close",
        "candle_close",
        "close",
        "price",
        default="",
    )

    # Entry
    entry = _get(
        signal,
        "entry",
        "entry_price",
        "entry_ltp",
        "ltp",
        default="",
    )

    # Stop loss
    sl = _get(
        signal,
        "sl",
        "stop_loss",
        "stop_loss_price",
        "sl_price",
        default="",
    )

    t1 = _get(signal, "t1", "target1", "target_1")
    t2 = _get(signal, "t2", "target2", "target_2")
    t3 = _get(signal, "t3", "target3", "target_3")

    # Current LTP
    ltp = _get(
        signal,
        "current_ltp",
        "ltp",
        "last_price",
        "price",
        default="",
    )

    rsi = _get(signal, "rsi", "rsi14")
    rvol = _get(signal, "rvol", "relative_volume")

    if str(direction).upper() == "BUY":
        direction_icon = "🟢"
    elif str(direction).upper() == "SELL":
        direction_icon = "🔴"
    else:
        direction_icon = "⚪"

    lines = [
        "📊 <b>NSE MOMENTUM SIGNAL</b>",
        "",
        f"📌 <b>Symbol:</b> {_html(symbol)}",
        f"{direction_icon} <b>Direction:</b> {_html(direction)}",
        f"⚙️ <b>Setup:</b> {_html(setup)}",
        f"🏆 <b>Score:</b> {_html(score)}",
        f"⭐ <b>Grade:</b> {_html(grade)}",
        "",
        f"🕯️ <b>Signal Candle Close:</b> {_price(candle_close)}",
        f"💰 <b>Current LTP:</b> {_price(ltp)}",
        f"⏰ <b>Signal Time:</b> {_time(signal_time)}",
        "",
        f"🎯 <b>Entry:</b> {_price(entry)}",
        f"🛑 <b>Stop Loss:</b> {_price(sl)}",
    ]

    if t1 not in (None, ""):
        lines.append(f"🎯 <b>T1:</b> {_price(t1)}")

    if t2 not in (None, ""):
        lines.append(f"🎯 <b>T2:</b> {_price(t2)}")

    if t3 not in (None, ""):
        lines.append(f"🎯 <b>T3:</b> {_price(t3)}")

    if rsi not in (None, ""):
        lines.append(f"📈 <b>RSI:</b> {_number(rsi)}")

    if rvol not in (None, ""):
        lines.append(f"📊 <b>RVOL:</b> {_number(rvol)}")

    lines.extend(
        [
            "",
            _html(DISCLAIMER),
        ]
    )

    return "\n".join(lines)


def exit_message(signal: Any) -> str:
    """
    Build formatted Telegram message for an exited signal.
    """

    symbol = _get(signal, "symbol")
    direction = _get(signal, "direction", "action")
    setup = _get(signal, "setup")

    entry = _get(
        signal,
        "entry",
        "entry_price",
    )

    exit_price = _get(
        signal,
        "exit",
        "exit_price",
        "exit_ltp",
    )

    exit_time = _get(
        signal,
        "exit_time",
        "timestamp",
        "signal_time",
    )

    pnl = _get(
        signal,
        "pnl_pct",
        "return_pct",
        "profit_pct",
    )

    reason = _get(
        signal,
        "exit_reason",
        "reason",
    )

    if str(direction).upper() == "BUY":
        direction_icon = "🟢"
    elif str(direction).upper() == "SELL":
        direction_icon = "🔴"
    else:
        direction_icon = "⚪"

    lines = [
        "📤 <b>NSE MOMENTUM EXIT</b>",
        "",
        f"📌 <b>Symbol:</b> {_html(symbol)}",
        f"{direction_icon} <b>Direction:</b> {_html(direction)}",
        f"⚙️ <b>Setup:</b> {_html(setup)}",
        "",
        f"🎯 <b>Entry:</b> {_price(entry)}",
        f"💰 <b>Exit:</b> {_price(exit_price)}",
        f"⏰ <b>Exit Time:</b> {_time(exit_time)}",
    ]

    if pnl not in (None, ""):
        lines.append(f"📈 <b>P&amp;L %:</b> {_number(pnl)}")

    if reason not in (None, ""):
        lines.append(f"ℹ️ <b>Reason:</b> {_html(reason)}")

    lines.extend(
        [
            "",
            _html(DISCLAIMER),
        ]
    )

    return "\n".join(lines)
