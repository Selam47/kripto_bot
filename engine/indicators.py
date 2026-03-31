import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class IndicatorSnapshot:
    ema_9: float
    ema_21: float
    ema_50: float
    ema_200: float
    prev_ema_9: float
    prev_ema_21: float
    rsi: float
    prev_rsi: float
    macd_line: float
    signal_line: float
    prev_macd_line: float
    prev_signal_line: float
    histogram: float
    prev_histogram: float
    atr: float
    atr_sma: float
    bb_upper: float
    bb_middle: float
    bb_lower: float
    stoch_k: float
    stoch_d: float
    prev_stoch_k: float
    prev_stoch_d: float
    vol_ratio: float
    close: float
    prev_close: float


class Indicators:

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def sma(series: pd.Series, period: int) -> pd.Series:
        return series.rolling(window=period, min_periods=period).mean()

    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)
        avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
        ema_fast = Indicators.ema(series, fast)
        ema_slow = Indicators.ema(series, slow)
        macd_line = ema_fast - ema_slow
        signal_line = Indicators.ema(macd_line, signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values
        prev_close = np.roll(close, 1)
        prev_close[0] = close[0]

        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - prev_close),
                np.abs(low - prev_close),
            ),
        )
        tr_series = pd.Series(tr, index=df.index)
        return tr_series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
        middle = Indicators.sma(series, period)
        std = series.rolling(window=period, min_periods=period).std()
        upper = middle + std * std_dev
        lower = middle - std * std_dev
        return upper, middle, lower

    @staticmethod
    def stochastic_rsi(
        series: pd.Series,
        rsi_period: int = 14,
        stoch_period: int = 14,
        smooth_k: int = 3,
        smooth_d: int = 3,
    ) -> tuple[pd.Series, pd.Series]:
        rsi_vals = Indicators.rsi(series, rsi_period)
        rsi_min = rsi_vals.rolling(window=stoch_period).min()
        rsi_max = rsi_vals.rolling(window=stoch_period).max()
        rsi_range = (rsi_max - rsi_min).replace(0, np.nan)
        stoch_rsi = (rsi_vals - rsi_min) / rsi_range
        k = stoch_rsi.rolling(window=smooth_k).mean() * 100.0
        d = k.rolling(window=smooth_d).mean()
        return k, d

    @staticmethod
    def volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
        vol = df["volume"].astype(float)
        avg_vol = vol.rolling(window=period).mean().replace(0, np.nan)
        return vol / avg_vol

    @staticmethod
    def compute_snapshot(df: pd.DataFrame) -> Optional[IndicatorSnapshot]:
        if df is None or len(df) < 30:
            return None

        close = df["close"].astype(float)

        ema_9 = Indicators.ema(close, 9)
        ema_21 = Indicators.ema(close, 21)
        ema_50 = Indicators.ema(close, 50)
        ema_200 = Indicators.ema(close, 200) if len(df) >= 200 else Indicators.ema(close, min(len(df), 100))

        rsi_s = Indicators.rsi(close, 14)
        macd_line, signal_line, histogram = Indicators.macd(close)
        atr_s = Indicators.atr(df, 14)
        atr_sma_s = Indicators.sma(atr_s, 20) if len(df) >= 34 else atr_s
        bb_upper, bb_middle, bb_lower = Indicators.bollinger_bands(close, 20, 2.0)
        stoch_k, stoch_d = Indicators.stochastic_rsi(close)
        vol_ratio = Indicators.volume_ratio(df, 20)

        def safe(s, idx=-1, default=50.0):
            val = s.iloc[idx]
            return float(val) if not pd.isna(val) else default

        return IndicatorSnapshot(
            ema_9=safe(ema_9),
            ema_21=safe(ema_21),
            ema_50=safe(ema_50),
            ema_200=safe(ema_200),
            prev_ema_9=safe(ema_9, -2),
            prev_ema_21=safe(ema_21, -2),
            rsi=safe(rsi_s),
            prev_rsi=safe(rsi_s, -2),
            macd_line=safe(macd_line, default=0.0),
            signal_line=safe(signal_line, default=0.0),
            prev_macd_line=safe(macd_line, -2, 0.0),
            prev_signal_line=safe(signal_line, -2, 0.0),
            histogram=safe(histogram, default=0.0),
            prev_histogram=safe(histogram, -2, 0.0),
            atr=safe(atr_s, default=0.0),
            atr_sma=safe(atr_sma_s, default=0.0),
            bb_upper=safe(bb_upper, default=0.0),
            bb_middle=safe(bb_middle, default=0.0),
            bb_lower=safe(bb_lower, default=0.0),
            stoch_k=safe(stoch_k),
            stoch_d=safe(stoch_d),
            prev_stoch_k=safe(stoch_k, -2),
            prev_stoch_d=safe(stoch_d, -2),
            vol_ratio=safe(vol_ratio, default=1.0),
            close=float(close.iloc[-1]),
            prev_close=float(close.iloc[-2]),
        )

    @staticmethod
    def detect_engulfing(df: pd.DataFrame) -> Optional[str]:
        if len(df) < 3:
            return None

        prev_open = float(df["open"].iloc[-2])
        prev_close = float(df["close"].iloc[-2])
        curr_open = float(df["open"].iloc[-1])
        curr_close = float(df["close"].iloc[-1])

        prev_body = abs(prev_close - prev_open)
        curr_body = abs(curr_close - curr_open)

        if curr_body < prev_body * 0.5:
            return None

        if prev_close < prev_open and curr_close > curr_open:
            if curr_close > prev_open and curr_open <= prev_close:
                return "BULLISH_ENGULFING"

        if prev_close > prev_open and curr_close < curr_open:
            if curr_close < prev_open and curr_open >= prev_close:
                return "BEARISH_ENGULFING"

        return None

    @staticmethod
    def detect_pin_bar(df: pd.DataFrame) -> Optional[str]:
        if len(df) < 2:
            return None

        o = float(df["open"].iloc[-1])
        h = float(df["high"].iloc[-1])
        l = float(df["low"].iloc[-1])
        c = float(df["close"].iloc[-1])

        body = abs(c - o)
        total_range = h - l

        if total_range == 0:
            return None

        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        body_ratio = body / total_range

        if body_ratio > 0.30:
            return None

        if lower_wick >= body * 2.5 and upper_wick < body * 1.0:
            return "HAMMER"

        if upper_wick >= body * 2.5 and lower_wick < body * 1.0:
            return "SHOOTING_STAR"

        return None
