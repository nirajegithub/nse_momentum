from __future__ import annotations

import logging
import os

import pandas as pd
from dhanhq import DhanContext, dhanhq

LOG = logging.getLogger(__name__)

DHAN_DETAILED_MASTER_URL = (
    "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
)


class DhanClient:
    def __init__(self) -> None:
        client_id = os.environ["DHAN_CLIENT_ID"]
        token = os.environ["DHAN_ACCESS_TOKEN"]

        self.context = DhanContext(client_id, token)
        self.dhan = dhanhq(self.context)

    def security_master(self) -> pd.DataFrame:
        """
        Download Dhan's detailed security master.

        NSE cash equity:
            EXCH_ID    = NSE
            SEGMENT    = E
            INSTRUMENT = EQUITY

        Scanner symbol:
            UNDERLYING_SYMBOL

        Dhan identifier:
            SECURITY_ID
        """

        columns = [
            "EXCH_ID",
            "SEGMENT",
            "SECURITY_ID",
            "INSTRUMENT",
            "UNDERLYING_SYMBOL",
            "SYMBOL_NAME",
        ]

        try:
            df = pd.read_csv(
                DHAN_DETAILED_MASTER_URL,
                usecols=columns,
                dtype=str,
                low_memory=False,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Unable to download Dhan detailed security master: {exc}"
            ) from exc

        for column in columns:
            df[column] = (
                df[column]
                .fillna("")
                .astype(str)
                .str.strip()
            )

        df = df[
            (df["EXCH_ID"].str.upper() == "NSE")
            & (df["SEGMENT"].str.upper() == "E")
            & (df["INSTRUMENT"].str.upper() == "EQUITY")
        ].copy()

        df["UNDERLYING_SYMBOL"] = (
            df["UNDERLYING_SYMBOL"]
            .str.upper()
            .str.strip()
        )

        df["SECURITY_ID"] = (
            df["SECURITY_ID"]
            .str.strip()
        )

        df = df[
            (df["UNDERLYING_SYMBOL"] != "")
            & (df["SECURITY_ID"] != "")
        ].copy()

        LOG.info(
            "Dhan detailed master: %d NSE equity instruments loaded",
            len(df),
        )

        return df

    def build_symbol_map(
        self,
        symbols: list[str],
    ) -> dict[str, dict]:
        """
        Build:

            NSE SYMBOL -> Dhan SECURITY_ID

        Example:

            POLYCAB -> {
                "security_id": "9598",
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY"
            }
        """

        df = self.security_master()

        wanted = {
            str(symbol).strip().upper()
            for symbol in symbols
            if str(symbol).strip()
        }

        if not wanted:
            return {}

        matched = df[
            df["UNDERLYING_SYMBOL"].isin(wanted)
        ].copy()

        matched = matched.drop_duplicates(
            subset=["UNDERLYING_SYMBOL"],
            keep="first",
        )

        result: dict[str, dict] = {}

        for _, row in matched.iterrows():
            symbol = (
                str(row["UNDERLYING_SYMBOL"])
                .strip()
                .upper()
            )

            security_id = (
                str(row["SECURITY_ID"])
                .strip()
            )

            if not symbol or not security_id:
                continue

            result[symbol] = {
                "security_id": security_id,
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY",
            }

        missing = sorted(
            wanted - set(result.keys())
        )

        LOG.info(
            "Dhan Security ID mapping: %d/%d symbols matched",
            len(result),
            len(wanted),
        )

        for symbol in missing:
            LOG.warning(
                "Missing Dhan security_id: %s",
                symbol,
            )

        return result

    def intraday_df(
        self,
        security_id: str,
        interval: int,
        from_date: str,
        to_date: str,
    ) -> pd.DataFrame:
        """
        Fetch Dhan intraday minute data.

        Resample locally to:
            5-minute
            15-minute
        """

        payload = self.dhan.intraday_minute_data(
            security_id=str(security_id),
            exchange_segment="NSE_EQ",
            instrument_type="EQUITY",
            from_date=from_date,
            to_date=to_date,
        )

        if not isinstance(payload, dict):
            return pd.DataFrame()

        keys = [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

        if not all(key in payload for key in keys):
            LOG.warning(
                "Incomplete intraday response for security_id=%s",
                security_id,
            )
            return pd.DataFrame()

        try:
            n = min(
                len(payload[key])
                for key in keys
            )

            if n == 0:
                return pd.DataFrame()

            df = pd.DataFrame(
                {
                    key: payload[key][:n]
                    for key in keys
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

            for column in [
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]:
                df[column] = pd.to_numeric(
                    df[column],
                    errors="coerce",
                )

            df = df.dropna(
                subset=[
                    "open",
                    "high",
                    "low",
                    "close",
                ]
            )

            if interval == 5:
                return resample_ohlcv(
                    df,
                    "5min",
                )

            if interval == 15:
                return resample_ohlcv(
                    df,
                    "15min",
                )

            return df

        except Exception as exc:
            LOG.warning(
                "Failed to process intraday data "
                "for security_id=%s: %s",
                security_id,
                exc,
            )
            return pd.DataFrame()


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
