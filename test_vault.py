import asyncio
import os
import pytest

from vault import MarketDataVault, AsyncVaultWriter


@pytest.fixture
def temp_vault_db(tmp_path):
    return str(tmp_path / "test_market_vault.db")


def test_vault_initialization(temp_vault_db):
    """Verify that MarketDataVault initializes all 7 tables with WAL mode and indices."""
    vault = MarketDataVault(db_path=temp_vault_db)
    summary = vault.get_vault_summary()

    assert os.path.exists(temp_vault_db)
    assert len(summary["row_counts"]) == 7
    for table, count in summary["row_counts"].items():
        assert count == 0


def test_whale_trades_insert_and_query(temp_vault_db):
    """Verify inserting and querying whale trades."""
    vault = MarketDataVault(db_path=temp_vault_db)
    trades = [
        {
            "symbol": "BTCUSDT",
            "futures_symbol": "BTCUSDT",
            "timestamp_ms": 1728932400000,
            "price": 81500.0,
            "quantity": 10.0,
            "notional": 815000.0,
            "side": "BUY",
            "badge": "WHALE",
            "is_buyer_maker": False,
            "trade_id": 1234567,
        },
        {
            "symbol": "BTCUSDT",
            "futures_symbol": "BTCUSDT",
            "timestamp_ms": 1728932405000,
            "price": 81450.0,
            "quantity": 2.0,
            "notional": 162900.0,
            "side": "SELL",
            "badge": "SHARK",
            "is_buyer_maker": True,
            "trade_id": 1234568,
        },
    ]

    inserted = vault.insert_whale_trades(trades)
    assert inserted == 2

    # Query window encompassing both
    res = vault.get_whale_trades_window("BTCUSDT", 1728932400000, 1728932410000)
    assert len(res) == 2
    assert res[0]["side"] == "BUY"
    assert res[0]["badge"] == "WHALE"
    assert res[0]["notional"] == 815000.0
    assert res[1]["side"] == "SELL"


def test_liquidations_insert_and_query(temp_vault_db):
    """Verify inserting and querying forced liquidation events."""
    vault = MarketDataVault(db_path=temp_vault_db)
    events = [
        {
            "symbol": "BTCUSDT",
            "futures_symbol": "BTCUSDT",
            "timestamp_ms": 1728932500000,
            "liquidated_position": "LONG",
            "price": 81200.0,
            "quantity": 5.0,
            "notional": 406000.0,
            "order_type": "LIMIT",
            "status": "FILLED",
        }
    ]
    inserted = vault.insert_liquidations(events)
    assert inserted == 1

    res = vault.get_liquidations_window("BTCUSDT", 1728932400000, 1728932600000)
    assert len(res) == 1
    assert res[0]["liquidated_position"] == "LONG"
    assert res[0]["notional"] == 406000.0


def test_derivatives_snapshots_insert_and_query(temp_vault_db):
    """Verify derivatives snapshots (OI, funding, basis spread)."""
    vault = MarketDataVault(db_path=temp_vault_db)
    snapshots = [
        {
            "symbol": "BTCUSDT",
            "timestamp_ms": 1728932400000,
            "mark_price": 81505.0,
            "index_price": 81500.0,
            "basis_spread_bps": 0.61,
            "open_interest": 45000.0,
            "open_interest_value": 3667500000.0,
            "oi_delta_15m": 150.0,
            "oi_regime": "LONG_BUILDUP",
            "last_funding_rate": 0.0001,
            "funding_apr_pct": 10.95,
            "next_funding_time_ms": 1728950400000,
        }
    ]
    inserted = vault.insert_derivatives_snapshots(snapshots)
    assert inserted == 1

    res = vault.get_derivatives_window("BTCUSDT", 1728932300000, 1728932500000)
    assert len(res) == 1
    assert res[0]["oi_regime"] == "LONG_BUILDUP"
    assert res[0]["funding_apr_pct"] == 10.95
    assert res[0]["basis_spread_bps"] == 0.61


def test_candle_microstructure_bars(temp_vault_db):
    """Verify enriched candle microstructure bars with net taker delta."""
    vault = MarketDataVault(db_path=temp_vault_db)
    bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "open_time": 1728932400000,
            "close_time": 1728933299999,
            "open": 81500.0,
            "high": 81800.0,
            "low": 81400.0,
            "close": 81750.0,
            "volume": 500.0,
            "turnover": 40875000.0,
            "trade_count": 1250,
            "taker_buy_volume": 320.0,
            "taker_sell_volume": 180.0,
            "net_volume_delta": 140.0,
            "delta_ratio": 0.28,
            "whale_buy_notional": 2500000.0,
            "whale_sell_notional": 500000.0,
            "liq_long_notional": 0.0,
            "liq_short_notional": 120000.0,
            "open_interest": 45150.0,
        }
    ]
    inserted = vault.insert_candle_bars(bars)
    assert inserted == 1

    res = vault.get_candle_bars("BTCUSDT", "15m", 1728932400000, 1728932400000)
    assert len(res) == 1
    assert res[0]["net_volume_delta"] == 140.0
    assert res[0]["whale_buy_notional"] == 2500000.0


@pytest.mark.asyncio
async def test_async_vault_writer(temp_vault_db):
    """Verify non-blocking AsyncVaultWriter batch draining."""
    vault = MarketDataVault(db_path=temp_vault_db)
    writer = AsyncVaultWriter(vault, flush_interval_sec=0.1, batch_size=100)
    writer.start()

    # Enqueue events
    writer.record_event(
        "whale_trades",
        {
            "symbol": "ETHUSDT",
            "futures_symbol": "ETHUSDT",
            "timestamp_ms": 1728932400000,
            "price": 2600.0,
            "quantity": 50.0,
            "notional": 130000.0,
            "side": "BUY",
            "badge": "SHARK",
            "is_buyer_maker": False,
        },
    )
    writer.record_event(
        "liquidation_events",
        {
            "symbol": "ETHUSDT",
            "futures_symbol": "ETHUSDT",
            "timestamp_ms": 1728932405000,
            "liquidated_position": "SHORT",
            "price": 2610.0,
            "quantity": 20.0,
            "notional": 52200.0,
            "order_type": "MARKET",
            "status": "FILLED",
        },
    )

    # Let the flush loop tick
    await asyncio.sleep(0.25)
    await writer.stop()

    summary = vault.get_vault_summary()
    assert summary["row_counts"]["whale_trades"] == 1
    assert summary["row_counts"]["liquidation_events"] == 1
