import asyncio
import time
import pytest
from fastapi.testclient import TestClient

from server import create_app


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



