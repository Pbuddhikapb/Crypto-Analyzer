"""
test_collector.py — Unit tests for MarketDataCollector with mocked WebSocket and REST streams.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from collector import MarketDataCollector


class MockAsyncIteratorWS:
    """Mock WebSocket context manager yielding specified messages and then terminating collector loop."""

    def __init__(self, messages, collector):
        self.messages = messages
        self.collector = collector

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def __aiter__(self):
        for msg in self.messages:
            yield msg
        self.collector._running = False


@pytest.fixture
def temp_collector(tmp_path):
    """Fixture providing MarketDataCollector with an isolated sqlite db."""
    db_path = str(tmp_path / "collector_test.db")
    collector = MarketDataCollector(
        symbols=["BTCUSDT", "ETHUSDT"],
        db_path=db_path,
        isp_friendly=False,
    )
    yield collector
    collector._running = False


@pytest.mark.asyncio
async def test_collector_klines_and_bbo_ws_mocked(temp_collector):
    """Verify that _run_klines_and_bbo_ws connects to fstream.binance.com and tags rows with source."""
    collector = temp_collector
    collector._running = True
    collector.writer.start()

    messages = [
        json.dumps({
            "stream": "btcusdt@bookTicker",
            "data": {
                "s": "BTCUSDT",
                "b": "65000.0",
                "B": "1.5",
                "a": "65001.0",
                "A": "2.5",
            },
        }),
        json.dumps({
            "stream": "btcusdt@kline_1m",
            "data": {
                "k": {
                    "s": "BTCUSDT",
                    "i": "1m",
                    "t": 1729000000000,
                    "T": 1729000059999,
                    "o": "65000.0",
                    "h": "65020.0",
                    "l": "64990.0",
                    "c": "65010.0",
                    "v": "10.0",
                    "q": "650000.0",
                    "n": 100,
                    "V": "6.0",
                    "x": True,
                }
            },
        }),
    ]

    recorded_urls = []

    def mock_connect(url, *args, **kwargs):
        recorded_urls.append(url)
        return MockAsyncIteratorWS(messages, collector)

    with patch("websockets.connect", side_effect=mock_connect):
        await collector._run_klines_and_bbo_ws()

    # Verify WebSocket endpoint is Binance USD-M Futures
    assert len(recorded_urls) >= 1
    ws_url = recorded_urls[0]
    assert ws_url.startswith("wss://fstream.binance.com/stream?streams=")
    assert "stream.binance.com:9443" not in ws_url
    assert "btcusdt@bookTicker" in ws_url
    assert "btcusdt@kline_1m" in ws_url
    assert "btcusdt@kline_15m" in ws_url

    # Allow background writer to flush to vault
    await asyncio.sleep(0.2)
    await collector.writer.stop()

    # Query vault rows and assert source column
    bbo_rows = collector.vault.get_bbo_snapshots_window("BTCUSDT", 0, 9999999999999)
    assert len(bbo_rows) == 1
    assert bbo_rows[0]["best_bid_price"] == 65000.0
    assert bbo_rows[0]["source"] == "binance_futures_um"

    bars = collector.vault.get_candle_bars("BTCUSDT", "1m", 0, 9999999999999)
    assert len(bars) == 1
    assert bars[0]["open"] == 65000.0
    assert bars[0]["close"] == 65010.0
    assert bars[0]["source"] == "binance_futures_um"


@pytest.mark.asyncio
async def test_collector_bbo_rest_mocked(temp_collector):
    """Verify that _poll_bbo_rest queries fapi.binance.com and tags rows with source."""
    collector = temp_collector
    collector._running = True
    collector.writer.start()

    mock_resp_data = [
        {
            "symbol": "BTCUSDT",
            "bidPrice": "65100.00",
            "bidQty": "3.5",
            "askPrice": "65101.00",
            "askQty": "4.0",
        },
        {
            "symbol": "ETHUSDT",
            "bidPrice": "2600.00",
            "bidQty": "10.0",
            "askPrice": "2600.50",
            "askQty": "12.0",
        },
        {
            "symbol": "DOGEUSDT",  # Not in temp_collector.symbols
            "bidPrice": "0.15",
            "bidQty": "1000",
            "askPrice": "0.16",
            "askQty": "1000",
        },
    ]

    requested_urls = []

    class MockClientResponse:
        def __init__(self, data):
            self.status = 200
            self._data = data

        async def json(self):
            return self._data

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

        def get(self, url, *args, **kwargs):
            requested_urls.append(url)
            # Stop collector after 1 poller cycle
            collector._running = False
            return MockClientResponse(mock_resp_data)

    with patch("aiohttp.ClientSession", MockSession):
        with patch("asyncio.sleep", AsyncMock(return_value=None)):
            await collector._poll_bbo_rest()

    assert len(requested_urls) == 1
    assert requested_urls[0] == "https://fapi.binance.com/fapi/v1/ticker/bookTicker"
    assert not requested_urls[0].startswith("https://api.binance.com")
    assert "api.binance.com/api/v3" not in requested_urls[0]

    # Allow background writer to flush
    await asyncio.sleep(0.2)
    await collector.writer.stop()

    btc_bbo = collector.vault.get_bbo_snapshots_window("BTCUSDT", 0, 9999999999999)
    assert len(btc_bbo) == 1
    assert btc_bbo[0]["best_bid_price"] == 65100.0
    assert btc_bbo[0]["source"] == "binance_futures_um"

    eth_bbo = collector.vault.get_bbo_snapshots_window("ETHUSDT", 0, 9999999999999)
    assert len(eth_bbo) == 1
    assert eth_bbo[0]["best_bid_price"] == 2600.0
    assert eth_bbo[0]["source"] == "binance_futures_um"

    # DOGEUSDT should not be stored since it was not in target symbols
    doge_bbo = collector.vault.get_bbo_snapshots_window("DOGEUSDT", 0, 9999999999999)
    assert len(doge_bbo) == 0
