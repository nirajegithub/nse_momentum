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

    @staticmethod
    def _records(payload):
        if isinstance(payload, pd.DataFrame):
            return payload.to_dict("records")

        if isinstance(payload, list):
            return payload

        if isinstance(payload, dict):
            for key in ("data", "Data", "result"):
                if isinstance(payload.get(key), list):
                    return payload[key]

        return []

    def security_master(self) -> pd.DataFrame:
        """
        Download Dhan's official detailed security master.

        We use the detailed master because it explicitly provides:
          EXCH_ID
          SEGMENT
          INSTRUMENT
          SYMBOL_NAME
          SECURITY_ID

        For NSE cash equities:
          EXCH_ID    = NSE
          SEGMENT    = E
          INSTRUMENT = EQUITY
        """

        usecols = [
            "EXCH_ID",
            "SEGMENT",
            "INSTRUMENT",
            "SYMBOL_NAME",
            "SECURITY_ID",
        ]

        try:
            df = pd.read_csv(
                DHAN_DETAILED_MASTER_URL,
                usecols=usecols,
                dtype=str,
                low_memory=False,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Unable to download Dhan detailed security master: {exc}"
            ) from exc

        # Normalize values.
        for col in usecols:
            df[col] = df[col].fillna("").astype(str).str.strip()

        # Keep only NSE cash-equity instruments.
        df = df[
            (df["EXCH_ID"].str.upper() == "NSE")
            & (df["SEGMENT"].str.upper() == "E")
            & (df["INSTRUMENT"].str.upper() == "EQUITY")
        ].copy()

        # Normalize symbol and security ID.
        df["SYMBOL_NAME"] = df["SYMBOL_NAME"].str.upper().str.strip()
        df["SECURITY_ID"] = df["SECURITY_ID"].str.strip()

        # Remove invalid rows.
        df = df[
            (df["SYMBOL_NAME"] != "")
            & (df["SECURITY_ID"] != "")
        ].copy()

        LOG.info(
            "Dhan detailed master: %d NSE equity instruments loaded",
            len(df),
        )

        return df

    def build_symbol_map(self, symbols: list[str]) -> dict[str, dict]:
        """
        Build:
            NSE symbol -> Dhan Security ID

        Example:
            POLYCAB -> {
                "security_id": "...",
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

        matched = df[df["SYMBOL_NAME"].isin(wanted)].copy()

        # If duplicate SYMBOL_NAME rows somehow exist,
        # keep the first valid Security ID.
        matched = matched.drop_duplicates(
            subset=["SYMBOL_NAME"],
            keep="first",
        )

        out: dict[str, dict] = {}

        for _, row in matched.iterrows():
            symbol = str(row["SYMBOL_NAME"]).strip().upper()
            security_id = str(row["SECURITY_ID"]).strip()

            if not symbol or not security_id:
                continue

            out[symbol] = {
                "security_id": security_id,
                "exchange_segment": "NSE_EQ",
                "instrument": "EQUITY",
            }

        missing = sorted(wanted - set(out.keys()))

        LOG.info(
            "Dhan Security ID mapping: %d/%d symbols matched",
            len(out),
            len(wanted),
        )

        if missing:
            for symbol in missing:
                LOG.warning(
                    "Missing Dhan security_id: %s",
                    symbol,
                )

        return out

    def intraday_df(
        self,
        security_id: str,
        interval: int,
        from_date: str,
        to_date: str,
    ) -> pd.DataFrame:
        """
        Fetch Dhan intraday minute data and resample locally
        to 5-minute or 15-minute candles.

        Dhan historical API uses:
            exchangeSegment = NSE_EQ
            instrument     = EQUITY
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

        if not all(k in payload for k in keys):
            LOG.warning(
                "Incomplete intraday response for security_id=%s",
                security_id,
            )
            return pd.DataFrame()

        try:
            n = min(len(payload[k]) for k in keys)

            if n == 0:
                return pd.DataFrame()

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

            # Make sure numeric fields are numeric.
            for col in [
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]:
                df[col] = pd.to_numeric(
                    df[col],
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
                df = resample_ohlcv(df, "5min")

            elif interval == 15:
                df = resample_ohlcv(df, "15min")

            return df

        except Exception as exc:
            LOG.warning(
                "Failed to process intraday data for security_id=%s: %s",
                security_id,
                exc,
            )
            return pd.DataFrame()


def resample_ohlcv(
    df: pd.DataFrame,
    rule: str,
) -> pd.DataFrame:
    """
    Convert minute OHLCV data into completed NSE candles.

    NSE regular session starts at 09:15 IST.

    Examples:
        5M:
          09:15-09:20 -> candle timestamp 09:20
          09:20-09:25 -> candle timestamp 09:25

        15M:
          09:15-09:30 -> candle timestamp 09:30
          09:30-09:45 -> candle timestamp 09:45
    """

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

    # Keep only market-session candles.
    out = out[
        (out.index.time >= pd.Timestamp("09:20").time())
        & (out.index.time <= pd.Timestamp("15:30").time())
    ]

    return out
