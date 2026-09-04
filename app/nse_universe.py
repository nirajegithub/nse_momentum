from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import logging
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
        "AppleWebKit/537.36 Chrome/124 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/",
}

IST = ZoneInfo("Asia/Kolkata")


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


def fetch_index(session, code):
    r = session.get(
        URL,
        params={
            "type": "Strategy Indices",
            "indices": code,
        },
        timeout=20,
    )

    r.raise_for_status()

    return extract_symbols(r.json())


def build_universe(dhan: DhanClient):

    # ---------------------------------------------------------
    # 1. Fetch M50 + M30
    # ---------------------------------------------------------

    session = requests.Session()
    session.headers.update(HEADERS)

    session.get(
        BASE + "/",
        timeout=20,
    )

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

    today = datetime.now(IST).date()

    prev_day = previous_trading_day(today)

    LOG.info(
        "Using previous trading day: %s",
        prev_day.isoformat(),
    )

    from_date = prev_day.isoformat()
    to_date = (prev_day + timedelta(days=1)).isoformat()

    to_date = prev_day.isoformat()

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

            # Price filter
            if close_price <= SETTINGS.min_price:

                price_rejected += 1

                LOG.info(
                    "%s rejected: price %.2f <= %.2f",
                    symbol,
                    close_price,
                    SETTINGS.min_price,
                )

                continue

            # Previous-day volume filter
            if prev_volume <= SETTINGS.min_prev_volume:

                volume_rejected += 1

                LOG.info(
                    "%s rejected: volume %d <= %d",
                    symbol,
                    prev_volume,
                    SETTINGS.min_prev_volume,
                )

                continue

            # Passed both filters
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
