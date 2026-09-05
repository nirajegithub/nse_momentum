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
    """Remove invisible Unicode/control characters from environment values."""
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
    Send a plain-text Telegram message.

    Plain text is intentional for V1 so Markdown/HTML parsing
    cannot cause Telegram HTTP 400 errors.
    """
    token, chat_id = _get_credentials()

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": message,
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


def _get(signal: Any, key: str, default: Any = "") -> Any:
    """Read a field from either a dict or an object."""
    if isinstance(signal, dict):
        return signal.get(key, default)

    return getattr(signal, key, default)


def _format_value(value: Any) -> str:
    if value is None:
        return ""

    return str(value)


def signal_message(signal: Any) -> str:
    """Build the Telegram message for a new signal."""

    symbol = _get(signal, "symbol")
    direction = _get(signal, "direction")
    setup = _get(signal, "setup")
    score = _get(signal, "score")
    grade = _get(signal, "grade")

    candle = _get(
        signal,
        "signal_candle_close",
        _get(signal, "candle", ""),
    )

    entry = _get(signal, "entry")
    sl = _get(
        signal,
        "sl",
        _get(signal, "stop_loss", ""),
    )

    t1 = _get(signal, "t1")
    t2 = _get(signal, "t2")
    t3 = _get(signal, "t3")

    ltp = _get(
        signal,
        "current_ltp",
        _get(signal, "ltp", ""),
    )

    signal_time = _get(
        signal,
        "signal_time",
        _get(signal, "timestamp", ""),
    )

    rsi = _get(signal, "rsi")
    rvol = _get(signal, "rvol")

    lines = [
        "📊 NSE MOMENTUM SIGNAL",
        "",
        f"Symbol: {_format_value(symbol)}",
        f"Direction: {_format_value(direction)}",
        f"Setup: {_format_value(setup)}",
        f"Score: {_format_value(score)}",
        f"Grade: {_format_value(grade)}",
        "",
        f"Signal Candle Close: {_format_value(candle)}",
        f"Current LTP: {_format_value(ltp)}",
        f"Signal Time: {_format_value(signal_time)}",
        "",
        f"Entry: {_format_value(entry)}",
        f"Stop Loss: {_format_value(sl)}",
    ]

    if t1 not in (None, ""):
        lines.append(f"T1: {_format_value(t1)}")

    if t2 not in (None, ""):
        lines.append(f"T2: {_format_value(t2)}")

    if t3 not in (None, ""):
        lines.append(f"T3: {_format_value(t3)}")

    if rsi not in (None, ""):
        lines.append(f"RSI: {_format_value(rsi)}")

    if rvol not in (None, ""):
        lines.append(f"RVOL: {_format_value(rvol)}")

    lines.extend(
        [
            "",
            DISCLAIMER,
        ]
    )

    return "\n".join(lines)


def exit_message(signal: Any) -> str:
    """Build the Telegram message for an exited signal."""

    symbol = _get(signal, "symbol")
    direction = _get(signal, "direction")
    setup = _get(signal, "setup")

    entry = _get(signal, "entry")
    exit_price = _get(
        signal,
        "exit",
        _get(signal, "exit_price", ""),
    )

    exit_time = _get(
        signal,
        "exit_time",
        _get(signal, "signal_time", ""),
    )

    pnl = _get(
        signal,
        "pnl_pct",
        _get(signal, "return_pct", ""),
    )

    reason = _get(
        signal,
        "exit_reason",
        _get(signal, "reason", ""),
    )

    lines = [
        "📤 NSE MOMENTUM EXIT",
        "",
        f"Symbol: {_format_value(symbol)}",
        f"Direction: {_format_value(direction)}",
        f"Setup: {_format_value(setup)}",
        "",
        f"Entry: {_format_value(entry)}",
        f"Exit: {_format_value(exit_price)}",
        f"Exit Time: {_format_value(exit_time)}",
    ]

    if pnl not in (None, ""):
        lines.append(f"P&L %: {_format_value(pnl)}")

    if reason not in (None, ""):
        lines.append(f"Reason: {_format_value(reason)}")

    lines.extend(
        [
            "",
            DISCLAIMER,
        ]
    )

    return "\n".join(lines)
