import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from engine.indicators import Indicators, IndicatorSnapshot
from engine.fibonacci import AutoFibonacci

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    direction: str
    confidence: float
    confluences: list = field(default_factory=list)
    entry_price: float = 0.0
    atr_value: float = 0.0
    trend_strength: str = "NEUTRAL"
    fib_zone: Optional[str] = None

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
    position_bias: str
    fib_tp: Optional[float] = None
    fib_sl: Optional[float] = None


class ConfluenceEngine:

    MIN_CONFLUENCES = 4
    CORE_REQUIRED = 3

    @staticmethod
    def analyze(df: pd.DataFrame) -> Optional[SignalResult]:
        if df is None or len(df) < 30:
            return None

        try:
            snap = Indicators.compute_snapshot(df)
            if snap is None:
                return None

            if snap.atr_sma > 0 and snap.atr < snap.atr_sma * 0.4:
                return None

            candle_pattern = Indicators.detect_engulfing(df)
            pin_bar = Indicators.detect_pin_bar(df)
            fib_levels = AutoFibonacci.compute(df, lookback=5)

            buy_conf, buy_core = ConfluenceEngine._check_buy(snap, candle_pattern, pin_bar, fib_levels)
            sell_conf, sell_core = ConfluenceEngine._check_sell(snap, candle_pattern, pin_bar, fib_levels)

            trend = ConfluenceEngine._assess_trend(snap)

            return ConfluenceEngine._decide(
                buy_conf, buy_core, sell_conf, sell_core, trend, snap, fib_levels
            )

        except Exception as e:
            logger.error(f"Confluence analysis error: {e}", exc_info=True)
            return None

    @staticmethod
    def _check_buy(snap: IndicatorSnapshot, candle: Optional[str], pin: Optional[str], fib) -> tuple[list, int]:
        confluences = []
        core_count = 0

        if snap.ema_9 > snap.ema_21 and snap.prev_ema_9 <= snap.prev_ema_21:
            confluences.append("EMA_9/21_GOLDEN_CROSS")
        elif snap.ema_9 > snap.ema_21 and snap.close > snap.ema_9:
            confluences.append("EMA_BULLISH_ALIGNMENT")

        rsi_hit = False
        if 30 <= snap.rsi <= 55 and snap.rsi > snap.prev_rsi and snap.prev_rsi < 40:
            confluences.append("RSI_OVERSOLD_RECOVERY")
            rsi_hit = True
            core_count += 1
        elif 50 < snap.rsi < 70 and snap.rsi > snap.prev_rsi:
            confluences.append("RSI_BULLISH_MOMENTUM")
            rsi_hit = True
            core_count += 1

        macd_hit = False
        if snap.histogram > 0 and snap.prev_histogram <= 0:
            confluences.append("MACD_HISTOGRAM_FLIP")
            macd_hit = True
            core_count += 1
        elif snap.macd_line > snap.signal_line and snap.prev_macd_line <= snap.prev_signal_line:
            confluences.append("MACD_BULLISH_CROSS")
            macd_hit = True
            core_count += 1
        elif snap.histogram > snap.prev_histogram and snap.histogram > 0:
            confluences.append("MACD_INCREASING_MOMENTUM")
            macd_hit = True
            core_count += 1

        bb_range = snap.bb_upper - snap.bb_lower
        bb_hit = False
        if bb_range > 0:
            bb_position = (snap.close - snap.bb_lower) / bb_range
            if bb_position < 0.25:
                confluences.append("BB_NEAR_LOWER_BAND")
                bb_hit = True
                core_count += 1
            elif snap.prev_close < snap.bb_middle and snap.close > snap.bb_middle:
                confluences.append("BB_MIDDLE_BREAKOUT")
                bb_hit = True
                core_count += 1

        if snap.vol_ratio > 1.2:
            confluences.append("VOLUME_ABOVE_AVERAGE")

        if snap.stoch_k < 30 and snap.stoch_k > snap.stoch_d and snap.prev_stoch_k <= snap.prev_stoch_d:
            confluences.append("STOCH_RSI_BULLISH_CROSS")
        elif snap.stoch_k < 40 and snap.stoch_k > snap.prev_stoch_k:
            confluences.append("STOCH_RSI_RECOVERY")

        if candle == "BULLISH_ENGULFING":
            confluences.append("BULLISH_ENGULFING")
        if pin == "HAMMER":
            confluences.append("HAMMER")

        if fib is not None:
            fib_zone = AutoFibonacci.get_fib_zone(snap.close, fib)
            if fib_zone and fib.trend == "UPTREND":
                confluences.append(fib_zone)

        return confluences, core_count

    @staticmethod
    def _check_sell(snap: IndicatorSnapshot, candle: Optional[str], pin: Optional[str], fib) -> tuple[list, int]:
        confluences = []
        core_count = 0

        if snap.ema_9 < snap.ema_21 and snap.prev_ema_9 >= snap.prev_ema_21:
            confluences.append("EMA_9/21_DEATH_CROSS")
        elif snap.ema_9 < snap.ema_21 and snap.close < snap.ema_9:
            confluences.append("EMA_BEARISH_ALIGNMENT")

        rsi_hit = False
        if 45 <= snap.rsi <= 70 and snap.rsi < snap.prev_rsi and snap.prev_rsi > 60:
            confluences.append("RSI_OVERBOUGHT_REJECTION")
            rsi_hit = True
            core_count += 1
        elif 30 < snap.rsi < 50 and snap.rsi < snap.prev_rsi:
            confluences.append("RSI_BEARISH_MOMENTUM")
            rsi_hit = True
            core_count += 1

        macd_hit = False
        if snap.histogram < 0 and snap.prev_histogram >= 0:
            confluences.append("MACD_HISTOGRAM_FLIP")
            macd_hit = True
            core_count += 1
        elif snap.macd_line < snap.signal_line and snap.prev_macd_line >= snap.prev_signal_line:
            confluences.append("MACD_BEARISH_CROSS")
            macd_hit = True
            core_count += 1
        elif snap.histogram < snap.prev_histogram and snap.histogram < 0:
            confluences.append("MACD_INCREASING_SELL_MOMENTUM")
            macd_hit = True
            core_count += 1

        bb_range = snap.bb_upper - snap.bb_lower
        bb_hit = False
        if bb_range > 0:
            bb_position = (snap.close - snap.bb_lower) / bb_range
            if bb_position > 0.75:
                confluences.append("BB_NEAR_UPPER_BAND")
                bb_hit = True
                core_count += 1
            elif snap.prev_close > snap.bb_middle and snap.close < snap.bb_middle:
                confluences.append("BB_MIDDLE_BREAKDOWN")
                bb_hit = True
                core_count += 1

        if snap.vol_ratio > 1.2:
            confluences.append("VOLUME_ABOVE_AVERAGE")

        if snap.stoch_k > 70 and snap.stoch_k < snap.stoch_d and snap.prev_stoch_k >= snap.prev_stoch_d:
            confluences.append("STOCH_RSI_BEARISH_CROSS")
        elif snap.stoch_k > 60 and snap.stoch_k < snap.prev_stoch_k:
            confluences.append("STOCH_RSI_REJECTION")

        if candle == "BEARISH_ENGULFING":
            confluences.append("BEARISH_ENGULFING")
        if pin == "SHOOTING_STAR":
            confluences.append("SHOOTING_STAR")

        if fib is not None:
            fib_zone = AutoFibonacci.get_fib_zone(snap.close, fib)
            if fib_zone and fib.trend == "DOWNTREND":
                confluences.append(fib_zone)

        return confluences, core_count

    @staticmethod
    def _assess_trend(snap: IndicatorSnapshot) -> str:
        c = snap.close
        e9 = snap.ema_9
        e21 = snap.ema_21
        e50 = snap.ema_50
        e200 = snap.ema_200

        if c > e9 > e21 > e50 > e200:
            return "STRONG_BULL"
        elif c > e50 and e9 > e21:
            return "BULL"
        elif c < e9 < e21 < e50 < e200:
            return "STRONG_BEAR"
        elif c < e50 and e9 < e21:
            return "BEAR"
        return "NEUTRAL"

    @staticmethod
    def _decide(
        buy_conf, buy_core, sell_conf, sell_core, trend, snap, fib_levels
    ) -> Optional[SignalResult]:
        buy_score = len(buy_conf)
        sell_score = len(sell_conf)

        if buy_score >= ConfluenceEngine.MIN_CONFLUENCES and sell_score >= ConfluenceEngine.MIN_CONFLUENCES:
            logger.debug(f"Conflicting signals: BUY({buy_score}) vs SELL({sell_score})")
            return None

        if trend == "STRONG_BULL" and sell_score >= ConfluenceEngine.MIN_CONFLUENCES and buy_score < 2:
            logger.debug("Trend filter: STRONG_BULL blocking SELL")
            return None
        if trend == "STRONG_BEAR" and buy_score >= ConfluenceEngine.MIN_CONFLUENCES and sell_score < 2:
            logger.debug("Trend filter: STRONG_BEAR blocking BUY")
            return None

        if buy_score >= ConfluenceEngine.MIN_CONFLUENCES and buy_core >= ConfluenceEngine.CORE_REQUIRED and buy_score > sell_score:
            confidence = min(buy_score / 8.0, 1.0)
            if trend in ("STRONG_BULL", "BULL"):
                confidence = min(confidence + 0.1, 1.0)

            fib_zone = None
            if fib_levels:
                fib_zone = AutoFibonacci.get_fib_zone(snap.close, fib_levels)

            return SignalResult(
                direction="BUY",
                confidence=confidence,
                confluences=buy_conf,
                entry_price=snap.close,
                atr_value=snap.atr,
                trend_strength=trend,
                fib_zone=fib_zone,
            )

        if sell_score >= ConfluenceEngine.MIN_CONFLUENCES and sell_core >= ConfluenceEngine.CORE_REQUIRED and sell_score > buy_score:
            confidence = min(sell_score / 8.0, 1.0)
            if trend in ("STRONG_BEAR", "BEAR"):
                confidence = min(confidence + 0.1, 1.0)

            fib_zone = None
            if fib_levels:
                fib_zone = AutoFibonacci.get_fib_zone(snap.close, fib_levels)

            return SignalResult(
                direction="SELL",
                confidence=confidence,
                confluences=sell_conf,
                entry_price=snap.close,
                atr_value=snap.atr,
                trend_strength=trend,
                fib_zone=fib_zone,
            )

        return None

    @staticmethod
    def calculate_atr_levels(signal: SignalResult, df: pd.DataFrame) -> Optional[TradeLevels]:
        if not signal or not signal.atr_value:
            return None

        atr = signal.atr_value
        entry = signal.entry_price
        confidence = signal.confidence

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
        tp1_distance = atr * 2.0
        tp2_distance = atr * 3.5
        tp3_distance = atr * 5.5

        fib_levels = AutoFibonacci.compute(df, lookback=5)
        fib_tp = None
        fib_sl = None

        if signal.direction == "BUY":
            sl = entry - sl_distance
            tp_list = [
                round(entry + tp1_distance, 8),
                round(entry + tp2_distance, 8),
                round(entry + tp3_distance, 8),
            ]
            if fib_levels and fib_levels.trend == "UPTREND":
                if fib_levels.swing_high > entry:
                    fib_tp = fib_levels.swing_high
                structure_sl = fib_levels.level_786 - (atr * 0.3)
                if structure_sl < sl and structure_sl > 0:
                    fib_sl = structure_sl
                    sl = structure_sl

        elif signal.direction == "SELL":
            sl = entry + sl_distance
            tp_list = [
                round(entry - tp1_distance, 8),
                round(entry - tp2_distance, 8),
                round(entry - tp3_distance, 8),
            ]
            if fib_levels and fib_levels.trend == "DOWNTREND":
                if fib_levels.swing_low < entry:
                    fib_tp = fib_levels.swing_low
                structure_sl = fib_levels.level_786 + (atr * 0.3)
                if structure_sl > sl:
                    fib_sl = structure_sl
                    sl = structure_sl
        else:
            return None

        highs = df["high"].rolling(window=10, center=False).max()
        lows = df["low"].rolling(window=10, center=False).min()

        try:
            last_swing_high = float(highs.iloc[-1])
            last_swing_low = float(lows.iloc[-1])

            if signal.direction == "BUY":
                swing_sl = last_swing_low - (atr * 0.3)
                if swing_sl < sl:
                    sl = swing_sl
            elif signal.direction == "SELL":
                swing_sl = last_swing_high + (atr * 0.3)
                if swing_sl > sl:
                    sl = swing_sl
        except (IndexError, ValueError):
            pass

        sl = round(sl, 8)
        rr_ratio = tp1_distance / sl_distance if sl_distance > 0 else 0

        if rr_ratio < 1.3:
            logger.debug(f"R:R too low ({rr_ratio:.2f}), skipping")
            return None

        return TradeLevels(
            entry_prices=[round(entry, 8)],
            tp_list=tp_list,
            sl=sl,
            risk_reward_ratio=round(rr_ratio, 2),
            atr=round(atr, 8),
            position_bias=position_bias,
            fib_tp=round(fib_tp, 8) if fib_tp else None,
            fib_sl=round(fib_sl, 8) if fib_sl else None,
        )
