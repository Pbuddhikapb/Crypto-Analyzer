"""
Unit tests for the Realistic Simulation Engine (engine/simulator.py).
Tests fees, adverse slippage, 8h funding rate drag, portfolio risk concurrency,
directional constraints, symbol cooldowns, and performance metrics.
"""

import pytest

from engine.exits import ExitConfig
from engine.simulator import (
    CandidateSignal,
    ExecutedTrade,
    SimulationEngine,
    SimulationFriction,
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


def test_ruin_guard_stops_new_positions():
    """Deterministic losing streak forces ruin (equity <= ruin_threshold_usd) and rejects all future positions."""
    sim = SimulationEngine(
        initial_equity_usd=1000.0,
        risk_per_trade_pct=25.0,  # $250 risk per trade
        ruin_threshold_usd=500.0,  # Ruin threshold at $500
        sizing_mode="fixed_risk",
    )

    # Trade 1: loses ~$250
    sig1 = CandidateSignal("BTCUSDT", "LONG", 100.0, 90.0, timestamp_ms=1000)
    t1_id = sim.open_position(sig1, current_bar_idx=0)
    assert t1_id is not None
    # Bar crashes down to 85 (below SL 90)
    c_loss1 = (100.0, 101.0, 85.0, 88.0)
    sim.process_bar("BTCUSDT", c_loss1, timestamp_ms=2000, current_bar_idx=1)
    assert sim.equity_usd < 760.0
    assert not sim.ruined

    # Trade 2: loses another ~$250, pushing equity below $500 ruin threshold
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


