"""
Fast Parquet Candle Loader with Memory Caching.

Loads historical candle data from the local Parquet data lake with
instantaneous cached in-memory retrieval for multi-pass Monte Carlo runs.
"""

import os
from typing import Dict, Optional, Tuple

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
    """Clear in-memory candle cache."""
    _CANDLE_CACHE.clear()
