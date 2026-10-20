"""
Unit tests for historical candle downloader, integrity verifier, and Parquet loader.
"""

import json
import os
import shutil
import tempfile
import urllib.error
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from data.downloader import (
    fetch_klines_batch,
    timeframe_to_ms,
    verify_candle_integrity,
)
from data.loader import clear_cache, load_candles


def test_timeframe_to_ms():
    assert timeframe_to_ms("1m") == 60 * 1000
    assert timeframe_to_ms("5m") == 300 * 1000
    assert timeframe_to_ms("15m") == 900 * 1000
    assert timeframe_to_ms("1h") == 3600 * 1000
    assert timeframe_to_ms("4h") == 14400 * 1000
    assert timeframe_to_ms("1d") == 86400 * 1000


def test_verify_candle_integrity_valid():
    base_ts = 1700000000000
    interval_ms = 3600 * 1000
    rows = []
    for i in range(10):
        t = base_ts + i * interval_ms
        rows.append(
            {
                "open_time": t,
                "datetime": pd.to_datetime(t, unit="ms", utc=True),
                "open": 100.0 + i,
                "high": 105.0 + i,
                "low": 95.0 + i,
                "close": 102.0 + i,
                "volume": 500.0,
            }
        )
    df = pd.DataFrame(rows)
    is_valid, issues = verify_candle_integrity(df, "1h")
    assert is_valid is True
    assert len(issues) == 0


def test_verify_candle_integrity_empty():
    df = pd.DataFrame()
    is_valid, issues = verify_candle_integrity(df, "1h")
    assert is_valid is False
    assert "empty" in issues[0].lower()


def test_verify_candle_integrity_high_low_violation():
    base_ts = 1700000000000
    # High is lower than close!
    df = pd.DataFrame(
        [
            {
                "open_time": base_ts,
                "datetime": pd.to_datetime(base_ts, unit="ms", utc=True),
                "open": 100.0,
                "high": 98.0,  # invalid: high < open
                "low": 90.0,
                "close": 95.0,
                "volume": 10.0,
            }
        ]
    )
    is_valid, issues = verify_candle_integrity(df, "1h")
    assert is_valid is False
    assert any("high < max" in s for s in issues)


def test_verify_candle_integrity_continuity_gap():
    base_ts = 1700000000000
    interval_ms = 3600 * 1000
    # Skip index 2 to create a gap
    rows = [
        {
            "open_time": base_ts,
            "datetime": pd.to_datetime(base_ts, unit="ms", utc=True),
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 102.0,
            "volume": 50.0,
        },
        {
            "open_time": base_ts + interval_ms,
            "datetime": pd.to_datetime(base_ts + interval_ms, unit="ms", utc=True),
            "open": 102.0,
            "high": 106.0,
            "low": 96.0,
            "close": 104.0,
            "volume": 50.0,
        },
        {
            "open_time": base_ts + (3 * interval_ms),  # skipped 1 hour!
            "datetime": pd.to_datetime(base_ts + (3 * interval_ms), unit="ms", utc=True),
            "open": 104.0,
            "high": 108.0,
            "low": 98.0,
            "close": 105.0,
            "volume": 50.0,
        },
    ]
    df = pd.DataFrame(rows)
    is_valid, issues = verify_candle_integrity(df, "1h")
    assert is_valid is False
    assert any("continuity gaps" in s for s in issues)


def test_verify_candle_integrity_negative_volume():
    base_ts = 1700000000000
    df = pd.DataFrame(
        [
            {
                "open_time": base_ts,
                "datetime": pd.to_datetime(base_ts, unit="ms", utc=True),
                "open": 100.0,
                "high": 105.0,
                "low": 95.0,
                "close": 102.0,
                "volume": -10.0,  # negative volume
            }
        ]
    )
    is_valid, issues = verify_candle_integrity(df, "1h")
    assert is_valid is False
    assert any("negative volume" in s for s in issues)


def test_parquet_save_and_load_roundtrip():
    temp_dir = tempfile.mkdtemp()
    try:
        base_ts = 1700000000000
        interval_ms = 3600 * 1000
        rows = []
        for i in range(50):
            t = base_ts + i * interval_ms
            rows.append(
                {
                    "open_time": t,
                    "datetime": pd.to_datetime(t, unit="ms", utc=True),
                    "open": 100.0 + i,
                    "high": 105.0 + i,
                    "low": 95.0 + i,
                    "close": 102.0 + i,
                    "volume": 500.0,
                }
            )
        df_orig = pd.DataFrame(rows)
        file_path = os.path.join(temp_dir, "BTCUSDT_1h.parquet")
        df_orig.to_parquet(file_path, engine="pyarrow", compression="snappy")

        clear_cache()
        df_loaded = load_candles("BTCUSDT", "1h", data_dir=temp_dir, use_cache=True)
        assert len(df_loaded) == 50
        assert df_loaded["close"].iloc[0] == 102.0
        assert df_loaded["close"].iloc[-1] == 102.0 + 49

        # Test date filtering
        mid_date = df_orig["datetime"].iloc[20].isoformat()
        df_filtered = load_candles(
            "BTCUSDT", "1h", start_date=mid_date, data_dir=temp_dir, use_cache=False
        )
        assert len(df_filtered) == 30
    finally:
        shutil.rmtree(temp_dir)


def test_fetch_klines_dual_endpoint_failover():
    # Mocking first endpoint returning 451, second returning valid kline data
    dummy_kline = [
        [1700000000000, "100.0", "105.0", "95.0", "102.0", "50.0", 1700003599999, "5000.0", 100]
    ]

    def mock_urlopen(req, timeout=12):
        if "binance.us" in req.full_url:
            # First endpoint throws 451
            fp = MagicMock()
            raise urllib.error.HTTPError(req.full_url, 451, "Unavailable", {}, fp)
        elif "binance.com" in req.full_url:
            # Second endpoint succeeds
            resp = MagicMock()
            resp.__enter__.return_value = resp
            resp.status = 200
            resp.read.return_value = json.dumps(dummy_kline).encode("utf-8")
            return resp
        raise ValueError("Unexpected URL")

    with patch("urllib.request.urlopen", side_effect=mock_urlopen):
        result = fetch_klines_batch(
            symbol="BTCUSDT",
            interval="1h",
            start_time=1700000000000,
            endpoints=["https://api.binance.us/api/v3/klines", "https://api.binance.com/api/v3/klines"],
        )
        assert len(result) == 1
        assert result[0][0] == 1700000000000
