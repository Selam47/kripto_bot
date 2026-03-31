"""
TradingEngine — Signal Orchestrator
======================================
The central signal-processing pipeline.  Every live kline update flows through
this class:

    WebSocket kline → validate → update MarketData
                                     ↓
                          lazy-load history (if needed)
                                     ↓
                          cooldown / duplicate guard
                                     ↓
                        MTF Trend Guard validation
                                     ↓
                       ConfluenceEngine.analyze(df)
                                     ↓
                    ConfluenceEngine.calculate_atr_levels()
                                     ↓
              DB persistence → chart request → NotificationManager

Design
------
- A 2-worker ``ThreadPoolExecutor`` (``SignalWorker-0/1``) handles per-symbol
  signal processing in parallel without blocking the WebSocket receive thread.
- All shared state (cooldowns, sent-signal cache) is protected by a single
  ``threading.Lock``.
- Signal deduplication: the same direction at the same price within one
  cooldown window is suppressed.
- Sent-signal cache is pruned every ``SENT_SIGNALS_CLEANUP_INTERVAL`` seconds
  to prevent unbounded memory growth.
"""

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import config
from database import get_database
from engine.confluence import ConfluenceEngine, SignalResult
from engine.market_data import MarketData
from engine.mtf_guard import MTFTrendGuard
from engine.notification_manager import NotificationManager
from structs import ChartData
from util import now_utc, timeframe_to_seconds

logger = logging.getLogger(__name__)

_SENT_MAX_AGE: int = 3600
_SENT_CLEANUP_INTERVAL: int = 600


class TradingEngine:
    """
    Signal orchestrator that connects live market data to the analysis engine
    and the notification pipeline.

    Attributes
    ----------
    market_data : MarketData
        Singleton OHLCV store.
    notifier : NotificationManager
        Singleton async Telegram gateway.
    risk_manager : RiskManager | None
        Optional risk manager for leverage-based TP/SL fallback.
    charting_service : ChartingService | None
        Optional async chart generator.
    """

    def __init__(
        self,
        market_data: MarketData,
        notification_mgr: NotificationManager,
        risk_manager=None,
        charting_service=None,
    ):
        """
        Initialise the trading engine.

        Args:
            market_data:       Singleton ``MarketData`` instance.
            notification_mgr:  Singleton ``NotificationManager`` instance.
            risk_manager:      Optional ``RiskManager`` for leverage-based
                               TP/SL calculations.
            charting_service:  Optional ``ChartingService`` for PNG chart
                               generation.
        """
        self.market_data = market_data
        self.notifier = notification_mgr
        self.risk_manager = risk_manager
        self.charting_service = charting_service
        self.db = get_database() if config.DB_ENABLE_PERSISTENCE else None

        self._pool = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="SignalWorker"
        )
        self._lock = threading.Lock()
        self._cooldowns: dict[tuple[str, str], float] = {}
        self._sent_signals: dict[str, float] = {}
        self._last_cleanup: float = time.monotonic()

    def handle_kline(self, k: dict):
        """
        Entry-point for a raw kline message from the Binance WebSocket.

        Validates the kline dict structure, updates the in-memory price store,
        and submits signal processing to the thread pool — all without blocking
        the WebSocket receive thread.

        Args:
            k: Raw kline dict with keys ``s``, ``i``, ``t``, ``o``, ``h``,
               ``l``, ``c``, ``v``.
        """
        if not self._validate_kline(k):
            return
        self.market_data.update_kline(k)
        self._pool.submit(self._process, k["s"], k["i"])

    @staticmethod
    def _validate_kline(k) -> bool:
        """
        Validate that a kline dict contains all required numeric fields.

        Args:
            k: Object to validate.

        Returns:
            ``True`` when the kline is well-formed.
        """
        if not isinstance(k, dict):
            return False
        for field in ("s", "i", "o", "h", "l", "c", "v", "t"):
            if field not in k:
                return False
        try:
            float(k["o"]); float(k["h"]); float(k["l"])
            float(k["c"]); float(k["v"]); int(k["t"])
        except (ValueError, TypeError):
            return False
        return True

    def _process(self, symbol: str, interval: str):
        """
        Worker-thread entry-point for signal processing.

        Wraps ``_run_pipeline`` in a top-level exception handler so a crash in
        one symbol never affects others.

        Args:
            symbol:   Trading pair.
            interval: Timeframe string.
        """
        try:
            self._run_pipeline(symbol, interval)
        except Exception as e:
            logger.error(
                f"Unhandled error in signal pipeline {symbol}-{interval}: {e}",
                exc_info=True,
            )

    def _run_pipeline(self, symbol: str, interval: str):
        """
        Full signal pipeline for one symbol/interval pair.

        Steps
        -----
        1. Ensure sufficient historical data (lazy-load if needed).
        2. Check cooldown — skip if a signal was recently emitted.
        3. Run MTF Trend Guard — block counter-trend trades.
        4. Run ConfluenceEngine — generate signal or exit.
        5. Deduplicate against sent-signal cache.
        6. Compute ATR-based TP/SL levels.
        7. Persist signal to database.
        8. Dispatch to NotificationManager (with or without chart).

        Args:
            symbol:   Trading pair.
            interval: Timeframe string.
        """
        min_candles = 20 if (config.SIMULATION_MODE or config.DATA_TESTING) else 50

        df = self.market_data.get_klines(symbol, interval)

        if len(df) < min_candles and self.market_data.has_historical_loader:
            if self.market_data.lazy_load(symbol, interval):
                df = self.market_data.get_klines(symbol, interval)

        if len(df) < min_candles:
            return

        key = (symbol, interval)
        now = time.monotonic()
        wall_now = time.time()

        if config.DATA_TESTING:
            cooldown = 0.0
        elif config.SIMULATION_MODE:
            cooldown = float(config.SIGNAL_COOLDOWN)
        else:
            cooldown = float(timeframe_to_seconds(interval))

        if self._is_on_cooldown(key, wall_now, cooldown):
            return

        signal: Optional[SignalResult] = ConfluenceEngine.analyze(df)
        if not signal:
            return

        allowed, htf_trend = MTFTrendGuard.validate(
            signal_direction=signal.direction,
            current_interval=interval,
            get_kline_fn=self.market_data.get_klines,
            symbol=symbol,
        )
        if not allowed:
            return

        try:
            last_price = float(df["close"].iloc[-1])
        except (IndexError, ValueError, TypeError):
            logger.warning(f"Could not read last price for {symbol}-{interval}")
            return

        signal_key = f"{symbol}-{interval}-{signal.direction}-{last_price:.2f}"
        with self._lock:
            if wall_now - self._sent_signals.get(signal_key, 0.0) < cooldown:
                return
            self._sent_signals[signal_key] = wall_now

        self._prune_sent_cache(now)

        max_leverage = (
            self.risk_manager.get_max_leverage_for_symbol(symbol)
            if self.risk_manager
            else int(config.MAX_LEVERAGE)
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
            self._dispatch_with_chart(
                symbol, interval, entry_prices, tp_list, sl,
                max_leverage, margin_type, signal, htf_trend, key, wall_now,
            )
        else:
            self.notifier.send_signal(
                symbol, interval, entry_prices, tp_list, sl,
                max_leverage, margin_type, signal, None, htf_trend,
            )
            with self._lock:
                self._cooldowns[key] = wall_now

    def _compute_levels(
        self,
        signal: SignalResult,
        last_price: float,
        df,
        symbol: str,
    ) -> tuple:
        """
        Compute TP/SL levels with a three-tier fallback strategy.

        Priority
        --------
        1. **ATR-based dynamic levels** (primary) — from ``ConfluenceEngine``.
        2. **Leverage-based levels** — from ``RiskManager`` when enabled.
        3. **Fixed percentage fallback** — from ``config.DEFAULT_*`` values.

        Args:
            signal:      ``SignalResult`` containing ATR and entry price.
            last_price:  Latest close price.
            df:          OHLCV DataFrame.
            symbol:      Trading pair (for leverage lookup).

        Returns:
            Tuple of ``(entry_prices, tp_list, sl, position_bias)`` or
            ``(None, None, None, None)`` on failure.
        """
        try:
            levels = ConfluenceEngine.calculate_atr_levels(signal, df)
            if levels:
                logger.debug(
                    f"ATR levels: entry={levels.entry_prices[0]:.4f} "
                    f"TP1={levels.tp_list[0]:.4f} SL={levels.sl:.4f} "
                    f"R:R={levels.risk_reward_ratio}"
                )
                return levels.entry_prices, levels.tp_list, levels.sl, levels.position_bias
        except Exception as e:
            logger.error(f"ATR level calculation error: {e}")

        if symbol and self.risk_manager and config.LEVERAGE_BASED_TP_SL_ENABLED:
            try:
                tp_list, sl, _ = self.risk_manager.calculate_leverage_based_tp_sl(
                    symbol, last_price, signal.direction
                )
                return [last_price], tp_list, sl, "NORMAL"
            except Exception as e:
                logger.error(f"Leverage TP/SL error for {symbol}: {e}")

        sl_pct = config.DEFAULT_SL_PERCENT
        tp_pcts = config.DEFAULT_TP_PERCENTS

        if signal.direction == "BUY":
            return (
                [last_price],
                [last_price * (1 + p) for p in tp_pcts],
                last_price * (1 - sl_pct),
                "NORMAL",
            )
        if signal.direction == "SELL":
            return (
                [last_price],
                [last_price * (1 - p) for p in tp_pcts],
                last_price * (1 + sl_pct),
                "NORMAL",
            )
        return None, None, None, None

    def _is_on_cooldown(
        self, key: tuple[str, str], wall_now: float, cooldown: float
    ) -> bool:
        """
        Check whether the symbol/interval is within its signal cooldown window.

        Checks the database first (for persistence across restarts), then falls
        back to the in-memory cooldown dict.  Updates the cooldown timestamp on
        first pass.

        Args:
            key:       ``(symbol, interval)`` tuple.
            wall_now:  Current wall-clock time (``time.time()``).
            cooldown:  Cooldown duration in seconds.

        Returns:
            ``True`` if still within cooldown and signal should be skipped.
        """
        with self._lock:
            if self.db:
                last_db = self.db.get_last_signal_time(key[0], key[1])
                last_ts = last_db.timestamp() if last_db else 0.0
            else:
                last_ts = self._cooldowns.get(key, 0.0)

            if wall_now - last_ts < cooldown:
                return True

            self._cooldowns[key] = wall_now
            return False

    def _prune_sent_cache(self, mono_now: float):
        """
        Remove stale entries from the sent-signal deduplication cache.

        Called after each new signal to keep memory usage bounded.  Uses
        monotonic time for the interval check but wall-clock time for the entry
        timestamps.

        Args:
            mono_now: Current monotonic time (``time.monotonic()``).
        """
        if mono_now - self._last_cleanup < _SENT_CLEANUP_INTERVAL:
            return
        wall_now = time.time()
        with self._lock:
            expired = [
                k for k, t in self._sent_signals.items()
                if wall_now - t > _SENT_MAX_AGE
            ]
            for k in expired:
                del self._sent_signals[k]
            if expired:
                logger.debug(f"Pruned {len(expired)} stale sent-signal entries")
        self._last_cleanup = mono_now

    def _dispatch_with_chart(
        self,
        symbol: str,
        interval: str,
        entry_prices: list,
        tp_list: list,
        sl: float,
        leverage: int,
        margin_type: str,
        signal: SignalResult,
        htf_trend: str,
        key: tuple,
        wall_now: float,
    ):
        """
        Submit a chart generation task and wire up the callback to send the
        resulting image alongside the signal notification.

        Falls back to a text-only notification if the chart data is insufficient
        or if chart generation fails.

        Args:
            symbol, interval, entry_prices, tp_list, sl, leverage, margin_type:
                Standard signal parameters.
            signal:    Full ``SignalResult``.
            htf_trend: Higher-TF trend label.
            key:       ``(symbol, interval)`` cooldown key.
            wall_now:  Current wall-clock time for cooldown recording.
        """
        min_candles = 20 if (config.SIMULATION_MODE or config.DATA_TESTING) else 50

        try:
            clean_df = self.market_data.get_clean_klines(symbol, interval)
            if len(clean_df) < min_candles:
                raise ValueError(f"Only {len(clean_df)} clean bars available")

            def _chart_callback(path: Optional[str], error):
                chart_path: Optional[str] = None
                if not error and path and os.path.exists(path):
                    size = os.path.getsize(path)
                    if 1024 <= size <= 50 * 1024 * 1024:
                        chart_path = path

                self.notifier.send_signal(
                    symbol, interval, entry_prices, tp_list, sl,
                    leverage, margin_type, signal, chart_path, htf_trend,
                )
                with self._lock:
                    self._cooldowns[key] = wall_now

            chart_data = ChartData(
                ohlc_df=clean_df,
                symbol=symbol,
                timeframe=interval,
                tp_levels=tp_list,
                sl_level=sl,
                callback=_chart_callback,
            )
            self.charting_service.submit_plot_chart_task(chart_data)

        except Exception as e:
            logger.error(f"Chart dispatch error for {symbol}-{interval}: {e}")
            self.notifier.send_signal(
                symbol, interval, entry_prices, tp_list, sl,
                leverage, margin_type, signal, None, htf_trend,
            )
            with self._lock:
                self._cooldowns[key] = wall_now

    def shutdown(self):
        """
        Gracefully shut down the signal worker thread pool.

        Waits for any in-flight signal processing to finish before returning.
        """
        logger.info("TradingEngine: shutting down signal pool")
        try:
            self._pool.shutdown(wait=True, cancel_futures=False)
        except TypeError:
            self._pool.shutdown(wait=True)
        except Exception as e:
            logger.error(f"TradingEngine shutdown error: {e}")
            try:
                self._pool.shutdown(wait=False)
            except Exception:
                pass
