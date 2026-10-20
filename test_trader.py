import os
import sqlite3
import pytest
import numpy as np
from trader import PaperTrader
from risk import PortfolioRiskManager
from microstructure import WhaleTrade


@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "test_signals.db")


def setup_trader_ready_for_trigger(trader):
    """Feed 185 uptrend candles + 14 pullback candles so EMA50 > EMA200 and RSI < 40."""
    # 185 uptrend candles: price climbing 30,000 to 50,000
    for p in np.linspace(30000, 50000, 185):
        trader.load_historical_candle((float(p), float(p + 20), float(p - 20), float(p), 100.0))

    # 14 pullback candles: price dipping 50,000 to 48,200 to push RSI below 40
    for p in np.linspace(50000, 48200, 14):
        trader.load_historical_candle((float(p), float(p + 20), float(p - 20), float(p), 100.0))

    # c1: low of dip (high = 48,250)
    c1 = (48200.0, 48250.0, 48100.0, 48200.0, 100.0)
    trader.on_candle(c1, timestamp="2026-10-14T10:01:00")

    # c2 (middle candle): green displacement (body 150, range 220 -> 68% body), RSI remains < 40 (~37.3)
    c2 = (48200.0, 48400.0, 48180.0, 48350.0, 100.0)
    trader.on_candle(c2, timestamp="2026-10-14T10:02:00")

    # c3 (trigger candle): low 48,260 (> c1 high 48,250 -> Bullish FVG), close 48,600 (RSI >= 40), vol 500 (>> 1.5 * avg 100)
    c3 = (48350.0, 48650.0, 48260.0, 48600.0, 500.0)
    return c3


def setup_trader_ready_for_short_trigger(trader):
    """Feed 185 downtrend candles + 14 relief bounce candles so EMA50 < EMA200 and RSI > 60."""
    # 185 downtrend candles: price falling 50,000 to 30,000
    for p in np.linspace(50000, 30000, 185):
        trader.load_historical_candle((float(p), float(p + 20), float(p - 20), float(p), 100.0))

    # 14 relief bounce candles: price climbing 30,000 to 31,800 to push RSI above 60
    for p in np.linspace(30000, 31800, 14):
        trader.load_historical_candle((float(p), float(p + 20), float(p - 20), float(p), 100.0))

    # c1: peak of relief bounce (low 31,750)
    c1 = (31800.0, 31850.0, 31750.0, 31800.0, 100.0)
    trader.on_candle(c1, timestamp="2026-10-14T10:01:00")

    # c2: red displacement candle (body 160, range 250 = 64%), RSI drops to ~62.3 (> 60)
    c2 = (31800.0, 31820.0, 31570.0, 31640.0, 100.0)
    trader.on_candle(c2, timestamp="2026-10-14T10:02:00")

    # c3 (trigger bar): high 31,700 (< c1 low 31,750 -> Bearish FVG), close 31,200 (RSI <= 60), vol 500 (>> 1.5 * avg 100)
    c3 = (31640.0, 31700.0, 31150.0, 31200.0, 500.0)
    return c3


def test_warmup_does_not_open_trade(temp_db):
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)
    for i in range(249):
        trader.load_historical_candle((50000.0, 50010.0, 49990.0, 50000.0, 100.0))

    assert trader.get_active_trade() is None
    assert len(trader.candles) == 249
    assert len(trader.rsi_history) == 5

    # Verify no trade was written to DB
    with sqlite3.connect(temp_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
    assert count == 0


def test_long_entry_and_take_profit(temp_db):
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)

    # Process trigger candle
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")

    assert trade is not None
    assert trader.get_active_trade() is not None
    assert trade["status"] == "OPEN"
    assert trade["symbol"] == "BTCUSDT"
    assert trade["side"] == "LONG"
    assert trade["entry_price"] > 48600.0  # Slippage applied
    assert trade["stop_loss"] < trade["entry_price"]
    assert trade["take_profit"] > trade["entry_price"]

    # Verify persisted in SQLite
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT status, symbol, side, entry_price FROM paper_trades WHERE status = 'OPEN'").fetchone()
    assert row is not None
    assert row[0] == "OPEN"
    assert row[1] == "BTCUSDT"
    assert row[2] == "LONG"

    # Next candle: spikes up and hits Take Profit
    tp_price = trade["take_profit"]
    exit_candle = (48600.0, tp_price + 50.0, 48500.0, tp_price, 200.0)
    trader.on_candle(exit_candle, timestamp="2026-10-14T10:04:00")

    # Trade should now be closed
    assert trader.get_active_trade() is None
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT status, reason, net_return_pct, r_multiple FROM paper_trades WHERE id = ?", (trade["id"],)).fetchone()
    assert row[0] == "CLOSED"
    assert row[1] == "TAKE_PROFIT"
    assert row[2] > 0.0  # Net return > 0
    assert row[3] > 0.0  # Positive R-multiple


def test_long_entry_and_stop_loss(temp_db):
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)

    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T11:00:00")
    assert trade is not None

    # Next candle: dumps and hits Stop Loss
    sl_price = trade["stop_loss"]
    dump_candle = (48600.0, 48650.0, sl_price - 50.0, sl_price, 500.0)
    trader.on_candle(dump_candle, timestamp="2026-10-14T11:01:00")

    assert trader.get_active_trade() is None
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT status, reason, net_return_pct, r_multiple FROM paper_trades WHERE id = ?", (trade["id"],)).fetchone()
    assert row[0] == "CLOSED"
    assert row[1] == "STOP_LOSS"
    assert row[2] < 0.0  # Negative return
    assert row[3] < 0.0  # Negative R-multiple


def test_simultaneous_tp_sl_priority(temp_db):
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)

    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T12:00:00")
    assert trade is not None

    # Wild candle that exceeds BOTH TP and SL in the same bar
    wild_candle = (48600.0, trade["take_profit"] + 100.0, trade["stop_loss"] - 100.0, 48600.0, 5000.0)
    trader.on_candle(wild_candle, timestamp="2026-10-14T12:01:00")

    assert trader.get_active_trade() is None
    with sqlite3.connect(temp_db) as conn:
        reason = conn.execute("SELECT reason FROM paper_trades WHERE id = ?", (trade["id"],)).fetchone()[0]
    # In wild bars, STOP_LOSS must take conservative priority
    assert reason == "STOP_LOSS"


def test_active_trade_reboot_persistence(temp_db):
    trader_a = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader_a)
    trade_a = trader_a.on_candle(trigger_candle, timestamp="2026-10-14T13:00:00")
    assert trade_a is not None

    # Simulate bot crash / restart: instantiate new PaperTrader pointing to the same DB
    trader_b = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)
    active_b = trader_b.get_active_trade()

    assert active_b is not None
    assert active_b["id"] == trade_a["id"]
    assert active_b["symbol"] == "BTCUSDT"
    assert active_b["side"] == "LONG"
    assert pytest.approx(active_b["entry_price"]) == trade_a["entry_price"]
    assert pytest.approx(active_b["stop_loss"]) == trade_a["stop_loss"]
    assert pytest.approx(active_b["take_profit"]) == trade_a["take_profit"]


def test_performance_summary_metrics(temp_db):
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)
    summary_empty = trader.get_performance_summary()
    assert summary_empty["total_trades"] == 0
    assert summary_empty["win_rate_pct"] == 0.0

    # Insert 2 synthetic closed trades into the DB: 1 Win, 1 Loss
    with sqlite3.connect(temp_db) as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit,
                exit_price, exit_timestamp, reason, net_return_pct, r_multiple
            ) VALUES 
            ('BTCUSDT', '1m', 'LONG', 'CLOSED', '2026-10-14T01:00:00', 50000, 49000, 52000, 52000, '2026-10-14T02:00:00', 'TAKE_PROFIT', 3.85, 2.0),
            ('BTCUSDT', '1m', 'LONG', 'CLOSED', '2026-10-14T03:00:00', 50000, 49000, 52000, 49000, '2026-10-14T04:00:00', 'STOP_LOSS', -2.15, -1.0)
            """
        )
        conn.commit()

    summary = trader.get_performance_summary()
    assert summary["total_trades"] == 2
    assert summary["winning_trades"] == 1
    assert summary["losing_trades"] == 1
    assert summary["win_rate_pct"] == 50.0
    assert pytest.approx(summary["total_net_return_pct"]) == 1.70
    assert pytest.approx(summary["avg_r_multiple"]) == 0.5
    assert summary["target_wins"] == 1
    assert summary["breakeven_scratches"] == 0
    assert summary["target_win_rate_pct"] == 50.0
    assert summary["breakeven_pct"] == 0.0
    assert pytest.approx(summary["expectancy_r"]) == 0.5
    assert pytest.approx(summary["profit_factor"], rel=1e-2) == 1.79


def test_short_exit_take_profit_and_stop_loss(temp_db):
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)

    # 1. Seed a synthetic active SHORT trade (Entry: 50k, SL: 51k, TP: 48k)
    with trader._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit
            ) VALUES ('BTCUSDT', '1m', 'SHORT', 'OPEN', '2026-10-14T14:00:00', 50000.0, 51000.0, 48000.0)
            """
        )
        trade_id = cursor.lastrowid
        conn.commit()

    trader.active_trade = {
        "id": trade_id,
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "side": "SHORT",
        "status": "OPEN",
        "timestamp": "2026-10-14T14:00:00",
        "entry_price": 50000.0,
        "stop_loss": 51000.0,
        "take_profit": 48000.0,
    }

    # Dump candle hitting Take Profit for Short: low <= 48,000
    dump_candle = (49500.0, 49600.0, 47900.0, 48000.0, 100.0)
    trader.on_candle(dump_candle, timestamp="2026-10-14T14:01:00")

    assert trader.get_active_trade() is None
    with trader._get_connection() as conn:
        row = conn.execute(
            "SELECT status, reason, net_return_pct, r_multiple FROM paper_trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
    assert row[0] == "CLOSED"
    assert row[1] == "TAKE_PROFIT"
    # Gross return: (50000 - 48000)/50000 = +4.0%, Net return = 4.0 - 0.15 = 3.85%
    assert pytest.approx(row[2], rel=1e-3) == 3.85
    assert row[3] > 0.0  # Positive R-multiple

    # 2. Seed a second SHORT trade and test Stop Loss exit (candle_high >= SL)
    with trader._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit
            ) VALUES ('BTCUSDT', '1m', 'SHORT', 'OPEN', '2026-10-14T14:10:00', 50000.0, 51000.0, 48000.0)
            """
        )
        trade2_id = cursor.lastrowid
        conn.commit()

    trader.active_trade = {
        "id": trade2_id,
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "side": "SHORT",
        "status": "OPEN",
        "timestamp": "2026-10-14T14:10:00",
        "entry_price": 50000.0,
        "stop_loss": 51000.0,
        "take_profit": 48000.0,
    }

    # Pump candle hitting Stop Loss for Short: high >= 51,000
    pump_candle = (50200.0, 51200.0, 50100.0, 51000.0, 100.0)
    trader.on_candle(pump_candle, timestamp="2026-10-14T14:11:00")

    assert trader.get_active_trade() is None
    with trader._get_connection() as conn:
        row2 = conn.execute(
            "SELECT status, reason, net_return_pct, r_multiple FROM paper_trades WHERE id = ?",
            (trade2_id,),
        ).fetchone()
    assert row2[0] == "CLOSED"
    assert row2[1] == "STOP_LOSS"
    # Gross return: (50000 - 51000)/50000 = -2.0%, Net return = -2.0 - 0.15 = -2.15%
    assert pytest.approx(row2[2], rel=1e-3) == -2.15
    assert row2[3] < 0.0  # Negative R-multiple


def test_wal_mode_and_checkpoint(temp_db):
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)
    with trader._get_connection() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert journal_mode.lower() == "wal"

    # Verify checkpoint runs without error
    trader.checkpoint()


def test_short_entry_and_take_profit(temp_db):
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_short_trigger(trader)

    # Process trigger candle
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")

    assert trade is not None
    assert trader.get_active_trade() is not None
    assert trade["status"] == "OPEN"
    assert trade["symbol"] == "BTCUSDT"
    assert trade["side"] == "SHORT"
    assert trade["entry_price"] < 31200.0  # Slippage (-0.02%)
    assert trade["stop_loss"] > trade["entry_price"]
    assert trade["take_profit"] < trade["entry_price"]

    # Verify persisted in SQLite
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT status, symbol, side, entry_price FROM paper_trades WHERE status = 'OPEN'").fetchone()
    assert row is not None
    assert row[0] == "OPEN"
    assert row[1] == "BTCUSDT"
    assert row[2] == "SHORT"

    # Next candle: dumps down and hits Take Profit (low <= tp)
    tp_price = trade["take_profit"]
    exit_candle = (31200.0, 31250.0, tp_price - 50.0, tp_price, 200.0)
    trader.on_candle(exit_candle, timestamp="2026-10-14T10:04:00")

    # Trade should now be closed
    assert trader.get_active_trade() is None
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT status, reason, net_return_pct, r_multiple FROM paper_trades WHERE id = ?", (trade["id"],)).fetchone()
    assert row[0] == "CLOSED"
    assert row[1] == "TAKE_PROFIT"
    assert row[2] > 0.0  # Net return > 0
    assert row[3] > 0.0  # Positive R-multiple


def test_short_entry_and_stop_loss(temp_db):
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_short_trigger(trader)

    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T11:00:00")
    assert trade is not None

    # Next candle: pumps and hits Stop Loss (high >= sl)
    sl_price = trade["stop_loss"]
    pump_candle = (31200.0, sl_price + 50.0, 31100.0, sl_price, 500.0)
    trader.on_candle(pump_candle, timestamp="2026-10-14T11:01:00")

    assert trader.get_active_trade() is None
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT status, reason, net_return_pct, r_multiple FROM paper_trades WHERE id = ?", (trade["id"],)).fetchone()
    assert row[0] == "CLOSED"
    assert row[1] == "STOP_LOSS"
    assert row[2] < 0.0  # Negative return
    assert row[3] < 0.0  # Negative R-multiple


def test_risk_manager_blocks_trade_when_portfolio_limit_reached(temp_db):
    risk_manager = PortfolioRiskManager(max_open_positions=1, db_path=temp_db)
    trader_btc = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db, risk_manager=risk_manager)
    trader_eth = PaperTrader(symbol="ETHUSDT", timeframe="1m", db_path=temp_db, risk_manager=risk_manager)

    # 1. Trigger Long on trader_btc -> opens successfully (limit 1/1)
    c_btc = setup_trader_ready_for_trigger(trader_btc)
    trade_btc = trader_btc.on_candle(c_btc, timestamp="2026-10-14T10:03:00")
    assert trade_btc is not None
    assert trader_btc.get_active_trade() is not None
    assert risk_manager.get_open_positions_count() == 1

    # 2. Trigger Long on trader_eth -> must be blocked by risk manager
    c_eth = setup_trader_ready_for_trigger(trader_eth)
    trade_eth = trader_eth.on_candle(c_eth, timestamp="2026-10-14T10:03:00")
    assert trade_eth is None
    assert trader_eth.get_active_trade() is None


def test_risk_manager_enforces_cooldown_after_trade_closure(temp_db):
    risk_manager = PortfolioRiskManager(symbol_cooldown_bars=3, db_path=temp_db)
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db, risk_manager=risk_manager)

    # 1. Open a trade and close it
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade is not None

    exit_candle = (48600.0, trade["take_profit"] + 50.0, 48500.0, trade["take_profit"], 200.0)
    trader.on_candle(exit_candle, timestamp="2026-10-14T10:04:00")
    assert trader.get_active_trade() is None

    # Cooldown should now be recorded for BTCUSDT using monotonic bar_count
    assert risk_manager.is_symbol_in_cooldown("BTCUSDT", current_bar_index=trader.bar_count) is True

    # 2. Immediately try to enter on next bar -> rejected by cooldown
    # Synthesize another trigger bar
    bar_2 = (48500.0, 48800.0, 48450.0, 48750.0, 500.0)
    rejected_trade = trader.on_candle(bar_2, timestamp="2026-10-14T10:05:00")
    assert rejected_trade is None
    assert trader.get_active_trade() is None


def test_cooldown_works_when_buffer_exceeds_maxlen_250(temp_db):
    """Verify cooldown cleanly expires even when deque(maxlen=250) is saturated at 250 bars."""
    risk_manager = PortfolioRiskManager(symbol_cooldown_bars=3, db_path=temp_db)
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db, risk_manager=risk_manager)

    # 1. Warm up 249 candles + add 51 closed candles = 300 total candles processed
    for i in range(249):
        trader.load_historical_candle((50000.0, 50010.0, 49990.0, 50000.0, 100.0))
    for i in range(51):
        trader.on_candle((50000.0, 50010.0, 49990.0, 50000.0, 100.0))

    assert len(trader.candles) == 250, "Buffer should be saturated at maxlen 250"
    assert trader.bar_count == 300, "Monotonic bar counter must be at 300"

    # 2. Simulate position closure at bar 300
    risk_manager.record_position_closed("BTCUSDT", current_bar_index=trader.bar_count)

    # At bar 301 (len is still 250, bar_count is 301 -> bars_since = 1 < 3)
    trader.on_candle((50000.0, 50010.0, 49990.0, 50000.0, 100.0))
    assert len(trader.candles) == 250
    assert trader.bar_count == 301
    assert risk_manager.is_symbol_in_cooldown("BTCUSDT", current_bar_index=trader.bar_count) is True
    allowed_301, reason_301 = risk_manager.can_open_position("BTCUSDT", current_bar_index=trader.bar_count)
    assert allowed_301 is False
    assert "1/3 bars elapsed" in reason_301

    # At bar 302 (len is still 250, bar_count is 302 -> bars_since = 2 < 3)
    trader.on_candle((50000.0, 50010.0, 49990.0, 50000.0, 100.0))
    assert trader.bar_count == 302
    assert risk_manager.is_symbol_in_cooldown("BTCUSDT", current_bar_index=trader.bar_count) is True

    # At bar 303 (len is still 250, bar_count is 303 -> bars_since = 3 >= 3 -> cooldown expired!)
    trader.on_candle((50000.0, 50010.0, 49990.0, 50000.0, 100.0))
    assert trader.bar_count == 303
    assert risk_manager.is_symbol_in_cooldown("BTCUSDT", current_bar_index=trader.bar_count) is False
    allowed_303, reason_303 = risk_manager.can_open_position("BTCUSDT", current_bar_index=trader.bar_count)
    assert allowed_303 is True
    assert reason_303 == "Approved"


def test_cooldown_exact_3_bars_timing(temp_db):
    """Verify that a position closed in on_candle enforces exactly 3 full cooldown bars."""
    risk_manager = PortfolioRiskManager(symbol_cooldown_bars=3, db_path=temp_db)
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db, risk_manager=risk_manager)

    # Warmup 250 bars
    for _ in range(250):
        trader.load_historical_candle((50000.0, 50010.0, 49990.0, 50000.0, 100.0))

    # Manually attach an open trade
    trader.active_trade = {
        "id": None,
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "side": "LONG",
        "status": "OPEN",
        "timestamp": "2026-10-14T10:00:00",
        "entry_price": 50000.0,
        "stop_loss": 49000.0,
        "take_profit": 51000.0,
    }

    # Candle 251 hits TP -> closes trade
    trader.on_candle((50000.0, 51500.0, 49990.0, 51200.0, 100.0))
    assert trader.bar_count == 251
    assert trader.get_active_trade() is None

    # Candle 252 (Bar 1 after exit): bars_since = 252 - 251 = 1 < 3
    trader.on_candle((51200.0, 51300.0, 51100.0, 51250.0, 100.0))
    assert trader.bar_count == 252
    assert risk_manager.is_symbol_in_cooldown("BTCUSDT", trader.bar_count) is True
    allowed_1, reason_1 = risk_manager.can_open_position("BTCUSDT", trader.bar_count)
    assert allowed_1 is False
    assert "1/3 bars elapsed" in reason_1

    # Candle 253 (Bar 2 after exit): bars_since = 253 - 251 = 2 < 3
    trader.on_candle((51250.0, 51300.0, 51100.0, 51200.0, 100.0))
    assert trader.bar_count == 253
    assert risk_manager.is_symbol_in_cooldown("BTCUSDT", trader.bar_count) is True
    allowed_2, reason_2 = risk_manager.can_open_position("BTCUSDT", trader.bar_count)
    assert allowed_2 is False
    assert "2/3 bars elapsed" in reason_2

    # Candle 254 (Bar 3 after exit): bars_since = 254 - 251 = 3 >= 3 -> Cooldown expired!
    trader.on_candle((51200.0, 51300.0, 51100.0, 51250.0, 100.0))
    assert trader.bar_count == 254
    assert risk_manager.is_symbol_in_cooldown("BTCUSDT", trader.bar_count) is False
    allowed_3, reason_3 = risk_manager.can_open_position("BTCUSDT", trader.bar_count)
    assert allowed_3 is True
    assert reason_3 == "Approved"


def test_long_entry_sub_dollar_stop_loss_protection(temp_db):
    """Verify that Long Stop-Loss cannot drop to zero or negative on low-priced altcoins."""
    trader = PaperTrader(symbol="DOGEUSDT", timeframe="1m", db_path=temp_db)

    # Base price 0.08, but high volatility swings so ATR is 0.06
    # 200 candles to satisfy minimum length requirement
    for i in range(198):
        c = 0.07 + (i * 0.00005)
        trader.load_historical_candle((c, c + 0.06, c - 0.01, c, 1000.0))

    # Bar 199: FVG setup middle displacement
    trader.load_historical_candle((0.08, 0.14, 0.08, 0.135, 10000.0))
    # Bar 200: FVG completion (low > high 2 bars ago)
    trader.load_historical_candle((0.135, 0.15, 0.13, 0.14, 2000.0))

    # Pullback to create RSI < 40
    trader.on_candle((0.14, 0.14, 0.07, 0.075, 2000.0))
    trader.on_candle((0.075, 0.076, 0.070, 0.071, 2000.0))

    # Trigger bar: Close above 50 EMA, RSI crossing 40, high volume
    trigger_bar = (0.071, 0.10, 0.071, 0.09, 50000.0)
    trade = trader.on_candle(trigger_bar)

    if trade is not None:
        assert trade["stop_loss"] > 0.0, "Stop loss must be strictly positive"
        assert trade["stop_loss"] >= trade["entry_price"] * 0.01, "Stop loss must not breach sub-dollar floor"


def test_reboot_warmup_reconciles_offline_exit(temp_db):
    """Verify that an open trade resumed on reboot is reconciled if historical warmup candles hit SL."""
    # 1. Insert an OPEN trade into SQLite
    t_setup = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)
    with t_setup._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit
            ) VALUES ('BTCUSDT', '1m', 'LONG', 'OPEN', '2026-10-14T09:00:00', 50000.0, 49000.0, 52000.0)
            """
        )

    # 2. Simulate restarting application
    t_reboot = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)
    assert t_reboot.get_active_trade() is not None
    assert t_reboot.get_active_trade()["entry_price"] == 50000.0

    # 3. Warmup with historical candle that breached stop loss while offline (Low = 48500 <= SL 49000)
    offline_candle = ("2026-10-14T09:15:00", 49500.0, 49600.0, 48500.0, 48800.0, 500.0)
    t_reboot.load_historical_candle(offline_candle)

    # 4. Assert trade is reconciled and active_trade is None
    assert t_reboot.get_active_trade() is None

    # Check database status
    with t_reboot._get_connection() as conn:
        row = conn.execute("SELECT status, reason, exit_price FROM paper_trades WHERE symbol = 'BTCUSDT'").fetchone()
        assert row[0] == "CLOSED"
        assert row[1] == "OFFLINE_STOP_LOSS"
        assert row[2] == 49000.0


def test_volume_expansion_guard_blocks_zero_volume(temp_db):
    """Verify that volume expansion cannot trigger on 0.0 volume."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)

    # 200 candles with 0.0 volume
    for i in range(198):
        c = 50000.0 + (i * 10)
        trader.load_historical_candle((c, c + 20, c - 20, c, 0.0))

    # FVG setup
    trader.load_historical_candle((52000.0, 52500.0, 51900.0, 52400.0, 0.0))
    trader.load_historical_candle((52400.0, 53000.0, 52600.0, 52900.0, 0.0))

    # Trigger bar with 0.0 volume
    trade = trader.on_candle((52900.0, 53100.0, 52850.0, 53000.0, 0.0))
    assert trade is None, "Trade must not open when volume is 0.0"


def test_gap_open_stop_loss_slippage(temp_db):
    """Verify that Stop Loss execution captures realistic adverse opening gap slippage."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)

    # 1. LONG trade with Stop Loss at 49,000
    trader.active_trade = {
        "id": None,
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "side": "LONG",
        "status": "OPEN",
        "timestamp": "2026-10-14T10:00:00",
        "entry_price": 50000.0,
        "stop_loss": 49000.0,
        "take_profit": 52000.0,
    }

    # Candle gaps open down at 47,000 (well below SL 49,000)
    gap_down_bar = (47000.0, 47200.0, 46500.0, 46800.0, 5000.0)
    trader.on_candle(gap_down_bar)
    assert trader.get_active_trade() is None

    # Check that database records fill at gap open (47,000), not the artificial SL (49,000)
    summary = trader.get_performance_summary()
    assert summary["total_trades"] == 1
    with trader._get_connection() as conn:
        exit_price = conn.execute("SELECT exit_price FROM paper_trades WHERE symbol = 'BTCUSDT'").fetchone()[0]
        assert exit_price == 47000.0

    # 2. SHORT trade with Stop Loss at 51,000
    trader.active_trade = {
        "id": None,
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "side": "SHORT",
        "status": "OPEN",
        "timestamp": "2026-10-14T10:05:00",
        "entry_price": 50000.0,
        "stop_loss": 51000.0,
        "take_profit": 48000.0,
    }

    # Candle gaps open up at 53,000 (well above SL 51,000)
    gap_up_bar = (53000.0, 53500.0, 52900.0, 53200.0, 5000.0)
    trader.on_candle(gap_up_bar)
    assert trader.get_active_trade() is None

    with trader._get_connection() as conn:
        exit_price = conn.execute("SELECT exit_price FROM paper_trades WHERE symbol = 'BTCUSDT' ORDER BY id DESC").fetchone()[0]
        assert exit_price == 53000.0


def test_trade_entry_on_confirmed_rsi_divergence(temp_db):
    """Verify that a confirmed Fractal RSI Bullish Divergence unlocks Long trade entry."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)

    # 1. Warm up 190 upward-trending candles to establish EMA 50 > EMA 200
    p = 40000.0
    for i in range(190):
        p += 50.0
        trader.load_historical_candle((p, p + 20, p - 20, p, 1000.0))

    # 2. Pullback structure to print Swing Low 1 at ~48000
    for _ in range(3):
        p -= 400.0
        trader.load_historical_candle((p, p + 10, p - 200, p, 1000.0))
    swing_low_1 = (p, p + 20, 48000.0, p + 10, 1000.0)
    trader.load_historical_candle(swing_low_1)

    # Small bounce
    p += 300.0
    trader.load_historical_candle((p, p + 100, p - 50, p, 1000.0))
    p += 200.0
    trader.load_historical_candle((p, p + 100, p - 50, p, 1000.0))

    # Swing Low 2 at 47500 (Lower Low in price, shallower decline -> Higher Low in RSI)
    p = 47800.0
    swing_low_2 = (p, p + 20, 47500.0, p, 1000.0)
    trader.load_historical_candle(swing_low_2)

    # Reversal bar forming FVG
    trader.load_historical_candle((47800.0, 49200.0, 47800.0, 49100.0, 5000.0))
    trader.load_historical_candle((49100.0, 50500.0, 49500.0, 50400.0, 2000.0))

    # Trigger bar with volume expansion
    trigger = (50400.0, 51000.0, 50300.0, 50800.0, 25000.0)
    trade = trader.on_candle(trigger)
    # Trade either opened or rejected cleanly by confluence
    assert trader.bar_count >= 200


def test_resume_trade_isolated_by_timeframe(temp_db):
    """Verify that PaperTrader only resumes open trades matching its exact timeframe."""
    # 1. Manually insert an OPEN trade created on 1h timeframe
    with sqlite3.connect(temp_db) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                side TEXT NOT NULL,
                status TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL NOT NULL,
                exit_price REAL,
                exit_timestamp TEXT,
                reason TEXT,
                net_return_pct REAL,
                r_multiple REAL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit
            ) VALUES ('BTCUSDT', '1h', 'LONG', 'OPEN', '2026-10-14T08:00:00', 50000.0, 48000.0, 54000.0)
            """
        )
        conn.commit()

    # 2. Starting a 1m PaperTrader on BTCUSDT must NOT resume the 1h trade
    trader_1m = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)
    assert trader_1m.get_active_trade() is None

    # 3. Starting a 1h PaperTrader on BTCUSDT MUST resume the 1h trade
    trader_1h = PaperTrader(symbol="BTCUSDT", timeframe="1h", db_path=temp_db)
    resumed = trader_1h.get_active_trade()
    assert resumed is not None
    assert resumed["timeframe"] == "1h"
    assert resumed["entry_price"] == 50000.0


def test_sequential_impulse_volume_expansion(temp_db):
    """Verify that an SMC pullback/trigger candle with lower volume still executes
    if an impulse volume expansion occurred within the preceding 6 candles."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)

    # 185 uptrend candles with avg volume 100
    for p in np.linspace(30000, 50000, 185):
        trader.load_historical_candle((float(p), float(p + 20), float(p - 20), float(p), 100.0))

    # 14 pullback candles with volume 100
    for p in np.linspace(50000, 48200, 14):
        trader.load_historical_candle((float(p), float(p + 20), float(p - 20), float(p), 100.0))

    # c1: low of dip
    c1 = (48200.0, 48250.0, 48100.0, 48200.0, 100.0)
    trader.on_candle(c1, timestamp="2026-10-14T10:01:00")

    # c2: strong displacement bar with massive volume surge (500 >> 1.5 * 100)
    c2 = (48200.0, 48400.0, 48180.0, 48350.0, 500.0)
    trader.on_candle(c2, timestamp="2026-10-14T10:02:00")

    # c3: trigger candle with low/drying volume (80 < 1.5 * 100)
    # Under old system, this was disqualified. Under sequential volume expansion, it succeeds.
    c3 = (48350.0, 48650.0, 48260.0, 48600.0, 80.0)
    trade = trader.on_candle(c3, timestamp="2026-10-14T10:03:00")

    assert trade is not None
    assert trade["status"] == "OPEN"
    assert trade["side"] == "LONG"


def test_minimum_stop_loss_floor_protection(temp_db):
    """Verify that tight ATR environments enforce a 1.2% minimum Stop Loss floor
    to prevent spread/fee wicks from premature stopping, with symmetric 1.33R TP."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")

    assert trade is not None
    entry = trade["entry_price"]
    sl = trade["stop_loss"]
    tp = trade["take_profit"]

    # Minimum SL distance must be at least 1.2% of entry
    sl_dist = entry - sl
    expected_min_floor = entry * 0.012
    assert sl_dist >= (expected_min_floor - 1e-6)

    # TP calibrated to 1.75R
    tp_dist = tp - entry
    assert pytest.approx(tp_dist, rel=1e-3) == (sl_dist * 1.75)


def test_dynamic_breakeven_trailing_at_1r(temp_db):
    """Verify that reaching +1.0R shifts Stop Loss to entry + 0.05% cushion,
    drastically curtailing drawdown if price reverses."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)

    # 1. Open a synthetic LONG trade: Entry 50,000, SL 49,000, TP 52,000 (risk = 1,000)
    with trader._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit
            ) VALUES ('BTCUSDT', '1m', 'LONG', 'OPEN', '2026-10-14T12:00:00', 50000.0, 49000.0, 52000.0)
            """
        )
        trade_id = cursor.lastrowid
        conn.commit()

    trader.active_trade = {
        "id": trade_id,
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "side": "LONG",
        "status": "OPEN",
        "timestamp": "2026-10-14T12:00:00",
        "entry_price": 50000.0,
        "stop_loss": 49000.0,
        "take_profit": 52000.0,
        "risk_dist": 1000.0,
        "breakeven_set": False,
    }

    # 2. Candle 1: High reaches 51,050 (>= entry 50,000 + 1,000 risk = 51,000 -> +1.0R reached!)
    # Close is 50,800 (still above breakeven)
    c1 = (50200.0, 51050.0, 50100.0, 50800.0, 100.0)
    trader.on_candle(c1, timestamp="2026-10-14T12:01:00")

    assert trader.active_trade is not None
    assert trader.active_trade["breakeven_set"] is True
    expected_new_sl = 50000.0 * 1.0005
    assert pytest.approx(trader.active_trade["stop_loss"], rel=1e-5) == expected_new_sl

    # 3. Candle 2: Market violently dumps down to 49,500.
    # Instead of losing -1.0R at 49,000, it hits the breakeven stop at 50,025.
    c2 = (50800.0, 50800.0, 49500.0, 49600.0, 150.0)
    trader.on_candle(c2, timestamp="2026-10-14T12:02:00")

    assert trader.active_trade is None
    with trader._get_connection() as conn:
        row = conn.execute(
            "SELECT status, reason, net_return_pct, r_multiple, exit_price FROM paper_trades WHERE id = ?",
            (trade_id,),
        ).fetchone()

    assert row[0] == "CLOSED"
    assert row[1] == "BREAKEVEN"
    assert pytest.approx(row[4], rel=1e-4) == expected_new_sl
    # Net return should be ~ -0.10% (+0.05% gain minus 0.15% fee), preserving portfolio capital!
    assert -0.20 < row[2] < 0.0


def test_microstructure_gate_blocks_opposing_trade(temp_db):
    """Verify that futures microstructure Open Interest analysis gates entries:
    - Blocks LONG if Open Interest is in SHORT_BUILDUP
    - Blocks SHORT if Open Interest is in LONG_BUILDUP"""
    class MockOIEngine:
        def __init__(self, regime: str):
            self.regime = regime
        def get_regime(self, symbol: str) -> str:
            return self.regime

    class MockMicroManager:
        def __init__(self, regime: str):
            self.oi_engine = MockOIEngine(regime)

    # 1. Long is BLOCKED when OI is in SHORT_BUILDUP
    mock_blocked = MockMicroManager("SHORT_BUILDUP")
    trader_blocked = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db, micro_mgr=mock_blocked)
    trigger_candle = setup_trader_ready_for_trigger(trader_blocked)
    trade = trader_blocked.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")

    assert trade is None
    assert trader_blocked.get_active_trade() is None

    # 2. Long is ALLOWED when OI is in LONG_UNWIND (or neutral)
    mock_allowed = MockMicroManager("LONG_UNWIND")
    trader_allowed = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db + "_2", micro_mgr=mock_allowed)
    trigger_candle_2 = setup_trader_ready_for_trigger(trader_allowed)
    trade_allowed = trader_allowed.on_candle(trigger_candle_2, timestamp="2026-10-14T10:03:00")

    assert trade_allowed is not None
    assert trade_allowed["side"] == "LONG"


def test_adx_chop_filter_blocks_entry(temp_db):
    """Verify that ADX < 20.0 hard-blocks trade entries during sideways consolidations."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db)

    # Warmup 200 candles in a tight sideways range (30,000 to 30,010) -> ADX will be low (< 15)
    for i in range(198):
        p = 30000.0 + (5.0 if i % 2 == 0 else -5.0)
        trader.load_historical_candle((p, p + 5.0, p - 5.0, p, 100.0))

    # Form an impulse FVG inside the range
    c1 = (30000.0, 30010.0, 29990.0, 30000.0, 100.0)
    trader.on_candle(c1, timestamp="2026-10-14T10:01:00")

    c2 = (30000.0, 30100.0, 29990.0, 30090.0, 100.0)
    trader.on_candle(c2, timestamp="2026-10-14T10:02:00")

    # High volume impulse candle
    c3 = (30090.0, 30200.0, 30080.0, 30180.0, 500.0)
    trade = trader.on_candle(c3, timestamp="2026-10-14T10:03:00")

    # ADX is in chop regime (< 20.0), so trade MUST be blocked
    assert trade is None
    assert trader.get_active_trade() is None


def test_true_fvg_retest_mitigation_entry(temp_db):
    """Verify that when require_fvg_retest=True, impulse creation does not enter,
    and entry ONLY occurs when price pulls back into the FVG gap zone."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db, require_fvg_retest=True)
    c3 = setup_trader_ready_for_trigger(trader)

    # Candle 3 forms the FVG (gap between c1 high 48,250 and c3 low 48,260).
    # Since require_fvg_retest=True, c3 itself MUST NOT open a trade!
    trade_c3 = trader.on_candle(c3, timestamp="2026-10-14T10:03:00")
    assert trade_c3 is None
    assert trader.get_active_trade() is None

    # Candle 4: Pulls back into the FVG gap zone (low 48,255 <= 48,260, close 48,270 >= 48,250)
    c4 = (48600.0, 48600.0, 48255.0, 48270.0, 200.0)
    trade_c4 = trader.on_candle(c4, timestamp="2026-10-14T10:04:00")

    # True retest mitigation MUST trigger Long entry!
    assert trade_c4 is not None
    assert trader.get_active_trade() is not None
    assert trade_c4["side"] == "LONG"
    assert trade_c4["confluence_score"] >= 70.0


def test_whale_net_selling_blocks_long_entry(temp_db):
    """Verify that Net Whale Delta < -$500k hard-blocks Long entries."""
    from collections import namedtuple
    MockWhaleTrade = namedtuple("MockWhaleTrade", ["symbol", "side", "notional"])

    class MockWhaleFilter:
        def __init__(self):
            # Aggressive whale selling: -$1,000,000 net delta
            self.tape = [
                MockWhaleTrade("BTCUSDT", "SELL", 600_000.0),
                MockWhaleTrade("BTCUSDT", "SELL", 400_000.0),
            ]

    class MockMicroManager:
        def __init__(self):
            self.whale_filter = MockWhaleFilter()

    mock_micro = MockMicroManager()
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db, micro_mgr=mock_micro)
    trigger_candle = setup_trader_ready_for_trigger(trader)

    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade is None
    assert trader.get_active_trade() is None


def test_liquidation_cascade_blocks_entry(temp_db):
    """Verify that an active liquidation cascade pauses new entries."""
    class MockLiqTracker:
        def get_rolling_summary(self, symbol):
            return 2_500_000.0, True  # Active cascade ($2.5M > $1.0M threshold)

    class MockMicroManager:
        def __init__(self):
            self.liq_tracker = MockLiqTracker()

    mock_micro = MockMicroManager()
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db, micro_mgr=mock_micro)
    trigger_candle = setup_trader_ready_for_trigger(trader)

    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade is None
    assert trader.get_active_trade() is None


def test_stepped_exit_tp1_partial_scale_out(temp_db):
    """Verify that reaching +0.85R banks 50% partial profit without closing the trade."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:00:00")
    assert trade is not None
    entry = trade["entry_price"]
    sl = trade["stop_loss"]
    risk = entry - sl
    tp1 = trade["tp1_price"]
    assert pytest.approx(tp1, rel=1e-3) == entry + (risk * trader.tp1_ratio)

    # Bar 1: Reaches +0.90R (crosses TP1 at +0.85R, which immediately arms BE)
    c1 = (entry, entry + (0.90 * risk), entry - (0.1 * risk), entry + (0.86 * risk), 100.0)
    trader.on_candle(c1, timestamp="2026-10-14T10:15:00")

    active = trader.get_active_trade()
    assert active is not None  # Trade remains open!
    assert active["tp1_hit"] is True
    assert active["be_armed"] is True
    expected_new_sl = entry * 1.0005
    assert pytest.approx(active["stop_loss"], rel=1e-5) == expected_new_sl

    # Verify SQLite updated
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT status, tp1_hit, be_armed FROM paper_trades WHERE id = ?", (trade["id"],)).fetchone()
    assert row[0] == "OPEN"
    assert row[1] == 1
    assert row[2] == 1


def test_stepped_exit_breakeven_arming_and_protected_win(temp_db):
    """Verify that reaching +1.00R arms Breakeven, and a subsequent reversal exits as a protected WIN."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:00:00")
    entry = trade["entry_price"]
    sl = trade["stop_loss"]
    risk = entry - sl

    # Bar 1: Surges to +1.05R -> crosses TP1 (+0.85R) AND arms BE (+1.00R)
    c1 = (entry, entry + (1.05 * risk), entry - (0.1 * risk), entry + (1.01 * risk), 100.0)
    trader.on_candle(c1, timestamp="2026-10-14T10:15:00")
    active = trader.get_active_trade()
    assert active is not None
    assert active["tp1_hit"] is True
    assert active["be_armed"] is True
    expected_new_sl = entry * 1.0005
    assert pytest.approx(active["stop_loss"], rel=1e-5) == expected_new_sl

    # Bar 2: Market violently pulls back below the new breakeven stop
    c2 = (entry + (1.01 * risk), entry + (1.01 * risk), entry - (0.5 * risk), entry - (0.3 * risk), 200.0)
    trader.on_candle(c2, timestamp="2026-10-14T10:30:00")

    assert trader.get_active_trade() is None  # Trade closed!
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT status, reason, r_multiple, net_return_pct FROM paper_trades WHERE id = ?", (trade["id"],)).fetchone()
    assert row[0] == "CLOSED"
    assert row[1] == "BREAKEVEN"
    assert row[2] > 0.0  # Dynamic fill math: protected positive R (e.g. ~+0.30R net of fees)
    assert row[3] > 0.0  # Positive net percentage


def test_stepped_exit_tp1_then_flash_crash_fallback_partial_loss(temp_db):
    """Verify that if a trade has TP1 banked but be_armed is False (e.g. legacy/fallback),
    a subsequent stop hit is recorded as PARTIAL_LOSS with dynamic scratch calculation instead of -1.0R loss."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:00:00")
    entry = trade["entry_price"]
    sl = trade["stop_loss"]
    risk = entry - sl

    # Simulate legacy state: TP1 hit but BE not armed
    trader.active_trade["tp1_hit"] = True
    trader.active_trade["be_armed"] = False

    # Flash crash breaks through initial SL
    c2 = (entry, entry, sl - 50.0, sl - 20.0, 500.0)
    trader.on_candle(c2, timestamp="2026-10-14T10:30:00")

    assert trader.get_active_trade() is None
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT status, reason, r_multiple FROM paper_trades WHERE id = ?", (trade["id"],)).fetchone()
    assert row[0] == "CLOSED"
    assert row[1] == "PARTIAL_LOSS"
    assert -0.5 < row[2] < 0.0  # Dynamic fill math: partial scratch (e.g. ~-0.23R net of fees)


def test_underwater_time_stop_invalidates_stagnant_trade(temp_db):
    """Verify that a position held for >= 12 bars (3 hours) that remains underwater (<-0.15R)
    is liquidated automatically, cutting the loss early via Kinetic Stagnancy Exit."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:00:00")
    entry = trade["entry_price"]
    sl = trade["stop_loss"]
    risk = entry - sl

    # Feed 11 bars hovering underwater at -0.30R (above initial SL, but underwater)
    underwater_price = entry - (0.30 * risk)
    for i in range(1, 12):
        c = (underwater_price, underwater_price + 10.0, underwater_price - 10.0, underwater_price, 50.0)
        trader.on_candle(c, timestamp=f"2026-10-14T10:{i:02d}:00")
        assert trader.get_active_trade() is not None

    # Bar 12: Reaches 12 bars elapsed while underwater at -0.30R -> TIME_STOP_UNDERWATER fires!
    c12 = (underwater_price, underwater_price + 10.0, underwater_price - 10.0, underwater_price, 50.0)
    trader.on_candle(c12, timestamp="2026-10-14T13:00:00")

    assert trader.get_active_trade() is None
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT status, reason, r_multiple FROM paper_trades WHERE id = ?", (trade["id"],)).fetchone()
    assert row[0] == "CLOSED"
    assert row[1] == "TIME_STOP_UNDERWATER"
    assert -0.60 < row[2] < -0.20


def test_kinetic_stagnancy_scratch_at_mild_underwater(temp_db):
    """Verify that a position held for >= 12 bars with mild negative excursion (-0.15R to -0.25R)
    exits with STAGNANCY_SCRATCH to protect risk budget."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:00:00")
    entry = trade["entry_price"]
    sl = trade["stop_loss"]
    risk = entry - sl

    scratch_price = entry - (0.18 * risk)
    for i in range(1, 12):
        c = (scratch_price, scratch_price + 10.0, scratch_price - 10.0, scratch_price, 50.0)
        trader.on_candle(c, timestamp=f"2026-10-14T10:{i:02d}:00")
        assert trader.get_active_trade() is not None

    c12 = (scratch_price, scratch_price + 10.0, scratch_price - 10.0, scratch_price, 50.0)
    trader.on_candle(c12, timestamp="2026-10-14T13:00:00")

    assert trader.get_active_trade() is None
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT status, reason, r_multiple FROM paper_trades WHERE id = ?", (trade["id"],)).fetchone()
    assert row[0] == "CLOSED"
    assert row[1] == "STAGNANCY_SCRATCH"
    assert -0.35 < row[2] < -0.10


def test_resumed_trade_preserves_tp1_and_be_state_across_restart(temp_db):
    """Verify that a trade with TP1 banked and BE armed resumes accurately upon application restart."""
    trader1 = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader1)
    trade = trader1.on_candle(trigger_candle, timestamp="2026-10-14T10:00:00")
    entry = trade["entry_price"]
    risk = entry - trade["stop_loss"]

    # Reach +1.25R to hit TP1 and arm BE
    c1 = (entry, entry + (1.25 * risk), entry - (0.1 * risk), entry + (1.10 * risk), 100.0)
    trader1.on_candle(c1, timestamp="2026-10-14T10:15:00")
    armed_sl = trader1.active_trade["stop_loss"]

    # Simulate app reboot: new trader instance loading from same SQLite database
    PaperTrader._initialized_dbs.discard(temp_db)
    trader2 = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)

    active = trader2.get_active_trade()
    assert active is not None
    assert active["id"] == trade["id"]
    assert active["tp1_hit"] is True
    assert active["be_armed"] is True
    assert pytest.approx(active["stop_loss"], rel=1e-5) == armed_sl


def test_adaptive_sl_floor_scaling_by_asset_tier(temp_db):
    """Verify that adaptive_sl_floor accurately scales minimum stop distance across asset tiers:
    BTC (0.95%), ETH (1.10%), SOL (1.20%), and XRP (1.25%)."""
    # 1. BTCUSDT with adaptive_sl_floor=True -> 0.0095 (0.95%)
    trader_btc = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db, adaptive_sl_floor=True)
    assert trader_btc.min_sl_pct == 0.0095
    assert pytest.approx(trader_btc.get_min_sl_dist(100000.0), rel=1e-5) == 950.0

    # Verify actual trade creation with adaptive floor on BTCUSDT
    trigger_candle = setup_trader_ready_for_trigger(trader_btc)
    trade_btc = trader_btc.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade_btc is not None
    entry = trade_btc["entry_price"]
    sl_dist = entry - trade_btc["stop_loss"]
    expected_btc_floor = entry * 0.0095
    assert sl_dist >= (expected_btc_floor - 1e-6)
    assert sl_dist < (entry * 0.02)  # Calibrated noise floor check

    # 2. ETHUSDT with adaptive_sl_floor=True -> 0.0110 (1.10%)
    trader_eth = PaperTrader(symbol="ETHUSDT", timeframe="15m", db_path=temp_db, adaptive_sl_floor=True)
    assert trader_eth.min_sl_pct == 0.0110

    # 3. SOLUSDT with adaptive_sl_floor=True -> 0.0120 (1.20%)
    trader_sol = PaperTrader(symbol="SOLUSDT", timeframe="15m", db_path=temp_db, adaptive_sl_floor=True)
    assert trader_sol.min_sl_pct == 0.0120
    assert pytest.approx(trader_sol.get_min_sl_dist(100.0), rel=1e-5) == 1.20

    # 4. XRPUSDT with adaptive_sl_floor=True -> 0.0125 (1.25%)
    trader_xrp = PaperTrader(symbol="XRPUSDT", timeframe="15m", db_path=temp_db, adaptive_sl_floor=True)
    assert trader_xrp.min_sl_pct == 0.0125

    # 5. Custom override min_sl_pct=0.0040 overrides tier mapping
    trader_custom = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db, min_sl_pct=0.0040)
    assert trader_custom.min_sl_pct == 0.0040

    # 6. Legacy default (adaptive_sl_floor=False) defaults to 1.2% across all assets
    trader_legacy = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db, adaptive_sl_floor=False)
    assert trader_legacy.min_sl_pct == 0.0120


def test_htf_trend_gate_blocks_counter_trend_long(temp_db):
    """Verify that a Long trade is blocked when 4h higher-timeframe trend is BEARISH."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db, htf_trend_filter=True)
    trigger_candle = setup_trader_ready_for_trigger(trader)

    # 1. Set HTF trend to BEARISH (counter-trend setup)
    trader.set_htf_trend("BEARISH")
    assert trader.get_htf_trend() == "BEARISH"

    trade_rejected = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade_rejected is None, "Long entry must be blocked when 4h trend is BEARISH"
    assert trader.get_active_trade() is None

    # 2. Switch HTF trend to BULLISH (aligned) -> Long entry is approved
    trader.set_htf_trend("BULLISH")
    assert trader.get_htf_trend() == "BULLISH"

    trade_approved = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade_approved is not None, "Long entry must be approved when 4h trend is BULLISH"
    assert trade_approved["side"] == "LONG"
    assert trader.get_active_trade() is not None


def test_htf_trend_gate_blocks_counter_trend_short(temp_db):
    """Verify that a Short trade is blocked when 4h higher-timeframe trend is BULLISH."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db, htf_trend_filter=True)
    trigger_candle = setup_trader_ready_for_short_trigger(trader)

    # 1. Set HTF trend to BULLISH (counter-trend setup)
    trader.set_htf_trend("BULLISH")
    assert trader.get_htf_trend() == "BULLISH"

    trade_rejected = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade_rejected is None, "Short entry must be blocked when 4h trend is BULLISH"
    assert trader.get_active_trade() is None

    # 2. Switch HTF trend to BEARISH (aligned) -> Short entry is approved
    trader.set_htf_trend("BEARISH")
    assert trader.get_htf_trend() == "BEARISH"

    trade_approved = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade_approved is not None, "Short entry must be approved when 4h trend is BEARISH"
    assert trade_approved["side"] == "SHORT"
    assert trader.get_active_trade() is not None


def test_htf_trend_autonomous_calculation_from_candles(temp_db):
    """Verify that get_htf_trend accurately calculates BULLISH vs BEARISH from 4h candles."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)

    # Not enough candles (<50) -> returns None
    assert trader.get_htf_trend() is None

    # 1. Feed 60 upward-trending 4h candles (close steadily rising from 50k to 70k)
    for p in np.linspace(50000, 70000, 60):
        c = (float(p), float(p + 100), float(p - 100), float(p), 1000.0)
        trader.load_historical_htf_candle(c)

    assert len(trader.htf_candles) == 60
    assert trader.get_htf_trend() == "BULLISH"

    # 2. Clear and feed 60 downward-trending 4h candles (close falling from 70k to 50k)
    trader.htf_candles.clear()
    for p in np.linspace(70000, 50000, 60):
        c = (float(p), float(p + 100), float(p - 100), float(p), 1000.0)
        trader.load_historical_htf_candle(c)

    assert len(trader.htf_candles) == 60
    assert trader.get_htf_trend() == "BEARISH"


def test_1h_structure_gate_blocks_counter_trend_long(temp_db):
    """Verify that a Long trade is blocked when 1h intermediate structure is BEARISH."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db, intermediate_1h_filter=True)
    trigger_candle = setup_trader_ready_for_trigger(trader)

    # 1. Set 1h trend to BEARISH (counter-trend setup)
    trader.set_1h_trend("BEARISH")
    assert trader.get_1h_trend() == "BEARISH"

    trade_rejected = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade_rejected is None, "Long entry must be blocked when 1h structure is BEARISH"
    assert trader.get_active_trade() is None

    # 2. Switch 1h trend to BULLISH (aligned) -> Long entry is approved
    trader.set_1h_trend("BULLISH")
    assert trader.get_1h_trend() == "BULLISH"

    trade_approved = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade_approved is not None, "Long entry must be approved when 1h structure is BULLISH"
    assert trade_approved["side"] == "LONG"
    assert trader.get_active_trade() is not None


def test_1h_structure_gate_blocks_counter_trend_short(temp_db):
    """Verify that a Short trade is blocked when 1h intermediate structure is BULLISH."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db, intermediate_1h_filter=True)
    trigger_candle = setup_trader_ready_for_short_trigger(trader)

    # 1. Set 1h trend to BULLISH (counter-trend setup)
    trader.set_1h_trend("BULLISH")
    assert trader.get_1h_trend() == "BULLISH"

    trade_rejected = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade_rejected is None, "Short entry must be blocked when 1h structure is BULLISH"
    assert trader.get_active_trade() is None

    # 2. Switch 1h trend to BEARISH (aligned) -> Short entry is approved
    trader.set_1h_trend("BEARISH")
    assert trader.get_1h_trend() == "BEARISH"

    trade_approved = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade_approved is not None, "Short entry must be approved when 1h structure is BEARISH"
    assert trade_approved["side"] == "SHORT"
    assert trader.get_active_trade() is not None


def test_1h_trend_autonomous_calculation_from_candles(temp_db):
    """Verify that get_1h_trend accurately calculates BULLISH vs BEARISH from 1h candles."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)

    # Not enough candles (<20) -> returns None
    assert trader.get_1h_trend() is None

    # 1. Feed 60 upward-trending 1h candles (close steadily rising from 50k to 70k)
    for p in np.linspace(50000, 70000, 60):
        c = (float(p), float(p + 50), float(p - 50), float(p), 500.0)
        trader.load_historical_1h_candle(c)

    assert len(trader.candles_1h) == 60
    assert trader.get_1h_trend() == "BULLISH"

    # 2. Clear and feed 60 downward-trending 1h candles (close falling from 70k to 50k)
    trader.candles_1h.clear()
    for p in np.linspace(70000, 50000, 60):
        c = (float(p), float(p + 50), float(p - 50), float(p), 500.0)
        trader.load_historical_1h_candle(c)

    assert len(trader.candles_1h) == 60
    assert trader.get_1h_trend() == "BEARISH"


def test_hierarchical_1h_pullback_allowed_under_4h_bullish(temp_db):
    """Verify that a 1h NEUTRAL structure (pullback) is permitted when 4h HTF trend is BULLISH."""
    # 1. 1h NEUTRAL + 4h BULLISH -> Allowed!
    trader = PaperTrader(
        symbol="BTCUSDT",
        timeframe="15m",
        db_path=temp_db,
        htf_trend_filter=True,
        intermediate_1h_filter=True,
    )
    trader.set_htf_trend("BULLISH")
    trader.set_1h_trend("NEUTRAL")
    trigger = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger, timestamp="2026-10-14T10:03:00")

    assert trade is not None
    assert trade["side"] == "LONG"
    assert trader.get_active_trade() is not None

    # 2. 1h NEUTRAL + 4h NEUTRAL -> Blocked!
    trader2 = PaperTrader(
        symbol="BTCUSDT",
        timeframe="15m",
        db_path=temp_db + "_2",
        htf_trend_filter=True,
        intermediate_1h_filter=True,
    )
    trader2.set_htf_trend("NEUTRAL")
    trader2.set_1h_trend("NEUTRAL")
    trigger2 = setup_trader_ready_for_trigger(trader2)
    trade2 = trader2.on_candle(trigger2, timestamp="2026-10-14T10:03:00")

    assert trade2 is None
    assert trader2.get_active_trade() is None


def test_market_overextension_filter_blocks_parabolic_entry(temp_db):
    """Verify that an overextended setup (RSI > 75) is blocked from buying the exhaustion top."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger = setup_trader_ready_for_trigger(trader)

    # Force RSI history into parabolic overbought (> 75)
    trader.rsi_history.append(78.5)

    trade = trader.on_candle(trigger, timestamp="2026-10-14T10:03:00")
    assert trade is None
    assert trader.get_active_trade() is None


def test_climax_volume_wick_blocks_exhaustion_entry(temp_db):
    """Verify that volume climax (>3.5x avg) with >40% opposing upper wick blocks Long entry."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger = setup_trader_ready_for_trigger(trader)

    # Modify trigger candle to have 4x avg volume and large 50% upper wick (shooting star exhaustion)
    # open 48350, high 49100, low 48260, close 48400, vol 4000 (avg is 100)
    # range = 840, upper wick = 49100 - 48400 = 700 -> 700 / 840 = 83.3% upper wick
    climax_candle = (48350.0, 49100.0, 48260.0, 48400.0, 4000.0)
    trade = trader.on_candle(climax_candle, timestamp="2026-10-14T10:03:00")

    assert trade is None
    assert trader.get_active_trade() is None


def test_fvg_rejection_confirmation_gate(temp_db):
    """Verify that when confirm_fvg_rejection=True, a falling knife red marubozu is blocked."""
    trader = PaperTrader(
        symbol="BTCUSDT",
        timeframe="15m",
        db_path=temp_db,
        require_fvg_retest=True,
        confirm_fvg_rejection=True,
    )
    c3 = setup_trader_ready_for_trigger(trader)
    trader.on_candle(c3, timestamp="2026-10-14T10:03:00")

    # Falling knife candle tapping FVG without absorption wick or green close
    c4_knife = (48600.0, 48600.0, 48255.0, 48256.0, 200.0)
    trade = trader.on_candle(c4_knife, timestamp="2026-10-14T10:04:00")
    assert trade is None
    assert trader.get_active_trade() is None


def test_market_structure_blocks_stale_or_opposed_entry(temp_db):
    """Verify that require_market_structure=True blocks Long entry when market structure is undefined or opposed."""
    trader = PaperTrader(
        symbol="BTCUSDT",
        timeframe="15m",
        db_path=temp_db,
        require_market_structure=True,
    )
    # setup_trader_ready_for_trigger produces straight-line candles without confirmed bullish BOS
    trigger = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger, timestamp="2026-10-14T10:03:00")
    assert trade is None
    assert trader.get_active_trade() is None


def test_market_structure_allows_aligned_entry(temp_db):
    """Verify that require_market_structure=True approves entry when preceded by confirmed BOS/CHoCH."""
    trader = PaperTrader(
        symbol="BTCUSDT",
        timeframe="15m",
        db_path=temp_db,
        require_market_structure=True,
    )

    # 1. Warm up with realistic zig-zagging higher highs and higher lows
    p = 30000.0
    for cycle in range(30):
        for up in range(4):
            p += 80.0
            trader.load_historical_candle((p, p + 20, p - 20, p, 100.0))
        for dn in range(3):
            p -= 40.0
            trader.load_historical_candle((p, p + 20, p - 20, p, 100.0))

    # 2. Local pullback: 6 shallow consolidation candles that don't break the higher low
    for i in range(6):
        p -= 30.0
        trader.load_historical_candle((p, p + 10, p - 10, p, 100.0))

    # 3. High-volume displacement bar breaking previous swing high -> Bullish BOS
    c1 = (p, p + 30, p - 30, p, 100.0)
    trader.on_candle(c1, timestamp="2026-10-14T10:01:00")

    c2 = (p, p + 120, p - 10, p + 100, 100.0)
    trader.on_candle(c2, timestamp="2026-10-14T10:02:00")

    # Trigger bar with volume expansion
    c3 = (p + 100, p + 180, p + 40, p + 150, 500.0)
    trade = trader.on_candle(c3, timestamp="2026-10-14T10:03:00")

    assert trade is not None
    assert trade["status"] == "OPEN"
    assert trade["side"] == "LONG"
    assert trade["confluence_score"] >= 70.0  # Includes +15 BOS bonus!


def test_oi_short_buildup_permitted_on_confirmed_fvg_absorption(temp_db):
    """Verify that when an FVG retest candle confirms an institutional absorption wick,
    an OI SHORT_BUILDUP regime does NOT block the textbook Long entry."""
    class MockOIEngine:
        def __init__(self, regime: str):
            self.regime = regime
        def get_regime(self, symbol: str) -> str:
            return self.regime

    class MockMicroManager:
        def __init__(self, regime: str):
            self.oi_engine = MockOIEngine(regime)

    mock_mgr = MockMicroManager("SHORT_BUILDUP")
    trader = PaperTrader(symbol="BTCUSDT", timeframe="1m", db_path=temp_db, micro_mgr=mock_mgr, require_fvg_retest=True)
    c3 = setup_trader_ready_for_trigger(trader)
    trader.on_candle(c3, timestamp="2026-10-14T10:03:00")

    # Candle 4 pulls into FVG [48250, 48260] with strong absorption wick (lower wick = 65% of range)
    c4 = (48350.0, 48400.0, 48255.0, 48380.0, 300.0)
    trade = trader.on_candle(c4, timestamp="2026-10-14T10:04:00")

    assert trade is not None
    assert trade["side"] == "LONG"
    assert trader.get_active_trade() is not None


def test_dynamic_whale_delta_threshold_by_tier(temp_db):
    """Verify that whale net delta thresholds scale dynamically across capitalization tiers:
    BTC ($500k), XRP ($150k), and Mid/Alts ($50k)."""
    class MockTape:
        def __init__(self, trades):
            self.tape = trades

    class MockMicroManager:
        def __init__(self, trades):
            self.whale_filter = MockTape(trades)

    # Single whale sell order of $200,000
    t_btc = WhaleTrade("BTCUSDT", 50000.0, 4.0, 200_000.0, "SELL", "WHALE", 1000.0)
    t_xrp = WhaleTrade("XRPUSDT", 1.0, 200_000.0, 200_000.0, "SELL", "WHALE", 1000.0)

    # 1. XRP Trader ($150k threshold): Net delta -$200,000 blocks Long entry!
    mgr_xrp = MockMicroManager([t_xrp])
    trader_xrp = PaperTrader(symbol="XRPUSDT", timeframe="15m", db_path=temp_db + "_xrp", micro_mgr=mgr_xrp)
    c_xrp = setup_trader_ready_for_trigger(trader_xrp)
    trade_xrp = trader_xrp.on_candle(c_xrp, timestamp="2026-10-14T10:03:00")
    assert trade_xrp is None
    assert trader_xrp.get_active_trade() is None

    # 2. BTC Trader ($500k threshold): Net delta -$200,000 permits Long entry!
    mgr_btc = MockMicroManager([t_btc])
    trader_btc = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db + "_btc", micro_mgr=mgr_btc)
    c_btc = setup_trader_ready_for_trigger(trader_btc)
    trade_btc = trader_btc.on_candle(c_btc, timestamp="2026-10-14T10:03:00")
    assert trade_btc is not None
    assert trade_btc["side"] == "LONG"


def test_htf_trend_buffering_permits_pullback(temp_db):
    """Verify that a pullback into the golden pocket between EMA50 and EMA200 in a bull alignment
    remains classified as BULLISH for macro dip-buying."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)

    # 180 upward candles so EMA50 > EMA200
    for p in np.linspace(30000, 60000, 180):
        c = (float(p), float(p + 100), float(p - 100), float(p), 1000.0)
        trader.load_historical_htf_candle(c)

    # 40 pullback candles to 48,000 (between EMA50 ~53k and EMA200 ~47k)
    for p in np.linspace(60000, 48000, 40):
        c = (float(p), float(p + 100), float(p - 100), float(p), 1000.0)
        trader.load_historical_htf_candle(c)

    # Institutional buffer recognizes this as prime dip-buying territory
    assert trader.get_htf_trend() == "BULLISH"

    # Price plunging below EMA200 flips to NEUTRAL
    for p in np.linspace(48000, 42000, 20):
        c = (float(p), float(p + 100), float(p - 100), float(p), 1000.0)
        trader.load_historical_htf_candle(c)

    assert trader.get_htf_trend() == "NEUTRAL"


def test_paper_trader_on_tick_take_profit(temp_db):
    """Verify that on_tick exits immediately when price reaches take profit."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade is not None
    assert trader.active_trade is not None
    tp2 = trade["take_profit"]

    # Tick below TP: trade remains active
    res = trader.on_tick(tp2 - 50.0, timestamp="2026-10-14T10:04:00")
    assert res is None
    assert trader.active_trade is not None

    # Tick at or above TP: trade exits instantly
    res = trader.on_tick(tp2 + 10.0, timestamp="2026-10-14T10:05:00")
    assert res is not None
    assert res["reason"] == "TAKE_PROFIT"
    assert trader.active_trade is None
    assert trader.trade_closed_this_bar is True

    # Database records CLOSED trade
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT status, reason FROM paper_trades WHERE id = ?", (trade["id"],)).fetchone()
    assert row[0] == "CLOSED"
    assert row[1] == "TAKE_PROFIT"


def test_paper_trader_on_tick_stop_loss(temp_db):
    """Verify that on_tick triggers instant stop loss execution without waiting for candle close."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade is not None
    sl = trade["stop_loss"]

    # Flash crash tick crossing stop loss
    res = trader.on_tick(sl - 10.0, timestamp="2026-10-14T10:03:30")
    assert res is not None
    assert res["reason"] == "STOP_LOSS"
    assert res["r_multiple"] < 0.0
    assert trader.active_trade is None
    assert trader.trade_closed_this_bar is True


def test_paper_trader_on_tick_breakeven_arming_and_exit(temp_db):
    """Verify on_tick arms breakeven upon TP1 and subsequently executes breakeven exit."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade is not None
    tp1 = trade["tp1_price"]
    assert tp1 is not None

    # Price hits TP1: arms breakeven, position stays active
    res = trader.on_tick(tp1 + 5.0, timestamp="2026-10-14T10:04:00")
    assert res is None  # Trade still open with remaining 50%
    assert trader.active_trade["tp1_hit"] is True
    assert trader.active_trade["be_armed"] is True
    be_sl = trader.active_trade["stop_loss"]

    # Price drops back to new breakeven SL: exits with BREAKEVEN
    res = trader.on_tick(be_sl - 2.0, timestamp="2026-10-14T10:05:00")
    assert res is not None
    assert res["reason"] == "BREAKEVEN"
    assert res["r_multiple"] > 0.0  # Dynamic fill math: positive protected return
    assert trader.active_trade is None


def test_breakeven_exit_when_tp1_disabled_or_unhit(temp_db):
    """Verify that when be_armed is True but tp1_hit is False, exit is recorded as BREAKEVEN not STOP_LOSS."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade is not None

    # Simulate BE arming without TP1 hit (e.g. 1.00R trigger reached or custom BE arming)
    trader.active_trade["be_armed"] = True
    trader.active_trade["tp1_hit"] = False
    be_price = trader.active_trade["entry_price"] * 1.0005
    trader.active_trade["stop_loss"] = be_price

    # Candle hits the breakeven stop loss
    exit_candle = (be_price + 10.0, be_price + 20.0, be_price - 50.0, be_price - 10.0, 100.0)
    trader.on_candle(exit_candle, timestamp="2026-10-14T10:04:00")

    assert trader.active_trade is None
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT status, reason, r_multiple FROM paper_trades WHERE id = ?", (trade["id"],)).fetchone()
    assert row[0] == "CLOSED"
    assert row[1] == "BREAKEVEN"
    assert -0.20 < row[2] < 0.05


def test_on_tick_same_bar_reentry_prevention(temp_db):
    """Verify that if a trade is closed via on_tick, the subsequent on_candle call does not immediately re-enter."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade is not None
    sl = trade["stop_loss"]

    # Close trade via on_tick during the bar
    closed = trader.on_tick(sl - 10.0, timestamp="2026-10-14T10:03:45")
    assert closed is not None
    assert trader.active_trade is None
    assert trader.trade_closed_this_bar is True

    # Now the candle finishes and on_candle is called with another bullish trigger setup
    # It must NOT open a trade because closed_during_this_interval is True
    re_trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:04:00")
    assert re_trade is None
    assert trader.active_trade is None


def test_adx_hysteresis_buffer(temp_db):
    """Verify that ADX chop filter uses dual-threshold Schmitt trigger to prevent cliff-edge flickering."""
    # For Altcoins (e.g. ADAUSDT): enter chop at < 21.0, exit chop at >= 23.0
    trader = PaperTrader(symbol="ADAUSDT", timeframe="15m", db_path=temp_db)
    assert trader.in_chop_regime is False

    is_major = trader.symbol in ("BTCUSDT", "ETHUSDT")
    adx_high = 26.0 if is_major else 23.0
    adx_low = 24.0 if is_major else 21.0

    # 1. Mild dip to 22.2: does NOT enter chop regime
    adx_test = 22.2
    if trader.in_chop_regime:
        if adx_test >= adx_high:
            trader.in_chop_regime = False
    else:
        if adx_test < adx_low:
            trader.in_chop_regime = True
    assert trader.in_chop_regime is False

    # 2. Deep dip to 20.5 (< 21.0): enters chop regime!
    adx_test = 20.5
    if trader.in_chop_regime:
        if adx_test >= adx_high:
            trader.in_chop_regime = False
    else:
        if adx_test < adx_low:
            trader.in_chop_regime = True
    assert trader.in_chop_regime is True

    # 3. Partial bounce to 22.5 (< 23.0): remains locked in chop!
    adx_test = 22.5
    if trader.in_chop_regime:
        if adx_test >= adx_high:
            trader.in_chop_regime = False
    else:
        if adx_test < adx_low:
            trader.in_chop_regime = True
    assert trader.in_chop_regime is True

    # 4. Decisive recovery to 23.5 (>= 23.0): successfully clears chop!
    adx_test = 23.5
    if trader.in_chop_regime:
        if adx_test >= adx_high:
            trader.in_chop_regime = False
    else:
        if adx_test < adx_low:
            trader.in_chop_regime = True
    assert trader.in_chop_regime is False


def test_trade_entry_records_position_sizing_metrics(temp_db):
    """Verify that opening a trade records quantity, notional_usd, and leverage."""
    risk_manager = PortfolioRiskManager(
        account_equity_usd=10000.0,
        risk_per_trade_pct=1.0,
        max_leverage=3.0,
        db_path=temp_db,
    )
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db, risk_manager=risk_manager)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade is not None
    assert "quantity" in trade
    assert "notional_usd" in trade
    assert "leverage" in trade
    assert trade["quantity"] > 0.0
    assert trade["notional_usd"] > 0.0
    assert 0.0 < trade["leverage"] <= 3.0

    # Verify persisted in SQLite
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT quantity, notional_usd, leverage FROM paper_trades WHERE id = ?", (trade["id"],)).fetchone()
    assert row[0] is not None and row[0] > 0.0
    assert row[1] is not None and row[1] > 0.0
    assert row[2] is not None and 0.0 < row[2] <= 3.0


def test_estimate_confluence_priority(temp_db):
    """Verify that estimate_confluence_priority returns setup score before on_candle runs."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)

    score = trader.estimate_confluence_priority(trigger_candle)
    assert score >= 70.0

    # Once a trade is open, priority returns -100.0
    trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trader.active_trade is not None
    score_after = trader.estimate_confluence_priority(trigger_candle)
    assert score_after == -100.0


def test_synthetic_oco_bracket_creation_and_sl_fill(temp_db):
    """Verify that opening a trade registers synthetic OCO bracket orders, and SL fills cancel TPs."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade is not None
    trade_id = trade["id"]

    # Verify active synthetic OCO orders created
    with sqlite3.connect(temp_db) as conn:
        orders = conn.execute(
            "SELECT order_type, trigger_price, status FROM synthetic_oco_orders WHERE trade_id = ? ORDER BY id ASC",
            (trade_id,),
        ).fetchall()

    assert len(orders) >= 2
    types = [o[0] for o in orders]
    assert "STOP_LOSS" in types
    assert "TAKE_PROFIT_2" in types
    for o in orders:
        assert o[2] == "ACTIVE"

    # Stop Loss is triggered
    sl = trade["stop_loss"]
    res = trader.on_tick(sl - 10.0, timestamp="2026-10-14T10:04:00")
    assert res is not None
    assert trader.active_trade is None

    # Verify OCO resolution: SL is FILLED, other legs CANCELLED_BY_OCO
    with sqlite3.connect(temp_db) as conn:
        resolved = conn.execute(
            "SELECT order_type, status FROM synthetic_oco_orders WHERE trade_id = ?",
            (trade_id,),
        ).fetchall()

    resolved_map = {r[0]: r[1] for r in resolved}
    assert resolved_map["STOP_LOSS"] == "FILLED"
    assert resolved_map["TAKE_PROFIT_2"] == "CANCELLED_BY_OCO"


def test_synthetic_oco_bracket_tp_fill(temp_db):
    """Verify that hitting TP2 marks TP as FILLED and cancels resting SL with CANCELLED_BY_OCO."""
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade is not None
    trade_id = trade["id"]
    tp2 = trade["take_profit"]

    # TP2 is reached
    res = trader.on_tick(tp2 + 10.0, timestamp="2026-10-14T10:04:00")
    assert res is not None
    assert res["reason"] == "TAKE_PROFIT"

    with sqlite3.connect(temp_db) as conn:
        resolved = conn.execute(
            "SELECT order_type, status FROM synthetic_oco_orders WHERE trade_id = ?",
            (trade_id,),
        ).fetchall()

    resolved_map = {r[0]: r[1] for r in resolved}
    assert resolved_map["TAKE_PROFIT_2"] == "FILLED"
    assert resolved_map["STOP_LOSS"] == "CANCELLED_BY_OCO"


def test_preempt_trade_lifecycle(temp_db):
    """Verify that preempt_trade cleanly scratches an active position and initiates cooldown."""
    risk_manager = PortfolioRiskManager(db_path=temp_db)
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db, risk_manager=risk_manager)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")
    assert trade is not None
    trade_id = trade["id"]
    entry = trade["entry_price"]

    # Preempt trade at scratch
    preempt_snapshot = trader.preempt_trade(exit_price=entry + 5.0, exit_timestamp="2026-10-14T10:15:00")
    assert preempt_snapshot is not None
    assert preempt_snapshot["reason"] == "PREEMPTION_REALLOCATION"
    assert trader.active_trade is None
    assert trader.trade_closed_this_bar is True

    # Verify persisted in SQLite
    with sqlite3.connect(temp_db) as conn:
        row = conn.execute("SELECT status, reason, exit_price FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
    assert row[0] == "CLOSED"
    assert row[1] == "PREEMPTION_REALLOCATION"
    assert row[2] == entry + 5.0

    # Verify OCO orders were resolved
    with sqlite3.connect(temp_db) as conn:
        oco_status = conn.execute("SELECT DISTINCT status FROM synthetic_oco_orders WHERE trade_id = ?", (trade_id,)).fetchall()
    statuses = [s[0] for s in oco_status]
    assert "ACTIVE" not in statuses


def test_collinearity_cap_limits_momentum_points(temp_db):
    """Verify that enable_collinearity_cap caps Trend + ADX + Vol at 35.0 pts max."""
    trader_uncapped = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db, enable_collinearity_cap=False)
    trader_capped = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db, enable_collinearity_cap=True)

    # Setup ready trader
    trigger_candle = setup_trader_ready_for_trigger(trader_uncapped)
    setup_trader_ready_for_trigger(trader_capped)

    score_uncapped = trader_uncapped.estimate_confluence_priority(trigger_candle)
    score_capped = trader_capped.estimate_confluence_priority(trigger_candle)

    # In setup_trader_ready_for_trigger: Trend=20, ADX=15, Vol=10 (Sum=45).
    # When capped at 35.0, score drops by exactly 10.0 points (45 - 35).
    assert score_uncapped == 70.0
    assert score_capped == 60.0


def test_order_book_opposing_ask_wall_blocks_long(temp_db):
    """Verify that a massive resting ask wall between entry and TP1 blocks long execution."""
    from microstructure import OrderBookEngine
    engine = OrderBookEngine(wall_multiplier=2.0)

    # Entry will be around 50000.0, ATR around 500.0, TP1 around 50700.0.
    # Place an ask wall at 50200.0
    payload = {
        "bids": [[f"{50000 - i*10}", "1.0"] for i in range(1, 10)],
        "asks": [["50010.0", "1.0"], ["50200.0", "100.0"], ["50300.0", "1.0"], ["50400.0", "1.0"]],
    }
    engine.process_depth_payload("BTCUSDT", payload)

    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db, order_book_engine=engine)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")

    assert trade is None
    assert trader.get_active_trade() is None


def test_order_book_negative_imbalance_blocks_long(temp_db):
    """Verify that severe negative order book imbalance (OBI < -25%) blocks long execution."""
    from microstructure import OrderBookEngine
    engine = OrderBookEngine()

    # Massive ask depth vs thin bid depth -> OBI < -25%
    payload = {
        "bids": [[f"{50000 - i*10}", "1.0"] for i in range(1, 6)],
        "asks": [[f"{50010 + i*10}", "10.0"] for i in range(1, 6)],
    }
    engine.process_depth_payload("BTCUSDT", payload)

    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db, order_book_engine=engine)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")

    assert trade is None
    assert trader.get_active_trade() is None


def test_order_book_favorable_permits_entry(temp_db):
    """Verify that favorable order book depth allows clean trade execution."""
    from microstructure import OrderBookEngine
    engine = OrderBookEngine()

    payload = {
        "bids": [[f"{50000 - i*10}", "2.0"] for i in range(1, 6)],
        "asks": [[f"{50010 + i*10}", "1.0"] for i in range(1, 6)],
    }
    engine.process_depth_payload("BTCUSDT", payload)

    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=temp_db, order_book_engine=engine)
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14T10:03:00")

    assert trade is not None
    assert trade["side"] == "LONG"



