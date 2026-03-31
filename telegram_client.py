import logging
import os
import time

import requests

from config import TELEGRAM_SEND_MESSAGE_URL, TELEGRAM_CHAT_ID


def format_signal_message(symbol: str, interval: str, entry_prices: list,
                          tp_list: list, sl_price: float, leverage, margin_type, risk_guidance=None) -> str:
    tf_label = {
        "5m":  "Scalp",
        "15m": "Kisa Vadeli",
        "30m": "Orta-Kisa Vadeli",
        "1h":  "Orta Vadeli",
        "4h":  "Uzun Vadeli",
        "1d":  "Cok Uzun Vadeli"
    }.get(interval, "Sinyal")

    entry_price = entry_prices[0] if entry_prices else 0

    if tp_list and tp_list[0] > entry_price:
        signal_type = "AL (LONG)"
        emoji = "🚀"
    else:
        signal_type = "SAT (SHORT)"
        emoji = "📉"

    def fmt(price):
        if price < 0.01:
            return f"{price:.6f}"
        elif price < 1:
            return f"{price:.4f}"
        elif price < 100:
            return f"{price:.3f}"
        else:
            return f"{price:.2f}"

    tp_lines = ""
    for i, tp in enumerate(tp_list):
        pct = ((tp - entry_price) / entry_price) * 100
        sign = "+" if pct > 0 else ""
        tp_lines += f"  TP{i+1}: {fmt(tp)}  ({sign}{pct:.1f}%)\n"

    sl_pct = ((sl_price - entry_price) / entry_price) * 100

    if tp_list and sl_price != entry_price:
        risk = abs(entry_price - sl_price)
        reward = abs(tp_list[0] - entry_price)
        rr = reward / risk if risk > 0 else 0
        rr_str = f"Risk/Odul: 1:{rr:.1f}\n"
    else:
        rr_str = ""

    line = "-" * 30

    msg = (
        f"{emoji} {symbol.upper()} | {tf_label}\n"
        f"{line}\n"
        f"SINYAL: {signal_type}\n\n"
        f"Giris: {fmt(entry_price)}\n\n"
        f"{tp_lines}"
        f"Stop Loss: {fmt(sl_price)}  ({sl_pct:.1f}%)\n"
        f"{rr_str}"
        f"{line}\n"
        f"Kaldirac: {leverage}x | {margin_type}\n"
        f"Bu bir finansal tavsiye degildir."
    )

    return msg


def send_message(text: str, chart_path: str = None):
    try:
        if chart_path and os.path.exists(chart_path):
            url = TELEGRAM_SEND_MESSAGE_URL.replace("sendMessage", "sendPhoto")
            with open(chart_path, "rb") as photo:
                r = requests.post(
                    url,
                    data={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "caption": text[:1024],
                    },
                    files={"photo": photo},
                    timeout=15
                )
        else:
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
            }
            r = requests.post(TELEGRAM_SEND_MESSAGE_URL, json=payload, timeout=15)

        r.raise_for_status()
        return r.json()
    except Exception as e:
        logging.error(f"Telegram mesaji gonderilemedi: {e}")
        return None


def send_message_with_retry(msg: str, chart_path=None, max_retries=3):
    if chart_path and os.path.exists(chart_path):
        file_size = os.path.getsize(chart_path)
        if file_size > 20 * 1024 * 1024 or file_size < 1024:
            chart_path = None

    for attempt in range(max_retries):
        try:
            result = send_message(msg, chart_path)
            if result is not None:
                logging.info(f"Mesaj gonderildi (deneme {attempt + 1})")
                return
            logging.error(f"Deneme {attempt + 1} basarisiz")
        except Exception as e:
            logging.error(f"Deneme {attempt + 1} hata: {e}")

        if attempt < max_retries - 1:
            time.sleep(1)

    try:
        result = send_message(msg)
        if result:
            logging.info("Mesaj grafiksiz gonderildi")
    except Exception as e:
        logging.error(f"Son deneme basarisiz: {e}")