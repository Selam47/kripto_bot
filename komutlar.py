import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

load_dotenv()
TOKEN = os.getenv("8664805865:AAHAzi1SZaa5LpWIxRj9PmStI58jsqHZk38")
CHAT_ID = os.getenv("-1003876494283")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 *Sinyal Botu Aktif!*\n\n"
        "📌 Komutlar:\n"
        "/start — Bu mesaj\n"
        "/durum — Bot durumu\n"
        "/coinler — Takip edilen coinler\n"
        "/hakkında — Bot hakkında",
        parse_mode="Markdown"
    )

async def durum(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ *Bot Durumu*\n\n"
        "🟢 Sinyal botu: Çalışıyor\n"
        "📊 Mod: Gerçek Sinyal\n"
        "🪙 Takip: 8 coin\n"
        "⏱ Zaman dilimleri: 1h, 4h\n"
        "📡 Borsa: Binance Futures",
        parse_mode="Markdown"
    )

async def coinler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🪙 *Takip Edilen Coinler:*\n\n"
        "• BTC/USDT — Bitcoin\n"
        "• ETH/USDT — Ethereum\n"
        "• SOL/USDT — Solana\n"
        "• BNB/USDT — BNB\n"
        "• XRP/USDT — Ripple\n"
        "• DOGE/USDT — Dogecoin\n"
        "• ADA/USDT — Cardano\n"
        "• AVAX/USDT — Avalanche",
        parse_mode="Markdown"
    )

async def hakkinda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "ℹ️ *Bot Hakkında*\n\n"
        "Bu bot Binance Futures verilerini analiz ederek "
        "RSI, EMA ve hacim göstergelerine göre "
        "AL/SAT sinyalleri üretir.\n\n"
        "⚠️ Bu bir finansal tavsiye değildir.\n"
        "Kendi araştırmanı yap.",
        parse_mode="Markdown"
    )

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("durum", durum))
    app.add_handler(CommandHandler("coinler", coinler))
    app.add_handler(CommandHandler("hakkinda", hakkinda))
    print("✅ Komut botu başlatıldı...")
    app.run_polling()

if __name__ == "__main__":
    main()