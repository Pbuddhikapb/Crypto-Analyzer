"""
Unit tests for the Realistic Simulation Engine (engine/simulator.py).
Tests fees, adverse slippage, 8h funding rate drag, portfolio risk concurrency,
directional constraints, symbol cooldowns, and performance metrics.
"""


import numpy as np
import pytest

from engine.exits import ExitConfig
from engine.simulator import (
    CandidateSignal,
    ExecutedTrade,
    SimulationEngine,
    SimulationFriction,
    simulate_portfolio_signals,
)


def test_entry_friction_and_slippage_long():
    friction = SimulationFriction(
        taker_fee_pct=0.05,
        slippage_pct=0.03,
    )
    sim = SimulationEngine(
        initial_equity_usd=10000.0,
        risk_per_trade_pct=1.0,
        friction=friction,
    )

    signal = CandidateSignal(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=100.0,
        stop_loss=90.0,  # Risk dist = 10.0 -> Risk USD = 100.0 -> Shares = 10.0 -> Notional = 1000.0
        timestamp_ms=1700000000000,
    )

    trade_id = sim.open_position(signal, current_bar_idx=0)
    assert trade_id is not None
    assert trade_id in sim.active_positions

    pos = sim.active_positions[trade_id]
    # Slippage: 100.0 * 1.0003 = 100.03
    assert round(pos["entry_fill_price"], 4) == 100.03
    # Notional: 1000.0
    assert round(pos["notional_usd"], 2) == 1000.0
    # Entry fee: 1000 * 0.0005 = 0.50 USD
    assert round(pos["entry_fee_usd"], 2) == 0.50
    # Equity immediately reduced by entry fee: 10000 - 0.50 = 9999.50
    assert round(sim.equity_usd, 2) == 9999.50


def test_stop_loss_exit_friction_and_slippage():
    friction = SimulationFriction(
        taker_fee_pct=0.05,
        slippage_pct=0.03,
    )
    sim = SimulationEngine(
        initial_equity_usd=10000.0,
        risk_per_trade_pct=1.0,
        friction=friction,
    )

    signal = CandidateSignal(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=100.0,
        stop_loss=90.0,
        timestamp_ms=1700000000000,
    )
    sim.open_position(signal, current_bar_idx=0)

    # Bar 1 hits Stop Loss at 90.0
    candle = (98.0, 99.0, 89.0, 92.0)
    closed = sim.process_bar("BTCUSDT", candle, timestamp_ms=1700003600000, current_bar_idx=1)
    assert len(closed) == 1
    t = closed[0]

    assert t.exit_reason == "STOP_LOSS"
    assert t.exit_price == 90.0
    # Stop exit slips adversely: 90.0 * (1 - 0.0003) = 89.973
    assert round(t.exit_fill_price, 4) == 89.973
    # Total fees = entry fee (0.50) + exit fee (0.45) = ~0.95
    assert t.fees_usd > 0.90
    # Slippage was incurred on both entry and exit
    assert t.slippage_usd > 0.50
    # Net PnL is negative and includes friction drag
    assert t.net_pnl_usd < -100.0
    # Cash equity delta matches net_pnl_usd to the penny (no double counting)
    assert round(sim.equity_usd - sim.initial_equity_usd, 2) == round(t.net_pnl_usd, 2)


def test_8h_funding_rate_accrual():
    friction = SimulationFriction(funding_rate_8h_pct=0.01)  # 0.01% per 8h
    sim = SimulationEngine(
        initial_equity_usd=10000.0,
        risk_per_trade_pct=1.0,
        friction=friction,
    )

    # Open position at t=0
    signal = CandidateSignal(
        symbol="ETHUSDT",
        side="LONG",
        entry_price=1000.0,
        stop_loss=950.0,  # Risk dist = 50 -> Shares = 2.0 -> Notional = 2000.0
        timestamp_ms=0,
    )
    sim.open_position(signal, current_bar_idx=0)

    # Bar at 25 hours (crosses 3 8-hour intervals: 8h, 16h, 24h)
    ts_25h = 25 * 3600 * 1000
    candle = (1000.0, 1010.0, 990.0, 1005.0)  # stays within bounds
    sim.process_bar("ETHUSDT", candle, timestamp_ms=ts_25h, current_bar_idx=25)

    pos = list(sim.active_positions.values())[0]
    # 3 funding periods * 0.01% * 2000 = 0.60 USD
    assert round(pos["funding_cost_usd"], 2) == 0.60


def test_portfolio_concurrency_limit():
    sim = SimulationEngine(
        initial_equity_usd=10000.0,
        max_open_positions=2,
    )

    # 1st trade opens
    sig1 = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, 1000)
    assert sim.open_position(sig1, 0) is not None

    # 2nd trade opens
    sig2 = CandidateSignal("ETHUSDT", "LONG", 1000.0, 900.0, 1000)
    assert sim.open_position(sig2, 0) is not None

    # 3rd trade should be rejected by concurrency limit
    sig3 = CandidateSignal("SOLUSDT", "LONG", 50.0, 45.0, 1000)
    assert sim.open_position(sig3, 0) is None
    assert len(sim.active_positions) == 2


def test_max_directional_positions_limit():
    sim = SimulationEngine(
        initial_equity_usd=10000.0,
        max_open_positions=5,
        max_directional_positions=1,  # Max 1 Long and 1 Short
    )

    sig_long1 = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, 1000)
    assert sim.open_position(sig_long1, 0) is not None

    # 2nd LONG rejected
    sig_long2 = CandidateSignal("ETHUSDT", "LONG", 1000.0, 900.0, 1000)
    assert sim.open_position(sig_long2, 0) is None

    # But a SHORT is allowed!
    sig_short = CandidateSignal("SOLUSDT", "SHORT", 50.0, 55.0, 1000)
    assert sim.open_position(sig_short, 0) is not None
    assert len(sim.active_positions) == 2


def test_symbol_cooldown():
    sim = SimulationEngine(
        initial_equity_usd=10000.0,
        symbol_cooldown_bars=3,
    )

    sig1 = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, 1000)
    sim.open_position(sig1, 0)

    # Close trade at bar 5
    candle = (98.0, 99.0, 89.0, 92.0)
    sim.process_bar("BTCUSDT", candle, timestamp_ms=2000, current_bar_idx=5)
    assert len(sim.active_positions) == 0

    # At bar 6: cooldown is 1/3 -> rejected
    sig_new1 = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, 3000)
    assert sim.open_position(sig_new1, 6) is None

    # At bar 7: cooldown is 2/3 -> rejected
    assert sim.open_position(sig_new1, 7) is None

    # At bar 8: cooldown elapsed (8 - 5 = 3 >= 3) -> allowed!
    assert sim.open_position(sig_new1, 8) is not None


def test_compute_summary_metrics():
    sim = SimulationEngine(initial_equity_usd=10000.0)

    # Simulate 2 wins and 1 loss
    # Win 1
    sig1 = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, 1000)
    sim.open_position(sig1, 0)
    # Candle 1 hits TP1 (107.5) -> closes 50%
    c_win1 = (105.0, 110.0, 104.0, 109.0)
    sim.process_bar("BTCUSDT", c_win1, 1500, 1)
    # Candle 2 hits TP2 (113.3) -> closes remaining 50%
    c_win2 = (109.0, 115.0, 108.0, 114.0)
    sim.process_bar("BTCUSDT", c_win2, 2000, 2)

    # Loss 1
    sig2 = CandidateSignal("ETHUSDT", "LONG", 1000.0, 900.0, 3000)
    sim.open_position(sig2, 2)
    c_loss = (980.0, 990.0, 890.0, 900.0)
    sim.process_bar("ETHUSDT", c_loss, 4000, 3)

    summary = sim.compute_summary()
    assert summary.total_trades == 2
    assert summary.winning_trades == 1
    assert summary.losing_trades == 1
    assert summary.win_rate_pct == 50.0
    assert summary.profit_factor > 0.0
    assert summary.expectancy_usd != 0.0
    assert summary.max_drawdown_pct >= 0.0


def test_partial_loss_exit_friction_and_slippage():
    friction = SimulationFriction(taker_fee_pct=0.05, maker_fee_pct=0.02, slippage_pct=0.03)
    # High be_arm_r so breakeven is NOT armed when TP1 is hit
    exit_config = ExitConfig(tp1_r=0.75, be_arm_r=5.0, tp2_r=10.0)
    sim = SimulationEngine(initial_equity_usd=10000.0, friction=friction, exit_config=exit_config)

    sig = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, 1000)
    sim.open_position(sig, 0)

    # Bar 1 hits TP1 (107.5) -> closes 50%
    c1 = (100.0, 108.0, 99.0, 107.0)
    sim.process_bar("BTCUSDT", c1, 2000, 1)

    # Bar 2 crashes down through initial SL (90.0) -> triggers PARTIAL_LOSS
    c2 = (100.0, 101.0, 89.0, 90.0)
    closed = sim.process_bar("BTCUSDT", c2, 3000, 2)
    assert len(closed) == 1
    t = closed[0]
    assert t.exit_reason == "PARTIAL_LOSS"
    # PARTIAL_LOSS must incur adverse slippage and taker fee on the remaining 50%
    assert t.exit_fill_price < 90.0
    assert t.slippage_usd > 0.30
    assert round(sim.equity_usd - sim.initial_equity_usd, 2) == round(t.net_pnl_usd, 2)


def test_multi_symbol_equity_curve_deduplication():
    sim = SimulationEngine(initial_equity_usd=10000.0)
    sig1 = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, 1000)
    sig2 = CandidateSignal("ETHUSDT", "LONG", 1000.0, 900.0, 1000)
    sim.open_position(sig1, 0)
    sim.open_position(sig2, 0)

    # Both symbols process bar at timestamp 2000
    c_btc = (100.0, 101.0, 99.0, 100.5)
    c_eth = (1000.0, 1010.0, 990.0, 1005.0)
    sim.process_bar("BTCUSDT", c_btc, 2000, 1)
    sim.process_bar("ETHUSDT", c_eth, 2000, 1)

    # Equity curve must only contain 1 entry for timestamp 2000, not 2!
    ts_list = [ts for ts, _ in sim.equity_curve]
    assert ts_list.count(2000) == 1


def test_sortino_ratio_identical_losses():
    sim = SimulationEngine(initial_equity_usd=10000.0)
    from engine.simulator import ExecutedTrade
    # 2 wins (+2%) and 2 identical losses (-1%)
    # Standard deviation of identical losses is 0, but Sortino semi-deviation must be > 0
    t_win1 = ExecutedTrade("t1", "BTCUSDT", "LONG", 0, 1, 100, 100, 102, 102, "TAKE_PROFIT_2", 1000, 20, 1, 0, 0, 19, 1.9, 2.0, 1, 2.0, 0)
    t_win2 = ExecutedTrade("t2", "BTCUSDT", "LONG", 0, 1, 100, 100, 102, 102, "TAKE_PROFIT_2", 1000, 20, 1, 0, 0, 19, 1.9, 2.0, 1, 2.0, 0)
    t_loss1 = ExecutedTrade("t3", "BTCUSDT", "LONG", 0, 1, 100, 100, 99, 99, "STOP_LOSS", 1000, -10, 1, 0, 0, -11, -1.1, -1.0, 1, 0, -1.0)
    t_loss2 = ExecutedTrade("t4", "BTCUSDT", "LONG", 0, 1, 100, 100, 99, 99, "STOP_LOSS", 1000, -10, 1, 0, 0, -11, -1.1, -1.0, 1, 0, -1.0)
    sim.completed_trades = [t_win1, t_win2, t_loss1, t_loss2]
    sim.equity_usd = 10000.0 + 19 + 19 - 11 - 11

    summary = sim.compute_summary()
    assert summary.sortino_ratio > 0.0


def test_compounding_mode_halts_at_ruin():
    """Deterministic losing streak forces ruin (equity <= ruin_threshold_usd) and rejects all future positions in compounding mode."""
    sim = SimulationEngine(
        initial_equity_usd=1000.0,
        risk_per_trade_pct=40.0,  # 40% risk per trade in compounding mode
        ruin_threshold_usd=500.0,  # Ruin threshold at $500
        sizing_mode="compounding",
    )

    # Trade 1: loses ~30% with leverage cap, equity drops to ~$695
    sig1 = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, timestamp_ms=1000)
    t1_id = sim.open_position(sig1, current_bar_idx=0)
    assert t1_id is not None
    # Bar crashes down to 85 (below SL 90)
    c_loss1 = (100.0, 101.0, 85.0, 88.0)
    sim.process_bar("BTCUSDT", c_loss1, timestamp_ms=2000, current_bar_idx=1)
    assert sim.equity_usd < 750.0
    assert not sim.ruined

    # Trade 2: loses another ~40% of remaining equity, pushing equity below $500 ruin threshold
    sig2 = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, timestamp_ms=3000)
    t2_id = sim.open_position(sig2, current_bar_idx=5)
    assert t2_id is not None
    sim.process_bar("BTCUSDT", c_loss1, timestamp_ms=4000, current_bar_idx=6)
    assert sim.equity_usd <= 500.0

    # Trade 3: Must be rejected because account is ruined
    sig3 = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, timestamp_ms=5000)
    can_open, reason = sim.can_open_position(sig3.symbol, sig3.side, current_bar_idx=10)
    assert not can_open
    assert "ruined" in reason.lower()

    t3_id = sim.open_position(sig3, current_bar_idx=10)
    assert t3_id is None

    summary = sim.compute_summary()
    assert summary.ruined is True
    assert summary.ruin_time_ms == 5000
    assert summary.total_trades == 2


test_ruin_guard_stops_new_positions = test_compounding_mode_halts_at_ruin


def test_fixed_risk_run_evaluates_100pct_of_dataset_days_even_after_deep_drawdown():
    """In fixed_risk mode, ruin guard never halts entries; evaluates 100% of dataset days even when cumulative R < -95."""
    sim = SimulationEngine(
        initial_equity_usd=10000.0,
        risk_per_trade_pct=1.0,  # $100 risk per trade
        ruin_threshold_usd=500.0,  # Ruin threshold at $500
        sizing_mode="fixed_risk",
    )

    t_ms = 1704067200000  # Start 2024-01-01
    c_loss = (100.0, 101.0, 85.0, 88.0)

    # Execute 100 consecutive losing trades, each losing ~1.0R
    for i in range(100):
        sig = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, timestamp_ms=t_ms)
        t_id = sim.open_position(sig, current_bar_idx=i * 5)
        assert t_id is not None
        sim.process_bar("BTCUSDT", c_loss, timestamp_ms=t_ms + 3600000, current_bar_idx=i * 5 + 1)
        t_ms += 86400000  # 1 day spacing

    # After 100 losing trades, cumulative_r is around -100R (< -95R)
    # Account equity is negative, ruined flag is set (report-only)
    assert sim.ruined is True
    assert sim.ruin_time_ms is not None

    # Trade 101: Must still be allowed to open in fixed_risk mode!
    sig_next = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, timestamp_ms=t_ms)
    can_open, reason = sim.can_open_position(sig_next.symbol, sig_next.side, current_bar_idx=505)
    assert can_open is True
    assert reason == "OK"

    t_next_id = sim.open_position(sig_next, current_bar_idx=505)
    assert t_next_id is not None
    sim.process_bar("BTCUSDT", c_loss, timestamp_ms=t_ms + 3600000, current_bar_idx=506)

    summary = sim.compute_summary()
    assert summary.total_trades == 101
    assert summary.cumulative_r < -95.0
    assert summary.max_drawdown_r >= 95.0
    assert summary.pct_of_dataset_evaluated == 100.0


def test_fixed_risk_sizing_independent_of_pnl():
    """In fixed_risk mode, position size is calculated from initial equity, invariant to PnL changes."""
    # 1. Fixed risk engine
    sim_fixed = SimulationEngine(
        initial_equity_usd=10000.0,
        risk_per_trade_pct=1.0,  # $100 risk
        sizing_mode="fixed_risk",
    )
    sig1 = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, timestamp_ms=1000)
    t1_id = sim_fixed.open_position(sig1, 0)
    pos1_fixed = sim_fixed.active_positions[t1_id]["notional_usd"]
    # Close with a 1R loss (SL at 90.0)
    c_loss = (100.0, 100.0, 85.0, 88.0)
    sim_fixed.process_bar("BTCUSDT", c_loss, timestamp_ms=2000, current_bar_idx=1)
    assert sim_fixed.equity_usd < 9950.0

    # Trade 2 with same risk distance
    sig2 = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, timestamp_ms=3000)
    t2_id = sim_fixed.open_position(sig2, 10)
    pos2_fixed = sim_fixed.active_positions[t2_id]["notional_usd"]

    # In fixed_risk, position size for trade 2 must equal trade 1
    assert round(pos1_fixed, 2) == round(pos2_fixed, 2)

    # 2. In compounding mode, position size scales down with lower equity
    sim_comp = SimulationEngine(
        initial_equity_usd=10000.0,
        risk_per_trade_pct=1.0,
        sizing_mode="compounding",
    )
    t1_c_id = sim_comp.open_position(sig1, 0)
    pos1_comp = sim_comp.active_positions[t1_c_id]["notional_usd"]
    sim_comp.process_bar("BTCUSDT", c_loss, timestamp_ms=2000, current_bar_idx=1)

    t2_c_id = sim_comp.open_position(sig2, 10)
    pos2_comp = sim_comp.active_positions[t2_c_id]["notional_usd"]
    assert pos2_comp < pos1_comp


def test_no_ghost_trades_below_minimum_unless_before_ruin():
    """Ensure no executed trade in SimulationResult has position_size_usd < $10 unless opened before ruin."""
    sim = SimulationEngine(
        initial_equity_usd=1000.0,
        ruin_threshold_usd=200.0,
        risk_per_trade_pct=10.0,  # $100 risk
        sizing_mode="fixed_risk",
    )

    for i in range(15):
        ts = 10000 + i * 1000
        sig = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, timestamp_ms=ts)
        t_id = sim.open_position(sig, current_bar_idx=i * 5)
        if t_id:
            # Suffer loss
            c = (100.0, 101.0, 85.0, 88.0)
            sim.process_bar("BTCUSDT", c, timestamp_ms=ts + 500, current_bar_idx=i * 5 + 1)

    summary = sim.compute_summary()
    assert summary.ruined is True
    assert summary.total_trades > 0

    for t in summary.trades:
        # Every trade must either have position >= $10 or have been opened before ruin occurred
        if summary.ruin_time_ms is not None:
            assert t.position_size_usd >= 10.0 or t.entry_time_ms < summary.ruin_time_ms
        else:
            assert t.position_size_usd >= 10.0


def test_net_profit_factor_and_net_r_with_fee_flipped_winner():
    """
    Test 3 hand-computed trades:
    Trade 1: Gross +$150, fees $20 -> Net +$130 (Winner, gross & net)
    Trade 2: Gross +$10, fees $25  -> Net -$15  (Gross winner flipped to net loser by fees)
    Trade 3: Gross -$100, fees $20 -> Net -$120 (Loser, gross & net)

    Total Gross: +$60.00 (> 0)
    Total Fees:  $65.00 (> Gross PnL)
    Total Net:   -$5.00 (< 0)

    Gross PF: 160.0 / 100.0 = 1.60 (> 1.0)
    Net PF:   130.0 / 135.0 = 0.9630 (< 1.0)
    """
    sim = SimulationEngine(initial_equity_usd=10000.0)

    # Hand-constructed trade 1: Net Winner
    t1 = ExecutedTrade(
        trade_id="T1",
        symbol="BTCUSDT",
        side="LONG",
        entry_time_ms=1000,
        exit_time_ms=2000,
        entry_price=100.0,
        entry_fill_price=100.0,
        exit_price=115.0,
        exit_fill_price=115.0,
        exit_reason="TAKE_PROFIT",
        position_size_usd=1000.0,
        gross_pnl_usd=150.0,
        fees_usd=20.0,
        slippage_usd=0.0,
        funding_cost_usd=0.0,
        net_pnl_usd=130.0,
        net_return_pct=13.0,
        realized_r=1.50,
        bars_held=5,
        mfe_r=1.50,
        mae_r=0.0,
        initial_risk_usd=100.0,
        net_r=1.30,
    )

    # Hand-constructed trade 2: Gross winner flipped to net loser by fees
    t2 = ExecutedTrade(
        trade_id="T2",
        symbol="ETHUSDT",
        side="LONG",
        entry_time_ms=3000,
        exit_time_ms=4000,
        entry_price=200.0,
        entry_fill_price=200.0,
        exit_price=202.0,
        exit_fill_price=202.0,
        exit_reason="TIME_STOP",
        position_size_usd=1000.0,
        gross_pnl_usd=10.0,
        fees_usd=25.0,
        slippage_usd=0.0,
        funding_cost_usd=0.0,
        net_pnl_usd=-15.0,
        net_return_pct=-1.5,
        realized_r=0.10,
        bars_held=20,
        mfe_r=0.20,
        mae_r=-0.10,
        initial_risk_usd=100.0,
        net_r=-0.15,
    )

    # Hand-constructed trade 3: Loser
    t3 = ExecutedTrade(
        trade_id="T3",
        symbol="SOLUSDT",
        side="SHORT",
        entry_time_ms=5000,
        exit_time_ms=6000,
        entry_price=50.0,
        entry_fill_price=50.0,
        exit_price=55.0,
        exit_fill_price=55.0,
        exit_reason="STOP_LOSS",
        position_size_usd=1000.0,
        gross_pnl_usd=-100.0,
        fees_usd=20.0,
        slippage_usd=0.0,
        funding_cost_usd=0.0,
        net_pnl_usd=-120.0,
        net_return_pct=-12.0,
        realized_r=-1.00,
        bars_held=8,
        mfe_r=0.0,
        mae_r=-1.00,
        initial_risk_usd=100.0,
        net_r=-1.20,
    )

    sim.completed_trades = [t1, t2, t3]
    sim.equity_usd = 10000.0 + 130.0 - 15.0 - 120.0  # $9995.00
    sim.equity_curve = [(0, 10000.0), (6000, 9995.0)]

    summary = sim.compute_summary()

    # Exact trade net_r assertions
    assert summary.trades[0].net_r == 1.30
    assert summary.trades[1].net_r == -0.15
    assert summary.trades[2].net_r == -1.20

    # Trade counts
    assert summary.total_trades == 3
    assert summary.winning_trades == 1  # Only T1 is net > 0
    assert summary.losing_trades == 2   # T2 and T3 are net <= 0

    # Profit Factor assertions
    # Gross PF = 160.0 / 100.0 = 1.60
    assert abs(summary.gross_profit_factor - 1.60) < 1e-4
    # Net PF = 130.0 / (15.0 + 120.0) = 130.0 / 135.0 = ~0.962963 -> rounded to 0.96
    assert summary.profit_factor == 0.96
    # Acceptance criteria: Gross PnL positive (+60) but fees ($65) > gross PnL -> Net PF < 1.0
    assert summary.profit_factor < 1.0
    assert summary.gross_profit_factor > 1.0

    # Net Expectancy R assertion: (1/3 * 1.30) - (2/3 * 0.675) = -0.05 / 3 = -0.01667 -> rounded to -0.0167
    expected_net_ev_r = round((1.30 - 0.15 - 1.20) / 3.0, 4)
    assert abs(summary.expectancy_r - expected_net_ev_r) < 1e-4
    assert summary.expectancy_r < 0.0

    # Also verify that SimulationEngine.process_bar sets initial_risk_usd and net_r on simulated trades
    sim_live = SimulationEngine(initial_equity_usd=10000.0, risk_per_trade_pct=1.0)
    sig = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, timestamp_ms=1000)
    t_id = sim_live.open_position(sig, current_bar_idx=0)
    assert t_id is not None
    # 1% risk on 10,000 is $100.0 risk
    assert sim_live.active_positions[t_id]["risk_usd"] == 100.0

    # Bar hits stop loss
    c_loss = (100.0, 101.0, 89.0, 89.0)
    closed = sim_live.process_bar("BTCUSDT", c_loss, timestamp_ms=2000, current_bar_idx=1)
    assert len(closed) == 1
    assert closed[0].initial_risk_usd == 100.0
    # Since it stopped out with slippage/fees, net_pnl is slightly worse than -$100, so net_r < -1.0
    assert closed[0].net_r < -0.95


def test_skip_signals_with_zero_volume(caplog):
    """Verify signals whose entry bar has volume == 0 are skipped and logged."""
    import logging

    sim = SimulationEngine(initial_equity_usd=10000.0)

    # 1. CandidateSignal with explicit volume=0.0
    sig_zero_vol = CandidateSignal(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=50000.0,
        stop_loss=49000.0,
        timestamp_ms=1000,
        volume=0.0,
    )
    with caplog.at_level(logging.WARNING):
        t_id1 = sim.open_position(sig_zero_vol, current_bar_idx=0)
        assert t_id1 is None
        assert "volume == 0" in caplog.text or "volume is zero" in caplog.text

    # 2. CandidateSignal with metadata volume == 0.0
    caplog.clear()
    sig_meta_zero = CandidateSignal(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=50000.0,
        stop_loss=49000.0,
        timestamp_ms=2000,
        metadata={"volume": 0.0},
    )
    with caplog.at_level(logging.WARNING):
        t_id2 = sim.open_position(sig_meta_zero, current_bar_idx=1)
        assert t_id2 is None
        assert "volume == 0" in caplog.text or "volume is zero" in caplog.text

    # 3. Explicit entry_volume=0.0 passed to open_position
    sig_normal = CandidateSignal(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=50000.0,
        stop_loss=49000.0,
        timestamp_ms=3000,
    )
    t_id3 = sim.open_position(sig_normal, current_bar_idx=2, entry_volume=0.0)
    assert t_id3 is None

    # 4. Signal with normal positive volume enters successfully
    t_id_valid = sim.open_position(sig_normal, current_bar_idx=3, entry_volume=125.0)
    assert t_id_valid is not None
    assert len(sim.active_positions) == 1


def test_session_break_on_large_gap_terminates_open_trades_and_logs(caplog):
    """Verify gaps larger than bar interval trigger a session break and liquidate open trades."""
    import logging

    interval_ms = 3600 * 1000  # 1 hour
    sim = SimulationEngine(initial_equity_usd=10000.0, bar_interval_ms=interval_ms)
    sim.indicator_buffers["BTCUSDT"] = {"rolling_mean": 50000.0}

    # Open a trade at bar 0
    t0 = 1700000000000
    sig = CandidateSignal(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=50000.0,
        stop_loss=48000.0,
        timestamp_ms=t0,
        volume=50.0,
    )
    t_id = sim.open_position(sig, current_bar_idx=0)
    assert t_id is not None
    assert len(sim.active_positions) == 1

    # First normal bar at t0 + 1h (gap == interval_ms)
    t1 = t0 + interval_ms
    c1 = (50100.0, 50200.0, 50000.0, 50150.0)
    closed = sim.process_bar("BTCUSDT", c1, timestamp_ms=t1, current_bar_idx=1)
    assert len(closed) == 0
    assert len(sim.active_positions) == 1
    assert "rolling_mean" in sim.indicator_buffers["BTCUSDT"]

    # Session break: jump 5 hours forward (t1 + 5h, gap = 5h > 1h)
    t2 = t1 + (5 * interval_ms)
    c2 = (49500.0, 49800.0, 49200.0, 49600.0)
    with caplog.at_level(logging.WARNING):
        closed_break = sim.process_bar("BTCUSDT", c2, timestamp_ms=t2, current_bar_idx=2)

    # Must log session break warning
    assert "Session break detected for BTCUSDT" in caplog.text

    # Must clear indicator buffers
    assert len(sim.indicator_buffers["BTCUSDT"]) == 0

    # Must terminate open trade at bar open (49500.0) with SESSION_BREAK exit reason
    assert len(closed_break) == 1
    trade = closed_break[0]
    assert trade.exit_reason == "SESSION_BREAK"
    assert trade.exit_price == 49500.0
    assert len(sim.active_positions) == 0
    assert len(sim.completed_trades) == 1


def test_research_path_skips_zero_volume_and_terminates_on_gap(caplog):
    """
    Verify that the research path (simulate_portfolio_signals using SimulationEngine)
    skips signals on zero-volume bars and terminates open trades on session break gaps.
    """
    import logging
    import numpy as np
    from engine.simulator import CandidateSignal, simulate_portfolio_signals

    interval_ms = 3600 * 1000  # 1 hour
    t0 = 1700000000000

    # Tiny dataset: 5 bars
    # Bar 0: normal volume (1000)
    # Bar 1: zero volume (0.0) -> Candidate signal 1 here must be skipped!
    # Bar 2: normal volume (1000) -> Candidate signal 2 here must enter!
    # Bar 3: normal volume (1000), but timestamp is t0 + 7h (gap of 5h > 1h!) -> must terminate trade with SESSION_BREAK!
    # Bar 4: normal volume (1000)
    bars = [
        [100.0, 102.0, 99.0, 101.0, t0, 1000.0],
        [101.0, 103.0, 100.0, 102.0, t0 + interval_ms, 0.0],
        [102.0, 104.0, 101.0, 103.0, t0 + 2 * interval_ms, 1000.0],
        [103.0, 105.0, 102.0, 104.0, t0 + 7 * interval_ms, 1000.0],
        [104.0, 106.0, 103.0, 105.0, t0 + 8 * interval_ms, 1000.0],
    ]
    arr = np.array(bars, dtype=np.float64)
    candles_dict = {"TESTUSDT": arr}

    # Candidate 1: on zero volume bar (Bar 1)
    sig_zero_vol = CandidateSignal(
        symbol="TESTUSDT",
        side="LONG",
        entry_price=101.0,
        stop_loss=95.0,
        timestamp_ms=t0 + interval_ms,
    )
    # Candidate 2: on normal volume bar (Bar 2)
    sig_valid = CandidateSignal(
        symbol="TESTUSDT",
        side="LONG",
        entry_price=102.0,
        stop_loss=95.0,
        timestamp_ms=t0 + 2 * interval_ms,
    )

    with caplog.at_level(logging.WARNING):
        res = simulate_portfolio_signals(
            candidates=[sig_zero_vol, sig_valid],
            candles_dict=candles_dict,
            initial_equity_usd=10000.0,
            bar_interval_ms=interval_ms,
        )

    # Candidate 1 was skipped due to zero volume
    assert "volume == 0" in caplog.text or "volume is zero" in caplog.text

    # Only 1 trade was executed (Candidate 2)
    assert res.total_trades == 1
    executed_trade = res.trades[0]
    assert executed_trade.entry_time_ms == t0 + 2 * interval_ms

    # Trade was terminated on SESSION_BREAK at Bar 3 (due to 5h gap)
    assert "Session break detected for TESTUSDT" in caplog.text
    assert executed_trade.exit_reason == "SESSION_BREAK"
    assert executed_trade.exit_time_ms == t0 + 7 * interval_ms


def test_trailing_exit_trade_lasts_300_bars_zero_dropped():
    """
    Verify that a synthetic trending series where a trailing-exit trade lasts > 300 bars
    appears in simulation results without being dropped by a 120-bar window,
    and assert zero dropped trades.
    """
    import numpy as np
    from engine.exits import ExitConfig
    from engine.simulator import CandidateSignal, simulate_portfolio_signals

    t0 = 1700000000000
    interval_ms = 3600 * 1000  # 1h
    n_bars = 400

    # Build trending price series
    # Bars 0 to 320: steady uptrend from 100.0 to 132.0 (0.1 per bar)
    # Bar 321: sharp drop to 110.0 to trigger trailing stop (peak=132, trail_dist_r=10 -> trail_stop=122)
    # Bars 322 to 399: flat at 110.0
    bars = []
    for i in range(n_bars):
        t = t0 + i * interval_ms
        if i <= 320:
            p = 100.0 + i * 0.1
            high = p + 0.05
            low = p - 0.05
            close = p
        elif i == 321:
            p = 132.0
            high = 132.0
            low = 120.0
            close = 121.0
        else:
            p = 112.0
            high = 113.0
            low = 111.0
            close = 112.0
        bars.append([p, high, low, close, t, 1000.0])

    arr = np.array(bars, dtype=np.float64)
    candles_dict = {"BTCUSDT": arr}

    sig = CandidateSignal(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=100.0,
        stop_loss=90.0,  # risk_dist = 10.0
        timestamp_ms=t0,
        volume=1000.0,
    )

    exit_cfg = ExitConfig(exit_mode="trailing_1r", trail_dist_r=1.0)
    res = simulate_portfolio_signals(
        candidates=[sig],
        candles_dict=candles_dict,
        exit_config=exit_cfg,
        bar_interval_ms=interval_ms,
    )

    assert res.total_trades == 1
    assert len(res.trades) == 1
    trade = res.trades[0]
    assert trade.bars_held >= 300, f"Expected trade to last >= 300 bars, but held {trade.bars_held}"
    assert trade.exit_reason == "TRAILING_STOP"
    assert res.dropped_trade_fraction == 0.0


def test_end_of_data_trade_closed_with_end_of_data():
    """Verify that an open trade reaching the end of candles is closed with END_OF_DATA."""
    import numpy as np
    from engine.exits import ExitConfig
    from engine.simulator import CandidateSignal, simulate_portfolio_signals

    t0 = 1700000000000
    interval_ms = 3600 * 1000  # 1h
    n_bars = 200

    # Steadily rising series that never retraces, so trailing stop is never hit
    bars = []
    for i in range(n_bars):
        t = t0 + i * interval_ms
        p = 100.0 + i * 0.2
        bars.append([p, p + 0.1, p - 0.05, p, t, 1000.0])

    arr = np.array(bars, dtype=np.float64)
    candles_dict = {"ETHUSDT": arr}

    sig = CandidateSignal(
        symbol="ETHUSDT",
        side="LONG",
        entry_price=100.0,
        stop_loss=90.0,
        timestamp_ms=t0,
        volume=1000.0,
    )

    exit_cfg = ExitConfig(exit_mode="trailing_1r", trail_dist_r=1.0)
    res = simulate_portfolio_signals(
        candidates=[sig],
        candles_dict=candles_dict,
        exit_config=exit_cfg,
        bar_interval_ms=interval_ms,
    )

    assert res.total_trades == 1
    trade = res.trades[0]
    assert trade.exit_reason == "END_OF_DATA"
    assert res.dropped_trade_fraction == 0.0


def test_dropped_trade_fraction_raises_error_above_threshold():
    """Verify simulate_portfolio_signals raises ValueError when dropped_trade_fraction > 0.5%."""
    import numpy as np
    import pytest
    from engine.simulator import CandidateSignal, simulate_portfolio_signals

    t0 = 1700000000000
    interval_ms = 3600 * 1000

    bars = [
        [100.0, 102.0, 99.0, 101.0, t0, 1000.0],
        [101.0, 103.0, 100.0, 102.0, t0 + interval_ms, 1000.0],
    ]
    arr = np.array(bars, dtype=np.float64)
    candles_dict = {"SOLUSDT": arr}

    # Signal placed at bar index 2 (beyond array length), causing forward_candles to be empty
    sig = CandidateSignal(
        symbol="SOLUSDT",
        side="LONG",
        entry_price=101.0,
        stop_loss=95.0,
        timestamp_ms=t0 + 2 * interval_ms,
        volume=1000.0,
    )

    bar_index_map = {"SOLUSDT": {t0 + 2 * interval_ms: len(arr)}}

    with pytest.raises(ValueError, match="dropped trade fraction"):
        simulate_portfolio_signals(
            candidates=[sig],
            candles_dict=candles_dict,
            bar_index_map=bar_index_map,
            bar_interval_ms=interval_ms,
        )


def test_dynamic_funding_rate_accrual_long_and_short():
    """Verify SimulationEngine accurately applies dynamic funding rates to LONG and SHORT positions."""
    import pandas as pd
    from domain.signals import CandidateSignal
    from engine.simulator import SimulationEngine, SimulationFriction

    t0 = 1700000000000
    p0 = t0 // (8 * 3600 * 1000)
    p1 = p0 + 1
    p2 = p0 + 2

    # Dynamic funding rate records: period 1 has +0.03% (0.0003), period 2 has -0.02% (-0.0002)
    funding_df = pd.DataFrame([
        {"calc_time": p1 * 8 * 3600 * 1000, "last_funding_rate": 0.0003},
        {"calc_time": p2 * 8 * 3600 * 1000, "last_funding_rate": -0.0002},
    ])

    # 1. LONG Position
    engine_long = SimulationEngine(
        initial_equity_usd=10000.0,
        friction=SimulationFriction(taker_fee_pct=0.0, slippage_pct=0.0),
        funding_rates={"BTCUSDT": funding_df},
    )
    sig_long = CandidateSignal(symbol="BTCUSDT", side="LONG", entry_price=100.0, stop_loss=95.0, timestamp_ms=t0)
    trade_id_long = engine_long.open_position(sig_long, current_bar_idx=0)
    assert trade_id_long is not None

    # Cross into period 1 (+0.03%): Long pays 0.03% of notional ($2000 * 0.0003 = 0.60 USD drag)
    t_p1 = p1 * 8 * 3600 * 1000 + 1000
    engine_long.process_bar("BTCUSDT", (100.0, 101.0, 99.0, 100.0), t_p1, current_bar_idx=1)
    pos_long = engine_long.active_positions[trade_id_long]
    assert round(pos_long["funding_cost_usd"], 2) == 0.60
    assert round(engine_long.equity_usd, 2) == 9999.40

    # Cross into period 2 (-0.02%): Long receives 0.02% rebate (-$0.40 drag) -> net funding = +$0.20
    t_p2 = p2 * 8 * 3600 * 1000 + 1000
    engine_long.process_bar("BTCUSDT", (100.0, 101.0, 99.0, 100.0), t_p2, current_bar_idx=2)
    assert round(pos_long["funding_cost_usd"], 2) == 0.20
    assert round(engine_long.equity_usd, 2) == 9999.80

    # 2. SHORT Position
    engine_short = SimulationEngine(
        initial_equity_usd=10000.0,
        friction=SimulationFriction(taker_fee_pct=0.0, slippage_pct=0.0),
        funding_rates={"BTCUSDT": funding_df},
    )
    sig_short = CandidateSignal(symbol="BTCUSDT", side="SHORT", entry_price=100.0, stop_loss=105.0, timestamp_ms=t0)
    trade_id_short = engine_short.open_position(sig_short, current_bar_idx=0)
    assert trade_id_short is not None

    # Cross into period 1 (+0.03%): Short receives 0.03% rebate (-$0.60 drag)
    engine_short.process_bar("BTCUSDT", (100.0, 101.0, 99.0, 100.0), t_p1, current_bar_idx=1)
    pos_short = engine_short.active_positions[trade_id_short]
    assert round(pos_short["funding_cost_usd"], 2) == -0.60
    assert round(engine_short.equity_usd, 2) == 10000.60

    # Cross into period 2 (-0.02%): Short pays 0.02% drag (+$0.40 drag) -> net funding = -$0.20
    engine_short.process_bar("BTCUSDT", (100.0, 101.0, 99.0, 100.0), t_p2, current_bar_idx=2)
    assert round(pos_short["funding_cost_usd"], 2) == -0.20
    assert round(engine_short.equity_usd, 2) == 10000.20


def test_dynamic_funding_rate_fallback_to_constant():
    """Verify SimulationEngine falls back to constant funding_rate_8h_pct when dynamic rates are missing."""
    from domain.signals import CandidateSignal
    from engine.simulator import SimulationEngine, SimulationFriction

    t0 = 1700000000000
    p0 = t0 // (8 * 3600 * 1000)
    p1 = p0 + 1

    # Empty funding rates dict for BTCUSDT
    engine = SimulationEngine(
        initial_equity_usd=10000.0,
        friction=SimulationFriction(funding_rate_8h_pct=0.01),
        funding_rates={"BTCUSDT": {}},
    )
    sig = CandidateSignal(symbol="BTCUSDT", side="LONG", entry_price=100.0, stop_loss=95.0, timestamp_ms=t0)
    trade_id = engine.open_position(sig, current_bar_idx=0)
    assert trade_id is not None

    # Cross into period 1: fallback constant 0.01% * $2000 = $0.20 USD
    t_p1 = p1 * 8 * 3600 * 1000 + 1000
    engine.process_bar("BTCUSDT", (100.0, 101.0, 99.0, 100.0), t_p1, current_bar_idx=1)
    pos = engine.active_positions[trade_id]
    assert round(pos["funding_cost_usd"], 2) == 0.20


def test_load_funding_rates_parquet_roundtrip(tmp_path):
    """Verify data.loader.load_funding_rates loads parquet archives and caches results."""
    import pandas as pd
    from data.loader import clear_funding_cache, load_funding_rates

    funding_dir = tmp_path / "funding"
    funding_dir.mkdir(parents=True)

    df_sample = pd.DataFrame([
        {
            "calc_time": 1700000000000,
            "datetime": "2023-11-14 22:13:20",
            "funding_interval_hours": 8,
            "last_funding_rate": 0.00015,
            "symbol": "BTCUSDT",
        }
    ])
    parquet_path = funding_dir / "BTCUSDT.parquet"
    df_sample.to_parquet(parquet_path)

    # 1. Successful load
    clear_funding_cache()
    loaded = load_funding_rates("BTCUSDT", data_dir=str(funding_dir))
    assert loaded is not None
    assert len(loaded) == 1
    assert loaded["last_funding_rate"].iloc[0] == 0.00015

    # 2. Cached retrieval
    cached = load_funding_rates("BTCUSDT", data_dir=str(funding_dir))
    assert cached is not None
    assert len(cached) == 1

    # 3. Missing symbol returns None
    missing = load_funding_rates("NONEXISTENT", data_dir=str(funding_dir))
    assert missing is None


def test_days_evaluated_narrow_exception_and_validation():
    """Verify days_evaluated computation in simulate_portfolio_signals raises on malformed candle data."""
    # 1. Malformed candle array missing timestamp column (only 3 columns) raises ValueError
    malformed_candles = {"TESTUSDT": np.array([[100.0, 105.0, 95.0]])}
    with pytest.raises(ValueError, match="candle arrays in candles_dict must contain valid timestamps"):
        simulate_portfolio_signals(
            candidates=[],
            candles_dict=malformed_candles,
        )

    # 2. Empty arrays in candles_dict does not raise and leaves days_evaluated == 0.0
    empty_candles = {"TESTUSDT": np.empty((0, 6))}
    res_empty = simulate_portfolio_signals(
        candidates=[],
        candles_dict=empty_candles,
    )
    assert res_empty.days_evaluated == 0.0

    # 3. Valid candle array calculates positive days_evaluated
    t0 = 1700000000000
    t1 = t0 + 10 * 86400 * 1000  # 10 days
    valid_candles = {
        "TESTUSDT": np.array([
            [100.0, 105.0, 95.0, 102.0, t0, 1000.0],
            [102.0, 106.0, 98.0, 104.0, t1, 1000.0],
        ])
    }
    res_valid = simulate_portfolio_signals(
        candidates=[],
        candles_dict=valid_candles,
    )
    assert pytest.approx(res_valid.days_evaluated, rel=1e-2) == 10.0


def test_funding_fallback_period_counter():
    """Verify simulation tracks total and fallback funding periods correctly."""
    interval_ms = 3600 * 1000  # 1h bars
    t0 = 1700000000000  # aligned timestamp
    # 24 hours of bars (crosses 3 8-hour funding intervals: 8h, 16h, 24h)
    bars = []
    for i in range(25):
        t = t0 + i * interval_ms
        bars.append([100.0, 105.0, 95.0, 100.0, t, 1000.0])
    candles = {"BTCUSDT": np.array(bars, dtype=np.float64)}
    bar_map = {"BTCUSDT": {int(bars[i][4]): i for i in range(len(bars))}}

    # Single trade entered at bar 0, held until bar 24 (crosses 3 8h periods)
    sig = CandidateSignal(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=100.0,
        stop_loss=90.0,
        timestamp_ms=t0,
    )

    # 1. No funding_rates map provided -> all periods use fallback
    res_fallback = simulate_portfolio_signals(
        candidates=[sig],
        candles_dict=candles,
        bar_index_map=bar_map,
    )
    assert res_fallback.funding_total_periods == 3
    assert res_fallback.funding_fallback_periods == 3

    # 2. Dynamic funding_rates map provided with 1 period defined and 2 missing
    p0 = t0 // (8 * 3600 * 1000)
    # Define funding rate only for second period (p0 + 2)
    dynamic_rates = {
        "BTCUSDT": {
            p0 + 2: 0.0002,
        }
    }
    res_mixed = simulate_portfolio_signals(
        candidates=[sig],
        candles_dict=candles,
        bar_index_map=bar_map,
        funding_rates=dynamic_rates,
    )
    assert res_mixed.funding_total_periods == 3
    assert res_mixed.funding_fallback_periods == 2  # 1 dynamic, 2 fallbacks







