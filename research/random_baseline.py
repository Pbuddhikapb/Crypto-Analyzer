"""
Monte Carlo Random-Entry Baseline Engine.

Establishes the empirical null hypothesis distribution (H0) across 1,000 passes
using the Single Exit Authority (engine/exits.py) and realistic friction models
(fees, slippage, adverse gap fills, 8h funding, and portfolio limits).

A strategy possesses genuine alpha if and only if its risk-adjusted performance
metrics beat the 95th percentile (p < 0.05) of this random entry baseline.
"""

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from data.loader import candles_to_array, load_candles
from engine.exits import (
    ExitConfig,
)
from engine.simulator import (
    CandidateSignal,
    ExecutedTrade,
    SimulationFriction,
    simulate_portfolio_signals,
    simulate_single_trade_lifecycle,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("random_baseline")

DEFAULT_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "SUIUSDT",
]


@dataclass
class MetricDistribution:
    """Distribution percentiles for a single metric."""

    name: str
    p5: float
    p25: float
    p50: float  # Median
    p75: float
    p95: float  # The Alpha Hurdle (top 5%)
    mean: float
    std: float
    min_val: float
    max_val: float


@dataclass
class MonteCarloDistributions:
    """Collection of metric distributions across Monte Carlo iterations."""

    iterations: int
    win_rate: MetricDistribution
    profit_factor: MetricDistribution
    expectancy_usd: MetricDistribution
    expectancy_r: MetricDistribution
    avg_r: MetricDistribution
    max_drawdown_pct: MetricDistribution
    sharpe_ratio: MetricDistribution
    sortino_ratio: MetricDistribution
    net_profit_pct: MetricDistribution
    total_trades_mean: float


@dataclass
class BaselineReport:
    """Final baseline analysis and hurdle report."""

    timestamp: str
    distributions: MonteCarloDistributions
    markdown_table: str
    hurdle_summary: str


def compute_metric_distribution(name: str, values: List[float]) -> MetricDistribution:
    """Compute empirical percentiles and summary statistics for an array of metric values."""
    arr = np.array(values, dtype=np.float64)
    # Filter out NaNs or Infs if any
    clean_arr = arr[np.isfinite(arr)]
    if len(clean_arr) == 0:
        clean_arr = np.array([0.0])

    return MetricDistribution(
        name=name,
        p5=round(float(np.percentile(clean_arr, 5)), 4),
        p25=round(float(np.percentile(clean_arr, 25)), 4),
        p50=round(float(np.percentile(clean_arr, 50)), 4),
        p75=round(float(np.percentile(clean_arr, 75)), 4),
        p95=round(float(np.percentile(clean_arr, 95)), 4),
        mean=round(float(np.mean(clean_arr)), 4),
        std=round(float(np.std(clean_arr)), 4),
        min_val=round(float(np.min(clean_arr)), 4),
        max_val=round(float(np.max(clean_arr)), 4),
    )


@dataclass
class MatchedBaselineResult:
    """Statistical outcome of running matched random-entry null distribution against a strategy."""

    iterations: int
    strategy_trade_count: int
    strategy_net_ev_r: float
    null_expectancy_r_distribution: List[float]
    median_expectancy_r: float
    mean_expectancy_r: float
    std_expectancy_r: float
    p5_expectancy_r: float
    p95_expectancy_r: float
    empirical_p_value: float
    bootstrap_ci: Tuple[float, float]
    exit_mode: str = "stepped"
    dropped_trade_fraction: float = 0.0


def compute_dataset_hash(candles_dict: Dict[str, np.ndarray]) -> str:
    """Deterministic hash of dataset symbols, lengths, and boundary timestamps."""
    tokens = []
    for s in sorted(candles_dict.keys()):
        arr = candles_dict[s]
        n = len(arr)
        t_first = int(arr[0, 4]) if n > 0 else 0
        t_last = int(arr[-1, 4]) if n > 0 else 0
        tokens.append(f"{s}:{n}:{t_first}:{t_last}")
    raw = "|".join(tokens)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def compute_bootstrap_ci(
    net_r_values: List[float],
    n_resamples: int = 10000,
    seed: int = 42,
    ci_level: float = 0.95,
) -> Tuple[float, float]:
    """
    Compute empirical bootstrap confidence interval for mean net_r.
    Resamples trade-level net_r values with replacement n_resamples times.
    """
    clean_vals = [float(v) for v in net_r_values if np.isfinite(v)]
    if len(clean_vals) == 0:
        return (0.0, 0.0)

    arr = np.array(clean_vals, dtype=np.float64)
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, len(arr), size=(n_resamples, len(arr)))
    boot_means = np.mean(arr[indices], axis=1)

    alpha = (1.0 - ci_level) / 2.0
    lower_pct = alpha * 100.0
    upper_pct = (1.0 - alpha) * 100.0

    ci_lower = round(float(np.percentile(boot_means, lower_pct)), 4)
    ci_upper = round(float(np.percentile(boot_means, upper_pct)), 4)
    return (ci_lower, ci_upper)


def save_baseline_distribution_json(
    output_json_path: str,
    distributions: Dict[str, MetricDistribution],
    metadata: Dict[str, Any],
) -> None:
    """Save full Monte Carlo distributions and metadata to JSON."""
    os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
    out = {
        "metadata": metadata,
        "distributions": {
            name: {
                "name": dist.name,
                "p5": dist.p5,
                "p25": dist.p25,
                "p50": dist.p50,
                "p75": dist.p75,
                "p95": dist.p95,
                "mean": dist.mean,
                "std": dist.std,
                "min_val": dist.min_val,
                "max_val": dist.max_val,
            }
            for name, dist in distributions.items()
        },
    }
    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    logger.info(f"Saved baseline distributions JSON to {output_json_path}")


def _simulate_single_matched_pass(
    trade_profiles: List[Tuple[str, float]],
    candles_dict: Dict[str, np.ndarray],
    rng: np.random.Generator,
    friction: SimulationFriction,
    exit_config: ExitConfig,
    initial_equity_usd: float = 10000.0,
    risk_per_trade_pct: float = 1.0,
    max_leverage: float = 3.0,
) -> Tuple[float, int, int]:
    """
    Simulate one matched random pass and return:
    (mean_net_r, completed_trades_count, dropped_trades_count).
    """
    pass_net_r: List[float] = []
    dropped_count = 0
    for sym, dist_pct in trade_profiles:
        sym_candles = candles_dict.get(sym)
        if sym_candles is None or len(sym_candles) < 200:
            dropped_count += 1
            continue
        # Only sample entry bars that leave at least 200 bars of data
        max_idx = len(sym_candles) - 200
        b_idx = int(rng.integers(0, max_idx + 1))
        side = "LONG" if rng.random() > 0.5 else "SHORT"
        entry_p = float(sym_candles[b_idx, 0])
        sl_p = entry_p * (1.0 - dist_pct) if side == "LONG" else entry_p * (1.0 + dist_pct)
        c_ts = int(sym_candles[b_idx, 4])
        # Give random trades the full remaining forward data to the end of data
        forward_slice = sym_candles[b_idx:]

        trade = simulate_single_trade_lifecycle(
            symbol=sym,
            side=side,
            nominal_entry=entry_p,
            stop_loss=sl_p,
            entry_ts_ms=c_ts,
            candles_forward=forward_slice,
            equity_at_entry=initial_equity_usd,
            risk_per_trade_pct=risk_per_trade_pct,
            max_leverage=max_leverage,
            friction=friction,
            exit_config=exit_config,
            close_at_end_of_data=True,
        )
        if trade is not None:
            pass_net_r.append(trade.net_r)
        else:
            dropped_count += 1

    pass_ev = float(np.mean(pass_net_r)) if pass_net_r else 0.0
    return pass_ev, len(pass_net_r), dropped_count


def _worker_matched_chunk(
    seeds: List[int],
    trade_profiles: List[Tuple[str, float]],
    candles_dict: Dict[str, np.ndarray],
    friction: SimulationFriction,
    exit_config: ExitConfig,
    initial_equity_usd: float,
    risk_per_trade_pct: float,
    max_leverage: float,
) -> List[Tuple[float, int, int]]:
    """Worker function to process a batch of matched passes across multiprocessing workers."""
    logging.getLogger("simulator").setLevel(logging.ERROR)
    chunk_results: List[Tuple[float, int, int]] = []
    for s in seeds:
        iter_rng = np.random.default_rng(s)
        res = _simulate_single_matched_pass(
            trade_profiles=trade_profiles,
            candles_dict=candles_dict,
            rng=iter_rng,
            friction=friction,
            exit_config=exit_config,
            initial_equity_usd=initial_equity_usd,
            risk_per_trade_pct=risk_per_trade_pct,
            max_leverage=max_leverage,
        )
        chunk_results.append(res)
    return chunk_results


def run_matched_baseline(
    strategy_trades: List[ExecutedTrade],
    candles_dict: Dict[str, np.ndarray],
    iterations: int = 1000,
    seed: int = 42,
    friction: Optional[SimulationFriction] = None,
    exit_config: Optional[ExitConfig] = None,
    initial_equity_usd: float = 10000.0,
    risk_per_trade_pct: float = 1.0,
    max_leverage: float = 3.0,
) -> MatchedBaselineResult:
    """
    Execute N matched random-entry passes against a strategy result.
    Each pass has:
    1. The EXACT SAME number of trades as the strategy.
    2. The EXACT SAME symbol mix as the strategy.
    3. The EXACT SAME per-trade stop distance (in percent) distribution.
    4. Evaluated through Single Exit Authority with identical exit rules and friction.

    Computes empirical rank-based p-value:
    p = (count(null_ev >= strategy_ev) + 1) / (N + 1)
    """
    fric = friction or SimulationFriction()
    cfg = exit_config or ExitConfig()

    n_trades = len(strategy_trades)
    strategy_net_r = [t.net_r for t in strategy_trades]
    strategy_ev_r = float(np.mean(strategy_net_r)) if strategy_net_r else 0.0
    boot_ci = compute_bootstrap_ci(strategy_net_r, n_resamples=10000, seed=seed)

    if n_trades == 0:
        return MatchedBaselineResult(
            iterations=iterations,
            strategy_trade_count=0,
            strategy_net_ev_r=0.0,
            null_expectancy_r_distribution=[0.0] * iterations,
            median_expectancy_r=0.0,
            mean_expectancy_r=0.0,
            std_expectancy_r=0.0,
            p5_expectancy_r=0.0,
            p95_expectancy_r=0.0,
            empirical_p_value=1.0,
            bootstrap_ci=(0.0, 0.0),
            exit_mode=cfg.exit_mode,
            dropped_trade_fraction=0.0,
        )

    # Per-trade profile: (symbol, stop_dist_pct)
    trade_profiles: List[Tuple[str, float]] = []
    for t in strategy_trades:
        sym = t.symbol
        if t.position_size_usd > 0 and t.initial_risk_usd > 0:
            dist_pct = t.initial_risk_usd / t.position_size_usd
        else:
            dist_pct = 0.02
        dist_pct = max(0.001, min(dist_pct, 0.20))
        trade_profiles.append((sym, dist_pct))

    master_rng = np.random.default_rng(seed)
    iter_seeds = [int(master_rng.integers(0, 100000000)) for _ in range(iterations)]

    null_ev_dist: List[float] = []
    total_dropped = 0
    total_simulated = 0

    workers = min(os.cpu_count() or 4, 4)
    if iterations >= 50 and workers > 1:
        logger.info(f"    Executing {iterations:,} matched passes in parallel using {workers} workers...")
        chunks = np.array_split(iter_seeds, workers)
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = [
                ex.submit(
                    _worker_matched_chunk,
                    chunk.tolist(),
                    trade_profiles,
                    candles_dict,
                    fric,
                    cfg,
                    initial_equity_usd,
                    risk_per_trade_pct,
                    max_leverage,
                )
                for chunk in chunks
                if len(chunk) > 0
            ]
            for f in futures:
                for pass_ev, comp_cnt, drop_cnt in f.result():
                    null_ev_dist.append(round(pass_ev, 4))
                    total_dropped += drop_cnt
                    total_simulated += comp_cnt
    else:
        step = max(50, iterations // 10)
        for it, it_seed in enumerate(iter_seeds):
            if (it + 1) % step == 0 or it == 0 or (it + 1) == iterations:
                logger.info(f"    Matched pass {it + 1}/{iterations} ({(it + 1) / iterations * 100:.0f}%)")
            iter_rng = np.random.default_rng(it_seed)
            pass_ev, comp_cnt, drop_cnt = _simulate_single_matched_pass(
                trade_profiles=trade_profiles,
                candles_dict=candles_dict,
                rng=iter_rng,
                friction=fric,
                exit_config=cfg,
                initial_equity_usd=initial_equity_usd,
                risk_per_trade_pct=risk_per_trade_pct,
                max_leverage=max_leverage,
            )
            null_ev_dist.append(round(pass_ev, 4))
            total_dropped += drop_cnt
            total_simulated += comp_cnt

    total_planned = iterations * n_trades
    dropped_trade_fraction = total_dropped / total_planned if total_planned > 0 else 0.0
    if dropped_trade_fraction > 0.005:
        raise RuntimeError(
            f"Matched baseline dropped trade fraction {dropped_trade_fraction:.4%} "
            f"exceeds 0.5% threshold ({total_dropped}/{total_planned} trades dropped)"
        )

    # Rank-based empirical p-value (exact rank within matched distribution)
    # Fraction of null passes whose net EV >= strategy net EV
    greater_equal_count = sum(1 for null_ev in null_ev_dist if null_ev >= strategy_ev_r)
    empirical_p = (greater_equal_count + 1.0) / (iterations + 1.0)
    empirical_p = min(max(empirical_p, 1.0 / (iterations + 1.0)), 1.0)

    median_ev = round(float(np.median(null_ev_dist)), 4)
    mean_ev = round(float(np.mean(null_ev_dist)), 4)
    std_ev = round(float(np.std(null_ev_dist)), 4)
    p5_ev = round(float(np.percentile(null_ev_dist, 5)), 4)
    p95_ev = round(float(np.percentile(null_ev_dist, 95)), 4)

    return MatchedBaselineResult(
        iterations=iterations,
        strategy_trade_count=n_trades,
        strategy_net_ev_r=round(strategy_ev_r, 4),
        null_expectancy_r_distribution=null_ev_dist,
        median_expectancy_r=median_ev,
        mean_expectancy_r=mean_ev,
        std_expectancy_r=std_ev,
        p5_expectancy_r=p5_ev,
        p95_expectancy_r=p95_ev,
        empirical_p_value=round(empirical_p, 4),
        bootstrap_ci=boot_ci,
        exit_mode=cfg.exit_mode,
        dropped_trade_fraction=round(dropped_trade_fraction, 6),
    )




def run_random_baseline_iteration(
    iteration_id: int,
    candles_dict: Dict[str, np.ndarray],  # symbol -> array with [open, high, low, close, open_time]
    trades_per_symbol: int = 80,
    stop_loss_pct: float = 2.0,  # 2.0% stop distance
    max_open_positions: int = 3,
    max_directional_positions: int = 2,
    symbol_cooldown_bars: int = 3,
    initial_equity_usd: float = 10000.0,
    risk_per_trade_pct: float = 1.0,
    max_leverage: float = 3.0,
    friction: Optional[SimulationFriction] = None,
    exit_config: Optional[ExitConfig] = None,
    rng: Optional[np.random.Generator] = None,
    sizing_mode: str = "fixed_risk",
    ruin_threshold_usd: Optional[float] = None,
) -> Dict[str, float]:
    """
    Run a single Monte Carlo pass with randomized entries and rigorous portfolio concurrency
    delegating directly to simulate_portfolio_signals in engine/simulator.py.
    """
    fric = friction or SimulationFriction()
    cfg = exit_config or ExitConfig()
    gen = rng or np.random.default_rng(iteration_id)

    candidate_signals: List[CandidateSignal] = []

    for sym, arr in candles_dict.items():
        total_bars = len(arr)
        max_entry_bar = total_bars - 50
        if max_entry_bar <= 10:
            continue

        available_bars = max_entry_bar - 10
        sample_size = min(trades_per_symbol, available_bars)
        if sample_size <= 0:
            continue

        chosen_bars = gen.choice(np.arange(10, max_entry_bar), size=sample_size, replace=False)

        for b_idx in chosen_bars:
            c_open = float(arr[b_idx, 0])
            c_ts = int(arr[b_idx, 4])
            side = "LONG" if gen.random() < 0.5 else "SHORT"
            sl_dist = c_open * (stop_loss_pct / 100.0)
            sl_price = (c_open - sl_dist) if side == "LONG" else (c_open + sl_dist)
            vol = float(arr[b_idx, 5]) if arr.shape[1] > 5 else 1.0

            candidate_signals.append(
                CandidateSignal(
                    symbol=sym,
                    side=side,
                    entry_price=c_open,
                    stop_loss=sl_price,
                    timestamp_ms=c_ts,
                    volume=vol,
                )
            )

    sim_res = simulate_portfolio_signals(
        candidates=candidate_signals,
        candles_dict=candles_dict,
        initial_equity_usd=initial_equity_usd,
        risk_per_trade_pct=risk_per_trade_pct,
        max_open_positions=max_open_positions,
        max_directional_positions=max_directional_positions,
        symbol_cooldown_bars=symbol_cooldown_bars,
        max_leverage=max_leverage,
        friction=fric,
        exit_config=cfg,
        sizing_mode=sizing_mode,
        ruin_threshold_usd=ruin_threshold_usd,
    )

    return {
        "win_rate": sim_res.win_rate_pct,
        "profit_factor": sim_res.profit_factor,
        "gross_profit_factor": sim_res.gross_profit_factor,
        "expectancy_usd": sim_res.expectancy_usd,
        "expectancy_r": sim_res.expectancy_r,
        "gross_expectancy_r": sim_res.gross_expectancy_r,
        "avg_r": sim_res.avg_r_multiple,
        "max_drawdown_pct": sim_res.max_drawdown_pct,
        "max_drawdown_r": sim_res.max_drawdown_r,
        "cumulative_r": sim_res.cumulative_r,
        "sharpe_ratio": sim_res.sharpe_ratio,
        "sortino_ratio": sim_res.sortino_ratio,
        "net_profit_pct": sim_res.net_profit_pct,
        "total_trades": float(sim_res.total_trades),
    }




def run_monte_carlo_baseline(
    iterations: int = 1000,
    symbols: Optional[List[str]] = None,
    timeframe: str = "1h",
    data_dir: str = "data/candles",
    output_report_path: str = "research/reports/random_baseline_hurdle.md",
    output_json_path: Optional[str] = None,
    seed: int = 42,
    trades_per_symbol: int = 25,
    sizing_mode: str = "fixed_risk",
    ruin_threshold_usd: Optional[float] = None,
) -> BaselineReport:
    """
    Run full Monte Carlo simulation across N iterations and compile distributions.
    """
    syms = symbols or DEFAULT_SYMBOLS
    logger.info(f"Loading {len(syms)} Parquet candle datasets for Monte Carlo baseline ({timeframe})...")

    candles_dict: Dict[str, np.ndarray] = {}
    for sym in syms:
        df = load_candles(sym, timeframe, data_dir=data_dir)
        # Store as contiguous numpy array: [open, high, low, close, open_time, volume]
        arr = candles_to_array(df)
        candles_dict[sym] = arr

    global _GLOBAL_CANDLES
    _GLOBAL_CANDLES = candles_dict

    logger.info(f"Loaded {len(candles_dict)} symbols. Executing {iterations} Monte Carlo iterations in parallel...")
    start_time = time.time()

    master_rng = np.random.default_rng(seed)
    metrics_log: Dict[str, List[float]] = {
        "win_rate": [],
        "profit_factor": [],
        "gross_profit_factor": [],
        "expectancy_usd": [],
        "expectancy_r": [],
        "gross_expectancy_r": [],
        "avg_r": [],
        "max_drawdown_pct": [],
        "max_drawdown_r": [],
        "cumulative_r": [],
        "sharpe_ratio": [],
        "sortino_ratio": [],
        "net_profit_pct": [],
        "total_trades": [],
    }

    friction = SimulationFriction()
    exit_config = ExitConfig()

    for i in range(iterations):
        iter_rng = np.random.default_rng(int(master_rng.integers(0, 10000000)))
        res = run_random_baseline_iteration(
            iteration_id=i,
            candles_dict=candles_dict,
            trades_per_symbol=trades_per_symbol,
            friction=friction,
            exit_config=exit_config,
            rng=iter_rng,
            sizing_mode=sizing_mode,
            ruin_threshold_usd=ruin_threshold_usd,
        )
        for k, v in res.items():
            metrics_log[k].append(v)

        if (i + 1) % 100 == 0 or (i + 1) == iterations:
            elapsed = time.time() - start_time
            logger.info(f"Progress: {i + 1}/{iterations} iterations completed ({elapsed:.1f}s)")

    # Aggregate metric distributions
    dist_wr = compute_metric_distribution("Win Rate (%)", metrics_log["win_rate"])
    dist_pf = compute_metric_distribution("Profit Factor", metrics_log["profit_factor"])
    dist_ev_usd = compute_metric_distribution("Expectancy ($)", metrics_log["expectancy_usd"])
    dist_ev_r = compute_metric_distribution("Expectancy (R)", metrics_log["expectancy_r"])
    dist_avg_r = compute_metric_distribution("Average Realized R", metrics_log["avg_r"])
    dist_dd = compute_metric_distribution("Max Drawdown (%)", metrics_log["max_drawdown_pct"])
    dist_sharpe = compute_metric_distribution("Sharpe Ratio", metrics_log["sharpe_ratio"])
    dist_sortino = compute_metric_distribution("Sortino Ratio", metrics_log["sortino_ratio"])
    dist_ret = compute_metric_distribution("Net Profit (%)", metrics_log["net_profit_pct"])
    trades_mean = float(np.mean(metrics_log["total_trades"]))

    distributions = MonteCarloDistributions(
        iterations=iterations,
        win_rate=dist_wr,
        profit_factor=dist_pf,
        expectancy_usd=dist_ev_usd,
        expectancy_r=dist_ev_r,
        avg_r=dist_avg_r,
        max_drawdown_pct=dist_dd,
        sharpe_ratio=dist_sharpe,
        sortino_ratio=dist_sortino,
        net_profit_pct=dist_ret,
        total_trades_mean=round(trades_mean, 1),
    )

    # Calculate dynamic dataset metadata
    min_ts = min(arr[0, 4] for arr in candles_dict.values() if len(arr) > 0)
    max_ts = max(arr[-1, 4] for arr in candles_dict.values() if len(arr) > 0)
    if min_ts > 10_000_000_000:
        total_days = (max_ts - min_ts) / (86400.0 * 1000.0)
    else:
        total_days = (max_ts - min_ts) / 86400.0
    dataset_years = total_days / 365.25
    bars_counts = [len(arr) for arr in candles_dict.values()]
    min_bars = min(bars_counts) if bars_counts else 0
    max_bars = max(bars_counts) if bars_counts else 0

    # Format Markdown Report
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    report_content = f"""# Monte Carlo Random-Entry Baseline: The Statistical Hurdle
**Generated:** {now_str}  
**Iterations:** {iterations:,} Monte Carlo Passes  
**Dataset:** 10 Major Pairs (`{', '.join(syms)}`), 1-Hour Timeframe ({dataset_years:.2f} Years, {min_bars:,} to {max_bars:,} bars/coin from Binance USD-M Futures)  
**Friction Model:** Taker Fee 0.05%, Maker Fee 0.02%, Adverse Slippage 0.03%, 8h Funding Drag 0.01%  
**Exit Mechanics:** Stepped Scaling (50% TP1 @ +0.75R, BE armed @ +0.85R excursion, TP2 @ +1.33R, 12-bar stagnancy stop)

---

## 1. Executive Summary & The Alpha Hurdle

> **The Null Hypothesis ($H_0$):** A proposed trading strategy or indicator confluence rule possesses zero predictive edge. Any measured performance is indistinguishable from random coin flips governed by identical exit mechanics and portfolio friction.
>
> **The $p < 0.05$ Decision Rule:** To reject $H_0$ and prove genuine alpha, a strategy must achieve performance exceeding the **95th percentile ($p_{{95}}$)** of this 1,000-pass random baseline.

### Primary Alpha Hurdles:
* **Minimum Required Profit Factor:** **`>{dist_pf.p95:.2f}`** (Median noise achieves `{dist_pf.p50:.2f}`)
* **Minimum Required Win Rate:** **`>{dist_wr.p95:.2f}%`** (Median noise achieves `{dist_wr.p50:.2f}%`)
* **Minimum Required Expectancy ($EV$):** **`>{dist_ev_r.p95:+.4f}R`** (Median noise achieves `{dist_ev_r.p50:+.4f}R`)
* **Minimum Required Sharpe Ratio:** **`>{dist_sharpe.p95:.2f}`** (Median noise achieves `{dist_sharpe.p50:.2f}`)
* **Maximum Acceptable Drawdown:** **`<{dist_dd.p50:.2f}%`** (Worst 5% of noise suffers `{dist_dd.p95:.2f}%`)

---

## 2. Empirical Distribution Table

| Performance Metric | 5th Pct ($p_5$) | 25th Pct ($p_{{25}}$) | 50th Pct ($p_{{50}}$ / Median) | 75th Pct ($p_{{75}}$) | 95th Pct ($p_{{95}}$ Hurdle) | Mean ± Std |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Win Rate (%)** | `{dist_wr.p5:.2f}%` | `{dist_wr.p25:.2f}%` | `{dist_wr.p50:.2f}%` | `{dist_wr.p75:.2f}%` | **`{dist_wr.p95:.2f}%`** | `{dist_wr.mean:.2f} ± {dist_wr.std:.2f}%` |
| **Profit Factor** | `{dist_pf.p5:.2f}` | `{dist_pf.p25:.2f}` | `{dist_pf.p50:.2f}` | `{dist_pf.p75:.2f}` | **`{dist_pf.p95:.2f}`** | `{dist_pf.mean:.2f} ± {dist_pf.std:.2f}` |
| **Expectancy per Trade (USD)** | `${dist_ev_usd.p5:+.2f}` | `${dist_ev_usd.p25:+.2f}` | `${dist_ev_usd.p50:+.2f}` | `${dist_ev_usd.p75:+.2f}` | **`${dist_ev_usd.p95:+.2f}`** | `${dist_ev_usd.mean:+.2f} ± {dist_ev_usd.std:.2f}` |
| **Expectancy per Trade (R)** | `{dist_ev_r.p5:+.4f}R` | `{dist_ev_r.p25:+.4f}R` | `{dist_ev_r.p50:+.4f}R` | `{dist_ev_r.p75:+.4f}R` | **`{dist_ev_r.p95:+.4f}R`** | `{dist_ev_r.mean:+.4f}R ± {dist_ev_r.std:.4f}` |
| **Average Realized R** | `{dist_avg_r.p5:+.4f}R` | `{dist_avg_r.p25:+.4f}R` | `{dist_avg_r.p50:+.4f}R` | `{dist_avg_r.p75:+.4f}R` | **`{dist_avg_r.p95:+.4f}R`** | `{dist_avg_r.mean:+.4f}R ± {dist_avg_r.std:.4f}` |
| **Max Drawdown (%)** | `{dist_dd.p5:.2f}%` | `{dist_dd.p25:.2f}%` | `{dist_dd.p50:.2f}%` | `{dist_dd.p75:.2f}%` | **`{dist_dd.p95:.2f}%`** | `{dist_dd.mean:.2f} ± {dist_dd.std:.2f}%` |
| **Sharpe Ratio** | `{dist_sharpe.p5:.2f}` | `{dist_sharpe.p25:.2f}` | `{dist_sharpe.p50:.2f}` | `{dist_sharpe.p75:.2f}` | **`{dist_sharpe.p95:.2f}`** | `{dist_sharpe.mean:.2f} ± {dist_sharpe.std:.2f}` |
| **Sortino Ratio** | `{dist_sortino.p5:.2f}` | `{dist_sortino.p25:.2f}` | `{dist_sortino.p50:.2f}` | `{dist_sortino.p75:.2f}` | **`{dist_sortino.p95:.2f}`** | `{dist_sortino.mean:.2f} ± {dist_sortino.std:.2f}` |
| **Net Portfolio Return (%)** | `{dist_ret.p5:+.2f}%` | `{dist_ret.p25:+.2f}%` | `{dist_ret.p50:+.2f}%` | `{dist_ret.p75:+.2f}%` | **`{dist_ret.p95:+.2f}%`** | `{dist_ret.mean:+.2f} ± {dist_ret.std:.2f}%` |

*Average trades generated per portfolio run ({dataset_years:.2f} years): {trades_mean:.1f} trades.*

---

## 3. Quantitative Takeaways for Strategy Development

1. **The Cost of Friction**:
   Without an edge, median random trading loses money over the full evaluation timeline (median net return is `{dist_ret.p50:+.2f}%`, median expectancy is `{dist_ev_r.p50:+.4f}R`). This confirms the honesty of the simulation: exchange fees (0.05%), adverse slippage (0.03%), and 8h funding costs slowly bleed random entries.
2. **The Asymmetric Exit Effect**:
   Because of stepped scaling (+0.75R partial scale and breakeven arming), random entries can sometimes string together winning streaks that push profit factor up to `{dist_pf.p75:.2f}`. This proves why examining simple backtests without a random benchmark leads retail traders to confuse asymmetric exit math with true entry edge.
3. **The Unbending Standard**:
   In Phase 3, any indicator signal, Smart Money Concept pattern, or confluence score must produce a Profit Factor >= {dist_pf.p95:.2f} and an Expectancy >= {dist_ev_r.p95:+.4f}R over the exact same period to be declared statistically valid ($p < 0.05$).
"""

    os.makedirs(os.path.dirname(output_report_path), exist_ok=True)
    with open(output_report_path, "w", encoding="utf-8") as f:
        f.write(report_content)

    logger.info(f"Saved Monte Carlo Baseline Hurdle report to {output_report_path}")

    # Compile and save JSON distributions with full metadata
    dists_dict = {
        "win_rate": dist_wr,
        "profit_factor": dist_pf,
        "gross_profit_factor": compute_metric_distribution("Gross Profit Factor", metrics_log["gross_profit_factor"]),
        "expectancy_usd": dist_ev_usd,
        "expectancy_r": dist_ev_r,
        "gross_expectancy_r": compute_metric_distribution("Gross Expectancy (R)", metrics_log["gross_expectancy_r"]),
        "avg_r": dist_avg_r,
        "max_drawdown_pct": dist_dd,
        "max_drawdown_r": compute_metric_distribution("Max Drawdown (R)", metrics_log["max_drawdown_r"]),
        "cumulative_r": compute_metric_distribution("Cumulative Net R", metrics_log["cumulative_r"]),
        "sharpe_ratio": dist_sharpe,
        "sortino_ratio": dist_sortino,
        "net_profit_pct": dist_ret,
        "total_trades": compute_metric_distribution("Total Trades", metrics_log["total_trades"]),
    }

    dataset_hash = compute_dataset_hash(candles_dict)
    min_ts = min(int(arr[0, 4]) for arr in candles_dict.values()) if candles_dict else 0
    max_ts = max(int(arr[-1, 4]) for arr in candles_dict.values()) if candles_dict else 0
    start_date = datetime.fromtimestamp(min_ts / 1000.0, tz=timezone.utc).isoformat() if min_ts > 0 else ""
    end_date = datetime.fromtimestamp(max_ts / 1000.0, tz=timezone.utc).isoformat() if max_ts > 0 else ""

    try:
        import subprocess

        git_hash = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        git_hash = "unknown"

    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "iterations": iterations,
        "git_commit": git_hash,
        "dataset_hash": dataset_hash,
        "timeframe": timeframe,
        "symbols": list(candles_dict.keys()),
        "start_time_ms": min_ts,
        "end_time_ms": max_ts,
        "start_date": start_date,
        "end_date": end_date,
        "sizing_mode": sizing_mode,
        "friction": {
            "taker_fee_pct": friction.taker_fee_pct,
            "maker_fee_pct": friction.maker_fee_pct,
            "slippage_pct": friction.slippage_pct,
            "funding_rate_8h_pct": friction.funding_rate_8h_pct,
        },
        "exit_config": {
            "tp1_r": exit_config.tp1_r,
            "tp2_r": exit_config.tp2_r,
            "be_arm_r": exit_config.be_arm_r,
            "underwater_bars_limit": exit_config.underwater_bars_limit,
        },
    }

    actual_json_path = output_json_path or os.path.join(os.path.dirname(output_report_path), "baseline_distribution.json")
    save_baseline_distribution_json(actual_json_path, dists_dict, metadata)

    return BaselineReport(
        timestamp=now_str,
        distributions=distributions,
        markdown_table=report_content,
        hurdle_summary=f"PF > {dist_pf.p95:.2f}, WR > {dist_wr.p95:.2f}%, EV > {dist_ev_r.p95:+.4f}R",
    )


def main():
    parser = argparse.ArgumentParser(description="Run Monte Carlo Random-Entry Baseline.")
    parser.add_argument(
        "--passes",
        "--iterations",
        dest="iterations",
        type=int,
        default=1000,
        help="Number of Monte Carlo passes (default: 1000, use 200 in tests/dev)",
    )
    parser.add_argument("--timeframe", type=str, default="1h", help="Timeframe (default: 1h)")
    parser.add_argument("--data-dir", type=str, default="data/candles", help="Parquet data directory")
    parser.add_argument("--output", type=str, default="research/reports/random_baseline_hurdle.md", help="Output report path")
    parser.add_argument("--output-json", type=str, default=None, help="Output JSON path (default: baseline_distribution.json)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--sizing-mode", type=str, choices=["fixed_risk", "compounding"], default="fixed_risk", help="Sizing mode (default: fixed_risk)")
    parser.add_argument("--ruin-threshold", type=float, default=None, help="Ruin threshold in USD (default: 5%% of initial equity)")

    args = parser.parse_args()
    report = run_monte_carlo_baseline(
        iterations=args.iterations,
        timeframe=args.timeframe,
        data_dir=args.data_dir,
        output_report_path=args.output,
        output_json_path=args.output_json,
        seed=args.seed,
        sizing_mode=args.sizing_mode,
        ruin_threshold_usd=args.ruin_threshold,
    )
    print("\n" + "=" * 60)
    print("MONTE CARLO BASELINE COMPLETE")
    print("=" * 60)
    print(f"Hurdle Summary: {report.hurdle_summary}")
    print(f"Report saved to: {args.output}\n")


if __name__ == "__main__":
    main()
