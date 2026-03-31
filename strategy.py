import logging
import pandas as pd
import config


def compute_ma(prices: pd.Series, period: int = 14) -> pd.Series:
    return prices.rolling(window=period).mean()


def compute_ema(prices: pd.Series, period: int = 14) -> pd.Series:
    return prices.ewm(span=period, adjust=False).mean()


def compute_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def compute_volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    return volume.rolling(window=period).mean()


def has_volume_confirmation(df: pd.DataFrame, volume_threshold: float = 1.3) -> bool:
    if len(df) < 20:
        return False
    try:
        current_volume = float(df['volume'].iloc[-1])
        volume_sma = compute_volume_sma(df['volume'])
        avg_volume = float(volume_sma.iloc[-1]) if not volume_sma.empty else 0
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
    except (IndexError, ValueError, ZeroDivisionError):
        return False
    return volume_ratio >= volume_threshold


def is_market_session_active() -> bool:
    from util import now_utc
    current_utc = now_utc()
    current_hour = current_utc.hour
    return 9 <= current_hour <= 21


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    high = df['high']
    low = df['low']
    close = df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.rolling(window=period).mean()
    if atr.empty:
        return 0
    try:
        return float(atr.iloc[-1])
    except (IndexError, ValueError):
        return 0


def calculate_risk_guidance(atr: float, current_price: float, multiplier: int = 2) -> dict:
    if atr <= 0 or current_price <= 0:
        return {
            'stop_loss_distance': 0,
            'stop_loss_percent': 0,
            'recommended_risk_percent': 1.0,
            'volatility_level': 'UNKNOWN',
            'position_guidance': 'Invalid parameters',
            'atr_value': 0
        }
    stop_loss_distance = atr * multiplier
    stop_loss_percent = (stop_loss_distance / current_price) * 100
    if stop_loss_percent > 5:
        recommended_risk = 0.5
        volatility_level = 'HIGH'
        guidance = f"High volatility ({stop_loss_percent:.1f}% SL) - Use 0.5% risk per trade"
    elif stop_loss_percent > 2:
        recommended_risk = 1.0
        volatility_level = 'MEDIUM'
        guidance = f"Medium volatility ({stop_loss_percent:.1f}% SL) - Use 1% risk per trade"
    else:
        recommended_risk = 1.5
        volatility_level = 'LOW'
        guidance = f"Low volatility ({stop_loss_percent:.1f}% SL) - Can use 1.5% risk per trade"
    return {
        'stop_loss_distance': round(stop_loss_distance, 4),
        'stop_loss_percent': round(stop_loss_percent, 2),
        'recommended_risk_percent': recommended_risk,
        'volatility_level': volatility_level,
        'position_guidance': guidance,
        'atr_value': round(atr, 4)
    }


def detect_market_regime(df: pd.DataFrame) -> str:
    if len(df) < 50:
        return 'UNCLEAR'
    returns = df['close'].pct_change().dropna()
    volatility = returns.std() * 100
    high = df['high']
    low = df['low']
    close = df['close']
    dm_plus = high.diff()
    dm_minus = -low.diff()
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    period = 14
    dm_plus_smooth = dm_plus.rolling(period).mean()
    dm_minus_smooth = dm_minus.rolling(period).mean()
    tr_smooth = true_range.rolling(period).mean()
    di_plus = 100 * (dm_plus_smooth / tr_smooth)
    di_minus = 100 * (dm_minus_smooth / tr_smooth)
    dx = 100 * abs(di_plus - di_minus) / (di_plus + di_minus)
    adx = dx.rolling(period).mean()
    try:
        current_volatility = float(volatility) if not pd.isna(volatility) else 0
    except (ValueError, TypeError):
        current_volatility = 0
    try:
        current_adx = float(adx.iloc[-1]) if not adx.empty else 0
    except (IndexError, ValueError):
        current_adx = 0
    if current_volatility > 3.0:
        return 'VOLATILE'
    elif current_adx > 25:
        return 'TRENDING'
    elif current_adx < 15:
        return 'RANGING'
    else:
        return 'UNCLEAR'


def is_signal_appropriate_for_regime(signal: str, market_regime: str) -> bool:
    if market_regime == 'TRENDING':
        return True
    elif market_regime == 'RANGING':
        return False  # Yatay piyasada sinyal atma
    elif market_regime == 'VOLATILE':
        return False  # Çok volatil piyasada atma
    elif market_regime == 'UNCLEAR':
        return False
    return True


def check_signal(df):
    """
    Gelişmiş sinyal tespiti:
    1. EMA 50 > EMA 200 → Yükseliş trendi
    2. RSI 30-60 arası → AL için ideal bölge
    3. Fiyat EMA 50'nin üstüne geçiyor → Giriş
    4. Hacim ortalamanın üstünde → Onay
    5. Market rejimi TRENDING → Filtre
    """

    if len(df) < 60:
        logging.debug(f"Yeterli veri yok: {len(df)} mum")
        return None

    # Aktif seans kontrolü (simulation modda atla)
    if not config.SIMULATION_MODE and not is_market_session_active():
        logging.debug("Sinyal atlandı: Aktif seans dışı")
        return None

    df_work = df.copy()

    # Market rejimi tespiti
    market_regime = detect_market_regime(df_work)
    logging.debug(f"Market rejimi: {market_regime}")

    # Teknik indikatörler
    df_work["RSI"] = compute_rsi(df_work["close"], 14)
    df_work["EMA_50"] = compute_ema(df_work["close"], 50)
    df_work["EMA_200"] = compute_ema(df_work["close"], 200) if len(df_work) >= 200 else compute_ema(df_work["close"], 50)

    df_cleaned = df_work.dropna()
    if len(df_cleaned) < 3:
        return None

    try:
        last_price  = float(df_cleaned["close"].iloc[-1])
        prev_price  = float(df_cleaned["close"].iloc[-2])
        prev2_price = float(df_cleaned["close"].iloc[-3])

        last_rsi   = float(df_cleaned["RSI"].iloc[-1])
        prev_rsi   = float(df_cleaned["RSI"].iloc[-2])

        last_ema50  = float(df_cleaned["EMA_50"].iloc[-1])
        prev_ema50  = float(df_cleaned["EMA_50"].iloc[-2])

        last_ema200 = float(df_cleaned["EMA_200"].iloc[-1])

    except (IndexError, ValueError):
        return None

    # Hacim onayı
    if config.SIMULATION_MODE:
        volume_confirmed = True
    else:
        volume_confirmed = has_volume_confirmation(df_cleaned)

    signal = None

    if config.SIMULATION_MODE:
        # Simulation: Sadece EMA crossover yeterli
        if prev_price < prev_ema50 and last_price > last_ema50:
            signal = "BUY"
        elif prev_price > prev_ema50 and last_price < last_ema50:
            signal = "SELL"
    else:
        # CANLI MOD — Kaliteli sinyal koşulları

        # ── AL (LONG) Koşulları ──────────────────────────
        # 1. Genel trend yukarı (EMA50 > EMA200)
        # 2. Fiyat EMA50'nin üstüne geçiyor (crossover)
        # 3. RSI 35-55 arası (ne aşırı alım ne aşırı satım)
        # 4. RSI yükseliyor (momentum onayı)
        # 5. Hacim onayı
        long_conditions = (
            last_ema50 > last_ema200 and                     # Yükseliş trendi
            prev_price < prev_ema50 and last_price > last_ema50 and  # EMA crossover
            35 <= last_rsi <= 60 and                          # RSI ideal bölge
            last_rsi > prev_rsi and                           # RSI yükseliyor
            volume_confirmed                                   # Hacim onayı
        )

        # ── SAT (SHORT) Koşulları ────────────────────────
        # 1. Genel trend aşağı (EMA50 < EMA200)
        # 2. Fiyat EMA50'nin altına geçiyor (crossover)
        # 3. RSI 40-65 arası
        # 4. RSI düşüyor (momentum onayı)
        # 5. Hacim onayı
        short_conditions = (
            last_ema50 < last_ema200 and                      # Düşüş trendi
            prev_price > prev_ema50 and last_price < last_ema50 and  # EMA crossover
            40 <= last_rsi <= 65 and                          # RSI ideal bölge
            last_rsi < prev_rsi and                           # RSI düşüyor
            volume_confirmed                                   # Hacim onayı
        )

        if long_conditions:
            signal = "BUY"
        elif short_conditions:
            signal = "SELL"

    # Market rejimine uygun mu?
    if signal and not config.SIMULATION_MODE:
        if not is_signal_appropriate_for_regime(signal, market_regime):
            logging.debug(f"Sinyal {signal} atlandı: {market_regime} piyasasına uygun değil")
            return None

    if signal:
        logging.info(
            f"✅ {signal} Sinyali | Fiyat: {last_price:.4f} | "
            f"EMA50: {last_ema50:.4f} | EMA200: {last_ema200:.4f} | "
            f"RSI: {last_rsi:.1f} | Rejim: {market_regime}"
        )

    return signal