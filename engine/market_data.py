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


class MarketData:

    _instance: Optional["MarketData"] = None
    _init_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
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
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._initialized = True

        self.binance_client = binance_client
        self.symbol_manager = symbol_manager
        self.klines: dict[tuple[str, str], pd.DataFrame] = {}
        self.historical_loaded: dict[tuple[str, str], bool] = {}
        self.has_historical_loader = self._check_loader()
        self._lock = threading.RLock()
        self.symbols_with_signals: set[str] = set()
        self.max_lazy_load_symbols = config.MAX_LAZY_LOAD_SYMBOLS
        self.lazy_loading_enabled = config.LAZY_LOADING_ENABLED
        self.max_concurrent_loads = config.MAX_CONCURRENT_LOADS
        self._loading_queue: set[tuple[str, str]] = set()
        self.db = get_database() if config.DB_ENABLE_PERSISTENCE else None

    def _check_loader(self) -> bool:
        try:
            return self.binance_client.load_historical_data is not None
        except (AttributeError, ImportError):
            logger.warning("Historical data loader not available")
            return False

    def initialize_historical(self):
        if not self.has_historical_loader:
            logger.info("No historical loader — using real-time data only")
            return

        symbols = self.symbol_manager.get_symbols()
        if not symbols:
            logger.error("Empty symbol list, cannot load history")
            return

        if self.lazy_loading_enabled:
            if config.SYMBOLS:
                to_load = config.SYMBOLS[: self.max_lazy_load_symbols]
                logger.info(f"Lazy loading: preloading {len(to_load)} configured symbols")
                self._bulk_load(to_load)
            else:
                logger.info(f"Lazy loading enabled, {len(symbols)} symbols available on-demand")
        else:
            logger.warning("Loading ALL symbols — high memory usage expected")
            self._bulk_load(symbols)

    def _bulk_load(self, symbols: list[str]):
        tasks = []
        for symbol in symbols:
            for interval in config.TIMEFRAMES:
                key = (symbol, interval)
                if not self.historical_loaded.get(key, False):
                    tasks.append((symbol, interval, key))

        if not tasks:
            logger.info("All historical data already cached")
            return

        logger.info(f"Bulk loading {len(tasks)} pairs with {self.max_concurrent_loads} workers")
        start = time.time()
        ok = 0

        with ThreadPoolExecutor(max_workers=self.max_concurrent_loads) as pool:
            future_map = {
                pool.submit(self._load_single, sym, itv): (sym, itv, k)
                for sym, itv, k in tasks
            }
            for future in as_completed(future_map):
                sym, itv, k = future_map[future]
                try:
                    result_df = future.result()
                    if result_df is not None and not result_df.empty:
                        with self._lock:
                            self.klines[k] = result_df
                            self.historical_loaded[k] = True
                        ok += 1
                except Exception as e:
                    logger.error(f"Load error {sym}-{itv}: {e}")

        logger.info(f"Bulk load done in {time.time()-start:.2f}s — {ok}/{len(tasks)} ok")

    def _load_single(self, symbol: str, interval: str) -> Optional[pd.DataFrame]:
        try:
            if self.db:
                db_data = self.db.load_historical_data(symbol, interval, limit=config.HISTORY_CANDLES)
                if not db_data.empty and len(db_data) >= config.HISTORY_CANDLES * 0.8:
                    return db_data

            optimal = self.binance_client.get_optimal_klines_limit(config.HISTORY_CANDLES)
            api_data = self.binance_client.load_historical_data(symbol, interval, limit=optimal)

            if self.db and api_data is not None and not api_data.empty:
                self.db.store_historical_data(symbol, interval, api_data)

            return api_data
        except Exception as e:
            logger.error(f"Historical load failed {symbol}-{interval}: {e}")
            return None

    def lazy_load(self, symbol: str, interval: str) -> bool:
        if not self.has_historical_loader:
            return False

        key = (symbol, interval)
        if self.historical_loaded.get(key, False):
            return True

        if len(self.symbols_with_signals) >= self.max_lazy_load_symbols:
            return False

        if key in self._loading_queue:
            return False

        self._loading_queue.add(key)
        try:
            result_df = self._load_single(symbol, interval)
            if result_df is not None and not result_df.empty:
                with self._lock:
                    self.klines[key] = result_df
                    self.historical_loaded[key] = True
                    self.symbols_with_signals.add(symbol)
                logger.info(f"Lazy loaded {len(result_df)} candles for {symbol}-{interval}")
                return True
            return False
        except Exception as e:
            logger.error(f"Lazy load error {symbol}-{interval}: {e}")
            return False
        finally:
            self._loading_queue.discard(key)

    def update_kline(self, k: dict):
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
                self.klines[key] = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

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
                self.klines[key] = self.klines[key].iloc[df_len - config.HISTORY_CANDLES :]

    def get_klines(self, symbol: str, interval: str) -> pd.DataFrame:
        with self._lock:
            data = self.klines.get((symbol, interval), pd.DataFrame())
            if not isinstance(data, pd.DataFrame):
                logger.error(f"Corrupted data for {symbol}-{interval}, resetting")
                self.klines[(symbol, interval)] = pd.DataFrame(
                    columns=["open", "high", "low", "close", "volume"]
                )
                return pd.DataFrame()
            return data.copy()

    def get_clean_klines(self, symbol: str, interval: str) -> pd.DataFrame:
        with self._lock:
            df = self.klines.get((symbol, interval), pd.DataFrame())
            if df.empty:
                return df
            clean = df.copy()

        clean = clean.dropna()
        required = ["open", "high", "low", "close", "volume"]
        if not all(c in clean.columns for c in required):
            return pd.DataFrame()

        invalid = (
            (clean["high"] < clean[["open", "close"]].max(axis=1))
            | (clean["low"] > clean[["open", "close"]].min(axis=1))
            | (clean["high"] <= 0)
            | (clean["low"] <= 0)
            | (clean["open"] <= 0)
            | (clean["close"] <= 0)
        )
        if invalid.any():
            clean = clean[~invalid]

        clean = clean.sort_index()
        clean = clean[~clean.index.duplicated(keep="last")]
        return clean
