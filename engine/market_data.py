"""
MarketData — Singleton OHLCV Store
=====================================
Central, thread-safe store for all live and historical kline data.

Design
------
- **Singleton**: only one instance exists per process, ensuring all components
  share the same in-memory price cache without duplicating data.
- **Lazy loading**: historical data for a symbol/interval pair is fetched from
  the Binance API (or the local SQLite cache) only when the pair first receives
  a live kline — preventing memory bloat with hundreds of symbols.
- **Bulk loading**: when explicit ``SYMBOLS`` are configured, an initial
  concurrent bulk load pre-warms the cache using a thread pool.
- **Memory management**: the kline store is capped at ``HISTORY_CANDLES``
  rows per key; oldest bars are dropped automatically.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import pandas as pd

import config
from binance_future_client import BinanceFuturesClient
from symbol_manager import SymbolManager
from database import get_database

logger = logging.getLogger(__name__)

_OHLCV_COLS = ["open", "high", "low", "close", "volume"]


class MarketData:
    """
    Thread-safe, singleton OHLCV data store.

    Attributes
    ----------
    klines : dict[tuple[str, str], pd.DataFrame]
        Live kline data keyed by ``(symbol, interval)``.
    historical_loaded : dict[tuple[str, str], bool]
        Flags indicating whether historical data has been loaded for each key.
    has_historical_loader : bool
        ``True`` when ``BinanceFuturesClient.load_historical_data`` is available.
    """

    _instance: Optional["MarketData"] = None
    _init_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """Enforce singleton pattern with double-checked locking."""
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        binance_client: BinanceFuturesClient,
        symbol_manager: SymbolManager,
    ):
        """
        Initialise the store.  Safe to call multiple times — subsequent calls
        are no-ops because of the singleton guard.

        Args:
            binance_client: Authenticated Binance Futures REST client.
            symbol_manager: Provides the current symbol list.
        """
        if getattr(self, "_initialized", False):
            return
        self._initialized = True

        self.binance_client = binance_client
        self.symbol_manager = symbol_manager
        self.klines: dict[tuple[str, str], pd.DataFrame] = {}
        self.historical_loaded: dict[tuple[str, str], bool] = {}
        self.has_historical_loader = self._check_loader()
        self._lock = threading.RLock()
        self.symbols_with_signals: set[str] = set()
        self.max_lazy_load_symbols: int = config.MAX_LAZY_LOAD_SYMBOLS
        self.lazy_loading_enabled: bool = config.LAZY_LOADING_ENABLED
        self.max_concurrent_loads: int = config.MAX_CONCURRENT_LOADS
        self._loading_queue: set[tuple[str, str]] = set()
        self.db = get_database() if config.DB_ENABLE_PERSISTENCE else None

    def _check_loader(self) -> bool:
        """
        Verify that the Binance client exposes a ``load_historical_data`` method.

        Returns:
            ``True`` if historical loading is available.
        """
        try:
            return callable(self.binance_client.load_historical_data)
        except AttributeError:
            logger.warning("BinanceFuturesClient.load_historical_data not available")
            return False

    def initialize_historical(self):
        """
        Run the initial historical data loading strategy.

        Behaviour depends on ``LAZY_LOADING_ENABLED``:
        - **Lazy (default)**: pre-load only the symbols listed in ``SYMBOLS``;
          all others are fetched on demand.
        - **Eager**: load every symbol in the symbol list — not recommended
          for more than ~30 symbols.
        """
        if not self.has_historical_loader:
            logger.info("No historical loader available — using real-time data only")
            return

        symbols = self.symbol_manager.get_symbols()
        if not symbols:
            logger.error("Symbol list is empty — cannot pre-load historical data")
            return

        if self.lazy_loading_enabled:
            if config.SYMBOLS:
                to_load = config.SYMBOLS[: self.max_lazy_load_symbols]
                logger.info(f"Lazy loading: pre-warming {len(to_load)} configured symbols")
                self._bulk_load(to_load)
            else:
                logger.info(
                    f"Lazy loading enabled with {len(symbols)} auto-fetched symbols — "
                    "data will be loaded on-demand"
                )
        else:
            logger.warning(
                f"Eager loading ALL {len(symbols)} symbols — "
                "consider enabling LAZY_LOADING_ENABLED for large lists"
            )
            self._bulk_load(symbols)

    def _bulk_load(self, symbols: list[str]):
        """
        Concurrently load historical data for a list of symbols.

        Uses a ``ThreadPoolExecutor`` to fire off multiple API calls in parallel,
        respecting ``MAX_CONCURRENT_LOADS``.

        Args:
            symbols: List of symbol strings (e.g. ``['BTCUSDT', 'ETHUSDT']``).
        """
        tasks = [
            (sym, itv, (sym, itv))
            for sym in symbols
            for itv in config.TIMEFRAMES
            if not self.historical_loaded.get((sym, itv), False)
        ]

        if not tasks:
            logger.info("Historical data already cached for all requested symbols")
            return

        logger.info(
            f"Bulk loading {len(tasks)} symbol/interval pairs "
            f"using {self.max_concurrent_loads} parallel workers"
        )
        t0 = time.monotonic()
        ok = 0

        with ThreadPoolExecutor(max_workers=self.max_concurrent_loads) as pool:
            future_map = {
                pool.submit(self._load_single, sym, itv): (sym, itv, key)
                for sym, itv, key in tasks
            }
            for future in as_completed(future_map):
                sym, itv, key = future_map[future]
                try:
                    df = future.result()
                    if df is not None and not df.empty:
                        with self._lock:
                            self.klines[key] = df
                            self.historical_loaded[key] = True
                        ok += 1
                except Exception as e:
                    logger.error(f"Bulk load error {sym}-{itv}: {e}")

        elapsed = time.monotonic() - t0
        logger.info(
            f"Bulk load complete in {elapsed:.2f}s — {ok}/{len(tasks)} pairs loaded"
        )

    def _load_single(
        self, symbol: str, interval: str
    ) -> Optional[pd.DataFrame]:
        """
        Load historical data for one symbol/interval from DB cache or API.

        Tries the local SQLite database first; falls back to the Binance REST
        API and caches the result for future restarts.

        Args:
            symbol:   Trading pair (e.g. ``'BTCUSDT'``).
            interval: Timeframe string (e.g. ``'1h'``).

        Returns:
            OHLCV DataFrame or ``None`` on failure.
        """
        try:
            if self.db:
                cached = self.db.load_historical_data(
                    symbol, interval, limit=config.HISTORY_CANDLES
                )
                if not cached.empty and len(cached) >= config.HISTORY_CANDLES * 0.8:
                    logger.debug(f"DB cache hit: {len(cached)} bars for {symbol}-{interval}")
                    return cached

            optimal = self.binance_client.get_optimal_klines_limit(config.HISTORY_CANDLES)
            api_df = self.binance_client.load_historical_data(
                symbol, interval, limit=optimal
            )

            if self.db and api_df is not None and not api_df.empty:
                self.db.store_historical_data(symbol, interval, api_df)
                logger.debug(f"Cached {len(api_df)} bars to DB for {symbol}-{interval}")

            return api_df

        except Exception as e:
            logger.error(f"Historical load failed {symbol}-{interval}: {e}")
            return None

    def lazy_load(self, symbol: str, interval: str) -> bool:
        """
        On-demand load of historical data for a symbol/interval that just
        received its first live kline.

        A loading-queue set prevents duplicate concurrent requests for the same
        key.  The lazy-load symbol limit (``MAX_LAZY_LOAD_SYMBOLS``) is
        respected to cap memory usage.

        Args:
            symbol:   Trading pair.
            interval: Timeframe string.

        Returns:
            ``True`` if data was loaded successfully or was already present.
        """
        if not self.has_historical_loader:
            return False

        key = (symbol, interval)
        if self.historical_loaded.get(key, False):
            return True
        if len(self.symbols_with_signals) >= self.max_lazy_load_symbols:
            logger.warning(
                f"Lazy-load limit ({self.max_lazy_load_symbols}) reached — "
                f"skipping {symbol}-{interval}"
            )
            return False
        if key in self._loading_queue:
            return False

        self._loading_queue.add(key)
        try:
            df = self._load_single(symbol, interval)
            if df is not None and not df.empty:
                with self._lock:
                    self.klines[key] = df
                    self.historical_loaded[key] = True
                    self.symbols_with_signals.add(symbol)
                logger.info(f"Lazy loaded {len(df)} bars for {symbol}-{interval}")
                return True
            return False
        except Exception as e:
            logger.error(f"Lazy load error {symbol}-{interval}: {e}")
            return False
        finally:
            self._loading_queue.discard(key)

    def update_kline(self, k: dict):
        """
        Apply a live WebSocket kline update to the in-memory store.

        For existing timestamps the row is updated in-place; new timestamps
        are appended.  The store is capped at ``HISTORY_CANDLES`` rows.

        Args:
            k: Raw kline dict from the Binance WebSocket stream with keys
               ``s`` (symbol), ``i`` (interval), ``t`` (open time ms),
               ``o``, ``h``, ``l``, ``c``, ``v``.
        """
        symbol, interval = k["s"], k["i"]
        key = (symbol, interval)
        ts = pd.to_datetime(k["t"], unit="ms")

        new_row = {
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
        }

        with self._lock:
            if key not in self.klines:
                self.klines[key] = pd.DataFrame(columns=_OHLCV_COLS)

            df = self.klines[key]

            if ts in df.index:
                for col, val in new_row.items():
                    df.at[ts, col] = val
            else:
                if df.empty:
                    self.klines[key] = pd.DataFrame([new_row], index=[ts])
                else:
                    df.loc[ts] = new_row
                    if len(df) > 1 and ts < df.index[-2]:
                        df.sort_index(inplace=True)

            df_len = len(self.klines[key])
            if df_len > config.HISTORY_CANDLES:
                self.klines[key] = self.klines[key].iloc[df_len - config.HISTORY_CANDLES:]

    def get_klines(self, symbol: str, interval: str) -> pd.DataFrame:
        """
        Return a defensive copy of the kline DataFrame for a symbol/interval.

        Args:
            symbol:   Trading pair.
            interval: Timeframe string.

        Returns:
            Copy of the stored DataFrame, or an empty DataFrame if not found.
            Never returns ``None``.
        """
        with self._lock:
            data = self.klines.get((symbol, interval), pd.DataFrame())
            if not isinstance(data, pd.DataFrame):
                logger.error(
                    f"Data corruption for {symbol}-{interval} "
                    f"(type: {type(data)}) — resetting to empty DataFrame"
                )
                self.klines[(symbol, interval)] = pd.DataFrame(columns=_OHLCV_COLS)
                return pd.DataFrame()
            return data.copy()

    def get_clean_klines(self, symbol: str, interval: str) -> pd.DataFrame:
        """
        Return a cleaned and validated copy of the kline DataFrame.

        Cleaning steps:
        1. Drop rows with any NaN values.
        2. Verify all required OHLCV columns are present.
        3. Remove rows with invalid OHLC relationships (high < max(O,C), etc.).
        4. Sort by timestamp index and deduplicate.

        Intended for use by the charting service which is sensitive to bad data.

        Args:
            symbol:   Trading pair.
            interval: Timeframe string.

        Returns:
            Cleaned OHLCV DataFrame, potentially shorter than the raw store.
        """
        with self._lock:
            raw = self.klines.get((symbol, interval), pd.DataFrame())
            if raw.empty:
                return raw
            clean = raw.copy()

        clean = clean.dropna()

        if not all(c in clean.columns for c in _OHLCV_COLS):
            logger.warning(f"Missing OHLCV columns for {symbol}-{interval}")
            return pd.DataFrame()

        invalid = (
            (clean["high"] < clean[["open", "close"]].max(axis=1))
            | (clean["low"] > clean[["open", "close"]].min(axis=1))
            | (clean[["high", "low", "open", "close"]] <= 0).any(axis=1)
        )
        if invalid.any():
            logger.debug(
                f"Removed {invalid.sum()} invalid OHLC rows from {symbol}-{interval}"
            )
            clean = clean[~invalid]

        clean = clean.sort_index()
        clean = clean[~clean.index.duplicated(keep="last")]
        return clean
