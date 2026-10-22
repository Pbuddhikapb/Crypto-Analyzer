"""
Strategy Evidence Runner for Phase 3 Validation.

Executes backtests across the 10 major pairs over the full 2-year Parquet dataset
using the Single Exit Authority (engine/exits.py) and realistic institutional friction.
Evaluates every hypothesis against the Phase 2 1,000-pass Monte Carlo Alpha Hurdle (H0)
and produces the formal validation report in research/reports/strategy_validation_report.md.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from data.loader import candles_to_array, load_candles
from engine.exits import ExitConfig
from engine.simulator import (
    CandidateSignal,
    ExecutedTrade,
    SimulationFriction,
    SimulationResult,
    simulate_portfolio_signals,
)
from research.evaluator import BaselineHurdles, StrategyEvaluation, StrategyEvaluator
from research.random_baseline import (
    MatchedBaselineResult,
    compute_bootstrap_ci,
    compute_dataset_hash,
    run_matched_baseline,
)
from research.strategies import (
    BaseStrategy,
    ConfluenceThresholdStrategy,
    Donchian4hN20,
    Donchian4hN55,
    FVGRetestStrategy,
    LiquiditySweepStrategy,
    TrendPullbackStrategy,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("strategy_runner")

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
class StrategyRunResult:
    """Complete portfolio result and per-symbol breakdown for a strategy."""

    strategy_name: str
    description: str
    sim_result: SimulationResult
    evaluation: StrategyEvaluation
    per_symbol_metrics: Dict[str, Dict[str, Any]]
    total_signals_generated: int
    signals_executed: int
    matched_baseline: Optional[MatchedBaselineResult] = None
    bootstrap_ci: Optional[Tuple[float, float]] = None


# simulate_portfolio_signals is imported directly from engine.simulator

def compute_per_symbol_breakdown(trades: List[ExecutedTrade]) -> Dict[str, Dict[str, Any]]:
    """Compute per-symbol metrics for reporting."""
    sym_map: Dict[str, List[ExecutedTrade]] = {}
    for t in trades:
        sym_map.setdefault(t.symbol, []).append(t)

    breakdown: Dict[str, Dict[str, Any]] = {}
    for sym, s_trades in sym_map.items():
        n = len(s_trades)
        wins = [t for t in s_trades if t.net_pnl_usd > 0]
        losses = [t for t in s_trades if t.net_pnl_usd <= 0]
        wr = (len(wins) / n * 100.0) if n > 0 else 0.0

        gp = sum(t.net_pnl_usd for t in wins)
        gl = abs(sum(t.net_pnl_usd for t in losses))
        pf = (gp / gl) if gl > 0 else (999.0 if gp > 0 else 0.0)

        gross_gp = sum(t.gross_pnl_usd for t in s_trades if t.gross_pnl_usd > 0)
        gross_gl = abs(sum(t.gross_pnl_usd for t in s_trades if t.gross_pnl_usd <= 0))
        gross_pf = (gross_gp / gross_gl) if gross_gl > 0 else (999.0 if gross_gp > 0 else 0.0)

        net_pnl = sum(t.net_pnl_usd for t in s_trades)
        avg_r = float(np.mean([t.net_r for t in s_trades])) if s_trades else 0.0
        gross_avg_r = float(np.mean([t.realized_r for t in s_trades])) if s_trades else 0.0

        breakdown[sym] = {
            "trades": n,
            "win_rate": round(wr, 1),
            "profit_factor": round(pf, 2),
            "gross_profit_factor": round(gross_pf, 2),
            "net_pnl": round(net_pnl, 2),
            "avg_r": round(avg_r, 4),
            "gross_avg_r": round(gross_avg_r, 4),
        }
    return breakdown


def run_strategy_evidence_validation(
    symbols: Optional[List[str]] = None,
    data_dir: str = "data/candles",
    output_report_path: str = "research/reports/strategy_validation_report.md",
    output_json_path: str = "research/reports/strategy_results.json",
    sizing_mode: str = "fixed_risk",
    ruin_threshold_usd: Optional[float] = None,
    matched_passes: int = 1000,
) -> List[StrategyRunResult]:
    """
    Main orchestration routine running all 4 hypotheses against the Phase 2 Monte Carlo hurdle.
    """
    syms = symbols or DEFAULT_SYMBOLS
    logger.info(f"Loading 1h and 4h Parquet datasets for {len(syms)} symbols...")

    df_1h_dict: Dict[str, pd.DataFrame] = {}
    df_4h_dict: Dict[str, pd.DataFrame] = {}
    candles_dict: Dict[str, np.ndarray] = {}
    bar_index_map: Dict[str, Dict[int, int]] = {}

    for sym in syms:
        df_1 = load_candles(sym, "1h", data_dir=data_dir)
        df_4 = load_candles(sym, "4h", data_dir=data_dir)
        df_1h_dict[sym] = df_1
        df_4h_dict[sym] = df_4

        arr = candles_to_array(df_1)
        candles_dict[sym] = arr

        # Fast lookup mapping: open_time_ms -> bar_idx
        bar_index_map[sym] = {int(arr[idx, 4]): idx for idx in range(len(arr))}

    logger.info("Loaded datasets. Initializing trading hypotheses...")

    strategies: List[BaseStrategy] = [
        TrendPullbackStrategy(),
        FVGRetestStrategy(),
        ConfluenceThresholdStrategy(),
        LiquiditySweepStrategy(),
        Donchian4hN20(),
        Donchian4hN55(),
    ]

    dataset_hash = compute_dataset_hash(candles_dict)
    fric = SimulationFriction()
    cfg = ExitConfig()
    fric_dict = {
        "taker_fee_pct": fric.taker_fee_pct,
        "maker_fee_pct": fric.maker_fee_pct,
        "slippage_pct": fric.slippage_pct,
        "funding_rate_8h_pct": fric.funding_rate_8h_pct,
    }
    evaluator = StrategyEvaluator(
        expected_dataset_hash=dataset_hash,
        expected_sizing_mode=sizing_mode,
        expected_friction=fric_dict,
    )
    run_results: List[StrategyRunResult] = []

    for strat in strategies:
        s_name = strat.config.name
        s_desc = strat.config.description
        strat_cfg = getattr(strat, "exit_config", cfg)
        strat_sig_level = getattr(strat, "significance_level", 0.05)
        logger.info(f"--- Generating Candidate Signals for Strategy: {s_name} ---")

        all_candidates: List[CandidateSignal] = []
        t0 = time.time()

        for sym in syms:
            sym_sigs = strat.generate_signals(sym, df_1h_dict[sym], df_4h_dict[sym])
            all_candidates.extend(sym_sigs)
            logger.info(f"  [{s_name}] {sym}: {len(sym_sigs)} candidate signals")

        t_gen = time.time() - t0
        logger.info(f"  Total signals for {s_name}: {len(all_candidates)} across {len(syms)} pairs ({t_gen:.2f}s)")

        # Run sequential multi-symbol portfolio simulation
        logger.info(f"  Simulating institutional portfolio execution for {s_name} (sizing: {sizing_mode})...")
        sim_res = simulate_portfolio_signals(
            candidates=all_candidates,
            candles_dict=candles_dict,
            bar_index_map=bar_index_map,
            sizing_mode=sizing_mode,
            ruin_threshold_usd=ruin_threshold_usd,
            exit_config=strat_cfg,
        )

        # Compute bootstrap 95% CI on mean net R (10,000 resamples, seeded)
        trade_net_r = [t.net_r for t in sim_res.trades]
        boot_ci = compute_bootstrap_ci(trade_net_r, n_resamples=10000, seed=42)

        # Run matched random baseline (same trade count, same symbol mix, same stop dist % distribution)
        logger.info(f"  Running matched random-entry baseline for {s_name} (N={matched_passes} passes)...")
        matched_res = run_matched_baseline(
            strategy_trades=sim_res.trades,
            candles_dict=candles_dict,
            iterations=matched_passes,
            seed=42,
            friction=fric,
            exit_config=strat_cfg,
        )

        evaluation = evaluator.evaluate(
            s_name,
            s_desc,
            sim_res,
            matched_baseline=matched_res,
            bootstrap_ci=boot_ci,
            significance_level=strat_sig_level,
        )
        per_sym = compute_per_symbol_breakdown(sim_res.trades)

        matched_p_val = evaluation.matched_p_value if evaluation.matched_p_value is not None else evaluation.p_composite
        if sim_res.sizing_mode == "fixed_risk":
            perf_metric_str = f"MaxDD(R)={sim_res.max_drawdown_r:.2f}R, CumR={sim_res.cumulative_r:+.2f}R"
        else:
            perf_metric_str = f"MaxDD={sim_res.max_drawdown_pct:.1f}%, Return={sim_res.net_profit_pct:+.1f}%"

        logger.info(
            f"  Result for {s_name}: Trades={sim_res.total_trades}, "
            f"WR={sim_res.win_rate_pct:.1f}%, PF={sim_res.profit_factor:.2f}, "
            f"EV={sim_res.expectancy_r:+.4f}R, Sharpe={sim_res.sharpe_ratio:.2f}, "
            f"{perf_metric_str} | "
            f"Matched Rank p-val: {matched_p_val:.4f} | "
            f"Bootstrap 95% CI: [{boot_ci[0]:+.4f}, {boot_ci[1]:+.4f}]R | "
            f"Verdict: {evaluation.verdict}"
        )

        run_results.append(
            StrategyRunResult(
                strategy_name=s_name,
                description=s_desc,
                sim_result=sim_res,
                evaluation=evaluation,
                per_symbol_metrics=per_sym,
                total_signals_generated=len(all_candidates),
                signals_executed=sim_res.total_trades,
                matched_baseline=matched_res,
                bootstrap_ci=boot_ci,
            )
        )

    # Generate and save master report
    if not candles_dict:
        raise ValueError("candles_dict is required and cannot be empty")
    min_ts = min(int(arr[0, 4]) for arr in candles_dict.values())
    max_ts = max(int(arr[-1, 4]) for arr in candles_dict.values())
    dataset_days_val = (max_ts - min_ts) / (86400 * 1000.0)
    dataset_years_val = round(dataset_days_val / 365.25, 2)
    bars_per_symbol_val = {s: len(arr) for s, arr in candles_dict.items()}
    dataset_hash_val = compute_dataset_hash(candles_dict)
    git_hash_val = get_git_commit_hash()
    generate_markdown_report(
        results=run_results,
        output_path=output_report_path,
        baseline_hurdles=evaluator.hurdles,
        dataset_info={
            "symbols": syms,
            "bars_per_symbol": bars_per_symbol_val,
            "days": dataset_days_val,
            "years": dataset_years_val,
        },
        friction=fric,
        exit_config=cfg,
        dataset_hash=dataset_hash_val,
        git_hash=git_hash_val,
    )
    save_strategy_results_json(
        run_results,
        output_json_path,
        candles_dict=candles_dict,
        bars_per_symbol=bars_per_symbol_val,
        dataset_years=dataset_years_val,
    )
    return run_results


def get_git_commit_hash() -> str:
    """Return the current git commit hash (with '+dirty' if working tree has uncommitted changes), or 'unknown'."""
    try:
        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip()
        status = subprocess.check_output(["git", "status", "--porcelain", "-uno"], cwd=repo_root, text=True).strip()
        if status:
            return f"{commit}+dirty"
        return commit
    except Exception:
        return "unknown"


def save_strategy_results_json(
    results: List[StrategyRunResult],
    output_json_path: str,
    candles_dict: Optional[Dict[str, np.ndarray]] = None,
    bars_per_symbol: Optional[Dict[str, int]] = None,
    dataset_years: Optional[float] = None,
) -> None:
    """Save machine-readable strategy validation outcomes to JSON."""
    os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    git_hash = get_git_commit_hash()

    min_ts = 0
    max_ts = 0
    if candles_dict:
        min_ts = min(int(arr[0, 4]) for arr in candles_dict.values())
        max_ts = max(int(arr[-1, 4]) for arr in candles_dict.values())
        if bars_per_symbol is None:
            bars_per_symbol = {s: len(arr) for s, arr in candles_dict.items()}
        if dataset_years is None:
            dataset_days = (max_ts - min_ts) / (86400 * 1000.0)
            dataset_years = round(dataset_days / 365.25, 2)
    else:
        if bars_per_symbol is None or dataset_years is None:
            raise ValueError("candles_dict or (bars_per_symbol and dataset_years) is required and cannot be missing")

    if bars_per_symbol is None or not bars_per_symbol:
        raise ValueError("bars_per_symbol is required in strategy_results.json metadata and cannot be missing or empty")
    if dataset_years is None or dataset_years <= 0:
        raise ValueError("dataset_years is required in strategy_results.json metadata and cannot be missing or non-positive")

    start_date_str = datetime.fromtimestamp(min_ts / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d") if min_ts > 0 else ""
    end_date_str = datetime.fromtimestamp(max_ts / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d") if max_ts > 0 else ""
    date_range_str = f"{start_date_str} to {end_date_str}" if start_date_str and end_date_str else ""

    strategies_data = {}
    for r in results:
        ev = r.evaluation
        sim = r.sim_result
        matched_p = ev.matched_p_value if ev.matched_p_value is not None else ev.p_composite
        boot_ci = [round(r.bootstrap_ci[0], 4), round(r.bootstrap_ci[1], 4)] if r.bootstrap_ci else None

        strategies_data[r.strategy_name] = {
            "strategy_name": r.strategy_name,
            "description": r.description,
            "trade_count": r.signals_executed,
            "net_win_rate": round(sim.win_rate_pct, 2),
            "net_profit_factor": round(sim.profit_factor, 2),
            "net_expectancy_r": round(sim.expectancy_r, 4),
            "gross_profit_factor": round(sim.gross_profit_factor, 2),
            "gross_expectancy_r": round(sim.gross_expectancy_r, 4),
            "bootstrap_ci": boot_ci,
            "matched_p_value": round(matched_p, 4) if matched_p is not None else None,
            "verdict": ev.verdict,
            "sizing_mode": sim.sizing_mode,
            "ruined": sim.ruined,
            "ruin_time_ms": sim.ruin_time_ms,
            "cumulative_r": round(sim.cumulative_r, 4),
            "max_drawdown_r": round(sim.max_drawdown_r, 4),
            "days_evaluated": round(sim.days_evaluated, 1),
            "pct_of_dataset_evaluated": round(sim.pct_of_dataset_evaluated, 1),
            "sharpe_ratio": round(sim.sharpe_ratio, 2),
            "max_drawdown_pct": round(sim.max_drawdown_pct, 2),
            "net_profit_pct": round(sim.net_profit_pct, 2),
            "start_time_ms": min_ts,
            "end_time_ms": max_ts,
            "run_timestamp": now_iso,
            "data_date_range": date_range_str,
            "git_commit": git_hash,
            "significance_level": getattr(ev, "significance_level", 0.05),
            "median_stop_pct": round(ev.med_stop_pct, 2) if getattr(ev, "med_stop_pct", None) is not None else None,
            "cost_drag_r": round(ev.cost_drag_r, 4) if getattr(ev, "cost_drag_r", None) is not None else None,
            "median_holding_bars": round(ev.med_holding_bars, 1) if getattr(ev, "med_holding_bars", None) is not None else None,
            "trades_per_symbol": getattr(ev, "trades_per_symbol", None),
            "per_year_net_r": {str(k): round(v, 2) for k, v in ev.per_year_net_r.items()} if getattr(ev, "per_year_net_r", None) else None,
            "dropped_trade_fraction": round(getattr(ev, "dropped_trade_fraction", 0.0), 6),
            "p_value_at_floor": getattr(ev, "p_value_at_floor", False),
        }

    dataset_hash = compute_dataset_hash(candles_dict) if candles_dict else "unknown"

    payload = {
        "metadata": {
            "run_timestamp": now_iso,
            "git_commit": git_hash,
            "dataset_hash": dataset_hash,
            "data_date_range": date_range_str,
            "start_time_ms": min_ts,
            "end_time_ms": max_ts,
            "bars_per_symbol": bars_per_symbol,
            "dataset_years": dataset_years,
        },
        "strategies": strategies_data,
    }

    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    logger.info(f"Saved machine-readable strategy results to {output_json_path}")


def generate_markdown_report(
    results: List[StrategyRunResult],
    output_path: str,
    baseline_hurdles: Optional[BaselineHurdles] = None,
    dataset_info: Optional[Dict[str, Any]] = None,
    friction: Optional[SimulationFriction] = None,
    exit_config: Optional[ExitConfig] = None,
    dataset_hash: Optional[str] = None,
    git_hash: Optional[str] = None,
) -> None:
    """Format publication-grade Strategy Validation Report."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Load baseline hurdles if not provided
    hurdles = baseline_hurdles
    if hurdles is None:
        try:
            hurdles = BaselineHurdles.load()
        except Exception:
            hurdles = None

    mc_iterations = int(hurdles.metadata.get("iterations", 1000)) if hurdles and hurdles.metadata else 1000
    pf_hurdle_p95 = hurdles.pf_hurdle_p95 if hurdles else 1.16
    pf_null_mean = hurdles.pf_null_mean if hurdles else 0.93
    wr_hurdle_p95 = hurdles.wr_hurdle_p95 if hurdles else 51.43
    wr_null_mean = hurdles.wr_null_mean if hurdles else 46.13
    ev_hurdle_p95 = hurdles.ev_r_hurdle_p95 if hurdles else 0.0602
    ev_null_mean = hurdles.ev_r_null_mean if hurdles else -0.0239
    sharpe_hurdle_p95 = hurdles.sharpe_hurdle_p95 if hurdles else 0.22
    sharpe_null_mean = hurdles.sharpe_null_mean if hurdles else -1.35
    dd_hurdle_p50 = hurdles.dd_hurdle_p50 if hurdles else 22.48
    dd_hurdle_p95 = hurdles.dd_hurdle_p95 if hurdles else 36.11

    fric = friction or SimulationFriction()
    taker_fee_pct = fric.taker_fee_pct
    maker_fee_pct = fric.maker_fee_pct
    slippage_pct = fric.slippage_pct
    funding_rate_pct = fric.funding_rate_8h_pct
    funding_hours = 8

    cfg = exit_config or ExitConfig()
    tp1_scale_pct = 50.0
    tp1_r = getattr(cfg, "tp1_r", 0.75)
    be_arm_r = getattr(cfg, "be_arm_r", 0.85)
    tp2_r = getattr(cfg, "tp2_r", 1.33)
    stagnant_bars = getattr(cfg, "underwater_bars_limit", 12)

    alpha_sig_level = 0.05
    boot_resamples_n = 10000

    # Dataset metadata
    if dataset_info:
        symbols_list = dataset_info.get("symbols")
        if not symbols_list:
            raise ValueError("symbols list is missing from dataset_info")
        total_symbols = len(symbols_list)
        dataset_days_val = dataset_info.get("days")
        if dataset_days_val is None:
            raise ValueError("dataset days is missing from dataset_info")
        dataset_days = float(dataset_days_val)
        bars_per_symbol = dataset_info.get("bars_per_symbol")
        if not bars_per_symbol:
            raise ValueError("bars_per_symbol is missing or empty in dataset_info")
        dataset_years_val = dataset_info.get("years")
        if dataset_years_val is None:
            dataset_years = dataset_days / 365.25
        else:
            dataset_years = float(dataset_years_val)
    elif results and results[0].per_symbol_metrics:
        symbols_list = sorted(list(results[0].per_symbol_metrics.keys()))
        total_symbols = len(symbols_list)
        dataset_days = float(results[0].sim_result.days_evaluated)
        if dataset_days <= 0:
            raise ValueError("days_evaluated must be positive when dataset_info is not provided")
        dataset_years = dataset_days / 365.25
        bars_per_symbol = {s: int(round(dataset_days * 24.0)) for s in symbols_list}
    else:
        raise ValueError("Cannot determine dataset parameters: dataset_info or results with per_symbol_metrics required")

    num_strategies = len(results)

    # Scorecard rows
    scorecard_rows = []
    for r in results:
        ev = r.evaluation
        badge = "🟢 **VALIDATED**" if ev.is_validated else "🔴 **REJECTED**"
        ruin_str = (
            f"⚠️ Ruined ({datetime.fromtimestamp(r.sim_result.ruin_time_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')})"
            if r.sim_result.ruined and r.sim_result.ruin_time_ms
            else ("Ruined" if r.sim_result.ruined else "No")
        )
        ci_str = f"[{r.bootstrap_ci[0]:+.4f}, {r.bootstrap_ci[1]:+.4f}]R" if r.bootstrap_ci else "N/A"
        matched_med_str = f"{r.matched_baseline.median_expectancy_r:+.4f}R" if r.matched_baseline else "N/A"
        matched_p_str = f"`p = {ev.matched_p_value:.4f}`" if ev.matched_p_value is not None else f"`p = {ev.p_composite:.4f}`"
        dropped_frac = getattr(ev, "dropped_trade_fraction", 0.0)
        dropped_str = f"{dropped_frac:.2%}"

        eval_days_str = f"{r.sim_result.days_evaluated:.0f}d ({r.sim_result.pct_of_dataset_evaluated:.1f}%)"
        row = (
            f"| **{ev.strategy_name}** | {ev.total_trades} | {ev.win_rate:.1f}% | "
            f"**{r.sim_result.profit_factor:.2f}** ({r.sim_result.gross_profit_factor:.2f}) | "
            f"**{r.sim_result.expectancy_r:+.4f}R** ({r.sim_result.gross_expectancy_r:+.4f}R) | "
            f"{r.sim_result.max_drawdown_r:.2f}R | "
            f"{ci_str} | {matched_med_str} | {matched_p_str} | {dropped_str} | "
            f"{eval_days_str} | `{r.sim_result.sizing_mode}` | {ruin_str} | {badge} |"
        )
        scorecard_rows.append(row)

    scorecard_table = "\n".join(scorecard_rows)

    # Detailed sections
    deep_dives = []
    for r in results:
        ev = r.evaluation
        sym_lines = []
        for sym, sm in sorted(r.per_symbol_metrics.items()):
            gross_pf_val = sm.get("gross_profit_factor", sm["profit_factor"])
            gross_r_val = sm.get("gross_avg_r", sm["avg_r"])
            sym_lines.append(
                f"| `{sym}` | {sm['trades']} | {sm['win_rate']:.1f}% | {sm['profit_factor']:.2f} ({gross_pf_val:.2f}) | "
                f"${sm['net_pnl']:+,.2f} | {sm['avg_r']:+.4f}R ({gross_r_val:+.4f}R) |"
            )
        sym_table = "\n".join(sym_lines)

        passed_list = "\n".join([f"- ✅ {p}" for p in ev.hurdles_passed]) or "- None"
        failed_list = "\n".join([f"- ❌ {f}" for f in ev.hurdles_failed]) or "- None"

        ruin_info = (
            f"YES (at {datetime.fromtimestamp(r.sim_result.ruin_time_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})"
            if r.sim_result.ruined and r.sim_result.ruin_time_ms
            else ("YES" if r.sim_result.ruined else "NO")
        )
        ci_str = f"[{r.bootstrap_ci[0]:+.4f}R, {r.bootstrap_ci[1]:+.4f}R]" if r.bootstrap_ci else "N/A"
        matched_med_str = f"{r.matched_baseline.median_expectancy_r:+.4f}R" if r.matched_baseline else "N/A"
        matched_p95_str = f"{r.matched_baseline.p95_expectancy_r:+.4f}R" if r.matched_baseline else "N/A"
        matched_p_display = f"p = {ev.matched_p_value:.4f}" if ev.matched_p_value is not None else f"p = {ev.p_composite:.4f}"
        matched_passes_n = r.matched_baseline.iterations if r.matched_baseline else 1000
        drop_pct_str = f"{getattr(ev, 'dropped_trade_fraction', 0.0):.2%}"
        floor_warning_str = ""
        if getattr(ev, "p_value_at_floor", False):
            floor_p = 1.0 / (matched_passes_n + 1.0)
            floor_warning_str = (
                f"\n> ⚠️ **Warning: Rank p-value ({matched_p_display}) equals minimum floor 1/(N+1) = {floor_p:.4f}. "
                "Null-sanity check required: check for survivorship or execution bias in the null distribution.**"
            )

        diag_list = "\n".join([f"- ℹ️ {d}" for d in ev.diagnostics]) or "- None"
        verdict_banner = (
            f"> **Statistical Verdict**: **`{ev.verdict}`**  \n"
            f"> Matched Rank $p$-value: **`{matched_p_display}`** (Empirical rank within {matched_passes_n:,} matched null passes)  \n"
            f"> Matched Null Dropped Trade Share: **`{drop_pct_str}`**  \n"
            f"> Total Candidate Signals: `{r.total_signals_generated}` | Executed Trades: `{r.signals_executed}`  \n"
            f"> Net Profit Factor: **`{r.sim_result.profit_factor:.2f}`** (Gross: `{r.sim_result.gross_profit_factor:.2f}`)  \n"
            f"> Net Expectancy: **`{r.sim_result.expectancy_r:+.4f}R`** (Gross: `{r.sim_result.gross_expectancy_r:+.4f}R`)  \n"
            f"> Max Drawdown (R): **`{r.sim_result.max_drawdown_r:.2f}R`** | Cumulative Net R: **`{r.sim_result.cumulative_r:+.2f}R`**  \n"
            f"> Days Evaluated: **`{r.sim_result.days_evaluated:.0f} days ({r.sim_result.pct_of_dataset_evaluated:.1f}% of dataset)`**  \n"
            f"> Bootstrap 95% CI (Mean Net R): **`{ci_str}`** ({boot_resamples_n:,} resamples)  \n"
            f"> Matched Baseline Median Net EV: **`{matched_med_str}`** (Matched 95th Pct Hurdle: `{matched_p95_str}`)  \n"
            f"> Sizing Mode: `{r.sim_result.sizing_mode}` | Account Ruined: `{ruin_info}`"
            f"{floor_warning_str}"
        )

        section = f"""### Hypothesis: {ev.strategy_name}
**Concept:** {r.description}

{verdict_banner}

#### Alpha Validation Requirements:
**Passed:**
{passed_list}

**Failed / Unmet:**
{failed_list}

#### Diagnostics:
{diag_list}

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
{sym_table}

---"""
        deep_dives.append(section)

    deep_dives_content = "\n\n".join(deep_dives)

    # Dynamic conclusions generated strictly from results
    validated_strats = [r for r in results if r.evaluation.is_validated]
    rejected_strats = [r for r in results if not r.evaluation.is_validated]

    if not validated_strats:
        conclusions_body = (
            "1. **Evidence-Based Discretionary Radar Filtering**:\n"
            "   No strategy passed; no pattern in this report has validated edge. "
            "Under rigorous institutional friction, all tested hypotheses exhibit negative net expectancy or fail "
            "to exceed random-entry baseline distributions at the statistical significance threshold. "
            "Consequently, the Discretionary Trading Terminal must not display unverified automated trade recommendations.\n\n"
            "2. **Fee and Slippage Drag Reality**:\n"
            f"   Execution friction ({taker_fee_pct:.2f}% taker fee, {slippage_pct:.2f}% slippage, "
            f"and {funding_rate_pct:.2f}% {funding_hours}h funding drag) decisively turns gross-positive or marginal setups into net losses. "
            "Identifying patterns that possess zero predictive edge under real market conditions prevents capital destruction from retail chart illusions.\n\n"
            "3. **Capital Preservation Protocol**:\n"
            "   Because no setup demonstrates a statistically validated edge against matched noise, the terminal operates in "
            "evidence-status and risk-calculator mode only, providing trade structure and sizing analytics without directional endorsement."
        )
    else:
        passed_bullets = "\n".join([
            f"   - **{r.strategy_name}**: Net EV = `{r.sim_result.expectancy_r:+.4f}R` (Gross: `{r.sim_result.gross_expectancy_r:+.4f}R`), "
            f"Net PF = `{r.sim_result.profit_factor:.2f}`, Bootstrap 95% CI = `[{r.bootstrap_ci[0]:+.4f}, {r.bootstrap_ci[1]:+.4f}]R`, "
            f"Matched Rank $p$-value = `{r.evaluation.matched_p_value:.4f}`"
            for r in validated_strats
        ])
        rejected_names = ", ".join([f"**{r.strategy_name}**" for r in rejected_strats]) or "None"
        conclusions_body = (
            f"1. **Validated Setups ({len(validated_strats)}/{num_strategies})**:\n"
            f"   The following strategies met all alpha validation criteria ($p < {alpha_sig_level:.2f}$, positive bootstrap CI lower bound):\n"
            f"{passed_bullets}\n\n"
            f"2. **Unvalidated Patterns**:\n"
            f"   The following strategies failed validation: {rejected_names}. These setups show no validated edge under institutional friction and will display warning banners.\n\n"
            "3. **Evidence-Backed Radar Screening**:\n"
            "   Only configurations meeting strict statistical criteria are highlighted as candidate setups on the Discretionary Radar."
        )

    # Dynamic Limitations Section
    sample_size_bullets = "\n".join([
        f"   - **{r.strategy_name}**: {r.signals_executed:,} executed trades "
        f"({r.sim_result.days_evaluated:.0f} days evaluated, {r.sim_result.pct_of_dataset_evaluated:.1f}% of dataset)"
        for r in results
    ])
    primary_sizing_mode = results[0].sim_result.sizing_mode if results else "fixed_risk"

    limitations_section = f"""## 5. Limitations

1. **Position Sizing Mode (`{primary_sizing_mode}`)**:
   Evaluations are performed under `{primary_sizing_mode}` position sizing. In fixed-risk mode, risk is calibrated to initial equity without compounding, ensuring trade-level statistical edge is measured uniformly across the full dataset period without sample truncation from ruin guards.
2. **Sample Size Evaluated Per Strategy**:
{sample_size_bullets}
3. **No Out-of-Sample Holdout**:
   No holdout dataset has been used yet (until Prompt 7 is done). All metrics reflect in-sample backtesting across the complete historical timeline.
4. **Gross vs. Net Metrics**:
   Both gross P&L (pre-friction) and net P&L (post-fees, post-slippage, and post-funding) are reported side-by-side. The comparison reveals the decisive impact of transaction costs and funding drag on theoretical retail chart patterns.
"""

    if isinstance(bars_per_symbol, dict):
        ordered_syms = [s for s in symbols_list if s in bars_per_symbol] + [s for s in sorted(bars_per_symbol.keys()) if s not in symbols_list]
        bar_counts_str = ", ".join(f"{s}: {bars_per_symbol[s]:,}" for s in ordered_syms)
        max_bars = max(bars_per_symbol.values()) if bars_per_symbol else 0
        sui_bars = bars_per_symbol.get("SUIUSDT")
        gap_coins = [s for s, c in bars_per_symbol.items() if s != "SUIUSDT" and c < max_bars]
    else:
        bar_counts_str = f"{bars_per_symbol:,} bars"
        max_bars = int(bars_per_symbol)
        sui_bars = None
        gap_coins = []

    continuity_bullets = []
    if sui_bars is not None:
        continuity_bullets.append(
            f"  - **SUIUSDT starts later:** SUI USD-M Perpetual contract was listed on Binance on 2023-05-03 16:00 UTC ({sui_bars:,} bars vs {max_bars:,} bars full period)."
        )
    if gap_coins:
        gap_coins_str = ", ".join(sorted(gap_coins))
        continuity_bullets.append(
            f"  - **Coins with maintenance gaps ({gap_coins_str}):** 120 bars missing in downloaded archive, cause unverified (73h: 2022-02-25 23:00 to 2022-03-01 00:00 UTC, and 49h: 2022-03-31 23:00 to 2022-04-03 00:00 UTC)."
        )
    elif "SOLUSDT" in symbols_list or "XRPUSDT" in symbols_list:
        gap_coins_found = [s for s in ["SOLUSDT", "XRPUSDT"] if s in symbols_list]
        gap_coins_str = ", ".join(gap_coins_found)
        continuity_bullets.append(
            f"  - **Coins with maintenance gaps ({gap_coins_str}):** 120 bars missing in downloaded archive, cause unverified (73h: 2022-02-25 23:00 to 2022-03-01 00:00 UTC, and 49h: 2022-03-31 23:00 to 2022-04-03 00:00 UTC)."
        )

    continuity_section = ""
    if continuity_bullets:
        continuity_section = "* **Data Continuity & Coverage Notes:**\n" + "\n".join(continuity_bullets) + "\n"

    git_commit_hash = git_hash or get_git_commit_hash()
    ds_hash = dataset_hash or "unknown"

    report_content = f"""# Strategy Validation Dossier: Evidence Before Infrastructure
**Generated:** {now_str}  
**Git Commit:** `{git_commit_hash}`  
**Dataset Hash:** `{ds_hash}`  
**Architecture Doctrine:** Claude's Option B (Discretionary Human Trading Terminal)  
**Null Hypothesis ($H_0$):** Phase 2 {mc_iterations:,}-pass Monte Carlo Baseline Hurdle  
**Dataset:** {total_symbols} Major Pairs (`{', '.join(symbols_list)}`), {dataset_years:.1f} Years (Binance USD-M Futures 1h candles)  
* **Per-Symbol 1h Bar Counts:** {bar_counts_str}  
{continuity_section}**Execution Authority:** Single Exit Authority (`engine/exits.py`)  
**Institutional Friction:** Taker Fee {taker_fee_pct:.2f}%, Maker Fee {maker_fee_pct:.2f}%, Slippage {slippage_pct:.2f}%, {funding_hours}h Funding Drag {funding_rate_pct:.2f}%  
**Exit Mechanics:** Stepped Scaling ({tp1_scale_pct:.0f}% TP1 @ {tp1_r:+.2f}R, BE armed @ {be_arm_r:+.2f}R excursion, TP2 @ {tp2_r:+.2f}R, {stagnant_bars}-bar stagnancy)

---

## 1. Executive Summary & Big Picture

Under Claude's doctrine (*"Evidence before infrastructure"*), no trading signal or indicator rule is permitted to enter the Discretionary Trading Terminal without empirical proof that it outperforms random noise ($p < {alpha_sig_level:.2f}$).

In Phase 2, we established the empirical null hypothesis ($H_0$) across {mc_iterations:,} Monte Carlo runs on Binance USD-M Futures under full institutional friction:
* **Profit Factor Hurdle ($p_{{95}}$):** `> {pf_hurdle_p95:.2f}` (Median noise: `{pf_null_mean:.2f}`)
* **Win Rate Hurdle ($p_{{95}}$):** `> {wr_hurdle_p95:.2f}%` (Median noise: `{wr_null_mean:.2f}%`)
* **Expectancy Hurdle ($p_{{95}}$):** `> {ev_hurdle_p95:+.4f}R` (Median noise: `{ev_null_mean:+.4f}R`)
* **Sharpe Ratio Hurdle ($p_{{95}}$):** `> {sharpe_hurdle_p95:.2f}` (Median noise: `{sharpe_null_mean:.2f}`)
* **Max Drawdown Hurdle ($p_{{50}}$):** `< {dd_hurdle_p50:.2f}%` (Worst 5% noise: `{dd_hurdle_p95:.2f}%`)

{num_strategies} core discretionary trading hypotheses were codified with strict zero lookahead (signal at bar $i$ close $\\rightarrow$ entry at bar $i+1$ open) and simulated across all {total_symbols} coins.

---

## 2. Master Strategy Scorecard

| Strategy | Trades | Win Rate | Net PF (Gross) | Net EV (Gross EV) | Max DD (R) | Bootstrap 95% CI | Matched Median EV | Matched Rank $p$-value | Dropped Null % | Days Evaluated | Sizing | Ruined? | Alpha Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
{scorecard_table}

---

## 3. Deep-Dive Strategy Evaluations

{deep_dives_content}

## 4. Strategic Implications for Discretionary Terminal

{conclusions_body}

---

{limitations_section}
"""

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_content)

    logger.info(f"Master Strategy Validation Report successfully written to {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Run Phase 3 Strategy Evidence Validation.")
    parser.add_argument("--data-dir", type=str, default="data/candles", help="Parquet candle directory")
    parser.add_argument(
        "--output",
        type=str,
        default="research/reports/strategy_validation_report.md",
        help="Output report markdown path",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default="research/reports/strategy_results.json",
        help="Output strategy results JSON path",
    )
    parser.add_argument(
        "--sizing-mode",
        type=str,
        choices=["fixed_risk", "compounding"],
        default="fixed_risk",
        help="Position sizing mode (default: fixed_risk)",
    )
    parser.add_argument(
        "--ruin-threshold",
        type=float,
        default=None,
        help="Ruin threshold in USD (default: 5%% of initial equity)",
    )
    parser.add_argument(
        "--passes",
        "--matched-passes",
        dest="matched_passes",
        type=int,
        default=1000,
        help="Number of matched baseline passes (default: 1000, use 200 in tests/dev)",
    )
    args = parser.parse_args()

    results = run_strategy_evidence_validation(
        data_dir=args.data_dir,
        output_report_path=args.output,
        output_json_path=args.output_json,
        sizing_mode=args.sizing_mode,
        ruin_threshold_usd=args.ruin_threshold,
        matched_passes=args.matched_passes,
    )

    print("\n" + "=" * 80)
    print("PHASE 3 STRATEGY EVIDENCE VALIDATION COMPLETE")
    print("=" * 80)
    for r in results:
        ev = r.evaluation
        ci_str = f"[{r.bootstrap_ci[0]:+.4f}, {r.bootstrap_ci[1]:+.4f}]R" if r.bootstrap_ci else "N/A"
        matched_med = f"{r.matched_baseline.median_expectancy_r:+.4f}R" if r.matched_baseline else "N/A"
        matched_p_val = ev.matched_p_value if ev.matched_p_value is not None else ev.p_composite
        eval_days = f"{r.sim_result.days_evaluated:.0f}d ({r.sim_result.pct_of_dataset_evaluated:.1f}%)"
        print(
            f"[{ev.verdict}] {ev.strategy_name:<22} | Trades: {ev.total_trades:>4} | "
            f"WR: {ev.win_rate:>5.1f}% | PF: {ev.profit_factor:>4.2f} | "
            f"EV: {ev.expectancy_r:>+7.4f}R | MaxDD(R): {r.sim_result.max_drawdown_r:>5.2f}R | "
            f"95% CI: {ci_str} | Matched Med: {matched_med} | Matched p-val: {matched_p_val:.4f} | "
            f"Days: {eval_days}"
        )
    print("=" * 80)
    print(f"Validation dossier saved to: {args.output}\n")


if __name__ == "__main__":
    main()
