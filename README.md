<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Signal Bot</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&family=Syne:wght@400;600;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #0e0e10;
    --bg2: #18181c;
    --bg3: #222228;
    --border: rgba(255,255,255,0.08);
    --border2: rgba(255,255,255,0.14);
    --text: #f0f0f2;
    --text2: #9b9ba8;
    --text3: #5f5f70;
    --green: #3dd68c;
    --green-bg: rgba(61,214,140,0.1);
    --red: #f87171;
    --red-bg: rgba(248,113,113,0.1);
    --blue: #60a5fa;
    --blue-bg: rgba(96,165,250,0.08);
    --amber: #fbbf24;
    --amber-bg: rgba(251,191,36,0.1);
    --accent: #7c6ef5;
  }

  body {
    font-family: 'Syne', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    padding: 2rem 1rem 4rem;
  }

  .wrap { max-width: 700px; margin: 0 auto; }

  /* HERO */
  .hero {
    text-align: center;
    padding: 3rem 1rem 2.5rem;
    border-bottom: 0.5px solid var(--border);
    margin-bottom: 2.5rem;
  }

  .badge {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    background: var(--green-bg);
    color: var(--green);
    font-size: 11px;
    font-weight: 600;
    padding: 5px 14px;
    border-radius: 99px;
    border: 0.5px solid rgba(61,214,140,0.25);
    margin-bottom: 1.25rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: var(--green);
    animation: pulse 2s ease-in-out infinite;
  }

  @keyframes pulse {
    0%,100% { opacity: 1; transform: scale(1); }
    50% { opacity: 0.4; transform: scale(0.8); }
  }

  h1 {
    font-size: 36px;
    font-weight: 700;
    color: var(--text);
    line-height: 1.15;
    margin-bottom: 0.75rem;
    letter-spacing: -0.02em;
  }

  h1 span { color: var(--accent); }

  .hero-sub {
    font-size: 15px;
    color: var(--text2);
    line-height: 1.7;
    max-width: 440px;
    margin: 0 auto;
  }

  /* SECTION */
  .section { margin-bottom: 2.5rem; }

  .sec-title {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.12em;
    color: var(--text3);
    text-transform: uppercase;
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .sec-title::after {
    content: '';
    flex: 1;
    height: 0.5px;
    background: var(--border);
  }

  /* CARDS */
  .cards {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 10px;
  }

  .card {
    background: var(--bg2);
    border: 0.5px solid var(--border);
    border-radius: 14px;
    padding: 1.1rem 1.25rem;
    transition: border-color 0.2s;
  }

  .card:hover { border-color: var(--border2); }

  .card-icon {
    font-size: 20px;
    margin-bottom: 8px;
    display: block;
  }

  .card-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
    margin-bottom: 4px;
  }

  .card-desc {
    font-size: 12px;
    color: var(--text2);
    line-height: 1.6;
  }

  /* SIGNAL PREVIEW */
  .signal-box {
    background: var(--bg2);
    border: 0.5px solid var(--border);
    border-radius: 14px;
    padding: 1.25rem 1.5rem;
    font-family: 'JetBrains Mono', monospace;
    font-size: 13px;
    line-height: 2.1;
  }

  .s-green { color: var(--green); font-weight: 500; }
  .s-red { color: var(--red); font-weight: 500; }
  .s-muted { color: var(--text3); }
  .s-label { color: var(--text2); }
  .s-title { color: var(--text); font-weight: 600; font-size: 14px; }

  .divider {
    border: none;
    border-top: 0.5px solid var(--border);
    margin: 0.5rem 0;
  }

  /* STEPS */
  .steps { display: flex; flex-direction: column; }

  .step {
    display: flex;
    gap: 16px;
    padding: 1rem 0;
    border-bottom: 0.5px solid var(--border);
  }

  .step:last-child { border-bottom: none; }

  .step-num {
    width: 26px; height: 26px;
    border-radius: 50%;
    background: var(--bg3);
    border: 0.5px solid var(--border2);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 11px;
    font-weight: 600;
    color: var(--text2);
    flex-shrink: 0;
    margin-top: 2px;
  }

  .step-label {
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
    margin-bottom: 3px;
  }

  .step-detail {
    font-size: 12px;
    color: var(--text2);
    line-height: 1.6;
  }

  .step-code {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    background: var(--bg3);
    color: var(--blue);
    padding: 2px 7px;
    border-radius: 5px;
    margin-top: 5px;
    display: inline-block;
  }

  /* CODE BLOCK */
  .code-block {
    background: var(--bg2);
    border: 0.5px solid var(--border);
    border-radius: 12px;
    padding: 1rem 1.25rem;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    line-height: 2;
    overflow-x: auto;
  }

  .k { color: var(--accent); }
  .v { color: var(--green); }
  .c { color: var(--text3); font-style: italic; }

  /* TAGS */
  .tag-row { display: flex; flex-wrap: wrap; gap: 7px; }

  .tag {
    background: var(--bg2);
    border: 0.5px solid var(--border2);
    border-radius: 99px;
    font-size: 12px;
    font-family: 'JetBrains Mono', monospace;
    color: var(--text2);
    padding: 4px 12px;
    transition: border-color 0.2s, color 0.2s;
  }

  .tag:hover { border-color: var(--accent); color: var(--text); }

  /* WARNING */
  .warn {
    background: var(--amber-bg);
    border: 0.5px solid rgba(251,191,36,0.2);
    border-radius: 12px;
    padding: 0.9rem 1.1rem;
    font-size: 13px;
    color: var(--amber);
    line-height: 1.7;
  }

  .warn strong { font-weight: 600; }

  /* FOOTER */
  .footer {
    text-align: center;
    padding-top: 2rem;
    margin-top: 1rem;
    border-top: 0.5px solid var(--border);
    font-size: 12px;
    color: var(--text3);
    line-height: 2;
  }

  .footer a { color: var(--blue); text-decoration: none; }
  .footer a:hover { text-decoration: underline; }

  /* STATS ROW */
  .stats {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
    margin-top: 1.5rem;
  }

  .stat {
    background: var(--bg2);
    border: 0.5px solid var(--border);
    border-radius: 12px;
    padding: 0.9rem 1rem;
    text-align: center;
  }

  .stat-val {
    font-size: 20px;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 3px;
  }

  .stat-label { font-size: 11px; color: var(--text3); }

  @media (max-width: 480px) {
    .cards { grid-template-columns: 1fr; }
    .stats { grid-template-columns: repeat(2, 1fr); }
    h1 { font-size: 28px; }
  }
</style>
</head>
<body>
<div class="wrap">

  <!-- HERO -->
  <div class="hero">
    <div class="badge"><span class="dot"></span> Aktif & Çalışıyor</div>
    <h1>Trading<br><span>Signal Bot</span></h1>
    <p class="hero-sub">Binance Futures verilerini analiz ederek Telegram grubuna otomatik AL / SAT sinyali atan açık kaynaklı bot</p>

    <div class="stats">
      <div class="stat">
        <div class="stat-val">8</div>
        <div class="stat-label">Takip edilen coin</div>
      </div>
      <div class="stat">
        <div class="stat-val">7/24</div>
        <div class="stat-label">Kesintisiz çalışır</div>
      </div>
      <div class="stat">
        <div class="stat-val">%0</div>
        <div class="stat-label">İşlem açmaz</div>
      </div>
    </div>
  </div>

  <!-- NE YAPAR -->
  <div class="section">
    <div class="sec-title">Ne yapar</div>
    <div class="cards">
      <div class="card">
        <span class="card-icon">📡</span>
        <div class="card-title">Canlı Veri</div>
        <div class="card-desc">Binance WebSocket üzerinden gerçek zamanlı fiyat verisi çeker</div>
      </div>
      <div class="card">
        <span class="card-icon">📊</span>
        <div class="card-title">Teknik Analiz</div>
        <div class="card-desc">RSI, EMA50, EMA200 ve hacim indikatörleriyle sinyal üretir</div>
      </div>
      <div class="card">
        <span class="card-icon">🎯</span>
        <div class="card-title">TP / SL Hesabı</div>
        <div class="card-desc">Giriş, hedef ve stop fiyatlarını otomatik olarak hesaplar</div>
      </div>
      <div class="card">
        <span class="card-icon">💬</span>
        <div class="card-title">Telegram</div>
        <div class="card-desc">Sinyal oluşunca grubuna veya kanalına direkt mesaj atar</div>
      </div>
    </div>
  </div>

  <!-- SİNYAL ÖRNEĞİ -->
  <div class="section">
    <div class="sec-title">Sinyal örneği</div>
    <div class="signal-box">
      <span class="s-green">🚀 BTCUSDT</span>&nbsp;&nbsp;<span class="s-muted">| 🕐 Orta Vadeli</span><br>
      <hr class="divider">
      <span class="s-title">📌 AL (LONG)</span><br><br>
      <span class="s-label">💰 Giriş: </span><span class="s-title">83,420.50</span><br>
      <span class="s-label">🎯 TP1 &nbsp;&nbsp;: </span><span class="s-green">85,089.10</span><span class="s-muted"> (+2.0%)</span><br>
      <span class="s-label">🎯 TP2 &nbsp;&nbsp;: </span><span class="s-green">86,757.70</span><span class="s-muted"> (+4.0%)</span><br>
      <span class="s-label">🎯 TP3 &nbsp;&nbsp;: </span><span class="s-green">88,426.30</span><span class="s-muted"> (+6.0%)</span><br>
      <span class="s-label">❌ Stop &nbsp;: </span><span class="s-red">81,751.69</span><span class="s-muted"> (-2.0%)</span><br>
      <span class="s-label">⚖️ R/Ö &nbsp;&nbsp;: </span><span>1:1.5</span>
      <hr class="divider">
      <span class="s-muted">🔧 20x | ISOLATED &nbsp;·&nbsp; ⚠️ Finansal tavsiye değildir</span>
    </div>
  </div>

  <!-- KURULUM -->
  <div class="section">
    <div class="sec-title">Kurulum — 5 adım</div>
    <div class="steps">
      <div class="step">
        <div class="step-num">1</div>
        <div>
          <div class="step-label">Projeyi indir</div>
          <div class="step-detail">github.com/rizesky/trading_signal_bot → Code → Download ZIP → Masaüstüne çıkart</div>
        </div>
      </div>
      <div class="step">
        <div class="step-num">2</div>
        <div>
          <div class="step-label">Kütüphaneleri kur</div>
          <div class="step-detail">Klasörde CMD aç ve sırayla çalıştır:</div>
          <div class="step-code">pip install -r requirements.txt</div><br>
          <div class="step-code">python -m playwright install chromium</div>
        </div>
      </div>
      <div class="step">
        <div class="step-num">3</div>
        <div>
          <div class="step-label">API bilgilerini gir</div>
          <div class="step-detail">example.env dosyasını .env olarak yeniden adlandır → Binance API key, API Secret ve Telegram bot token'ını doldur → Telegram grup chat ID'sini ekle</div>
        </div>
      </div>
      <div class="step">
        <div class="step-num">4</div>
        <div>
          <div class="step-label">Botu başlat</div>
          <div class="step-detail">CMD'de şunu yaz:</div>
          <div class="step-code">python main.py</div>
        </div>
      </div>
      <div class="step">
        <div class="step-num">5</div>
        <div>
          <div class="step-label">Railway'e al — isteğe bağlı</div>
          <div class="step-detail">7/24 çalışsın istiyorsan → railway.app → GitHub reposunu bağla → Variables sekmesine .env içeriğini yapıştır → Deploy et</div>
        </div>
      </div>
    </div>
  </div>

  <!-- ENV AYARLARI -->
  <div class="section">
    <div class="sec-title">Önemli .env ayarları</div>
    <div class="code-block">
<span class="c"># Binance bağlantısı</span>
<span class="k">BINANCE_API_KEY</span>=<span class="v">api_keyin</span>
<span class="k">BINANCE_API_SECRET</span>=<span class="v">secret_keyin</span>
<span class="k">BINANCE_WS_URL</span>=<span class="v">wss://fstream.binance.com</span>

<span class="c"># Telegram</span>
<span class="k">TELEGRAM_BOT_TOKEN</span>=<span class="v">tokenin</span>
<span class="k">TELEGRAM_CHAT_ID</span>=<span class="v">-100xxxxxxxxx</span>

<span class="c"># Takip edilecek coinler</span>
<span class="k">SYMBOLS</span>=<span class="v">BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT</span>
<span class="k">TIMEFRAMES</span>=<span class="v">1h,4h</span>

<span class="c"># İşlem AÇMAZ — sadece sinyal atar</span>
<span class="k">SIMULATION_MODE</span>=<span class="v">0</span>

<span class="c"># Stop ve hedef seviyeleri</span>
<span class="k">DEFAULT_SL_PERCENT</span>=<span class="v">0.02</span>
<span class="k">DEFAULT_TP_PERCENTS</span>=<span class="v">0.02,0.04,0.06,0.10</span>
    </div>
  </div>

  <!-- TAKİP EDİLEN COİNLER -->
  <div class="section">
    <div class="sec-title">Takip edilen coinler</div>
    <div class="tag-row">
      <span class="tag">BTCUSDT</span>
      <span class="tag">ETHUSDT</span>
      <span class="tag">SOLUSDT</span>
      <span class="tag">BNBUSDT</span>
      <span class="tag">XRPUSDT</span>
      <span class="tag">DOGEUSDT</span>
      <span class="tag">ADAUSDT</span>
      <span class="tag">AVAXUSDT</span>
    </div>
  </div>

  <!-- GÜVENLİK -->
  <div class="section">
    <div class="sec-title">Binance API güvenliği</div>
    <div class="warn">
      ⚠️ &nbsp;API key oluştururken sadece <strong>Okuma (Read)</strong> iznini aç. İşlem ve Para Çekme izinlerini kesinlikle açma. Bot sadece veri okur, hesabına hiç dokunmaz.
    </div>
  </div>

  <!-- FOOTER -->
  <div class="footer">
    <a href="https://github.com/rizesky/trading_signal_bot">github.com/rizesky/trading_signal_bot</a><br>
    Bu bir finansal tavsiye değildir · Kendi araştırmanı yap · DYOR
  </div>

</div>
</body>
</html>
