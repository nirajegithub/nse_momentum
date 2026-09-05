from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import logging
import time
import requests

from .config import SETTINGS
from .calendar import previous_trading_day
from .dhan_client import DhanClient


LOG = logging.getLogger(__name__)

BASE = "https://www.nseindia.com"
URL = f"{BASE}/api/heatmap-symbols"

INDEXES = {
    "M50": "NIFTY500MOMENTM50",
    "M30": "NIFTY200MOMENTM30",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/",
    "Connection": "keep-alive",
}

IST = ZoneInfo("Asia/Kolkata")

NSE_TIMEOUT = 45
NSE_RETRIES = 3
NSE_RETRY_DELAY = 5


def extract_symbols(payload):
    found = set()

    keys = {
        "symbol",
        "tradingsymbol",
        "tradingSymbol",
        "symbolCode",
    }

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():

                if k in keys and isinstance(v, str):
                    s = v.strip().upper()

                    if (
                        s
                        and len(s) <= 40
                        and s.replace("-", "").isalnum()
                    ):
                        found.add(s)

                else:
                    walk(v)

        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(payload)

    return sorted(found)


def create_nse_session():
    session = requests.Session()
    session.headers.update(HEADERS)

    return session


def nse_get(session, url, params=None):
    """
    Reliable NSE GET request with retries.

    Retries:
    - ReadTimeout
    - ConnectionError
    - HTTP 429
    - HTTP 5xx
    """

    last_exception = None

    for attempt in range(1, NSE_RETRIES + 1):

        try:

            LOG.info(
                "NSE request attempt %d/%d | url=%s | params=%s",
                attempt,
                NSE_RETRIES,
                url,
                params,
            )

            response = session.get(
                url,
                params=params,
                timeout=NSE_TIMEOUT,
            )

            LOG.info(
                "NSE response | status=%d | attempt=%d/%d",
                response.status_code,
                attempt,
                NSE_RETRIES,
            )

            # Retry rate-limit responses.
            if response.status_code == 429:

                LOG.warning(
                    "NSE rate limited (429). Waiting %d seconds.",
                    NSE_RETRY_DELAY,
                )

                time.sleep(NSE_RETRY_DELAY)
                continue

            # Retry temporary NSE/server errors.
            if response.status_code >= 500:

                LOG.warning(
                    "NSE server error %d. Waiting %d seconds.",
                    response.status_code,
                    NSE_RETRY_DELAY,
                )

                time.sleep(NSE_RETRY_DELAY)
                continue

            response.raise_for_status()

            return response

        except (
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ConnectionError,
        ) as exc:

            last_exception = exc

            LOG.warning(
                "NSE request failed on attempt %d/%d: %s",
                attempt,
                NSE_RETRIES,
                exc,
            )

            if attempt < NSE_RETRIES:
                LOG.info(
                    "Retrying NSE request in %d seconds...",
                    NSE_RETRY_DELAY,
                )

                time.sleep(NSE_RETRY_DELAY)

    if last_exception is not None:
        raise last_exception

    raise RuntimeError("NSE request failed after retries")


def fetch_index(session, code):

    response = nse_get(
        session,
        URL,
        params={
            "type": "Strategy Indices",
            "indices": code,
        },
    )

    symbols = extract_symbols(response.json())

    LOG.info(
        "NSE index=%s returned %d symbols",
        code,
        len(symbols),
    )

    return symbols


def build_universe(dhan: DhanClient, as_of_date=None):

    # ---------------------------------------------------------
    # 1. Fetch M50 + M30
    # ---------------------------------------------------------

    session = create_nse_session()

    # NSE sometimes requires an initial homepage request
    # before API requests are accepted.
    try:

        LOG.info("NSE session initialization")

        nse_get(
            session,
            BASE + "/",
        )

        LOG.info("NSE homepage session initialized")

    except Exception as exc:

        LOG.warning(
            "NSE homepage initialization failed: %s",
            exc,
        )

        # Continue. The API request itself may still work.

    membership = defaultdict(set)

    for name, code in INDEXES.items():

        symbols = fetch_index(
            session,
            code,
        )

        LOG.info(
            "%s returned %d symbols",
            name,
            len(symbols),
        )

        for symbol in symbols:
            membership[symbol].add(name)

    symbols = sorted(membership)

    LOG.info(
        "Merged M50 + M30 universe: %d symbols",
        len(symbols),
    )

    # ---------------------------------------------------------
    # 2. Dhan Security ID mapping
    # ---------------------------------------------------------

    mapping = dhan.build_symbol_map(symbols)

    candidates = []

    for symbol in symbols:

        meta = mapping.get(symbol)

        if not meta:

            LOG.warning(
                "Missing Dhan security_id: %s",
                symbol,
            )

            continue

        candidates.append(
            {
                "symbol": symbol,
                **meta,
                "indices": sorted(
                    membership[symbol]
                ),
                "membership_count": len(
                    membership[symbol]
                ),
            }
        )

    LOG.info(
        "Dhan-mapped candidates: %d",
        len(candidates),
    )

    # ---------------------------------------------------------
    # 3. Previous trading day
    # ---------------------------------------------------------

    if as_of_date:
        if isinstance(as_of_date, str):
            today = datetime.strptime(
                as_of_date,
                "%Y-%m-%d",
            ).date()
        else:
            today = as_of_date
    else:
        today = datetime.now(IST).date()
    
    prev_day = previous_trading_day(today)

    LOG.info(
        "Using previous trading day: %s",
        prev_day.isoformat(),
    )

    # Dhan's toDate is non-inclusive.
    from_date = prev_day.isoformat()
    to_date = (
        prev_day + timedelta(days=1)
    ).isoformat()

    LOG.info(
        "Daily historical range: from=%s to=%s",
        from_date,
        to_date,
    )

    # ---------------------------------------------------------
    # 4. Apply price + previous-day volume filters
    # ---------------------------------------------------------

    result = []

    price_rejected = 0
    volume_rejected = 0
    data_failed = 0

    for item in candidates:

        symbol = item["symbol"]
        security_id = item["security_id"]

        try:

            df = dhan.historical_daily_df(
                security_id=security_id,
                from_date=from_date,
                to_date=to_date,
            )

            if df.empty:

                LOG.warning(
                    "%s: no daily data",
                    symbol,
                )

                data_failed += 1
                continue

            # Find the previous trading-day candle.
            prev_rows = df[
                df.index.date == prev_day
            ]

            if prev_rows.empty:

                LOG.warning(
                    "%s: no candle for %s",
                    symbol,
                    prev_day.isoformat(),
                )

                data_failed += 1
                continue

            candle = prev_rows.iloc[-1]

            close_price = float(
                candle["close"]
            )

            prev_volume = int(
                candle["volume"]
            )

            # -------------------------------------------------
            # Price filter
            # -------------------------------------------------

            if close_price <= SETTINGS.min_price:

                price_rejected += 1

                LOG.info(
                    "%s rejected: price %.2f <= %.2f",
                    symbol,
                    close_price,
                    SETTINGS.min_price,
                )

                continue

            # -------------------------------------------------
            # Previous-day volume filter
            # -------------------------------------------------

            if prev_volume <= SETTINGS.min_prev_volume:

                volume_rejected += 1

                LOG.info(
                    "%s rejected: volume %d <= %d",
                    symbol,
                    prev_volume,
                    SETTINGS.min_prev_volume,
                )

                continue

            # -------------------------------------------------
            # Passed both filters
            # -------------------------------------------------

            item["prev_close"] = close_price
            item["prev_volume"] = prev_volume
            item["prev_trading_day"] = (
                prev_day.isoformat()
            )

            result.append(item)

            LOG.info(
                "%s PASSED: price=%.2f volume=%d",
                symbol,
                close_price,
                prev_volume,
            )

        except Exception as exc:

            data_failed += 1

            LOG.exception(
                "%s daily filter failed: %s",
                symbol,
                exc,
            )

    # ---------------------------------------------------------
    # 5. Final summary
    # ---------------------------------------------------------

    LOG.info(
        "Price filter rejected: %d",
        price_rejected,
    )

    LOG.info(
        "Volume filter rejected: %d",
        volume_rejected,
    )

    LOG.info(
        "Daily data failures: %d",
        data_failed,
    )

    LOG.info(
        "FINAL UNIVERSE: %d symbols",
        len(result),
    )

    return result
