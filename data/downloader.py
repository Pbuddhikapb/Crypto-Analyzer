"""
Historical Binance USD-M Futures Kline Downloader and Parquet Data Lake Generator.

Streams official multi-year historical kline data for major cryptocurrency pairs directly
from data.binance.vision (Amazon S3 / CloudFront) with in-memory decompression.
Guarantees authentic USD-M Futures volume, continuous liquidity, zero-trade prevention,
strict schema standardization, and provenance metadata (source='binance_futures_um').
"""

import argparse
import hashlib
import io
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
import zipfile
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

FUTURES_ARCHIVE_BASE = "https://data.binance.vision/data/futures/um"

STANDARD_COLUMNS = [
    "open_time",
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "source",
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


def parse_kline_csv(csv_bytes: bytes) -> pd.DataFrame:
    """
    Parse a raw kline CSV from Binance Vision zip file, handling both headered
    and unheadered variants, and standardizing all column types.
    """
    expected_names = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_buy_volume",
        "taker_buy_quote_volume",
        "ignore",
    ]

    # Read first line to detect if header exists
    first_line = csv_bytes[:200].decode("utf-8", errors="ignore").split("\n")[0]
    has_header = "open_time" in first_line.lower() or "open" in first_line.lower()

    if has_header:
        df = pd.read_csv(io.BytesIO(csv_bytes))
        df.columns = [c.strip().lower() for c in df.columns]
        if "count" in df.columns and "trades" not in df.columns:
            df = df.rename(columns={"count": "trades"})
    else:
        df = pd.read_csv(io.BytesIO(csv_bytes), header=None, names=expected_names)

    # Harmonize required columns
    for col in ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume", "taker_buy_quote_volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype(np.float64)
        else:
            df[col] = 0.0

    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce").astype(np.int64)
    df["close_time"] = pd.to_numeric(df["close_time"], errors="coerce").astype(np.int64)

    if "trades" in df.columns:
        df["trades"] = pd.to_numeric(df["trades"], errors="coerce").fillna(0).astype(np.int64)
    else:
        df["trades"] = 0

    df["datetime"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["source"] = "binance_futures_um"

    return df[STANDARD_COLUMNS]


def fetch_checksum(checksum_url: str, headers: Dict[str, str]) -> Optional[str]:
    """Fetch expected SHA256 checksum string from data.binance.vision .CHECKSUM file."""
    req = urllib.request.Request(checksum_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            if response.status == 200:
                raw_text = response.read().decode("utf-8", errors="ignore").strip()
                tokens = raw_text.split()
                if tokens:
                    candidate = tokens[0].lower()
                    if len(candidate) == 64 and all(c in "0123456789abcdef" for c in candidate):
                        return candidate
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
    except Exception as e:
        logger.debug(f"Failed to fetch checksum from {checksum_url}: {e}")
        return None
    return None


def fetch_vision_zip(url: str, max_retries: int = 3, verify_checksum: bool = True) -> Optional[bytes]:
    """
    Fetch a zip archive from data.binance.vision over HTTPS with backoff.
    Verifies SHA256 against companion .CHECKSUM file before extracting.
    On checksum mismatch, retries once, then raises ValueError.
    Returns uncompressed CSV bytes or None if 404/unavailable.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
        "Accept": "*/*",
    }

    req = urllib.request.Request(url, headers=headers)
    checksum_url = f"{url}.CHECKSUM"
    mismatch_seen = False

    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                if response.status == 200:
                    zip_content = response.read()

                    # 1. Download and verify SHA256 if checksum file exists
                    if verify_checksum:
                        expected_hash = fetch_checksum(checksum_url, headers)
                        if expected_hash:
                            actual_hash = hashlib.sha256(zip_content).hexdigest().lower()
                            if actual_hash != expected_hash:
                                if mismatch_seen:
                                    raise ValueError(
                                        f"Checksum mismatch for {url}: expected {expected_hash}, got {actual_hash}"
                                    )
                                logger.warning(
                                    f"Checksum mismatch for {url}: expected {expected_hash}, got {actual_hash}. Retrying once..."
                                )
                                mismatch_seen = True
                                time.sleep(0.5)
                                continue

                    with zipfile.ZipFile(io.BytesIO(zip_content)) as z:
                        namelist = z.namelist()
                        if not namelist:
                            return None
                        # Return first CSV file in the zip
                        csv_name = [n for n in namelist if n.endswith(".csv")][0]
                        return z.read(csv_name)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # File not published yet or outside date range
                return None
            time.sleep(0.5 * (attempt + 1))
        except ValueError:
            raise
        except Exception as e:
            logger.debug(f"Attempt {attempt + 1} failed for {url}: {e}")
            time.sleep(0.5 * (attempt + 1))

    if mismatch_seen:
        raise ValueError(f"Checksum mismatch for {url}: failed after retry")

    return None


def fetch_monthly_klines(symbol: str, timeframe: str, year: int, month: int) -> Optional[pd.DataFrame]:
    """Download a single month's official futures klines from data.binance.vision."""
    url = f"{FUTURES_ARCHIVE_BASE}/monthly/klines/{symbol}/{timeframe}/{symbol}-{timeframe}-{year}-{month:02d}.zip"
    csv_bytes = fetch_vision_zip(url)
    if csv_bytes is None:
        return None
    try:
        return parse_kline_csv(csv_bytes)
    except Exception as e:
        logger.warning(f"Failed to parse monthly CSV for {symbol} {timeframe} {year}-{month:02d}: {e}")
        return None


def fetch_daily_klines(symbol: str, timeframe: str, year: int, month: int, day: int) -> Optional[pd.DataFrame]:
    """Download a single day's official futures klines from data.binance.vision."""
    url = f"{FUTURES_ARCHIVE_BASE}/daily/klines/{symbol}/{timeframe}/{symbol}-{timeframe}-{year}-{month:02d}-{day:02d}.zip"
    csv_bytes = fetch_vision_zip(url)
    if csv_bytes is None:
        return None
    try:
        return parse_kline_csv(csv_bytes)
    except Exception as e:
        logger.warning(f"Failed to parse daily CSV for {symbol} {timeframe} {year}-{month:02d}-{day:02d}: {e}")
        return None


def verify_candle_integrity(df: pd.DataFrame, timeframe: str) -> Tuple[bool, List[str]]:
    """
    Audit candle DataFrame for schema compliance, null values, monotonicity,
    temporal continuity, price hierarchy, and real futures liquidity.
    """
    issues: List[str] = []
    if df.empty:
        return False, ["DataFrame is completely empty"]

    required_cols = ["open_time", "datetime", "open", "high", "low", "close", "volume", "quote_volume", "source"]
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
    max_oc = np.maximum(df["open"].values, df["close"].values)
    high_violations = np.sum(df["high"].values < (max_oc - 1e-8))
    if high_violations > 0:
        issues.append(f"Found {high_violations} bars where high < max(open, close)")

    min_oc = np.minimum(df["open"].values, df["close"].values)
    low_violations = np.sum(df["low"].values > (min_oc + 1e-8))
    if low_violations > 0:
        issues.append(f"Found {low_violations} bars where low > min(open, close)")

    if np.any(df["low"].values <= 0.0):
        issues.append("Found non-positive low prices")

    if np.any(df["volume"].values < 0.0):
        issues.append("Found negative volume values")

    # 5. Continuity / Step check
    interval_ms = timeframe_to_ms(timeframe)
    diffs = np.diff(df["open_time"].values)
    gaps = np.where(diffs != interval_ms)[0]
    if len(gaps) > 0:
        gap_details = []
        for g_idx in gaps[:5]:
            t1 = df["open_time"].iloc[g_idx]
            t2 = df["open_time"].iloc[g_idx + 1]
            gap_details.append(f"gap of {t2 - t1}ms between {t1} and {t2}")
        issues.append(f"Found {len(gaps)} continuity gaps in {timeframe} sequence ({', '.join(gap_details)})")

    # 6. Liquidity & Volume Sanity Check (Futures Sanity)
    median_vol = float(df["quote_volume"].median()) if "quote_volume" in df.columns else 0.0
    if median_vol < 1000.0:
        issues.append(f"Abnormally low median hourly volume (${median_vol:,.2f}), possible illiquid or wrong market")

    if "trades" in df.columns:
        zero_trade_pct = (df["trades"] == 0).mean() * 100.0
        if zero_trade_pct > 0.5:
            issues.append(f"Found {zero_trade_pct:.2f}% zero-trade hours, unacceptable for institutional futures")

    is_valid = len(issues) == 0
    return is_valid, issues


def download_candles_futures(
    symbol: str,
    timeframe: str,
    start_year: int = 2024,
    start_month: int = 1,
    end_year: int = 2026,
    end_month: int = 10,
    output_dir: str = "data/candles",
) -> pd.DataFrame:
    """
    Download continuous Binance USD-M Futures klines from data.binance.vision
    and save atomically to Parquet with full schema and provenance metadata.
    """
    os.makedirs(output_dir, exist_ok=True)
    sym = symbol.upper()
    tf = timeframe.lower()

    logger.info(f"Downloading official Binance USD-M Futures for {sym} [{tf}] from {start_year}-{start_month:02d}...")

    frames: List[pd.DataFrame] = []

    # 1. Fetch monthly archives
    for y in range(start_year, end_year + 1):
        m_start = start_month if y == start_year else 1
        m_end = (end_month - 1) if y == end_year else 12

        for m in range(m_start, m_end + 1):
            m_df = fetch_monthly_klines(sym, tf, y, m)
            if m_df is not None and not m_df.empty:
                frames.append(m_df)
                logger.debug(f"Loaded {sym} {tf} monthly archive: {y}-{m:02d} ({len(m_df)} bars)")

    # 2. Fetch daily archives for current/most recent incomplete month
    for d in range(1, 32):
        d_df = fetch_daily_klines(sym, tf, end_year, end_month, d)
        if d_df is not None and not d_df.empty:
            frames.append(d_df)
            logger.debug(f"Loaded {sym} {tf} daily archive: {end_year}-{end_month:02d}-{d:02d} ({len(d_df)} bars)")
        elif d > 5:
            # If several days fail sequentially, we have reached the latest published day
            break

    if not frames:
        logger.error(f"Zero futures klines retrieved for {sym} {tf} from data.binance.vision")
        return pd.DataFrame()

    # Combine all months and days
    full_df = pd.concat(frames, ignore_index=True)

    # Deduplicate and sort strictly by open_time
    full_df = full_df.drop_duplicates(subset=["open_time"]).sort_values("open_time").reset_index(drop=True)

    # Validate integrity
    is_valid, issues = verify_candle_integrity(full_df, tf)
    if not is_valid:
        logger.warning(f"Integrity warnings for {sym} {tf}: {issues}")
    else:
        logger.info(
            f"Integrity check PASSED for {sym} {tf}: {len(full_df)} candles from "
            f"{full_df['datetime'].iloc[0].strftime('%Y-%m-%d')} to {full_df['datetime'].iloc[-1].strftime('%Y-%m-%d')} "
            f"(Median Quote Vol: ${full_df['quote_volume'].median():,.0f}/bar)"
        )

    # Save to Parquet atomically
    parquet_path = os.path.join(output_dir, f"{sym}_{tf}.parquet")
    tmp_path = os.path.join(output_dir, f".tmp_{sym}_{tf}.parquet")

    full_df.to_parquet(tmp_path, engine="pyarrow", compression="snappy", index=False)
    os.replace(tmp_path, parquet_path)
    logger.info(f"Saved {len(full_df)} candles to {parquet_path}")

    return full_df


def download_universe_futures(
    symbols: Optional[List[str]] = None,
    timeframes: Optional[List[str]] = None,
    start_year: int = 2024,
    start_month: int = 1,
    end_year: int = 2026,
    end_month: int = 10,
    output_dir: str = "data/candles",
) -> Dict[str, pd.DataFrame]:
    """Download continuous official futures datasets for all universe symbols and timeframes."""
    syms = symbols or DEFAULT_SYMBOLS
    tfs = timeframes or DEFAULT_TIMEFRAMES

    results: Dict[str, pd.DataFrame] = {}
    start_time = time.time()

    for sym in syms:
        for tf in tfs:
            key = f"{sym}_{tf}"
            df = download_candles_futures(
                symbol=sym,
                timeframe=tf,
                start_year=start_year,
                start_month=start_month,
                end_year=end_year,
                end_month=end_month,
                output_dir=output_dir,
            )
            results[key] = df

    elapsed = time.time() - start_time
    logger.info(f"Universe ingestion complete: {len(results)} datasets saved in {elapsed:.1f}s")
    return results


def parse_funding_rate_csv(csv_bytes: bytes, symbol: str = "BTCUSDT") -> pd.DataFrame:
    """
    Parse a raw funding rate CSV from Binance Vision zip file and standardize types.
    Columns: calc_time, datetime, funding_interval_hours, last_funding_rate, symbol.
    """
    first_line = csv_bytes[:200].decode("utf-8", errors="ignore").split("\n")[0]
    has_header = "calc_time" in first_line.lower() or "funding" in first_line.lower()

    if has_header:
        df = pd.read_csv(io.BytesIO(csv_bytes))
        df.columns = [c.strip().lower() for c in df.columns]
    else:
        df = pd.read_csv(
            io.BytesIO(csv_bytes),
            header=None,
            names=["calc_time", "funding_interval_hours", "last_funding_rate"],
        )

    df["calc_time"] = pd.to_numeric(df["calc_time"], errors="coerce").astype(np.int64)
    df["funding_interval_hours"] = (
        pd.to_numeric(df["funding_interval_hours"], errors="coerce").fillna(8).astype(np.int64)
    )
    df["last_funding_rate"] = pd.to_numeric(df["last_funding_rate"], errors="coerce").astype(np.float64)
    df["datetime"] = pd.to_datetime(df["calc_time"], unit="ms", utc=True)
    df["symbol"] = symbol.upper()
    return df[["calc_time", "datetime", "funding_interval_hours", "last_funding_rate", "symbol"]]


def validate_funding_rates(df: pd.DataFrame) -> Tuple[bool, List[str]]:
    """
    Validate funding rate DataFrame:
    1. Not empty
    2. Monotonically increasing calc_time (sorted)
    3. No duplicate timestamps
    4. 8-hour cadence (28,800,000 ms between consecutive funding intervals)
    """
    issues: List[str] = []
    if df.empty:
        return False, ["Funding rate DataFrame is completely empty"]

    required_cols = ["calc_time", "datetime", "funding_interval_hours", "last_funding_rate"]
    for col in required_cols:
        if col not in df.columns:
            issues.append(f"Missing required column: {col}")

    if issues:
        return False, issues

    null_counts = df[required_cols].isnull().sum()
    for col, count in null_counts.items():
        if count > 0:
            issues.append(f"Column {col} has {count} null values")

    # 1. Monotonicity (sorted)
    if not df["calc_time"].is_monotonic_increasing:
        issues.append("calc_time is not strictly monotonic increasing (not sorted)")

    # 2. Duplicate check
    dups = int(df["calc_time"].duplicated().sum())
    if dups > 0:
        issues.append(f"Found {dups} duplicate calc_time timestamps")

    # 3. 8-hour cadence check (28,800,000 ms)
    diffs = np.diff(df["calc_time"].values)
    expected_cadence_ms = 8 * 3600 * 1000  # 28,800,000 ms
    cadence_violations = np.where(diffs != expected_cadence_ms)[0]
    if len(cadence_violations) > 0:
        issues.append(
            f"Found {len(cadence_violations)} cadence deviations from 8h ({expected_cadence_ms}ms)"
        )

    return len(issues) == 0, issues


def download_funding_rates(
    symbol: str,
    start_year: int = 2024,
    start_month: int = 1,
    end_year: int = 2026,
    end_month: int = 10,
    output_dir: str = "data/funding",
) -> pd.DataFrame:
    """
    Download official Binance USD-M Futures funding rate history from monthly archives on
    data.binance.vision into data/funding/<SYMBOL>.parquet.
    Validates sorting, zero duplicates, and 8-hour cadence.
    """
    os.makedirs(output_dir, exist_ok=True)
    sym = symbol.upper()
    logger.info(f"Downloading USD-M Futures funding rate history for {sym} from {start_year}-{start_month:02d}...")

    frames: List[pd.DataFrame] = []

    for y in range(start_year, end_year + 1):
        m_start = start_month if y == start_year else 1
        m_end = end_month if y == end_year else 12

        for m in range(m_start, m_end + 1):
            url = f"{FUTURES_ARCHIVE_BASE}/monthly/fundingRate/{sym}/{sym}-fundingRate-{y}-{m:02d}.zip"
            csv_bytes = fetch_vision_zip(url)
            if csv_bytes is not None:
                try:
                    m_df = parse_funding_rate_csv(csv_bytes, sym)
                    if not m_df.empty:
                        frames.append(m_df)
                        logger.debug(f"Loaded {sym} funding rate archive: {y}-{m:02d} ({len(m_df)} records)")
                except Exception as e:
                    logger.warning(f"Failed to parse funding rate CSV for {sym} {y}-{m:02d}: {e}")

    if not frames:
        logger.error(f"Zero funding rate records retrieved for {sym} from data.binance.vision")
        return pd.DataFrame()

    full_df = pd.concat(frames, ignore_index=True)
    full_df = full_df.drop_duplicates(subset=["calc_time"]).sort_values("calc_time").reset_index(drop=True)

    is_valid, issues = validate_funding_rates(full_df)
    if not is_valid:
        logger.warning(f"Funding rate validation warnings for {sym}: {issues}")
    else:
        logger.info(
            f"Funding rate validation PASSED for {sym}: {len(full_df)} intervals from "
            f"{full_df['datetime'].iloc[0].strftime('%Y-%m-%d %H:%M')} to "
            f"{full_df['datetime'].iloc[-1].strftime('%Y-%m-%d %H:%M')}"
        )

    parquet_path = os.path.join(output_dir, f"{sym}.parquet")
    tmp_path = os.path.join(output_dir, f".tmp_{sym}.parquet")
    full_df.to_parquet(tmp_path, engine="pyarrow", compression="snappy", index=False)
    os.replace(tmp_path, parquet_path)
    logger.info(f"Saved {len(full_df)} funding rate records to {parquet_path}")

    return full_df


# Backwards-compatible aliases
download_candles = download_candles_futures
download_universe = download_universe_futures


def main():
    parser = argparse.ArgumentParser(description="Download official Binance USD-M Futures historical data.")
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
        "--start-year",
        type=int,
        default=2024,
        help="Start year (default: 2024)",
    )
    parser.add_argument(
        "--start-month",
        type=int,
        default=1,
        help="Start month (default: 1)",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=2026,
        help="End year (default: 2026)",
    )
    parser.add_argument(
        "--end-month",
        type=int,
        default=10,
        help="End month (default: 10)",
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
                source = df["source"].iloc[0] if "source" in df.columns else "unknown"
                med_vol = df["quote_volume"].median() if "quote_volume" in df.columns else 0
                if valid:
                    print(f"[OK] {sym}_{tf}: {len(df)} candles | Source: {source} | Med Vol: ${med_vol:,.0f} | 0 issues.")
                else:
                    print(f"[FAIL] {sym}_{tf}: {issues}")
                    all_passed = False
        sys.exit(0 if all_passed else 1)

    download_universe_futures(
        symbols=symbols,
        timeframes=timeframes,
        start_year=args.start_year,
        start_month=args.start_month,
        end_year=args.end_year,
        end_month=args.end_month,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
