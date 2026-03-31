"""
Teknik Analiz Sinyal Motoru — Confluence (Birleşim) Tabanlı
============================================================
Sinyal üretmek için birden fazla indikatörün aynı anda onay vermesi gerekir.
Tek başına hiçbir indikatör sinyal üretmez → düşük kaliteli sinyaller engellenir.

Kullanılan İndikatörler:
  1. EMA Crossover (9/21) + Trend Filtresi (50/200 EMA)
  2. RSI (14) — Aşırı alım/satım + momentum yönü
  3. MACD — Histogram yönü + sinyal kesişimi
  4. Volume — Ortalama üstü hacim onayı
  5. ATR — Volatilite bazlı dinamik TP/SL
  6. Bollinger Bands — Fiyat pozisyonu ve squeeze tespiti
  7. Stochastic RSI — Ek momentum onayı
  8. Yapı Analizi — Son swing high/low kırılımı

Minimum 4/7 onay → sinyal üretilir (confluent skoru)
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────
# Veri Yapıları
# ─────────────────────────────────────────────

@dataclass
class SignalResult:
    direction: str  # "BUY" veya "SELL"
    confidence: float  # 0.0 - 1.0
    confluences: list = field(default_factory=list)
    entry_price: float = 0.0
    atr_value: float = 0.0
    trend_strength: str = "NEUTRAL"  # STRONG_BULL, BULL, NEUTRAL, BEAR, STRONG_BEAR

    def __str__(self):
        return self.direction

    def __bool__(self):
        return self.direction in ("BUY", "SELL")


@dataclass
class TradeLevels:
    entry_prices: list
    tp_list: list
    sl: float
    risk_reward_ratio: float
    atr: float
    position_bias: str  # "AGGRESSIVE", "NORMAL", "CONSERVATIVE"


# ─────────────────────────────────────────────
# İndikatör Hesaplamaları
# ─────────────────────────────────────────────

def calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def calc_sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calc_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = calc_ema(series, fast)
    ema_slow = calc_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calc_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df['high']
    low = df['low']
    close = df['close']
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def calc_bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0):
    middle = calc_sma(series, period)
    std = series.rolling(window=period).std()
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    return upper, middle, lower


def calc_stochastic_rsi(series: pd.Series, rsi_period: int = 14, stoch_period: int = 14,
                         smooth_k: int = 3, smooth_d: int = 3):
    rsi = calc_rsi(series, rsi_period)
    rsi_min = rsi.rolling(window=stoch_period).min()
    rsi_max = rsi.rolling(window=stoch_period).max()
    stoch_rsi = (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, np.nan)
    k = stoch_rsi.rolling(window=smooth_k).mean() * 100
    d = k.rolling(window=smooth_d).mean()
    return k, d


def calc_volume_profile(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Hacmin ortalamasına göre oranını döndürür (1.0 = ortalama)."""
    vol = df['volume'].astype(float)
    avg_vol = vol.rolling(window=period).mean()
    return vol / avg_vol.replace(0, np.nan)


def find_swing_levels(df: pd.DataFrame, lookback: int = 10):
    """Son N mum içindeki swing high ve swing low'ları bulur."""
    highs = df['high'].rolling(window=lookback, center=False).max()
    lows = df['low'].rolling(window=lookback, center=False).min()
    return highs, lows


def detect_engulfing(df: pd.DataFrame) -> Optional[str]:
    """Son 2 mumda engulfing pattern var mı?"""
    if len(df) < 3:
        return None

    prev_open = float(df['open'].iloc[-2])
    prev_close = float(df['close'].iloc[-2])
    curr_open = float(df['open'].iloc[-1])
    curr_close = float(df['close'].iloc[-1])

    prev_body = abs(prev_close - prev_open)
    curr_body = abs(curr_close - curr_open)

    if curr_body < prev_body * 0.5:
        return None

    # Bullish Engulfing
    if prev_close < prev_open and curr_close > curr_open:
        if curr_close > prev_open and curr_open <= prev_close:
            return "BULLISH_ENGULFING"

    # Bearish Engulfing
    if prev_close > prev_open and curr_close < curr_open:
        if curr_close < prev_open and curr_open >= prev_close:
            return "BEARISH_ENGULFING"

    return None


def detect_pin_bar(df: pd.DataFrame) -> Optional[str]:
    """Son mumda pin bar (hammer/shooting star) var mı?"""
    if len(df) < 2:
        return None

    o = float(df['open'].iloc[-1])
    h = float(df['high'].iloc[-1])
    l = float(df['low'].iloc[-1])
    c = float(df['close'].iloc[-1])

    body = abs(c - o)
    total_range = h - l

    if total_range == 0:
        return None

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    body_ratio = body / total_range

    # Body küçük olmalı (toplam range'in %30'undan az)
    if body_ratio > 0.30:
        return None

    # Hammer: Alt fitil body'nin en az 2 katı
    if lower_wick >= body * 2.5 and upper_wick < body * 1.0:
        return "HAMMER"

    # Shooting Star: Üst fitil body'nin en az 2 katı
    if upper_wick >= body * 2.5 and lower_wick < body * 1.0:
        return "SHOOTING_STAR"

    return None


# ─────────────────────────────────────────────
# Trend Gücü Analizi
# ─────────────────────────────────────────────

def assess_trend_strength(close: pd.Series, ema_9, ema_21, ema_50, ema_200) -> str:
    """EMA diziliminden trend gücünü belirler."""
    c = float(close.iloc[-1])
    e9 = float(ema_9.iloc[-1])
    e21 = float(ema_21.iloc[-1])
    e50 = float(ema_50.iloc[-1])
    e200 = float(ema_200.iloc[-1])

    # Tam bullish dizilim: Fiyat > 9 > 21 > 50 > 200
    if c > e9 > e21 > e50 > e200:
        return "STRONG_BULL"
    # Bullish ama tam dizilim yok
    elif c > e50 and e9 > e21:
        return "BULL"
    # Tam bearish dizilim
    elif c < e9 < e21 < e50 < e200:
        return "STRONG_BEAR"
    # Bearish ama tam dizilim yok
    elif c < e50 and e9 < e21:
        return "BEAR"
    else:
        return "NEUTRAL"


# ─────────────────────────────────────────────
# Ana Sinyal Fonksiyonu
# ─────────────────────────────────────────────

# Dışarıdan çağrılan ana fonksiyon — StrategyExecutor bunu kullanır
def check_signal(df: pd.DataFrame) -> Optional[SignalResult]:
    """
    DataFrame alır, confluence bazlı analiz yapar.
    Yeterli onay varsa SignalResult döner, yoksa None.

    Minimum gerekli veri: 50 mum (200 EMA için ideal: 200+)
    """
    if df is None or len(df) < 30:
        return None

    try:
        return _analyze_confluence(df)
    except Exception as e:
        logging.error(f"Signal analysis error: {e}")
        return None


def _analyze_confluence(df: pd.DataFrame) -> Optional[SignalResult]:
    close = df['close'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)

    # ── İndikatörleri hesapla ──
    ema_9 = calc_ema(close, 9)
    ema_21 = calc_ema(close, 21)
    ema_50 = calc_ema(close, 50)
    # 200 EMA: yeterli veri yoksa 50 EMA'yı kullan
    ema_200 = calc_ema(close, 200) if len(df) >= 200 else calc_ema(close, min(len(df), 100))

    rsi = calc_rsi(close, 14)
    macd_line, signal_line, histogram = calc_macd(close)
    atr = calc_atr(df, 14)
    bb_upper, bb_middle, bb_lower = calc_bollinger_bands(close, 20, 2.0)
    stoch_k, stoch_d = calc_stochastic_rsi(close)
    vol_ratio = calc_volume_profile(df, 20)

    # ── Son değerleri al ──
    last_close = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    last_rsi = float(rsi.iloc[-1])
    prev_rsi = float(rsi.iloc[-2])
    last_hist = float(histogram.iloc[-1])
    prev_hist = float(histogram.iloc[-2])
    last_macd = float(macd_line.iloc[-1])
    last_signal = float(signal_line.iloc[-1])
    prev_macd = float(macd_line.iloc[-2])
    prev_signal = float(signal_line.iloc[-2])
    last_atr = float(atr.iloc[-1])
    last_bb_upper = float(bb_upper.iloc[-1])
    last_bb_lower = float(bb_lower.iloc[-1])
    last_bb_middle = float(bb_middle.iloc[-1])
    last_stoch_k = float(stoch_k.iloc[-1]) if not pd.isna(stoch_k.iloc[-1]) else 50
    last_stoch_d = float(stoch_d.iloc[-1]) if not pd.isna(stoch_d.iloc[-1]) else 50
    prev_stoch_k = float(stoch_k.iloc[-2]) if not pd.isna(stoch_k.iloc[-2]) else 50
    prev_stoch_d = float(stoch_d.iloc[-2]) if not pd.isna(stoch_d.iloc[-2]) else 50
    last_vol_ratio = float(vol_ratio.iloc[-1]) if not pd.isna(vol_ratio.iloc[-1]) else 1.0
    last_ema_9 = float(ema_9.iloc[-1])
    last_ema_21 = float(ema_21.iloc[-1])
    prev_ema_9 = float(ema_9.iloc[-2])
    prev_ema_21 = float(ema_21.iloc[-2])

    trend = assess_trend_strength(close, ema_9, ema_21, ema_50, ema_200)
    candle_pattern = detect_engulfing(df)
    pin_bar = detect_pin_bar(df)

    # ── ATR bazlı volatilite filtresi ──
    # ATR çok düşükse (sıkışma) sinyal üretme — false breakout riski yüksek
    atr_sma = float(calc_sma(atr, 20).iloc[-1]) if len(df) >= 34 else last_atr
    if last_atr < atr_sma * 0.4:
        return None  # Çok dar range, sıkışma — bekle

    # ═══════════════════════════════════════════
    # BUY (LONG) Confluence Kontrolü
    # ═══════════════════════════════════════════
    buy_confluences = []

    # 1) EMA Crossover: 9 EMA, 21 EMA'yı yukarı kesiyor VEYA üzerinde
    if last_ema_9 > last_ema_21 and prev_ema_9 <= prev_ema_21:
        buy_confluences.append("EMA_9/21_GOLDEN_CROSS")
    elif last_ema_9 > last_ema_21 and last_close > last_ema_9:
        buy_confluences.append("EMA_BULLISH_ALIGNMENT")

    # 2) RSI: Oversold'dan çıkış (30-50 arası) veya 50 üstü momentum
    if 30 <= last_rsi <= 55 and last_rsi > prev_rsi and prev_rsi < 40:
        buy_confluences.append("RSI_OVERSOLD_RECOVERY")
    elif 50 < last_rsi < 70 and last_rsi > prev_rsi:
        buy_confluences.append("RSI_BULLISH_MOMENTUM")

    # 3) MACD: Histogram pozitife dönüyor veya sinyal kesişimi
    if last_hist > 0 and prev_hist <= 0:
        buy_confluences.append("MACD_HISTOGRAM_FLIP")
    elif last_macd > last_signal and prev_macd <= prev_signal:
        buy_confluences.append("MACD_BULLISH_CROSS")
    elif last_hist > prev_hist and last_hist > 0:
        buy_confluences.append("MACD_INCREASING_MOMENTUM")

    # 4) Hacim: Ortalamanın 1.2 katından fazla
    if last_vol_ratio > 1.2:
        buy_confluences.append("VOLUME_ABOVE_AVERAGE")

    # 5) Bollinger: Alt banda yakın veya orta bandın üstüne geçiş
    bb_range = last_bb_upper - last_bb_lower
    if bb_range > 0:
        bb_position = (last_close - last_bb_lower) / bb_range
        if bb_position < 0.25:
            buy_confluences.append("BB_NEAR_LOWER_BAND")
        elif prev_close < last_bb_middle and last_close > last_bb_middle:
            buy_confluences.append("BB_MIDDLE_BREAKOUT")

    # 6) Stochastic RSI: Oversold bölgesinden yukarı çaprazlama
    if last_stoch_k < 30 and last_stoch_k > last_stoch_d and prev_stoch_k <= prev_stoch_d:
        buy_confluences.append("STOCH_RSI_BULLISH_CROSS")
    elif last_stoch_k < 40 and last_stoch_k > prev_stoch_k:
        buy_confluences.append("STOCH_RSI_RECOVERY")

    # 7) Candlestick Pattern
    if candle_pattern == "BULLISH_ENGULFING":
        buy_confluences.append("BULLISH_ENGULFING")
    if pin_bar == "HAMMER":
        buy_confluences.append("HAMMER")

    # ═══════════════════════════════════════════
    # SELL (SHORT) Confluence Kontrolü
    # ═══════════════════════════════════════════
    sell_confluences = []

    # 1) EMA Crossover: 9 EMA, 21 EMA'yı aşağı kesiyor VEYA altında
    if last_ema_9 < last_ema_21 and prev_ema_9 >= prev_ema_21:
        sell_confluences.append("EMA_9/21_DEATH_CROSS")
    elif last_ema_9 < last_ema_21 and last_close < last_ema_9:
        sell_confluences.append("EMA_BEARISH_ALIGNMENT")

    # 2) RSI: Overbought'tan düşüş (50-70 arası) veya 50 altı momentum
    if 45 <= last_rsi <= 70 and last_rsi < prev_rsi and prev_rsi > 60:
        sell_confluences.append("RSI_OVERBOUGHT_REJECTION")
    elif 30 < last_rsi < 50 and last_rsi < prev_rsi:
        sell_confluences.append("RSI_BEARISH_MOMENTUM")

    # 3) MACD: Histogram negatife dönüyor veya sinyal kesişimi
    if last_hist < 0 and prev_hist >= 0:
        sell_confluences.append("MACD_HISTOGRAM_FLIP")
    elif last_macd < last_signal and prev_macd >= prev_signal:
        sell_confluences.append("MACD_BEARISH_CROSS")
    elif last_hist < prev_hist and last_hist < 0:
        sell_confluences.append("MACD_INCREASING_SELL_MOMENTUM")

    # 4) Hacim
    if last_vol_ratio > 1.2:
        sell_confluences.append("VOLUME_ABOVE_AVERAGE")

    # 5) Bollinger: Üst banda yakın veya orta bandın altına düşüş
    if bb_range > 0:
        bb_position = (last_close - last_bb_lower) / bb_range
        if bb_position > 0.75:
            sell_confluences.append("BB_NEAR_UPPER_BAND")
        elif prev_close > last_bb_middle and last_close < last_bb_middle:
            sell_confluences.append("BB_MIDDLE_BREAKDOWN")

    # 6) Stochastic RSI: Overbought bölgesinden aşağı çaprazlama
    if last_stoch_k > 70 and last_stoch_k < last_stoch_d and prev_stoch_k >= prev_stoch_d:
        sell_confluences.append("STOCH_RSI_BEARISH_CROSS")
    elif last_stoch_k > 60 and last_stoch_k < prev_stoch_k:
        sell_confluences.append("STOCH_RSI_REJECTION")

    # 7) Candlestick Pattern
    if candle_pattern == "BEARISH_ENGULFING":
        sell_confluences.append("BEARISH_ENGULFING")
    if pin_bar == "SHOOTING_STAR":
        sell_confluences.append("SHOOTING_STAR")

    # ═══════════════════════════════════════════
    # Karar Mekanizması
    # ═══════════════════════════════════════════

    MIN_CONFLUENCES = 4  # Minimum onay sayısı

    buy_score = len(buy_confluences)
    sell_score = len(sell_confluences)

    # İki yön de güçlüyse → kararsız piyasa, sinyal üretme
    if buy_score >= MIN_CONFLUENCES and sell_score >= MIN_CONFLUENCES:
        logging.debug(f"Conflicting signals: BUY({buy_score}) vs SELL({sell_score}) — skipping")
        return None

    # ── Trend filtresi: Trend yönüne karşı sinyal üretme (opsiyonel ama önemli) ──
    # Güçlü trend varsa sadece trend yönünde sinyal kabul et
    if trend == "STRONG_BULL" and sell_score >= MIN_CONFLUENCES and buy_score < 2:
        logging.debug(f"Trend filter: STRONG_BULL trend, blocking SELL signal")
        return None
    if trend == "STRONG_BEAR" and buy_score >= MIN_CONFLUENCES and sell_score < 2:
        logging.debug(f"Trend filter: STRONG_BEAR trend, blocking BUY signal")
        return None

    # ── BUY sinyali ──
    if buy_score >= MIN_CONFLUENCES and buy_score > sell_score:
        confidence = min(buy_score / 8.0, 1.0)

        # Trend uyumu bonusu
        if trend in ("STRONG_BULL", "BULL"):
            confidence = min(confidence + 0.1, 1.0)

        return SignalResult(
            direction="BUY",
            confidence=confidence,
            confluences=buy_confluences,
            entry_price=last_close,
            atr_value=last_atr,
            trend_strength=trend
        )

    # ── SELL sinyali ──
    if sell_score >= MIN_CONFLUENCES and sell_score > buy_score:
        confidence = min(sell_score / 8.0, 1.0)

        if trend in ("STRONG_BEAR", "BEAR"):
            confidence = min(confidence + 0.1, 1.0)

        return SignalResult(
            direction="SELL",
            confidence=confidence,
            confluences=sell_confluences,
            entry_price=last_close,
            atr_value=last_atr,
            trend_strength=trend
        )

    return None


# ─────────────────────────────────────────────
# ATR Bazlı Dinamik TP/SL Hesaplama
# ─────────────────────────────────────────────

def calculate_atr_based_levels(signal: SignalResult, df: pd.DataFrame) -> Optional[TradeLevels]:
    """
    ATR bazlı dinamik TP ve SL seviyeleri hesaplar.
    Sabit yüzdeler yerine piyasa volatilitesine göre adapte olur.

    Risk/Reward: Minimum 1:1.5 (SL:TP1), 1:2.5 (TP2), 1:4 (TP3)
    """
    if not signal or not signal.atr_value:
        return None

    atr = signal.atr_value
    entry = signal.entry_price
    confidence = signal.confidence

    # Confidence'a göre SL mesafesini ayarla
    # Yüksek confidence → daha dar SL (daha agresif)
    # Düşük confidence → daha geniş SL (daha konservatif)
    if confidence >= 0.75:
        sl_multiplier = 1.2
        position_bias = "AGGRESSIVE"
    elif confidence >= 0.55:
        sl_multiplier = 1.5
        position_bias = "NORMAL"
    else:
        sl_multiplier = 2.0
        position_bias = "CONSERVATIVE"

    sl_distance = atr * sl_multiplier

    # TP seviyeleri: kademeli R:R oranları
    tp1_distance = atr * 2.0   # ~1:1.5 R:R
    tp2_distance = atr * 3.5   # ~1:2.5 R:R
    tp3_distance = atr * 5.5   # ~1:4 R:R

    if signal.direction == "BUY":
        sl = entry - sl_distance
        tp_list = [
            round(entry + tp1_distance, 8),
            round(entry + tp2_distance, 8),
            round(entry + tp3_distance, 8),
        ]
    elif signal.direction == "SELL":
        sl = entry + sl_distance
        tp_list = [
            round(entry - tp1_distance, 8),
            round(entry - tp2_distance, 8),
            round(entry - tp3_distance, 8),
        ]
    else:
        return None

    # Swing high/low bazlı SL doğrulama
    # SL'nin mantıksal bir yapı seviyesinin arkasında olmasını sağla
    try:
        swing_highs, swing_lows = find_swing_levels(df, lookback=10)
        last_swing_high = float(swing_highs.iloc[-1])
        last_swing_low = float(swing_lows.iloc[-1])

        if signal.direction == "BUY":
            # SL, son swing low'un biraz altında olmalı
            structure_sl = last_swing_low - (atr * 0.3)
            if structure_sl < sl:
                sl = structure_sl  # Yapısal SL daha güvenli
        elif signal.direction == "SELL":
            structure_sl = last_swing_high + (atr * 0.3)
            if structure_sl > sl:
                sl = structure_sl
    except Exception:
        pass  # Swing hesaplanamıyorsa ATR bazlı SL'yi kullan

    sl = round(sl, 8)
    rr_ratio = tp1_distance / sl_distance if sl_distance > 0 else 0

    # Risk/Reward çok düşükse sinyal atla
    if rr_ratio < 1.3:
        logging.debug(f"R:R too low ({rr_ratio:.2f}), skipping signal")
        return None

    return TradeLevels(
        entry_prices=[round(entry, 8)],
        tp_list=tp_list,
        sl=sl,
        risk_reward_ratio=round(rr_ratio, 2),
        atr=round(atr, 8),
        position_bias=position_bias
    )