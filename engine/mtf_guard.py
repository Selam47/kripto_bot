"""
Multi-Timeframe Trend Guard (MTF Guard)
========================================
Prevents the bot from executing trades that go against the higher-timeframe
trend.  Before any signal is acted upon, this module fetches the kline data
for the next higher timeframe and checks whether the macro trend is aligned.

Timeframe hierarchy used
------------------------
    1m  → 5m
    3m  → 15m
    5m  → 15m
    15m → 1h   ← most common bot setup
    30m → 1h
    1h  → 4h
    4h  → 1d
    1d  → 1w

A ``STRONG_BULL`` higher-TF trend blocks any SELL signal on the lower TF.
A ``STRONG_BEAR`` higher-TF trend blocks any BUY signal on the lower TF.
All other combinations are allowed to pass (the individual bar confluence must
still be present — this guard only suppresses clear counter-trend extremes).
"""

import logging
from typing import Callable, Optional

import pandas as pd

from engine.indicators import Indicators

logger = logging.getLogger(__name__)

_HIGHER_TF_MAP: dict[str, str] = {
    "1m": "5m",
    "3m": "15m",
    "5m": "15m",
    "15m": "1h",
    "30m": "1h",
    "1h": "4h",
    "4h": "1d",
    "1d": "1w",
}


class MTFTrendGuard:
    """
    Stateless Multi-Timeframe Trend Guard.

    The primary entry-point is ``validate()``, which accepts a callable that
    returns a DataFrame for any symbol/interval pair.  This design avoids
    creating a hard dependency on ``MarketData`` inside this module, making it
    independently testable.
    """

    @staticmethod
    def get_higher_timeframe(interval: str) -> Optional[str]:
        """
        Return the higher timeframe mapped to ``interval``.

        Args:
            interval: Lower timeframe string (e.g. ``'15m'``).

        Returns:
            Higher timeframe string (e.g. ``'1h'``), or ``None`` if no
            mapping is defined.
        """
        return _HIGHER_TF_MAP.get(interval)

    @staticmethod
    def assess_trend(df: pd.DataFrame) -> str:
        """
        Assess the macro trend of a timeframe using EMA stack + RSI + MACD.

        A composite confirmation is required for ``STRONG_*`` labels:
        - ``STRONG_BULL``: price > 9 > 21 > 50 EMA AND RSI > 50 AND histogram > 0
        - ``BULL``:        price > 50 EMA AND 9 > 21 EMA AND RSI > 45
        - ``STRONG_BEAR``: price < 9 < 21 < 50 EMA AND RSI < 50 AND histogram < 0
        - ``BEAR``:        price < 50 EMA AND 9 < 21 EMA AND RSI < 55
        - ``NEUTRAL``:     everything else

        Args:
            df: OHLCV DataFrame for the higher timeframe with at least 30 rows.

        Returns:
            Trend label string.
        """
        if df is None or len(df) < 30:
            return "NEUTRAL"

        close = df["close"].astype(float)
        ema_9 = Indicators.ema(close, 9)
        ema_21 = Indicators.ema(close, 21)
        ema_50 = Indicators.ema(close, 50)

        c = float(close.iloc[-1])
        e9 = float(ema_9.iloc[-1])
        e21 = float(ema_21.iloc[-1])
        e50 = float(ema_50.iloc[-1])

        rsi_s = Indicators.rsi(close, 14)
        rsi_val = float(rsi_s.iloc[-1]) if not pd.isna(rsi_s.iloc[-1]) else 50.0

        _, _, histogram_s = Indicators.macd(close)
        hist_val = float(histogram_s.iloc[-1]) if not pd.isna(histogram_s.iloc[-1]) else 0.0

        if c > e9 > e21 > e50 and rsi_val > 50 and hist_val > 0:
            return "STRONG_BULL"
        if c > e50 and e9 > e21 and rsi_val > 45:
            return "BULL"
        if c < e9 < e21 < e50 and rsi_val < 50 and hist_val < 0:
            return "STRONG_BEAR"
        if c < e50 and e9 < e21 and rsi_val < 55:
            return "BEAR"
        return "NEUTRAL"

    @staticmethod
    def is_trade_allowed(signal_direction: str, higher_tf_trend: str) -> bool:
        """
        Determine whether a trade direction is permitted given the higher-TF trend.

        Rules
        -----
        - BUY blocked only when higher TF is ``STRONG_BEAR``.
        - SELL blocked only when higher TF is ``STRONG_BULL``.
        - All other combinations are allowed.

        Args:
            signal_direction: ``'BUY'`` or ``'SELL'``.
            higher_tf_trend:  Trend string from ``assess_trend()``.

        Returns:
            ``True`` if the trade is permitted.
        """
        if signal_direction == "BUY" and higher_tf_trend == "STRONG_BEAR":
            return False
        if signal_direction == "SELL" and higher_tf_trend == "STRONG_BULL":
            return False
        return True

    @staticmethod
    def validate(
        signal_direction: str,
        current_interval: str,
        get_kline_fn: Callable[[str, str], pd.DataFrame],
        symbol: str,
    ) -> tuple[bool, str]:
        """
        Full MTF validation — the primary public entry-point.

        Looks up the higher timeframe, fetches its data via ``get_kline_fn``,
        assesses the trend, and decides whether the trade is permitted.

        Args:
            signal_direction:  ``'BUY'`` or ``'SELL'`` (or ``''`` for a
                               pre-signal check).
            current_interval:  The interval at which the signal fired
                               (e.g. ``'15m'``).
            get_kline_fn:      Callable ``(symbol, interval) -> pd.DataFrame``.
                               Typically ``MarketData.get_klines``.
            symbol:            Trading pair symbol (e.g. ``'BTCUSDT'``).

        Returns:
            Tuple of ``(allowed: bool, higher_tf_trend: str)``.
            If no higher TF mapping exists the tuple is ``(True, 'NO_HIGHER_TF')``.
            On data errors the trade defaults to **allowed** to avoid false
            blocking.
        """
        higher_tf = MTFTrendGuard.get_higher_timeframe(current_interval)
        if not higher_tf:
            return True, "NO_HIGHER_TF"

        if not signal_direction:
            return True, "PENDING"

        try:
            higher_df = get_kline_fn(symbol, higher_tf)
            if higher_df is None or len(higher_df) < 20:
                logger.debug(
                    f"MTF Guard: insufficient data for {symbol} {higher_tf} — trade allowed by default"
                )
                return True, "INSUFFICIENT_DATA"

            trend = MTFTrendGuard.assess_trend(higher_df)
            allowed = MTFTrendGuard.is_trade_allowed(signal_direction, trend)

            if not allowed:
                logger.info(
                    f"MTF Guard BLOCKED: {signal_direction} {symbol} {current_interval} "
                    f"| Higher TF ({higher_tf}) trend = {trend}"
                )
            else:
                logger.debug(
                    f"MTF Guard PASSED: {signal_direction} {symbol} {current_interval} "
                    f"| Higher TF ({higher_tf}) trend = {trend}"
                )

            return allowed, trend

        except Exception as e:
            logger.error(f"MTF Guard error for {symbol} {current_interval}: {e}")
            return True, "ERROR"
