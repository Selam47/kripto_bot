import logging
import time
from concurrent.futures import ThreadPoolExecutor
import threading

import config
from risk_manager import RiskManager
from strategy import check_signal
from telegram_client import format_signal_message, send_message_with_retry
from trade_manager import TradeManager
from util import create_realistic_test_data, timeframe_to_seconds, now_utc
from database import get_database
from structs import ChartData, ChartCallbackData, SignalNotificationData


class StrategyExecutor:
    def __init__(self, trade_manager: TradeManager | None, charting_service=None, risk_manager: RiskManager | None = None):
        self.trade_manager = trade_manager
        self.signal_cooldown = {}
        self.charting_service = charting_service
        self.risk_manager = risk_manager
        self.db = get_database() if config.DB_ENABLE_PERSISTENCE else None
        self.signal_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="SignalProcessor")
        self.processing_lock = threading.Lock()

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
                risk_guidance=None
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

    def _async_process_signals(self, symbol, interval):
        try:
            df = self.trade_manager.get_kline_data(symbol, interval)
            self.process_signals(symbol, interval, df)
        except Exception as e:
            logging.error(f"Error in async signal processing for {symbol}-{interval}: {e}")

    def process_signals(self, symbol, interval, df):
        min_candles_needed = 20 if config.SIMULATION_MODE or config.DATA_TESTING else 50

        if len(df) < min_candles_needed and self.trade_manager.has_historical_loader:
            logging.info(f"Insufficient real-time data for {symbol}-{interval} ({len(df)} candles), attempting lazy load...")
            lazy_loaded = self.trade_manager.lazy_load_historical_data(symbol, interval)
            if lazy_loaded:
                df = self.trade_manager.get_kline_data(symbol, interval)
                logging.info(f"After lazy loading: {len(df)} candles available for {symbol}-{interval}")

        if len(df) < min_candles_needed:
            logging.warning(f"Signals skipped. Need {min_candles_needed} candles, have {len(df)}.")
            return

        key = (symbol, interval)
        current_time = time.time()

        with self.processing_lock:
            if self.db:
                last_signal_time_db = self.db.get_last_signal_time(symbol, interval)
                last_signal_timestamp = last_signal_time_db.timestamp() if last_signal_time_db else 0
            else:
                last_signal_timestamp = self.signal_cooldown.get(key, 0)

            if config.DATA_TESTING:
                cooldown_seconds = 0
            elif config.SIMULATION_MODE:
                cooldown_seconds = config.SIGNAL_COOLDOWN
            else:
                cooldown_seconds = timeframe_to_seconds(interval)

            time_diff = current_time - last_signal_timestamp
            if time_diff < cooldown_seconds:
                return

        if not self._check_higher_timeframe_trend(symbol, interval):
            return

        signal_info = check_signal(df)
        if not signal_info:
            return

        try:
            last_price = float(df["close"].iloc[-1])
        except (IndexError, ValueError, TypeError):
            logging.warning(f"Error getting last price for {symbol}-{interval}")
            return

        max_leverage = self.risk_manager.get_max_leverage_for_symbol(symbol) if self.risk_manager else 20
        margin_type = "ISOLATED"
        entry_prices, tp_list, sl, risk_guidance = self._generate_trade_parameters(signal_info, last_price, df, symbol)

        if not entry_prices:
            return

        # ── Grafik varsa grafik ile gönder, yoksa direkt gönder ──
        if self.charting_service:
            try:
                clean_df = self.trade_manager.get_clean_kline_data_for_chart(symbol, interval)
                if len(clean_df) < min_candles_needed:
                    logging.warning(f"Not enough clean data for chart {symbol}-{interval}")
                    del clean_df
                    # Grafik olmadan gönder
                    self._send_without_chart(symbol, interval, entry_prices, tp_list, sl, signal_info, max_leverage, margin_type, key, current_time)
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
                logging.error(f"Chart error for {symbol}-{interval}: {e} — grafik olmadan gönderiliyor")
                self._send_without_chart(symbol, interval, entry_prices, tp_list, sl, signal_info, max_leverage, margin_type, key, current_time)
        else:
            # Grafik servisi yok — direkt mesaj at
            self._send_without_chart(symbol, interval, entry_prices, tp_list, sl, signal_info, max_leverage, margin_type, key, current_time)

    def _send_without_chart(self, symbol, interval, entry_prices, tp_list, sl, signal_info, max_leverage, margin_type, key, current_time):
        """Grafik olmadan direkt Telegram'a sinyal gönder."""
        notif_data = SignalNotificationData(
            symbol=symbol,
            interval=interval,
            entry_prices=entry_prices,
            tp_list=tp_list,
            sl=sl,
            chart_path=None,
            signal_info=signal_info,
            leverage=max_leverage,
            margin_type=margin_type,
            risk_guidance=None
        )
        self._send_signal_notif(notif_data)
        with self.processing_lock:
            self.signal_cooldown[key] = current_time

    def _check_higher_timeframe_trend(self, symbol, interval):
        higher_timeframes = {'15m': '1h', '30m': '4h', '1h': '4h', '4h': '1d'}
        higher_tf = higher_timeframes.get(interval)
        if not higher_tf:
            return True
        try:
            higher_df = self.trade_manager.get_kline_data(symbol, higher_tf)
            if len(higher_df) < 20:
                return True
            higher_df['MA_20'] = higher_df['close'].rolling(20).mean()
            higher_df['MA_50'] = higher_df['close'].rolling(50).mean()
            higher_df_clean = higher_df.dropna()
            if len(higher_df_clean) < 2:
                return True
            return True
        except Exception as e:
            logging.warning(f"Higher timeframe check error for {symbol}-{interval}: {e}")
            return True

    def _generate_trade_parameters(self, signal_info, last_price, df=None, symbol=None):
        if symbol and self.risk_manager and config.LEVERAGE_BASED_TP_SL_ENABLED:
            try:
                tp_list, sl, risk_info = self.risk_manager.calculate_leverage_based_tp_sl(symbol, last_price, signal_info)
                entry_prices = [last_price]
                risk_guidance = {}
                risk_guidance.update(risk_info)
                return entry_prices, tp_list, sl, risk_guidance
            except Exception as e:
                logging.error(f"Leverage-based TP/SL error for {symbol}: {e}")

        sl_percent = config.DEFAULT_SL_PERCENT
        tp_percents = config.DEFAULT_TP_PERCENTS

        if signal_info == "BUY":
            entry_prices = [last_price]
            tp_list = [last_price * (1 + p) for p in tp_percents]
            sl = last_price * (1 - sl_percent)
            return entry_prices, tp_list, sl, None

        elif signal_info == "SELL":
            entry_prices = [last_price]
            tp_list = [last_price * (1 - p) for p in tp_percents]
            sl = last_price * (1 + sl_percent)
            return entry_prices, tp_list, sl, None

        return None, None, None, None

    def _send_signal_notif(self, notif_data: SignalNotificationData):
        if self.db:
            signal_data = {
                'symbol': notif_data.symbol,
                'interval': notif_data.interval,
                'signal_type': notif_data.signal_info,
                'price': notif_data.entry_prices[0] if notif_data.entry_prices else 0,
                'entry_prices': notif_data.entry_prices,
                'tp_levels': notif_data.tp_list,
                'sl_level': notif_data.sl,
                'leverage': notif_data.leverage,
                'margin_type': notif_data.margin_type,
                'timestamp': now_utc()
            }
            self.db.store_signal(signal_data)

        if config.SIMULATION_MODE:
            original_msg = format_signal_message(
                notif_data.symbol, notif_data.interval, notif_data.entry_prices,
                notif_data.tp_list, notif_data.sl, notif_data.leverage,
                notif_data.margin_type, risk_guidance=None
            )
            msg = f"🚦 [SIMULATION] 🚦\n{original_msg}"
        else:
            msg = format_signal_message(
                notif_data.symbol, notif_data.interval, notif_data.entry_prices,
                notif_data.tp_list, notif_data.sl, notif_data.leverage,
                notif_data.margin_type, risk_guidance=None
            )

        send_message_with_retry(msg, notif_data.chart_path)
        app_mode = "SIMULATION" if config.SIMULATION_MODE else "LIVE"
        logging.info(f"✅ Sinyal gönderildi: {notif_data.signal_info} | {notif_data.symbol}-{notif_data.interval} ({app_mode})")

    def run_testing_mode(self):
        test_symbols = config.SYMBOLS if config.SYMBOLS else ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
        logging.info(f"Test modu: {test_symbols}")

        for symbol in test_symbols:
            for interval in config.TIMEFRAMES:
                try:
                    df = create_realistic_test_data(periods=50, base_price=30000)
                    test_signal = "BUY" if hash(symbol + interval) % 2 == 0 else "SELL"
                    last_price = float(df['close'].iloc[-1])
                    entry_prices, tp_list, sl, risk_guidance = self._generate_trade_parameters(test_signal, last_price, df, symbol)

                    if entry_prices:
                        if self.charting_service:
                            chart_data = ChartData(
                                ohlc_df=df, symbol=symbol, timeframe=interval,
                                tp_levels=tp_list, sl_level=sl,
                                callback=lambda path, error: self.handle_chart_callback(
                                    ChartCallbackData(
                                        chart_path=path, error=error, symbol=symbol, interval=interval,
                                        entry_prices=entry_prices, tp_list=tp_list, sl=sl,
                                        signal_info=test_signal,
                                        leverage=self.risk_manager.get_max_leverage_for_symbol(symbol) if self.risk_manager else 20,
                                        margin_type="Isolated"
                                    )
                                )
                            )
                            self.charting_service.submit_plot_chart_task(chart_data)
                        else:
                            key = (symbol, interval)
                            self._send_without_chart(symbol, interval, entry_prices, tp_list, sl, test_signal, 20, "ISOLATED", key, time.time())
                        time.sleep(1)
                except Exception as e:
                    logging.error(f"Test modu hatası {symbol} {interval}: {e}")

    def shutdown(self):
        logging.info("StrategyExecutor kapatılıyor...")
        try:
            self.signal_executor.shutdown(wait=True)
            logging.info("Signal processing thread pool shut down successfully")
        except Exception as e:
            logging.error(f"Shutdown error: {e}")
            try:
                self.signal_executor.shutdown(wait=False)
            except Exception:
                pass