"""
Fast Parquet Candle Loader with Memory Caching.

Loads historical candle data from the local Parquet data lake with
instantaneous cached in-memory retrieval for multi-pass Monte Carlo runs.
"""

import os
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

DEVELOPMENT_END = "2026-04-18"

_CANDLE_CACHE: Dict[Tuple[str, str], pd.DataFrame] = {}


def load_candles(
    symbol: str,
    timeframe: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    data_dir: str = "data/candles",
    use_cache: bool = True,
    allow_holdout: bool = False,
) -> pd.DataFrame:
    """
    Load historical candles from Parquet data lake into a clean DataFrame.

    Args:
        symbol: e.g. 'BTCUSDT'
        timeframe: '1h' or '4h'
        start_date: optional ISO date string, e.g. '2024-11-01'
        end_date: optional ISO date string, e.g. '2026-10-01'
        data_dir: path to candle lake
        use_cache: whether to store and retrieve from in-memory cache
        allow_holdout: whether to allow candles past DEVELOPMENT_END (default: False)

    Returns:
        DataFrame sorted by open_time with datetime as index or column.
    """
    sym = symbol.upper()
    tf = timeframe.lower()
    cache_key = (sym, tf)

    if use_cache and cache_key in _CANDLE_CACHE:
        df = _CANDLE_CACHE[cache_key].copy()
    else:
        file_path = os.path.join(data_dir, f"{sym}_{tf}.parquet")
        if not os.path.exists(file_path):
            raise FileNotFoundError(
                f"Candle data for {sym} {tf} not found at {file_path}. "
                f"Run `python -m data.downloader --symbols {sym} --timeframes {tf}` first."
            )

        df = pd.read_parquet(file_path)
        if "datetime" not in df.columns and "open_time" in df.columns:
            df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)

        if use_cache:
            _CANDLE_CACHE[cache_key] = df.copy()

    # Apply date filters if specified
    if start_date is not None:
        start_dt = pd.to_datetime(start_date, utc=True)
        df = df[df["datetime"] >= start_dt]

    # Holdout protection: default returns only data up to DEVELOPMENT_END
    if not allow_holdout:
        dev_end_dt = pd.to_datetime(DEVELOPMENT_END, utc=True)
        if end_date is not None:
            user_end_dt = pd.to_datetime(end_date, utc=True)
            effective_end_dt = min(user_end_dt, dev_end_dt)
        else:
            effective_end_dt = dev_end_dt
        df = df[df["datetime"] <= effective_end_dt]
    elif end_date is not None:
        end_dt = pd.to_datetime(end_date, utc=True)
        df = df[df["datetime"] <= end_dt]

    return df.reset_index(drop=True)


def clear_cache():
    """Clear in-memory candle and funding rate caches."""
    _CANDLE_CACHE.clear()
    _FUNDING_CACHE.clear()


_FUNDING_CACHE: Dict[str, pd.DataFrame] = {}


def load_funding_rates(
    symbol: str,
    data_dir: str = "data/funding",
    use_cache: bool = True,
) -> Optional[pd.DataFrame]:
    """
    Load historical funding rate records for a symbol from the Parquet data lake.

    Returns DataFrame with columns ['calc_time', 'datetime', 'funding_interval_hours', 'last_funding_rate', 'symbol']
    or None if the archive does not exist.
    """
    sym = symbol.upper()
    if use_cache and sym in _FUNDING_CACHE:
        return _FUNDING_CACHE[sym].copy()

    file_path = os.path.join(data_dir, f"{sym}.parquet")
    if not os.path.exists(file_path):
        return None

    df = pd.read_parquet(file_path)
    if use_cache:
        _FUNDING_CACHE[sym] = df.copy()

    return df.reset_index(drop=True)


def clear_funding_cache():
    """Clear in-memory funding rate cache."""
    _FUNDING_CACHE.clear()


def candles_to_array(df: pd.DataFrame) -> np.ndarray:
    """Convert candle DataFrame to contiguous numpy array [open, high, low, close, open_time, volume]."""
    return df[["open", "high", "low", "close", "open_time", "volume"]].to_numpy(dtype=np.float64)



def compute_dataset_hash(candles_dict: Dict[str, pd.DataFrame | pd.Series | object]) -> str:
    """Deterministic hash of dataset symbols, lengths, and boundary timestamps."""
    import hashlib
    import numpy as np

    tokens = []
    for s in sorted(candles_dict.keys()):
        arr = candles_dict[s]
        if hasattr(arr, "to_numpy"):
            arr = arr[["open", "high", "low", "close", "open_time"]].to_numpy(dtype=np.float64)
        n = len(arr)
        t_first = int(arr[0, 4]) if n > 0 else 0
        t_last = int(arr[-1, 4]) if n > 0 else 0
        tokens.append(f"{s}:{n}:{t_first}:{t_last}")
    raw = "|".join(tokens)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def get_current_dataset_hash(
    symbols: Optional[list] = None,
    timeframe: str = "1h",
    data_dir: str = "data/candles",
    allow_holdout: bool = False,
) -> str:
    """Load default development dataset and compute its deterministic hash."""
    import numpy as np

    syms = symbols or [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "BNBUSDT",
        "XRPUSDT",
        "DOGEUSDT",
        "ADAUSDT",
        "AVAXUSDT",
        "LINKUSDT",
        "SUIUSDT",
    ]
    candles_dict = {}
    for sym in syms:
        df = load_candles(sym, timeframe, data_dir=data_dir, allow_holdout=allow_holdout)
        candles_dict[sym] = df[["open", "high", "low", "close", "open_time"]].to_numpy(dtype=np.float64)
    return compute_dataset_hash(candles_dict)

