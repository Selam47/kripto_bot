import os
from dotenv import load_dotenv

load_dotenv()


def _bool(key: str, default: int = 0) -> bool:
    return int(os.getenv(key, str(default))) == 1


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _list_str(key: str, default: str = "") -> list[str]:
    raw = os.getenv(key, default).strip()
    if not raw:
        return []
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _list_float(key: str, default: str = "") -> list[float]:
    raw = os.getenv(key, default).strip()
    if not raw:
        return []
    try:
        return [float(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError:
        return []


BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")
BINANCE_ENV: str = os.getenv("BINANCE_ENV", "prod")
BINANCE_WS_URL: str = os.getenv("BINANCE_WS_URL", "wss://fstream.binance.com")

TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

TELEGRAM_SEND_MESSAGE_URL: str = (
    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    if TELEGRAM_BOT_TOKEN else ""
)
TELEGRAM_SEND_PHOTO_URL: str = (
    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    if TELEGRAM_BOT_TOKEN else ""
)
TELEGRAM_DELETE_WEBHOOK_URL: str = (
    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook"
    if TELEGRAM_BOT_TOKEN else ""
)

SYMBOLS: list[str] = ["BTCUSDT", "PIPPINUSDT"]

TIMEFRAMES: list[str] = (
    [tf.strip() for tf in os.getenv("TIMEFRAMES", "15m").split(",") if tf.strip()]
    or ["15m"]
)

MAX_SYMBOLS: int = _int("MAX_SYMBOLS", 0)
SYMBOL_SELECTION_STRATEGY: str = os.getenv("SYMBOL_SELECTION_STRATEGY", "quality").lower()
MIN_DAILY_VOLUME_USDT: float = _float("MIN_DAILY_VOLUME_USDT", 500_000.0)
MIN_MARKET_CAP_USD: float = _float("MIN_MARKET_CAP_USD", 0.0)
FILTER_BY_MARKET_CAP: bool = _bool("FILTER_BY_MARKET_CAP", 0)

MAX_LEVERAGE: int = _int("MAX_LEVERAGE", 20)

DEFAULT_SL_PERCENT: float = _float("DEFAULT_SL_PERCENT", 0.02)
DEFAULT_TP_PERCENTS: list[float] = _list_float("DEFAULT_TP_PERCENTS", "0.03,0.06,0.10")

LEVERAGE_BASED_TP_SL_ENABLED: bool = _bool("LEVERAGE_BASED_TP_SL_ENABLED", 0)
LEVERAGE_BASE_RISK_PERCENT: float = _float("LEVERAGE_BASE_RISK_PERCENT", 2.0)
LEVERAGE_BASE_TP_PERCENT: float = _float("LEVERAGE_BASE_TP_PERCENT", 1.0)
LEVERAGE_MIN_SL_DISTANCE: float = _float("LEVERAGE_MIN_SL_DISTANCE", 0.1)
LEVERAGE_MAX_SL_DISTANCE: float = _float("LEVERAGE_MAX_SL_DISTANCE", 5.0)
LEVERAGE_MIN_TP_DISTANCE: float = _float("LEVERAGE_MIN_TP_DISTANCE", 0.2)
LEVERAGE_MAX_TP_DISTANCE: float = _float("LEVERAGE_MAX_TP_DISTANCE", 3.0)

HISTORY_CANDLES: int = _int("HISTORY_CANDLES", 200)
SIGNAL_COOLDOWN: int = _int("SIGNAL_COOLDOWN", 3600)
MIN_CONFLUENCE_SCORE: int = _int("MIN_CONFLUENCE_SCORE", 4)

SIMULATION_MODE: bool = _bool("SIMULATION_MODE", 0)
DATA_TESTING: bool = _bool("DATA_TESTING", 0)
DISABLE_CHARTING: bool = _bool("DISABLE_CHARTING", 0)

LAZY_LOADING_ENABLED: bool = _bool("LAZY_LOADING_ENABLED", 1)
MAX_LAZY_LOAD_SYMBOLS: int = _int("MAX_LAZY_LOAD_SYMBOLS", 20)
MAX_CONCURRENT_LOADS: int = _int("MAX_CONCURRENT_LOADS", 5)

DB_PATH: str = os.getenv("DB_PATH", "trading_bot.db")
DB_POOL_SIZE: int = _int("DB_POOL_SIZE", 10)
DB_ENABLE_PERSISTENCE: bool = _bool("DB_ENABLE_PERSISTENCE", 1)
DB_CLEANUP_DAYS: int = _int("DB_CLEANUP_DAYS", 7)
DB_MAX_SIZE_MB: float = _float("DB_MAX_SIZE_MB", 200.0)
DB_MAX_RECORDS: int = _int("DB_MAX_RECORDS", 1_000_000)
DB_AUTO_CLEANUP_ENABLED: bool = _bool("DB_AUTO_CLEANUP_ENABLED", 1)
DB_CLEANUP_INTERVAL_HOURS: int = _int("DB_CLEANUP_INTERVAL_HOURS", 6)

RATE_LIMITING_ENABLED: bool = _bool("RATE_LIMITING_ENABLED", 1)
RATE_LIMIT_SAFETY_MARGIN: float = _float("RATE_LIMIT_SAFETY_MARGIN", 0.1)
RATE_LIMIT_WARNING_THRESHOLD: float = _float("RATE_LIMIT_WARNING_THRESHOLD", 0.8)
RATE_LIMIT_MAX_WEIGHT_PER_MINUTE: int = _int("RATE_LIMIT_MAX_WEIGHT_PER_MINUTE", 1200)
RATE_LIMIT_MAX_REQUESTS_PER_MINUTE: int = _int("RATE_LIMIT_MAX_REQUESTS_PER_MINUTE", 1200)
RATE_LIMIT_RETRY_DELAY: float = _float("RATE_LIMIT_RETRY_DELAY", 1.0)
RATE_LIMIT_MAX_RETRIES: int = _int("RATE_LIMIT_MAX_RETRIES", 3)
RATE_LIMIT_DETAILED_LOGGING: bool = _bool("RATE_LIMIT_DETAILED_LOGGING", 0)
RATE_LIMIT_LOG_INTERVAL: int = _int("RATE_LIMIT_LOG_INTERVAL", 60)
