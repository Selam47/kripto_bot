"""
main.py — Single Entry Point
==============================
Start the bot:
    python main.py

Execution modes (set in .env):
    SIMULATION_MODE=1   — signals sent, no real orders
    DATA_TESTING=1      — synthetic data, no WebSocket
"""

import logging
import logging.handlers
import os
import signal
import sys
import threading
import time

import requests

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


def _run_telegram_command_server(stop_event: threading.Event) -> None:
    """
    Run the ONE AND ONLY ``telegram.ext.Application`` in a dedicated thread.

    This is the single source of truth for Telegram bot polling.  No other
    module creates an Application — doing so would cause ``Conflict: 409``
    errors from two simultaneous ``getUpdates`` calls.

    Boot sequence
    -------------
    1. Create a new ``asyncio`` event loop (isolated from all other threads).
    2. Build the Application with the bot token.
    3. Register /start /durum /coinler /hakkinda command handlers.
    4. ``initialize()`` the application.
    5. ``bot.delete_webhook(drop_pending_updates=True)`` — clears any ghost
       webhook/session left over from a previous run.
    6. ``start()`` + ``updater.start_polling(drop_pending_updates=True)``.
    7. ``loop.run_forever()`` until ``stop_event`` fires.
    8. Async teardown: ``updater.stop()`` → ``app.stop()`` → ``app.shutdown()``.
    """
    import asyncio as _asyncio

    _logger = logging.getLogger("TelegramCmdServer")

    if not config.TELEGRAM_BOT_TOKEN:
        _logger.warning("TELEGRAM_BOT_TOKEN not set — command server disabled")
        return

    try:
        from telegram.ext import Application, CommandHandler
        from engine.notification_manager import (
            _cmd_start, _cmd_durum, _cmd_coinler, _cmd_hakkinda,
        )
    except ImportError as exc:
        _logger.error(f"Telegram import failed: {exc}")
        return

    loop = _asyncio.new_event_loop()
    _asyncio.set_event_loop(loop)

    app: Application | None = None

    async def _boot():
        nonlocal app
        app = (
            Application.builder()
            .token(config.TELEGRAM_BOT_TOKEN)
            .build()
        )
        app.add_handler(CommandHandler("start", _cmd_start))
        app.add_handler(CommandHandler("durum", _cmd_durum))
        app.add_handler(CommandHandler("coinler", _cmd_coinler))
        app.add_handler(CommandHandler("hakkinda", _cmd_hakkinda))

        await app.initialize()

        # ── CONFLICT FIX ───────────────────────────────────────────────────
        # delete_webhook clears any previous webhook or ghost getUpdates
        # session.  drop_pending_updates=True discards stale messages so the
        # first poll starts from a clean state.
        try:
            await app.bot.delete_webhook(drop_pending_updates=True)
            _logger.info("delete_webhook OK (drop_pending_updates=True)")
            await _asyncio.sleep(0.5)   # brief pause before first getUpdates
        except Exception as wh_err:
            _logger.warning(f"delete_webhook failed (non-fatal): {wh_err}")

        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        _logger.info("Telegram command polling started (/start /durum /coinler /hakkinda)")

    async def _shutdown():
        if app is None:
            return
        try:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()
            _logger.info("Telegram command server shut down cleanly")
        except Exception as exc:
            _logger.debug(f"Telegram shutdown error (non-fatal): {exc}")

    try:
        loop.run_until_complete(_boot())
        # Poll until stop_event is set, checking every second
        while not stop_event.is_set():
            loop.run_until_complete(_asyncio.sleep(1))
    except Exception as exc:
        _logger.error(f"Telegram command server crashed: {exc}", exc_info=True)
    finally:
        try:
            loop.run_until_complete(_shutdown())
        except Exception:
            pass
        # Cancel any remaining tasks so the loop closes without RuntimeError
        pending = _asyncio.all_tasks(loop)
        if pending:
            for t in pending:
                t.cancel()
            loop.run_until_complete(_asyncio.gather(*pending, return_exceptions=True))
        loop.close()
        _logger.info("Telegram event loop closed")


def delete_webhook() -> None:
    """
    Call the Telegram deleteWebhook endpoint before starting long-polling.

    This clears any previously registered webhook so that getUpdates
    (long-polling) can receive messages without conflicts.  Runs synchronously
    at boot time and is safe to call even if no webhook exists.
    """
    url = config.TELEGRAM_DELETE_WEBHOOK_URL
    if not url:
        logger.warning("TELEGRAM_BOT_TOKEN not set — skipping deleteWebhook")
        return
    try:
        resp = requests.post(url, timeout=10)
        data = resp.json()
        if data.get("ok"):
            logger.info("Telegram webhook cleared (deleteWebhook OK)")
        else:
            logger.warning(f"deleteWebhook returned: {data.get('description', data)}")
    except Exception as exc:
        logger.warning(f"deleteWebhook request failed (non-fatal): {exc}")


def _validate_config() -> bool:
    errors: list[str] = []

    if not config.BINANCE_API_KEY or not config.BINANCE_API_SECRET:
        errors.append("Binance API credentials missing (BINANCE_API_KEY / BINANCE_API_SECRET)")
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        errors.append("Telegram credentials missing (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
    if not config.TIMEFRAMES:
        errors.append("No timeframes configured (TIMEFRAMES)")
    if not config.SYMBOLS:
        errors.append("No symbols configured (SYMBOLS)")
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


class AppRunner:
    """
    Top-level lifecycle controller.

    Boot order:
        deleteWebhook → validate config → Database → SymbolManager →
        NotificationManager → MarketData (bulk-load) → TradingEngine →
        BinanceWS → main sleep loop
    """

    def __init__(self) -> None:
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

        # Single Telegram command server — created here, started in run()
        self._cmd_server_stop = threading.Event()
        self._cmd_server_thread: threading.Thread | None = None

    def shutdown_handler(self, signum, frame) -> None:
        if self.is_shutting_down.is_set():
            return
        logger.info("Shutdown signal received — stopping all subsystems...")
        self.is_shutting_down.set()

        # Stop Telegram command server first (signals its loop to exit)
        self._cmd_server_stop.set()

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

        # Wait for the command server thread to finish (max 8 s)
        if self._cmd_server_thread and self._cmd_server_thread.is_alive():
            self._cmd_server_thread.join(timeout=8)

        self.stop_event.set()
        logger.info("Shutdown complete")

    def run(self) -> None:
        # ── Antigravity Engine: Simulation Mode enforced ───────────────────────
        # Per spec: TRADING_MODE = 0 (Simulation). Override env var at runtime
        # so all signals carry the [SIMULATION] prefix regardless of .env.
        import config as _cfg
        _cfg.SIMULATION_MODE = True

        # The two symbols this engine specifically optimises for.
        # Sources from config (SYMBOLS env var) but always includes these two.
        monitored_symbols = list(
            dict.fromkeys(["BTCUSDT", "PIPPINUSDT"] + list(config.SYMBOLS))
        )
        primary_interval = config.TIMEFRAMES[0] if config.TIMEFRAMES else "15m"

        mode = "SIMULATION" if config.SIMULATION_MODE else "LIVE TRADING"
        logger.info(f"Starting Kripto Botu in {mode} mode")
        logger.info(f"Target symbols : {monitored_symbols}")
        logger.info(f"Primary interval : {primary_interval}")
        logger.info(f"Timeframes     : {config.TIMEFRAMES}")
        logger.info(
            f"Signal cooldown: {config.SIGNAL_COOLDOWN}s "
            f"({config.SIGNAL_COOLDOWN / 3600:.1f}h)"
        )

        if not _validate_config():
            logger.error("Startup aborted due to configuration errors")
            return

        delete_webhook()

        self.symbol_manager.start()
        self.notifier.start()

        # ── Telegram Command Server ─────────────────────────────────────────
        # One Application, one polling loop, one thread.  This is the ONLY
        # place a bot instance is created — guaranteeing no Conflict: 409.
        self._cmd_server_thread = threading.Thread(
            target=_run_telegram_command_server,
            args=(self._cmd_server_stop,),
            name="TelegramCmdServer",
            daemon=True,
        )
        self._cmd_server_thread.start()
        logger.info("Telegram command server thread started")

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

        # ── Startup Pulse ──────────────────────────────────────────────────────
        # Fire a "System Online — Current Market Status" message immediately.
        # Runs in a daemon thread so it never delays WebSocket initialisation.
        logger.info("Dispatching startup market-status pulse...")
        self.engine.run_initial_analysis(monitored_symbols, primary_interval)

        # ── Instant Alert Monitor ────────────────────────────────────
        # 60-second background thread: fires the pipeline immediately on
        # ATR spike, RSI 30/70 crossover, or MACD line/signal crossover.
        self.engine.start_instant_alert_monitor(
            monitored_symbols, primary_interval, poll_seconds=60
        )

        # ── 15-Minute Periodic Analysis Loop ──────────────────────────────────
        # Scheduled full scan every 15 minutes (at least 1 analysis per hour).
        # Belt-and-suspenders to the Instant Alert monitor. Dedup guard and
        # cooldown inside TradingEngine prevent duplicate Telegram messages.
        PERIODIC_INTERVAL_SECONDS = 900  # 15 minutes
        try:
            while not self.stop_event.is_set():
                self.stop_event.wait(timeout=PERIODIC_INTERVAL_SECONDS)
                if self.stop_event.is_set():
                    break
                logger.debug("Periodic tick — running scheduled analysis")
                self.engine.run_periodic_check(monitored_symbols, primary_interval)
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            logger.error(f"Main loop unexpected error: {exc}", exc_info=True)
        finally:
            if not self.is_shutting_down.is_set():
                self.shutdown_handler(None, None)

    def _monitor_rate_limits(self) -> None:
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

    test_symbols = config.SYMBOLS if config.SYMBOLS else ["PIPPINUSDT"]
    fake_candle_time = int(time.time() * 1000)

    for symbol in test_symbols:
        for interval in config.TIMEFRAMES:
            try:
                df = create_realistic_test_data(periods=60, base_price=0.00042)
                key = (symbol, interval)
                market_data.klines[key] = df
                market_data.historical_loaded[key] = True
                engine._run_pipeline(symbol, interval, fake_candle_time)
                time.sleep(0.5)
            except Exception as exc:
                logger.error(
                    f"Test pipeline error {symbol}/{interval}: {exc}", exc_info=True
                )

    engine.shutdown()
    notifier.stop()
    if charting_service:
        charting_service.stop()

    logger.info("DATA_TESTING completed")


if __name__ == "__main__":
    setup_logging()

    try:
        if config.DATA_TESTING:
            _run_data_testing()
        else:
            AppRunner().run()
    except Exception as exc:
        logging.critical(f"Fatal startup error: {exc}", exc_info=True)
        sys.exit(1)
