"""
Unit tests for historical Binance USD-M Futures candle downloader, integrity verifier, and Parquet loader.
"""

import io
import os
import shutil
import tempfile
import zipfile
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from data.downloader import (
    timeframe_to_ms,
    parse_kline_csv,
    fetch_vision_zip,
    verify_candle_integrity,
    STANDARD_COLUMNS,
    parse_funding_rate_csv,
    validate_funding_rates,
    download_funding_rates,
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
                "quote_volume": 50000.0,
                "trades": 100,
                "source": "binance_futures_um",
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
                "quote_volume": 5000.0,
                "source": "binance_futures_um",
            }
        ]
    )
    is_valid, issues = verify_candle_integrity(df, "1h")
    assert is_valid is False
    assert any("high < max" in s for s in issues)


def test_verify_candle_integrity_continuity_gap():
    base_ts = 1700000000000
    interval_ms = 3600 * 1000
    rows = [
        {
            "open_time": base_ts,
            "datetime": pd.to_datetime(base_ts, unit="ms", utc=True),
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 102.0,
            "volume": 50.0,
            "quote_volume": 5000.0,
            "source": "binance_futures_um",
        },
        {
            "open_time": base_ts + interval_ms,
            "datetime": pd.to_datetime(base_ts + interval_ms, unit="ms", utc=True),
            "open": 102.0,
            "high": 106.0,
            "low": 96.0,
            "close": 104.0,
            "volume": 50.0,
            "quote_volume": 5000.0,
            "source": "binance_futures_um",
        },
        {
            "open_time": base_ts + (3 * interval_ms),  # skipped 1 hour!
            "datetime": pd.to_datetime(base_ts + (3 * interval_ms), unit="ms", utc=True),
            "open": 104.0,
            "high": 108.0,
            "low": 98.0,
            "close": 105.0,
            "volume": 50.0,
            "quote_volume": 5000.0,
            "source": "binance_futures_um",
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
                "quote_volume": 5000.0,
                "source": "binance_futures_um",
            }
        ]
    )
    is_valid, issues = verify_candle_integrity(df, "1h")
    assert is_valid is False
    assert any("negative volume" in s for s in issues)


def test_verify_candle_integrity_liquidity_fail():
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
                "volume": 0.001,
                "quote_volume": 50.0,  # abnormally low quote volume (<1000)
                "trades": 0,
                "source": "binance_futures_um",
            }
        ]
    )
    is_valid, issues = verify_candle_integrity(df, "1h")
    assert is_valid is False
    assert any("abnormally low" in s.lower() for s in issues)


def test_parse_kline_csv_headered():
    raw_csv = (
        b"open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
        b"1704067200000,42000.0,42500.0,41900.0,42400.0,100.5,1704070799999,4261200.0,1500,60.2,2552480.0,0\n"
    )
    df = parse_kline_csv(raw_csv)
    assert len(df) == 1
    assert df["open_time"].iloc[0] == 1704067200000
    assert df["open"].iloc[0] == 42000.0
    assert df["trades"].iloc[0] == 1500
    assert df["source"].iloc[0] == "binance_futures_um"
    assert list(df.columns) == STANDARD_COLUMNS


def test_parse_kline_csv_unheadered():
    raw_csv = b"1704067200000,42000.0,42500.0,41900.0,42400.0,100.5,1704070799999,4261200.0,1500,60.2,2552480.0,0\n"
    df = parse_kline_csv(raw_csv)
    assert len(df) == 1
    assert df["open_time"].iloc[0] == 1704067200000
    assert df["close"].iloc[0] == 42400.0
    assert df["trades"].iloc[0] == 1500
    assert df["source"].iloc[0] == "binance_futures_um"


def test_fetch_vision_zip_mock():
    # Build a real in-memory zip file containing a CSV
    csv_bytes = b"open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n1704067200000,42000.0,42500.0,41900.0,42400.0,100.5,1704070799999,4261200.0,1500,60.2,2552480.0,0\n"
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("BTCUSDT-1h-2024-01.csv", csv_bytes)
    zip_data = zip_buf.getvalue()

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = zip_data
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        res_bytes = fetch_vision_zip("https://fake.url/test.zip")
        assert res_bytes is not None
        assert b"42000.0" in res_bytes


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
                    "quote_volume": 50000.0,
                    "trades": 50,
                    "source": "binance_futures_um",
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


def test_checksum_mismatch_retry_and_raise():
    """Verify SHA256 checksum mismatch triggers retry then raises ValueError."""
    import hashlib

    corrupted_zip_bytes = b"bad corrupted content"
    valid_expected_sha = hashlib.sha256(b"original good content").hexdigest()
    checksum_file_bytes = f"{valid_expected_sha}  test.zip\n".encode("utf-8")

    call_count = {"zip": 0, "checksum": 0}

    def fake_urlopen(req, timeout=12):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        mock = MagicMock()
        mock.status = 200
        mock.__enter__.return_value = mock

        if url.endswith(".CHECKSUM"):
            call_count["checksum"] += 1
            mock.read.return_value = checksum_file_bytes
        else:
            call_count["zip"] += 1
            mock.read.return_value = corrupted_zip_bytes
        return mock

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with pytest.raises(ValueError, match="Checksum mismatch"):
            fetch_vision_zip("https://fake.url/test.zip", max_retries=3)

    # Must have attempted download, noticed mismatch, retried once, then raised
    assert call_count["zip"] >= 2
    assert call_count["checksum"] >= 2


def test_checksum_valid_verification():
    """Verify clean download and unpack when checksum matches exactly."""
    import hashlib

    csv_bytes = b"calc_time,funding_interval_hours,last_funding_rate\n1704067200000,8,0.00037\n"
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("test.csv", csv_bytes)
    zip_data = zip_buf.getvalue()

    sha256_hash = hashlib.sha256(zip_data).hexdigest()
    checksum_file_bytes = f"{sha256_hash}  test.zip\n".encode("utf-8")

    def fake_urlopen(req, timeout=12):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        mock = MagicMock()
        mock.status = 200
        mock.__enter__.return_value = mock

        if url.endswith(".CHECKSUM"):
            mock.read.return_value = checksum_file_bytes
        else:
            mock.read.return_value = zip_data
        return mock

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        extracted = fetch_vision_zip("https://fake.url/test.zip")
        assert extracted is not None
        assert b"0.00037" in extracted


def test_parse_and_validate_funding_rates():
    """Verify funding rate parsing, monotonicity, duplicate detection, and 8h cadence validation."""
    raw_csv = (
        b"calc_time,funding_interval_hours,last_funding_rate\n"
        b"1704067200000,8,0.00037409\n"
        b"1704096000000,8,0.00027213\n"
        b"1704124800000,8,0.00033601\n"
    )
    df = parse_funding_rate_csv(raw_csv, "BTCUSDT")
    assert len(df) == 3
    assert df["calc_time"].iloc[0] == 1704067200000
    assert df["funding_interval_hours"].iloc[0] == 8
    assert abs(df["last_funding_rate"].iloc[0] - 0.00037409) < 1e-8
    assert df["symbol"].iloc[0] == "BTCUSDT"

    # Validate passes
    valid, issues = validate_funding_rates(df)
    assert valid is True
    assert len(issues) == 0

    # Unsorted check fails
    df_unsorted = df.iloc[::-1].copy()
    valid, issues = validate_funding_rates(df_unsorted)
    assert valid is False
    assert any("monotonic" in s for s in issues)

    # Duplicate check fails
    df_dup = pd.concat([df, df.iloc[:1]], ignore_index=True).sort_values("calc_time")
    valid, issues = validate_funding_rates(df_dup)
    assert valid is False
    assert any("duplicate" in s for s in issues)

    # Cadence violation fails (gap is 4h instead of 8h)
    df_broken_cadence = df.copy()
    df_broken_cadence.loc[1, "calc_time"] = 1704067200000 + (4 * 3600 * 1000)
    valid, issues = validate_funding_rates(df_broken_cadence)
    assert valid is False
    assert any("cadence deviations" in s for s in issues)


def test_download_funding_rates_roundtrip():
    """Verify downloading monthly funding rate archives and saving to Parquet."""
    temp_dir = tempfile.mkdtemp()
    try:
        csv_bytes = (
            b"calc_time,funding_interval_hours,last_funding_rate\n"
            b"1704067200000,8,0.00037409\n"
            b"1704096000000,8,0.00027213\n"
        )
        with patch("data.downloader.fetch_vision_zip", return_value=csv_bytes):
            df = download_funding_rates(
                symbol="BTCUSDT",
                start_year=2024,
                start_month=1,
                end_year=2024,
                end_month=1,
                output_dir=temp_dir,
            )
            assert len(df) == 2
            parquet_path = os.path.join(temp_dir, "BTCUSDT.parquet")
            assert os.path.exists(parquet_path)

            loaded = pd.read_parquet(parquet_path)
            assert len(loaded) == 2
            assert loaded["calc_time"].iloc[0] == 1704067200000
    finally:
        shutil.rmtree(temp_dir)

