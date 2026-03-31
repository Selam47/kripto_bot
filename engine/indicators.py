"""
Vectorized Technical Indicator Library
========================================
All calculations operate on full pandas Series / numpy arrays — no Python loops.
A single call to ``Indicators.compute_snapshot()`` runs every indicator in one
pass and returns an immutable ``IndicatorSnapshot`` dataclass, ready for the
Confluence Engine.

Indicators included
-------------------
- EMA (9, 21, 50, 200)
- RSI (14-period Wilder smoothing)
- MACD (12/26/9)
- ATR (14-period Wilder smoothing, numpy-vectorized)
- Bollinger Bands (20-period SMA ± 2σ)
- Stochastic RSI (14/14/3/3)
- Volume Ratio (current / 20-period SMA)
- Candlestick pattern detection: Engulfing, Pin Bar
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class IndicatorSnapshot:
    """
    Immutable snapshot of the latest bar's indicator values.

    All values correspond to the **closed** bar at ``df.iloc[-1]``,
    with ``prev_*`` fields referring to ``df.iloc[-2]``.
    """

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
    """
    Stateless factory of vectorized technical indicators.

    Every method accepts a ``pd.Series`` or ``pd.DataFrame`` and returns
    a ``pd.Series`` (or tuple of Series) of the same length, suitable for
    direct iloc indexing.
    """

    @staticmethod
    def ema(series: pd.Series, period: int) -> pd.Series:
        """
        Exponential Moving Average using pandas' built-in EWM.

        Args:
            series: Close price series.
            period: Span (equivalent to the classic EMA period).

        Returns:
            pd.Series aligned with the input index.
        """
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def sma(series: pd.Series, period: int) -> pd.Series:
        """
        Simple Moving Average.

        Args:
            series: Any numeric series.
            period: Rolling window size.

        Returns:
            pd.Series with NaN for the first ``period - 1`` bars.
        """
        return series.rolling(window=period, min_periods=period).mean()

    @staticmethod
    def rsi(series: pd.Series, period: int = 14) -> pd.Series:
        """
        Relative Strength Index using Wilder's smoothing (EWM alpha = 1/period).

        Args:
            series: Close price series.
            period: Look-back window (default 14).

        Returns:
            pd.Series of RSI values in [0, 100].
        """
        delta = series.diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)
        alpha = 1.0 / period
        avg_gain = gain.ewm(alpha=alpha, min_periods=period).mean()
        avg_loss = loss.ewm(alpha=alpha, min_periods=period).mean()
        rs = avg_gain / avg_loss.replace(0.0, np.nan)
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def macd(
        series: pd.Series,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """
        MACD indicator: MACD line, signal line, and histogram.

        Args:
            series: Close price series.
            fast:   Fast EMA period (default 12).
            slow:   Slow EMA period (default 26).
            signal: Signal line EMA period (default 9).

        Returns:
            Tuple of (macd_line, signal_line, histogram).
        """
        ema_fast = Indicators.ema(series, fast)
        ema_slow = Indicators.ema(series, slow)
        macd_line = ema_fast - ema_slow
        signal_line = Indicators.ema(macd_line, signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        Average True Range using numpy-vectorised True Range, then Wilder EWM.

        Eliminates the Python-loop overhead of the standard three-line rolling max.

        Args:
            df:     OHLCV DataFrame with columns ``high``, ``low``, ``close``.
            period: ATR look-back (default 14).

        Returns:
            pd.Series of ATR values aligned with ``df.index``.
        """
        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        c = df["close"].to_numpy(dtype=float)

        prev_c = np.empty_like(c)
        prev_c[0] = c[0]
        prev_c[1:] = c[:-1]

        tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
        return pd.Series(tr, index=df.index).ewm(span=period, adjust=False).mean()

    @staticmethod
    def bollinger_bands(
        series: pd.Series,
        period: int = 20,
        std_dev: float = 2.0,
    ) -> tuple[pd.Series, pd.Series, pd.Series]:
        """
        Bollinger Bands: upper, middle (SMA), and lower bands.

        Args:
            series:  Close price series.
            period:  SMA window (default 20).
            std_dev: Standard deviation multiplier (default 2.0).

        Returns:
            Tuple of (upper, middle, lower) pd.Series.
        """
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
        """
        Stochastic RSI — normalises RSI into a [0, 100] stochastic oscillator.

        Args:
            series:       Close price series.
            rsi_period:   RSI look-back (default 14).
            stoch_period: Stochastic rolling window over RSI (default 14).
            smooth_k:     %K smoothing periods (default 3).
            smooth_d:     %D (signal) smoothing periods (default 3).

        Returns:
            Tuple of (%K, %D) pd.Series, values in [0, 100].
        """
        rsi_vals = Indicators.rsi(series, rsi_period)
        rsi_min = rsi_vals.rolling(window=stoch_period).min()
        rsi_max = rsi_vals.rolling(window=stoch_period).max()
        rsi_range = (rsi_max - rsi_min).replace(0.0, np.nan)
        stoch_rsi = (rsi_vals - rsi_min) / rsi_range
        k = stoch_rsi.rolling(window=smooth_k).mean() * 100.0
        d = k.rolling(window=smooth_d).mean()
        return k, d

    @staticmethod
    def volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
        """
        Volume Ratio: current volume divided by its ``period``-bar SMA.

        A value > 1.0 means above-average volume; < 1.0 means below.

        Args:
            df:     OHLCV DataFrame with a ``volume`` column.
            period: Look-back window for the average (default 20).

        Returns:
            pd.Series where 1.0 equals the rolling average volume.
        """
        vol = df["volume"].astype(float)
        avg = vol.rolling(window=period).mean().replace(0.0, np.nan)
        return vol / avg

    @staticmethod
    def compute_snapshot(df: pd.DataFrame) -> Optional["IndicatorSnapshot"]:
        """
        Compute every indicator in a single pass and return an immutable snapshot.

        This is the primary entry-point for the Confluence Engine.  All values
        are taken from the last two closed bars (``iloc[-1]`` and ``iloc[-2]``).

        Args:
            df: OHLCV DataFrame with at least 30 rows and columns
                ``open``, ``high``, ``low``, ``close``, ``volume``.

        Returns:
            ``IndicatorSnapshot`` or ``None`` if there is insufficient data.
        """
        if df is None or len(df) < 30:
            return None

        close = df["close"].astype(float)

        ema_9_s = Indicators.ema(close, 9)
        ema_21_s = Indicators.ema(close, 21)
        ema_50_s = Indicators.ema(close, 50)
        ema_200_s = (
            Indicators.ema(close, 200)
            if len(df) >= 200
            else Indicators.ema(close, min(len(df), 100))
        )

        rsi_s = Indicators.rsi(close, 14)
        macd_line_s, signal_line_s, histogram_s = Indicators.macd(close)
        atr_s = Indicators.atr(df, 14)
        atr_sma_s = Indicators.sma(atr_s, 20) if len(df) >= 34 else atr_s
        bb_upper_s, bb_middle_s, bb_lower_s = Indicators.bollinger_bands(close, 20, 2.0)
        stoch_k_s, stoch_d_s = Indicators.stochastic_rsi(close)
        vol_ratio_s = Indicators.volume_ratio(df, 20)

        def _safe(s: pd.Series, idx: int = -1, default: float = 50.0) -> float:
            val = s.iloc[idx]
            return float(val) if not pd.isna(val) else default

        return IndicatorSnapshot(
            ema_9=_safe(ema_9_s),
            ema_21=_safe(ema_21_s),
            ema_50=_safe(ema_50_s),
            ema_200=_safe(ema_200_s),
            prev_ema_9=_safe(ema_9_s, -2),
            prev_ema_21=_safe(ema_21_s, -2),
            rsi=_safe(rsi_s),
            prev_rsi=_safe(rsi_s, -2),
            macd_line=_safe(macd_line_s, default=0.0),
            signal_line=_safe(signal_line_s, default=0.0),
            prev_macd_line=_safe(macd_line_s, -2, 0.0),
            prev_signal_line=_safe(signal_line_s, -2, 0.0),
            histogram=_safe(histogram_s, default=0.0),
            prev_histogram=_safe(histogram_s, -2, 0.0),
            atr=_safe(atr_s, default=0.0),
            atr_sma=_safe(atr_sma_s, default=0.0),
            bb_upper=_safe(bb_upper_s, default=0.0),
            bb_middle=_safe(bb_middle_s, default=0.0),
            bb_lower=_safe(bb_lower_s, default=0.0),
            stoch_k=_safe(stoch_k_s),
            stoch_d=_safe(stoch_d_s),
            prev_stoch_k=_safe(stoch_k_s, -2),
            prev_stoch_d=_safe(stoch_d_s, -2),
            vol_ratio=_safe(vol_ratio_s, default=1.0),
            close=float(close.iloc[-1]),
            prev_close=float(close.iloc[-2]),
        )

    @staticmethod
    def detect_engulfing(df: pd.DataFrame) -> Optional[str]:
        """
        Detect a bullish or bearish engulfing candlestick pattern on the last two bars.

        Criteria
        --------
        - Current body must be at least 50 % larger than the previous body.
        - Bullish engulfing: prev bar is bearish, current bar is bullish,
          and current bar's body fully covers the previous body.
        - Bearish engulfing: the mirror image.

        Args:
            df: OHLCV DataFrame with at least 3 rows.

        Returns:
            ``'BULLISH_ENGULFING'``, ``'BEARISH_ENGULFING'``, or ``None``.
        """
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
        """
        Detect a pin-bar (hammer or shooting star) on the last bar.

        Criteria
        --------
        - Body-to-range ratio must be ≤ 30 %.
        - Hammer: lower wick ≥ 2.5× body, upper wick < 1× body.
        - Shooting star: upper wick ≥ 2.5× body, lower wick < 1× body.

        Args:
            df: OHLCV DataFrame with at least 2 rows.

        Returns:
            ``'HAMMER'``, ``'SHOOTING_STAR'``, or ``None``.
        """
        if len(df) < 2:
            return None

        o = float(df["open"].iloc[-1])
        h = float(df["high"].iloc[-1])
        lo = float(df["low"].iloc[-1])
        c = float(df["close"].iloc[-1])

        body = abs(c - o)
        total_range = h - lo
        if total_range == 0.0:
            return None

        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - lo

        if body / total_range > 0.30:
            return None

        if lower_wick >= body * 2.5 and upper_wick < body * 1.0:
            return "HAMMER"

        if upper_wick >= body * 2.5 and lower_wick < body * 1.0:
            return "SHOOTING_STAR"

        return None
