Kripto piyasalarında otomatik teknik analiz yapıp Telegram üzerinden alım/satım sinyali gönderen bir bot. Binance Futures çiftlerini analiz eder.
Sinyal Formatı

[SIMULATION] veya [LIVE]
🚀 {SEMBOL} | {Vade: Kisa/Orta/Uzun Vadeli}
------------------------------
SINYAL: AL (LONG) veya SAT (SHORT)
Giris: {Fiyat}
  TP1: {Fiyat}  (+%X)
  TP2: {Fiyat}  (+%X)
  TP3: {Fiyat}  (+%X)
Stop Loss: {Fiyat}  (%X)
Risk/Odul: 1:{Oran}
------------------------------
Kaldirac: {X}x | ISOLATED veya CROSS
Teknik Analiz Detayi:
  Guven: {0-100}% — {Dusuk/Orta/Yuksek} Kalite
  Trend: {Yukselis/Dusus/Yatay}
  Ust TF Trend: {Yukselis/Dusus/Yatay/INSUFFICIENT_DATA}
  Onaylar ({N}):
    - {İndikatör listesi}
  ATR: {Değer}
Bu format sabittir. Emoji, çizgi, hizalama değiştirilmez.
Teknik İndikatörler
İndikatörBullish OnayBearish OnayEMA (Kesişim)EMA BullishEMA BearishRSIRSI YukselisRSI DususMACDMACD DonusMACD KirilimBollinger BandsBB Orta Kirilim / BB Alt BantBB Ust BantHacimHacim OnayiHacim OnayiMum FormasyonuYutan Boga, Cekic, Sabah YildiziYutan Ayi, Kayan Yildiz, Aksam Yildizi
Kurallar: Min 3 onay gerekli. Güven skoru: 0-40% düşük, 41-69% orta, 70-100% yüksek. Üst TF ters yöndeyse güven %15 düşer.
Risk Yönetimi
* SL: ATR bazlı hesaplanır, maks %10 mesafe.
* TP: 3 kademeli (TP1 konservatif → TP3 agresif), ATR çarpanlarıyla.
* R/R: TP1 mesafesi / SL mesafesi. İdeal ≥ 1:1.5.
* Kaldıraç: Güven 70%+ → maks 25x, 50-69% → maks 15x, 30-49% → maks 10x, <30% → sinyal üretilmez.
* Margin: Varsayılan ISOLATED.
Kodlama Kuralları
* Mesajlarda ASCII Türkçe kullanılır (ö→o, ü→u, ş→s, ç→c, ğ→g, ı→i). Örn: "Yukselis" yazılır, "Yükseliş" yazılmaz.
* Ondalık hassasiyet sembolün fiyat aralığına göre dinamik ayarlanır (BTC→2, düşük fiyatlı coin→4-5).
* Yüzde hesabı: ((TP - Giris) / Giris) × 100 (LONG), ((Giris - TP) / Giris) × 100 (SHORT).
* Tüm zamanlar UTC.
* Aynı sembol + aynı yönde aktif sinyal varken yeni sinyal üretilmez.