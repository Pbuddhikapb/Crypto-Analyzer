"""
Comprehensive Unit Test Suite for Phase 3 Strategies and Statistical Evaluator.
"""

import numpy as np
import pandas as pd
import pytest

from engine.exits import ExitConfig
from engine.simulator import CandidateSignal, SimulationFriction
from research.evaluator import BaselineHurdles, StrategyEvaluation, StrategyEvaluator, compute_p_value
from research.strategies import (
    ConfluenceThresholdStrategy,
    FVGRetestStrategy,
    LiquiditySweepStrategy,
    TrendPullbackStrategy,
)
from research.strategy_runner import compute_per_symbol_breakdown, simulate_portfolio_signals


def make_synthetic_candles(n: int = 300, base_price: float = 100.0, trend: float = 0.05) -> pd.DataFrame:
    """Generate deterministic synthetic candles with open_time, open, high, low, close, volume."""
    rng = np.random.default_rng(42)
    rows = []
    price = base_price
    t_start = 1700000000000  # ms timestamp

    for i in range(n):
        o = price
        noise = rng.normal(0, 0.5)
        c = o + trend + noise
        h = max(o, c) + abs(rng.normal(0, 0.4))
        l = min(o, c) - abs(rng.normal(0, 0.4))
        v = 1000.0 + abs(rng.normal(0, 200))
        t = t_start + i * 3600000  # 1h interval
        rows.append({"open_time": t, "open": o, "high": h, "low": l, "close": c, "volume": v})
        price = c

    return pd.DataFrame(rows)


def make_synthetic_4h_candles(n: int = 100, base_price: float = 100.0, trend: float = 0.2) -> pd.DataFrame:
    """Generate 4h candles."""
    rng = np.random.default_rng(42)
    rows = []
    price = base_price
    t_start = 1700000000000

    for i in range(n):
        o = price
        c = o + trend + rng.normal(0, 0.8)
        h = max(o, c) + abs(rng.normal(0, 0.6))
        l = min(o, c) - abs(rng.normal(0, 0.6))
        v = 4000.0 + abs(rng.normal(0, 500))
        t = t_start + i * 14400000  # 4h interval
        rows.append({"open_time": t, "open": o, "high": h, "low": l, "close": c, "volume": v})
        price = c

    return pd.DataFrame(rows)


class TestZeroLookaheadContract:
    """Verify that strategies strictly timestamp signals for bar i+1 open."""

    def test_trend_pullback_zero_lookahead(self):
        df_1h = make_synthetic_candles(n=400, trend=0.1)
        df_4h = make_synthetic_4h_candles(n=100, trend=0.4)
        strat = TrendPullbackStrategy()
        signals = strat.generate_signals("BTCUSDT", df_1h, df_4h)

        for sig in signals:
            bar_idx = sig.metadata["bar_idx"]
            expected_open = float(df_1h.iloc[bar_idx]["open"])
            expected_time = int(df_1h.iloc[bar_idx]["open_time"])
            assert sig.entry_price == expected_open, "Signal entry price must match next bar open"
            assert sig.timestamp_ms == expected_time, "Signal timestamp must match next bar open_time"
            assert sig.stop_loss < sig.entry_price if sig.side == "LONG" else sig.stop_loss > sig.entry_price

    def test_liquidity_sweep_zero_lookahead(self):
        df_1h = make_synthetic_candles(n=400, trend=0.0)
        df_4h = make_synthetic_4h_candles(n=100, trend=0.0)
        strat = LiquiditySweepStrategy()
        signals = strat.generate_signals("ETHUSDT", df_1h, df_4h)

        for sig in signals:
            bar_idx = sig.metadata["bar_idx"]
            expected_open = float(df_1h.iloc[bar_idx]["open"])
            expected_time = int(df_1h.iloc[bar_idx]["open_time"])
            assert sig.entry_price == expected_open
            assert sig.timestamp_ms == expected_time


class TestStrategySignalGeneration:
    """Verify signal generation logic and stop-loss bounds."""

    def test_trend_pullback_bounds(self):
        df_1h = make_synthetic_candles(n=400, trend=0.05)
        df_4h = make_synthetic_4h_candles(n=100, trend=0.2)
        strat = TrendPullbackStrategy()
        signals = strat.generate_signals("SOLUSDT", df_1h, df_4h)

        for s in signals:
            stop_pct = abs(s.entry_price - s.stop_loss) / s.entry_price * 100.0
            assert 0.79 <= stop_pct <= 4.01, f"Stop loss % {stop_pct:.2f}% out of bounds"

    def test_fvg_retest_bounds(self):
        df_1h = make_synthetic_candles(n=400, trend=0.1)
        df_4h = make_synthetic_4h_candles(n=100, trend=0.3)
        strat = FVGRetestStrategy()
        signals = strat.generate_signals("BTCUSDT", df_1h, df_4h)

        for s in signals:
            stop_pct = abs(s.entry_price - s.stop_loss) / s.entry_price * 100.0
            assert 0.79 <= stop_pct <= 4.01, f"FVG stop loss % {stop_pct:.2f}% out of bounds"

    def test_liquidity_sweep_structure(self):
        df_1h = make_synthetic_candles(n=400, trend=0.02)
        df_4h = make_synthetic_4h_candles(n=100, trend=0.1)
        strat = LiquiditySweepStrategy()
        signals = strat.generate_signals("ADAUSDT", df_1h, df_4h)

        for s in signals:
            assert s.metadata["strategy"] == "LiquiditySweep"
            assert "swept_level" in s.metadata
            assert "wick_ratio" in s.metadata


class TestStrategyEvaluator:
    """Verify statistical evaluator, p-value calculations, and decision verdicts."""

    def test_p_value_calculation(self):
        # A strategy matching null mean has p-value ~ 0.50
        p_mid = compute_p_value(0.93, null_mean=0.93, null_std=0.13, higher_is_better=True)
        assert pytest.approx(p_mid, abs=0.01) == 0.50

        # An exceptional Profit Factor of 1.30 (over 2.8 standard deviations)
        p_high = compute_p_value(1.30, null_mean=0.93, null_std=0.13, higher_is_better=True)
        assert p_high < 0.01

        # A terrible Profit Factor of 0.70 has high p-value
        p_low = compute_p_value(0.70, null_mean=0.93, null_std=0.13, higher_is_better=True)
        assert p_low > 0.90

    def test_evaluator_verdict_validated(self):
        evaluator = StrategyEvaluator()
        mock_result = {
            "total_trades": 120,
            "win_rate_pct": 54.0,
            "profit_factor": 1.25,
            "expectancy_r": 0.0850,
            "expectancy_usd": 12.50,
            "avg_r": 0.0850,
            "sharpe_ratio": 0.85,
            "sortino_ratio": 1.10,
            "max_drawdown_pct": 14.5,
            "total_return_pct": 22.0,
        }
        res = evaluator.evaluate("AlphaStrategy", "Validated Model", mock_result)
        assert res.is_validated is True
        assert res.verdict == "VALIDATED (p < 0.05)"
        assert res.p_composite < 0.05
        assert len(res.hurdles_passed) >= 4

    def test_evaluator_verdict_rejected(self):
        evaluator = StrategyEvaluator()
        mock_result = {
            "total_trades": 80,
            "win_rate_pct": 42.0,
            "profit_factor": 0.90,
            "expectancy_r": -0.0350,
            "expectancy_usd": -5.20,
            "avg_r": -0.0350,
            "sharpe_ratio": -1.20,
            "sortino_ratio": -1.50,
            "max_drawdown_pct": 28.5,
            "total_return_pct": -15.0,
        }
        res = evaluator.evaluate("RandomNoise", "Unproven Model", mock_result)
        assert res.is_validated is False
        assert res.verdict == "REJECTED (p >= 0.05)"
        assert res.p_composite >= 0.05


class TestPortfolioSimulatorSmoke:
    """Verify chronological simulation, friction, and portfolio constraints."""

    def test_simulation_concurrency_and_friction(self):
        df_1h = make_synthetic_candles(n=300, base_price=100.0)
        arr = df_1h[["open", "high", "low", "close", "open_time"]].to_numpy(dtype=np.float64)
        candles_dict = {"BTCUSDT": arr}
        bar_idx_map = {"BTCUSDT": {int(arr[i, 4]): i for i in range(len(arr))}}

        # Create 5 signals at spaced intervals
        candidates = [
            CandidateSignal(
                symbol="BTCUSDT",
                side="LONG",
                entry_price=float(arr[20, 0]),
                stop_loss=float(arr[20, 0] * 0.98),
                timestamp_ms=int(arr[20, 4]),
                score=80.0,
            ),
            CandidateSignal(
                symbol="BTCUSDT",
                side="LONG",
                entry_price=float(arr[60, 0]),
                stop_loss=float(arr[60, 0] * 0.98),
                timestamp_ms=int(arr[60, 4]),
                score=80.0,
            ),
            CandidateSignal(
                symbol="BTCUSDT",
                side="SHORT",
                entry_price=float(arr[120, 0]),
                stop_loss=float(arr[120, 0] * 1.02),
                timestamp_ms=int(arr[120, 4]),
                score=80.0,
            ),
        ]

        sim_res = simulate_portfolio_signals(
            candidates=candidates,
            candles_dict=candles_dict,
            bar_index_map=bar_idx_map,
            initial_equity_usd=10000.0,
        )

        assert sim_res.total_trades > 0
        assert sim_res.total_fees_usd > 0
        assert sim_res.total_slippage_usd > 0
        assert len(sim_res.equity_curve) >= 2
