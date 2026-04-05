import asyncio
import logging
import os
import threading
from typing import Optional

import aiohttp

import config
from engine.confluence import SignalResult

logger = logging.getLogger(__name__)

_READABLE_MAP: dict[str, str] = {
    "EMA_9/21_GOLDEN_CROSS": "EMA Golden Cross",
    "EMA_BULLISH_ALIGNMENT": "EMA Bullish",
    "EMA_9/21_DEATH_CROSS": "EMA Death Cross",
    "EMA_BEARISH_ALIGNMENT": "EMA Bearish",
    "RSI_OVERSOLD_RECOVERY": "RSI Toparlanma",
    "RSI_BULLISH_MOMENTUM": "RSI Yukselis",
    "RSI_OVERBOUGHT_REJECTION": "RSI Red",
    "RSI_BEARISH_MOMENTUM": "RSI Dusus",
    "MACD_HISTOGRAM_FLIP": "MACD Donus",
    "MACD_BULLISH_CROSS": "MACD Alis",
    "MACD_BEARISH_CROSS": "MACD Satis",
    "MACD_INCREASING_MOMENTUM": "MACD Gucleniyor",
    "MACD_INCREASING_SELL_MOMENTUM": "MACD Satis Gucleniyor",
    "VOLUME_ABOVE_AVERAGE": "Hacim Onayi",
    "BB_NEAR_LOWER_BAND": "BB Alt Bant",
    "BB_NEAR_UPPER_BAND": "BB Ust Bant",
    "BB_MIDDLE_BREAKOUT": "BB Orta Kirilim",
    "BB_MIDDLE_BREAKDOWN": "BB Orta Dusus",
    "STOCH_RSI_BULLISH_CROSS": "StochRSI Alis",
    "STOCH_RSI_BEARISH_CROSS": "StochRSI Satis",
    "STOCH_RSI_RECOVERY": "StochRSI Toparlanma",
    "STOCH_RSI_REJECTION": "StochRSI Red",
    "BULLISH_ENGULFING": "Yutan Boga",
    "BEARISH_ENGULFING": "Yutan Ayi",
    "HAMMER": "Cekic",
    "SHOOTING_STAR": "Kayan Yildiz",
    "FIB_0.618_GOLDEN_POCKET": "Fib 0.618 Altin Cep",
    "FIB_0.786_DEEP_RETRACE": "Fib 0.786 Derin Cekilis",
    "FIB_0.500_MIDPOINT": "Fib 0.500 Orta Nokta",
    "FIB_0.382_SHALLOW": "Fib 0.382 Yuzeysel",
}

_TREND_MAP: dict[str, str] = {
    "STRONG_BULL": "Guclu Yukselis",
    "BULL": "Yukselis",
    "NEUTRAL": "Notr",
    "BEAR": "Dusus",
    "STRONG_BEAR": "Guclu Dusus",
}

_TF_LABEL: dict[str, str] = {
    "5m": "Scalp",
    "15m": "Kisa Vadeli",
    "30m": "Orta-Kisa Vadeli",
    "1h": "Orta Vadeli",
    "4h": "Uzun Vadeli",
    "1d": "Cok Uzun Vadeli",
}


class NotificationManager:

    _instance: Optional["NotificationManager"] = None
    _init_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._stop_event = threading.Event()

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.debug("NotificationManager already running")
            return

        self._loop = asyncio.new_event_loop()
        ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(ready,),
            name="NotificationLoop",
            daemon=True,
        )
        self._thread.start()
        ready.wait(timeout=10)
        logger.info("NotificationManager async loop started")

    def _run_loop(self, ready_event: threading.Event):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._bootstrap(ready_event))
            self._loop.run_forever()
        except Exception as e:
            logger.error(f"Notification loop crashed: {e}", exc_info=True)
        finally:
            try:
                if not self._loop.is_closed():
                    self._loop.run_until_complete(self._cleanup())
            except Exception as e:
                logger.debug(f"Cleanup coroutine error during shutdown: {e}")
            finally:
                if not self._loop.is_closed():
                    pending = asyncio.all_tasks(self._loop)
                    if pending:
                        logger.debug(f"Cancelling {len(pending)} pending async task(s)")
                        for task in pending:
                            task.cancel()
                        try:
                            self._loop.run_until_complete(
                                asyncio.gather(*pending, return_exceptions=True)
                            )
                        except Exception:
                            pass
                    self._loop.close()
                    logger.info("Notification event loop closed cleanly")

    async def _bootstrap(self, ready_event: threading.Event):
        timeout = aiohttp.ClientTimeout(total=20)
        self._session = aiohttp.ClientSession(timeout=timeout)
        ready_event.set()

    def stop(self):
        logger.info("Stopping NotificationManager...")
        self._stop_event.set()

        if self._loop and not self._loop.is_closed() and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

        logger.info("NotificationManager stopped")

    def send_raw_message(self, text: str):
        if config.SIMULATION_MODE:
            text = f"[SIMULATION]\n{text}"

        loop_alive = (
            self._loop is not None
            and not self._loop.is_closed()
            and self._loop.is_running()
        )
        if loop_alive:
            asyncio.run_coroutine_threadsafe(
                self._async_send(text, None), self._loop
            )
        else:
            logger.warning("Notification loop not running — using sync fallback for raw message")
            self._sync_send(text, None)

    async def _cleanup(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def send_signal(
        self,
        symbol: str,
        interval: str,
        entry_prices: list,
        tp_list: list,
        sl: float,
        leverage: int,
        margin_type: str,
        signal_result: Optional[SignalResult] = None,
        chart_path: Optional[str] = None,
        higher_tf_trend: str = "",
    ):
        msg = self._format_message(
            symbol, interval, entry_prices, tp_list, sl,
            leverage, margin_type, signal_result, higher_tf_trend,
        )

        if config.SIMULATION_MODE:
            msg = f"[SIMULATION]\n{msg}"

        loop_alive = (
            self._loop is not None
            and not self._loop.is_closed()
            and self._loop.is_running()
        )
        if loop_alive:
            asyncio.run_coroutine_threadsafe(
                self._async_send(msg, chart_path), self._loop
            )
        else:
            logger.warning("Notification loop not running — using sync fallback")
            self._sync_send(msg, chart_path)

    async def _async_send(
        self, text: str, chart_path: Optional[str], max_retries: int = 3
    ):
        for attempt in range(max_retries):
            try:
                if chart_path and os.path.exists(chart_path):
                    size = os.path.getsize(chart_path)
                    if 1024 <= size <= 20 * 1024 * 1024:
                        if await self._send_photo(text, chart_path):
                            return

                if await self._send_text(text):
                    return

            except Exception as e:
                logger.error(f"Send attempt {attempt + 1}/{max_retries} failed: {e}")

            if attempt < max_retries - 1:
                await asyncio.sleep(float(attempt + 1))

        try:
            await self._send_text(text)
        except Exception as e:
            logger.error(f"Final send attempt failed: {e}")

    async def _send_text(self, text: str) -> bool:
        if not self._session or not config.TELEGRAM_SEND_MESSAGE_URL:
            return False
        payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text}
        try:
            async with self._session.post(
                config.TELEGRAM_SEND_MESSAGE_URL, json=payload
            ) as resp:
                if resp.status == 200:
                    return True
                body = await resp.text()
                logger.error(f"Telegram API {resp.status}: {body[:200]}")
        except Exception as e:
            logger.error(f"_send_text error: {e}")
        return False

    async def _send_photo(self, caption: str, photo_path: str) -> bool:
        if not self._session or not config.TELEGRAM_SEND_PHOTO_URL:
            return False
        try:
            data = aiohttp.FormData()
            data.add_field("chat_id", str(config.TELEGRAM_CHAT_ID))
            data.add_field("caption", caption[:1024])
            with open(photo_path, "rb") as f:
                data.add_field(
                    "photo", f,
                    filename=os.path.basename(photo_path),
                    content_type="image/png",
                )
                async with self._session.post(
                    config.TELEGRAM_SEND_PHOTO_URL, data=data
                ) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.error(f"_send_photo error: {e}")
        return False

    def _sync_send(self, text: str, chart_path: Optional[str]):
        import requests

        try:
            if chart_path and os.path.exists(chart_path):
                size = os.path.getsize(chart_path)
                if 1024 <= size <= 20 * 1024 * 1024:
                    with open(chart_path, "rb") as f:
                        r = requests.post(
                            config.TELEGRAM_SEND_PHOTO_URL,
                            data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": text[:1024]},
                            files={"photo": f},
                            timeout=15,
                        )
                        r.raise_for_status()
                        return

            r = requests.post(
                config.TELEGRAM_SEND_MESSAGE_URL,
                json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text},
                timeout=15,
            )
            r.raise_for_status()
        except Exception as e:
            logger.error(f"Sync send failed: {e}")

    def _format_message(
        self,
        symbol: str,
        interval: str,
        entry_prices: list,
        tp_list: list,
        sl: float,
        leverage: int,
        margin_type: str,
        signal_result: Optional[SignalResult] = None,
        higher_tf_trend: str = "",
    ) -> str:
        tf_label = _TF_LABEL.get(interval, "Sinyal")
        entry = entry_prices[0] if entry_prices else 0.0
        is_long = bool(tp_list and tp_list[0] > entry)
        signal_type = "AL (LONG)" if is_long else "SAT (SHORT)"
        emoji = "\U0001f680" if is_long else "\U0001f4c9"

        def _fmt(p: float) -> str:
            if p < 0.01:
                return f"{p:.6f}"
            if p < 1.0:
                return f"{p:.4f}"
            if p < 100.0:
                return f"{p:.3f}"
            return f"{p:.2f}"

        tp_lines = ""
        for i, tp in enumerate(tp_list, start=1):
            pct = ((tp - entry) / entry * 100) if entry else 0.0
            sign = "+" if pct > 0 else ""
            tp_lines += f"  TP{i}: {_fmt(tp)}  ({sign}{pct:.1f}%)\n"

        sl_pct = abs((sl - entry) / entry * 100) if entry else 0.0
        risk = abs(entry - sl)
        reward = abs(tp_list[0] - entry) if tp_list else 0.0
        rr = reward / risk if risk > 0 else 0.0

        sep = "-" * 30
        msg = (
            f"{emoji} {symbol.upper()} | {tf_label}\n"
            f"{sep}\n"
            f"SINYAL: {signal_type}\n"
            f"Giris: {_fmt(entry)}\n"
            f"{tp_lines}"
            f"Stop Loss: {_fmt(sl)}  ({sl_pct:.1f}%)\n"
            f"Risk/Odul: 1:{rr:.1f}\n"
            f"{sep}\n"
            f"Kaldirac: {leverage}x | {margin_type}\n"
        )

        if signal_result:
            msg += self._format_confluence_block(signal_result, higher_tf_trend)

        return msg

    def _format_confluence_block(
        self, sr: SignalResult, htf_trend: str = ""
    ) -> str:
        names = [_READABLE_MAP.get(c, c) for c in sr.confluences]
        trend_text = _TREND_MAP.get(sr.trend_strength, sr.trend_strength)
        pct = f"{sr.confidence:.0%}"

        quality = (
            "Yuksek Kalite" if sr.confidence >= 0.75
            else "Normal" if sr.confidence >= 0.55
            else "Dusuk Guven"
        )

        htf_display = _TREND_MAP.get(htf_trend, htf_trend) if htf_trend else "INSUFFICIENT_DATA"
        if htf_trend in ("NO_HIGHER_TF", "ERROR", "PENDING", ""):
            htf_display = "INSUFFICIENT_DATA"

        lines = [
            "Teknik Analiz Detayi:",
            f"  Guven: {pct} \u2014 {quality}",
            f"  Trend: {trend_text}",
            f"  Ust TF Trend: {htf_display}",
        ]
        if sr.fib_zone:
            lines.append(f"  Fibonacci: {_READABLE_MAP.get(sr.fib_zone, sr.fib_zone)}")

        lines.append(f"  Onaylar ({len(names)}):")
        for name in names:
            lines.append(f"    - {name}")
        lines.append(f"  ATR: {sr.atr_value:.4f}")

        return "\n".join(lines) + "\n"


async def _cmd_start(update, context):
    await update.message.reply_text(
        "*Sinyal Botu Aktif!*\n\n"
        "Komutlar:\n"
        "/start    — Bu mesaj\n"
        "/durum    — Bot durumu\n"
        "/coinler  — Takip edilen coinler\n"
        "/hakkinda — Bot hakkinda\n"
        "/ip       — Sunucu IP adresi",
        parse_mode="Markdown",
    )


async def _cmd_durum(update, context):
    mode = "Simulasyon" if config.SIMULATION_MODE else "Gercek Sinyal"
    symbols = ", ".join(config.SYMBOLS) if config.SYMBOLS else "Otomatik secim"
    timeframes = ", ".join(config.TIMEFRAMES)
    await update.message.reply_text(
        "*Bot Durumu*\n\n"
        f"Mod: {mode}\n"
        f"Coinler: {symbols}\n"
        f"Zaman dilimleri: {timeframes}\n"
        "Borsa: Binance Futures",
        parse_mode="Markdown",
    )


async def _cmd_coinler(update, context):
    if config.SYMBOLS:
        coin_lines = "\n".join(f"  {s}" for s in config.SYMBOLS)
    else:
        coin_lines = "  Otomatik secim aktif"
    await update.message.reply_text(
        f"*Takip Edilen Coinler:*\n\n{coin_lines}",
        parse_mode="Markdown",
    )


async def _cmd_hakkinda(update, context):
    await update.message.reply_text(
        "*Bot Hakkinda*\n\n"
        "Bu bot Binance Futures verilerini analiz ederek "
        "EMA, RSI, MACD, Bollinger Bands, Fibonacci ve diger "
        "gostergelere gore AL/SAT sinyalleri uretir.\n\n"
        "Uyari: Bu bir finansal tavsiye degildir. "
        "Kendi arastirmanizi yapin.",
        parse_mode="Markdown",
    )


async def _cmd_ip(update, context):
    import requests as _requests
    try:
        resp = _requests.get("https://api.ipify.org", timeout=10)
        resp.raise_for_status()
        ip_addr = resp.text.strip()
        await update.message.reply_text(f"Public IP: `{ip_addr}`", parse_mode="Markdown")
    except Exception as exc:
        await update.message.reply_text(f"IP alinamadi: {exc}")
