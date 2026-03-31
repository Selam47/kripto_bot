🚀 Trading Signal Bot

Binance Futures verilerini analiz ederek Telegram'a otomatik AL / SAT sinyali atan bot.
İşlem açmaz — sadece sinyal mesajı gönderir.


📌 Sinyal Örneği
🚀 BTCUSDT | 🕐 Orta Vadeli
────────────────────────────
📌 AL (LONG)

💰 Giriş:  83,420.50
🎯 TP1  :  85,089.10  (+2.0%)
🎯 TP2  :  86,757.70  (+4.0%)
🎯 TP3  :  88,426.30  (+6.0%)
❌ Stop :  81,751.69  (-2.0%)
⚖️ R/Ö :  1:1.5
────────────────────────────
🔧 20x | ISOLATED
⚠️ Finansal tavsiye değildir.

✨ Özellikler
ÖzellikAçıklama📡 Canlı VeriBinance WebSocket ile gerçek zamanlı fiyat📊 Teknik AnalizRSI + EMA50 + EMA200 + Hacim analizi🎯 TP / SLGiriş, hedef ve stop otomatik hesaplanır💬 TelegramSinyal gelince gruba / kanala mesaj atar🛡️ Rate LimitBinance API ban koruması dahil🔄 7/24Railway üzerinde kesintisiz çalışır

⚙️ Kurulum
1. Projeyi İndir
bashgit clone https://github.com/rizesky/trading_signal_bot.git
cd trading_signal_bot
2. Kütüphaneleri Kur
bashpip install -r requirements.txt
python -m playwright install chromium
3. .env Dosyasını Hazırla
example.env dosyasını .env olarak kopyala ve doldur:
env# Binance
BINANCE_API_KEY=api_keyin
BINANCE_API_SECRET=secret_keyin
BINANCE_ENV=prod
BINANCE_WS_URL=wss://fstream.binance.com

# Telegram
TELEGRAM_BOT_TOKEN=tokenin
TELEGRAM_CHAT_ID=-100xxxxxxxxx

# Coinler
SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT
TIMEFRAMES=1h,4h

# Mod — 0 = gerçek sinyal, 1 = test modu
SIMULATION_MODE=0

# Stop / Hedef seviyeleri
DEFAULT_SL_PERCENT=0.02
DEFAULT_TP_PERCENTS=0.02,0.04,0.06,0.10

# Rate limiting (ban koruması)
RATE_LIMITING_ENABLED=1
RATE_LIMIT_SAFETY_MARGIN=0.1
LAZY_LOADING_ENABLED=1
4. Botu Başlat
bashpython main.py

🚂 Railway'e Deploy (7/24 Çalışsın)

Bu repoyu kendi GitHub hesabına Fork'la
railway.app → New Project → GitHub reposunu seç
Variables sekmesine .env içeriğini yapıştır
Deploy et — bot 7/24 çalışır, bilgisayarını kapatabilirsin


🪙 Takip Edilen Coinler
BTCUSDT · ETHUSDT · SOLUSDT · BNBUSDT · XRPUSDT · DOGEUSDT · ADAUSDT · AVAXUSDT

İstediğin coini .env dosyasındaki SYMBOLS satırına ekleyebilirsin.


🛡️ Binance API Güvenliği

⚠️ API key oluştururken sadece Okuma (Read) iznini aç.
İşlem ve Para Çekme izinlerini kesinlikle açma.
Bot sadece veri okur, hesabına hiç dokunmaz.


📁 Proje Yapısı
trading_signal_bot/
├── main.py                 # Ana başlangıç dosyası
├── strategy.py             # Sinyal üretim mantığı (RSI + EMA)
├── strategy_executor.py    # Sinyal işleme ve gönderme
├── telegram_client.py      # Telegram mesaj formatı
├── risk_manager.py         # TP / SL hesaplama
├── binance_ws_client.py    # Binance WebSocket bağlantısı
├── trade_manager.py        # Veri yönetimi
├── .env                    # Konfigürasyon (gizli tut!)
└── requirements.txt        # Bağımlılıklar

⚠️ Sorumluluk Reddi
Bu bot finansal tavsiye vermez. Sinyaller teknik analize dayanır ve her zaman doğru olmayabilir. Kendi araştırmanı yap (DYOR). Oluşabilecek zararlardan geliştirici sorumlu değildir.

MIT License · github.com/rizesky/trading_signal_bot
