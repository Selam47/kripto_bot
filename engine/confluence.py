"""
Confluence Engine — Antigravity Signal Strategy
=================================================
A signal is only considered **valid** when three independent indicator groups
all agree simultaneously — the so-called "confluence" approach:

    1. **RSI Group** — momentum exhaustion (oversold < 25 / overbought > 75)
    2. **MACD Group** — trend momentum confirmation / divergence
    3. **Bollinger Bands Group** — price exhaustion at 2.5σ bands

All three *core* groups must fire (``core_confirmed >= 3``) AND the total
confluence count must reach ``MIN_CONFLUENCES`` (default 4) before a
``SignalResult`` is emitted.  The "Antigravity" logic specifically seeks
price that has reached extreme exhaustion and is poised for a sharp reversal
or a high-momentum continuation break.

- Counter-trend trades against a ``STRONG_BULL`` / ``STRONG_BEAR`` EMA stack
  are suppressed.
- Conflicting buy/sell scores of equal strength are discarded (choppy market).
- ATR squeeze filter: if current ATR < 40 % of its 20-bar SMA the market is
  too compressed to trade — no signal is produced.

The ``calculate_atr_levels()`` method converts a ``SignalResult`` into a
``TradeLevels`` object with:
- Three tiered take-profit levels (2.0×, 3.5×, 5.5× ATR)
- A structural stop-loss anchored to the last swing high/low (or 1.5× ATR)
- Fibonacci-based secondary TP and SL when the structure supports it
- Risk/Reward guard: if TP1/SL < 1.3 the trade is rejected
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from engine.indicators import Indicators, IndicatorSnapshot
from engine.fibonacci import AutoFibonacci, FibonacciLevels

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    """
    Output of the Confluence Engine for a single bar.

    Attributes
    ----------
    direction : str
        ``'BUY'`` or ``'SELL'``.
    confidence : float
        Normalised score in [0.0, 1.0] — ``len(confluences) / 8``.
    confluences : list[str]
        Named conditions that fired (e.g. ``'MACD_HISTOGRAM_FLIP'``).
    entry_price : float
        Closing price at the moment the signal fired.
    atr_value : float
        ATR value used for TP/SL sizing.
    trend_strength : str
        EMA-stack assessment: ``STRONG_BULL | BULL | NEUTRAL | BEAR | STRONG_BEAR``.
    fib_zone : str | None
        Nearest Fibonacci zone tag, or ``None`` if price is not near a level.
    """

    direction: str
    confidence: float
    confluences: list = field(default_factory=list)
    entry_price: float = 0.0
    atr_value: float = 0.0
    trend_strength: str = "NEUTRAL"
    fib_zone: Optional[str] = None

    def __str__(self) -> str:
        return self.direction

    def __bool__(self) -> bool:
        return self.direction in ("BUY", "SELL")


@dataclass
class TradeLevels:
    """
    Fully calculated trade execution parameters.

    Attributes
    ----------
    entry_prices : list[float]
        Suggested entry zone (currently a single price).
    tp_list : list[float]
        Three take-profit levels in order (TP1, TP2, TP3).
    sl : float
        Stop-loss level.
    risk_reward_ratio : float
        Reward-to-risk ratio for TP1 vs SL.
    atr : float
        ATR value used for the calculation.
    position_bias : str
        ``'AGGRESSIVE' | 'NORMAL' | 'CONSERVATIVE'`` based on confidence.
    fib_tp : float | None
        Fibonacci-derived take-profit target (swing high/low), or ``None``.
    fib_sl : float | None
        Fibonacci-derived stop-loss level (0.786 + buffer), or ``None``.
    """

    entry_prices: list
    tp_list: list
    sl: float
    risk_reward_ratio: float
    atr: float
    position_bias: str
    fib_tp: Optional[float] = None
    fib_sl: Optional[float] = None


class ConfluenceEngine:
    """
    Stateless signal-generation engine.

    All methods are static so the engine can be called from any thread without
    shared state.  The typical call sequence is:

        signal = ConfluenceEngine.analyze(df)
        if signal:
            levels = ConfluenceEngine.calculate_atr_levels(signal, df)
    """

    MIN_CONFLUENCES: int = 4
    CORE_REQUIRED: int = 3

    @staticmethod
    def analyze(df: pd.DataFrame) -> Optional[SignalResult]:
        """
        Run the full confluence pipeline on a DataFrame of OHLCV bars.

        Steps
        -----
        1. Compute all indicators via ``Indicators.compute_snapshot()``.
        2. Apply the ATR squeeze filter.
        3. Detect candlestick patterns and Fibonacci levels.
        4. Score BUY and SELL confluences independently.
        5. Apply trend and conflict filters.
        6. Emit a ``SignalResult`` if all thresholds are met.

        Args:
            df: OHLCV DataFrame with at least 30 rows and columns
                ``open``, ``high``, ``low``, ``close``, ``volume``.

        Returns:
            ``SignalResult`` or ``None``.
        """
        if df is None or len(df) < 30:
            return None

        try:
            snap = Indicators.compute_snapshot(df)
            if snap is None:
                return None

            if snap.atr_sma > 0.0 and snap.atr < snap.atr_sma * 0.4:
                return None

            candle = Indicators.detect_engulfing(df)
            pin = Indicators.detect_pin_bar(df)
            fib = AutoFibonacci.compute(df, lookback=5)

            buy_conf, buy_core = ConfluenceEngine._score_buy(snap, candle, pin, fib)
            sell_conf, sell_core = ConfluenceEngine._score_sell(snap, candle, pin, fib)
            trend = ConfluenceEngine._assess_trend(snap)

            return ConfluenceEngine._decide(
                buy_conf, buy_core, sell_conf, sell_core, trend, snap, fib
            )

        except Exception as e:
            logger.error(f"Confluence analysis error: {e}", exc_info=True)
            return None

    @staticmethod
    def _score_buy(
        snap: IndicatorSnapshot,
        candle: Optional[str],
        pin: Optional[str],
        fib: Optional[FibonacciLevels],
    ) -> tuple[list[str], int]:
        """
        Evaluate all BUY confluence conditions and return (conditions, core_count).

        Core conditions (RSI, MACD, Bollinger) each increment ``core_count``.
        A signal requires ``core_count >= CORE_REQUIRED``, ensuring all three
        pillars are represented.

        Args:
            snap:   Pre-computed indicator snapshot.
            candle: Engulfing pattern tag or ``None``.
            pin:    Pin-bar pattern tag or ``None``.
            fib:    Fibonacci levels or ``None``.

        Returns:
            Tuple of (list of fired condition names, core group count).
        """
        conf: list[str] = []
        core = 0

        if snap.ema_9 > snap.ema_21 and snap.prev_ema_9 <= snap.prev_ema_21:
            conf.append("EMA_9/21_GOLDEN_CROSS")
        elif snap.ema_9 > snap.ema_21 and snap.close > snap.ema_9:
            conf.append("EMA_BULLISH_ALIGNMENT")

        # RSI: Antigravity oversold threshold = 25 (deeper exhaustion)
        if 25 <= snap.rsi <= 50 and snap.rsi > snap.prev_rsi and snap.prev_rsi < 40:
            conf.append("RSI_OVERSOLD_RECOVERY")
            core += 1
        elif 50 < snap.rsi < 75 and snap.rsi > snap.prev_rsi:
            conf.append("RSI_BULLISH_MOMENTUM")
            core += 1

        if snap.histogram > 0.0 and snap.prev_histogram <= 0.0:
            conf.append("MACD_HISTOGRAM_FLIP")
            core += 1
        elif snap.macd_line > snap.signal_line and snap.prev_macd_line <= snap.prev_signal_line:
            conf.append("MACD_BULLISH_CROSS")
            core += 1
        elif snap.histogram > snap.prev_histogram and snap.histogram > 0.0:
            conf.append("MACD_INCREASING_MOMENTUM")
            core += 1

        bb_range = snap.bb_upper - snap.bb_lower
        if bb_range > 0.0:
            bb_pos = (snap.close - snap.bb_lower) / bb_range
            # 2.5σ bands — only fire at true exhaustion zone (< 10 % of band)
            if bb_pos < 0.10:
                conf.append("BB_NEAR_LOWER_BAND")
                core += 1
            elif snap.prev_close < snap.bb_middle and snap.close > snap.bb_middle:
                conf.append("BB_MIDDLE_BREAKOUT")
                core += 1

        if snap.vol_ratio > 1.2:
            conf.append("VOLUME_ABOVE_AVERAGE")

        if snap.stoch_k < 25 and snap.stoch_k > snap.stoch_d and snap.prev_stoch_k <= snap.prev_stoch_d:
            conf.append("STOCH_RSI_BULLISH_CROSS")
        elif snap.stoch_k < 35 and snap.stoch_k > snap.prev_stoch_k:
            conf.append("STOCH_RSI_RECOVERY")

        if candle == "BULLISH_ENGULFING":
            conf.append("BULLISH_ENGULFING")
        if pin == "HAMMER":
            conf.append("HAMMER")

        if fib is not None and fib.trend == "UPTREND":
            zone = AutoFibonacci.get_fib_zone(snap.close, fib)
            if zone:
                conf.append(zone)

        return conf, core

    @staticmethod
    def _score_sell(
        snap: IndicatorSnapshot,
        candle: Optional[str],
        pin: Optional[str],
        fib: Optional[FibonacciLevels],
    ) -> tuple[list[str], int]:
        """
        Evaluate all SELL confluence conditions and return (conditions, core_count).

        Mirrors ``_score_buy`` with bearish criteria.

        Args:
            snap:   Pre-computed indicator snapshot.
            candle: Engulfing pattern tag or ``None``.
            pin:    Pin-bar pattern tag or ``None``.
            fib:    Fibonacci levels or ``None``.

        Returns:
            Tuple of (list of fired condition names, core group count).
        """
        conf: list[str] = []
        core = 0

        if snap.ema_9 < snap.ema_21 and snap.prev_ema_9 >= snap.prev_ema_21:
            conf.append("EMA_9/21_DEATH_CROSS")
        elif snap.ema_9 < snap.ema_21 and snap.close < snap.ema_9:
            conf.append("EMA_BEARISH_ALIGNMENT")

        # RSI: Antigravity overbought threshold = 75 (extreme exhaustion)
        if 50 <= snap.rsi <= 75 and snap.rsi < snap.prev_rsi and snap.prev_rsi > 60:
            conf.append("RSI_OVERBOUGHT_REJECTION")
            core += 1
        elif 32 < snap.rsi < 50 and snap.rsi < snap.prev_rsi:
            conf.append("RSI_BEARISH_MOMENTUM")
            core += 1

        if snap.histogram < 0.0 and snap.prev_histogram >= 0.0:
            conf.append("MACD_HISTOGRAM_FLIP")
            core += 1
        elif snap.macd_line < snap.signal_line and snap.prev_macd_line >= snap.prev_signal_line:
            conf.append("MACD_BEARISH_CROSS")
            core += 1
        elif snap.histogram < snap.prev_histogram and snap.histogram < 0.0:
            conf.append("MACD_INCREASING_SELL_MOMENTUM")
            core += 1

        bb_range = snap.bb_upper - snap.bb_lower
        if bb_range > 0.0:
            bb_pos = (snap.close - snap.bb_lower) / bb_range
            # 2.5σ bands — only fire at true exhaustion zone (> 90 % of band)
            if bb_pos > 0.90:
                conf.append("BB_NEAR_UPPER_BAND")
                core += 1
            elif snap.prev_close > snap.bb_middle and snap.close < snap.bb_middle:
                conf.append("BB_MIDDLE_BREAKDOWN")
                core += 1

        if snap.vol_ratio > 1.2:
            conf.append("VOLUME_ABOVE_AVERAGE")

        if snap.stoch_k > 75 and snap.stoch_k < snap.stoch_d and snap.prev_stoch_k >= snap.prev_stoch_d:
            conf.append("STOCH_RSI_BEARISH_CROSS")
        elif snap.stoch_k > 65 and snap.stoch_k < snap.prev_stoch_k:
            conf.append("STOCH_RSI_REJECTION")

        if candle == "BEARISH_ENGULFING":
            conf.append("BEARISH_ENGULFING")
        if pin == "SHOOTING_STAR":
            conf.append("SHOOTING_STAR")

        if fib is not None and fib.trend == "DOWNTREND":
            zone = AutoFibonacci.get_fib_zone(snap.close, fib)
            if zone:
                conf.append(zone)

        return conf, core

    @staticmethod
    def _assess_trend(snap: IndicatorSnapshot) -> str:
        """
        Determine macro trend strength from the EMA stack alignment.

        Args:
            snap: Indicator snapshot.

        Returns:
            One of ``'STRONG_BULL'``, ``'BULL'``, ``'NEUTRAL'``,
            ``'BEAR'``, or ``'STRONG_BEAR'``.
        """
        c = snap.close
        if c > snap.ema_9 > snap.ema_21 > snap.ema_50 > snap.ema_200:
            return "STRONG_BULL"
        if c > snap.ema_50 and snap.ema_9 > snap.ema_21:
            return "BULL"
        if c < snap.ema_9 < snap.ema_21 < snap.ema_50 < snap.ema_200:
            return "STRONG_BEAR"
        if c < snap.ema_50 and snap.ema_9 < snap.ema_21:
            return "BEAR"
        return "NEUTRAL"

    @staticmethod
    def _decide(
        buy_conf: list[str],
        buy_core: int,
        sell_conf: list[str],
        sell_core: int,
        trend: str,
        snap: IndicatorSnapshot,
        fib: Optional[FibonacciLevels],
    ) -> Optional[SignalResult]:
        """
        Apply the final decision logic: conflict resolution, trend filters, and
        minimum-threshold guards.

        Args:
            buy_conf:   BUY confluence condition list.
            buy_core:   BUY core group count.
            sell_conf:  SELL confluence condition list.
            sell_core:  SELL core group count.
            trend:      Current trend assessment string.
            snap:       Indicator snapshot (needed for entry price and ATR).
            fib:        Fibonacci levels for zone tagging.

        Returns:
            ``SignalResult`` or ``None``.
        """
        buy_score = len(buy_conf)
        sell_score = len(sell_conf)

        if buy_score >= ConfluenceEngine.MIN_CONFLUENCES and sell_score >= ConfluenceEngine.MIN_CONFLUENCES:
            logger.debug(f"Conflicting signals: BUY({buy_score}) vs SELL({sell_score}) — skip")
            return None

        if trend == "STRONG_BULL" and sell_score >= ConfluenceEngine.MIN_CONFLUENCES and buy_score < 2:
            logger.debug("Trend filter: STRONG_BULL blocking SELL")
            return None
        if trend == "STRONG_BEAR" and buy_score >= ConfluenceEngine.MIN_CONFLUENCES and sell_score < 2:
            logger.debug("Trend filter: STRONG_BEAR blocking BUY")
            return None

        fib_zone: Optional[str] = (
            AutoFibonacci.get_fib_zone(snap.close, fib) if fib else None
        )

        if (
            buy_score >= ConfluenceEngine.MIN_CONFLUENCES
            and buy_core >= ConfluenceEngine.CORE_REQUIRED
            and buy_score > sell_score
        ):
            confidence = min(buy_score / 8.0, 1.0)
            if trend in ("STRONG_BULL", "BULL"):
                confidence = min(confidence + 0.1, 1.0)
            return SignalResult(
                direction="BUY",
                confidence=confidence,
                confluences=buy_conf,
                entry_price=snap.close,
                atr_value=snap.atr,
                trend_strength=trend,
                fib_zone=fib_zone,
            )

        if (
            sell_score >= ConfluenceEngine.MIN_CONFLUENCES
            and sell_core >= ConfluenceEngine.CORE_REQUIRED
            and sell_score > buy_score
        ):
            confidence = min(sell_score / 8.0, 1.0)
            if trend in ("STRONG_BEAR", "BEAR"):
                confidence = min(confidence + 0.1, 1.0)
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
    def calculate_atr_levels(
        signal: SignalResult,
        df: pd.DataFrame,
    ) -> Optional[TradeLevels]:
        """
        Compute TP and SL levels using ATR-based dynamic sizing.

        Logic
        -----
        - SL multiplier is chosen by confidence:
          ≥ 0.75 → 1.2× (Aggressive), ≥ 0.55 → 1.5× (Normal), else 2.0× (Conservative).
        - TP1 = 2.0× ATR, TP2 = 3.5× ATR, TP3 = 5.5× ATR from entry.
        - Structural SL: the last 10-bar swing low/high is used as an
          additional anchor — whichever is more conservative wins.
        - Fibonacci-enhanced SL: if a 0.786 level exists beyond the swing SL,
          it further extends the stop to avoid premature exits.
        - Risk/Reward guard: if TP1/SL distance < 1.3 the trade is rejected.

        Args:
            signal: ``SignalResult`` from ``analyze()``.
            df:     The same OHLCV DataFrame used to generate the signal.

        Returns:
            ``TradeLevels`` or ``None`` if the R:R is below threshold.
        """
        if not signal or not signal.atr_value:
            return None

        atr = signal.atr_value
        entry = signal.entry_price
        conf = signal.confidence

        # Antigravity SL: base is always 1.5x ATR; confidence shifts the
        # bias label but the floor never drops below a safe 1.5x distance.
        if conf >= 0.75:
            sl_mult, position_bias = 1.5, "AGGRESSIVE"
        elif conf >= 0.55:
            sl_mult, position_bias = 1.5, "NORMAL"
        else:
            sl_mult, position_bias = 2.0, "CONSERVATIVE"

        sl_distance = atr * sl_mult
        tp1_dist = atr * 2.0
        tp2_dist = atr * 3.5
        tp3_dist = atr * 5.5

        fib = AutoFibonacci.compute(df, lookback=5)
        fib_tp: Optional[float] = None
        fib_sl: Optional[float] = None

        if signal.direction == "BUY":
            sl = entry - sl_distance
            tp_list = [
                round(entry + tp1_dist, 8),
                round(entry + tp2_dist, 8),
                round(entry + tp3_dist, 8),
            ]
            if fib and fib.trend == "UPTREND":
                if fib.swing_high > entry:
                    fib_tp = fib.swing_high
                struct_sl = fib.level_786 - atr * 0.3
                if 0.0 < struct_sl < sl:
                    fib_sl = struct_sl
                    sl = struct_sl

        elif signal.direction == "SELL":
            sl = entry + sl_distance
            tp_list = [
                round(entry - tp1_dist, 8),
                round(entry - tp2_dist, 8),
                round(entry - tp3_dist, 8),
            ]
            if fib and fib.trend == "DOWNTREND":
                if fib.swing_low < entry:
                    fib_tp = fib.swing_low
                struct_sl = fib.level_786 + atr * 0.3
                if struct_sl > sl:
                    fib_sl = struct_sl
                    sl = struct_sl
        else:
            return None

        try:
            swing_highs = df["high"].rolling(window=10, center=False).max()
            swing_lows = df["low"].rolling(window=10, center=False).min()
            last_sh = float(swing_highs.iloc[-1])
            last_sl_val = float(swing_lows.iloc[-1])

            if signal.direction == "BUY":
                swing_stop = last_sl_val - atr * 0.3
                if swing_stop < sl:
                    sl = swing_stop
            else:
                swing_stop = last_sh + atr * 0.3
                if swing_stop > sl:
                    sl = swing_stop
        except (IndexError, ValueError):
            pass

        sl = round(sl, 8)
        rr = tp1_dist / sl_distance if sl_distance > 0.0 else 0.0

        if rr < 1.3:
            logger.debug(f"R:R {rr:.2f} below minimum 1.3 — signal rejected")
            return None

        return TradeLevels(
            entry_prices=[round(entry, 8)],
            tp_list=tp_list,
            sl=sl,
            risk_reward_ratio=round(rr, 2),
            atr=round(atr, 8),
            position_bias=position_bias,
            fib_tp=round(fib_tp, 8) if fib_tp else None,
            fib_sl=round(fib_sl, 8) if fib_sl else None,
        )
