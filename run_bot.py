"""
run_bot.py — Single Entry Point
=================================
Boots the entire trading system and blocks until a SIGINT/SIGTERM arrives.

Execution modes
---------------
- **Live trading** (default): ``SIMULATION_MODE=0`` in ``.env``
- **Simulation**:             ``SIMULATION_MODE=1``
- **Data testing**:           ``DATA_TESTING=1`` — injects synthetic OHLCV
  data directly into the engine without opening a WebSocket.

Start the bot
-------------
    python run_bot.py

Dependencies
------------
See ``requirements.txt``.  Install with::

    pip install -r requirements.txt
"""

import logging
import logging.handlers
import os
import signal
import sys
import threading
import time

import config
from binance_future_client import BinanceFuturesClient
from binance_ws_client import BinanceWS
from database import get_database
from database_maintenance import get_maintenance_service
from engine.market_data import MarketData
from engine.notification_manager import NotificationManager
from engine.trading_engine import TradingEngine
from risk_manager import RiskManager
from symbol_manager import SymbolManager

_CHARTING_ENABLED: bool = not config.DISABLE_CHARTING

if _CHARTING_ENABLED:
    try:
        from charting_service import ChartingService
    except Exception:
        _CHARTING_ENABLED = False
        ChartingService = None
else:
    ChartingService = None

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """
    Configure rotating-file + stdout logging.

    Creates a ``logs/`` directory if it does not exist and attaches a
    10 MB rotating file handler (5 backups) alongside a ``StreamHandler``
    that writes to stdout.
    """
    os.makedirs("logs", exist_ok=True)
    fmt = (
        "%(asctime)s [%(levelname)s] [%(process)d:%(threadName)s] "
        "%(name)s:%(filename)s:%(lineno)d - %(message)s"
    )
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            "logs/trading_bot.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


class AppRunner:
    """
    Top-level application controller.

    Owns the lifecycle of every subsystem:
    ``Database → SymbolManager → NotificationManager → MarketData →
    TradingEngine → BinanceWS``.

    All subsystems are started inside ``run()`` and torn down inside
    ``shutdown_handler()``, which is wired to ``SIGINT`` and ``SIGTERM``.
    """

    def __init__(self) -> None:
        """
        Instantiate shared services.

        Does **not** start any background threads — that happens in ``run()``.
        """
        if config.DB_ENABLE_PERSISTENCE:
            self.db = get_database()
            logger.info("Database initialized")
            self.db_maintenance = get_maintenance_service()
        else:
            self.db = None
            self.db_maintenance = None
            logger.info("Database persistence disabled")

        self.binance_client = BinanceFuturesClient(
            config.BINANCE_API_KEY, config.BINANCE_API_SECRET
        )
        self.stop_event = threading.Event()
        self.is_shutting_down = threading.Event()
        self.ws = None
        self.symbol_manager = SymbolManager(self.binance_client)
        self.risk_manager = RiskManager(self.binance_client)
        self.notifier = NotificationManager()
        self.engine = None

        if _CHARTING_ENABLED and ChartingService:
            try:
                self.charting_service = ChartingService()
                logger.info("Charting service initialised")
            except Exception as exc:
                logger.warning(f"Charting init failed: {exc}")
                self.charting_service = None
        else:
            self.charting_service = None
            logger.info("Charting disabled")

        self._rate_limit_thread: threading.Thread | None = None
        if config.RATE_LIMITING_ENABLED:
            self._rate_limit_thread = threading.Thread(
                target=self._monitor_rate_limits,
                name="RateLimitMonitor",
                daemon=True,
            )

    def shutdown_handler(self, signum, frame) -> None:
        """
        Gracefully stop all subsystems and signal the main loop to exit.

        Safe to call multiple times — guarded by ``is_shutting_down`` event.
        Registered for ``SIGINT`` and ``SIGTERM``.

        Args:
            signum: Signal number (unused beyond logging context).
            frame:  Current stack frame (unused).
        """
        if self.is_shutting_down.is_set():
            return
        logger.info("Shutdown signal received — stopping all subsystems...")
        self.is_shutting_down.set()

        if self.ws:
            self.ws.stop()
        if self.symbol_manager:
            self.symbol_manager.stop()
        if self.engine:
            self.engine.shutdown()
        if self.notifier:
            self.notifier.stop()
        if self.charting_service:
            self.charting_service.stop()
        if self.db_maintenance:
            self.db_maintenance.stop()
        if self.db:
            self.db.close()

        self.stop_event.set()
        logger.info("Shutdown complete")

    def _validate_config(self) -> bool:
        """
        Validate critical configuration values before the system starts.

        Returns:
            ``True`` when all checks pass; ``False`` when fatal errors are found.
        """
        errors: list[str] = []

        if not config.BINANCE_API_KEY or not config.BINANCE_API_SECRET:
            errors.append("Binance API credentials missing (BINANCE_API_KEY / BINANCE_API_SECRET)")
        if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
            errors.append("Telegram configuration missing (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        if not config.TIMEFRAMES:
            errors.append("No timeframes configured (TIMEFRAMES)")
        if config.HISTORY_CANDLES <= 0:
            errors.append("HISTORY_CANDLES must be a positive integer")
        elif config.HISTORY_CANDLES > 1500:
            errors.append("HISTORY_CANDLES cannot exceed 1500 (Binance API limit)")
        if config.SIGNAL_COOLDOWN < 0:
            errors.append("SIGNAL_COOLDOWN must be non-negative")
        if not (0 < config.DEFAULT_SL_PERCENT < 1):
            errors.append("DEFAULT_SL_PERCENT must be strictly between 0 and 1")
        for tp in config.DEFAULT_TP_PERCENTS:
            if not (0 < tp < 1):
                errors.append(f"TP percent {tp} must be strictly between 0 and 1")

        if errors:
            for msg in errors:
                logger.error(f"Config error: {msg}")
            return False

        logger.info("Configuration validated successfully")
        return True

    def run(self) -> None:
        """
        Main execution loop.

        Orchestration order
        -------------------
        1. Validate configuration.
        2. Start background services (SymbolManager, Notifier, DB maintenance).
        3. Initialise ``MarketData`` singleton and bulk-load historical bars.
        4. Build ``TradingEngine``.
        5. Apply optional market-cap filter to symbol list.
        6. Open the Binance WebSocket stream.
        7. Block until shutdown is requested.
        """
        mode = "SIMULATION" if config.SIMULATION_MODE else "LIVE TRADING"
        logger.info(f"Starting Kripto Botu in {mode} mode")

        if not self._validate_config():
            logger.error("Startup aborted due to configuration errors")
            return

        self.symbol_manager.start()
        self.notifier.start()

        if self.charting_service:
            self.charting_service.start()
        if self.db_maintenance:
            self.db_maintenance.start()
        if self._rate_limit_thread:
            self._rate_limit_thread.start()
            logger.info("Rate-limit monitor started")

        market_data = MarketData(self.binance_client, self.symbol_manager)
        if market_data.has_historical_loader:
            market_data.initialize_historical()

        self.engine = TradingEngine(
            market_data=market_data,
            notification_mgr=self.notifier,
            risk_manager=self.risk_manager,
            charting_service=self.charting_service,
        )

        symbols = self.symbol_manager.get_symbols()

        if config.FILTER_BY_MARKET_CAP and symbols:
            min_cap = int(os.getenv("MIN_MARKET_CAP", "10000000000"))
            symbols = self.risk_manager.filter_symbols_by_market_cap(symbols, min_cap)
            if not symbols:
                logger.error("No symbols remaining after market-cap filter — aborting")
                self.shutdown_handler(None, None)
                return
            logger.info(f"Market-cap filter retained {len(symbols)} symbols")

        self.ws = BinanceWS(
            symbol_to_subs=symbols,
            on_message_callback=self.engine.handle_kline,
        )

        signal.signal(signal.SIGINT, self.shutdown_handler)
        signal.signal(signal.SIGTERM, self.shutdown_handler)

        self.ws.run()

        try:
            while not self.stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            if not self.is_shutting_down.is_set():
                self.shutdown_handler(None, None)

    def _monitor_rate_limits(self) -> None:
        """
        Background daemon that warns when Binance API weight usage is high.

        Polls every 30 seconds and logs a ``WARNING`` when weight or request
        usage exceeds 80 %.
        """
        while not self.stop_event.is_set():
            try:
                if getattr(self.binance_client, "rate_limiter", None):
                    stats = self.binance_client.get_rate_limit_stats()
                    if stats:
                        w = stats.get("weight_usage_percent", 0)
                        r = stats.get("request_usage_percent", 0)
                        if w > 80 or r > 80:
                            logger.warning(
                                f"High API usage — Weight: {w:.1f}%  Requests: {r:.1f}%"
                            )
            except Exception as exc:
                logger.error(f"Rate-limit monitor error: {exc}")
            self.stop_event.wait(30)


def _run_data_testing() -> None:
    """
    Execute a synthetic data smoke-test without opening a WebSocket.

    Injects ``create_realistic_test_data()`` DataFrames directly into
    ``MarketData`` and drives ``TradingEngine._run_pipeline()`` for every
    configured symbol/interval combination.

    Used for verifying the signal pipeline locally without live credentials.
    """
    from util import create_realistic_test_data

    logger.info("DATA_TESTING mode — synthetic data injection")

    binance_client = BinanceFuturesClient(
        config.BINANCE_API_KEY, config.BINANCE_API_SECRET
    )
    symbol_manager = SymbolManager(binance_client)
    risk_manager = RiskManager(binance_client)
    notifier = NotificationManager()
    notifier.start()

    market_data = MarketData(binance_client, symbol_manager)

    charting_service = None
    if _CHARTING_ENABLED and ChartingService:
        try:
            charting_service = ChartingService()
            charting_service.start()
        except Exception as exc:
            logger.warning(f"Charting not available in test mode: {exc}")

    engine = TradingEngine(
        market_data=market_data,
        notification_mgr=notifier,
        risk_manager=risk_manager,
        charting_service=charting_service,
    )

    test_symbols = config.SYMBOLS if config.SYMBOLS else ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

    for symbol in test_symbols:
        for interval in config.TIMEFRAMES:
            try:
                df = create_realistic_test_data(periods=60, base_price=30000)
                key = (symbol, interval)
                market_data.klines[key] = df
                market_data.historical_loaded[key] = True
                engine._run_pipeline(symbol, interval)
                time.sleep(0.5)
            except Exception as exc:
                logger.error(f"Test pipeline error {symbol}/{interval}: {exc}", exc_info=True)

    engine.shutdown()
    notifier.stop()
    if charting_service:
        charting_service.stop()

    logger.info("DATA_TESTING completed")


if __name__ == "__main__":
    setup_logging()

    if config.DATA_TESTING:
        _run_data_testing()
    else:
        AppRunner().run()
