"""
Robustness Panel Module for Strategy Evidence Validation.

Computes report-only robustness diagnostics for every evaluated trading strategy:
(a) Matched rank p with Monte Carlo standard error sqrt(p(1-p)/N), flagged when the threshold is within 2 SE;
(b) iid bootstrap 2.5% bound over 30 seeds (share above zero);
(c) Week-block bootstrap 95% CI (accounting for temporal clustering and concurrent exposure);
(d) Mean net R and n per calendar year;
(e) Top-10 trades' share of total R and mean without them;
(f) Per-trade standard deviation and trades needed for 80% statistical power.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math
import statistics
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from engine.simulator import ExecutedTrade


@dataclass
class RobustnessPanel:
    strategy_name: str
    total_trades: int
    mean_net_r: float
    # (a) Matched rank p & Monte Carlo SE
    matched_p: Optional[float]
    mc_iterations: int
    mc_se: Optional[float]
    significance_threshold: float
    threshold_within_2se: bool
    # (b) iid bootstrap 2.5% bound over 30 seeds
    iid_boot_share_above_zero: float
    iid_boot_min_bound_2_5pct: float
    iid_boot_max_bound_2_5pct: float
    iid_boot_mean_bound_2_5pct: float
    iid_boot_bounds_30_seeds: List[float]
    # (c) Week-block bootstrap 95% CI
    week_block_ci: Tuple[float, float]
    # (d) Annual calendar breakdown
    annual_breakdown: Dict[int, Dict[str, Any]]
    # (e) Outlier dependence (top 10 trades)
    total_net_r: float
    top10_share_pct: float
    top10_total_r: float
    mean_net_r_without_top10: float
    # (f) Dispersion and power analysis
    std_net_r: float
    trades_needed_80_power_onesided: Optional[int]
    trades_needed_80_power_twosided: Optional[int]

    def to_dict(self) -> Dict[str, Any]:
        """Convert robustness panel metrics to a JSON-serializable dictionary."""
        d = asdict(self)
        d["week_block_ci"] = [round(self.week_block_ci[0], 4), round(self.week_block_ci[1], 4)]
        # Convert integer keys in annual_breakdown to strings for JSON
        d["annual_breakdown"] = {str(k): v for k, v in self.annual_breakdown.items()}
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RobustnessPanel":
        """Reconstruct a RobustnessPanel instance from a dictionary."""
        annual = {}
        for k, v in d.get("annual_breakdown", {}).items():
            try:
                annual[int(k)] = v
            except (ValueError, TypeError):
                annual[k] = v
        wb_ci_raw = d.get("week_block_ci", [0.0, 0.0])
        wb_ci = (float(wb_ci_raw[0]), float(wb_ci_raw[1]))
        return cls(
            strategy_name=d["strategy_name"],
            total_trades=int(d["total_trades"]),
            mean_net_r=float(d["mean_net_r"]),
            matched_p=float(d["matched_p"]) if d.get("matched_p") is not None else None,
            mc_iterations=int(d.get("mc_iterations", 1000)),
            mc_se=float(d["mc_se"]) if d.get("mc_se") is not None else None,
            significance_threshold=float(d.get("significance_threshold", 0.05)),
            threshold_within_2se=bool(d.get("threshold_within_2se", False)),
            iid_boot_share_above_zero=float(d.get("iid_boot_share_above_zero", 0.0)),
            iid_boot_min_bound_2_5pct=float(d.get("iid_boot_min_bound_2_5pct", 0.0)),
            iid_boot_max_bound_2_5pct=float(d.get("iid_boot_max_bound_2_5pct", 0.0)),
            iid_boot_mean_bound_2_5pct=float(d.get("iid_boot_mean_bound_2_5pct", 0.0)),
            iid_boot_bounds_30_seeds=[float(x) for x in d.get("iid_boot_bounds_30_seeds", [])],
            week_block_ci=wb_ci,
            annual_breakdown=annual,
            total_net_r=float(d.get("total_net_r", 0.0)),
            top10_share_pct=float(d.get("top10_share_pct", 0.0)),
            top10_total_r=float(d.get("top10_total_r", 0.0)),
            mean_net_r_without_top10=float(d.get("mean_net_r_without_top10", 0.0)),
            std_net_r=float(d.get("std_net_r", 0.0)),
            trades_needed_80_power_onesided=int(d["trades_needed_80_power_onesided"]) if d.get("trades_needed_80_power_onesided") is not None else None,
            trades_needed_80_power_twosided=int(d["trades_needed_80_power_twosided"]) if d.get("trades_needed_80_power_twosided") is not None else None,
        )

    def format_markdown(self) -> str:
        """Format the robustness panel as a clean GitHub-flavored markdown block."""
        mc_se_str = f"{self.mc_se:.4f}" if self.mc_se is not None else "N/A"
        matched_p_str = f"{self.matched_p:.4f}" if self.matched_p is not None else "N/A"
        flag_str = (
            "⚠️ **YES (p is within 2 MC SE of threshold)**"
            if self.threshold_within_2se
            else "No (threshold outside 2 SE)"
        )

        annual_rows = []
        for yr, stats in sorted(self.annual_breakdown.items()):
            annual_rows.append(
                f"| {yr} | {stats['n']:,} | {stats['win_rate_pct']:.1f}% | "
                f"{stats['mean_net_r']:+.4f}R | {stats['total_net_r']:+.2f}R |"
            )
        annual_table = "\n".join(annual_rows) if annual_rows else "| None | - | - | - | - |"

        power_one_str = f"{self.trades_needed_80_power_onesided:,}" if self.trades_needed_80_power_onesided else "N/A (mean <= 0)"
        power_two_str = f"{self.trades_needed_80_power_twosided:,}" if self.trades_needed_80_power_twosided else "N/A (mean <= 0)"

        return f"""#### Robustness Panel: {self.strategy_name}
*(Report-only diagnostics; does not modify pre-registered hypothesis verdict)*

| Diagnostic Metric | Value | Reference / Standard |
| :--- | :--- | :--- |
| **Matched $p$-value** | `{matched_p_str}` (N={self.mc_iterations:,}) | Gate threshold $\\alpha = {self.significance_threshold}$ |
| **Monte Carlo Standard Error** | `SE = {mc_se_str}` | $\\sqrt{{p(1-p)/N}}$ |
| **Threshold within 2 SE?** | {flag_str} | $|p - \\alpha| \\le 2 \\times SE$ |
| **iid Bootstrap 2.5% Bound (30 seeds)** | **`{self.iid_boot_share_above_zero:.1%}`** above zero | Min: `{self.iid_boot_min_bound_2_5pct:+.4f}R`, Max: `{self.iid_boot_max_bound_2_5pct:+.4f}R` |
| **Week-Block Bootstrap 95% CI** | `[{self.week_block_ci[0]:+.4f}, {self.week_block_ci[1]:+.4f}]R` | 7-day block temporal resampling |
| **Top-10 Outlier Share of Total R** | **`{self.top10_share_pct:.1f}%`** (`{self.top10_total_r:+.2f}R` / `{self.total_net_r:+.2f}R`) | Lower indicates broader distribution |
| **Mean Net R Without Top-10 Outliers** | **`{self.mean_net_r_without_top10:+.4f}R`** (vs `{self.mean_net_r:+.4f}R` all) | Baseline expectancy excluding windfalls |
| **Per-Trade Standard Deviation ($s$)** | `{self.std_net_r:.4f}R` | Sample trade dispersion ($ddof=1$) |
| **Trades for 80% Power (One-sided $\\alpha$)** | `{power_one_str}` | $N = \\lceil ((z_{{1-\\alpha}} + z_{{0.80}}) s / \\mu)^2 \\rceil$ |
| **Trades for 80% Power (Two-sided $\\alpha=0.05$)** | `{power_two_str}` | $N = \\lceil ((1.960 + 0.842) s / \\mu)^2 \\rceil$ |

**Calendar Year Performance Breakdown**:
| Year | Trades ($n$) | Win Rate | Mean Net R | Total Net R |
| :--- | :---: | :---: | :---: | :---: |
{annual_table}
"""


def compute_week_block_bootstrap_ci(
    trades: Sequence[ExecutedTrade],
    n_resamples: int = 10000,
    seed: int = 42,
    ci_level: float = 0.95,
    block_duration_ms: int = 7 * 24 * 3600 * 1000,
) -> Tuple[float, float]:
    """
    Compute 95% confidence interval for mean net R using weekly block bootstrap.
    Groups trades into contiguous 7-day chronological blocks to preserve within-week
    cross-symbol and temporal correlation.
    """
    valid_trades = [t for t in trades if np.isfinite(t.net_r)]
    if not valid_trades:
        return (0.0, 0.0)

    min_ts = min(t.entry_time_ms for t in valid_trades)
    max_ts = max(t.entry_time_ms for t in valid_trades)
    num_blocks = int(math.ceil((max_ts - min_ts + 1) / block_duration_ms))
    num_blocks = max(num_blocks, 1)

    block_sums = np.zeros(num_blocks, dtype=np.float64)
    block_counts = np.zeros(num_blocks, dtype=np.int64)

    for t in valid_trades:
        b_idx = int((t.entry_time_ms - min_ts) // block_duration_ms)
        b_idx = min(max(b_idx, 0), num_blocks - 1)
        block_sums[b_idx] += float(t.net_r)
        block_counts[b_idx] += 1

    rng = np.random.default_rng(seed)
    sampled_indices = rng.integers(0, num_blocks, size=(n_resamples, num_blocks))

    # Vectorized sum across sampled blocks
    boot_sums = np.sum(block_sums[sampled_indices], axis=1)
    boot_counts = np.sum(block_counts[sampled_indices], axis=1)

    # Avoid division by zero in any rare degenerate resample
    valid_mask = boot_counts > 0
    boot_means = np.where(valid_mask, boot_sums / np.maximum(boot_counts, 1), 0.0)

    alpha = (1.0 - ci_level) / 2.0
    lower_pct = alpha * 100.0
    upper_pct = (1.0 - alpha) * 100.0

    ci_lower = round(float(np.percentile(boot_means, lower_pct)), 4)
    ci_upper = round(float(np.percentile(boot_means, upper_pct)), 4)
    return (ci_lower, ci_upper)


def compute_iid_bootstrap_seeds(
    net_r_values: Sequence[float],
    num_seeds: int = 30,
    resamples_per_seed: int = 2000,
    base_seed: int = 42,
    quantile_pct: float = 2.5,
) -> Tuple[float, float, float, float, List[float]]:
    """
    Compute 2.5% bootstrap lower bound across multiple seeds.
    Returns:
    (share_above_zero, min_bound, max_bound, mean_bound, list_of_bounds)
    """
    clean_vals = [float(v) for v in net_r_values if np.isfinite(v)]
    if not clean_vals:
        return (0.0, 0.0, 0.0, 0.0, [0.0] * num_seeds)

    arr = np.array(clean_vals, dtype=np.float64)
    n = len(arr)
    bounds: List[float] = []

    for s_idx in range(num_seeds):
        rng = np.random.default_rng(base_seed + s_idx)
        idx_mat = rng.integers(0, n, size=(resamples_per_seed, n))
        means = np.mean(arr[idx_mat], axis=1)
        q = round(float(np.percentile(means, quantile_pct)), 4)
        bounds.append(q)

    above_zero_count = sum(1 for b in bounds if b > 0.0)
    share_above_zero = round(above_zero_count / float(num_seeds), 4)
    min_b = min(bounds)
    max_b = max(bounds)
    mean_b = round(float(np.mean(bounds)), 4)

    return (share_above_zero, min_b, max_b, mean_b, bounds)


def compute_annual_breakdown(trades: Sequence[ExecutedTrade]) -> Dict[int, Dict[str, Any]]:
    """Compute trade count, win rate, mean net R, and total net R per calendar year."""
    by_year: Dict[int, List[float]] = {}
    for t in trades:
        if not np.isfinite(t.net_r):
            continue
        yr = datetime.fromtimestamp(t.entry_time_ms / 1000.0, tz=timezone.utc).year
        by_year.setdefault(yr, []).append(float(t.net_r))

    breakdown: Dict[int, Dict[str, Any]] = {}
    for yr in sorted(by_year.keys()):
        vals = by_year[yr]
        n = len(vals)
        mean_r = float(np.mean(vals)) if n > 0 else 0.0
        tot_r = float(np.sum(vals)) if n > 0 else 0.0
        wins = sum(1 for v in vals if v > 0.0)
        wr = (wins / n * 100.0) if n > 0 else 0.0
        breakdown[yr] = {
            "n": n,
            "mean_net_r": round(mean_r, 4),
            "total_net_r": round(tot_r, 2),
            "win_rate_pct": round(wr, 1),
        }
    return breakdown


def compute_outlier_impact(
    net_r_values: Sequence[float],
    top_n: int = 10,
) -> Tuple[float, float, float, float]:
    """
    Compute total net R, top-10 share of total R, top-10 total R, and mean net R without top 10.
    Returns: (total_net_r, top_n_share_pct, top_n_total_r, mean_without_top_n)
    """
    clean_vals = [float(v) for v in net_r_values if np.isfinite(v)]
    if not clean_vals:
        return (0.0, 0.0, 0.0, 0.0)

    total_r = round(float(sum(clean_vals)), 4)
    sorted_vals = sorted(clean_vals, reverse=True)
    top_vals = sorted_vals[:top_n]
    top_total_r = round(float(sum(top_vals)), 4)

    if total_r != 0.0:
        top_share_pct = round((top_total_r / total_r) * 100.0, 2)
    else:
        top_share_pct = 0.0

    rem_vals = sorted_vals[top_n:]
    if rem_vals:
        mean_without = round(float(np.mean(rem_vals)), 4)
    else:
        mean_without = 0.0

    return (total_r, top_share_pct, top_total_r, mean_without)


def compute_sample_power(
    mean_net_r: float,
    std_net_r: float,
    significance_level: float,
    target_power: float = 0.80,
) -> Tuple[Optional[int], Optional[int]]:
    """
    Calculate sample size needed for target statistical power (default 80%).
    Returns: (trades_needed_onesided, trades_needed_twosided)
    If mean_net_r <= 0 or std_net_r <= 0, returns (None, None).
    """
    if mean_net_r <= 0.0 or std_net_r <= 0.0:
        return (None, None)

    dist = statistics.NormalDist()
    z_beta = dist.inv_cdf(target_power)

    # One-sided test at alpha
    z_alpha_one = dist.inv_cdf(1.0 - significance_level)
    n_one = int(math.ceil(((z_alpha_one + z_beta) * std_net_r / mean_net_r) ** 2))

    # Two-sided test at alpha
    z_alpha_two = dist.inv_cdf(1.0 - (significance_level / 2.0))
    n_two = int(math.ceil(((z_alpha_two + z_beta) * std_net_r / mean_net_r) ** 2))

    return (n_one, n_two)


def compute_robustness_panel(
    strategy_name: str,
    trades: Sequence[ExecutedTrade],
    matched_p: Optional[float] = None,
    mc_iterations: int = 10000,
    significance_level: float = 0.05,
) -> RobustnessPanel:
    """
    Orchestrate full computation of all report-only robustness panel diagnostics.
    """
    clean_r = [float(t.net_r) for t in trades if np.isfinite(t.net_r)]
    n_trades = len(clean_r)
    mean_r = round(float(np.mean(clean_r)), 4) if n_trades > 0 else 0.0
    std_r = round(float(np.std(clean_r, ddof=1)), 4) if n_trades > 1 else 0.0

    # (a) Matched rank p & Monte Carlo SE
    if matched_p is not None and mc_iterations > 0:
        p_clamped = min(max(float(matched_p), 0.0), 1.0)
        mc_se = round(math.sqrt(p_clamped * (1.0 - p_clamped) / mc_iterations), 4)
        within_2se = abs(p_clamped - significance_level) <= (2.0 * mc_se)
    else:
        mc_se = None
        within_2se = False

    # (b) iid bootstrap 2.5% bound over 30 seeds
    share_above, min_b, max_b, mean_b, bounds = compute_iid_bootstrap_seeds(clean_r, num_seeds=30)

    # (c) Week-block bootstrap 95% CI
    wb_ci = compute_week_block_bootstrap_ci(trades, n_resamples=10000, seed=42)

    # (d) Annual calendar breakdown
    annual = compute_annual_breakdown(trades)

    # (e) Outlier dependence (top 10 trades)
    tot_r, top10_pct, top10_r, mean_without = compute_outlier_impact(clean_r, top_n=10)

    # (f) Dispersion and power analysis
    n_power_one, n_power_two = compute_sample_power(mean_r, std_r, significance_level, target_power=0.80)

    return RobustnessPanel(
        strategy_name=strategy_name,
        total_trades=n_trades,
        mean_net_r=mean_r,
        matched_p=matched_p,
        mc_iterations=mc_iterations,
        mc_se=mc_se,
        significance_threshold=significance_level,
        threshold_within_2se=within_2se,
        iid_boot_share_above_zero=share_above,
        iid_boot_min_bound_2_5pct=min_b,
        iid_boot_max_bound_2_5pct=max_b,
        iid_boot_mean_bound_2_5pct=mean_b,
        iid_boot_bounds_30_seeds=bounds,
        week_block_ci=wb_ci,
        annual_breakdown=annual,
        total_net_r=tot_r,
        top10_share_pct=top10_pct,
        top10_total_r=top10_r,
        mean_net_r_without_top10=mean_without,
        std_net_r=std_r,
        trades_needed_80_power_onesided=n_power_one,
        trades_needed_80_power_twosided=n_power_two,
    )
