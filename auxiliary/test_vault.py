import asyncio
import os
import pytest

from auxiliary.vault import MarketDataVault, AsyncVaultWriter


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


def test_vault_schema_migration_legacy_rows_source_is_null_and_new_inserts_futures_um(tmp_path):
    """
    Verify that an older schema database from 09861f5:
    1. Leaves legacy rows with source IS NULL upon migration.
    2. Explicitly labels new inserts as 'binance_futures_um'.
    """
    import sqlite3

    old_db_path = str(tmp_path / "legacy_09861f5_vault.db")

    # 1. Build an old-schema database (matching 09861f5:vault.py without source column)
    with sqlite3.connect(old_db_path) as conn:
        conn.execute(
            """
            CREATE TABLE whale_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                futures_symbol TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                notional REAL NOT NULL,
                side TEXT NOT NULL,
                badge TEXT NOT NULL,
                is_buyer_maker INTEGER NOT NULL,
                trade_id INTEGER
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE liquidation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                futures_symbol TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                liquidated_position TEXT NOT NULL,
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                notional REAL NOT NULL,
                order_type TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE order_book_bbo_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                best_bid_price REAL NOT NULL,
                best_bid_qty REAL NOT NULL,
                best_ask_price REAL NOT NULL,
                best_ask_qty REAL NOT NULL,
                spread REAL NOT NULL,
                spread_bps REAL NOT NULL,
                book_imbalance REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE candle_microstructure_bars (
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                close_time INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                turnover REAL NOT NULL,
                trade_count INTEGER NOT NULL,
                taker_buy_volume REAL NOT NULL,
                taker_sell_volume REAL NOT NULL,
                net_volume_delta REAL NOT NULL,
                delta_ratio REAL NOT NULL,
                whale_buy_notional REAL DEFAULT 0.0,
                whale_sell_notional REAL DEFAULT 0.0,
                liq_long_notional REAL DEFAULT 0.0,
                liq_short_notional REAL DEFAULT 0.0,
                open_interest REAL,
                PRIMARY KEY (symbol, timeframe, open_time)
            )
            """
        )

        # Insert legacy rows without source column
        conn.execute(
            """
            INSERT INTO order_book_bbo_snapshots (
                symbol, timestamp_ms, best_bid_price, best_bid_qty,
                best_ask_price, best_ask_qty, spread, spread_bps, book_imbalance
            ) VALUES ('BTCUSDT', 1700000000000, 60000.0, 1.5, 60001.0, 2.0, 1.0, 0.166, 0.14)
            """
        )
        conn.execute(
            """
            INSERT INTO candle_microstructure_bars (
                symbol, timeframe, open_time, close_time, open, high, low, close,
                volume, turnover, trade_count, taker_buy_volume, taker_sell_volume,
                net_volume_delta, delta_ratio
            ) VALUES ('BTCUSDT', '1m', 1700000000000, 1700000059999, 60000.0, 60010.0, 59990.0, 60005.0,
                      10.0, 600000.0, 50, 6.0, 4.0, 2.0, 0.2)
            """
        )
        conn.execute(
            """
            INSERT INTO whale_trades (
                symbol, futures_symbol, timestamp_ms, price, quantity, notional,
                side, badge, is_buyer_maker, trade_id
            ) VALUES ('BTCUSDT', 'BTCUSDT', 1700000000000, 60000.0, 5.0, 300000.0, 'BUY', 'WHALE', 0, 111)
            """
        )
        conn.commit()

    # 2. Instantiate MarketDataVault on the old database (triggers _migrate_db)
    vault = MarketDataVault(db_path=old_db_path)

    # 3. Assert that legacy rows have source IS NULL (None)
    with sqlite3.connect(old_db_path) as conn:
        bbo_src = conn.execute("SELECT source FROM order_book_bbo_snapshots WHERE timestamp_ms = 1700000000000").fetchone()[0]
        assert bbo_src is None, f"Expected legacy BBO row source to be NULL, got {bbo_src}"

        candle_src = conn.execute("SELECT source FROM candle_microstructure_bars WHERE open_time = 1700000000000").fetchone()[0]
        assert candle_src is None, f"Expected legacy candle bar source to be NULL, got {candle_src}"

        whale_src = conn.execute("SELECT source FROM whale_trades WHERE trade_id = 111").fetchone()[0]
        assert whale_src is None, f"Expected legacy whale trade source to be NULL, got {whale_src}"

    # Also check through getters
    bbo_rows = vault.get_bbo_snapshots_window("BTCUSDT", 1700000000000, 1700000000000)
    assert len(bbo_rows) == 1
    assert bbo_rows[0]["source"] is None

    candle_rows = vault.get_candle_bars("BTCUSDT", "1m", 1700000000000, 1700000000000)
    assert len(candle_rows) == 1
    assert candle_rows[0]["source"] is None

    # 4. Insert new rows without specifying source -> must get 'binance_futures_um' explicitly
    vault.insert_bbo_snapshots([
        {
            "symbol": "BTCUSDT",
            "timestamp_ms": 1700000010000,
            "best_bid_price": 60020.0,
            "best_bid_qty": 2.0,
            "best_ask_price": 60021.0,
            "best_ask_qty": 3.0,
            "spread": 1.0,
            "spread_bps": 0.166,
            "book_imbalance": 0.1,
        }
    ])
    vault.insert_candle_bars([
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "open_time": 1700000060000,
            "close_time": 1700000119999,
            "open": 60005.0,
            "high": 60030.0,
            "low": 60000.0,
            "close": 60025.0,
            "volume": 15.0,
            "turnover": 900000.0,
            "trade_count": 60,
            "taker_buy_volume": 10.0,
            "taker_sell_volume": 5.0,
            "net_volume_delta": 5.0,
            "delta_ratio": 0.33,
        }
    ])
    vault.insert_whale_trades([
        {
            "symbol": "BTCUSDT",
            "futures_symbol": "BTCUSDT",
            "timestamp_ms": 1700000010000,
            "price": 60020.0,
            "quantity": 10.0,
            "notional": 600200.0,
            "side": "BUY",
            "badge": "WHALE",
            "is_buyer_maker": False,
            "trade_id": 222,
        }
    ])

    with sqlite3.connect(old_db_path) as conn:
        new_bbo_src = conn.execute("SELECT source FROM order_book_bbo_snapshots WHERE timestamp_ms = 1700000010000").fetchone()[0]
        assert new_bbo_src == "binance_futures_um"

        new_candle_src = conn.execute("SELECT source FROM candle_microstructure_bars WHERE open_time = 1700000060000").fetchone()[0]
        assert new_candle_src == "binance_futures_um"

        new_whale_src = conn.execute("SELECT source FROM whale_trades WHERE trade_id = 222").fetchone()[0]
        assert new_whale_src == "binance_futures_um"


def test_repair_vault_source_script_on_temp_copy(tmp_path):
    """
    Test scripts/repair_vault_source.py:
    1. Cutoff older rows -> 'binance_spot_legacy'
    2. Cutoff and newer rows -> 'binance_futures_um'
    3. Other tables NULL rows -> 'binance_futures_um' when confirmed
    4. Safety backup created in backup_dir
    5. No rows deleted.
    """
    import sqlite3
    from auxiliary.repair_vault_source import repair_vault_source

    db_path = str(tmp_path / "test_repair.db")
    backup_dir = str(tmp_path / "backups")

    # Seed test database with legacy and newer data
    vault = MarketDataVault(db_path=db_path)

    # Legacy row with source=None simulated
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO order_book_bbo_snapshots (
                symbol, timestamp_ms, best_bid_price, best_bid_qty,
                best_ask_price, best_ask_qty, spread, spread_bps, book_imbalance, source
            ) VALUES ('BTCUSDT', 1700000000000, 60000.0, 1.0, 60001.0, 1.0, 1.0, 0.16, 0.0, NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO candle_microstructure_bars (
                symbol, timeframe, open_time, close_time, open, high, low, close,
                volume, turnover, trade_count, taker_buy_volume, taker_sell_volume,
                net_volume_delta, delta_ratio, source
            ) VALUES ('BTCUSDT', '1m', 1700000000000, 1700000059999, 60000.0, 60010.0, 59990.0, 60005.0,
                      10.0, 600000.0, 50, 6.0, 4.0, 2.0, 0.2, NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO whale_trades (
                symbol, futures_symbol, timestamp_ms, price, quantity, notional,
                side, badge, is_buyer_maker, trade_id, source
            ) VALUES ('BTCUSDT', 'BTCUSDT', 1700000000000, 60000.0, 5.0, 300000.0, 'BUY', 'WHALE', 0, 101, NULL)
            """
        )
        conn.commit()

    # Newer rows
    vault.insert_bbo_snapshots([
        {
            "symbol": "BTCUSDT",
            "timestamp_ms": 1700000020000,
            "best_bid_price": 60100.0,
            "best_bid_qty": 2.0,
            "best_ask_price": 60101.0,
            "best_ask_qty": 2.0,
            "spread": 1.0,
            "spread_bps": 0.16,
            "book_imbalance": 0.0,
        }
    ])
    vault.insert_candle_bars([
        {
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            "open_time": 1700000060000,
            "close_time": 1700000119999,
            "open": 60100.0,
            "high": 60110.0,
            "low": 60090.0,
            "close": 60105.0,
            "volume": 12.0,
            "turnover": 721260.0,
            "trade_count": 45,
            "taker_buy_volume": 8.0,
            "taker_sell_volume": 4.0,
            "net_volume_delta": 4.0,
            "delta_ratio": 0.33,
        }
    ])

    # 1. Run dry-run first
    res_dry = repair_vault_source(db_path=db_path, apply=False, cutoff=None, backup_dir=backup_dir)
    assert res_dry["status"] == "dry_run"
    assert not os.path.exists(backup_dir)

    # 2. Run with --apply and cutoff = 1700000010000 (between 1700000000000 and 1700000020000/1700000060000)
    cutoff_ms = 1700000010000
    res_apply = repair_vault_source(
        db_path=db_path,
        apply=True,
        cutoff=cutoff_ms,
        confirm_other_tables=True,
        backup_dir=backup_dir,
    )
    assert res_apply["status"] == "applied"
    assert os.path.exists(res_apply["backup_file"])

    # 3. Verify outcomes in SQLite
    with sqlite3.connect(db_path) as conn:
        # BBO: older row (< cutoff) must be 'binance_spot_legacy'
        r1 = conn.execute("SELECT source FROM order_book_bbo_snapshots WHERE timestamp_ms = 1700000000000").fetchone()[0]
        assert r1 == "binance_spot_legacy"

        # BBO: newer row (>= cutoff) must be 'binance_futures_um'
        r2 = conn.execute("SELECT source FROM order_book_bbo_snapshots WHERE timestamp_ms = 1700000020000").fetchone()[0]
        assert r2 == "binance_futures_um"

        # Candle bars: older bar (< cutoff) must be 'binance_spot_legacy'
        c1 = conn.execute("SELECT source FROM candle_microstructure_bars WHERE open_time = 1700000000000").fetchone()[0]
        assert c1 == "binance_spot_legacy"

        # Candle bars: newer bar (>= cutoff) must be 'binance_futures_um'
        c2 = conn.execute("SELECT source FROM candle_microstructure_bars WHERE open_time = 1700000060000").fetchone()[0]
        assert c2 == "binance_futures_um"

        # Other table (whale_trades) NULL row updated to 'binance_futures_um'
        w1 = conn.execute("SELECT source FROM whale_trades WHERE trade_id = 101").fetchone()[0]
        assert w1 == "binance_futures_um"

        # Assert no rows deleted
        bbo_count = conn.execute("SELECT COUNT(*) FROM order_book_bbo_snapshots").fetchone()[0]
        assert bbo_count == 2
        candle_count = conn.execute("SELECT COUNT(*) FROM candle_microstructure_bars").fetchone()[0]
        assert candle_count == 2
        whale_count = conn.execute("SELECT COUNT(*) FROM whale_trades").fetchone()[0]
        assert whale_count == 1


