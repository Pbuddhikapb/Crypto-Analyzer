"""
Unit tests for Hypothesis 1: 4h Donchian Breakout with ATR Trailing Exit.

Verifies:
1. No lookahead: truncating data after bar t leaves signals up to t unchanged.
2. Exact breakout condition (strictly > highest high, strictly < lowest low of previous N bars).
3. Trailing stop never moving backward (for both LONG and SHORT).
4. Exit-mode isolation (stepped vs trailing_1r).
5. Matched random baseline uses the SAME exit mode as the strategy under test.
6. Significance level option (0.025 for Donchian variants, 0.05 default).
7. Pre-registered diagnostics computation.
"""

import numpy as np
import pandas as pd
import pytest

from engine.exits import ExitConfig, create_trade_state, evaluate_trade_exit
from engine.simulator import ExecutedTrade, SimulationResult
from research.evaluator import StrategyEvaluator
from research.random_baseline import run_matched_baseline
from research.strategies.donchian_4h import (
    Donchian4hN20,
    Donchian4hN55,
)


def _make_candles(
    n_bars: int,
    start_ts: int = 1609459200000,
    interval_hours: int = 4,
    base_price: float = 100.0,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic OHLCV dataframe."""
    rng = np.random.default_rng(seed)
    open_times = [start_ts + i * interval_hours * 3600 * 1000 for i in range(n_bars)]
    prices = [base_price]
    for _ in range(n_bars - 1):
        delta = rng.normal(0, 1.0)
        prices.append(max(10.0, prices[-1] + delta))

    opens = prices
    highs = [p + abs(rng.normal(0.5, 0.5)) for p in prices]
    lows = [p - abs(rng.normal(0.5, 0.5)) for p in prices]
    closes = [rng.uniform(lows[i], highs[i]) for i in range(n_bars)]
    volumes = [1000.0] * n_bars

    return pd.DataFrame(
        {
            "open_time": open_times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


class TestDonchianNoLookahead:
    """Rule 7.1: Truncating data after bar t leaves signals up to t unchanged."""

    def test_no_lookahead_truncation(self):
        # 100 4h bars = 400 1h bars
        start_ts = 1609459200000
        df_4h_full = _make_candles(100, start_ts=start_ts, interval_hours=4, seed=42)
        df_1h_full = _make_candles(400, start_ts=start_ts, interval_hours=1, seed=43)

        strat = Donchian4hN20()
        signals_full = strat.generate_signals("BTCUSDT", df_1h_full, df_4h_full)
        assert len(signals_full) > 0, "Full dataset should generate signals"

        # Pick cutoff at bar 60 (4h) = 240 (1h)
        cutoff_4h_idx = 60
        cutoff_ts = int(df_4h_full["open_time"].iloc[cutoff_4h_idx] + 14400000)

        df_4h_trunc = df_4h_full.iloc[:cutoff_4h_idx].copy()
        cutoff_1h_idx = int((cutoff_ts - start_ts) // (3600 * 1000))
        df_1h_trunc = df_1h_full.iloc[:cutoff_1h_idx].copy()

        signals_trunc = strat.generate_signals("BTCUSDT", df_1h_trunc, df_4h_trunc)

        # Signals generated on full dataset up to cutoff_ts
        expected_sigs = [s for s in signals_full if s.timestamp_ms <= cutoff_ts]

        assert len(signals_trunc) == len(expected_sigs), (
            f"Signal count mismatch: truncated has {len(signals_trunc)}, expected {len(expected_sigs)}"
        )
        for s_t, s_e in zip(signals_trunc, expected_sigs):
            assert s_t.timestamp_ms == s_e.timestamp_ms
            assert s_t.side == s_e.side
            assert s_t.entry_price == pytest.approx(s_e.entry_price, rel=1e-5)
            assert s_t.stop_loss == pytest.approx(s_e.stop_loss, rel=1e-5)


class TestDonchianExactBreakoutCondition:
    """Rule 7.2: Verify strictly greater/less breakout logic and exclusion of current bar."""

    def test_exact_breakout_long_short_boundary(self):
        # Construct exactly 25 4h bars
        # Bars 0..19: highs are exactly 100.0, lows are exactly 80.0, closes are 90.0
        start_ts = 1609459200000
        open_times_4h = [start_ts + i * 14400000 for i in range(25)]
        highs_4h = [100.0] * 25
        lows_4h = [80.0] * 25
        closes_4h = [90.0] * 25
        opens_4h = [90.0] * 25

        # Bar 20: close == 100.0 (boundary condition: NOT > 100.0) -> Should NOT trigger LONG
        closes_4h[20] = 100.0

        # Bar 21: close == 100.01 (> 100.0) -> Should trigger LONG
        closes_4h[21] = 100.01
        highs_4h[21] = 105.0  # New high on bar 21

        # Bar 22: close == 80.0 (boundary condition: NOT < 80.0) -> Should NOT trigger SHORT
        closes_4h[22] = 80.0

        # Bar 23: close == 79.99 (< 80.0) -> Should trigger SHORT
        closes_4h[23] = 79.99

        df_4h = pd.DataFrame({
            "open_time": open_times_4h,
            "open": opens_4h,
            "high": highs_4h,
            "low": lows_4h,
            "close": closes_4h,
            "volume": [1000.0] * 25,
        })

        # Matching 1h bars: 25 * 4 = 100 bars
        open_times_1h = [start_ts + i * 3600000 for i in range(100)]
        df_1h = pd.DataFrame({
            "open_time": open_times_1h,
            "open": [90.0] * 100,
            "high": [95.0] * 100,
            "low": [85.0] * 100,
            "close": [90.0] * 100,
            "volume": [1000.0] * 100,
        })

        strat = Donchian4hN20()
        signals = strat.generate_signals("BTCUSDT", df_1h, df_4h)

        # Bar 20 (close == 100.0) should produce NO signal
        # Bar 21 (close == 100.01) should produce 1 LONG signal
        # Bar 22 (close == 80.0) should produce NO signal
        # Bar 23 (close == 79.99) should produce 1 SHORT signal
        assert len(signals) == 2, f"Expected exactly 2 signals, got {len(signals)}"

        # Signal 1: Long at bar 21 close -> enters at bar 22 open (ts = start_ts + 22 * 14400000)
        assert signals[0].side == "LONG"
        expected_ts_1 = start_ts + 22 * 14400000
        assert signals[0].timestamp_ms == expected_ts_1

        # Signal 2: Short at bar 23 close -> enters at bar 24 open (ts = start_ts + 24 * 14400000)
        assert signals[1].side == "SHORT"
        expected_ts_2 = start_ts + 24 * 14400000
        assert signals[1].timestamp_ms == expected_ts_2


class TestTrailingStopNeverMovesBackward:
    """Rule 7.3: Verify that trailing stop moves only in profit direction and never backwards."""

    def test_trailing_stop_ratchet_long(self):
        cfg = ExitConfig(exit_mode="trailing_1r", trail_dist_r=1.0)
        # Entry = 100.0, Stop = 70.0 -> Risk = 30.0 (1R)
        state = create_trade_state("T_LONG", "BTCUSDT", "LONG", 100.0, 70.0, config=cfg)
        assert state.current_sl == 70.0

        # Bar 1: Rallies to 130.0 (+1.0R). High = 130.0, Close = 125.0
        # Trail stop: 100 + (1.0 - 1.0) * 30 = 100.0
        dec1 = evaluate_trade_exit(state, (100.0, 130.0, 99.0, 125.0), config=cfg)
        assert dec1.action == "HOLD"
        assert state.current_sl == 100.0
        sl_after_bar1 = state.current_sl

        # Bar 2: Retraces! High = 105.0, Low = 101.0, Close = 102.0
        # Stop must NOT move backward (must stay 100.0)
        dec2 = evaluate_trade_exit(state, (125.0, 125.0, 101.0, 102.0), config=cfg)
        assert dec2.action == "HOLD"
        assert state.current_sl == sl_after_bar1, "Trailing stop moved backward on retracement!"

        # Bar 3: Pushes further to 160.0 (+2.0R). Trail SL = 100 + (2.0 - 1.0) * 30 = 130.0
        dec3 = evaluate_trade_exit(state, (102.0, 160.0, 102.0, 155.0), config=cfg)
        assert dec3.action == "HOLD"
        assert state.current_sl == 130.0

        # Bar 4: Retraces to 132.0. Stop must stay 130.0
        dec4 = evaluate_trade_exit(state, (155.0, 155.0, 132.0, 134.0), config=cfg)
        assert dec4.action == "HOLD"
        assert state.current_sl == 130.0

        # Bar 5: Pulls back through 130.0 to 128.0 -> Stopped out at 130.0!
        dec5 = evaluate_trade_exit(state, (134.0, 135.0, 128.0, 129.0), config=cfg)
        assert dec5.action == "FULL_EXIT"
        assert dec5.exit_reason == "TRAILING_STOP"
        assert dec5.exit_price == 130.0
        assert dec5.realized_r == 1.0

    def test_trailing_stop_ratchet_short(self):
        cfg = ExitConfig(exit_mode="trailing_1r", trail_dist_r=1.0)
        # Entry = 100.0, Stop = 130.0 -> Risk = 30.0 (1R)
        state = create_trade_state("T_SHORT", "BTCUSDT", "SHORT", 100.0, 130.0, config=cfg)
        assert state.current_sl == 130.0

        # Bar 1: Plunges to 70.0 (+1.0R). Trail stop = 100 - (1.0 - 1.0) * 30 = 100.0
        dec1 = evaluate_trade_exit(state, (100.0, 101.0, 70.0, 75.0), config=cfg)
        assert dec1.action == "HOLD"
        assert state.current_sl == 100.0

        # Bar 2: Bounces to 95.0. Stop must NOT move backward (must stay 100.0)
        dec2 = evaluate_trade_exit(state, (75.0, 95.0, 75.0, 92.0), config=cfg)
        assert dec2.action == "HOLD"
        assert state.current_sl == 100.0, "Short trailing stop moved backward on bounce!"

        # Bar 3: Drops to 40.0 (+2.0R). Trail stop = 100 - (2.0 - 1.0) * 30 = 70.0
        dec3 = evaluate_trade_exit(state, (92.0, 92.0, 40.0, 45.0), config=cfg)
        assert dec3.action == "HOLD"
        assert state.current_sl == 70.0

        # Bar 4: Bounces through 70.0 to 75.0 -> Stopped out at 70.0!
        dec4 = evaluate_trade_exit(state, (45.0, 75.0, 44.0, 72.0), config=cfg)
        assert dec4.action == "FULL_EXIT"
        assert dec4.exit_reason == "TRAILING_STOP"
        assert dec4.exit_price == 70.0
        assert dec4.realized_r == 1.0


class TestExitModeIsolation:
    """Rule 7.4: Verify that stepped mode and trailing_1r mode execute strictly isolated mechanics."""

    def test_mode_isolation(self):
        cfg_stepped = ExitConfig(exit_mode="stepped", tp1_r=0.75, tp2_r=1.33)
        cfg_trail = ExitConfig(exit_mode="trailing_1r", trail_dist_r=1.0)

        # Candle reaches +0.80R:
        # Stepped mode: MUST trigger PARTIAL_TP1 (50% scale-out)
        # Trailing 1R mode: MUST NOT trigger TP1 (must stay 100% position size)
        entry = 100.0
        sl = 90.0  # R = 10.0
        # Candle moves to 108.0 (+0.8R)
        candle = (100.0, 108.0, 99.0, 107.0)

        state_step = create_trade_state("S1", "BTCUSDT", "LONG", entry, sl, config=cfg_stepped)
        dec_step = evaluate_trade_exit(state_step, candle, config=cfg_stepped)
        assert dec_step.action == "PARTIAL_TP1"
        assert dec_step.closed_pct == 0.5
        assert state_step.size_remaining_pct == 0.5

        state_trail = create_trade_state("T1", "BTCUSDT", "LONG", entry, sl, config=cfg_trail)
        dec_trail = evaluate_trade_exit(state_trail, candle, config=cfg_trail)
        assert dec_trail.action == "HOLD"
        assert dec_trail.closed_pct == 0.0
        assert state_trail.size_remaining_pct == 1.0


class TestMatchedBaselineExitModeInheritance:
    """Rule 3: Verify that matched random baseline uses the SAME exit mode as the strategy."""

    def test_matched_baseline_uses_strategy_exit_mode(self):
        # Create synthetic executed trade
        t = ExecutedTrade(
            trade_id="T_1",
            symbol="BTCUSDT",
            side="LONG",
            entry_time_ms=1609459200000,
            exit_time_ms=1609459200000 + 3600000 * 5,
            entry_price=100.0,
            entry_fill_price=100.0,
            exit_price=110.0,
            exit_fill_price=110.0,
            exit_reason="TRAILING_STOP",
            position_size_usd=1000.0,
            gross_pnl_usd=100.0,
            fees_usd=1.0,
            slippage_usd=1.0,
            funding_cost_usd=0.0,
            net_pnl_usd=98.0,
            net_return_pct=9.8,
            realized_r=1.0,
            bars_held=5,
            mfe_r=2.0,
            mae_r=-0.2,
            initial_risk_usd=100.0,
            net_r=0.98,
        )

        df_1h = _make_candles(200, start_ts=1609459200000, interval_hours=1)
        arr = df_1h[["open", "high", "low", "close", "open_time"]].to_numpy(dtype=np.float64)
        candles_dict = {"BTCUSDT": arr}

        strat = Donchian4hN20()
        assert strat.exit_config.exit_mode == "trailing_1r"

        matched_res = run_matched_baseline(
            strategy_trades=[t],
            candles_dict=candles_dict,
            iterations=5,
            seed=42,
            exit_config=strat.exit_config,
        )

        assert matched_res.exit_mode == "trailing_1r", (
            f"Expected matched baseline to use 'trailing_1r', got '{matched_res.exit_mode}'"
        )


class TestSignificanceLevelAndDiagnostics:
    """Rules 4 & 5: Significance level option (0.025 for Donchian) and pre-registered diagnostics."""

    def test_significance_level_and_diagnostics_reporting(self):
        strat_n20 = Donchian4hN20()
        strat_n55 = Donchian4hN55()
        assert strat_n20.significance_level == 0.025
        assert strat_n55.significance_level == 0.025

        # Check evaluator verdict formatting with significance_level
        evaluator = StrategyEvaluator()

        t = ExecutedTrade(
            trade_id="T_1",
            symbol="BTCUSDT",
            side="LONG",
            entry_time_ms=1609459200000,
            exit_time_ms=1609459200000 + 3600000 * 10,
            entry_price=100.0,
            entry_fill_price=100.0,
            exit_price=110.0,
            exit_fill_price=110.0,
            exit_reason="TRAILING_STOP",
            position_size_usd=1000.0,
            gross_pnl_usd=100.0,
            fees_usd=1.0,
            slippage_usd=1.0,
            funding_cost_usd=0.0,
            net_pnl_usd=98.0,
            net_return_pct=9.8,
            realized_r=1.0,
            bars_held=10,
            mfe_r=2.0,
            mae_r=-0.2,
            initial_risk_usd=100.0,
            net_r=0.98,
        )

        sim_res = SimulationResult(
            initial_equity_usd=10000.0,
            final_equity_usd=10098.0,
            net_profit_usd=98.0,
            net_profit_pct=0.98,
            total_trades=1,
            winning_trades=1,
            losing_trades=0,
            win_rate_pct=100.0,
            gross_profit_usd=100.0,
            gross_loss_usd=0.0,
            profit_factor=10.0,
            expectancy_usd=98.0,
            expectancy_r=0.98,
            avg_r_multiple=0.98,
            max_drawdown_pct=0.0,
            max_drawdown_duration_bars=0,
            sharpe_ratio=2.0,
            sortino_ratio=2.0,
            total_fees_usd=1.0,
            total_slippage_usd=1.0,
            total_funding_cost_usd=0.0,
            trades=[t],
            ruined=False,
            sizing_mode="fixed_risk",
            gross_profit_factor=10.0,
            gross_expectancy_r=1.0,
            cumulative_r=0.98,
            max_drawdown_r=0.0,
            days_evaluated=10.0,
            pct_of_dataset_evaluated=100.0,
        )

        # Evaluated at alpha = 0.025
        ev = evaluator.evaluate(
            "Donchian4hN20",
            "H1a Test",
            sim_res,
            matched_baseline=None,
            bootstrap_ci=(0.5, 1.2),
            significance_level=0.025,
        )

        assert ev.significance_level == 0.025
        # Total trades < 100 so rejected
        assert "p >= 0.025" in ev.verdict

        # Verify diagnostics presence
        assert ev.med_stop_pct is not None
        assert ev.cost_drag_r is not None
        assert ev.med_holding_bars is not None
        assert ev.trades_per_symbol is not None
        assert ev.per_year_net_r is not None

        # Check diagnostics text lines
        diag_str = "\n".join(ev.diagnostics)
        assert "Median Stop % of Price:" in diag_str
        assert "Cost Drag in R per Trade:" in diag_str
        assert "Median Holding Time:" in diag_str
        assert "Trades per Symbol:" in diag_str
        assert "Per-Year Net R:" in diag_str
