from __future__ import annotations
import logging
import os
from datetime import date
import pandas as pd
from dhanhq import DhanContext, dhanhq

LOG = logging.getLogger(__name__)

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
        result = self.dhan.fetch_security_list("compact")
        return result if isinstance(result, pd.DataFrame) else pd.DataFrame(result)

    @staticmethod
    def _find_col(df: pd.DataFrame, names):
        normalized = {str(c).lower().replace(" ", "_"): c for c in df.columns}
        for name in names:
            if name.lower() in normalized:
                return normalized[name.lower()]
        return None

    def build_symbol_map(self, symbols: list[str]) -> dict[str, dict]:
        df = self.security_master()
        ex = self._find_col(df, ["SEM_EXM_EXCH_ID", "exchange_segment", "exchange"])
        inst = self._find_col(df, ["SEM_INSTRUMENT_NAME", "instrument_type", "instrument"])
        sym = self._find_col(df, ["SEM_TRADING_SYMBOL", "trading_symbol", "tradingsymbol"])
        sid = self._find_col(df, ["SEM_SMST_SECURITY_ID", "security_id", "securityid"])
        if not all([ex, inst, sym, sid]):
            raise RuntimeError(f"Unsupported Dhan security-master columns: {list(df.columns)}")
        wanted = {s.upper() for s in symbols}
        out = {}
        for _, row in df.iterrows():
            if str(row[ex]).upper() != "NSE_EQ":
                continue
            if str(row[inst]).upper() != "EQUITY":
                continue
            symbol = str(row[sym]).strip().upper()
            if symbol in wanted:
                out[symbol] = {
                    "security_id": str(row[sid]),
                    "exchange_segment": "NSE_EQ",
                    "instrument": "EQUITY",
                }
        return out

    def intraday_df(self, security_id: str, interval: int, from_date: str, to_date: str) -> pd.DataFrame:
        payload = self.dhan.intraday_minute_data(
            security_id=str(security_id),
            exchange_segment="NSE_EQ",
            instrument_type="EQUITY",
            from_date=from_date,
            to_date=to_date,
        )
        # Dhan returns parallel arrays for intraday data.
        if not isinstance(payload, dict):
            return pd.DataFrame()
        keys = ["timestamp", "open", "high", "low", "close", "volume"]
        if not all(k in payload for k in keys):
            return pd.DataFrame()
        n = min(len(payload[k]) for k in keys)
        df = pd.DataFrame({k: payload[k][:n] for k in keys})
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        df = df.set_index("timestamp").sort_index()
        if interval == 5:
            df = resample_ohlcv(df, "5min")
        elif interval == 15:
            df = resample_ohlcv(df, "15min")
        return df

def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.resample(rule, origin="start_day", offset="15min", label="right", closed="right").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna(subset=["open", "high", "low", "close"])
    return out
