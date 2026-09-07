from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    min_price: float = float(os.getenv("MIN_PRICE", "250"))
    min_prev_volume: int = int(os.getenv("MIN_PREV_VOLUME", "500000"))
    atr_buffer: float = float(os.getenv("ATR_BUFFER", "0.25"))
    max_stop_atr: float = float(os.getenv("MAX_STOP_ATR", "2.5"))
    rvol_lookback: int = int(os.getenv("RVOL_LOOKBACK", "20"))
    min_5m_candles: int = int(os.getenv("MIN_5M_CANDLES", "30"))
    min_15m_candles: int = int(os.getenv("MIN_15M_CANDLES", "30"))
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() == "true"

    # Telegram alert-noise controls.
    # These affect Telegram notifications only; signal generation/scoring is unchanged.
    alert_min_score: int = int(os.getenv("ALERT_MIN_SCORE", "80"))
    alert_min_rvol: float = float(os.getenv("ALERT_MIN_RVOL", "1.0"))
    alert_cooldown_minutes: int = int(os.getenv("ALERT_COOLDOWN_MINUTES", "30"))
    alert_score_improvement: int = int(os.getenv("ALERT_SCORE_IMPROVEMENT", "10"))
    alert_reversal_min_score: int = int(os.getenv("ALERT_REVERSAL_MIN_SCORE", "85"))


DISCLAIMER = (
    "⚠️ Disclaimer: Above calls are not Buy or Sell levels. "
    "These calls are for educational purposes only, based on research. "
    "Consult your financial advisor before investing."
)


SETTINGS = Settings()
