"""
engine — Signal Processing Package
=====================================
Exports all public engine components for convenient top-level imports.

    from engine import MarketData, TradingEngine, NotificationManager

Submodules
----------
market_data         Thread-safe singleton OHLCV store.
indicators          Vectorised technical indicator library.
confluence          Multi-indicator confluence scoring engine.
fibonacci           Auto-Fibonacci swing detection and level computation.
mtf_guard           Multi-timeframe trend guard.
trading_engine      Signal orchestrator and pipeline controller.
notification_manager Async Telegram gateway with command polling.
"""

from engine.confluence import ConfluenceEngine, SignalResult
from engine.fibonacci import AutoFibonacci, FibonacciLevels
from engine.indicators import Indicators, IndicatorSnapshot
from engine.market_data import MarketData
from engine.mtf_guard import MTFTrendGuard
from engine.notification_manager import NotificationManager
from engine.trading_engine import TradingEngine

__all__ = [
    "ConfluenceEngine",
    "SignalResult",
    "AutoFibonacci",
    "FibonacciLevels",
    "Indicators",
    "IndicatorSnapshot",
    "MarketData",
    "MTFTrendGuard",
    "NotificationManager",
    "TradingEngine",
]
