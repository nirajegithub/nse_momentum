from __future__ import annotations

import logging
import os

import pandas as pd
import requests
from dhanhq import DhanContext, dhanhq


LOG = logging.getLogger(__name__)


DHAN_DETAILED_MASTER_URL = (
    "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
)

DHAN_HISTORICAL_URL = (
    "https://api.dhan.co/v2/charts/historical"
)


class DhanClient:
    def __init__(self) -> None:
        client_id = os.environ["DHAN_CLIENT_ID"]
        token = os.environ["DHAN_ACCESS_TOKEN"]

        self.access_token = token

        self.context = DhanContext(
            client_id,
            token,
        )

        self.dhan = dhanhq(self.context)

    # ------------------------------------------------------------------
    # Dhan Security Master
    # ------------------------------------------------------------------

    def security_master(self) -> pd.DataFrame:
        df = pd.read_csv(
            DHAN_DETAILED_MASTER_URL,
            low_memory=False,
        )

        LOG.info(
            "Dhan detailed master: %d rows loaded",
            len(df),
        )

        return df

    def build_symbol_map(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:

        df = self.security_master()

        required = [
            "EXCH_ID",
            "SEGMENT",
            "SECURITY_ID",
            "INSTRUMENT",
            "UNDERLYING_SYMBOL",
        ]

        missing = [
            column
            for column in required
            if column not in df.columns
        ]

        if missing:
            raise RuntimeError(
                f"Missing Dhan master columns: {missing}"
            )

        df = df[
            (df["EXCH_ID"].astype(str).str.upper() == "NSE")
            & (df["SEGMENT"].astype(str).str.upper() == "E")
            & (
                df["INSTRUMENT"]
                .astype(str)
                .str.upper()
                == "EQUITY"
            )
        ].copy()

        wanted = {
            str(symbol).strip().upper()
            for symbol in symbols
        }

        df["UNDERLYING_SYMBOL"] = (
            df["UNDERLYING_SYMBOL"]
            .astype(str)
            .str.strip()
            .str.upper()
        )

        matched = df[
            df["UNDERLYING_SYMBOL"].isin(wanted)
        ]

        result: dict[str, dict] = {}

        for _, row in matched.iterrows():

            symbol = row["UNDERLYING_SYMBOL"]

            result[symbol] = {
                "security_id": str(
                    row["SECURITY_ID"]
                ),
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY",
            }

        LOG.info(
            "Dhan Security ID mapping: %d/%d symbols matched",
            len(result),
            len(wanted),
        )

        return result

    # ------------------------------------------------------------------
    # Daily Historical Data
    # ------------------------------------------------------------------

    def historical_daily_df(
        self,
        security_id: str,
        from_date: str,
        to_date: str,
    ) -> pd.DataFrame:

        headers = {
            "Content-Type": "application/json",
            "access-token": self.access_token,
        }

        payload = {
            "securityId": str(security_id),
            "exchangeSegment": "NSE_EQ",
            "instrument": "EQUITY",
            "expiryCode": 0,
            "oi": False,
            "fromDate": from_date,
            "toDate": to_date,
        }

        try:
            response = requests.post(
                DHAN_HISTORICAL_URL,
                headers=headers,
                json=payload,
                timeout=20,
            )

            if response.status_code != 200:
                LOG.error(
                    "Dhan historical API failed: "
                    "security_id=%s HTTP=%s response=%s",
                    security_id,
                    response.status_code,
                    response.text,
                )
                return pd.DataFrame()

            data = response.json()

            required = [
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]

            missing = [
                key
                for key in required
                if key not in data
            ]

            if missing:
                LOG.error(
                    "Dhan historical response missing fields: "
                    "security_id=%s missing=%s response=%s",
                    security_id,
                    missing,
                    data,
                )
                return pd.DataFrame()

            n = min(
                len(data[key])
                for key in required
            )

            if n == 0:
                LOG.warning(
                    "Dhan historical API returned no candles: "
                    "security_id=%s from=%s to=%s",
                    security_id,
                    from_date,
                    to_date,
                )
                return pd.DataFrame()

            df = pd.DataFrame(
                {
                    "timestamp": data["timestamp"][:n],
                    "open": data["open"][:n],
                    "high": data["high"][:n],
                    "low": data["low"][:n],
                    "close": data["close"][:n],
                    "volume": data["volume"][:n],
                }
            )

            df["timestamp"] = pd.to_datetime(
                df["timestamp"],
                unit="s",
                utc=True,
            ).dt.tz_convert("Asia/Kolkata")

            df = (
                df
                .set_index("timestamp")
                .sort_index()
            )

            return df

        except requests.RequestException as exc:

            LOG.error(
                "Dhan historical HTTP exception: "
                "security_id=%s error=%s",
                security_id,
                exc,
            )
            return pd.DataFrame()

        except Exception as exc:

            LOG.exception(
                "Dhan historical API exception: "
                "security_id=%s error=%s",
                security_id,
                exc,
            )
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Intraday Minute Data
    # ------------------------------------------------------------------

    def intraday_df(
        self,
        security_id: str,
        interval: int,
        from_date: str,
        to_date: str,
    ) -> pd.DataFrame:

        try:
            payload = self.dhan.intraday_minute_data(
                security_id=str(security_id),
                exchange_segment="NSE_EQ",
                instrument_type="EQUITY",
                from_date=from_date,
                to_date=to_date,
                interval=1,
            )

        except Exception as exc:

            LOG.exception(
                "Dhan intraday API exception: "
                "security_id=%s error=%s",
                security_id,
                exc,
            )
            return pd.DataFrame()

        # --------------------------------------------------------------
        # Dhan SDK response format:
        #
        # {
        #     "status": "success",
        #     "remarks": "",
        #     "data": {
        #         "open": [...],
        #         "high": [...],
        #         "low": [...],
        #         "close": [...],
        #         "volume": [...],
        #         "timestamp": [...]
        #     }
        # }
        # --------------------------------------------------------------

        if not isinstance(payload, dict):

            LOG.error(
                "Dhan intraday response is not a dict: "
                "security_id=%s type=%s",
                security_id,
                type(payload).__name__,
            )

            return pd.DataFrame()

        # Check API status
        status = payload.get("status")

        if status != "success":

            LOG.error(
                "Dhan intraday API unsuccessful: "
                "security_id=%s status=%s remarks=%s",
                security_id,
                status,
                payload.get("remarks", ""),
            )

            return pd.DataFrame()

        # Extract nested data
        data = payload.get("data")

        if not isinstance(data, dict):

            LOG.error(
                "Dhan intraday response missing data object: "
                "security_id=%s",
                security_id,
            )

            return pd.DataFrame()

        # --------------------------------------------------------------
        # Temporary concise validation log
        # --------------------------------------------------------------

        if str(security_id) == "21614":

            LOG.info(
                "Dhan intraday response validated: "
                "security_id=%s data_keys=%s",
                security_id,
                list(data.keys()),
            )

            LOG.info(
                "Dhan intraday candle counts: "
                "security_id=%s counts=%s",
                security_id,
                {
                    key: len(data[key])
                    for key in data
                    if isinstance(data[key], list)
                },
            )

        # --------------------------------------------------------------
        # Required OHLCV fields
        # --------------------------------------------------------------

        keys = [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

        missing = [
            key
            for key in keys
            if key not in data
        ]

        if missing:

            LOG.error(
                "Dhan intraday data missing fields: "
                "security_id=%s missing=%s",
                security_id,
                missing,
            )

            return pd.DataFrame()

        # --------------------------------------------------------------
        # Validate arrays
        # --------------------------------------------------------------

        if not all(
            isinstance(data[key], list)
            for key in keys
        ):

            LOG.error(
                "Dhan intraday OHLCV fields are not lists: "
                "security_id=%s",
                security_id,
            )

            return pd.DataFrame()

        n = min(
            len(data[key])
            for key in keys
        )

        if n == 0:

            LOG.warning(
                "Dhan intraday API returned no candles: "
                "security_id=%s from=%s to=%s",
                security_id,
                from_date,
                to_date,
            )

            return pd.DataFrame()

        # --------------------------------------------------------------
        # Build DataFrame
        # --------------------------------------------------------------

        df = pd.DataFrame(
            {
                key: data[key][:n]
                for key in keys
            }
        )

        # --------------------------------------------------------------
        # Convert timestamp
        # --------------------------------------------------------------

        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            unit="s",
            utc=True,
        ).dt.tz_convert(
            "Asia/Kolkata"
        )

        df = (
            df
            .set_index("timestamp")
            .sort_index()
        )

        # --------------------------------------------------------------
        # Remove duplicate timestamps
        # --------------------------------------------------------------

        df = df[
            ~df.index.duplicated(
                keep="last"
            )
        ]

        # --------------------------------------------------------------
        # Resample
        # --------------------------------------------------------------

        if interval == 5:

            df = resample_ohlcv(
                df,
                "5min",
            )

        elif interval == 15:

            df = resample_ohlcv(
                df,
                "15min",
            )

        return df


# ----------------------------------------------------------------------
# OHLCV Resampling
# ----------------------------------------------------------------------

def resample_ohlcv(
    df: pd.DataFrame,
    rule: str,
) -> pd.DataFrame:

    if df.empty:
        return df

    out = (
        df.resample(
            rule,
            origin="start_day",
            offset="15min",
            label="right",
            closed="right",
        )
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna(
            subset=[
                "open",
                "high",
                "low",
                "close",
            ]
        )
    )

    return out
