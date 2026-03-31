import logging
import os
from urllib.error import HTTPError

import requests

from config import TELEGRAM_SEND_MESSAGE_URL, TELEGRAM_CHAT_ID


def format_signal_message(symbol: str, interval: str, entry_prices: list,
                          tp_list: list, sl_price: float, leverage, margin_type, risk_guidance=None) -> str:
    """
    Temiz ve anlaşılır AL/SAT sinyal mesajı formatı.
    """
    # Zaman dilimi etiketi
    tf_label = {
        "5m":  "⚡ Scalp",
        "15m": "📈 Kısa Vadeli",
        "30m": "📊 Orta-Kısa Vadeli",
        "1h":  "🕐 Orta Vadeli",
        "4h":  "📅 Uzun Vadeli",
        "1d":  "📆 Çok Uzun Vadeli"
    }.get(interval, "📊 Sinyal")

    # Giriş fiyatı
    entry_price = entry_prices[0] if entry_prices else 0

    # AL mı SAT mı — TP'nin entry'den büyük/küçük olmasına göre belirle
    if tp_list and tp_list[0] > entry_price:
        signal_type = "🟢 AL (LONG)"
        emoji = "🚀"
    else:
        signal_type = "🔴 SAT (SHORT)"
        emoji = "📉"

    # Fiyat formatı — küçük coinler için daha fazla ondalık
    def fmt(price):
        if price < 0.01:
            return f"{price:.6f}"
        elif price < 1:
            return f"{price:.4f}"
        elif price < 100:
            return f"{price:.3f}"
        else:
            return f"{price:.2f}"

    # TP seviyeleri
    tp_lines = ""
    for i, tp in enumerate(tp_list):
        pct = ((tp - entry_price) / entry_price) * 100
        sign = "+" if pct > 0 else ""
        tp_lines += f"  🎯 TP{i+1}: {fmt(tp)}  ({sign}{pct:.1f}%)\n"

    # SL yüzdesi
    sl_pct = ((sl_price - entry_price) / entry_price) * 100

    # Risk/Ödül oranı
    if tp_list and sl_price != entry_price:
        risk = abs(entry_price - sl_price)
        reward = abs(tp_list[0] - entry_price)
        rr = reward / risk if risk > 0 else 0
        rr_str = f"  ⚖️ Risk/Ödül: 1:{rr:.1f}\n"
    else:
        rr_str = ""

    msg = (
        f"{emoji} *{symbol.upper()}* | {tf_label}\n"
        f"{'─' * 28}\n"
        f"📌 *{signal_type}*\n\n"
        f"💰 *Giriş:* `{fmt(entry_price)}`\n\n"
        f"{tp_lines}"
        f"❌ *Stop Loss:* `{fmt(sl_price)}`  ({sl_pct:.1f}%)\n"
        f"{rr_str}"
        f"{'─' * 28}\n"
        f"🔧 Kaldıraç: {leverage}x | {margin_type}\n"
        f"⚠️ _Bu bir finansal tavsiye değildir._"
    )

    return msg


def send_message(text: str, chart_path: str = None):
    try:
        def escape_markdown(text: str) -> str:
            # Sadece MarkdownV2'de sorun çıkaran karakterleri escape et
            # Ama zaten * ve _ gibi formatting karakterlerini koruyalım
            escape_chars = r'>#+-=|{}.!'
            result = ""
            i = 0
            while i < len(text):
                c = text[i]
                if c == '\\':
                    result += c
                    i += 1
                    if i < len(text):
                        result += text[i]
                elif c in escape_chars:
                    result += f'\\{c}'
                else:
                    result += c
                i += 1
            return result

        if chart_path:
            url = TELEGRAM_SEND_MESSAGE_URL.replace("sendMessage", "sendPhoto")
            with open(chart_path, "rb") as photo:
                r = requests.post(
                    url,
                    data={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "caption": escape_markdown(text),
                        "parse_mode": "MarkdownV2"
                    },
                    files={"photo": photo},
                    timeout=15
                )
        else:
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": escape_markdown(text),
                "parse_mode": "MarkdownV2"
            }
            r = requests.post(TELEGRAM_SEND_MESSAGE_URL, json=payload, timeout=15)

        r.raise_for_status()
        return r.json()
    except HTTPError as e:
        logging.error("Telegram mesajı gönderilemedi", exc_info=e)
        return None
    except Exception as e:
        logging.error("Telegram mesajı gönderilemedi", exc_info=e)
        return None


def send_message_with_retry(msg: str, chart_path=None, max_retries=3):
    """Retry mantığıyla mesaj gönder"""

    if chart_path and os.path.exists(chart_path):
        file_size = os.path.getsize(chart_path)
        if file_size > 20 * 1024 * 1024:
            logging.error("Grafik çok büyük, görselsiz gönderiliyor...")
            chart_path = None
        elif file_size < 1024:
            logging.error("Grafik çok küçük, görselsiz gönderiliyor...")
            chart_path = None

    for attempt in range(max_retries):
        try:
            result = send_message(msg, chart_path) if (chart_path and os.path.exists(chart_path)) else send_message(msg)

            if result is not None:
                logging.info(f"Mesaj başarıyla gönderildi (deneme {attempt + 1})")
                return

            logging.error(f"Deneme {attempt + 1} başarısız")

            if attempt == max_retries - 1:
                send_message(msg)  # Son çare: görselsiz gönder

        except Exception as e:
            logging.error(f"Deneme {attempt + 1} hata: {e}")
            if attempt == max_retries - 1:
                try:
                    send_message(msg)
                except Exception as e2:
                    logging.error(f"Son deneme de başarısız: {e2}")
            else:
                import time
                time.sleep(1)