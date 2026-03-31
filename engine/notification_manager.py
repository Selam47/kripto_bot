import asyncio
import logging
import os
import threading
from typing import Optional

import aiohttp

import config
from engine.confluence import SignalResult

logger = logging.getLogger(__name__)

READABLE_MAP = {
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
    "FIB_0.786_DEEP_RETRACE": "Fib 0.786 Derin Geri Cekilme",
    "FIB_0.500_MIDPOINT": "Fib 0.500 Orta Nokta",
    "FIB_0.382_SHALLOW": "Fib 0.382 Yuzeysel",
}

TREND_MAP = {
    "STRONG_BULL": "Guclu Yukselis",
    "BULL": "Yukselis",
    "NEUTRAL": "Notr",
    "BEAR": "Dusus",
    "STRONG_BEAR": "Guclu Dusus",
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
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._send_msg_url = config.TELEGRAM_SEND_MESSAGE_URL
        self._send_photo_url = config.TELEGRAM_SEND_PHOTO_URL
        self._chat_id = config.TELEGRAM_CHAT_ID

    def start(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="NotificationLoop", daemon=True
        )
        self._thread.start()
        logger.info("NotificationManager async loop started")

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._init_session())
        self._loop.run_forever()

    async def _init_session(self):
        timeout = aiohttp.ClientTimeout(total=20)
        self._session = aiohttp.ClientSession(timeout=timeout)

    async def stop(self):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._cleanup(), self._loop)
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("NotificationManager stopped")

    async def _cleanup(self):
        if self._session and not self._session.closed:
            await self._session.close()

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

        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(
                self._async_send(msg, chart_path), self._loop
            )
        else:
            logger.warning("Notification loop not running, using sync fallback")
            self._sync_send(msg, chart_path)

    async def _async_send(self, text: str, chart_path: Optional[str], max_retries: int = 3):
        for attempt in range(max_retries):
            try:
                if chart_path and os.path.exists(chart_path):
                    file_size = os.path.getsize(chart_path)
                    if 1024 <= file_size <= 20 * 1024 * 1024:
                        success = await self._send_photo(text, chart_path)
                        if success:
                            logger.info(f"Signal sent with chart (attempt {attempt + 1})")
                            return

                success = await self._send_text(text)
                if success:
                    logger.info(f"Signal sent (attempt {attempt + 1})")
                    return

            except Exception as e:
                logger.error(f"Send attempt {attempt + 1} failed: {e}")

            if attempt < max_retries - 1:
                await asyncio.sleep(1.0 * (attempt + 1))

        try:
            await self._send_text(text)
        except Exception as e:
            logger.error(f"Final send attempt failed: {e}")

    async def _send_text(self, text: str) -> bool:
        if not self._session or not self._send_msg_url:
            return False
        payload = {"chat_id": self._chat_id, "text": text}
        async with self._session.post(self._send_msg_url, json=payload) as resp:
            if resp.status == 200:
                return True
            body = await resp.text()
            logger.error(f"Telegram API error {resp.status}: {body}")
            return False

    async def _send_photo(self, caption: str, photo_path: str) -> bool:
        if not self._session or not self._send_photo_url:
            return False
        data = aiohttp.FormData()
        data.add_field("chat_id", str(self._chat_id))
        data.add_field("caption", caption[:1024])
        data.add_field("photo", open(photo_path, "rb"), filename=os.path.basename(photo_path))
        async with self._session.post(self._send_photo_url, data=data) as resp:
            return resp.status == 200

    def _sync_send(self, text: str, chart_path: Optional[str]):
        import requests as req
        try:
            if chart_path and os.path.exists(chart_path):
                url = self._send_photo_url
                with open(chart_path, "rb") as f:
                    r = req.post(url, data={"chat_id": self._chat_id, "caption": text[:1024]}, files={"photo": f}, timeout=15)
                    r.raise_for_status()
                    return
            payload = {"chat_id": self._chat_id, "text": text}
            r = req.post(self._send_msg_url, json=payload, timeout=15)
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
        tf_label = {
            "5m": "Scalp", "15m": "Kisa Vadeli", "30m": "Orta-Kisa Vadeli",
            "1h": "Orta Vadeli", "4h": "Uzun Vadeli", "1d": "Cok Uzun Vadeli",
        }.get(interval, "Sinyal")

        entry = entry_prices[0] if entry_prices else 0
        is_long = tp_list and tp_list[0] > entry
        signal_type = "AL (LONG)" if is_long else "SAT (SHORT)"
        emoji = "\U0001f680" if is_long else "\U0001f4c9"

        def fmt(p):
            if p < 0.01:
                return f"{p:.6f}"
            elif p < 1:
                return f"{p:.4f}"
            elif p < 100:
                return f"{p:.3f}"
            return f"{p:.2f}"

        tp_lines = ""
        for i, tp in enumerate(tp_list):
            pct = ((tp - entry) / entry) * 100 if entry else 0
            sign = "+" if pct > 0 else ""
            tp_lines += f"  TP{i+1}: {fmt(tp)}  ({sign}{pct:.1f}%)\n"

        sl_pct = ((sl - entry) / entry) * 100 if entry else 0
        risk = abs(entry - sl)
        reward = abs(tp_list[0] - entry) if tp_list else 0
        rr = reward / risk if risk > 0 else 0

        line = "-" * 30

        msg = (
            f"{emoji} {symbol.upper()} | {tf_label}\n"
            f"{line}\n"
            f"SINYAL: {signal_type}\n\n"
            f"Giris: {fmt(entry)}\n\n"
            f"{tp_lines}"
            f"Stop Loss: {fmt(sl)}  ({sl_pct:.1f}%)\n"
            f"Risk/Odul: 1:{rr:.1f}\n"
            f"{line}\n"
            f"Kaldirac: {leverage}x | {margin_type}\n"
        )

        if signal_result:
            msg += self._format_confluence(signal_result, higher_tf_trend)

        msg += "\nBu bir finansal tavsiye degildir."
        return msg

    def _format_confluence(self, sr: SignalResult, htf_trend: str = "") -> str:
        conf_names = [READABLE_MAP.get(c, c) for c in sr.confluences]
        trend_text = TREND_MAP.get(sr.trend_strength, sr.trend_strength)
        confidence_pct = f"{sr.confidence:.0%}"

        if sr.confidence >= 0.75:
            quality = "Yuksek Kalite"
        elif sr.confidence >= 0.55:
            quality = "Normal"
        else:
            quality = "Dusuk Guven"

        lines = [
            f"\nTeknik Analiz Detayi:",
            f"  Guven: {confidence_pct} {quality}",
            f"  Trend: {trend_text}",
        ]

        if htf_trend:
            htf_text = TREND_MAP.get(htf_trend, htf_trend)
            lines.append(f"  Ust TF Trend: {htf_text}")

        if sr.fib_zone:
            lines.append(f"  Fibonacci: {READABLE_MAP.get(sr.fib_zone, sr.fib_zone)}")

        lines.append(f"  Onaylar ({len(conf_names)}):")
        for name in conf_names:
            lines.append(f"    - {name}")
        lines.append(f"  ATR: {sr.atr_value:.4f}")

        return "\n".join(lines)
