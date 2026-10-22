"""
Comprehensive Unit Test Suite for Phase 3 Strategies and Statistical Evaluator.
"""

import numpy as np
import pandas as pd
import pytest

from engine.simulator import CandidateSignal, SimulationResult
from research.evaluator import StrategyEvaluator, compute_p_value
from research.strategies import (
    FVGRetestStrategy,
    LiquiditySweepStrategy,
    TrendPullbackStrategy,
)
from research.strategy_runner import simulate_portfolio_signals


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
            "bootstrap_ci": (0.02, 0.15),
            "matched_p_value": 0.01,
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

    def test_explicit_alpha_verdict_synthetic_positive_and_negative(self):
        """Synthetic strategy with true +0.1R edge is VALIDATED, and one with -0.1R is REJECTED."""
        evaluator = StrategyEvaluator()

        # Positive strategy (+0.10R edge, 150 trades, CI > 0, matched rank p < 0.05)
        pos_result = {
            "total_trades": 150,
            "win_rate_pct": 55.0,
            "profit_factor": 1.30,
            "expectancy_r": 0.1000,
            "expectancy_usd": 15.0,
            "avg_r": 0.1000,
            "sharpe_ratio": 1.2,
            "sortino_ratio": 1.5,
            "max_drawdown_pct": 10.0,
            "total_return_pct": 30.0,
            "bootstrap_ci": (0.035, 0.165),
            "matched_p_value": 0.005,
        }
        res_pos = evaluator.evaluate("SyntheticEdge", "True +0.1R Edge", pos_result)
        assert res_pos.is_validated is True
        assert res_pos.verdict == "VALIDATED (p < 0.05)"
        assert res_pos.expectancy_r == 0.1000
        assert res_pos.bootstrap_ci[0] > 0.0
        assert res_pos.matched_p_value < 0.05

        # Negative strategy (-0.10R edge, 150 trades, CI <= 0, matched rank p >= 0.05)
        neg_result = {
            "total_trades": 150,
            "win_rate_pct": 40.0,
            "profit_factor": 0.82,
            "expectancy_r": -0.1000,
            "expectancy_usd": -15.0,
            "avg_r": -0.1000,
            "sharpe_ratio": -1.1,
            "sortino_ratio": -1.4,
            "max_drawdown_pct": 35.0,
            "total_return_pct": -25.0,
            "bootstrap_ci": (-0.165, -0.035),
            "matched_p_value": 0.985,
        }
        res_neg = evaluator.evaluate("SyntheticNoise", "Negative -0.1R Drift", neg_result)
        assert res_neg.is_validated is False
        assert res_neg.verdict == "REJECTED (p >= 0.05)"
        assert res_neg.expectancy_r == -0.1000
        assert res_neg.bootstrap_ci[0] <= 0.0
        assert res_neg.matched_p_value >= 0.05

    def test_strategy_results_json_metadata_date_consistency(self):
        """start_time_ms/end_time_ms must be derived from actual dataset range and match data_date_range."""
        import json
        import os
        from datetime import datetime, timezone

        json_path = "research/reports/strategy_results.json"
        if not os.path.exists(json_path):
            pytest.skip("strategy_results.json not generated yet")

        with open(json_path, "r") as f:
            data = json.load(f)

        strategies = data.get("strategies", data)
        assert len(strategies) >= 4, "Must contain all strategies"
        meta = data.get("metadata", {})
        for s_name, strat_meta in strategies.items():
            start_ms = strat_meta.get("start_time_ms") or meta.get("start_time_ms")
            end_ms = strat_meta.get("end_time_ms") or meta.get("end_time_ms")
            date_range = strat_meta.get("data_date_range") or meta.get("data_date_range")

            assert start_ms is not None and start_ms > 0
            assert end_ms is not None and end_ms > start_ms
            assert date_range is not None and " to " in date_range

            start_d_str = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
            end_d_str = datetime.fromtimestamp(end_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")

            expected_range = f"{start_d_str} to {end_d_str}"
            assert date_range == expected_range, f"Mismatch for {s_name}: {date_range} vs {expected_range}"

            if "days_evaluated" in strat_meta:
                assert strat_meta["days_evaluated"] > 0
            if "pct_of_dataset_evaluated" in strat_meta and strat_meta.get("sizing_mode") == "fixed_risk":
                assert strat_meta["pct_of_dataset_evaluated"] == 100.0

    def test_strategy_results_json_metadata_bars_and_years(self):
        """bars_per_symbol and dataset_years must be present in strategy_results.json metadata."""
        import json
        import os

        json_path = "research/reports/strategy_results.json"
        if not os.path.exists(json_path):
            pytest.skip("strategy_results.json not generated yet")

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        meta = data.get("metadata", {})
        assert "bars_per_symbol" in meta, "bars_per_symbol missing in metadata"
        bars = meta["bars_per_symbol"]
        assert isinstance(bars, dict), "bars_per_symbol must be a dict"
        assert len(bars) == 10, f"Expected 10 symbols, got {len(bars)}"
        assert "SUIUSDT" in bars
        assert "BTCUSDT" in bars
        assert bars["SUIUSDT"] < bars["BTCUSDT"], "SUIUSDT should start later and have fewer bars"
        assert bars["SOLUSDT"] < bars["BTCUSDT"], "SOLUSDT should have downtime gaps and fewer bars than BTC"
        assert bars["XRPUSDT"] < bars["BTCUSDT"], "XRPUSDT should have downtime gaps and fewer bars than BTC"

        assert "dataset_years" in meta, "dataset_years missing in metadata"
        years = meta["dataset_years"]
        assert isinstance(years, (int, float)) and years > 0.0

    def test_save_strategy_results_json_raises_on_missing_bars_or_years(self, tmp_path):
        """save_strategy_results_json must raise ValueError if bars_per_symbol or dataset_years is missing without candles_dict."""
        from research.strategy_runner import save_strategy_results_json

        out_json = str(tmp_path / "results.json")
        with pytest.raises(ValueError, match=r"candles_dict or.*bars_per_symbol and dataset_years"):
            save_strategy_results_json([], out_json, candles_dict=None)

        with pytest.raises(ValueError, match="bars_per_symbol is required"):
            save_strategy_results_json([], out_json, candles_dict=None, bars_per_symbol={}, dataset_years=5.0)

        with pytest.raises(ValueError, match="dataset_years is required"):
            save_strategy_results_json([], out_json, candles_dict=None, bars_per_symbol={"BTCUSDT": 100}, dataset_years=0.0)

    def test_generate_markdown_report_raises_on_missing_dataset_info(self, tmp_path):
        """generate_markdown_report must raise ValueError if dataset_info lacks required fields or if results are empty without dataset_info."""
        from research.strategy_runner import generate_markdown_report

        out_md = str(tmp_path / "report.md")
        with pytest.raises(ValueError, match="symbols list is missing"):
            generate_markdown_report([], out_md, dataset_info={"symbols": []})

        with pytest.raises(ValueError, match="dataset days is missing"):
            generate_markdown_report([], out_md, dataset_info={"symbols": ["BTCUSDT"]})

        with pytest.raises(ValueError, match="bars_per_symbol is missing"):
            generate_markdown_report([], out_md, dataset_info={"symbols": ["BTCUSDT"], "days": 10.0})

        with pytest.raises(ValueError, match="Cannot determine dataset parameters"):
            generate_markdown_report([], out_md, dataset_info=None)

    def test_get_git_commit_hash_dirty(self):
        """get_git_commit_hash should return a valid git hash and append '+dirty' if working tree has changes."""
        from research.strategy_runner import get_git_commit_hash

        gh = get_git_commit_hash()
        assert gh != "unknown"
        assert len(gh) >= 7


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


class TestMarkdownReportGeneration:
    """Verify publication-grade Strategy Validation Report formatting and dynamic narrative rules."""

    @staticmethod
    def _make_sim_result(
        total_trades: int = 150,
        win_rate_pct: float = 42.0,
        profit_factor: float = 0.85,
        gross_profit_factor: float = 0.92,
        expectancy_r: float = -0.08,
        gross_expectancy_r: float = -0.03,
        max_drawdown_r: float = 15.2,
        cumulative_r: float = -12.0,
        days_evaluated: float = 1022.0,
        pct_of_dataset_evaluated: float = 100.0,
        ruined: bool = False,
        sizing_mode: str = "fixed_risk",
    ) -> SimulationResult:
        return SimulationResult(
            initial_equity_usd=10000.0,
            final_equity_usd=9000.0,
            net_profit_usd=-1000.0,
            net_profit_pct=-10.0,
            total_trades=total_trades,
            winning_trades=int(total_trades * win_rate_pct / 100),
            losing_trades=total_trades - int(total_trades * win_rate_pct / 100),
            win_rate_pct=win_rate_pct,
            gross_profit_usd=2000.0,
            gross_loss_usd=2500.0,
            profit_factor=profit_factor,
            expectancy_usd=expectancy_r * 100,
            expectancy_r=expectancy_r,
            avg_r_multiple=expectancy_r,
            max_drawdown_pct=18.5,
            max_drawdown_duration_bars=50,
            sharpe_ratio=-1.5,
            sortino_ratio=-1.8,
            total_fees_usd=100.0,
            total_slippage_usd=50.0,
            total_funding_cost_usd=10.0,
            ruined=ruined,
            sizing_mode=sizing_mode,
            gross_profit_factor=gross_profit_factor,
            gross_expectancy_r=gross_expectancy_r,
            cumulative_r=cumulative_r,
            max_drawdown_r=max_drawdown_r,
            days_evaluated=days_evaluated,
            pct_of_dataset_evaluated=pct_of_dataset_evaluated,
        )

    def test_report_all_rejected_has_no_edge_emerges_or_proven(self, tmp_path):
        from research.evaluator import StrategyEvaluation
        from research.strategy_runner import StrategyRunResult, generate_markdown_report

        sim_res1 = self._make_sim_result(total_trades=150, win_rate_pct=42.0, expectancy_r=-0.08)
        ev1 = StrategyEvaluation(
            strategy_name="TrendPullback",
            description="EMA Pullback",
            total_trades=150,
            win_rate=42.0,
            profit_factor=0.85,
            expectancy_r=-0.08,
            expectancy_usd=-8.0,
            avg_r=-0.08,
            sharpe_ratio=-1.5,
            sortino_ratio=-1.8,
            max_drawdown_pct=18.5,
            net_profit_pct=-10.0,
            p_profit_factor=0.80,
            p_win_rate=0.75,
            p_expectancy_r=0.85,
            p_sharpe=0.90,
            p_composite=0.85,
            is_validated=False,
            verdict="REJECTED (p >= 0.05)",
        )
        r1 = StrategyRunResult(
            strategy_name="TrendPullback",
            description="EMA Pullback",
            sim_result=sim_res1,
            evaluation=ev1,
            per_symbol_metrics={"BTCUSDT": {"trades": 150, "win_rate": 42.0, "profit_factor": 0.85, "net_pnl": -1000.0, "avg_r": -0.08}},
            total_signals_generated=500,
            signals_executed=150,
            bootstrap_ci=(-0.14, -0.02),
        )

        out_path = str(tmp_path / "test_report.md")
        generate_markdown_report([r1], out_path)

        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Rule 5 check: Must not contain "edge emerges" or "proven"
        assert "edge emerges" not in content.lower()
        assert "proven" not in content.lower()

        # Rule 1 check: Plainly states no strategy passed
        assert "No strategy passed; no pattern in this report has validated edge" in content

        # Rule 3 check: Limitations section exists with required fields
        assert "## 5. Limitations" in content
        assert "fixed_risk" in content
        assert "**TrendPullback**: 150 executed trades" in content
        assert "Prompt 7" in content
        assert "gross P&L" in content or "Gross vs. Net" in content

    def test_report_validated_lists_only_passed_strategies(self, tmp_path):
        from research.evaluator import StrategyEvaluation
        from research.strategy_runner import StrategyRunResult, generate_markdown_report

        sim_pass = self._make_sim_result(total_trades=200, win_rate_pct=58.0, profit_factor=1.45, expectancy_r=0.15)
        ev_pass = StrategyEvaluation(
            strategy_name="AlphaModel",
            description="Alpha",
            total_trades=200,
            win_rate=58.0,
            profit_factor=1.45,
            expectancy_r=0.15,
            expectancy_usd=15.0,
            avg_r=0.15,
            sharpe_ratio=1.8,
            sortino_ratio=2.2,
            max_drawdown_pct=8.0,
            net_profit_pct=25.0,
            p_profit_factor=0.001,
            p_win_rate=0.002,
            p_expectancy_r=0.001,
            p_sharpe=0.005,
            p_composite=0.001,
            matched_p_value=0.001,
            is_validated=True,
            verdict="VALIDATED (p < 0.05)",
        )
        r_pass = StrategyRunResult(
            strategy_name="AlphaModel",
            description="Alpha",
            sim_result=sim_pass,
            evaluation=ev_pass,
            per_symbol_metrics={"BTCUSDT": {"trades": 200, "win_rate": 58.0, "profit_factor": 1.45, "net_pnl": 2500.0, "avg_r": 0.15}},
            total_signals_generated=800,
            signals_executed=200,
            bootstrap_ci=(0.08, 0.22),
        )

        sim_fail = self._make_sim_result(total_trades=110, win_rate_pct=41.0, profit_factor=0.82, expectancy_r=-0.11)
        ev_fail = StrategyEvaluation(
            strategy_name="BetaNoise",
            description="Noise",
            total_trades=110,
            win_rate=41.0,
            profit_factor=0.82,
            expectancy_r=-0.11,
            expectancy_usd=-11.0,
            avg_r=-0.11,
            sharpe_ratio=-1.2,
            sortino_ratio=-1.4,
            max_drawdown_pct=15.0,
            net_profit_pct=-8.0,
            p_profit_factor=0.85,
            p_win_rate=0.80,
            p_expectancy_r=0.90,
            p_sharpe=0.88,
            p_composite=0.90,
            matched_p_value=0.92,
            is_validated=False,
            verdict="REJECTED (p >= 0.05)",
        )
        r_fail = StrategyRunResult(
            strategy_name="BetaNoise",
            description="Noise",
            sim_result=sim_fail,
            evaluation=ev_fail,
            per_symbol_metrics={"BTCUSDT": {"trades": 110, "win_rate": 41.0, "profit_factor": 0.82, "net_pnl": -800.0, "avg_r": -0.11}},
            total_signals_generated=400,
            signals_executed=110,
            bootstrap_ci=(-0.18, -0.04),
        )

        out_path = str(tmp_path / "test_report_mixed.md")
        generate_markdown_report([r_pass, r_fail], out_path)

        with open(out_path, "r", encoding="utf-8") as f:
            content = f.read()

        assert "Validated Setups (1/2)" in content
        assert "AlphaModel" in content
        assert "BetaNoise" in content
        assert "edge emerges" not in content.lower()
        assert "proven" not in content.lower()


class TestStrategyResultsDatasetHash:
    """Ensure strategy_results.json is synchronized with the actual dataset."""

    def test_strategy_results_dataset_hash_matches_loader(self):
        import json
        import os
        from data.loader import get_current_dataset_hash

        json_path = "research/reports/strategy_results.json"
        assert os.path.exists(json_path), f"{json_path} does not exist"
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        saved_hash = data.get("metadata", {}).get("dataset_hash")
        assert saved_hash is not None, "dataset_hash missing in strategy_results.json metadata"

        current_hash = get_current_dataset_hash()
        assert saved_hash == current_hash, (
            f"strategy_results.json dataset_hash ({saved_hash}) differs from loader's current hash ({current_hash}). "
            f"Regenerate research/reports/strategy_results.json to synchronize."
        )


class TestZeroVolumeFiltering:
    """Verify that the real runner data path includes volume and skips signals on zero-volume entry bars."""

    def test_runner_candle_arrays_include_volume_and_skip_zero_volume_signal(self, caplog):
        import logging
        from data.loader import load_candles
        from research.strategies import TrendPullbackStrategy
        from engine.simulator import simulate_portfolio_signals

        df_1h = load_candles("BTCUSDT", "1h").iloc[:1000].copy()
        df_4h = load_candles("BTCUSDT", "4h").iloc[:300].copy()

        strat = TrendPullbackStrategy()
        signals = strat.generate_signals("BTCUSDT", df_1h, df_4h)
        assert len(signals) > 0, "Expected at least one signal in candle slice"

        target_sig = signals[0]
        target_bar_idx = target_sig.metadata["bar_idx"]

        # 1. Normal volume run: signal executes
        arr_normal = df_1h[["open", "high", "low", "close", "open_time", "volume"]].to_numpy(dtype=np.float64)
        assert arr_normal.shape[1] >= 6, "Array must include volume at column index 5"
        assert arr_normal[target_bar_idx, 5] > 0.0

        candles_normal = {"BTCUSDT": arr_normal}
        bar_idx_normal = {"BTCUSDT": {int(arr_normal[i, 4]): i for i in range(len(arr_normal))}}
        res_normal = simulate_portfolio_signals(
            candidates=[target_sig],
            candles_dict=candles_normal,
            bar_index_map=bar_idx_normal,
        )
        assert res_normal.total_trades == 1

        # 2. Zero volume entry bar: signal must be skipped
        df_1h_zero = df_1h.copy()
        df_1h_zero.loc[target_bar_idx, "volume"] = 0.0
        arr_zero = df_1h_zero[["open", "high", "low", "close", "open_time", "volume"]].to_numpy(dtype=np.float64)
        assert arr_zero[target_bar_idx, 5] == 0.0

        candles_zero = {"BTCUSDT": arr_zero}
        bar_idx_zero = {"BTCUSDT": {int(arr_zero[i, 4]): i for i in range(len(arr_zero))}}

        with caplog.at_level(logging.WARNING):
            res_zero = simulate_portfolio_signals(
                candidates=[target_sig],
                candles_dict=candles_zero,
                bar_index_map=bar_idx_zero,
            )

        assert res_zero.total_trades == 0
        assert any(
            "entry bar has volume == 0" in record.message or "volume is zero" in record.message
            for record in caplog.records
        )

    def test_real_runner_pipeline_skips_zero_volume_bar(self, tmp_path, caplog):
        import logging
        from unittest.mock import MagicMock, patch
        from data.loader import load_candles
        from research.evaluator import BaselineHurdles
        from research.strategies import TrendPullbackStrategy
        from research.strategy_runner import run_strategy_evidence_validation

        df_1h = load_candles("BTCUSDT", "1h").iloc[:1000].copy()
        df_4h = load_candles("BTCUSDT", "4h").iloc[:300].copy()

        strat = TrendPullbackStrategy()
        sigs = strat.generate_signals("BTCUSDT", df_1h, df_4h)
        assert len(sigs) > 0
        first_sig = sigs[0]
        target_bar = first_sig.metadata["bar_idx"]

        # Set entry bar volume to 0.0
        df_1h.loc[target_bar, "volume"] = 0.0

        def mock_load(sym, tf, **kwargs):
            if tf == "1h":
                return df_1h.copy()
            return df_4h.copy()

        mock_hurdles = BaselineHurdles(
            pf_hurdle_p95=1.04,
            pf_null_mean=0.84,
            pf_null_std=0.1,
            wr_hurdle_p95=52.0,
            wr_null_mean=46.8,
            wr_null_std=3.0,
            ev_r_hurdle_p95=0.016,
            ev_r_null_mean=-0.075,
            ev_r_null_std=0.05,
            sharpe_hurdle_p95=0.3,
            sharpe_null_mean=-1.39,
            sharpe_null_std=0.8,
            sortino_hurdle_p95=0.4,
            sortino_null_mean=-1.5,
            sortino_null_std=0.8,
            dd_hurdle_p50=25.0,
            dd_hurdle_p95=44.0,
            dd_null_mean=25.0,
            dd_null_std=5.0,
            ret_hurdle_p95=10.0,
            ret_null_mean=-20.0,
            ret_null_std=10.0,
        )

        with caplog.at_level(logging.WARNING):
            with patch("research.strategy_runner.load_candles", side_effect=mock_load):
                with patch("research.evaluator.BaselineHurdles.load", return_value=mock_hurdles):
                    with patch("research.strategy_runner.run_matched_baseline") as mock_matched:
                        mock_matched.return_value = MagicMock(
                            empirical_p_value=1.0,
                            median_expectancy_r=-0.1,
                            p95_expectancy_r=0.0,
                            iterations=1,
                        )
                        results = run_strategy_evidence_validation(
                            symbols=["BTCUSDT"],
                            output_report_path=str(tmp_path / "report.md"),
                            output_json_path=str(tmp_path / "results.json"),
                            matched_passes=1,
                        )

        trend_res = next(r for r in results if r.strategy_name == "TrendPullback")
        executed_entry_times = {t.entry_time_ms for t in trend_res.sim_result.trades}

        # Assert that the signal on the zero-volume bar was skipped
        assert first_sig.timestamp_ms not in executed_entry_times
        assert any("entry bar has volume == 0" in record.message for record in caplog.records)


