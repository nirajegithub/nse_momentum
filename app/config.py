from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    min_price: float = float(os.getenv("MIN_PRICE", "350"))
    min_prev_volume: int = int(os.getenv("MIN_DAILY_VOLUME", os.getenv("MIN_PREV_VOLUME", "500000")))
    buy_rsi_min: float = float(os.getenv("BUY_RSI_MIN", "55"))
    buy_rsi_max: float = float(os.getenv("BUY_RSI_MAX", "70"))
    sell_rsi_min: float = float(os.getenv("SELL_RSI_MIN", "30"))
    sell_rsi_max: float = float(os.getenv("SELL_RSI_MAX", "45"))
    min_5m_rvol: float = float(os.getenv("MIN_5M_RVOL", "1.5"))
    require_ema_crossover: bool = os.getenv("REQUIRE_EMA_CROSSOVER", "false").lower() == "true"
    pending_setup_max_5m_candles: int = int(os.getenv("PENDING_SETUP_MAX_5M_CANDLES", "1"))
    entry_timeframe: int = int(os.getenv("ENTRY_TIMEFRAME", "1"))
    setup_timeframe: int = int(os.getenv("SETUP_TIMEFRAME", "5"))
    atr_buffer: float = float(os.getenv("ATR_BUFFER", "0.25"))
    min_stop_atr: float = float(os.getenv("MIN_STOP_ATR", "0.5"))
    max_stop_atr: float = float(os.getenv("MAX_STOP_ATR", "2.5"))
    max_entry_chase_atr: float = float(
        os.getenv("MAX_ENTRY_CHASE_ATR", "0.25")
    )
    min_entry_rvol: float = float(os.getenv("MIN_ENTRY_RVOL", "1.0"))
    rvol_lookback: int = int(os.getenv("RVOL_LOOKBACK", "20"))
    min_1m_candles: int = int(os.getenv("MIN_1M_CANDLES", "30"))
    min_5m_candles: int = int(os.getenv("MIN_5M_CANDLES", "30"))
    min_15m_candles: int = int(os.getenv("MIN_15M_CANDLES", "30"))
    scan_start_hhmm: int = int(os.getenv("SCAN_START_HHMM", "930"))
    scan_end_hhmm: int = int(os.getenv("SCAN_END_HHMM", "1505"))
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"
    volume_gainer_refresh_minutes: int = int(
        os.getenv("VOLUME_GAINER_REFRESH_MINUTES", "10")
    )
    
    # Telegram alert-noise controls.
    # These affect Telegram notifications only; signal generation/scoring is unchanged.
    alert_min_score: int = int(os.getenv("ALERT_MIN_SCORE", "80"))
    alert_min_rvol: float = float(os.getenv("ALERT_MIN_RVOL", "0.5"))
    alert_max_rvol: float = float(os.getenv("ALERT_MAX_RVOL", "20.0"))
    alert_require_structure: bool = os.getenv(
        "ALERT_REQUIRE_STRUCTURE", "true"
    ).lower() == "true"
    alert_cooldown_minutes: int = int(os.getenv("ALERT_COOLDOWN_MINUTES", "30"))
    alert_score_improvement: int = int(os.getenv("ALERT_SCORE_IMPROVEMENT", "10"))
    alert_reversal_min_score: int = int(os.getenv("ALERT_REVERSAL_MIN_SCORE", "85"))


DISCLAIMER = (
    "⚠️ Disclaimer: Above calls are not Buy or Sell levels. "
    "These calls are for educational purposes only, based on research. "
    "Consult your financial advisor before investing."
)


SETTINGS = Settings()
