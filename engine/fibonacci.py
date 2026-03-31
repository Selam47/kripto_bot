import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FibonacciLevels:
    swing_high: float
    swing_low: float
    level_236: float
    level_382: float
    level_500: float
    level_618: float
    level_786: float
    trend: str


class AutoFibonacci:

    @staticmethod
    def find_swing_high(highs: np.ndarray, lookback: int = 5) -> tuple[int, float]:
        if len(highs) < lookback * 2 + 1:
            idx = int(np.argmax(highs))
            return idx, float(highs[idx])

        best_idx = -1
        best_val = -np.inf

        for i in range(lookback, len(highs) - lookback):
            window = highs[i - lookback : i + lookback + 1]
            if highs[i] == np.max(window) and highs[i] > best_val:
                best_val = highs[i]
                best_idx = i

        if best_idx == -1:
            best_idx = int(np.argmax(highs))
            best_val = float(highs[best_idx])

        return best_idx, float(best_val)

    @staticmethod
    def find_swing_low(lows: np.ndarray, lookback: int = 5) -> tuple[int, float]:
        if len(lows) < lookback * 2 + 1:
            idx = int(np.argmin(lows))
            return idx, float(lows[idx])

        best_idx = -1
        best_val = np.inf

        for i in range(lookback, len(lows) - lookback):
            window = lows[i - lookback : i + lookback + 1]
            if lows[i] == np.min(window) and lows[i] < best_val:
                best_val = lows[i]
                best_idx = i

        if best_idx == -1:
            best_idx = int(np.argmin(lows))
            best_val = float(lows[best_idx])

        return best_idx, float(best_val)

    @staticmethod
    def compute(df: pd.DataFrame, lookback: int = 5) -> Optional[FibonacciLevels]:
        if df is None or len(df) < 20:
            return None

        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)

        sh_idx, swing_high = AutoFibonacci.find_swing_high(highs, lookback)
        sl_idx, swing_low = AutoFibonacci.find_swing_low(lows, lookback)

        if swing_high <= swing_low:
            return None

        diff = swing_high - swing_low

        if sh_idx > sl_idx:
            trend = "UPTREND"
            level_236 = swing_high - diff * 0.236
            level_382 = swing_high - diff * 0.382
            level_500 = swing_high - diff * 0.500
            level_618 = swing_high - diff * 0.618
            level_786 = swing_high - diff * 0.786
        else:
            trend = "DOWNTREND"
            level_236 = swing_low + diff * 0.236
            level_382 = swing_low + diff * 0.382
            level_500 = swing_low + diff * 0.500
            level_618 = swing_low + diff * 0.618
            level_786 = swing_low + diff * 0.786

        return FibonacciLevels(
            swing_high=swing_high,
            swing_low=swing_low,
            level_236=level_236,
            level_382=level_382,
            level_500=level_500,
            level_618=level_618,
            level_786=level_786,
            trend=trend,
        )

    @staticmethod
    def is_near_golden_pocket(price: float, fib: FibonacciLevels, tolerance_pct: float = 0.5) -> bool:
        tol = price * (tolerance_pct / 100.0)
        if abs(price - fib.level_618) <= tol:
            return True
        if abs(price - fib.level_786) <= tol:
            return True
        return False

    @staticmethod
    def get_fib_zone(price: float, fib: FibonacciLevels) -> Optional[str]:
        tol = price * 0.003
        if abs(price - fib.level_618) <= tol:
            return "FIB_0.618_GOLDEN_POCKET"
        if abs(price - fib.level_786) <= tol:
            return "FIB_0.786_DEEP_RETRACE"
        if abs(price - fib.level_500) <= tol:
            return "FIB_0.500_MIDPOINT"
        if abs(price - fib.level_382) <= tol:
            return "FIB_0.382_SHALLOW"
        return None
