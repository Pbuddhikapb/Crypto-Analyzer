"""
Strategy Evidence Runner for Phase 3 Validation.

Executes backtests across the 10 major pairs over the full development Parquet dataset
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

from data.loader import candles_to_array, load_all_funding_rates, load_candles
from engine.exits import ExitConfig
from engine.simulator import (
    CandidateSignal,
    ExecutedTrade,
    SimulationFriction,
    SimulationResult,
    simulate_portfolio_signals,
)
from research.evaluator import BaselineHurdles, StrategyEvaluation, StrategyEvaluator
from research.robustness import RobustnessPanel, compute_robustness_panel
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
from research.universes import (
    DEFAULT_UNIVERSE,
    get_universe_symbols,
    validate_universe_data,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("strategy_runner")

DEFAULT_SYMBOLS = get_universe_symbols(DEFAULT_UNIVERSE)


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
    robustness_panel: Optional[RobustnessPanel] = None


def strategy_run_result_from_dict(strat_data: Dict[str, Any]) -> StrategyRunResult:
    """Reconstruct a StrategyRunResult from serialized strategy_results.json entry."""
    s_name = strat_data["strategy_name"]
    s_desc = strat_data.get("description", "")
    trade_count = int(strat_data.get("trade_count", 0))
    verdict = strat_data.get("verdict", "REJECTED")
    is_validated = "PASSED" in verdict

    sim_res = SimulationResult(
        initial_equity_usd=10000.0,
        final_equity_usd=10000.0,
        net_profit_usd=0.0,
        net_profit_pct=float(strat_data.get("net_profit_pct", 0.0)),
        total_trades=trade_count,
        winning_trades=int(round(trade_count * float(strat_data.get("net_win_rate", 0.0)) / 100.0)),
        losing_trades=trade_count - int(round(trade_count * float(strat_data.get("net_win_rate", 0.0)) / 100.0)),
        win_rate_pct=float(strat_data.get("net_win_rate", 0.0)),
        gross_profit_usd=0.0,
        gross_loss_usd=0.0,
        profit_factor=float(strat_data.get("net_profit_factor", 0.0)),
        expectancy_usd=0.0,
        expectancy_r=float(strat_data.get("net_expectancy_r", 0.0)),
        avg_r_multiple=float(strat_data.get("net_expectancy_r", 0.0)),
        max_drawdown_pct=float(strat_data.get("max_drawdown_pct", 0.0)),
        max_drawdown_duration_bars=0,
        sharpe_ratio=float(strat_data.get("sharpe_ratio", 0.0)),
        sortino_ratio=0.0,
        total_fees_usd=0.0,
        total_slippage_usd=0.0,
        total_funding_cost_usd=0.0,
        trades=[],
        equity_curve=[],
        ruined=bool(strat_data.get("ruined", False)),
        ruin_time_ms=strat_data.get("ruin_time_ms"),
        sizing_mode=strat_data.get("sizing_mode", "fixed_risk"),
        gross_profit_factor=float(strat_data.get("gross_profit_factor", 0.0)),
        gross_expectancy_r=float(strat_data.get("gross_expectancy_r", 0.0)),
        cumulative_r=float(strat_data.get("cumulative_r", 0.0)),
        max_drawdown_r=float(strat_data.get("max_drawdown_r", 0.0)),
        days_evaluated=float(strat_data.get("days_evaluated", 0.0)),
        pct_of_dataset_evaluated=float(strat_data.get("pct_of_dataset_evaluated", 100.0)),
        dropped_trade_fraction=float(strat_data.get("dropped_trade_fraction", 0.0)),
        funding_fallback_periods=int(strat_data.get("funding_fallback_periods", 0)),
        funding_total_periods=int(strat_data.get("funding_total_periods", 0)),
    )

    ev = StrategyEvaluation(
        strategy_name=s_name,
        description=s_desc,
        total_trades=trade_count,
        win_rate=float(strat_data.get("net_win_rate", 0.0)),
        profit_factor=float(strat_data.get("net_profit_factor", 0.0)),
        expectancy_r=float(strat_data.get("net_expectancy_r", 0.0)),
        expectancy_usd=0.0,
        avg_r=float(strat_data.get("net_expectancy_r", 0.0)),
        sharpe_ratio=float(strat_data.get("sharpe_ratio", 0.0)),
        sortino_ratio=0.0,
        max_drawdown_pct=float(strat_data.get("max_drawdown_pct", 0.0)),
        net_profit_pct=float(strat_data.get("net_profit_pct", 0.0)),
        p_profit_factor=0.0,
        p_win_rate=0.0,
        p_expectancy_r=0.0,
        p_sharpe=0.0,
        p_composite=float(strat_data.get("matched_p_value", 1.0) if strat_data.get("matched_p_value") is not None else 1.0),
        matched_p_value=float(strat_data.get("matched_p_value")) if strat_data.get("matched_p_value") is not None else None,
        is_validated=is_validated,
        verdict=verdict,
        hurdles_passed=strat_data.get("hurdles_passed", []),
        hurdles_failed=strat_data.get("hurdles_failed", []),
        diagnostics=strat_data.get("diagnostics", []),
        significance_level=float(strat_data.get("significance_level", 0.05)),
        dropped_trade_fraction=float(strat_data.get("dropped_trade_fraction", 0.0)),
        p_value_at_floor=bool(strat_data.get("p_value_at_floor", False)),
        med_stop_pct=strat_data.get("median_stop_pct"),
        cost_drag_r=strat_data.get("cost_drag_r"),
        med_holding_bars=strat_data.get("median_holding_bars"),
        trades_per_symbol=strat_data.get("trades_per_symbol"),
        per_year_net_r=strat_data.get("per_year_net_r"),
    )

    rob_panel = None
    if "robustness_panel" in strat_data and strat_data["robustness_panel"]:
        rob_panel = RobustnessPanel.from_dict(strat_data["robustness_panel"])

    matched_passes_n = rob_panel.mc_iterations if rob_panel else 1000
    matched_baseline = MatchedBaselineResult(
        iterations=matched_passes_n,
        strategy_trade_count=trade_count,
        strategy_net_ev_r=float(strat_data.get("net_expectancy_r", 0.0)),
        null_expectancy_r_distribution=[],
        median_expectancy_r=float(strat_data.get("matched_median_expectancy_r", 0.0)),
        mean_expectancy_r=0.0,
        std_expectancy_r=float(strat_data.get("matched_std_expectancy_r", 0.0)),
        p5_expectancy_r=0.0,
        p95_expectancy_r=float(strat_data.get("matched_p95_expectancy_r", 0.0)),
        empirical_p_value=float(strat_data.get("matched_p_value", 1.0) if strat_data.get("matched_p_value") is not None else 1.0),
        bootstrap_ci=tuple(strat_data["bootstrap_ci"]) if strat_data.get("bootstrap_ci") else (0.0, 0.0),
        dropped_trade_fraction=float(strat_data.get("dropped_trade_fraction", 0.0)),
    )

    boot_ci = tuple(strat_data["bootstrap_ci"]) if strat_data.get("bootstrap_ci") else None
    per_symbol = strat_data.get("per_symbol_metrics", {})
    total_signals = int(strat_data.get("total_signals_generated", trade_count))

    return StrategyRunResult(
        strategy_name=s_name,
        description=s_desc,
        sim_result=sim_res,
        evaluation=ev,
        per_symbol_metrics=per_symbol,
        total_signals_generated=total_signals,
        signals_executed=trade_count,
        matched_baseline=matched_baseline,
        bootstrap_ci=boot_ci,
        robustness_panel=rob_panel,
    )


def update_log_md(
    log_path: str,
    matched_p: float,
    mc_se: float,
    null_median: float,
    null_std: float,
    is_passed: bool,
    threshold_within_2se: bool,
    strategy_name: str = "Donchian4hN55",
    iterations: int = 10000,
) -> None:
    """Append the definitive outcome for a strategy evaluation to research/preregistration/LOG.md."""
    if not os.path.exists(log_path):
        raise FileNotFoundError(f"{log_path} not found")

    with open(log_path, "r", encoding="utf-8") as f:
        existing = f.read().strip()

    hypo_map = {
        "Donchian4hN55": "H1b",
        "Donchian4hN20": "H1a",
    }
    hypo_label = hypo_map.get(strategy_name, strategy_name)
    tag = f"{hypo_label} ({strategy_name})" if hypo_label != strategy_name else strategy_name

    thresh = 0.025 if strategy_name == "Donchian4hN55" else 0.05
    p_2se_below = (matched_p + 2.0 * mc_se) < thresh

    if is_passed:
        entry = (
            f"{tag}: {iterations:,} matched baseline passes completed. "
            f"matched p = {matched_p:.4f}, Monte Carlo SE = {mc_se:.4f}, "
            f"null median = {null_median:+.4f}R, null std = {null_std:.4f}R. "
            f"p + 2*SE = {matched_p + 2.0 * mc_se:.4f} (below {thresh}: {p_2se_below}). "
            f"Verdict: PASSED DEV GATE (see robustness) (p < {thresh}, threshold={thresh}). "
            f"Holdout pending."
        )
    else:
        entry = (
            f"{tag}: {iterations:,} matched baseline passes completed. "
            f"matched p = {matched_p:.4f}, Monte Carlo SE = {mc_se:.4f}, "
            f"null median = {null_median:+.4f}R, null std = {null_std:.4f}R. "
            f"p + 2*SE = {matched_p + 2.0 * mc_se:.4f} (below {thresh}: {p_2se_below}). "
            f"Verdict: REJECTED per pre-registered rule (p = {matched_p:.4f} >= {thresh}). "
            f"Thresholds were not adjusted. Holdout not used."
        )

    # Avoid duplicate append if identical entry exists
    if entry not in existing:
        new_content = existing + "\n" + entry + "\n"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        logger.info(f"Appended entry to {log_path}:\n  {entry}")
    else:
        logger.info("LOG.md already contains this entry.")


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
    universe_name: str = DEFAULT_UNIVERSE,
    only_strategy: Optional[str] = None,
    data_dir: str = "data/candles",
    funding_dir: str = "data/funding",
    output_report_path: str = "research/reports/strategy_validation_report.md",
    output_json_path: str = "research/reports/strategy_results.json",
    log_md_path: str = "research/preregistration/LOG.md",
    sizing_mode: str = "fixed_risk",
    ruin_threshold_usd: Optional[float] = None,
    matched_passes: int = 1000,
    workers: Optional[int] = None,
    checkpoint_path: Optional[str] = None,
) -> List[StrategyRunResult]:
    """
    Main orchestration routine running strategy evidence validation against the Monte Carlo hurdle.
    Supports evaluating all hypotheses or an isolated strategy (--only), and named universes (--universe).
    """
    if symbols is not None:
        syms = list(symbols)
    else:
        syms = get_universe_symbols(universe_name)

    logger.info(f"Validating candle data for universe '{universe_name}' ({len(syms)} symbols)...")
    validate_universe_data(universe_name, syms, data_dir=data_dir)

    logger.info(f"Loading 1h and 4h Parquet datasets for {len(syms)} symbols in universe '{universe_name}'...")

    df_1h_dict: Dict[str, pd.DataFrame] = {}
    df_4h_dict: Dict[str, pd.DataFrame] = {}
    candles_dict: Dict[str, np.ndarray] = {}
    bar_index_map: Dict[str, Dict[int, int]] = {}

    for sym in syms:
        df_1 = load_candles(sym, "1h", data_dir=data_dir)
        df_4 = load_candles(sym, "4h", data_dir=data_dir)
        if df_1.empty or df_4.empty:
            raise ValueError(
                f"Symbol '{sym}' in universe '{universe_name}' has empty candle data. Never skipping silently."
            )
        df_1h_dict[sym] = df_1
        df_4h_dict[sym] = df_4

        arr = candles_to_array(df_1)
        candles_dict[sym] = arr

        # Fast lookup mapping: open_time_ms -> bar_idx
        bar_index_map[sym] = {int(arr[idx, 4]): idx for idx in range(len(arr))}

    f_dir = funding_dir
    if funding_dir == "data/funding" and data_dir != "data/candles":
        candidate_fdir = os.path.join(os.path.dirname(data_dir), "funding")
        if os.path.exists(candidate_fdir):
            f_dir = candidate_fdir
    funding_rates = load_all_funding_rates(syms, data_dir=f_dir, allow_holdout=False)
    if funding_rates:
        logger.info(f"Loaded funding rate history for {len(funding_rates)} symbols from '{f_dir}'.")
    else:
        logger.warning(f"No funding rate archives found in '{f_dir}'; flat fallback rate will apply.")

    logger.info("Loaded datasets. Initializing trading hypotheses...")

    all_strategies: List[BaseStrategy] = [
        TrendPullbackStrategy(),
        FVGRetestStrategy(),
        ConfluenceThresholdStrategy(),
        LiquiditySweepStrategy(),
        Donchian4hN20(),
        Donchian4hN55(),
    ]

    if only_strategy:
        target = only_strategy.strip().lower()
        strategies = [
            s for s in all_strategies
            if s.config.name.lower() == target
            or s.__class__.__name__.lower() == target
            or s.config.name.lower().replace("_", "") == target.replace("_", "")
        ]
        if not strategies:
            avail = [s.config.name for s in all_strategies]
            raise ValueError(
                f"Strategy '{only_strategy}' not found. Available strategies: {', '.join(avail)}"
            )
        logger.info(f"Running ONLY strategy: {strategies[0].config.name} (passes: {matched_passes:,})")
    else:
        strategies = all_strategies

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
            funding_rates=funding_rates,
        )

        # Compute bootstrap 95% CI on mean net R (10,000 resamples, seeded)
        trade_net_r = [t.net_r for t in sim_res.trades]
        boot_ci = compute_bootstrap_ci(trade_net_r, n_resamples=10000, seed=42)

        # Matched baseline checkpoint and workers
        strat_ckpt = checkpoint_path
        if strat_ckpt is None:
            default_ckpt = f"research/checkpoints/{s_name.lower()}_{matched_passes}_matched.json"
            alt_ckpt = f"research/checkpoints/{s_name}_{matched_passes}_matched.json"
            legacy_ckpt = (
                "research/checkpoints/n55_10k_matched.json"
                if (s_name == "Donchian4hN55" and matched_passes == 10000)
                else None
            )
            if legacy_ckpt and os.path.exists(legacy_ckpt):
                strat_ckpt = legacy_ckpt
            elif os.path.exists(alt_ckpt):
                strat_ckpt = alt_ckpt
            else:
                strat_ckpt = default_ckpt

        num_workers = workers if workers is not None else (os.cpu_count() or 4)

        logger.info(
            f"  Running matched random-entry baseline for {s_name} (N={matched_passes} passes, "
            f"workers={num_workers}, checkpoint={strat_ckpt})..."
        )
        matched_res = run_matched_baseline(
            strategy_trades=sim_res.trades,
            candles_dict=candles_dict,
            iterations=matched_passes,
            seed=42,
            friction=fric,
            exit_config=strat_cfg,
            workers=num_workers,
            checkpoint_path=strat_ckpt,
            chunk_size=250,
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

        # Compute report-only robustness panel
        rob_panel = compute_robustness_panel(
            strategy_name=s_name,
            trades=sim_res.trades,
            matched_p=matched_p_val,
            mc_iterations=matched_passes,
            significance_level=strat_sig_level,
        )

        flag_note = " (⚠️ WITHIN 2 SE OF THRESHOLD)" if rob_panel.threshold_within_2se else ""
        mc_se_str = f"{rob_panel.mc_se:.4f}" if rob_panel.mc_se is not None else "N/A"

        logger.info(
            f"  Result for {s_name}: Trades={sim_res.total_trades}, "
            f"WR={sim_res.win_rate_pct:.1f}%, PF={sim_res.profit_factor:.2f}, "
            f"EV={sim_res.expectancy_r:+.4f}R, Sharpe={sim_res.sharpe_ratio:.2f}, "
            f"{perf_metric_str} | "
            f"Matched Rank p-val: {matched_p_val:.4f} (MC SE: {mc_se_str}{flag_note}) | "
            f"Bootstrap 95% CI: [{boot_ci[0]:+.4f}, {boot_ci[1]:+.4f}]R | "
            f"Verdict: {evaluation.verdict}"
        )
        logger.info(
            f"  Robustness for {s_name}: iid 2.5% bound > 0: {rob_panel.iid_boot_share_above_zero:.1%} | "
            f"Week-Block 95% CI: [{rob_panel.week_block_ci[0]:+.4f}, {rob_panel.week_block_ci[1]:+.4f}]R | "
            f"Top-10 Share: {rob_panel.top10_share_pct:.1f}% (mean w/o: {rob_panel.mean_net_r_without_top10:+.4f}R) | "
            f"80% Power Trades: {rob_panel.trades_needed_80_power_onesided or 'N/A'}"
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
                robustness_panel=rob_panel,
            )
        )

    # If only_strategy was specified, merge with existing strategies from strategy_results.json
    if only_strategy and os.path.exists(output_json_path):
        combined_results: List[StrategyRunResult] = list(run_results)
        evaluated_names = {r.strategy_name for r in run_results}
        try:
            with open(output_json_path, "r", encoding="utf-8") as f:
                existing_json = json.load(f)
            existing_strats = existing_json.get("strategies", {})
            for name, s_data in existing_strats.items():
                if name not in evaluated_names:
                    combined_results.append(strategy_run_result_from_dict(s_data))
        except Exception as e:
            logger.warning(f"Could not load existing strategies from {output_json_path}: {e}")

        canonical_order = [
            "TrendPullback",
            "FVGRetest",
            "ConfluenceThreshold",
            "LiquiditySweep",
            "Donchian4hN20",
            "Donchian4hN55",
        ]
        combined_results.sort(
            key=lambda r: canonical_order.index(r.strategy_name) if r.strategy_name in canonical_order else 99
        )
        report_results = combined_results
    else:
        report_results = run_results

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
        results=report_results,
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
        universe_name=universe_name,
    )
    save_strategy_results_json(
        report_results,
        output_json_path,
        candles_dict=candles_dict,
        bars_per_symbol=bars_per_symbol_val,
        dataset_years=dataset_years_val,
        universe_name=universe_name,
    )

    # If only_strategy was specified, update LOG.md through update_log_md
    if only_strategy and os.path.exists(log_md_path):
        for r in run_results:
            ev = r.evaluation
            m_res = r.matched_baseline
            m_p = ev.matched_p_value if ev.matched_p_value is not None else 1.0
            rob = r.robustness_panel
            se_val = rob.mc_se if rob and rob.mc_se is not None else 0.0
            med_r = m_res.median_expectancy_r if m_res else 0.0
            std_r = m_res.std_expectancy_r if m_res and hasattr(m_res, "std_expectancy_r") else 0.0
            update_log_md(
                log_path=log_md_path,
                matched_p=m_p,
                mc_se=se_val,
                null_median=med_r,
                null_std=std_r,
                is_passed=ev.is_validated,
                threshold_within_2se=rob.threshold_within_2se if rob else False,
                strategy_name=r.strategy_name,
                iterations=matched_passes,
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
    universe_name: str = DEFAULT_UNIVERSE,
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

        matched_med = 0.0
        if r.matched_baseline and hasattr(r.matched_baseline, "median_expectancy_r"):
            try:
                matched_med = round(float(r.matched_baseline.median_expectancy_r), 4)
            except (TypeError, ValueError):
                matched_med = 0.0

        matched_p95 = 0.0
        if r.matched_baseline and hasattr(r.matched_baseline, "p95_expectancy_r"):
            try:
                matched_p95 = round(float(r.matched_baseline.p95_expectancy_r), 4)
            except (TypeError, ValueError):
                matched_p95 = 0.0

        matched_std = 0.0
        if r.matched_baseline and hasattr(r.matched_baseline, "std_expectancy_r"):
            try:
                matched_std = round(float(r.matched_baseline.std_expectancy_r), 4)
            except (TypeError, ValueError):
                matched_std = 0.0

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
            "funding_fallback_periods": getattr(sim, "funding_fallback_periods", 0),
            "funding_total_periods": getattr(sim, "funding_total_periods", 0),
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
            "robustness_panel": r.robustness_panel.to_dict() if getattr(r, "robustness_panel", None) else None,
            "per_symbol_metrics": r.per_symbol_metrics,
            "total_signals_generated": r.total_signals_generated,
            "hurdles_passed": getattr(ev, "hurdles_passed", []),
            "hurdles_failed": getattr(ev, "hurdles_failed", []),
            "diagnostics": getattr(ev, "diagnostics", []),
            "matched_median_expectancy_r": matched_med,
            "matched_p95_expectancy_r": matched_p95,
            "matched_std_expectancy_r": matched_std,
        }

    # Preserve any existing strategies not present in results
    if os.path.exists(output_json_path):
        try:
            with open(output_json_path, "r", encoding="utf-8") as f:
                existing_payload = json.load(f)
            existing_strats = existing_payload.get("strategies", {})
            for k, v in existing_strats.items():
                if k not in strategies_data:
                    strategies_data[k] = v
        except Exception as e:
            logger.warning(f"Could not load existing strategies from {output_json_path}: {e}")

    dataset_hash = compute_dataset_hash(candles_dict) if candles_dict else "unknown"

    payload = {
        "metadata": {
            "run_timestamp": now_iso,
            "git_commit": git_hash,
            "dataset_hash": dataset_hash,
            "universe": universe_name,
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
    universe_name: str = DEFAULT_UNIVERSE,
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
        badge = "🟢 **PASSED DEV GATE (see robustness)**" if ev.is_validated else "🔴 **REJECTED**"
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
            f"> Sizing Mode: `{r.sim_result.sizing_mode}` | Account Ruined: `{ruin_info}`  \n"
            f"> Funding Fallback Periods: `{getattr(r.sim_result, 'funding_fallback_periods', 0)} / {getattr(r.sim_result, 'funding_total_periods', 0)}` "
            f"({((getattr(r.sim_result, 'funding_fallback_periods', 0) / getattr(r.sim_result, 'funding_total_periods', 1)) * 100.0) if getattr(r.sim_result, 'funding_total_periods', 0) > 0 else 0.0:.1f}%)"
            f"{floor_warning_str}"
        )

        rob_md = ""
        if getattr(r, "robustness_panel", None):
            rob_md = "\n\n" + r.robustness_panel.format_markdown()

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
{sym_table}{rob_md}

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
            f"1. **Passed Dev Gate (see robustness) ({len(validated_strats)}/{num_strategies})**:\n"
            f"   The following strategies met all pre-registered dev gate criteria ($p < {alpha_sig_level:.2f}$, positive bootstrap CI lower bound):\n"
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
   No holdout dataset has been used yet. All metrics reflect in-sample backtesting across the complete historical timeline.
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
**Universe:** {universe_name}  
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
        "--universe",
        type=str,
        default=DEFAULT_UNIVERSE,
        help=f"Named universe for research (e.g. U10, U4, U5; default: {DEFAULT_UNIVERSE})",
    )
    parser.add_argument(
        "--only",
        type=str,
        default=None,
        help="Evaluate only the specified strategy (e.g. Donchian4hN55)",
    )
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
        "--log-md",
        type=str,
        default="research/preregistration/LOG.md",
        help="Path to preregistration LOG.md",
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
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Number of worker processes for matched baseline (default: os.cpu_count())",
    )
    parser.add_argument(
        "--checkpoint",
        type=str,
        default=None,
        help="Path to matched baseline checkpoint file",
    )
    args = parser.parse_args()

    results = run_strategy_evidence_validation(
        universe_name=args.universe,
        only_strategy=args.only,
        data_dir=args.data_dir,
        output_report_path=args.output,
        output_json_path=args.output_json,
        log_md_path=args.log_md,
        sizing_mode=args.sizing_mode,
        ruin_threshold_usd=args.ruin_threshold,
        matched_passes=args.matched_passes,
        workers=args.workers,
        checkpoint_path=args.checkpoint,
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
        funding_str = f"{getattr(r.sim_result, 'funding_fallback_periods', 0)}/{getattr(r.sim_result, 'funding_total_periods', 0)}"
        print(
            f"[{ev.verdict}] {ev.strategy_name:<22} | Trades: {ev.total_trades:>4} | "
            f"WR: {ev.win_rate:>5.1f}% | PF: {ev.profit_factor:>4.2f} | "
            f"EV: {ev.expectancy_r:>+7.4f}R | MaxDD(R): {r.sim_result.max_drawdown_r:>5.2f}R | "
            f"Funding Fallback: {funding_str} | "
            f"95% CI: {ci_str} | Matched Med: {matched_med} | Matched p-val: {matched_p_val:.4f} | "
            f"Days: {eval_days}"
        )
    print("=" * 80)
    print(f"Validation dossier saved to: {args.output}\n")


if __name__ == "__main__":
    main()
