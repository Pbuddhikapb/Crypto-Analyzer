"""
Unit tests for the Monte Carlo Random-Entry Baseline (research/random_baseline.py).
Tests distribution percentile calculation, single-trade simulation fidelity,
seed reproducibility, and report generation.
"""

import os
import tempfile
import numpy as np
import pytest

from engine.exits import ExitConfig
from engine.simulator import ExecutedTrade, SimulationFriction
from research.evaluator import BaselineHurdles
from research.random_baseline import (
    compute_bootstrap_ci,
    compute_metric_distribution,
    run_matched_baseline,
    run_monte_carlo_baseline,
    run_random_baseline_iteration,
    simulate_single_trade_lifecycle,
)


def test_compute_metric_distribution():
    data = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    dist = compute_metric_distribution("TestMetric", data)

    assert dist.name == "TestMetric"
    assert dist.min_val == 10.0
    assert dist.max_val == 100.0
    # Strict monotonicity of percentiles
    assert dist.p5 <= dist.p25 <= dist.p50 <= dist.p75 <= dist.p95
    assert dist.p50 == pytest.approx(55.0, abs=5.0)


def test_simulate_single_trade_lifecycle():
    # Long trade with entry at 100, SL at 90
    friction = SimulationFriction(taker_fee_pct=0.05, slippage_pct=0.03)
    exit_config = ExitConfig()

    # Candles forward:
    # Bar 1 hits TP1 (107.5)
    # Bar 2 hits TP2 (113.3)
    candles = [
        (100.0, 110.0, 99.0, 108.0, 1000),
        (108.0, 115.0, 107.0, 114.0, 2000),
    ]

    trade = simulate_single_trade_lifecycle(
        symbol="BTCUSDT",
        side="LONG",
        nominal_entry=100.0,
        stop_loss=90.0,
        entry_ts_ms=1000,
        candles_forward=candles,
        equity_at_entry=10000.0,
        risk_per_trade_pct=1.0,
        max_leverage=3.0,
        friction=friction,
        exit_config=exit_config,
    )

    assert trade is not None
    assert trade.symbol == "BTCUSDT"
    assert trade.side == "LONG"
    assert trade.exit_reason == "TAKE_PROFIT_2"
    assert trade.net_pnl_usd > 0
    assert trade.fees_usd > 0
    assert trade.slippage_usd > 0
    # Net PnL matches gross PnL minus fees and funding exactly (slippage is not double deducted)
    assert trade.net_pnl_usd == round(trade.gross_pnl_usd - trade.fees_usd - trade.funding_cost_usd, 2)


def test_simulate_trade_funding_after_tp1():
    friction = SimulationFriction(taker_fee_pct=0.05, slippage_pct=0.03, funding_rate_8h_pct=0.01)
    exit_config = ExitConfig()

    # Candles forward:
    # Bar 1 at 4h hits TP1 (107.5)
    # Bar 2 at 18h (crosses 8h and 16h boundaries) hits TP2 (113.3)
    ms_4h = 4 * 3600 * 1000
    ms_18h = 18 * 3600 * 1000
    candles = [
        (100.0, 110.0, 99.0, 108.0, ms_4h),
        (108.0, 115.0, 107.0, 114.0, ms_18h),
    ]

    trade = simulate_single_trade_lifecycle(
        symbol="ETHUSDT",
        side="LONG",
        nominal_entry=100.0,
        stop_loss=90.0,
        entry_ts_ms=0,
        candles_forward=candles,
        equity_at_entry=10000.0,
        risk_per_trade_pct=1.0,
        max_leverage=3.0,
        friction=friction,
        exit_config=exit_config,
    )

    assert trade is not None
    # Entry notional = 1000 USD (risk=100, dist=10 -> shares=10 -> notional=1000).
    # At t=4h, TP1 hit -> size halved to 500 USD.
    # Funding boundaries at 8h and 16h (2 periods).
    # Both periods occurred post-TP1, so funding cost = 2 * 0.01% * 500 = 0.10 USD.
    assert trade.funding_cost_usd == 0.10
    assert trade.net_pnl_usd == round(trade.gross_pnl_usd - trade.fees_usd - trade.funding_cost_usd, 2)


def test_seed_reproducibility():
    # Generate dummy candle array for 1 symbol: 200 bars
    bars = []
    base_price = 100.0
    for i in range(200):
        o = base_price + np.sin(i * 0.1) * 5.0
        h = o + 2.0
        l = o - 2.0
        c = o + 0.5
        ts = 1700000000000 + i * 3600000
        bars.append([o, h, l, c, ts])
    arr = np.array(bars, dtype=np.float64)
    candles_dict = {"BTCUSDT": arr}

    # Run twice with the exact same seed
    res1 = run_random_baseline_iteration(
        iteration_id=0,
        candles_dict=candles_dict,
        trades_per_symbol=20,
        rng=np.random.default_rng(12345),
    )
    res2 = run_random_baseline_iteration(
        iteration_id=0,
        candles_dict=candles_dict,
        trades_per_symbol=20,
        rng=np.random.default_rng(12345),
    )

    assert res1 == res2
    assert res1["total_trades"] > 0


def test_small_monte_carlo_run():
    # Smoke test running N=5 iterations on existing Parquet candle data
    with tempfile.NamedTemporaryFile(suffix=".md") as tmp:
        report = run_monte_carlo_baseline(
            iterations=5,
            symbols=["BTCUSDT", "ETHUSDT"],
            timeframe="1h",
            output_report_path=tmp.name,
            seed=42,
        )
        assert report is not None
        assert report.distributions.iterations == 5
        assert report.distributions.win_rate.p5 <= report.distributions.win_rate.p95
        assert "Alpha Hurdle" in report.markdown_table


def _make_dummy_candles(n: int = 200, base: float = 100.0) -> np.ndarray:
    bars = []
    for i in range(n):
        o = base + np.sin(i * 0.1) * 5.0
        h = o + 2.0
        l = o - 2.0
        c = o + 0.5
        ts = 1700000000000 + i * 3600000
        bars.append([o, h, l, c, ts])
    return np.array(bars, dtype=np.float64)


def _make_mock_trade(symbol: str, net_r: float, risk_usd: float = 100.0, notional: float = 5000.0) -> ExecutedTrade:
    return ExecutedTrade(
        trade_id=f"{symbol}_mock",
        symbol=symbol,
        side="LONG",
        entry_time_ms=1700000000000,
        exit_time_ms=1700000000000 + 3600000,
        entry_price=100.0,
        entry_fill_price=100.03,
        exit_price=102.0,
        exit_fill_price=101.97,
        exit_reason="TAKE_PROFIT_1",
        position_size_usd=notional,
        gross_pnl_usd=100.0,
        fees_usd=4.0,
        slippage_usd=3.0,
        funding_cost_usd=0.0,
        net_pnl_usd=93.0,
        net_return_pct=1.86,
        realized_r=1.0,
        bars_held=5,
        mfe_r=1.0,
        mae_r=0.0,
        initial_risk_usd=risk_usd,
        net_r=net_r,
    )


def test_matched_baseline_seeded_determinism():
    """Assert running matched baseline twice with same seed produces identical distributions."""
    candles = {
        "BTCUSDT": _make_dummy_candles(200, 100.0),
        "ETHUSDT": _make_dummy_candles(200, 50.0),
    }
    trades = [
        _make_mock_trade("BTCUSDT", net_r=-0.5, risk_usd=100.0, notional=5000.0),
        _make_mock_trade("BTCUSDT", net_r=1.2, risk_usd=100.0, notional=4000.0),
        _make_mock_trade("ETHUSDT", net_r=-1.1, risk_usd=100.0, notional=5000.0),
        _make_mock_trade("ETHUSDT", net_r=0.8, risk_usd=100.0, notional=2500.0),
    ]

    res1 = run_matched_baseline(trades, candles, iterations=15, seed=123)
    res2 = run_matched_baseline(trades, candles, iterations=15, seed=123)

    assert res1.null_expectancy_r_distribution == res2.null_expectancy_r_distribution
    assert res1.empirical_p_value == res2.empirical_p_value
    assert res1.bootstrap_ci == res2.bootstrap_ci
    assert res1.median_expectancy_r == res2.median_expectancy_r
    assert res1.mean_expectancy_r == res2.mean_expectancy_r
    assert res1.strategy_trade_count == len(trades)


def test_matched_baseline_trade_count():
    """Assert matched baseline preserves exact trade count and symbol mix of the strategy."""
    candles = {
        "BTCUSDT": _make_dummy_candles(200, 100.0),
        "ETHUSDT": _make_dummy_candles(200, 50.0),
    }
    # 7 BTC trades and 3 ETH trades = 10 total
    trades = [
        _make_mock_trade("BTCUSDT", net_r=0.1 * i, risk_usd=100.0, notional=5000.0)
        for i in range(7)
    ] + [
        _make_mock_trade("ETHUSDT", net_r=-0.1 * i, risk_usd=100.0, notional=3000.0)
        for i in range(3)
    ]

    res = run_matched_baseline(trades, candles, iterations=5, seed=777)
    assert res.strategy_trade_count == 10
    assert len(res.null_expectancy_r_distribution) == 5
    for val in res.null_expectancy_r_distribution:
        assert isinstance(val, float)
        assert np.isfinite(val)


def test_hurdle_loader_fails_loudly_on_mismatched_config():
    """Assert BaselineHurdles.load fails loudly on missing file or config mismatch."""
    # 1. Missing file -> FileNotFoundError
    with pytest.raises(FileNotFoundError, match="Baseline distribution file missing"):
        BaselineHurdles.load("nonexistent_distribution_path_9999.json")

    # 2. Existing file -> verify valid load first
    json_path = "research/reports/baseline_distribution.json"
    if os.path.exists(json_path):
        hurdles = BaselineHurdles.load(json_path)
        assert hurdles.pf_hurdle_p95 > 0.0
        assert hurdles.wr_hurdle_p95 > 0.0

        # Sizing mode mismatch -> ValueError
        with pytest.raises(ValueError, match="sizing_mode mismatch"):
            BaselineHurdles.load(json_path, expected_sizing_mode="compounding_unknown_mode")

        # Dataset hash mismatch -> ValueError
        with pytest.raises(ValueError, match="dataset mismatch"):
            BaselineHurdles.load(json_path, expected_dataset_hash="invalid_deadbeef_hash")

        # Friction mismatch -> ValueError
        with pytest.raises(ValueError, match="friction mismatch"):
            BaselineHurdles.load(json_path, expected_friction={"taker_fee_pct": 0.9999})


def test_bootstrap_ci_coverage():
    """Assert compute_bootstrap_ci behavior and seed determinism."""
    vals = [0.1, 0.2, 0.15, 0.3, 0.25, 0.18, 0.22]
    ci1 = compute_bootstrap_ci(vals, n_resamples=500, seed=42)
    ci2 = compute_bootstrap_ci(vals, n_resamples=500, seed=42)

    assert ci1 == ci2
    # All values positive -> 95% CI should be strictly positive
    assert ci1[0] > 0.0
    assert ci1[1] > ci1[0]

    # Empty values -> (0.0, 0.0)
    assert compute_bootstrap_ci([]) == (0.0, 0.0)


def test_matched_baseline_trending_series_zero_dropped():
    """Rule 5.1: In a synthetic trending series where trades last > 100 bars, matched baseline drops 0 trades."""
    n_bars = 500
    times = [1609459200000 + i * 3600000 for i in range(n_bars)]
    prices = [100.0 + i * 1.0 for i in range(n_bars)]
    candles = np.column_stack([
        prices,
        [p + 0.5 for p in prices],
        [p - 0.2 for p in prices],
        prices,
        times,
    ])
    candles_dict = {"BTCUSDT": candles}

    trade = _make_mock_trade("BTCUSDT", net_r=1.0, risk_usd=100.0, notional=5000.0)
    cfg = ExitConfig(exit_mode="trailing_1r", trail_dist_r=1.0)

    res = run_matched_baseline(
        strategy_trades=[trade] * 10,
        candles_dict=candles_dict,
        iterations=5,
        exit_config=cfg,
        seed=42,
    )
    assert res.dropped_trade_fraction == 0.0
    assert res.strategy_trade_count == 10


def test_matched_baseline_driftless_random_walk_median_near_zero():
    """Rule 5.2: Matched median for a driftless random-walk series must be within +/-0.05R of zero."""
    rng = np.random.default_rng(42)
    n_bars = 10000
    steps = rng.choice([-0.1, 0.1], size=n_bars)
    prices = 100.0 + np.cumsum(steps)
    prices = np.clip(prices, 50.0, 200.0)
    times = [1609459200000 + i * 3600000 for i in range(n_bars)]
    highs = prices + 0.1
    lows = prices - 0.1

    candles = np.column_stack([
        prices,
        highs,
        lows,
        prices,
        times,
    ])
    candles_dict = {"RW_COIN": candles}
    trade = _make_mock_trade("RW_COIN", net_r=0.0, risk_usd=100.0, notional=5000.0)
    cfg = ExitConfig(exit_mode="trailing_1r", trail_dist_r=1.0)
    fric = SimulationFriction(taker_fee_pct=0.0, maker_fee_pct=0.0, slippage_pct=0.0, funding_rate_8h_pct=0.0)

    res = run_matched_baseline(
        strategy_trades=[trade] * 100,
        candles_dict=candles_dict,
        iterations=50,
        exit_config=cfg,
        friction=fric,
        seed=42,
    )
    assert -0.05 <= res.median_expectancy_r <= 0.05, (
        f"Expected median EV in [-0.05, +0.05]R, got {res.median_expectancy_r:+.4f}R"
    )
    assert res.dropped_trade_fraction <= 0.005


def test_guardrail_p_value_at_floor_warning():
    """Task 4: If rank p-value equals floor 1/(N+1), print warning asking for null-sanity check."""
    from research.evaluator import StrategyEvaluator
    from research.random_baseline import MatchedBaselineResult
    from research.strategy_runner import StrategyRunResult, generate_markdown_report
    from engine.simulator import SimulationResult

    evaluator = StrategyEvaluator()
    sim_res = SimulationResult(
        initial_equity_usd=10000.0,
        final_equity_usd=15000.0,
        net_profit_usd=5000.0,
        net_profit_pct=50.0,
        total_trades=150,
        winning_trades=100,
        losing_trades=50,
        win_rate_pct=66.7,
        gross_profit_usd=6000.0,
        gross_loss_usd=1000.0,
        profit_factor=6.0,
        expectancy_usd=33.3,
        expectancy_r=1.5,
        avg_r_multiple=1.5,
        max_drawdown_pct=5.0,
        max_drawdown_duration_bars=10,
        sharpe_ratio=2.5,
        sortino_ratio=3.0,
        total_fees_usd=50.0,
        total_slippage_usd=30.0,
        total_funding_cost_usd=10.0,
        trades=[],
        ruined=False,
        sizing_mode="fixed_risk",
        gross_profit_factor=6.5,
        gross_expectancy_r=1.6,
        cumulative_r=150.0,
        max_drawdown_r=5.0,
        days_evaluated=100.0,
        pct_of_dataset_evaluated=100.0,
    )

    matched = MatchedBaselineResult(
        iterations=1000,
        strategy_trade_count=150,
        strategy_net_ev_r=1.5,
        null_expectancy_r_distribution=[-0.05] * 1000,
        median_expectancy_r=-0.05,
        mean_expectancy_r=-0.05,
        std_expectancy_r=0.02,
        p5_expectancy_r=-0.08,
        p95_expectancy_r=-0.01,
        empirical_p_value=0.0010,
        bootstrap_ci=(1.2, 1.8),
        exit_mode="trailing_1r",
        dropped_trade_fraction=0.0,
    )

    ev = evaluator.evaluate("TestStrat", "Test Description", sim_res, matched_baseline=matched, bootstrap_ci=(1.2, 1.8))
    assert ev.p_value_at_floor is True
    assert any("Null-sanity check required" in d for d in ev.diagnostics)

    run_res = StrategyRunResult(
        strategy_name="TestStrat",
        description="Test Description",
        sim_result=sim_res,
        evaluation=ev,
        per_symbol_metrics={"BTCUSDT": {"trades": 150, "win_rate": 66.7, "profit_factor": 6.0, "net_pnl": 5000.0, "avg_r": 1.5}},
        total_signals_generated=200,
        signals_executed=150,
        bootstrap_ci=(1.2, 1.8),
        matched_baseline=matched,
    )

    with tempfile.NamedTemporaryFile(suffix=".md", mode="w+", delete=False) as tf:
        out_path = tf.name

    generate_markdown_report([run_res], out_path, dataset_info={"symbols": ["BTCUSDT"], "days": 100.0, "bars_per_symbol": 2400})
    with open(out_path, "r", encoding="utf-8") as f:
        rep = f.read()

    assert "Null-sanity check required" in rep
    assert "equals minimum floor 1/(N+1)" in rep

