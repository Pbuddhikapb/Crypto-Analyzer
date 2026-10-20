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
from typing import Any, Dict, List, Optional, Tuple

from engine.simulator import SimulationResult


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

    # Matched Baseline & Bootstrap Metrics
    matched_p_value: Optional[float] = None
    bootstrap_ci: Optional[Tuple[float, float]] = None
    matched_median_ev: Optional[float] = None
    matched_p95_ev: Optional[float] = None

    # Verification checks
    hurdles_passed: List[str] = field(default_factory=list)
    hurdles_failed: List[str] = field(default_factory=list)
    verdict: str = "REJECTED (p >= 0.05)"
    is_validated: bool = False

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
            "hurdles_passed": self.hurdles_passed,
            "hurdles_failed": self.hurdles_failed,
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
        net_ret = float(m.get("total_return_pct", 0.0))

        # Compute individual p-values against standard baseline
        p_pf = compute_p_value(profit_factor, h.pf_null_mean, h.pf_null_std, higher_is_better=True)
        p_wr = compute_p_value(win_rate, h.wr_null_mean, h.wr_null_std, higher_is_better=True)
        p_ev = compute_p_value(expectancy_r, h.ev_r_null_mean, h.ev_r_null_std, higher_is_better=True)
        p_sharpe = compute_p_value(sharpe, h.sharpe_null_mean, h.sharpe_null_std, higher_is_better=True)

        matched_p: Optional[float] = None
        matched_med_ev: Optional[float] = None
        matched_p95_ev: Optional[float] = None

        if matched_baseline is not None:
            matched_p = float(getattr(matched_baseline, "empirical_p_value", 1.0))
            matched_med_ev = float(getattr(matched_baseline, "median_expectancy_r", 0.0))
            matched_p95_ev = float(getattr(matched_baseline, "p95_expectancy_r", 0.0))
            # Empirical rank p-value from matched distribution governs composite significance
            p_composite = matched_p
        else:
            # Fallback to analytical max(p_pf, p_ev) when no matched baseline is provided
            p_composite = max(p_pf, p_ev)

        passed: List[str] = []
        failed: List[str] = []

        # Check Matched Rank P-Value if available
        if matched_p is not None:
            if matched_p < 0.05:
                passed.append(f"Matched Rank p-value {matched_p:.4f} < 0.05")
            else:
                failed.append(f"Matched Rank p-value {matched_p:.4f} >= 0.05")

        # Check Profit Factor Hurdle
        if profit_factor > h.pf_hurdle_p95:
            passed.append(f"Profit Factor {profit_factor:.2f} > {h.pf_hurdle_p95:.2f} (p={p_pf:.4f})")
        else:
            failed.append(f"Profit Factor {profit_factor:.2f} <= {h.pf_hurdle_p95:.2f} (p={p_pf:.4f})")

        # Check Win Rate Hurdle
        if win_rate > h.wr_hurdle_p95:
            passed.append(f"Win Rate {win_rate:.2f}% > {h.wr_hurdle_p95:.2f}% (p={p_wr:.4f})")
        else:
            failed.append(f"Win Rate {win_rate:.2f}% <= {h.wr_hurdle_p95:.2f}% (p={p_wr:.4f})")

        # Check Expectancy Hurdle (against matched p95 if available, else standard p95)
        ev_target_hurdle = matched_p95_ev if matched_p95_ev is not None else h.ev_r_hurdle_p95
        if expectancy_r > ev_target_hurdle:
            passed.append(f"Expectancy {expectancy_r:+.4f}R > {ev_target_hurdle:+.4f}R (p={p_ev:.4f})")
        else:
            failed.append(f"Expectancy {expectancy_r:+.4f}R <= {ev_target_hurdle:+.4f}R (p={p_ev:.4f})")

        # Check Sharpe Hurdle
        if sharpe > h.sharpe_hurdle_p95:
            passed.append(f"Sharpe {sharpe:.2f} > {h.sharpe_hurdle_p95:.2f} (p={p_sharpe:.4f})")
        else:
            failed.append(f"Sharpe {sharpe:.2f} <= {h.sharpe_hurdle_p95:.2f} (p={p_sharpe:.4f})")

        # Check Max Drawdown Hurdle
        if max_dd < h.dd_hurdle_p50:
            passed.append(f"Max DD {max_dd:.2f}% < {h.dd_hurdle_p50:.2f}%")
        else:
            failed.append(f"Max DD {max_dd:.2f}% >= {h.dd_hurdle_p50:.2f}%")

        # Statistical Alpha Verdict
        # Required: Total trades >= 30, p_composite < 0.05, PF > h.pf_hurdle_p95, EV > ev_target_hurdle
        is_validated = (
            total_trades >= 30
            and p_composite < 0.05
            and profit_factor > h.pf_hurdle_p95
            and expectancy_r > ev_target_hurdle
        )

        verdict = "VALIDATED (p < 0.05)" if is_validated else "REJECTED (p >= 0.05)"

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
            verdict=verdict,
            is_validated=is_validated,
        )
