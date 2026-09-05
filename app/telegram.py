from __future__ import annotations

import logging
import os

import requests

from .config import DISCLAIMER


logger = logging.getLogger(__name__)


TELEGRAM_API = "https://api.telegram.org"


def _clean(value: str | None) -> str:
    """
    Clean GitHub Actions secret values without exposing them.
    """
    if value is None:
        return ""

    return (
        str(value)
        .replace("\ufeff", "")
        .replace("\u200b", "")
        .replace("\u200c", "")
        .replace("\u200d", "")
        .strip()
    )


def _telegram_config():
    token = _clean(os.getenv("TELEGRAM_BOT_TOKEN"))
    chat_id = _clean(os.getenv("TELEGRAM_CHAT_ID"))

    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

    if not chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID is missing")

    return token, chat_id


def send(text: str) -> bool:
    """
    Send a plain-text Telegram message.

    No parse_mode is used intentionally.
    This prevents Markdown/HTML formatting issues.
    """

    full = text.rstrip() + "\n\n" + DISCLAIMER

    dry_run = (
        _clean(os.getenv("DRY_RUN", "true")).lower()
        == "true"
    )

    if dry_run:
        logger.info("Telegram DRY_RUN=true")
        print(full)
        return True

    token, chat_id = _telegram_config()

    url = f"{TELEGRAM_API}/bot{token}/sendMessage"

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

        logger.info(
            "Telegram response | status=%s",
            response.status_code,
        )

        if response.status_code != 200:
            # Do not print token/chat_id.
            logger.error(
                "Telegram send failed | status=%s | body=%s",
                response.status_code,
                response.text[:500],
            )
            return False

        try:
            result = response.json()
        except Exception:
            logger.error(
                "Telegram returned non-JSON response"
            )
            return False

        if not result.get("ok"):
            logger.error(
                "Telegram API returned ok=false | description=%s",
                result.get("description", "unknown"),
            )
            return False

        logger.info("Telegram message sent successfully")
        return True

    except requests.RequestException as exc:
        logger.error(
            "Telegram request failed | %s",
            exc,
        )
        return False


def _fmt_price(value) -> str:
    try:
        return f"₹{float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_rvol(value) -> str:
    try:
        return f"{float(value):.2f}x"
    except (TypeError, ValueError):
        return "N/A"


def _signal_direction_header(direction: str, grade: str) -> str:
    if direction == "BUY":
        return f"🚀 BUY — {grade}"

    return f"🔻 SELL — {grade}"


def signal_message(s: dict) -> str:
    """
    Format a new signal.
    """

    risk = s["risk"]
    regime = s.get("regime", {})

    direction = s["direction"]
    grade = s.get("grade", "N/A")

    symbol = s.get("symbol", "N/A")
    signal_price = s.get(
        "signal_price",
        risk.get("entry"),
    )
    ltp = s.get(
        "ltp",
        signal_price,
    )

    signal_time = s.get(
        "signal_time",
        s.get("candle_time", "N/A"),
    )

    # Convert ISO timestamp to a clean display value.
    if isinstance(signal_time, str):
        signal_time = signal_time.replace("T", " ")
        if "+" in signal_time:
            signal_time = signal_time.split("+")[0]

    regime_direction = regime.get(
        "direction",
        "N/A",
    )

    setup = s.get(
        "setup",
        "N/A",
    )

    rsi = s.get(
        "rsi",
        0,
    )

    rvol = s.get(
        "rvol",
        0,
    )

    score = s.get(
        "score",
        0,
    )

    lines = [
        _signal_direction_header(
            direction,
            grade,
        ),
        "",
        symbol,
        "",
        f"📌 Signal Candle Close: {_fmt_price(signal_price)}",
        f"💰 Current LTP: {_fmt_price(ltp)}",
        f"🕐 Signal Time: {signal_time}",
        "",
        "🛡 Risk Management",
        f"Entry: {_fmt_price(risk.get('entry'))}",
        f"SL: {_fmt_price(risk.get('sl'))}",
        f"T1: {_fmt_price(risk.get('t1'))}",
        f"T2: {_fmt_price(risk.get('t2'))}",
        f"T3: {_fmt_price(risk.get('t3'))}",
        "",
        "📊 Technical Setup",
        f"15M Regime: {regime_direction}",
        f"5M Setup: {setup}",
        f"RSI: {float(rsi):.1f}",
        f"RVOL: {_fmt_rvol(rvol)}",
        f"Score: {int(score)}/100",
    ]

    return "\n".join(lines)


def exit_message(
    s: dict,
    exit_price: float,
    reason: str,
    exit_time: str,
) -> str:
    """
    Format an EXIT message.
    """

    risk = s["risk"]

    entry = float(risk["entry"])
    exit_price = float(exit_price)

    if s["direction"] == "BUY":
        move = (
            (exit_price - entry)
            / entry
            * 100
        )
    else:
        move = (
            (entry - exit_price)
            / entry
            * 100
        )

    signal_time = s.get(
        "signal_time",
        s.get("candle_time", "N/A"),
    )

    if isinstance(signal_time, str):
        signal_time = signal_time.replace(
            "T",
            " ",
        )

    return "\n".join(
        [
            f"⚠️ EXIT — {s.get('symbol', 'N/A')}",
            "",
            f"Direction: {s.get('direction', 'N/A')}",
            f"Entry: {_fmt_price(entry)}",
            f"Exit: {_fmt_price(exit_price)}",
            f"Move: {move:+.2f}%",
            "",
            f"Reason: {reason}",
            f"Original Signal: {s.get('grade', 'N/A')}",
            f"Original Score: {s.get('score', 0)}/100",
            f"Entry Time: {signal_time}",
            f"Exit Time: {exit_time}",
        ]
    )
