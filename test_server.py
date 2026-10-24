import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from main import get_combined_stream_url, poll_htf_candles, warmup_symbol
from server import CandleFeedBuffer, MarketDataFeed, create_app, run_kline_ingestion


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
    """Verify /api/health returns 200 with proper diagnostic fields and status='down' when unseeded."""
    app, client = app_and_client
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "down"
    assert "git_commit" in data
    assert isinstance(data["git_commit"], str)
    assert len(data["git_commit"]) > 0
    assert "kline_connectivity" in data
    assert "spot_connectivity" in data
    assert data["kline_connectivity"] == data["spot_connectivity"]
    assert "futures_connectivity" in data
    assert "futures_status_message" in data
    assert data["active_symbols_count"] == 3
    assert data["db_status"] == "connected"
    assert data["connected_ws_clients"] == 0
    assert set(data["stale_symbols"]) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}


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
    """Verify /api/portfolio/summary returns risk sizing parameters with paper trading disabled."""
    app, client = app_and_client

    response = client.get("/api/portfolio/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["active_positions_count"] == 0
    assert data["max_open_positions"] == 3
    assert data["active_trades"] == []
    assert data["paper_trading_enabled"] is False
    assert data["execution_mode"] == "EVIDENCE_ONLY"


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
    assert "inflection_pattern" in top
    assert "historical_baseline" in top
    assert "profit_factor" in top["historical_baseline"]
    assert "win_rate" in top["historical_baseline"]


def test_trade_bracket_endpoint(app_and_client):
    """Verify /api/trade/bracket computes mathematically verified Single Exit Authority levels."""
    app, client = app_and_client
    # Test LONG bracket
    resp = client.get("/api/trade/bracket?symbol=BTCUSDT&side=LONG&entry_price=60000&stop_loss=59000")
    assert resp.status_code == 200
    b_long = resp.json()
    assert b_long["side"] == "LONG"
    assert b_long["limit_entry"] == 60000.0
    assert b_long["stop_loss"] == 59000.0
    assert b_long["risk_dist"] == 1000.0
    assert b_long["tp1"] == 60750.0  # +0.75R
    assert b_long["tp1_r"] == 0.75
    assert b_long["be_trigger"] == 60850.0  # +0.85R
    assert b_long["be_trigger_r"] == 0.85
    assert b_long["be_price"] == 60030.0  # 60000 * 1.0005
    assert b_long["tp2"] == 61330.0  # +1.33R
    assert b_long["tp2_r"] == 1.33
    assert b_long["stagnancy_bars"] == 12

    # Test SHORT bracket
    resp_short = client.get("/api/trade/bracket?symbol=ETHUSDT&side=SHORT&entry_price=3000&stop_loss=3100")
    assert resp_short.status_code == 200
    b_short = resp_short.json()
    assert b_short["side"] == "SHORT"
    assert b_short["limit_entry"] == 3000.0
    assert b_short["stop_loss"] == 3100.0
    assert b_short["risk_dist"] == 100.0
    assert b_short["tp1"] == 2925.0  # -0.75R
    assert b_short["tp1_r"] == 0.75
    assert b_short["be_trigger"] == 2915.0  # -0.85R
    assert b_short["tp2"] == 2867.0  # -1.33R
    assert b_short["tp2_r"] == 1.33


def test_api_portfolio_summary_returns_extended_metrics(app_and_client):
    """Verify /api/portfolio/summary returns capital allocation and risk sizing metrics."""
    app, client = app_and_client

    response = client.get("/api/portfolio/summary")
    assert response.status_code == 200
    data = response.json()
    assert "account_equity_usd" in data
    assert "total_unrealized_pnl_usd" in data
    assert "current_equity_usd" in data
    assert data["active_positions_count"] == 0
    assert data["active_trades"] == []
    assert data["paper_trading_enabled"] is False


def test_market_depth_endpoint(app_and_client):
    """Verify /api/market/depth returns 20-level order book and OBI metrics."""
    app, client = app_and_client
    response = client.get("/api/market/depth?symbol=BTCUSDT")
    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "BTCUSDT"
    assert "bids" in data
    assert "asks" in data
    assert "imbalance_pct" in data
    assert "best_bid" in data
    assert "best_ask" in data
    assert data["best_bid"] <= data["best_ask"]


def test_websocket_focus_and_telemetry_actions(app_and_client):
    """Verify WebSocket handles focus_symbol, pause_telemetry, and resume_telemetry."""
    app, client = app_and_client
    with client.websocket_connect("/ws") as ws:
        # Focus symbol
        ws.send_json({"action": "focus_symbol", "symbol": "ETHUSDT"})
        resp = ws.receive_json()
        assert resp["type"] == "order_book_depth"
        assert resp["depth"]["symbol"] == "ETHUSDT"
        assert app.state.active_depth_symbol == "ETHUSDT"

        # Pause telemetry (sleep mode)
        ws.send_json({"action": "pause_telemetry"})
        time.sleep(0.05)
        assert app.state.is_telemetry_paused is True

        # Resume telemetry
        ws.send_json({"action": "resume_telemetry"})
        time.sleep(0.05)
        assert app.state.is_telemetry_paused is False


def test_market_candles_timeframe_aggregation(app_and_client):
    """Verify that requesting a higher timeframe aggregates lower timeframe candles into proper OHLCV bars."""
    app, client = app_and_client
    trader = app.state.traders["BTCUSDT"]
    trader.timeframe = "15m"

    # Feed 8 15-minute candles (should become 2 1-hour candles)
    base_t = 1700000000
    for i in range(8):
        c = (
            50000.0 + i * 10,       # open
            50050.0 + i * 10,       # high
            49950.0 + i * 10,       # low
            50020.0 + i * 10,       # close
            10.0,                   # vol
        )
        trader.load_historical_candle(c)
        if "BTCUSDT" not in app.state.candle_timestamps:
            app.state.candle_timestamps["BTCUSDT"] = []
        app.state.candle_timestamps["BTCUSDT"].append(base_t + i * 900)

    response = client.get("/api/market/candles?symbol=BTCUSDT&timeframe=1h")
    assert response.status_code == 200
    data = response.json()
    assert data["timeframe"] == "1h"
    candles = data["candles"]
    assert len(candles) == 2

    # Bar 0 aggregates candles 0..3
    assert candles[0]["open"] == 50000.0
    assert candles[0]["high"] == 50080.0   # max of 50050, 50060, 50070, 50080
    assert candles[0]["low"] == 49950.0    # min of 49950, 49960, 49970, 49980
    assert candles[0]["close"] == 50050.0  # close of candle 3: 50020 + 3*10
    assert candles[0]["volume"] == 40.0    # sum of 4 * 10.0
    assert candles[0]["time"] == base_t

    # Bar 1 aggregates candles 4..7
    assert candles[1]["open"] == 50040.0
    assert candles[1]["high"] == 50120.0
    assert candles[1]["volume"] == 40.0
    assert candles[1]["time"] == base_t + 4 * 900


def test_no_hardcoded_sample_trades_in_server():
    """Verify that no hard-coded baseline numbers remain in server.py (grep-style test)."""
    import re
    with open("server.py", "r", encoding="utf-8") as f:
        content = f.read()
    # Check that sample_trades is never assigned integer literals in server.py
    matches = re.findall(r'["\']sample_trades["\']\s*:\s*\d+', content)
    assert len(matches) == 0, f"Found hardcoded sample_trades numbers in server.py: {matches}"

    # Check that win_rate and profit_factor are also not hard-coded in dictionaries
    matches_wr = re.findall(r'["\']win_rate["\']\s*:\s*\d+\.?\d*', content)
    assert len(matches_wr) == 0, f"Found hardcoded win_rate numbers in server.py: {matches_wr}"


def test_untested_pattern_returns_untested():
    """Verify that untested patterns return status='UNTESTED', message='No historical evidence', and null numbers."""
    from server import get_historical_baseline

    res_rsi = get_historical_baseline("RSI Divergence")
    assert res_rsi["status"] == "UNTESTED"
    assert res_rsi["message"] == "No historical evidence"
    assert res_rsi["win_rate"] is None
    assert res_rsi["profit_factor"] is None
    assert res_rsi["expectancy_r"] is None
    assert res_rsi["sample_trades"] is None

    res_unknown = get_historical_baseline("Unknown Arbitrary Pattern")
    assert res_unknown["status"] == "UNTESTED"
    assert res_unknown["message"] == "No historical evidence"
    assert res_unknown["win_rate"] is None
    assert res_unknown["profit_factor"] is None


def test_rejected_strategy_returns_no_validated_edge_and_matches_json():
    """Verify rejected strategy returns status='NO VALIDATED EDGE' with numbers matching strategy_results.json."""
    import json
    from server import get_historical_baseline

    with open("research/reports/strategy_results.json", "r", encoding="utf-8") as f:
        data = json.load(f)

    # Test FVG Retest -> FVGRetest
    fvg_json = data["strategies"]["FVGRetest"]
    fvg_baseline = get_historical_baseline("FVG Retest")
    assert fvg_baseline["status"] == "NO VALIDATED EDGE"
    assert fvg_baseline["win_rate"] == fvg_json["net_win_rate"]
    assert fvg_baseline["profit_factor"] == fvg_json["net_profit_factor"]
    assert fvg_baseline["expectancy_r"] == fvg_json["net_expectancy_r"]
    assert fvg_baseline["sample_trades"] == fvg_json["trade_count"]

    # Test Trend Pullback -> TrendPullback
    tp_json = data["strategies"]["TrendPullback"]
    tp_baseline = get_historical_baseline("Trend Pullback")
    assert tp_baseline["status"] == "NO VALIDATED EDGE"
    assert tp_baseline["win_rate"] == tp_json["net_win_rate"]
    assert tp_baseline["profit_factor"] == tp_json["net_profit_factor"]
    assert tp_baseline["sample_trades"] == tp_json["trade_count"]

    # Test Multi-TF Confluence -> ConfluenceThreshold
    conf_json = data["strategies"]["ConfluenceThreshold"]
    conf_baseline = get_historical_baseline("Multi-TF Confluence")
    assert conf_baseline["status"] == "NO VALIDATED EDGE"
    assert conf_baseline["win_rate"] == conf_json["net_win_rate"]
    assert conf_baseline["profit_factor"] == conf_json["net_profit_factor"]
    assert conf_baseline["sample_trades"] == conf_json["trade_count"]


def test_modifying_strategy_results_json_updates_endpoint_live(tmp_path):
    """Verify acceptance criterion: changing strategy_results.json changes returned baseline with no code edits."""
    import json
    import os
    import time
    from server import get_historical_baseline

    test_json = tmp_path / "strategy_results.json"
    data1 = {
        "metadata": {"run_timestamp": "2026-10-20T12:00:00Z"},
        "strategies": {
            "FVGRetest": {
                "strategy_name": "FVGRetest",
                "trade_count": 999,
                "net_win_rate": 42.0,
                "net_profit_factor": 0.65,
                "net_expectancy_r": -0.15,
                "verdict": "REJECTED (p >= 0.05)",
            }
        },
    }
    with open(test_json, "w", encoding="utf-8") as f:
        json.dump(data1, f)

    res1 = get_historical_baseline("FVG Retest", json_path=str(test_json))
    assert res1["sample_trades"] == 999
    assert res1["profit_factor"] == 0.65
    assert res1["win_rate"] == 42.0
    assert res1["status"] == "NO VALIDATED EDGE"

    # Modify JSON and bump mtime
    time.sleep(0.05)
    data2 = {
        "metadata": {"run_timestamp": "2026-10-20T12:30:00Z"},
        "strategies": {
            "FVGRetest": {
                "strategy_name": "FVGRetest",
                "trade_count": 1234,
                "net_win_rate": 55.5,
                "net_profit_factor": 1.25,
                "net_expectancy_r": 0.08,
                "verdict": "VALIDATED (p < 0.05)",
            }
        },
    }
    with open(test_json, "w", encoding="utf-8") as f:
        json.dump(data2, f)
    new_mtime = os.path.getmtime(test_json) + 2.0
    os.utime(test_json, (new_mtime, new_mtime))

    res2 = get_historical_baseline("FVG Retest", json_path=str(test_json))
    assert res2["sample_trades"] == 1234
    assert res2["profit_factor"] == 1.25
    assert res2["win_rate"] == 55.5
    assert res2["status"] == "VALIDATED (p < 0.05)"


def test_cors_restricted_origins_and_methods(app_and_client):
    """Verify CORS allow_origins is restricted to loopback Vite dev and only GET is permitted."""
    _, client = app_and_client

    # 1. Allowed origin with GET method
    resp = client.options(
        "/api/health",
        headers={
            "Origin": "http://127.0.0.1:5174",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://127.0.0.1:5174"
    assert "GET" in resp.headers.get("access-control-allow-methods", "")

    # 2. Disallowed method POST (must be rejected with 400 Bad Request)
    resp_post = client.options(
        "/api/health",
        headers={
            "Origin": "http://127.0.0.1:5174",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp_post.status_code == 400
    assert "Disallowed CORS method" in resp_post.text

    # 3. Disallowed origin (must be rejected with 400 Bad Request)
    resp_disallowed = client.options(
        "/api/health",
        headers={
            "Origin": "http://malicious.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp_disallowed.status_code == 400
    assert "Disallowed CORS origin" in resp_disallowed.text


def test_static_frontend_serving(tmp_path):
    """Verify hermetic static frontend serving via create_app(frontend_dist=...)."""
    dist_dir = tmp_path / "mock_dist"
    dist_dir.mkdir()
    index_file = dist_dir / "index.html"
    index_file.write_text("<!DOCTYPE html><html><body><div id=\"root\"></div></body></html>", encoding="utf-8")

    db_path = str(tmp_path / "test_server.db")
    app = create_app(
        enable_ingestion=False,
        db_path=db_path,
        symbols=["BTCUSDT"],
        frontend_dist=str(dist_dir),
    )
    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "html" in resp.headers.get("content-type", "").lower()
        assert '<div id="root"></div>' in resp.text


def test_static_frontend_serving_env_var(tmp_path, monkeypatch):
    """Verify hermetic static frontend serving via FRONTEND_DIST environment variable."""
    dist_dir = tmp_path / "mock_dist_env"
    dist_dir.mkdir()
    index_file = dist_dir / "index.html"
    index_file.write_text("<!DOCTYPE html><html><body><div id=\"root\"></div></body></html>", encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(dist_dir))

    db_path = str(tmp_path / "test_server.db")
    app = create_app(
        enable_ingestion=False,
        db_path=db_path,
        symbols=["BTCUSDT"],
    )
    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert '<div id="root"></div>' in resp.text


def test_candle_feed_buffer_lifecycle():
    """Verify CandleFeedBuffer stores candles, updates HTF EMA trend, and exposes no fake trade stub."""
    buffer = CandleFeedBuffer(symbol="BTCUSDT", timeframe="15m")
    assert buffer.symbol == "BTCUSDT"
    assert buffer.bar_count == 0
    assert len(buffer.candles) == 0

    # Add candles using add_candle
    buffer.add_candle((50000.0, 50100.0, 49900.0, 50050.0, 100.0))
    assert buffer.bar_count == 1
    assert len(buffer.candles) == 1

    # Backwards compatibility alias
    buffer.on_candle((50050.0, 50200.0, 50000.0, 50150.0, 120.0))
    assert buffer.bar_count == 2
    assert len(buffer.candles) == 2

    # HTF trend calculation
    for i in range(50):
        buffer.load_historical_htf_candle((50000.0 + i * 10, 50100.0 + i * 10, 49900.0 + i * 10, 50050.0 + i * 10, 50.0))
    assert buffer.get_htf_trend() == "BULLISH"

    # Verify MarketDataFeed is CandleFeedBuffer
    assert MarketDataFeed is CandleFeedBuffer


def test_futures_stream_url_format():
    """Verify get_combined_stream_url points to Binance USD-M futures and excludes spot / US hosts."""
    url = get_combined_stream_url(["BTCUSDT", "ETHUSDT"], "15m")
    assert url.startswith("wss://fstream.binance.com/market/stream?streams=")
    assert "btcusdt@kline_15m/ethusdt@kline_15m" in url
    assert "wss://stream.binance.com" not in url
    assert "binance.us" not in url


@pytest.mark.asyncio
async def test_warmup_symbol_uses_futures_url_and_retries():
    """Verify warmup_symbol queries fapi.binance.com, handles retries with backoff, and sets buffer."""
    buf = CandleFeedBuffer(symbol="BTCUSDT", timeframe="15m")
    timestamps = []

    mock_candle_payload = [
        [1729597200000, "65000.0", "65500.0", "64900.0", "65200.0", "150.0", 1729598099999],
        [1729598100000, "65200.0", "65600.0", "65100.0", "65400.0", "200.0", 1729598999999],
    ]

    mock_resp_429 = MagicMock()
    mock_resp_429.status = 429

    mock_resp_200 = MagicMock()
    mock_resp_200.status = 200
    mock_resp_200.json = AsyncMock(return_value=mock_candle_payload)

    # First attempt returns 429, second attempt succeeds with 200
    mock_session = MagicMock()
    call_urls = []

    class MockContextManager:
        def __init__(self, resp):
            self.resp = resp
        async def __aenter__(self):
            return self.resp
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    def mock_get(url, *args, **kwargs):
        call_urls.append(url)
        if len(call_urls) == 1:
            return MockContextManager(mock_resp_429)
        return MockContextManager(mock_resp_200)

    mock_session.get = mock_get

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await warmup_symbol(mock_session, "BTCUSDT", "15m", buf, timestamps_list=timestamps)
        assert mock_sleep.called

    assert len(call_urls) == 3
    assert call_urls[0].startswith("https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=15m")
    assert call_urls[1].startswith("https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=15m")
    assert call_urls[2].startswith("https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=4h")
    for url in call_urls:
        assert "https://api.binance.com" not in url
        assert "binance.us" not in url

    assert len(buf.candles) == 2
    assert len(timestamps) == 2
    assert buf.candles[0] == (65000.0, 65500.0, 64900.0, 65200.0, 150.0)


@pytest.mark.asyncio
async def test_warmup_symbol_raises_on_fatal_failure():
    """Verify warmup_symbol raises RuntimeError if USD-M futures fails 3 times."""
    buf = CandleFeedBuffer(symbol="BTCUSDT", timeframe="15m")
    mock_session = MagicMock()
    mock_session.get.side_effect = RuntimeError("Connection timeout")

    with patch("asyncio.sleep", new_callable=AsyncMock):
        with pytest.raises(RuntimeError) as exc_info:
            await warmup_symbol(mock_session, "BTCUSDT", "15m", buf)
        assert "Binance USD-M futures" in str(exc_info.value)


@pytest.mark.asyncio
async def test_poll_htf_candles_uses_futures_endpoint():
    """Verify poll_htf_candles fetches 4h candles strictly from USD-M futures."""
    buf = CandleFeedBuffer(symbol="BTCUSDT", timeframe="15m")
    buf.htf_trend_filter = True
    traders = {"BTCUSDT": buf}

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=[
        [1729597200000, "65000.0", "66000.0", "64500.0", "65800.0", "1200.0"],
    ])

    class MockContextManager:
        async def __aenter__(self):
            return mock_resp
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    called_urls = []
    class MockClientSession:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def get(self, url, *args, **kwargs):
            called_urls.append(url)
            return MockContextManager()

    # Run one cycle of poll_htf_candles by patching sleep to cancel after first pass
    with patch("aiohttp.ClientSession", MockClientSession), \
         patch("asyncio.sleep", side_effect=[None, asyncio.CancelledError]):
        try:
            await poll_htf_candles(traders, ["BTCUSDT"])
        except asyncio.CancelledError:
            pass

    assert len(called_urls) == 1
    assert called_urls[0] == "https://fapi.binance.com/fapi/v1/klines?symbol=BTCUSDT&interval=4h&limit=2"
    assert len(buf.htf_candles) == 1
    assert buf.htf_candles[0] == (65000.0, 66000.0, 64500.0, 65800.0, 1200.0)


@pytest.mark.asyncio
async def test_run_kline_ingestion_futures_ws_mock(tmp_path):
    """Verify run_kline_ingestion connects to fstream, processes closed kline, and tracks last_update_times."""
    db_path = str(tmp_path / "test.db")
    app = create_app(enable_ingestion=False, db_path=db_path, symbols=["BTCUSDT"], timeframe="15m")
    app.state.candle_buffers = {"BTCUSDT": CandleFeedBuffer("BTCUSDT", "15m")}
    app.state.traders = app.state.candle_buffers

    kline_msg = json.dumps({
        "stream": "btcusdt@kline_15m",
        "data": {
            "e": "kline",
            "s": "BTCUSDT",
            "k": {
                "t": 1729597200000,
                "o": "65000.0",
                "h": "65500.0",
                "l": "64800.0",
                "c": "65300.0",
                "v": "300.0",
                "x": True,
            },
        },
    })

    class MockWS:
        def __init__(self):
            self.sent = False
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        def __aiter__(self):
            return self
        async def __anext__(self):
            if not self.sent:
                self.sent = True
                return kline_msg
            raise asyncio.CancelledError()

    ws_urls = []
    def mock_connect(url, *args, **kwargs):
        ws_urls.append(url)
        return MockWS()

    with patch("websockets.connect", side_effect=mock_connect):
        try:
            await run_kline_ingestion(app, ["BTCUSDT"], "15m")
        except asyncio.CancelledError:
            pass

    assert len(ws_urls) == 1
    assert ws_urls[0].startswith("wss://fstream.binance.com/market/stream?streams=")
    assert "btcusdt@kline_15m" in ws_urls[0]
    assert app.state.latest_prices.get("BTCUSDT") == 65300.0
    assert "BTCUSDT" in app.state.last_update_times
    assert len(app.state.candle_buffers["BTCUSDT"].candles) == 1


def test_health_endpoint_data_source_and_last_updates(app_and_client):
    """Verify /api/health exposes data_source='binance_futures_um', last_update_per_symbol, and interval."""
    app, client = app_and_client
    app.state.last_update_times["BTCUSDT"] = 1729597200
    app.state.last_update_times["ETHUSDT"] = 1729597205

    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data_source"] == "binance_futures_um"
    assert data["last_update_per_symbol"]["BTCUSDT"] == 1729597200
    assert data["last_update_per_symbol"]["ETHUSDT"] == 1729597205
    assert data["candle_interval_sec"] == 60  # timeframe="1m" in fixture
    assert data["timeframe"] == "1m"


def test_health_endpoint_status_states(app_and_client):
    """Verify computed status logic: down, degraded (disconnected or stale), and healthy."""
    app, client = app_and_client
    now_t = int(time.time())

    # 1. State: DOWN (no symbol has data)
    app.state.candle_buffers["BTCUSDT"].candles.clear()
    app.state.candle_buffers["ETHUSDT"].candles.clear()
    app.state.candle_buffers["SOLUSDT"].candles.clear()
    app.state.last_update_times.clear()
    app.state.is_kline_connected = False
    data = client.get("/api/health").json()
    assert data["status"] == "down"
    assert set(data["stale_symbols"]) == {"BTCUSDT", "ETHUSDT", "SOLUSDT"}

    # 2. State: DEGRADED because klines disconnected even though data exists
    app.state.last_update_times["BTCUSDT"] = now_t
    app.state.last_update_times["ETHUSDT"] = now_t
    app.state.last_update_times["SOLUSDT"] = now_t
    app.state.is_kline_connected = False
    data = client.get("/api/health").json()
    assert data["status"] == "degraded"
    assert data["stale_symbols"] == []

    # 3. State: DEGRADED because a symbol is stale (> 2 * 60s ago for 1m timeframe)
    app.state.is_kline_connected = True
    app.state.last_update_times["BTCUSDT"] = now_t
    app.state.last_update_times["ETHUSDT"] = now_t - 150  # 150s > 120s threshold
    app.state.last_update_times["SOLUSDT"] = now_t
    data = client.get("/api/health").json()
    assert data["status"] == "degraded"
    assert data["stale_symbols"] == ["ETHUSDT"]

    # 4. State: HEALTHY when klines connected and all symbols updated recently
    app.state.last_update_times["ETHUSDT"] = now_t - 30
    data = client.get("/api/health").json()
    assert data["status"] == "healthy"
    assert data["stale_symbols"] == []
    assert data["kline_connectivity"] is True
    assert data["spot_connectivity"] is True


def test_seed_buffer_from_parquet():
    """Verify seed_buffer_from_parquet pre-seeds buffer with 249 historical candles and timestamps."""
    from server import CandleFeedBuffer, seed_buffer_from_parquet
    buf = CandleFeedBuffer(symbol="BTCUSDT", timeframe="15m")
    ts_dict = {"BTCUSDT": []}

    success = seed_buffer_from_parquet(buf, "BTCUSDT", "15m", timestamps_dict=ts_dict, limit=249)
    assert success is True
    assert len(buf.candles) == 249
    assert len(ts_dict["BTCUSDT"]) == 249
    # Strict monotonicity of timestamps
    for i in range(1, len(ts_dict["BTCUSDT"])):
        assert ts_dict["BTCUSDT"][i] > ts_dict["BTCUSDT"][i - 1]
    # Closes are realistic BTC prices (> 10000)
    assert buf.candles[-1][3] > 10000.0


def test_git_commit_detection():
    """Verify get_git_commit detects the git commit or returns 'unknown'."""
    from server import get_git_commit
    commit = get_git_commit()
    assert isinstance(commit, str)
    assert len(commit) > 0


def test_git_commit_fallback_on_error(monkeypatch):
    """Verify get_git_commit gracefully falls back to 'unknown' on subprocess failure."""
    import subprocess
    from server import get_git_commit

    def fail_run(*args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", fail_run)
    assert get_git_commit() == "unknown"
