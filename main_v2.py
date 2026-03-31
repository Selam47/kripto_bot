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
from config import FILTER_BY_MARKET_CAP, BINANCE_API_KEY, BINANCE_API_SECRET
from risk_manager import RiskManager
from symbol_manager import SymbolManager
from database import get_database
from database_maintenance import get_maintenance_service

from engine.market_data import MarketData
from engine.trading_engine import TradingEngine
from engine.notification_manager import NotificationManager

CHARTING_ENABLED = os.getenv("DISABLE_CHARTING", "0") != "1"

if CHARTING_ENABLED:
    try:
        from charting_service import ChartingService
    except Exception:
        CHARTING_ENABLED = False
        ChartingService = None
else:
    ChartingService = None


class AppRunner:

    def __init__(self):
        if config.DB_ENABLE_PERSISTENCE:
            self.db = get_database()
            logging.info("Database initialized")
            self.db_maintenance = get_maintenance_service()
        else:
            self.db = None
            self.db_maintenance = None
            logging.info("Database persistence disabled")

        self.binance_client = BinanceFuturesClient(BINANCE_API_KEY, BINANCE_API_SECRET)
        self.stop_event = threading.Event()
        self.is_shutting_down = threading.Event()
        self.ws = None
        self.symbol_manager = SymbolManager(self.binance_client)
        self.risk_manager = RiskManager(self.binance_client)
        self.notifier = NotificationManager()
        self.engine = None

        if CHARTING_ENABLED and ChartingService:
            try:
                self.charting_service = ChartingService()
                logging.info("Charting service initialized")
            except Exception as e:
                logging.warning(f"Charting init failed: {e}")
                self.charting_service = None
        else:
            self.charting_service = None
            logging.info("Charting disabled")

        self.rate_limit_thread = None
        if config.RATE_LIMITING_ENABLED:
            self.rate_limit_thread = threading.Thread(
                name="RateLimitMonitor",
                target=self._monitor_rate_limits,
                daemon=True,
            )

    def shutdown_handler(self, signum, frame):
        if self.is_shutting_down.is_set():
            return
        logging.info("Shutting down...")
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

    def _validate_config(self) -> bool:
        errors = []
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            errors.append("Binance API credentials missing")
        if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
            errors.append("Telegram configuration missing")
        if not config.TIMEFRAMES:
            errors.append("No timeframes configured")
        if config.HISTORY_CANDLES <= 0:
            errors.append("HISTORY_CANDLES must be positive")
        elif config.HISTORY_CANDLES > 1500:
            errors.append("HISTORY_CANDLES cannot exceed 1500")
        if config.SIGNAL_COOLDOWN < 0:
            errors.append("SIGNAL_COOLDOWN must be non-negative")
        if config.DEFAULT_SL_PERCENT <= 0 or config.DEFAULT_SL_PERCENT >= 1:
            errors.append("DEFAULT_SL_PERCENT must be between 0 and 1")
        for tp in config.DEFAULT_TP_PERCENTS:
            if tp <= 0 or tp >= 1:
                errors.append(f"TP percent {tp} must be between 0 and 1")

        if errors:
            for e in errors:
                logging.error(f"Config error: {e}")
            return False

        logging.info("Configuration validated")
        return True

    def run(self):
        mode = "SIMULATION" if config.SIMULATION_MODE else "LIVE TRADING"
        logging.info(f"Starting in {mode} mode")

        if not self._validate_config():
            logging.error("Config validation failed. Exiting.")
            return

        self.symbol_manager.start()
        self.notifier.start()

        if self.charting_service:
            self.charting_service.start()
        if self.db_maintenance:
            self.db_maintenance.start()
        if self.rate_limit_thread:
            self.rate_limit_thread.start()
            logging.info("Rate limit monitor started")

        market_data = MarketData(self.binance_client, self.symbol_manager)
        if market_data.has_historical_loader:
            market_data.initialize_historical()

        self.engine = TradingEngine(
            market_data=market_data,
            notification_mgr=self.notifier,
            risk_manager=self.risk_manager,
            charting_service=self.charting_service,
        )

        symbols_to_subscribe = self.symbol_manager.get_symbols()

        if FILTER_BY_MARKET_CAP:
            min_cap = 10_000_000_000
            if symbols_to_subscribe and min_cap > 0:
                symbols_to_subscribe = self.risk_manager.filter_symbols_by_market_cap(
                    symbols_to_subscribe, min_cap
                )
            if not symbols_to_subscribe:
                logging.error("No symbols after filtering. Exiting.")
                self.shutdown_handler(None, None)
                return

        self.ws = BinanceWS(
            symbol_to_subs=symbols_to_subscribe,
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

    def _monitor_rate_limits(self):
        while not self.stop_event.is_set():
            try:
                if self.binance_client.rate_limiter:
                    stats = self.binance_client.get_rate_limit_stats()
                    if stats:
                        w = stats["weight_usage_percent"]
                        r = stats["request_usage_percent"]
                        if w > 80 or r > 80:
                            logging.warning(f"High API usage: Weight {w:.1f}%, Requests {r:.1f}%")
                self.stop_event.wait(30)
            except Exception as e:
                logging.error(f"Rate limit monitor error: {e}")
                self.stop_event.wait(30)


def setup_logging():
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(process)d:%(threadName)s] %(name)s:%(filename)s:%(lineno)d - %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                "logs/trading_bot.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
            ),
            logging.StreamHandler(sys.stdout),
        ],
    )


if __name__ == "__main__":
    setup_logging()

    if config.DATA_TESTING:
        logging.info("DATA_TESTING mode")
        from util import create_realistic_test_data

        binance_client = BinanceFuturesClient(BINANCE_API_KEY, BINANCE_API_SECRET)
        notifier = NotificationManager()
        notifier.start()

        symbol_manager = SymbolManager(binance_client)
        market_data = MarketData(binance_client, symbol_manager)
        risk_manager = RiskManager(binance_client)

        charting_service = None
        if CHARTING_ENABLED and ChartingService:
            try:
                charting_service = ChartingService()
                charting_service.start()
            except Exception as e:
                logging.warning(f"Charting not available in test: {e}")

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
                    df = create_realistic_test_data(periods=50, base_price=30000)
                    key = (symbol, interval)
                    market_data.klines[key] = df
                    market_data.historical_loaded[key] = True
                    engine._run_signal_pipeline(symbol, interval)
                    time.sleep(1)
                except Exception as e:
                    logging.error(f"Test error {symbol} {interval}: {e}")

        engine.shutdown()
        notifier.stop()
        if charting_service:
            charting_service.stop()
        logging.info("DATA_TESTING completed")
    else:
        app = AppRunner()
        app.run()
