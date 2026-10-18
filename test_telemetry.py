import asyncio
import io
import sys
from unittest.mock import AsyncMock, patch
import pytest

from indicators import format_price
from main import print_multi_coin_radar, warmup_all_traders
from risk import PortfolioRiskManager
from trader import PaperTrader


def test_format_price_boundaries():
    """Verify format_price outputs proper strings for boundary values."""
    assert format_price(0.0) == "$0.00"
    assert format_price(-0.0) == "$0.00"
    assert format_price(0.005) == "$0.005000"
    assert format_price(0.01) == "$0.0100"
    assert format_price(0.9999) == "$0.9999"
    assert format_price(1.0) == "$1.00"
    assert format_price(99999.99) == "$99,999.99"


def test_print_multi_coin_radar_with_live_prices(tmp_path, capsys):
    """Verify print_multi_coin_radar uses latest_prices and formats sub-dollar coins correctly."""
    db_path = str(tmp_path / "telemetry_test.db")
    risk_manager = PortfolioRiskManager(db_path=db_path)

    trader_btc = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=db_path)
    trader_doge = PaperTrader(symbol="DOGEUSDT", timeframe="1m", db_path=db_path)

    # Populate 1 candle for each
    trader_btc.load_historical_candle((80000.0, 80100.0, 79900.0, 80000.0, 1000.0))
    trader_doge.load_historical_candle((0.1200, 0.1250, 0.1180, 0.1200, 50000.0))

    traders = {"BTCUSDT": trader_btc, "DOGEUSDT": trader_doge}
    latest_prices = {"BTCUSDT": 80550.0, "DOGEUSDT": 0.1245}

    print_multi_coin_radar(traders, risk_manager, "1m", latest_prices=latest_prices)
    captured = capsys.readouterr().out

    # Verify both symbols appear in output
    assert "BTCUSDT" in captured
    assert "DOGEUSDT" in captured
    # Verify DOGE formatted with 4 decimal places
    assert "$0.1245" in captured
    # Verify BTC formatted with 2 decimal places and thousands comma
    assert "$80,550.00" in captured


@pytest.mark.asyncio
async def test_warmup_all_traders_fault_isolation(tmp_path):
    """Verify that a failure in one symbol drops only that symbol and allows others to proceed."""
    db_path = str(tmp_path / "warmup_fault_test.db")
    trader_btc = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=db_path)
    trader_fail = PaperTrader(symbol="FAILUSDT", timeframe="1m", db_path=db_path)

    traders = {"BTCUSDT": trader_btc, "FAILUSDT": trader_fail}
    symbols = ["BTCUSDT", "FAILUSDT"]

    async def mock_warmup(session, sym, tf, trader):
        if sym == "FAILUSDT":
            raise RuntimeError("Binance 429 Rate Limited")
        trader.load_historical_candle((50000.0, 50100.0, 49900.0, 50000.0, 100.0))

    with patch("main.warmup_symbol", side_effect=mock_warmup):
        await warmup_all_traders(traders, symbols, "1m")

    # FAILUSDT must be dropped from traders and symbols
    assert "FAILUSDT" not in traders
    assert "FAILUSDT" not in symbols
    # BTCUSDT must remain
    assert "BTCUSDT" in traders
    assert "BTCUSDT" in symbols
    assert len(trader_btc.candles) == 1
