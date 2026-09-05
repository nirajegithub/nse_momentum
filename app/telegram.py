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
    """Remove invisible/unicode formatting characters and surrounding whitespace."""
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

    Plain text is intentionally used for V1 so that Markdown/HTML parsing
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

        # Never log the bot token or full URL.
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


def signal_message(signal: Any) -> str:
    """
    Build the Telegram signal message.

    Supports the existing dict/object signal structure used by app.main.
    """
    symbol = _get(signal, "symbol", "")
    direction = _get(signal, "direction", "")
    setup = _get(signal, "setup", "")
    score = _get(signal, "score", "")
    grade = _get(signal, "grade", "")

    candle = _get(
        signal,
        "signal_candle_close",
        _get(signal, "candle", ""),
    )

    entry = _get(signal, "entry", "")
    sl = _get(signal, "sl", _get(signal, "stop_loss", ""))

    t1 = _get(signal, "t1", "")
    t2 = _get(signal, "t2", "")
    t3 = _get(signal, "t3", "")

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

    rsi = _get(signal, "rsi", "")
    rvol = _get(signal, "rvol", "")

    lines = [
        "📊 NSE MOMENTUM SIGNAL",
        "",
        f"Symbol: {symbol}",
        f"Direction: {direction}",
        f"Setup: {setup}",
        f"Score: {score}",
        f"Grade: {grade}",
        "",
        f"Signal Candle Close: {candle}",
        f"Current LTP: {ltp}",
        f"Signal Time: {signal_time}",
        "",
        f"Entry: {entry}",
        f"Stop Loss: {sl}",
    ]

    if t1 != "":
        lines.append(f"T1: {t1}")
    if t2 != "":
        lines.append(f"T2: {t2}")
    if t3 != "":
        lines.append(f"T3: {t3}")

    if rsi != "":
        lines.append(f"RSI: {rsi}")

    if rvol != "":
        lines.append(f"RVOL: {rvol}")

    lines.extend(
        [
            "",
            DISCLAIMER,
        ]
    )

    return "\n".join(lines)
