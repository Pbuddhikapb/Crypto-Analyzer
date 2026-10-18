import asyncio
import os
import time
import pytest
from fastapi.testclient import TestClient

from server import create_app, extract_fvg_boxes, format_candles_for_chart, compute_indicators


@pytest.fixture
def app_and_client(tmp_path):
    """Fixture providing a test FastAPI app and TestClient isolated from network feeds."""
    db_path = str(tmp_path / "test_server.db")
    app = create_app(
        enable_ingestion=False,
        db_path=db_path,
        symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        timeframe="1m",
    )
    with TestClient(app) as client:
        yield app, client


def test_health_endpoint(app_and_client):
    """Verify /api/health returns 200 with proper diagnostic fields."""
    app, client = app_and_client
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "spot_connectivity" in data
    assert "futures_connectivity" in data
    assert "futures_status_message" in data
    assert data["active_symbols_count"] == 3
    assert data["db_status"] == "connected"
    assert data["connected_ws_clients"] == 0


def test_market_symbols_endpoint(app_and_client):
    """Verify /api/market/symbols returns monitored universe with price and trend metadata."""
    app, client = app_and_client
    # Seed a candle
    app.state.traders["BTCUSDT"].load_historical_candle((65000.0, 65500.0, 64900.0, 65200.0, 100.0))
    app.state.latest_prices["BTCUSDT"] = 65250.0

    response = client.get("/api/market/symbols")
    assert response.status_code == 200
    symbols = response.json()
    assert len(symbols) == 3

    btc = next(s for s in symbols if s["symbol"] == "BTCUSDT")
    assert btc["futures_symbol"] == "BTCUSDT"
    assert btc["latest_price"] == 65250.0
    assert btc["has_open_position"] is False
    assert "trend" in btc
    assert "rsi" in btc


def test_market_candles_endpoint(app_and_client):
    """Verify /api/market/candles returns integer-second timestamps and valid OHLCV fields."""
    app, client = app_and_client
    trader = app.state.traders["BTCUSDT"]

    # Prime with 10 candles
    base_p = 60000.0
    for i in range(10):
        trader.load_historical_candle((base_p + i * 10, base_p + i * 10 + 20, base_p + i * 10 - 10, base_p + i * 10 + 5, 50.0))

    response = client.get("/api/market/candles?symbol=BTCUSDT&timeframe=1m&limit=10")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "BTCUSDT"
    assert data["timeframe"] == "1m"
    candles = data["candles"]
    assert len(candles) == 10

    # Validate TradingView requirements: integer-second timestamps and strictly increasing order
    for i, c in enumerate(candles):
        assert isinstance(c["time"], int)
        assert "open" in c and "high" in c and "low" in c and "close" in c and "volume" in c
        if i > 0:
            assert c["time"] > candles[i - 1]["time"]


def test_market_candles_indicators_and_fvg(app_and_client):
    """Verify /api/market/candles correctly computes indicators and FVG zones."""
    app, client = app_and_client
    trader = app.state.traders["BTCUSDT"]

    # Construct a Bullish Fair Value Gap:
    # Bar 0: High = 100
    # Bar 1: Green displacement candle (open=100, high=120, low=99, close=118, vol=50) -> body/range = 18/21 > 0.5
    # Bar 2: Low = 105 (> High of Bar 0 = 100) -> Gap between 100 and 105
    trader.load_historical_candle((95.0, 100.0, 90.0, 98.0, 10.0))
    trader.load_historical_candle((100.0, 120.0, 99.0, 118.0, 50.0))
    trader.load_historical_candle((110.0, 125.0, 105.0, 122.0, 20.0))

    response = client.get("/api/market/candles?symbol=BTCUSDT&timeframe=1m")
    assert response.status_code == 200
    indicators = response.json()["indicators"]
    assert indicators["has_bullish_fvg"] is True
    assert indicators["has_bearish_fvg"] is False

    boxes = indicators["active_fvg_boxes"]
    assert len(boxes) >= 1
    bull_box = next(b for b in boxes if b["type"] == "BULLISH")
    assert bull_box["top"] == 105.0
    assert bull_box["bottom"] == 100.0


def test_market_candles_invalid_symbol(app_and_client):
    """Verify requesting an unmonitored symbol returns 404."""
    app, client = app_and_client
    response = client.get("/api/market/candles?symbol=UNKNOWNCOIN")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_portfolio_summary_endpoint(app_and_client):
    """Verify /api/portfolio/summary returns metrics and active trade details."""
    app, client = app_and_client
    risk_mgr = app.state.risk_manager
    trader = app.state.traders["BTCUSDT"]

    # Simulate an active trade
    trader.active_trade = {
        "id": 1,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_price": 50000.0,
        "stop_loss": 49000.0,
        "take_profit": 52000.0,
        "timestamp": "2026-04-14T12:00:00Z",
    }
    app.state.latest_prices["BTCUSDT"] = 51000.0

    response = client.get("/api/portfolio/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["active_positions_count"] == 1
    assert data["max_open_positions"] == 3
    assert len(data["active_trades"]) == 1

    trade = data["active_trades"][0]
    assert trade["symbol"] == "BTCUSDT"
    assert trade["unrealized_pnl_pct"] == 2.0  # (51000 - 50000) / 50000 * 100


def test_microstructure_summary_endpoint(app_and_client):
    """Verify /api/microstructure/summary returns OI regime, funding, and whale tape."""
    app, client = app_and_client
    micro_mgr = app.state.micro_mgr

    # Ingest mock OI
    micro_mgr.oi_engine.update("BTCUSDT", 60000.0, 1000.0)
    micro_mgr.oi_engine.update("BTCUSDT", 61000.0, 1100.0)  # Price UP + OI UP -> LONG_BUILDUP

    # Ingest mock Funding
    micro_mgr.funding_tracker.update_funding("BTCUSDT", 0.0001, int(time.time() * 1000) + 14400000)

    # Ingest mock Whale Trade
    micro_mgr.whale_filter.process_trade({
        "s": "BTCUSDT",
        "p": "60500.0",
        "q": "10.0",
        "m": False,  # Market BUY
        "T": int(time.time() * 1000),
    })

    response = client.get("/api/microstructure/summary?symbol=BTCUSDT")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "BTCUSDT"
    assert data["oi_regime"] == "LONG_BUILDUP"
    assert data["funding_rate"] == 0.0001
    assert data["funding_apr_pct"] > 0
    assert len(data["recent_whale_trades"]) == 1
    assert data["recent_whale_trades"][0]["badge"] == "WHALE"
    assert data["recent_whale_trades"][0]["side"] == "BUY"


def test_websocket_ping_pong(app_and_client):
    """Verify WebSocket hub responds to ping with pong and timestamp."""
    app, client = app_and_client
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "ping"})
        msg = ws.receive_json()
        assert msg["type"] == "pong"
        assert "timestamp" in msg


def test_websocket_broadcast_and_disconnect(app_and_client):
    """Verify ConnectionManager broadcasts to active clients and handles disconnected clients."""
    app, client = app_and_client
    manager = app.state.connection_manager

    with client.websocket_connect("/ws") as ws:
        assert len(manager.active_connections) == 1
        # Synchronously broadcast via event loop
        loop = asyncio.new_event_loop()
        loop.run_until_complete(manager.broadcast({"type": "test_alert", "msg": "whale_detected"}))
        loop.close()

        msg = ws.receive_json()
        assert msg["type"] == "test_alert"
        assert msg["msg"] == "whale_detected"

    # Outside the context manager, the socket is closed
    # Broadcasting again must gracefully clean up without raising an unhandled exception
    loop = asyncio.new_event_loop()
    loop.run_until_complete(manager.broadcast({"type": "test_alert", "msg": "second_test"}))
    loop.close()


def test_scanner_opportunities_endpoint(app_and_client):
    """Verify /api/scanner/opportunities returns ranked candidate setups with trade staging params."""
    app, client = app_and_client
    for i in range(30):
        p = 60000.0 + (i * 20.0)
        app.state.traders["BTCUSDT"].load_historical_candle((p, p + 50.0, p - 50.0, p, 100.0))

    response = client.get("/api/scanner/opportunities")
    assert response.status_code == 200
    opportunities = response.json()
    assert isinstance(opportunities, list)
    assert len(opportunities) >= 1
    top = opportunities[0]
    assert "symbol" in top
    assert "confluence_score" in top
    assert "current_price" in top
    assert "trade_setup" in top


def test_api_portfolio_summary_returns_extended_metrics(app_and_client):
    """Verify /api/portfolio/summary returns capital allocation, notional, and unrealized USD PnL."""
    app, client = app_and_client
    trader = app.state.traders["BTCUSDT"]
    trader.active_trade = {
        "id": 1,
        "symbol": "BTCUSDT",
        "side": "LONG",
        "entry_price": 50000.0,
        "stop_loss": 49000.0,
        "take_profit": 52000.0,
        "quantity": 0.1,
        "notional_usd": 5000.0,
        "leverage": 0.5,
        "timestamp": "2026-10-14T10:00:00",
    }
    app.state.latest_prices["BTCUSDT"] = 51000.0  # +$1000 on 0.1 BTC = +$100.00 unrealized

    response = client.get("/api/portfolio/summary")
    assert response.status_code == 200
    data = response.json()
    assert "account_equity_usd" in data
    assert "total_unrealized_pnl_usd" in data
    assert "current_equity_usd" in data
    assert data["active_positions_count"] >= 1

    active = next(t for t in data["active_trades"] if t["symbol"] == "BTCUSDT")
    assert active["unrealized_pnl_usd"] == 100.0
    assert active["unrealized_pnl_pct"] == 2.0
    assert active["current_r_multiple"] == 1.0
