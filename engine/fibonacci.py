"""
Auto-Fibonacci Module
=======================
Dynamically identifies the most significant swing high and swing low within a
look-back window and computes the full set of Fibonacci retracement levels,
with emphasis on the **0.618 Golden Pocket** and **0.786 Deep Retracement**
zones that professional traders use for high-probability entries.

Usage
-----
    fib = AutoFibonacci.compute(df, lookback=5)
    if fib and AutoFibonacci.is_near_golden_pocket(last_price, fib):
        # Price is at a high-confluence Fibonacci zone
        ...
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FibonacciLevels:
    """
    Immutable Fibonacci retracement level set derived from a detected swing.

    Attributes
    ----------
    swing_high : float
        The highest price in the detected swing structure.
    swing_low : float
        The lowest price in the detected swing structure.
    level_236 : float
        23.6 % retracement level.
    level_382 : float
        38.2 % retracement level — shallow retracement zone.
    level_500 : float
        50.0 % midpoint (not a true Fibonacci ratio but widely observed).
    level_618 : float
        61.8 % Golden Ratio — the Golden Pocket entry zone.
    level_786 : float
        78.6 % deep retracement level.
    trend : str
        ``'UPTREND'`` if the swing high formed *after* the swing low (price
        rallied), ``'DOWNTREND'`` otherwise.
    """

    swing_high: float
    swing_low: float
    level_236: float
    level_382: float
    level_500: float
    level_618: float
    level_786: float
    trend: str


class AutoFibonacci:
    """
    Dynamic swing-detection and Fibonacci retracement calculator.

    The swing detection uses a local-extremum scan: a bar is considered a
    swing high (or low) only when it is the maximum (or minimum) within a
    symmetric window of ``lookback`` bars on each side.  The *most extreme*
    qualifying bar in the entire series is selected so that the levels are
    anchored to the dominant structure.
    """

    @staticmethod
    def find_swing_high(highs: np.ndarray, lookback: int = 5) -> tuple[int, float]:
        """
        Find the index and value of the most significant swing high.

        A bar qualifies as a swing high when its value equals the rolling
        maximum over the window ``[i - lookback, i + lookback]``.  Among all
        qualifying bars, the global maximum is returned.

        Args:
            highs:    1-D numpy array of bar high prices.
            lookback: Half-window size for local extremum detection (default 5).

        Returns:
            Tuple of (index, value) for the dominant swing high.
        """
        if len(highs) < lookback * 2 + 1:
            idx = int(np.argmax(highs))
            return idx, float(highs[idx])

        best_idx, best_val = -1, -np.inf
        for i in range(lookback, len(highs) - lookback):
            window = highs[i - lookback: i + lookback + 1]
            if highs[i] == np.max(window) and highs[i] > best_val:
                best_val = highs[i]
                best_idx = i

        if best_idx == -1:
            best_idx = int(np.argmax(highs))
            best_val = float(highs[best_idx])

        return best_idx, float(best_val)

    @staticmethod
    def find_swing_low(lows: np.ndarray, lookback: int = 5) -> tuple[int, float]:
        """
        Find the index and value of the most significant swing low.

        Mirrors ``find_swing_high`` but searches for the global minimum
        among locally qualifying bars.

        Args:
            lows:     1-D numpy array of bar low prices.
            lookback: Half-window size for local extremum detection (default 5).

        Returns:
            Tuple of (index, value) for the dominant swing low.
        """
        if len(lows) < lookback * 2 + 1:
            idx = int(np.argmin(lows))
            return idx, float(lows[idx])

        best_idx, best_val = -1, np.inf
        for i in range(lookback, len(lows) - lookback):
            window = lows[i - lookback: i + lookback + 1]
            if lows[i] == np.min(window) and lows[i] < best_val:
                best_val = lows[i]
                best_idx = i

        if best_idx == -1:
            best_idx = int(np.argmin(lows))
            best_val = float(lows[best_idx])

        return best_idx, float(best_val)

    @staticmethod
    def compute(df: pd.DataFrame, lookback: int = 5) -> Optional[FibonacciLevels]:
        """
        Detect the dominant swing structure and compute retracement levels.

        The trend direction is determined by the *relative position* of the
        swing high vs. swing low indices:

        - ``swing_high_idx > swing_low_idx`` → price rallied (UPTREND);
          retracement levels descend from the high.
        - ``swing_high_idx < swing_low_idx`` → price fell (DOWNTREND);
          retracement levels ascend from the low.

        Args:
            df:       OHLCV DataFrame with at least 20 rows and ``high`` / ``low``
                      columns.
            lookback: Local-extremum detection half-window (default 5).

        Returns:
            ``FibonacciLevels`` dataclass or ``None`` if the data is
            insufficient or the swing range is degenerate (high ≤ low).
        """
        if df is None or len(df) < 20:
            return None

        highs = df["high"].to_numpy(dtype=float)
        lows = df["low"].to_numpy(dtype=float)

        sh_idx, swing_high = AutoFibonacci.find_swing_high(highs, lookback)
        sl_idx, swing_low = AutoFibonacci.find_swing_low(lows, lookback)

        if swing_high <= swing_low:
            return None

        diff = swing_high - swing_low

        if sh_idx > sl_idx:
            trend = "UPTREND"
            base = swing_high
            sign = -1.0
        else:
            trend = "DOWNTREND"
            base = swing_low
            sign = 1.0

        return FibonacciLevels(
            swing_high=swing_high,
            swing_low=swing_low,
            level_236=base + sign * diff * 0.236,
            level_382=base + sign * diff * 0.382,
            level_500=base + sign * diff * 0.500,
            level_618=base + sign * diff * 0.618,
            level_786=base + sign * diff * 0.786,
            trend=trend,
        )

    @staticmethod
    def is_near_golden_pocket(
        price: float,
        fib: FibonacciLevels,
        tolerance_pct: float = 0.5,
    ) -> bool:
        """
        Return ``True`` when ``price`` is within ``tolerance_pct`` % of either
        the 0.618 or 0.786 Fibonacci level.

        Args:
            price:         Current market price.
            fib:           Pre-computed ``FibonacciLevels`` object.
            tolerance_pct: Percentage distance tolerance (default 0.5 %).

        Returns:
            ``True`` if price is inside the golden pocket zone.
        """
        tol = price * (tolerance_pct / 100.0)
        return abs(price - fib.level_618) <= tol or abs(price - fib.level_786) <= tol

    @staticmethod
    def get_fib_zone(price: float, fib: FibonacciLevels, tol_pct: float = 0.3) -> Optional[str]:
        """
        Return a named Fibonacci zone tag if ``price`` is within ``tol_pct`` %
        of any major retracement level.

        Args:
            price:   Current market price.
            fib:     Pre-computed ``FibonacciLevels``.
            tol_pct: Percentage distance tolerance (default 0.3 %).

        Returns:
            One of ``'FIB_0.618_GOLDEN_POCKET'``, ``'FIB_0.786_DEEP_RETRACE'``,
            ``'FIB_0.500_MIDPOINT'``, ``'FIB_0.382_SHALLOW'``, or ``None``.
        """
        tol = price * (tol_pct / 100.0)
        if abs(price - fib.level_618) <= tol:
            return "FIB_0.618_GOLDEN_POCKET"
        if abs(price - fib.level_786) <= tol:
            return "FIB_0.786_DEEP_RETRACE"
        if abs(price - fib.level_500) <= tol:
            return "FIB_0.500_MIDPOINT"
        if abs(price - fib.level_382) <= tol:
            return "FIB_0.382_SHALLOW"
        return None
