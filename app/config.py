from dataclasses import dataclass
import os


def _bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() == "true"


@dataclass(frozen=True)
class Settings:
    min_price: float = float(os.getenv("MIN_PRICE", "350"))
    min_prev_volume: int = int(os.getenv("MIN_PREV_VOLUME", "500000"))
    min_daily_volume: int = int(os.getenv("MIN_DAILY_VOLUME", "500000"))

    buy_rsi_min: float = float(os.getenv("BUY_RSI_MIN", "55"))
    buy_rsi_max: float = float(os.getenv("BUY_RSI_MAX", "80"))
    sell_rsi_min: float = float(os.getenv("SELL_RSI_MIN", "30"))
    sell_rsi_max: float = float(os.getenv("SELL_RSI_MAX", "45"))
    rvol_lookback: int = int(os.getenv("RVOL_LOOKBACK", "20"))
    min_15m_rvol: float = float(os.getenv("MIN_15M_RVOL", "1.2"))
    require_ema_crossover: bool = _bool("REQUIRE_EMA_CROSSOVER", "false")

    setup_timeframe: int = int(os.getenv("SETUP_TIMEFRAME", "15"))
    entry_timeframe: int = int(os.getenv("ENTRY_TIMEFRAME", "5"))
    min_15m_candles: int = int(os.getenv("MIN_15M_CANDLES", "30"))
    min_5m_candles: int = int(os.getenv("MIN_5M_CANDLES", "30"))

    min_trade_score: float = float(os.getenv("MIN_TRADE_SCORE", "3"))
    max_trade_score: float = float(os.getenv("MAX_TRADE_SCORE", "7"))

    t1_rr: float = float(os.getenv("T1_RR", "2.0"))
    t2_rr: float = float(os.getenv("T2_RR", "3.0"))
    t3_rr: float = float(os.getenv("T3_RR", "4.0"))
    min_rr: float = float(os.getenv("MIN_RR", "2.0"))
    min_stop_distance_percent: float = float(os.getenv("MIN_STOP_DISTANCE_PERCENT", "0.50"))
    risk_percent: float = float(os.getenv("RISK_PERCENT", "1.0"))
    pending_setup_max_15m_candles: int = int(os.getenv("PENDING_SETUP_MAX_15M_CANDLES", "1"))

    scan_start_hhmm: int = int(os.getenv("SCAN_START_HHMM", "930"))
    scan_end_hhmm: int = int(os.getenv("SCAN_END_HHMM", "1505"))
    dry_run: bool = _bool("DRY_RUN", "true")


DISCLAIMER = (
    "⚠️ Educational/informational purposes only. Not financial advice. Trade at your own risk."
)

SETTINGS = Settings()
