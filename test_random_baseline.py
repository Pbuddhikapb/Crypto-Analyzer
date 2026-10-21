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

