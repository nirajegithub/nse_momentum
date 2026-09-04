from __future__ import annotations

import logging
import os
from datetime import date

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

    @staticmethod
    def _find_col(df: pd.DataFrame, names):
        normalized = {
            str(c).lower().replace(" ", "_"): c
            for c in df.columns
        }

        for name in names:
            if name.lower() in normalized:
                return normalized[name.lower()]

        return None

    def security_master(self) -> pd.DataFrame:
        df = pd.read_csv(
            DHAN_DETAILED_MASTER_URL,
            low_memory=False
        )

        LOG.info(
            "Dhan detailed master: %d rows loaded",
            len(df)
        )

        return df

    def build_symbol_map(
        self,
        symbols: list[str]
    ) -> dict[str, dict]:

        df = self.security_master()

        required = [
            "EXCH_ID",
            "SEGMENT",
            "SECURITY_ID",
            "INSTRUMENT",
            "UNDERLYING_SYMBOL",
        ]

        missing = [c for c in required if c not in df.columns]

        if missing:
            raise RuntimeError(
                f"Missing Dhan master columns: {missing}"
            )

        df = df[
            (df["EXCH_ID"].astype(str).str.upper() == "NSE")
            & (df["SEGMENT"].astype(str).str.upper() == "E")
            & (df["INSTRUMENT"].astype(str).str.upper() == "EQUITY")
        ].copy()

        wanted = {s.upper() for s in symbols}

        df["UNDERLYING_SYMBOL"] = (
            df["UNDERLYING_SYMBOL"]
            .astype(str)
            .str.strip()
            .str.upper()
        )

        matched = df[
            df["UNDERLYING_SYMBOL"].isin(wanted)
        ]

        out = {}

        for _, row in matched.iterrows():
            symbol = row["UNDERLYING_SYMBOL"]

            out[symbol] = {
                "security_id": str(row["SECURITY_ID"]),
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY",
            }

        LOG.info(
            "Dhan Security ID mapping: %d/%d symbols matched",
            len(out),
            len(wanted),
        )

        return out

    def historical_daily_df(
        self,
        security_id: str,
        from_date: str,
        to_date: str,
    ) -> pd.DataFrame:

        payload = self.dhan.historical_daily_data(
            security_id=str(security_id),
            exchange_segment="NSE_EQ",
            instrument_type="EQUITY",
            from_date=from_date,
            to_date=to_date,
        )

        if not isinstance(payload, dict):
            return pd.DataFrame()

        required = [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]

        if not all(k in payload for k in required):
            return pd.DataFrame()

        n = min(len(payload[k]) for k in required)

        df = pd.DataFrame(
            {
                k: payload[k][:n]
                for k in required
            }
        )

        if df.empty:
            return df

        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            unit="s",
            utc=True,
        ).dt.tz_convert("Asia/Kolkata")

        df = (
            df.set_index("timestamp")
            .sort_index()
        )

        return df

    def intraday_df(
        self,
        security_id: str,
        interval: int,
        from_date: str,
        to_date: str,
    ) -> pd.DataFrame:

        payload = self.dhan.intraday_minute_data(
            security_id=str(security_id),
            exchange_segment="NSE_EQ",
            instrument_type="EQUITY",
            from_date=from_date,
            to_date=to_date,
            interval=1,
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

        if not all(k in payload for k in keys):
            return pd.DataFrame()

        n = min(len(payload[k]) for k in keys)

        df = pd.DataFrame(
            {
                k: payload[k][:n]
                for k in keys
            }
        )

        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            unit="s",
            utc=True,
        ).dt.tz_convert("Asia/Kolkata")

        df = (
            df.set_index("timestamp")
            .sort_index()
        )

        if interval == 5:
            df = resample_ohlcv(df, "5min")
        elif interval == 15:
            df = resample_ohlcv(df, "15min")

        return df


def resample_ohlcv(
    df: pd.DataFrame,
    rule: str
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
