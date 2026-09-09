from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyotp
import requests
from nacl.public import PublicKey, SealedBox


IST = ZoneInfo("Asia/Kolkata")

# Dhan
DHAN_TOKEN_URL = "https://auth.dhan.co/app/generateAccessToken"
DHAN_PROFILE_URL = "https://api.dhan.co/v2/profile"

# GitHub
GITHUB_API = "https://api.github.com"
GITHUB_OWNER = "nirajegithub"
GITHUB_REPO = "nse_momentum"
GITHUB_SECRET_NAME = "DHAN_ACCESS_TOKEN"

# Existing project holiday file
HOLIDAY_FILE = Path("data/nse_holidays.json")

REQUEST_TIMEOUT = 20


def log(message: str) -> None:
    print(
        f"[{datetime.now(IST):%Y-%m-%d %H:%M:%S} IST] {message}",
        flush=True,
    )


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()

    if not value:
        raise RuntimeError(
            f"Required secret is missing: {name}"
        )

    return value


# ---------------------------------------------------------
# NSE TRADING DAY
# ---------------------------------------------------------

def load_holidays() -> set[str]:
    if not HOLIDAY_FILE.exists():
        raise RuntimeError(
            f"Holiday file not found: {HOLIDAY_FILE}"
        )

    with HOLIDAY_FILE.open(
        "r",
        encoding="utf-8",
    ) as file:
        data = json.load(file)

    if data.get("market") != "CM":
        raise RuntimeError(
            "nse_holidays.json must contain market='CM'."
        )

    holidays = data.get("holidays", [])

    if not isinstance(holidays, list):
        raise RuntimeError(
            "nse_holidays.json 'holidays' must be a list."
        )

    return {
        str(value).strip()
        for value in holidays
    }


def is_nse_trading_day(
    now: datetime,
) -> bool:

    # Saturday / Sunday
    if now.weekday() >= 5:
        log("SKIP | Weekend.")
        return False

    today = now.strftime("%Y-%m-%d")

    holidays = load_holidays()

    if today in holidays:
        log(
            f"SKIP | NSE CM holiday | {today}"
        )
        return False

    log(
        f"TRADING DAY | {today}"
    )

    return True


# ---------------------------------------------------------
# DHAN TOKEN GENERATION
# ---------------------------------------------------------

def generate_dhan_token() -> tuple[str, str]:

    client_id = required_env(
        "DHAN_CLIENT_ID"
    )

    pin = required_env(
        "DHAN_PIN"
    )

    totp_secret = required_env(
        "DHAN_TOTP_SECRET"
    )

    # Generate current 6-digit TOTP.
    totp_code = pyotp.TOTP(
        totp_secret
    ).now()

    log(
        "Generating fresh Dhan access token..."
    )

    response = requests.post(
        DHAN_TOKEN_URL,
        params={
            "dhanClientId": client_id,
            "pin": pin,
            "totp": totp_code,
        },
        timeout=REQUEST_TIMEOUT,
    )

    if not response.ok:
        raise RuntimeError(
            "Dhan token generation failed | "
            f"HTTP {response.status_code} | "
            f"{response.text[:500]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Dhan token API returned invalid JSON."
        ) from exc

    token = str(
        payload.get("accessToken", "")
    ).strip()

    expiry = str(
        payload.get("expiryTime", "")
    ).strip()

    if not token:
        raise RuntimeError(
            "Dhan response did not contain "
            "accessToken."
        )

    log(
        "SUCCESS | Fresh Dhan token generated."
    )

    return token, expiry


# ---------------------------------------------------------
# GITHUB SECRET UPDATE
# ---------------------------------------------------------

def github_headers() -> dict[str, str]:

    github_token = required_env(
        "GH_SECRET_UPDATE_TOKEN"
    )

    return {
        "Accept": (
            "application/vnd.github+json"
        ),
        "Authorization": (
            f"Bearer {github_token}"
        ),
        "X-GitHub-Api-Version": "2026-03-10",
        "Content-Type": "application/json",
    }


def get_github_public_key() -> tuple[str, str]:

    url = (
        f"{GITHUB_API}/repos/"
        f"{GITHUB_OWNER}/"
        f"{GITHUB_REPO}/"
        "actions/secrets/public-key"
    )

    response = requests.get(
        url,
        headers=github_headers(),
        timeout=REQUEST_TIMEOUT,
    )

    if not response.ok:
        raise RuntimeError(
            "GitHub public-key request failed | "
            f"HTTP {response.status_code} | "
            f"{response.text[:500]}"
        )

    payload = response.json()

    public_key = str(
        payload.get("key", "")
    ).strip()

    key_id = str(
        payload.get("key_id", "")
    ).strip()

    if not public_key or not key_id:
        raise RuntimeError(
            "GitHub public-key response is incomplete."
        )

    return public_key, key_id


def encrypt_secret(
    secret_value: str,
    public_key_b64: str,
) -> str:

    try:
        public_key = PublicKey(
            base64.b64decode(
                public_key_b64
            )
        )

        sealed_box = SealedBox(
            public_key
        )

        encrypted = sealed_box.encrypt(
            secret_value.encode("utf-8")
        )

        return base64.b64encode(
            encrypted
        ).decode("utf-8")

    except Exception as exc:
        raise RuntimeError(
            f"Unable to encrypt GitHub secret: {exc}"
        ) from exc


def update_github_secret(
    new_token: str,
) -> None:

    public_key, key_id = (
        get_github_public_key()
    )

    encrypted_value = encrypt_secret(
        new_token,
        public_key,
    )

    url = (
        f"{GITHUB_API}/repos/"
        f"{GITHUB_OWNER}/"
        f"{GITHUB_REPO}/"
        f"actions/secrets/"
        f"{GITHUB_SECRET_NAME}"
    )

    response = requests.put(
        url,
        headers=github_headers(),
        json={
            "encrypted_value": encrypted_value,
            "key_id": key_id,
        },
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code not in (
        201,
        204,
    ):
        raise RuntimeError(
            "GitHub secret update failed | "
            f"HTTP {response.status_code} | "
            f"{response.text[:500]}"
        )

    log(
        "SUCCESS | GitHub secret updated | "
        f"{GITHUB_SECRET_NAME}"
    )


# ---------------------------------------------------------
# DHAN TOKEN HEALTH CHECK
# ---------------------------------------------------------

def validate_token(
    token: str,
) -> str:

    response = requests.get(
        DHAN_PROFILE_URL,
        headers={
            "access-token": token,
        },
        timeout=REQUEST_TIMEOUT,
    )

    if not response.ok:
        raise RuntimeError(
            "Dhan token health check failed | "
            f"HTTP {response.status_code} | "
            f"{response.text[:500]}"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Dhan profile returned invalid JSON."
        ) from exc

    token_validity = str(
        payload.get(
            "tokenValidity",
            "",
        )
    ).strip()

    if not token_validity:
        raise RuntimeError(
            "Dhan profile response did not "
            "contain tokenValidity."
        )

    return token_validity


# ---------------------------------------------------------
# PRIVATE TELEGRAM
# ---------------------------------------------------------

def send_telegram(
    message: str,
) -> None:

    bot_token = required_env(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = required_env(
        "TELEGRAM_TOKEN_ALERT_CHAT_ID"
    )

    url = (
        "https://api.telegram.org/"
        f"bot{bot_token}/sendMessage"
    )

    response = requests.post(
        url,
        json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=REQUEST_TIMEOUT,
    )

    if not response.ok:
        raise RuntimeError(
            "Telegram notification failed | "
            f"HTTP {response.status_code} | "
            f"{response.text[:500]}"
        )

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {result}"
        )


def success_message(
    expiry: str,
    token_validity: str,
) -> str:

    checked = datetime.now(
        IST
    ).strftime(
        "%Y-%m-%d %H:%M:%S IST"
    )

    return (
        "🔐 <b>Dhan Token Health Check</b>\n\n"

        "Status: ✅ <b>VALID</b>\n\n"

        "Fresh access token generated successfully.\n"
        "GitHub <code>DHAN_ACCESS_TOKEN</code> "
        "updated.\n"
        "Token health check passed.\n\n"

        f"🕐 <b>Checked:</b> {checked}\n"
        f"⏳ <b>Dhan Expiry:</b> "
        f"{expiry or 'N/A'}\n"
        f"🔎 <b>Token Validity:</b> "
        f"{token_validity}\n\n"

        "<b>Next:</b>\n"
        "09:22 → Build Universe\n"
        "09:30 → Scanner starts"
    )


def failure_message(
    error: Exception,
) -> str:

    checked = datetime.now(
        IST
    ).strftime(
        "%Y-%m-%d %H:%M:%S IST"
    )

    return (
        "🚨 <b>Dhan Token Health Check</b>\n\n"

        "Status: ❌ <b>FAILED</b>\n\n"

        "Automatic Dhan token management failed.\n\n"

        f"🕐 <b>Checked:</b> {checked}\n"
        f"❗ <b>Error:</b> "
        f"{str(error)[:800]}\n\n"

        "⚠️ <b>NSE Momentum Scanner should NOT "
        "be considered operational until this "
        "is resolved.</b>"
    )


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main() -> int:

    now = datetime.now(IST)

    log(
        "Dhan Token Manager started | "
        f"{now:%Y-%m-%d %H:%M:%S %Z}"
    )

    # Weekend / NSE holiday
    if not is_nse_trading_day(now):
        return 0

    try:

        # 1. Generate fresh token
        token, expiry = (
            generate_dhan_token()
        )

        # 2. Update GitHub secret
        update_github_secret(
            token
        )

        # 3. Validate new token
        log(
            "Validating newly generated "
            "Dhan token..."
        )

        token_validity = validate_token(
            token
        )

        log(
            "SUCCESS | Dhan token health "
            f"check passed | "
            f"tokenValidity={token_validity}"
        )

        # 4. Send success notification
        send_telegram(
            success_message(
                expiry,
                token_validity,
            )
        )

        log(
            "SUCCESS | Private Telegram "
            "notification sent."
        )

        return 0

    except Exception as exc:

        log(
            f"ERROR | {exc}"
        )

        # Failure notification
        try:

            send_telegram(
                failure_message(
                    exc
                )
            )

            log(
                "Private Telegram failure "
                "notification sent."
            )

        except Exception as telegram_exc:

            log(
                "ERROR | Could not send "
                "failure Telegram notification | "
                f"{telegram_exc}"
            )

        # Make GitHub Actions visibly failed.
        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )
