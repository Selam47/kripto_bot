"""
StrategyExecutor — Güncellenmiş Versiyon
=========================================
Yenilikler:
  - Confluence bazlı sinyal sistemi entegrasyonu
  - ATR bazlı dinamik TP/SL (sabit yüzde yerine)
  - Sinyal kalite skoru ve confluence bilgisi Telegram mesajına eklenir
  - Düşük kaliteli sinyaller filtrelenir
  - sent_signals bellek temizliği (memory leak önleme)
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
import threading

import config
from risk_manager import RiskManager
from strategy import check_signal, calculate_atr_based_levels, SignalResult
from telegram_client import format_signal_message, send_message_with_retry
from trade_manager import TradeManager
from util import create_realistic_test_data, timeframe_to_seconds, now_utc
from database import get_database
from structs import ChartData, ChartCallbackData, SignalNotificationData


# Sent signals cache temizliği için max yaş (saniye)
SENT_SIGNALS_MAX_AGE = 3600  # 1 saat
SENT_SIGNALS_CLEANUP_INTERVAL = 600  # 10 dakikada bir temizle


class StrategyExecutor:
    def __init__(self, trade_manager: TradeManager | None, charting_service=None, risk_manager: RiskManager | None = None):
        self.trade_manager = trade_manager
        self.signal_cooldown = {}
        self.charting_service = charting_service
        self.risk_manager = risk_manager
        self.db = get_database() if config.DB_ENABLE_PERSISTENCE else None
        self.signal_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="SignalProcessor")
        self.processing_lock = threading.Lock()
        self.sent_signals = {}
        self._last_cleanup_time = time.time()

    # ─────────────────────────────────────────
    # Kline İşleme
    # ─────────────────────────────────────────

    def handle_kline(self, k):
        if not self._validate_kline_input(k):
            return
        symbol = k["s"]
        interval = k["i"]
        self.trade_manager.update_kline_data(k)
        self.signal_executor.submit(self._async_process_signals, symbol, interval)

    def _validate_kline_input(self, k):
        if not isinstance(k, dict):
            return False
        required_fields = ['s', 'i', 'o', 'h', 'l', 'c', 'v', 't']
        for field in required_fields:
            if field not in k:
                return False
        try:
            float(k['o']); float(k['h']); float(k['l']); float(k['c']); float(k['v']); int(k['t'])
        except (ValueError, TypeError):
            return False
        return True

    # ─────────────────────────────────────────
    # Chart Callback
    # ─────────────────────────────────────────

    def handle_chart_callback(self, callback_data: ChartCallbackData):
        try:
            chart_path = None
            if not callback_data.error:
                chart_path = callback_data.chart_path
                if chart_path and not self._validate_chart_file(chart_path):
                    chart_path = None

            notif_data = SignalNotificationData(
                symbol=callback_data.symbol,
                interval=callback_data.interval,
                entry_prices=callback_data.entry_prices,
                tp_list=callback_data.tp_list,
                sl=callback_data.sl,
                chart_path=chart_path,
                signal_info=callback_data.signal_info,
                leverage=callback_data.leverage,
                margin_type=callback_data.margin_type,
                risk_guidance=callback_data.risk_guidance if hasattr(callback_data, 'risk_guidance') else None
            )
            self._send_signal_notif(notif_data)

            with self.processing_lock:
                self.signal_cooldown[(callback_data.symbol, callback_data.interval)] = time.time()
        finally:
            callback_data = None

    def _validate_chart_file(self, chart_path):
        try:
            import os
            if not os.path.exists(chart_path):
                return False
            file_size = os.path.getsize(chart_path)
            if file_size < 1024 or file_size > 50 * 1024 * 1024:
                return False
            return True
        except Exception:
            return False

    # ─────────────────────────────────────────
    # Asenkron Sinyal İşleme
    # ─────────────────────────────────────────

    def _async_process_signals(self, symbol, interval):
        try:
            df = self.trade_manager.get_kline_data(symbol, interval)
            self.process_signals(symbol, interval, df)
        except Exception as e:
            logging.error(f"Error in async signal processing for {symbol}-{interval}: {e}")

    def _is_on_cooldown(self, key, current_time, cooldown_seconds):
        with self.processing_lock:
            if self.db:
                last_signal_time_db = self.db.get_last_signal_time(key[0], key[1])
                last_signal_timestamp = last_signal_time_db.timestamp() if last_signal_time_db else 0
            else:
                last_signal_timestamp = self.signal_cooldown.get(key, 0)

            time_diff = current_time - last_signal_timestamp
            if time_diff < cooldown_seconds:
                return True

            self.signal_cooldown[key] = current_time
            return False

    # ─────────────────────────────────────────
    # Ana Sinyal İşleme
    # ─────────────────────────────────────────

    def process_signals(self, symbol, interval, df):
        min_candles_needed = 20 if config.SIMULATION_MODE or config.DATA_TESTING else 50

        if len(df) < min_candles_needed and self.trade_manager.has_historical_loader:
            lazy_loaded = self.trade_manager.lazy_load_historical_data(symbol, interval)
            if lazy_loaded:
                df = self.trade_manager.get_kline_data(symbol, interval)

        if len(df) < min_candles_needed:
            return

        key = (symbol, interval)
        current_time = time.time()

        if config.DATA_TESTING:
            cooldown_seconds = 0
        elif config.SIMULATION_MODE:
            cooldown_seconds = config.SIGNAL_COOLDOWN
        else:
            cooldown_seconds = timeframe_to_seconds(interval)

        if self._is_on_cooldown(key, current_time, cooldown_seconds):
            return

        if not self._check_higher_timeframe_trend(symbol, interval):
            return

        # ── Yeni confluence bazlı sinyal kontrolü ──
        signal_result = check_signal(df)
        if not signal_result:
            return

        # SignalResult objesinden yön bilgisini al
        signal_info = signal_result.direction  # "BUY" veya "SELL"

        try:
            last_price = float(df["close"].iloc[-1])
        except (IndexError, ValueError, TypeError):
            logging.warning(f"Error getting last price for {symbol}-{interval}")
            return

        # Aynı fiyatta aynı sinyal tekrar gitmesin
        signal_key = f"{symbol}-{interval}-{signal_info}-{last_price:.2f}"
        with self.processing_lock:
            last_sent = self.sent_signals.get(signal_key, 0)
            if current_time - last_sent < cooldown_seconds:
                logging.debug(f"Duplicate signal blocked: {signal_key}")
                return
            self.sent_signals[signal_key] = current_time

        # Periyodik bellek temizliği
        self._cleanup_sent_signals(current_time)

        max_leverage = self.risk_manager.get_max_leverage_for_symbol(symbol) if self.risk_manager else 20
        margin_type = "ISOLATED"

        # ── ATR bazlı dinamik TP/SL hesaplama ──
        entry_prices, tp_list, sl, risk_guidance = self._generate_trade_parameters(
            signal_info, last_price, df, symbol, signal_result=signal_result
        )

        if not entry_prices:
            return

        # Confluence bilgisini risk_guidance'a ekle
        confluence_text = self._format_confluence_info(signal_result)

        logging.info(
            f"📊 Sinyal bulundu: {signal_info} {symbol}-{interval} | "
            f"Confidence: {signal_result.confidence:.0%} | "
            f"Confluences: {', '.join(signal_result.confluences)} | "
            f"Trend: {signal_result.trend_strength}"
        )

        if self.charting_service:
            try:
                clean_df = self.trade_manager.get_clean_kline_data_for_chart(symbol, interval)
                if len(clean_df) < min_candles_needed:
                    del clean_df
                    self._send_without_chart(
                        symbol, interval, entry_prices, tp_list, sl, signal_info,
                        max_leverage, margin_type, key, current_time, confluence_text
                    )
                    return

                chart_data = ChartData(
                    ohlc_df=clean_df,
                    symbol=symbol,
                    timeframe=interval,
                    tp_levels=tp_list,
                    sl_level=sl,
                    callback=lambda path, error: self.handle_chart_callback(
                        ChartCallbackData(
                            chart_path=path, error=error, symbol=symbol, interval=interval,
                            entry_prices=entry_prices, tp_list=tp_list, sl=sl,
                            signal_info=signal_info, leverage=max_leverage, margin_type=margin_type
                        )
                    )
                )
                self.charting_service.submit_plot_chart_task(chart_data)
                del clean_df
            except Exception as e:
                logging.error(f"Chart error for {symbol}-{interval}: {e}")
                self._send_without_chart(
                    symbol, interval, entry_prices, tp_list, sl, signal_info,
                    max_leverage, margin_type, key, current_time, confluence_text
                )
        else:
            self._send_without_chart(
                symbol, interval, entry_prices, tp_list, sl, signal_info,
                max_leverage, margin_type, key, current_time, confluence_text
            )

    # ─────────────────────────────────────────
    # TP/SL Hesaplama (Güncellenmiş)
    # ─────────────────────────────────────────

    def _generate_trade_parameters(self, signal_info, last_price, df=None, symbol=None, signal_result=None):
        """
        Öncelik sırası:
        1. ATR bazlı dinamik seviyeler (signal_result varsa)
        2. Risk manager leverage bazlı seviyeler
        3. Sabit yüzde bazlı seviyeler (fallback)
        """

        # ── 1) ATR bazlı dinamik seviyeler ──
        if signal_result and df is not None:
            try:
                trade_levels = calculate_atr_based_levels(signal_result, df)
                if trade_levels:
                    logging.debug(
                        f"ATR-based levels: Entry={trade_levels.entry_prices[0]:.2f}, "
                        f"TP={[f'{tp:.2f}' for tp in trade_levels.tp_list]}, "
                        f"SL={trade_levels.sl:.2f}, R:R={trade_levels.risk_reward_ratio}"
                    )
                    return trade_levels.entry_prices, trade_levels.tp_list, trade_levels.sl, trade_levels.position_bias
            except Exception as e:
                logging.error(f"ATR-based TP/SL error: {e}")

        # ── 2) Risk manager leverage bazlı ──
        if symbol and self.risk_manager and config.LEVERAGE_BASED_TP_SL_ENABLED:
            try:
                tp_list, sl, risk_info = self.risk_manager.calculate_leverage_based_tp_sl(symbol, last_price, signal_info)
                return [last_price], tp_list, sl, risk_info
            except Exception as e:
                logging.error(f"Leverage-based TP/SL error for {symbol}: {e}")

        # ── 3) Sabit yüzde (fallback) ──
        sl_percent = config.DEFAULT_SL_PERCENT
        tp_percents = config.DEFAULT_TP_PERCENTS

        if signal_info == "BUY":
            return [last_price], [last_price * (1 + p) for p in tp_percents], last_price * (1 - sl_percent), None
        elif signal_info == "SELL":
            return [last_price], [last_price * (1 - p) for p in tp_percents], last_price * (1 + sl_percent), None

        return None, None, None, None

    # ─────────────────────────────────────────
    # Mesaj Gönderme
    # ─────────────────────────────────────────

    def _send_without_chart(self, symbol, interval, entry_prices, tp_list, sl, signal_info,
                            max_leverage, margin_type, key, current_time, confluence_text=None):
        notif_data = SignalNotificationData(
            symbol=symbol, interval=interval, entry_prices=entry_prices,
            tp_list=tp_list, sl=sl, chart_path=None, signal_info=signal_info,
            leverage=max_leverage, margin_type=margin_type, risk_guidance=confluence_text
        )
        self._send_signal_notif(notif_data)
        with self.processing_lock:
            self.signal_cooldown[key] = current_time

    def _send_signal_notif(self, notif_data: SignalNotificationData):
        if self.db:
            self.db.store_signal({
                'symbol': notif_data.symbol, 'interval': notif_data.interval,
                'signal_type': notif_data.signal_info,
                'price': notif_data.entry_prices[0] if notif_data.entry_prices else 0,
                'entry_prices': notif_data.entry_prices, 'tp_levels': notif_data.tp_list,
                'sl_level': notif_data.sl, 'leverage': notif_data.leverage,
                'margin_type': notif_data.margin_type, 'timestamp': now_utc()
            })

        if config.SIMULATION_MODE:
            original_msg = format_signal_message(
                notif_data.symbol, notif_data.interval, notif_data.entry_prices,
                notif_data.tp_list, notif_data.sl, notif_data.leverage,
                notif_data.margin_type, risk_guidance=notif_data.risk_guidance
            )
            msg = f"🚦 [SIMULATION] 🚦\n{original_msg}"
        else:
            msg = format_signal_message(
                notif_data.symbol, notif_data.interval, notif_data.entry_prices,
                notif_data.tp_list, notif_data.sl, notif_data.leverage,
                notif_data.margin_type, risk_guidance=notif_data.risk_guidance
            )

        send_message_with_retry(msg, notif_data.chart_path)
        app_mode = "SIMULATION" if config.SIMULATION_MODE else "LIVE"
        logging.info(f"✅ Sinyal gönderildi: {notif_data.signal_info} | {notif_data.symbol}-{notif_data.interval} ({app_mode})")

    # ─────────────────────────────────────────
    # Confluence Bilgisi Formatlama
    # ─────────────────────────────────────────

    def _format_confluence_info(self, signal_result: SignalResult) -> str:
        """Telegram mesajına eklenecek confluence bilgisi oluşturur."""
        # İndikatör isimlerini kısa ve okunabilir hale getir
        readable_map = {
            "EMA_9/21_GOLDEN_CROSS": "EMA Golden Cross",
            "EMA_BULLISH_ALIGNMENT": "EMA Bullish",
            "EMA_9/21_DEATH_CROSS": "EMA Death Cross",
            "EMA_BEARISH_ALIGNMENT": "EMA Bearish",
            "RSI_OVERSOLD_RECOVERY": "RSI Toparlanma",
            "RSI_BULLISH_MOMENTUM": "RSI Yükseliş",
            "RSI_OVERBOUGHT_REJECTION": "RSI Red",
            "RSI_BEARISH_MOMENTUM": "RSI Düşüş",
            "MACD_HISTOGRAM_FLIP": "MACD Dönüş",
            "MACD_BULLISH_CROSS": "MACD Alış",
            "MACD_BEARISH_CROSS": "MACD Satış",
            "MACD_INCREASING_MOMENTUM": "MACD Güçleniyor",
            "MACD_INCREASING_SELL_MOMENTUM": "MACD Satış Güçleniyor",
            "VOLUME_ABOVE_AVERAGE": "Hacim Onayı",
            "BB_NEAR_LOWER_BAND": "BB Alt Bant",
            "BB_NEAR_UPPER_BAND": "BB Üst Bant",
            "BB_MIDDLE_BREAKOUT": "BB Orta Kırılım",
            "BB_MIDDLE_BREAKDOWN": "BB Orta Düşüş",
            "STOCH_RSI_BULLISH_CROSS": "StochRSI Alış",
            "STOCH_RSI_BEARISH_CROSS": "StochRSI Satış",
            "STOCH_RSI_RECOVERY": "StochRSI Toparlanma",
            "STOCH_RSI_REJECTION": "StochRSI Red",
            "BULLISH_ENGULFING": "Yutan Boğa",
            "BEARISH_ENGULFING": "Yutan Ayı",
            "HAMMER": "Çekiç",
            "SHOOTING_STAR": "Kayan Yıldız",
        }

        trend_map = {
            "STRONG_BULL": "Güçlü Yükseliş 📈",
            "BULL": "Yükseliş 📈",
            "NEUTRAL": "Nötr ➡️",
            "BEAR": "Düşüş 📉",
            "STRONG_BEAR": "Güçlü Düşüş 📉",
        }

        conf_names = [readable_map.get(c, c) for c in signal_result.confluences]
        trend_text = trend_map.get(signal_result.trend_strength, signal_result.trend_strength)
        confidence_pct = f"{signal_result.confidence:.0%}"

        # Confidence'a göre emoji
        if signal_result.confidence >= 0.75:
            quality = "🔥 Yüksek Kalite"
        elif signal_result.confidence >= 0.55:
            quality = "✅ Normal"
        else:
            quality = "⚠️ Düşük Güven"

        lines = [
            f"\n📊 Teknik Analiz Detayı:",
            f"├ Güven: {confidence_pct} {quality}",
            f"├ Trend: {trend_text}",
            f"├ Onaylar ({len(conf_names)}):",
        ]
        for name in conf_names:
            lines.append(f"│  • {name}")
        lines.append(f"└ ATR: {signal_result.atr_value:.4f}")

        return "\n".join(lines)

    # ─────────────────────────────────────────
    # Higher Timeframe Trend Kontrolü
    # ─────────────────────────────────────────

    def _check_higher_timeframe_trend(self, symbol, interval):
        higher_timeframes = {'15m': '1h', '30m': '4h', '1h': '4h', '4h': '1d'}
        higher_tf = higher_timeframes.get(interval)
        if not higher_tf:
            return True
        try:
            higher_df = self.trade_manager.get_kline_data(symbol, higher_tf)
            if len(higher_df) < 20:
                return True
            return True  # Şimdilik her zaman True — ileride trend filtresi eklenebilir
        except Exception:
            return True

    # ─────────────────────────────────────────
    # Bellek Temizliği
    # ─────────────────────────────────────────

    def _cleanup_sent_signals(self, current_time):
        """Eski sent_signals kayıtlarını temizle — memory leak önleme."""
        if current_time - self._last_cleanup_time < SENT_SIGNALS_CLEANUP_INTERVAL:
            return
        with self.processing_lock:
            expired_keys = [
                k for k, t in self.sent_signals.items()
                if current_time - t > SENT_SIGNALS_MAX_AGE
            ]
            for k in expired_keys:
                del self.sent_signals[k]
            if expired_keys:
                logging.debug(f"Cleaned up {len(expired_keys)} expired signal entries")
            self._last_cleanup_time = current_time

    # ─────────────────────────────────────────
    # Test Modu
    # ─────────────────────────────────────────

    def run_testing_mode(self):
        test_symbols = config.SYMBOLS if config.SYMBOLS else ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
        for symbol in test_symbols:
            for interval in config.TIMEFRAMES:
                try:
                    df = create_realistic_test_data(periods=50, base_price=30000)
                    test_signal = "BUY" if hash(symbol + interval) % 2 == 0 else "SELL"
                    last_price = float(df['close'].iloc[-1])
                    entry_prices, tp_list, sl, _ = self._generate_trade_parameters(test_signal, last_price, df, symbol)
                    if entry_prices:
                        key = (symbol, interval)
                        self._send_without_chart(symbol, interval, entry_prices, tp_list, sl, test_signal, 20, "ISOLATED", key, time.time())
                    time.sleep(1)
                except Exception as e:
                    logging.error(f"Test modu hatası {symbol} {interval}: {e}")

    def shutdown(self):
        logging.info("StrategyExecutor kapatılıyor...")
        try:
            self.signal_executor.shutdown(wait=True)
        except Exception as e:
            logging.error(f"Shutdown error: {e}")
            try:
                self.signal_executor.shutdown(wait=False)
            except Exception:
                pass