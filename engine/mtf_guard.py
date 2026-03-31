import logging
from typing import Optional

import pandas as pd

from engine.indicators import Indicators

logger = logging.getLogger(__name__)

HIGHER_TF_MAP = {
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

    @staticmethod
    def get_higher_timeframe(interval: str) -> Optional[str]:
        return HIGHER_TF_MAP.get(interval)

    @staticmethod
    def assess_trend(df: pd.DataFrame) -> str:
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

        macd_line, signal_line, histogram = Indicators.macd(close)
        hist_val = float(histogram.iloc[-1]) if not pd.isna(histogram.iloc[-1]) else 0.0

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
        if signal_direction == "BUY":
            if higher_tf_trend in ("STRONG_BEAR",):
                return False
            return True

        if signal_direction == "SELL":
            if higher_tf_trend in ("STRONG_BULL",):
                return False
            return True

        return False

    @staticmethod
    def validate(
        signal_direction: str,
        current_interval: str,
        get_kline_fn,
        symbol: str,
    ) -> tuple[bool, str]:
        higher_tf = MTFTrendGuard.get_higher_timeframe(current_interval)
        if not higher_tf:
            return True, "NO_HIGHER_TF"

        try:
            higher_df = get_kline_fn(symbol, higher_tf)
            if higher_df is None or len(higher_df) < 20:
                logger.debug(f"MTF Guard: insufficient data for {symbol} {higher_tf}, allowing trade")
                return True, "INSUFFICIENT_DATA"

            trend = MTFTrendGuard.assess_trend(higher_df)
            allowed = MTFTrendGuard.is_trade_allowed(signal_direction, trend)

            if not allowed:
                logger.info(
                    f"MTF Guard BLOCKED: {signal_direction} on {symbol} {current_interval} | "
                    f"Higher TF ({higher_tf}) trend: {trend}"
                )
                return False, trend

            logger.debug(
                f"MTF Guard PASSED: {signal_direction} on {symbol} {current_interval} | "
                f"Higher TF ({higher_tf}) trend: {trend}"
            )
            return True, trend

        except Exception as e:
            logger.error(f"MTF Guard error for {symbol}: {e}")
            return True, "ERROR"
