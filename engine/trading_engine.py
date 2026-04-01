import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import config
from database import get_database
from engine.confluence import ConfluenceEngine, SignalResult
from engine.indicators import Indicators
from engine.market_data import MarketData
from engine.mtf_guard import MTFTrendGuard
from engine.notification_manager import NotificationManager
from structs import ChartData
from util import now_utc, timeframe_to_seconds

logger = logging.getLogger(__name__)


class SignalTracker:

    def __init__(
        self,
        max_age_seconds: int = 28800,
        cleanup_interval_seconds: int = 600,
    ):
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._max_age = max_age_seconds
        self._cleanup_interval = cleanup_interval_seconds
        self._last_cleanup: float = time.monotonic()

    def is_duplicate(self, symbol: str, direction: str, candle_open_time: int) -> bool:
        key = f"{symbol}-{direction}-{candle_open_time}"
        with self._lock:
            if key in self._seen:
                return True
            self._seen[key] = time.time()
            return False

    def prune(self):
        now_mono = time.monotonic()
        if now_mono - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now_mono
        wall_now = time.time()
        with self._lock:
            expired = [
                k for k, ts in self._seen.items()
                if wall_now - ts > self._max_age
            ]
            for k in expired:
                del self._seen[k]
            if expired:
                logger.debug(f"SignalTracker pruned {len(expired)} stale entries")


class TradingEngine:

    def __init__(
        self,
        market_data: MarketData,
        notification_mgr: NotificationManager,
        risk_manager=None,
        charting_service=None,
    ):
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
        self._signal_tracker = SignalTracker(
            max_age_seconds=max(config.SIGNAL_COOLDOWN * 2, 28800),
            cleanup_interval_seconds=600,
        )
        self._last_analyzed_candle: dict[tuple[str, str], int] = {}

        self._instant_alert_stop = threading.Event()
        self._instant_alert_thread: Optional[threading.Thread] = None
        self._prev_rsi: dict[tuple[str, str], float] = {}
        self._prev_macd_above: dict[tuple[str, str], Optional[bool]] = {}

    def handle_kline(self, k: dict):
        if not self._validate_kline(k):
            return
        self.market_data.update_kline(k)
        if not k.get("x", False):
            return
        self._pool.submit(self._process, k["s"], k["i"], int(k["t"]))

    @staticmethod
    def _validate_kline(k) -> bool:
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

    def _process(self, symbol: str, interval: str, candle_open_time: int):
        try:
            self._run_pipeline(symbol, interval, candle_open_time)
        except Exception as e:
            logger.error(
                f"Unhandled error in signal pipeline {symbol}-{interval}: {e}",
                exc_info=True,
            )

    def _run_pipeline(self, symbol: str, interval: str, candle_open_time: int):
        key = (symbol, interval)

        with self._lock:
            if self._last_analyzed_candle.get(key) == candle_open_time:
                logger.debug(
                    "Candle %d already processed for %s-%s — skipping",
                    candle_open_time, symbol, interval,
                )
                return
            self._last_analyzed_candle[key] = candle_open_time

        min_candles = 20 if (config.SIMULATION_MODE or config.DATA_TESTING) else 50

        df = self.market_data.get_klines(symbol, interval)

        if len(df) < min_candles and self.market_data.has_historical_loader:
            if self.market_data.lazy_load(symbol, interval):
                df = self.market_data.get_klines(symbol, interval)

        if len(df) < min_candles:
            return

        wall_now = time.time()

        cooldown = 0.0 if config.DATA_TESTING else float(config.SIGNAL_COOLDOWN)

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

        if self._signal_tracker.is_duplicate(symbol, signal.direction, candle_open_time):
            logger.debug(
                f"Duplicate suppressed: {symbol} {signal.direction} candle={candle_open_time}"
            )
            return

        self._signal_tracker.prune()

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
        try:
            levels = ConfluenceEngine.calculate_atr_levels(signal, df)
            if levels:
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

    def run_initial_analysis(self, symbols: list[str], interval: str) -> None:
        self._pool.submit(self._send_startup_pulse, symbols, interval)

    def _send_startup_pulse(self, symbols: list[str], interval: str) -> None:
        import datetime
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"\U0001f7e2 Sistem Aktif \u2014 Pazar Durumu",
            f"Zaman: {now_str}",
            f"Mod: {'Simulasyon' if config.SIMULATION_MODE else 'Gercek Sinyal'}",
            "-" * 32,
        ]

        for symbol in symbols:
            try:
                df = self.market_data.get_klines(symbol, interval)
                if df is None or len(df) < 30:
                    lines.append(f"\u26a0\ufe0f {symbol}: Yeterli veri yok")
                    continue

                snap = Indicators.compute_snapshot(df)
                if snap is None:
                    lines.append(f"\u26a0\ufe0f {symbol}: Indikator hesaplanamadi")
                    continue

                price = snap.close
                if price < 0.01:
                    price_str = f"{price:.6f}"
                elif price < 1.0:
                    price_str = f"{price:.4f}"
                elif price < 100:
                    price_str = f"{price:.3f}"
                else:
                    price_str = f"{price:.2f}"

                if snap.ema_9 > snap.ema_21 > snap.ema_50:
                    ema_status = "\U0001f4c8 Yukselis"
                elif snap.ema_9 < snap.ema_21 < snap.ema_50:
                    ema_status = "\U0001f4c9 Dusus"
                else:
                    ema_status = "\u27a1\ufe0f Notr"

                if snap.rsi < 30:
                    rsi_label = "Asiri Satim"
                elif snap.rsi > 70:
                    rsi_label = "Asiri Alim"
                else:
                    rsi_label = "Normal"

                macd_dir = "Yukari" if snap.histogram > 0 else "Asagi"

                bb_range = snap.bb_upper - snap.bb_lower
                if bb_range > 0:
                    bb_pos = (price - snap.bb_lower) / bb_range * 100
                    if bb_pos < 20:
                        bb_label = "Alt banda yakin"
                    elif bb_pos > 80:
                        bb_label = "Ust banda yakin"
                    else:
                        bb_label = "Orta bolgede"
                else:
                    bb_label = "N/A"

                lines += [
                    f"\U0001f4b0 {symbol}",
                    f"  Fiyat : {price_str}",
                    f"  EMA   : {ema_status}",
                    f"  RSI   : {snap.rsi:.1f}  ({rsi_label})",
                    f"  MACD  : Histogram {macd_dir} ({snap.histogram:.4f})",
                    f"  BB    : {bb_label}",
                    "-" * 32,
                ]

            except Exception as exc:
                logger.error(f"Startup pulse error for {symbol}: {exc}", exc_info=True)
                lines.append(f"\u274c {symbol}: Hata \u2014 {exc}")

        msg = "\n".join(lines)
        self.notifier.send_raw_message(msg)
        logger.info("Startup pulse sent to Telegram")

    def run_periodic_check(self, symbols: list[str], interval: str) -> None:
        for symbol in symbols:
            try:
                df = self.market_data.get_klines(symbol, interval)
                if df is None or len(df) < 2:
                    continue
                candle_open_time = (
                    int(df.index[-1].timestamp() * 1000)
                    if hasattr(df.index[-1], "timestamp")
                    else int(time.time() * 1000)
                )
                self._pool.submit(self._process, symbol, interval, candle_open_time)
            except Exception as exc:
                logger.error(
                    f"Periodic check error for {symbol}: {exc}", exc_info=True
                )

    def start_instant_alert_monitor(
        self,
        symbols: list[str],
        interval: str,
        poll_seconds: int = 60,
    ) -> None:
        if self._instant_alert_thread and self._instant_alert_thread.is_alive():
            logger.debug("Instant alert monitor already running")
            return
        self._instant_alert_stop.clear()
        self._instant_alert_thread = threading.Thread(
            target=self._instant_alert_loop,
            args=(symbols, interval, poll_seconds),
            name="InstantAlertMonitor",
            daemon=True,
        )
        self._instant_alert_thread.start()
        logger.info(
            f"Instant alert monitor started — polling every {poll_seconds}s "
            f"for {symbols}"
        )

    def _instant_alert_loop(
        self,
        symbols: list[str],
        interval: str,
        poll_seconds: int,
    ) -> None:
        while not self._instant_alert_stop.wait(timeout=poll_seconds):
            for symbol in symbols:
                try:
                    self._check_instant_alert(symbol, interval)
                except Exception as exc:
                    logger.error(
                        f"Instant alert check error {symbol}: {exc}",
                        exc_info=True,
                    )

    def _check_instant_alert(self, symbol: str, interval: str) -> None:
        df = self.market_data.get_klines(symbol, interval)
        if df is None or len(df) < 30:
            return

        snap = Indicators.compute_snapshot(df)
        if snap is None:
            return

        key = (symbol, interval)
        triggered = False
        reasons: list[str] = []

        if snap.atr_sma > 0 and snap.atr > snap.atr_sma * 1.5:
            triggered = True
            reasons.append(
                f"ATR Spike ({snap.atr:.4f} > 1.5x SMA {snap.atr_sma:.4f})"
            )

        prev_rsi = self._prev_rsi.get(key)
        if prev_rsi is not None:
            if prev_rsi >= 30 > snap.rsi:
                triggered = True
                reasons.append(f"RSI crossed below 30 ({snap.rsi:.1f})")
            elif prev_rsi <= 30 < snap.rsi:
                triggered = True
                reasons.append(f"RSI broke above 30 ({snap.rsi:.1f})")
            elif prev_rsi <= 70 < snap.rsi:
                triggered = True
                reasons.append(f"RSI crossed above 70 ({snap.rsi:.1f})")
            elif prev_rsi >= 70 > snap.rsi:
                triggered = True
                reasons.append(f"RSI dropped below 70 ({snap.rsi:.1f})")
        self._prev_rsi[key] = snap.rsi

        macd_above = snap.macd_line > snap.signal_line
        prev_macd_above = self._prev_macd_above.get(key)
        if prev_macd_above is not None and macd_above != prev_macd_above:
            direction = "bullish" if macd_above else "bearish"
            triggered = True
            reasons.append(f"MACD {direction} crossover")
        self._prev_macd_above[key] = macd_above

        if triggered:
            candle_open_time = (
                int(df.index[-1].timestamp() * 1000)
                if hasattr(df.index[-1], "timestamp")
                else int(time.time() * 1000)
            )
            logger.info(
                f"[InstantAlert] {symbol}-{interval} triggered: "
                + "; ".join(reasons)
            )
            with self._lock:
                self._last_analyzed_candle.pop(key, None)
            self._pool.submit(self._process, symbol, interval, candle_open_time)

    def shutdown(self):
        logger.info("TradingEngine: shutting down")
        self._instant_alert_stop.set()
        if self._instant_alert_thread and self._instant_alert_thread.is_alive():
            self._instant_alert_thread.join(timeout=5)
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
