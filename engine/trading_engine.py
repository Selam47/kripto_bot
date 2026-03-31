import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import config
from engine.market_data import MarketData
from engine.confluence import ConfluenceEngine, SignalResult, TradeLevels
from engine.mtf_guard import MTFTrendGuard
from engine.notification_manager import NotificationManager
from engine.fibonacci import AutoFibonacci
from risk_manager import RiskManager
from database import get_database
from util import timeframe_to_seconds, now_utc
from structs import ChartData, ChartCallbackData, SignalNotificationData

logger = logging.getLogger(__name__)

SENT_SIGNALS_MAX_AGE = 3600
SENT_SIGNALS_CLEANUP_INTERVAL = 600


class TradingEngine:

    def __init__(
        self,
        market_data: MarketData,
        notification_mgr: NotificationManager,
        risk_manager: Optional[RiskManager] = None,
        charting_service=None,
    ):
        self.market_data = market_data
        self.notifier = notification_mgr
        self.risk_manager = risk_manager
        self.charting_service = charting_service
        self.db = get_database() if config.DB_ENABLE_PERSISTENCE else None

        self._signal_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="SignalWorker")
        self._lock = threading.Lock()
        self._cooldowns: dict[tuple[str, str], float] = {}
        self._sent_signals: dict[str, float] = {}
        self._last_cleanup = time.time()

    def handle_kline(self, k: dict):
        if not self._validate_kline(k):
            return
        symbol = k["s"]
        interval = k["i"]
        self.market_data.update_kline(k)
        self._signal_pool.submit(self._process, symbol, interval)

    def _validate_kline(self, k) -> bool:
        if not isinstance(k, dict):
            return False
        for field in ("s", "i", "o", "h", "l", "c", "v", "t"):
            if field not in k:
                return False
        try:
            float(k["o"])
            float(k["h"])
            float(k["l"])
            float(k["c"])
            float(k["v"])
            int(k["t"])
        except (ValueError, TypeError):
            return False
        return True

    def _process(self, symbol: str, interval: str):
        try:
            self._run_signal_pipeline(symbol, interval)
        except Exception as e:
            logger.error(f"Signal pipeline error {symbol}-{interval}: {e}", exc_info=True)

    def _run_signal_pipeline(self, symbol: str, interval: str):
        min_candles = 20 if config.SIMULATION_MODE or config.DATA_TESTING else 50

        df = self.market_data.get_klines(symbol, interval)

        if len(df) < min_candles and self.market_data.has_historical_loader:
            loaded = self.market_data.lazy_load(symbol, interval)
            if loaded:
                df = self.market_data.get_klines(symbol, interval)

        if len(df) < min_candles:
            return

        key = (symbol, interval)
        now = time.time()

        if config.DATA_TESTING:
            cooldown = 0
        elif config.SIMULATION_MODE:
            cooldown = config.SIGNAL_COOLDOWN
        else:
            cooldown = timeframe_to_seconds(interval)

        if self._is_on_cooldown(key, now, cooldown):
            return

        mtf_allowed, htf_trend = MTFTrendGuard.validate(
            signal_direction="",
            current_interval=interval,
            get_kline_fn=self.market_data.get_klines,
            symbol=symbol,
        )

        signal = ConfluenceEngine.analyze(df)
        if not signal:
            return

        mtf_allowed, htf_trend = MTFTrendGuard.validate(
            signal_direction=signal.direction,
            current_interval=interval,
            get_kline_fn=self.market_data.get_klines,
            symbol=symbol,
        )
        if not mtf_allowed:
            return

        try:
            last_price = float(df["close"].iloc[-1])
        except (IndexError, ValueError, TypeError):
            return

        signal_key = f"{symbol}-{interval}-{signal.direction}-{last_price:.2f}"
        with self._lock:
            last_sent = self._sent_signals.get(signal_key, 0)
            if now - last_sent < cooldown:
                return
            self._sent_signals[signal_key] = now

        self._cleanup_cache(now)

        max_leverage = (
            self.risk_manager.get_max_leverage_for_symbol(symbol)
            if self.risk_manager
            else 20
        )
        margin_type = "ISOLATED"

        entry_prices, tp_list, sl, position_bias = self._compute_levels(
            signal, last_price, df, symbol
        )
        if not entry_prices:
            return

        logger.info(
            f"Signal: {signal.direction} {symbol}-{interval} | "
            f"Confidence: {signal.confidence:.0%} | "
            f"Confluences: {', '.join(signal.confluences)} | "
            f"Trend: {signal.trend_strength} | HTF: {htf_trend}"
        )

        if self.db:
            self.db.store_signal({
                "symbol": symbol,
                "interval": interval,
                "signal_type": signal.direction,
                "price": entry_prices[0],
                "entry_prices": entry_prices,
                "tp_levels": tp_list,
                "sl_level": sl,
                "leverage": max_leverage,
                "margin_type": margin_type,
                "timestamp": now_utc(),
            })

        if self.charting_service:
            self._send_with_chart(
                symbol, interval, entry_prices, tp_list, sl,
                max_leverage, margin_type, signal, htf_trend, key, now,
            )
        else:
            self.notifier.send_signal(
                symbol, interval, entry_prices, tp_list, sl,
                max_leverage, margin_type, signal, None, htf_trend,
            )
            with self._lock:
                self._cooldowns[key] = now

    def _compute_levels(self, signal: SignalResult, last_price: float, df, symbol: str):
        try:
            levels = ConfluenceEngine.calculate_atr_levels(signal, df)
            if levels:
                return levels.entry_prices, levels.tp_list, levels.sl, levels.position_bias
        except Exception as e:
            logger.error(f"ATR levels error: {e}")

        if symbol and self.risk_manager and config.LEVERAGE_BASED_TP_SL_ENABLED:
            try:
                tp_list, sl, _ = self.risk_manager.calculate_leverage_based_tp_sl(
                    symbol, last_price, signal.direction
                )
                return [last_price], tp_list, sl, "NORMAL"
            except Exception as e:
                logger.error(f"Leverage TP/SL error: {e}")

        sl_pct = config.DEFAULT_SL_PERCENT
        tp_pcts = config.DEFAULT_TP_PERCENTS

        if signal.direction == "BUY":
            return [last_price], [last_price * (1 + p) for p in tp_pcts], last_price * (1 - sl_pct), "NORMAL"
        elif signal.direction == "SELL":
            return [last_price], [last_price * (1 - p) for p in tp_pcts], last_price * (1 + sl_pct), "NORMAL"

        return None, None, None, None

    def _is_on_cooldown(self, key: tuple, now: float, cooldown: float) -> bool:
        with self._lock:
            if self.db:
                last_db = self.db.get_last_signal_time(key[0], key[1])
                last_ts = last_db.timestamp() if last_db else 0
            else:
                last_ts = self._cooldowns.get(key, 0)

            if now - last_ts < cooldown:
                return True

            self._cooldowns[key] = now
            return False

    def _cleanup_cache(self, now: float):
        if now - self._last_cleanup < SENT_SIGNALS_CLEANUP_INTERVAL:
            return
        with self._lock:
            expired = [k for k, t in self._sent_signals.items() if now - t > SENT_SIGNALS_MAX_AGE]
            for k in expired:
                del self._sent_signals[k]
            self._last_cleanup = now

    def _send_with_chart(
        self, symbol, interval, entry_prices, tp_list, sl,
        leverage, margin_type, signal, htf_trend, key, now,
    ):
        try:
            min_candles = 20 if config.SIMULATION_MODE or config.DATA_TESTING else 50
            clean_df = self.market_data.get_clean_klines(symbol, interval)
            if len(clean_df) < min_candles:
                self.notifier.send_signal(
                    symbol, interval, entry_prices, tp_list, sl,
                    leverage, margin_type, signal, None, htf_trend,
                )
                with self._lock:
                    self._cooldowns[key] = now
                return

            def chart_callback(path, error):
                chart_path = None
                if not error and path:
                    import os
                    if os.path.exists(path):
                        size = os.path.getsize(path)
                        if 1024 <= size <= 50 * 1024 * 1024:
                            chart_path = path

                self.notifier.send_signal(
                    symbol, interval, entry_prices, tp_list, sl,
                    leverage, margin_type, signal, chart_path, htf_trend,
                )
                with self._lock:
                    self._cooldowns[key] = now

            chart_data = ChartData(
                ohlc_df=clean_df,
                symbol=symbol,
                timeframe=interval,
                tp_levels=tp_list,
                sl_level=sl,
                callback=chart_callback,
            )
            self.charting_service.submit_plot_chart_task(chart_data)

        except Exception as e:
            logger.error(f"Chart error {symbol}-{interval}: {e}")
            self.notifier.send_signal(
                symbol, interval, entry_prices, tp_list, sl,
                leverage, margin_type, signal, None, htf_trend,
            )
            with self._lock:
                self._cooldowns[key] = now

    def shutdown(self):
        logger.info("TradingEngine shutting down")
        try:
            self._signal_pool.shutdown(wait=True, cancel_futures=False)
        except TypeError:
            self._signal_pool.shutdown(wait=True)
        except Exception as e:
            logger.error(f"Shutdown error: {e}")
            self._signal_pool.shutdown(wait=False)
