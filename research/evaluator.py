"""
Strategy Statistical Hurdle Evaluator for Phase 3.

Evaluates trading hypothesis backtest results against the Phase 2 Monte Carlo
1,000-pass empirical baseline distribution (H0). Computes exact z-scores,
p-values, and hurdle checks to establish whether a strategy possesses genuine alpha
or is indistinguishable from random noise (p >= 0.05).
"""

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np



@dataclass(frozen=True)
class BaselineHurdles:
    """Empirical 1,000-pass Monte Carlo baseline distributions and hurdles loaded from baseline_distribution.json."""

    # Profit Factor
    pf_hurdle_p95: float
    pf_null_mean: float
    pf_null_std: float

    # Win Rate (%)
    wr_hurdle_p95: float
    wr_null_mean: float
    wr_null_std: float

    # Expectancy (R)
    ev_r_hurdle_p95: float
    ev_r_null_mean: float
    ev_r_null_std: float

    # Sharpe Ratio
    sharpe_hurdle_p95: float
    sharpe_null_mean: float
    sharpe_null_std: float

    # Sortino Ratio
    sortino_hurdle_p95: float
    sortino_null_mean: float
    sortino_null_std: float

    # Max Drawdown (%)
    dd_hurdle_p50: float
    dd_hurdle_p95: float
    dd_null_mean: float
    dd_null_std: float

    # Net Return (%)
    ret_hurdle_p95: float
    ret_null_mean: float
    ret_null_std: float

    # Max Drawdown (R)
    max_dd_r_p50: float = 0.0
    max_dd_r_p95: float = 0.0
    max_dd_r_null_mean: float = 0.0
    max_dd_r_null_std: float = 0.0

    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(
        cls,
        json_path: str = "research/reports/baseline_distribution.json",
        expected_dataset_hash: Optional[str] = None,
        expected_sizing_mode: Optional[str] = None,
        expected_friction: Optional[Dict[str, float]] = None,
    ) -> "BaselineHurdles":
        """
        Load baseline hurdles and null distribution parameters from JSON file.
        Raises FileNotFoundError if file is missing.
        Raises ValueError if dataset_hash, sizing_mode, or friction differ from expectations.
        """
        if not os.path.exists(json_path):
            raise FileNotFoundError(
                f"Baseline distribution file missing at '{json_path}'. "
                "Run research/random_baseline.py first to generate the empirical baseline distribution."
            )

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        meta = data.get("metadata", {})
        dists = data.get("distributions", {})

        # Validation: dataset hash
        if expected_dataset_hash is not None:
            found_hash = meta.get("dataset_hash")
            if found_hash != expected_dataset_hash:
                raise ValueError(
                    f"Baseline distribution dataset mismatch: baseline dataset_hash='{found_hash}', "
                    f"expected strategy dataset_hash='{expected_dataset_hash}'."
                )

        # Validation: sizing mode
        if expected_sizing_mode is not None:
            found_mode = meta.get("sizing_mode")
            if found_mode != expected_sizing_mode:
                raise ValueError(
                    f"Baseline distribution sizing_mode mismatch: baseline was generated with '{found_mode}', "
                    f"but strategy run specified sizing_mode='{expected_sizing_mode}'."
                )

        # Validation: friction
        if expected_friction is not None:
            found_fric = meta.get("friction", {})
            for k, expected_v in expected_friction.items():
                if k in found_fric:
                    actual_v = float(found_fric[k])
                    if abs(actual_v - float(expected_v)) > 1e-6:
                        raise ValueError(
                            f"Baseline distribution friction mismatch on '{k}': "
                            f"baseline={actual_v}, strategy expected={expected_v}."
                        )

        dd_r = dists.get("max_drawdown_r", {})

        return cls(
            pf_hurdle_p95=float(dists["profit_factor"]["p95"]),
            pf_null_mean=float(dists["profit_factor"]["mean"]),
            pf_null_std=float(dists["profit_factor"]["std"]),
            wr_hurdle_p95=float(dists["win_rate"]["p95"]),
            wr_null_mean=float(dists["win_rate"]["mean"]),
            wr_null_std=float(dists["win_rate"]["std"]),
            ev_r_hurdle_p95=float(dists["expectancy_r"]["p95"]),
            ev_r_null_mean=float(dists["expectancy_r"]["mean"]),
            ev_r_null_std=float(dists["expectancy_r"]["std"]),
            sharpe_hurdle_p95=float(dists["sharpe_ratio"]["p95"]),
            sharpe_null_mean=float(dists["sharpe_ratio"]["mean"]),
            sharpe_null_std=float(dists["sharpe_ratio"]["std"]),
            sortino_hurdle_p95=float(dists["sortino_ratio"]["p95"]),
            sortino_null_mean=float(dists["sortino_ratio"]["mean"]),
            sortino_null_std=float(dists["sortino_ratio"]["std"]),
            dd_hurdle_p50=float(dists["max_drawdown_pct"]["p50"]),
            dd_hurdle_p95=float(dists["max_drawdown_pct"]["p95"]),
            dd_null_mean=float(dists["max_drawdown_pct"]["mean"]),
            dd_null_std=float(dists["max_drawdown_pct"]["std"]),
            max_dd_r_p50=float(dd_r.get("p50", 0.0)),
            max_dd_r_p95=float(dd_r.get("p95", 0.0)),
            max_dd_r_null_mean=float(dd_r.get("mean", 0.0)),
            max_dd_r_null_std=float(dd_r.get("std", 0.0)),
            ret_hurdle_p95=float(dists["net_profit_pct"]["p95"]),
            ret_null_mean=float(dists["net_profit_pct"]["mean"]),
            ret_null_std=float(dists["net_profit_pct"]["std"]),
            metadata=meta,
        )


def compute_p_value(observed: float, null_mean: float, null_std: float, higher_is_better: bool = True) -> float:
    """
    Compute right-tail survival probability (p-value) against null distribution H0.
    Uses standard normal survival function via math.erfc:
    p = 0.5 * erfc(z / sqrt(2))
    """
    if null_std <= 1e-9:
        return 0.5

    if higher_is_better:
        z = (observed - null_mean) / null_std
    else:
        # Lower is better (e.g. Drawdown)
        z = (null_mean - observed) / null_std

    # Standard normal survival function: P(Z >= z)
    p_val = 0.5 * math.erfc(z / math.sqrt(2.0))
    return min(max(float(p_val), 0.0001), 0.9999)


@dataclass
class StrategyEvaluation:
    """Comprehensive statistical evaluation of a trading hypothesis."""

    strategy_name: str
    description: str
    total_trades: int
    win_rate: float
    profit_factor: float
    expectancy_r: float
    expectancy_usd: float
    avg_r: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    net_profit_pct: float

    # P-values against Monte Carlo H0
    p_profit_factor: float
    p_win_rate: float
    p_expectancy_r: float
    p_sharpe: float
    p_composite: float

    # R-metrics and evaluation span
    max_drawdown_r: float = 0.0
    cumulative_r: float = 0.0
    days_evaluated: float = 0.0
    pct_of_dataset_evaluated: float = 100.0

    # Matched Baseline & Bootstrap Metrics
    matched_p_value: Optional[float] = None
    bootstrap_ci: Optional[Tuple[float, float]] = None
    matched_median_ev: Optional[float] = None
    matched_p95_ev: Optional[float] = None

    # Verification checks
    hurdles_passed: List[str] = field(default_factory=list)
    hurdles_failed: List[str] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)
    verdict: str = "REJECTED (p >= 0.05)"
    is_validated: bool = False
    significance_level: float = 0.05

    # Pre-registered diagnostic metrics
    med_stop_pct: Optional[float] = None
    cost_drag_r: Optional[float] = None
    med_holding_bars: Optional[float] = None
    trades_per_symbol: Optional[Dict[str, int]] = None
    per_year_net_r: Optional[Dict[int, float]] = None
    dropped_trade_fraction: float = 0.0
    p_value_at_floor: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "description": self.description,
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "expectancy_r": self.expectancy_r,
            "expectancy_usd": self.expectancy_usd,
            "avg_r": self.avg_r,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "max_drawdown_r": self.max_drawdown_r,
            "cumulative_r": self.cumulative_r,
            "days_evaluated": self.days_evaluated,
            "pct_of_dataset_evaluated": self.pct_of_dataset_evaluated,
            "net_profit_pct": self.net_profit_pct,
            "p_profit_factor": self.p_profit_factor,
            "p_win_rate": self.p_win_rate,
            "p_expectancy_r": self.p_expectancy_r,
            "p_sharpe": self.p_sharpe,
            "p_composite": self.p_composite,
            "matched_p_value": self.matched_p_value,
            "bootstrap_ci": self.bootstrap_ci,
            "matched_median_ev": self.matched_median_ev,
            "matched_p95_ev": self.matched_p95_ev,
            "verdict": self.verdict,
            "is_validated": self.is_validated,
            "significance_level": self.significance_level,
            "med_stop_pct": self.med_stop_pct,
            "cost_drag_r": self.cost_drag_r,
            "med_holding_bars": self.med_holding_bars,
            "trades_per_symbol": self.trades_per_symbol,
            "per_year_net_r": self.per_year_net_r,
            "dropped_trade_fraction": self.dropped_trade_fraction,
            "p_value_at_floor": self.p_value_at_floor,
            "hurdles_passed": self.hurdles_passed,
            "hurdles_failed": self.hurdles_failed,
            "diagnostics": self.diagnostics,
        }


class StrategyEvaluator:
    """Evaluates backtest simulation results against the Phase 2 Alpha Hurdle."""

    def __init__(
        self,
        hurdles: Optional[BaselineHurdles] = None,
        json_path: str = "research/reports/baseline_distribution.json",
        expected_dataset_hash: Optional[str] = None,
        expected_sizing_mode: Optional[str] = None,
        expected_friction: Optional[Dict[str, float]] = None,
    ):
        if hurdles is not None:
            self.hurdles = hurdles
        else:
            self.hurdles = BaselineHurdles.load(
                json_path=json_path,
                expected_dataset_hash=expected_dataset_hash,
                expected_sizing_mode=expected_sizing_mode,
                expected_friction=expected_friction,
            )

    def evaluate(
        self,
        strategy_name: str,
        description: str,
        sim_result: Any,
        matched_baseline: Optional[Any] = None,
        bootstrap_ci: Optional[Tuple[float, float]] = None,
        significance_level: float = 0.05,
    ) -> StrategyEvaluation:
        h = self.hurdles

        # Handle both SimulationResult dataclass and dict formats
        if hasattr(sim_result, "metrics") and isinstance(sim_result.metrics, dict):
            m = sim_result.metrics
        elif isinstance(sim_result, dict):
            m = sim_result
        else:
            m = {
                "total_trades": getattr(sim_result, "total_trades", 0),
                "win_rate_pct": getattr(sim_result, "win_rate_pct", 0.0),
                "profit_factor": getattr(sim_result, "profit_factor", 0.0),
                "expectancy_r": getattr(sim_result, "expectancy_r", 0.0),
                "expectancy_usd": getattr(sim_result, "expectancy_usd", 0.0),
                "avg_r": getattr(sim_result, "avg_r_multiple", 0.0),
                "sharpe_ratio": getattr(sim_result, "sharpe_ratio", 0.0),
                "sortino_ratio": getattr(sim_result, "sortino_ratio", 0.0),
                "max_drawdown_pct": getattr(sim_result, "max_drawdown_pct", 0.0),
                "total_return_pct": getattr(sim_result, "net_profit_pct", 0.0),
                "max_drawdown_r": getattr(sim_result, "max_drawdown_r", 0.0),
                "cumulative_r": getattr(sim_result, "cumulative_r", 0.0),
                "days_evaluated": getattr(sim_result, "days_evaluated", 0.0),
                "pct_of_dataset_evaluated": getattr(sim_result, "pct_of_dataset_evaluated", 100.0),
            }

        total_trades = int(m.get("total_trades", 0))
        win_rate = float(m.get("win_rate_pct", 0.0))
        profit_factor = float(m.get("profit_factor", 0.0))
        expectancy_r = float(m.get("expectancy_r", 0.0))
        expectancy_usd = float(m.get("expectancy_usd", 0.0))
        avg_r = float(m.get("avg_r", 0.0))
        sharpe = float(m.get("sharpe_ratio", 0.0))
        sortino = float(m.get("sortino_ratio", 0.0))
        max_dd = float(m.get("max_drawdown_pct", 0.0))
        max_dd_r = float(m.get("max_drawdown_r", 0.0))
        cumulative_r = float(m.get("cumulative_r", 0.0))
        days_evaluated = float(m.get("days_evaluated", 0.0))
        pct_of_dataset_evaluated = float(m.get("pct_of_dataset_evaluated", 100.0))
        net_ret = float(m.get("total_return_pct", 0.0))

        # Compute individual p-values against standard baseline
        p_pf = compute_p_value(profit_factor, h.pf_null_mean, h.pf_null_std, higher_is_better=True)
        p_wr = compute_p_value(win_rate, h.wr_null_mean, h.wr_null_std, higher_is_better=True)
        p_ev = compute_p_value(expectancy_r, h.ev_r_null_mean, h.ev_r_null_std, higher_is_better=True)
        p_sharpe = compute_p_value(sharpe, h.sharpe_null_mean, h.sharpe_null_std, higher_is_better=True)

        if bootstrap_ci is None and "bootstrap_ci" in m and m["bootstrap_ci"] is not None:
            bootstrap_ci = tuple(m["bootstrap_ci"])

        matched_p: Optional[float] = None
        matched_med_ev: Optional[float] = None
        matched_p95_ev: Optional[float] = None
        dropped_trade_fraction: float = 0.0
        p_value_at_floor: bool = False
        matched_iterations: int = 1000

        if matched_baseline is not None:
            matched_p = float(getattr(matched_baseline, "empirical_p_value", 1.0))
            matched_med_ev = float(getattr(matched_baseline, "median_expectancy_r", 0.0))
            matched_p95_ev = float(getattr(matched_baseline, "p95_expectancy_r", 0.0))
            dropped_trade_fraction = float(getattr(matched_baseline, "dropped_trade_fraction", 0.0))
            matched_iterations = int(getattr(matched_baseline, "iterations", 1000))
            p_composite = matched_p
        elif "matched_p_value" in m or "matched_p" in m:
            matched_p = float(m.get("matched_p_value", m.get("matched_p", 1.0)))
            matched_med_ev = float(m.get("matched_median_ev", 0.0)) if "matched_median_ev" in m else None
            matched_p95_ev = float(m.get("matched_p95_ev", 0.0)) if "matched_p95_ev" in m else None
            dropped_trade_fraction = float(m.get("dropped_trade_fraction", 0.0))
            matched_iterations = int(m.get("matched_iterations", m.get("iterations", 1000)))
            p_composite = matched_p
        else:
            # Fallback to analytical max(p_pf, p_ev) when no matched baseline is provided
            p_composite = max(p_pf, p_ev)

        if hasattr(sim_result, "dropped_trade_fraction") and sim_result.dropped_trade_fraction > 0:
            dropped_trade_fraction = max(dropped_trade_fraction, float(getattr(sim_result, "dropped_trade_fraction", 0.0)))

        if matched_p is not None and matched_iterations > 0:
            floor_p = 1.0 / (matched_iterations + 1.0)
            if abs(matched_p - floor_p) < 1e-4:
                p_value_at_floor = True

        # Explicit alpha decision rule:
        # VALIDATED only if total_trades >= 100 AND mean net_r > 0 AND bootstrap 95% CI lower bound > 0 AND matched rank p < significance_level.
        has_min_trades = total_trades >= 100
        has_positive_ev = expectancy_r > 0.0
        has_positive_ci = bootstrap_ci is not None and bootstrap_ci[0] > 0.0
        has_significant_p = matched_p is not None and matched_p < significance_level

        is_validated = (
            has_min_trades
            and has_positive_ev
            and has_positive_ci
            and has_significant_p
        )
        sig_str = f"{significance_level:.3g}"
        verdict = f"VALIDATED (p < {sig_str})" if is_validated else f"REJECTED (p >= {sig_str})"

        passed: List[str] = []
        failed: List[str] = []

        if has_min_trades:
            passed.append(f"Sample size {total_trades} >= 100 trades")
        else:
            failed.append(f"Sample size {total_trades} < 100 trades")

        if has_positive_ev:
            passed.append(f"Mean Net Expectancy {expectancy_r:+.4f}R > 0.0R")
        else:
            failed.append(f"Mean Net Expectancy {expectancy_r:+.4f}R <= 0.0R")

        if has_positive_ci:
            passed.append(f"Bootstrap 95% CI lower bound {bootstrap_ci[0]:+.4f}R > 0.0R")
        else:
            ci_str = f"{bootstrap_ci[0]:+.4f}R" if bootstrap_ci is not None else "N/A"
            failed.append(f"Bootstrap 95% CI lower bound {ci_str} <= 0.0R")

        if has_significant_p:
            passed.append(f"Matched Rank p-value {matched_p:.4f} < {sig_str}")
        else:
            p_str = f"{matched_p:.4f}" if matched_p is not None else "N/A"
            failed.append(f"Matched Rank p-value {p_str} >= {sig_str}")

        # Compute pre-registered diagnostics if trades are available
        trades_list = getattr(sim_result, "trades", [])
        med_stop_pct = None
        cost_drag_r = None
        med_holding_bars = None
        trades_per_symbol = None
        per_year_net_r = None

        if trades_list:
            stop_pcts = [
                (t.initial_risk_usd / t.position_size_usd * 100.0)
                for t in trades_list
                if getattr(t, "position_size_usd", 0) > 0 and getattr(t, "initial_risk_usd", 0) > 0
            ]
            if stop_pcts:
                med_stop_pct = float(np.median(stop_pcts))
            cost_drag_r = float(np.mean([t.realized_r - t.net_r for t in trades_list]))
            holding_bars = [getattr(t, "bars_held", 0) for t in trades_list]
            med_holding_bars = float(np.median(holding_bars)) if holding_bars else 0.0

            sym_counts: Dict[str, int] = {}
            for t in trades_list:
                sym_counts[t.symbol] = sym_counts.get(t.symbol, 0) + 1
            trades_per_symbol = sym_counts
            sym_counts_str = ", ".join(f"{s}: {c}" for s, c in sorted(sym_counts.items()))

            year_net_r: Dict[int, float] = {}
            for t in trades_list:
                yr = datetime.fromtimestamp(t.entry_time_ms / 1000.0, tz=timezone.utc).year
                year_net_r[yr] = year_net_r.get(yr, 0.0) + t.net_r
            per_year_net_r = year_net_r
            year_r_str = ", ".join(f"{yr}: {r:+.2f}R" for yr, r in sorted(year_net_r.items()))

            diagnostics_items = [
                f"Net Profit Factor: {profit_factor:.2f} (Null p95 Hurdle: {h.pf_hurdle_p95:.2f}, p={p_pf:.4f})",
                f"Net Win Rate: {win_rate:.2f}% (Null p95 Hurdle: {h.wr_hurdle_p95:.2f}%, p={p_wr:.4f})",
                f"Max Drawdown (R): {max_dd_r:.2f}R",
                f"Sharpe Ratio: {sharpe:.2f} (p={p_sharpe:.4f})",
            ]
            if med_stop_pct is not None:
                diagnostics_items.append(f"Median Stop % of Price: {med_stop_pct:.2f}%")
            diagnostics_items.extend([
                f"Cost Drag in R per Trade: {cost_drag_r:+.4f}R",
                f"Median Holding Time: {med_holding_bars:.0f} bars ({med_holding_bars:.0f}h)",
                f"Trades per Symbol: {sym_counts_str}",
                f"Per-Year Net R: {year_r_str}",
            ])
            diagnostics = diagnostics_items
        else:
            diagnostics = [
                f"Net Profit Factor: {profit_factor:.2f} (Null p95 Hurdle: {h.pf_hurdle_p95:.2f}, p={p_pf:.4f})",
                f"Net Win Rate: {win_rate:.2f}% (Null p95 Hurdle: {h.wr_hurdle_p95:.2f}%, p={p_wr:.4f})",
                f"Max Drawdown (R): {max_dd_r:.2f}R",
                f"Sharpe Ratio: {sharpe:.2f} (p={p_sharpe:.4f})",
            ]

        if dropped_trade_fraction > 0.0:
            diagnostics.append(f"Matched Null Dropped Trade Fraction: {dropped_trade_fraction:.2%}")
        if p_value_at_floor:
            floor_str = f"{1.0 / (matched_iterations + 1.0):.4f}"
            diagnostics.append(
                f"⚠️ WARNING: Matched rank p-value ({matched_p:.4f}) equals minimum floor 1/(N+1) = {floor_str}. "
                "Null-sanity check required: verify that the matched null distribution is unbiased."
            )

        return StrategyEvaluation(
            strategy_name=strategy_name,
            description=description,
            total_trades=total_trades,
            win_rate=round(win_rate, 2),
            profit_factor=round(profit_factor, 2),
            expectancy_r=round(expectancy_r, 4),
            expectancy_usd=round(expectancy_usd, 2),
            avg_r=round(avg_r, 4),
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            max_drawdown_pct=round(max_dd, 2),
            max_drawdown_r=round(max_dd_r, 2),
            cumulative_r=round(cumulative_r, 4),
            days_evaluated=round(days_evaluated, 1),
            pct_of_dataset_evaluated=pct_of_dataset_evaluated,
            net_profit_pct=round(net_ret, 2),
            p_profit_factor=round(p_pf, 4),
            p_win_rate=round(p_wr, 4),
            p_expectancy_r=round(p_ev, 4),
            p_sharpe=round(p_sharpe, 4),
            p_composite=round(p_composite, 4),
            matched_p_value=round(matched_p, 4) if matched_p is not None else None,
            bootstrap_ci=bootstrap_ci,
            matched_median_ev=round(matched_med_ev, 4) if matched_med_ev is not None else None,
            matched_p95_ev=round(matched_p95_ev, 4) if matched_p95_ev is not None else None,
            hurdles_passed=passed,
            hurdles_failed=failed,
            diagnostics=diagnostics,
            verdict=verdict,
            is_validated=is_validated,
            significance_level=significance_level,
            med_stop_pct=med_stop_pct,
            cost_drag_r=cost_drag_r,
            med_holding_bars=med_holding_bars,
            trades_per_symbol=trades_per_symbol,
            per_year_net_r=per_year_net_r,
            dropped_trade_fraction=round(dropped_trade_fraction, 6),
            p_value_at_floor=p_value_at_floor,
        )
