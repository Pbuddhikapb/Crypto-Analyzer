"""
Historical Kline Downloader and Parquet Data Lake Generator.

Downloads continuous, multi-year historical kline data for major cryptocurrency pairs
using resilient dual-endpoint failover (Binance US primary, Binance Global fallback).
Validates schema, checks data continuity and price hierarchy, and saves into columnar Parquet.
"""

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("downloader")

DEFAULT_SYMBOLS = [
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

DEFAULT_TIMEFRAMES = ["1h", "4h"]

BINANCE_ENDPOINTS = [
    "https://api.binance.us/api/v3/klines",
    "https://api.binance.com/api/v3/klines",
]


def timeframe_to_ms(timeframe: str) -> int:
    """Convert Binance timeframe interval string to milliseconds."""
    multipliers = {
        "m": 60 * 1000,
        "h": 3600 * 1000,
        "d": 86400 * 1000,
        "w": 7 * 86400 * 1000,
    }
    unit = timeframe[-1].lower()
    val = int(timeframe[:-1])
    return val * multipliers.get(unit, 60 * 1000)


def fetch_klines_batch(
    symbol: str,
    interval: str,
    start_time: int,
    end_time: Optional[int] = None,
    limit: int = 1000,
    endpoints: Optional[List[str]] = None,
    max_retries: int = 3,
) -> List[List[Any]]:
    """
    Fetch a single batch of klines using dual-endpoint failover and retry logic.
    Prioritizes Binance US (which avoids regional 451 blocks), with fallback to Binance Global.
    """
    eps = endpoints or BINANCE_ENDPOINTS
    sym = symbol.upper()

    params = f"symbol={sym}&interval={interval}&startTime={start_time}&limit={limit}"
    if end_time is not None:
        params += f"&endTime={end_time}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    last_error: Optional[Exception] = None

    for attempt in range(max_retries):
        for endpoint in eps:
            url = f"{endpoint}?{params}"
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=12) as response:
                    if response.status == 200:
                        raw_data = response.read().decode("utf-8")
                        data = json.loads(raw_data)
                        if isinstance(data, list):
                            return data
                        logger.warning(f"Unexpected non-list response from {endpoint}: {data}")
            except urllib.error.HTTPError as e:
                last_error = e
                # 451: Unavailable For Legal Reasons (geo-blocked), 403: Forbidden
                if e.code in (451, 403):
                    # Fail over silently to next endpoint
                    continue
                elif e.code == 429:
                    logger.warning(f"Rate limited (429) on {endpoint}. Backing off 2s...")
                    time.sleep(2.0)
                else:
                    logger.warning(f"HTTP error {e.code} from {endpoint}: {e.reason}")
            except (urllib.error.URLError, TimeoutError, ConnectionResetError) as e:
                last_error = e
                logger.warning(f"Network error querying {endpoint}: {e}")

        # Sleep with backoff before next attempt across all endpoints
        time.sleep(0.5 * (2**attempt))

    if last_error:
        logger.error(f"Failed to fetch batch for {sym} {interval} at {start_time}: {last_error}")
    return []


def verify_candle_integrity(df: pd.DataFrame, timeframe: str) -> Tuple[bool, List[str]]:
    """
    Audit candle DataFrame for schema compliance, null values, monotonicity,
    temporal continuity, and price/volume sanity.
    """
    issues: List[str] = []
    if df.empty:
        return False, ["DataFrame is completely empty"]

    required_cols = ["open_time", "datetime", "open", "high", "low", "close", "volume"]
    for col in required_cols:
        if col not in df.columns:
            issues.append(f"Missing required column: {col}")

    if issues:
        return False, issues

    # 1. Null check
    null_counts = df[required_cols].isnull().sum()
    for col, count in null_counts.items():
        if count > 0:
            issues.append(f"Column {col} has {count} null values")

    # 2. Monotonicity
    if not df["open_time"].is_monotonic_increasing:
        issues.append("open_time is not strictly monotonic increasing")

    # 3. Duplicate timestamps
    dups = df["open_time"].duplicated().sum()
    if dups > 0:
        issues.append(f"Found {dups} duplicate open_time timestamps")

    # 4. Price hierarchy check
    # High >= max(Open, Close)
    max_oc = np.maximum(df["open"].values, df["close"].values)
    high_violations = np.sum(df["high"].values < (max_oc - 1e-8))
    if high_violations > 0:
        issues.append(f"Found {high_violations} bars where high < max(open, close)")

    # Low <= min(Open, Close)
    min_oc = np.minimum(df["open"].values, df["close"].values)
    low_violations = np.sum(df["low"].values > (min_oc + 1e-8))
    if low_violations > 0:
        issues.append(f"Found {low_violations} bars where low > min(open, close)")

    # Positive prices
    if np.any(df["low"].values <= 0.0):
        issues.append("Found non-positive low prices")

    # Non-negative volume
    if np.any(df["volume"].values < 0.0):
        issues.append("Found negative volume values")

    # 5. Continuity / Step check
    interval_ms = timeframe_to_ms(timeframe)
    diffs = np.diff(df["open_time"].values)
    gaps = np.where(diffs != interval_ms)[0]
    if len(gaps) > 0:
        gap_details = []
        for g_idx in gaps[:5]:  # show up to 5 gaps
            t1 = df["open_time"].iloc[g_idx]
            t2 = df["open_time"].iloc[g_idx + 1]
            gap_details.append(f"gap of {t2 - t1}ms between {t1} and {t2}")
        issues.append(f"Found {len(gaps)} continuity gaps in {timeframe} sequence ({', '.join(gap_details)})")

    is_valid = len(issues) == 0
    return is_valid, issues


def download_candles(
    symbol: str,
    timeframe: str,
    start_time_ms: int,
    end_time_ms: Optional[int] = None,
    output_dir: str = "data/candles",
    rate_limit_delay: float = 0.05,
) -> pd.DataFrame:
    """
    Download a continuous time series of klines and save to Parquet.
    """
    os.makedirs(output_dir, exist_ok=True)
    sym = symbol.upper()
    interval_ms = timeframe_to_ms(timeframe)

    if end_time_ms is None:
        end_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    logger.info(
        f"Downloading {sym} [{timeframe}] from "
        f"{datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} to "
        f"{datetime.fromtimestamp(end_time_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')}"
    )

    all_rows: List[List[Any]] = []
    curr_start = start_time_ms

    while curr_start < end_time_ms:
        batch = fetch_klines_batch(
            symbol=sym,
            interval=timeframe,
            start_time=curr_start,
            end_time=end_time_ms,
            limit=1000,
        )

        if not batch:
            logger.warning(f"No more data returned for {sym} {timeframe} at {curr_start}")
            break

        all_rows.extend(batch)
        last_open_time = int(batch[-1][0])
        next_start = last_open_time + interval_ms

        if next_start <= curr_start:
            # Avoid infinite loop on stuck timestamp
            curr_start += interval_ms
        else:
            curr_start = next_start

        if rate_limit_delay > 0:
            time.sleep(rate_limit_delay)

    if not all_rows:
        logger.error(f"Download returned 0 candles for {sym} {timeframe}")
        return pd.DataFrame()

    # Parse raw kline rows into structured DataFrame
    # Raw format: [open_time, open, high, low, close, volume, close_time, quote_asset_volume, number_of_trades, ...]
    parsed_records = []
    for r in all_rows:
        parsed_records.append(
            {
                "open_time": int(r[0]),
                "datetime": pd.to_datetime(int(r[0]), unit="ms", utc=True),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
                "close_time": int(r[6]),
                "quote_volume": float(r[7]),
                "trades": int(r[8]),
            }
        )

    df = pd.DataFrame(parsed_records)

    # Deduplicate and sort
    df = df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

    # Validate integrity
    is_valid, issues = verify_candle_integrity(df, timeframe)
    if not is_valid:
        logger.warning(f"Data integrity warnings for {sym} {timeframe}: {issues}")
    else:
        logger.info(f"Integrity check PASSED for {sym} {timeframe}: {len(df)} continuous candles")

    # Save to Parquet atomically
    parquet_path = os.path.join(output_dir, f"{sym}_{timeframe}.parquet")
    tmp_path = os.path.join(output_dir, f".tmp_{sym}_{timeframe}.parquet")

    df.to_parquet(tmp_path, engine="pyarrow", compression="snappy", index=False)
    os.replace(tmp_path, parquet_path)
    logger.info(f"Saved {len(df)} candles to {parquet_path}")

    return df


def download_universe(
    symbols: Optional[List[str]] = None,
    timeframes: Optional[List[str]] = None,
    lookback_days: int = 730,
    output_dir: str = "data/candles",
) -> Dict[str, pd.DataFrame]:
    """
    Download historical candle datasets for the entire universe.
    """
    syms = symbols or DEFAULT_SYMBOLS
    tfs = timeframes or DEFAULT_TIMEFRAMES
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = now_ms - (lookback_days * 86400 * 1000)

    results: Dict[str, pd.DataFrame] = {}

    for sym in syms:
        for tf in tfs:
            key = f"{sym}_{tf}"
            logger.info(f"=== Processing {key} ===")
            df = download_candles(
                symbol=sym,
                timeframe=tf,
                start_time_ms=start_ms,
                end_time_ms=now_ms,
                output_dir=output_dir,
            )
            results[key] = df

    return results


def main():
    parser = argparse.ArgumentParser(description="Download historical candles into Parquet data lake.")
    parser.add_argument(
        "--symbols",
        type=str,
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated list of symbols (default: 10 liquid coins)",
    )
    parser.add_argument(
        "--timeframes",
        type=str,
        default=",".join(DEFAULT_TIMEFRAMES),
        help="Comma-separated list of timeframes (default: 1h,4h)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=730,
        help="Number of lookback days (default: 730 = 2 years)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/candles",
        help="Directory to save Parquet files (default: data/candles)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify existing Parquet files without downloading",
    )

    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip().lower() for t in args.timeframes.split(",") if t.strip()]

    if args.verify_only:
        all_passed = True
        for sym in symbols:
            for tf in timeframes:
                path = os.path.join(args.output_dir, f"{sym}_{tf}.parquet")
                if not os.path.exists(path):
                    print(f"[MISSING] {path} does not exist.")
                    all_passed = False
                    continue
                df = pd.read_parquet(path)
                valid, issues = verify_candle_integrity(df, tf)
                if valid:
                    print(f"[OK] {sym}_{tf}: {len(df)} candles, 0 integrity issues.")
                else:
                    print(f"[FAIL] {sym}_{tf}: {issues}")
                    all_passed = False
        sys.exit(0 if all_passed else 1)

    download_universe(
        symbols=symbols,
        timeframes=timeframes,
        lookback_days=args.days,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
