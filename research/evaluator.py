"""
Strategy Statistical Hurdle Evaluator for Phase 3.

Evaluates trading hypothesis backtest results against the Phase 2 Monte Carlo
1,000-pass empirical baseline distribution (H0). Computes exact z-scores,
p-values, and hurdle checks to establish whether a strategy possesses genuine alpha
or is indistinguishable from random noise (p >= 0.05).
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.simulator import SimulationResult


@dataclass(frozen=True)
class BaselineHurdles:
    """Empirical 1,000-pass Monte Carlo baseline distributions and hurdles."""

    # Profit Factor
    pf_hurdle_p95: float = 1.14
    pf_null_mean: float = 0.93
    pf_null_std: float = 0.13

    # Win Rate (%)
    wr_hurdle_p95: float = 50.00
    wr_null_mean: float = 44.82
    wr_null_std: float = 3.07

    # Expectancy (R)
    ev_r_hurdle_p95: float = 0.0520
    ev_r_null_mean: float = -0.0282
    ev_r_null_std: float = 0.0520

    # Sharpe Ratio
    sharpe_hurdle_p95: float = 0.07
    sharpe_null_mean: float = -1.46
    sharpe_null_std: float = 1.00

    # Sortino Ratio
    sortino_hurdle_p95: float = 0.10
    sortino_null_mean: float = -1.90
    sortino_null_std: float = 1.25

    # Max Drawdown (%)
    dd_hurdle_p50: float = 23.26
    dd_hurdle_p95: float = 36.95
    dd_null_mean: float = 23.49
    dd_null_std: float = 7.78

    # Net Return (%)
    ret_hurdle_p95: float = 0.07
    ret_null_mean: float = -17.47
    ret_null_std: float = 10.68


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
            "verdict": self.verdict,
            "is_validated": self.is_validated,
            "hurdles_passed": self.hurdles_passed,
            "hurdles_failed": self.hurdles_failed,
        }


class StrategyEvaluator:
    """Evaluates backtest simulation results against the Phase 2 Alpha Hurdle."""

    def __init__(self, hurdles: Optional[BaselineHurdles] = None):
        self.hurdles = hurdles or BaselineHurdles()

    def evaluate(
        self,
        strategy_name: str,
        description: str,
        sim_result: Any,
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

        # Compute individual p-values
        p_pf = compute_p_value(profit_factor, h.pf_null_mean, h.pf_null_std, higher_is_better=True)
        p_wr = compute_p_value(win_rate, h.wr_null_mean, h.wr_null_std, higher_is_better=True)
        p_ev = compute_p_value(expectancy_r, h.ev_r_null_mean, h.ev_r_null_std, higher_is_better=True)
        p_sharpe = compute_p_value(sharpe, h.sharpe_null_mean, h.sharpe_null_std, higher_is_better=True)

        # Composite p-value: harmonic mean or conservative max of primary edge drivers (PF & EV)
        # In financial statistical testing, a true edge requires BOTH PF and EV to reject H0
        p_composite = max(p_pf, p_ev)

        passed: List[str] = []
        failed: List[str] = []

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

        # Check Expectancy Hurdle
        if expectancy_r > h.ev_r_hurdle_p95:
            passed.append(f"Expectancy {expectancy_r:+.4f}R > {h.ev_r_hurdle_p95:+.4f}R (p={p_ev:.4f})")
        else:
            failed.append(f"Expectancy {expectancy_r:+.4f}R <= {h.ev_r_hurdle_p95:+.4f}R (p={p_ev:.4f})")

        # Check Sharpe Hurdle
        if sharpe > h.sharpe_hurdle_p95:
            passed.append(f"Sharpe {sharpe:.2f} > {h.sharpe_hurdle_p95:.2f} (p={p_sharpe:.4f})")
        else:
            failed.append(f"Sharpe {sharpe:.2f} <= {h.sharpe_hurdle_p95:.2f} (p={p_sharpe:.4f})")

        # Check Max Drawdown Hurdle (Must be lower than median random drawdown of 23.26%)
        if max_dd < h.dd_hurdle_p50:
            passed.append(f"Max DD {max_dd:.2f}% < {h.dd_hurdle_p50:.2f}%")
        else:
            failed.append(f"Max DD {max_dd:.2f}% >= {h.dd_hurdle_p50:.2f}%")

        # Statistical Alpha Verdict
        # Required: Total trades >= 30, p_composite < 0.05, PF > 1.14, EV > +0.0520R
        is_validated = (
            total_trades >= 30
            and p_composite < 0.05
            and profit_factor > h.pf_hurdle_p95
            and expectancy_r > h.ev_r_hurdle_p95
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
            hurdles_passed=passed,
            hurdles_failed=failed,
            verdict=verdict,
            is_validated=is_validated,
        )
