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

from data.loader import load_candles
from engine.exits import ExitConfig
from engine.simulator import (
    CandidateSignal,
    ExecutedTrade,
    SimulationFriction,
    SimulationResult,
)
from research.evaluator import BaselineHurdles, StrategyEvaluation, StrategyEvaluator
from research.random_baseline import (
    MatchedBaselineResult,
    compute_bootstrap_ci,
    compute_dataset_hash,
    run_matched_baseline,
    simulate_single_trade_lifecycle,
)
from research.strategies import (
    BaseStrategy,
    ConfluenceThresholdStrategy,
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


def simulate_portfolio_signals(
    candidates: List[CandidateSignal],
    candles_dict: Dict[str, np.ndarray],  # symbol -> [open, high, low, close, open_time]
    bar_index_map: Dict[str, Dict[int, int]],  # symbol -> {open_time_ms: bar_idx}
    initial_equity_usd: float = 10000.0,
    risk_per_trade_pct: float = 1.0,
    max_open_positions: int = 3,
    max_directional_positions: int = 2,
    symbol_cooldown_bars: int = 3,
    max_leverage: float = 3.0,
    friction: Optional[SimulationFriction] = None,
    exit_config: Optional[ExitConfig] = None,
    sizing_mode: str = "fixed_risk",
    ruin_threshold_usd: Optional[float] = None,
) -> SimulationResult:
    """
    Simulate portfolio execution of chronological candidate signals enforcing:
    1. Max concurrency (3 total, 2 directional)
    2. Zero duplicate positions per symbol
    3. 3-bar post-exit symbol cooldown
    4. Sizing mode: fixed_risk (default) or compounding, max 3x leverage
    5. Ruin guard: reject all new entries if equity <= ruin_threshold_usd (5% of initial equity)
    6. Single Exit Authority lifecycle with adverse market stop slippage and taker fees
    7. Deferred equity credit upon trade exit
    """
    fric = friction or SimulationFriction()
    cfg = exit_config or ExitConfig()
    ruin_limit = float(ruin_threshold_usd) if ruin_threshold_usd is not None else (0.05 * initial_equity_usd)
    ruined = False
    ruin_time_ms: Optional[int] = None

    # Sort all candidates chronologically
    sorted_signals = sorted(candidates, key=lambda s: s.timestamp_ms)

    current_equity = initial_equity_usd
    completed_trades: List[ExecutedTrade] = []
    # Active tracking list: [{"symbol", "side", "exit_ts_ms", "net_pnl_usd", "trade"}]
    active_open: List[Dict[str, Any]] = []
    # symbol -> last_closed_bar_idx
    cooldowns: Dict[str, int] = {}
    equity_curve: List[Tuple[int, float]] = [(sorted_signals[0].timestamp_ms if sorted_signals else 0, current_equity)]

    for sig in sorted_signals:
        sym = sig.symbol.upper()
        c_ts = sig.timestamp_ms

        # 1. Clean up positions that exited at or before candidate entry time
        still_open = []
        for pos in active_open:
            if pos["exit_ts_ms"] <= c_ts:
                current_equity += pos["net_pnl_usd"]
                # Update equity curve
                if equity_curve and equity_curve[-1][0] == pos["exit_ts_ms"]:
                    equity_curve[-1] = (pos["exit_ts_ms"], round(current_equity, 2))
                else:
                    equity_curve.append((pos["exit_ts_ms"], round(current_equity, 2)))
            else:
                still_open.append(pos)
        active_open = still_open

        # 2. Ruin Guard Check: reject opening new trades only in compounding mode
        if sizing_mode == "compounding":
            if current_equity <= ruin_limit:
                if not ruined:
                    ruined = True
                    ruin_time_ms = c_ts
                continue
        else:
            # In fixed_risk mode: ruin is report-only, never stops entry
            if current_equity <= ruin_limit and not ruined:
                ruined = True
                ruin_time_ms = c_ts

        # 3. Portfolio Concurrency Checks
        if len(active_open) >= max_open_positions:
            continue

        if any(p["symbol"] == sym for p in active_open):
            continue

        directional_count = sum(1 for p in active_open if p["side"] == sig.side)
        if directional_count >= max_directional_positions:
            continue

        arr = candles_dict.get(sym)
        if arr is None:
            continue

        idx_map = bar_index_map.get(sym, {})
        entry_bar_idx = idx_map.get(c_ts)
        if entry_bar_idx is None:
            continue

        # Cooldown check
        last_closed_bar = cooldowns.get(sym, -999)
        if (entry_bar_idx - last_closed_bar) < symbol_cooldown_bars:
            continue

        # 3. Simulate trade lifecycle forward (120-bar forward window is ample for exit authority)
        max_k = min(entry_bar_idx + 120, len(arr))
        candles_forward = [
            (float(arr[k, 0]), float(arr[k, 1]), float(arr[k, 2]), float(arr[k, 3]), int(arr[k, 4]))
            for k in range(entry_bar_idx, max_k)
        ]

        if not candles_forward:
            continue

        equity_for_sizing = initial_equity_usd if sizing_mode == "fixed_risk" else current_equity

        trade = simulate_single_trade_lifecycle(
            symbol=sym,
            side=sig.side,
            nominal_entry=sig.entry_price,
            stop_loss=sig.stop_loss,
            entry_ts_ms=c_ts,
            candles_forward=candles_forward,
            equity_at_entry=equity_for_sizing,
            risk_per_trade_pct=risk_per_trade_pct,
            max_leverage=max_leverage,
            friction=fric,
            exit_config=cfg,
        )

        if trade is None:
            continue

        # Find bar index when trade exited
        exit_bar_idx = idx_map.get(trade.exit_time_ms, entry_bar_idx + trade.bars_held)
        cooldowns[sym] = exit_bar_idx

        active_open.append({
            "symbol": sym,
            "side": sig.side,
            "exit_ts_ms": trade.exit_time_ms,
            "net_pnl_usd": trade.net_pnl_usd,
            "trade": trade,
        })
        completed_trades.append(trade)

    # 4. Final flush: credit any remaining active positions at their exit times
    for pos in active_open:
        current_equity += pos["net_pnl_usd"]
        if equity_curve and equity_curve[-1][0] == pos["exit_ts_ms"]:
            equity_curve[-1] = (pos["exit_ts_ms"], round(current_equity, 2))
        else:
            equity_curve.append((pos["exit_ts_ms"], round(current_equity, 2)))

    # 5. Compute full performance metrics
    total_trades = len(completed_trades)
    if total_trades == 0:
        return SimulationResult(
            initial_equity_usd=initial_equity_usd,
            final_equity_usd=current_equity,
            net_profit_usd=0.0,
            net_profit_pct=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate_pct=0.0,
            gross_profit_usd=0.0,
            gross_loss_usd=0.0,
            profit_factor=0.0,
            gross_profit_factor=0.0,
            expectancy_usd=0.0,
            expectancy_r=0.0,
            gross_expectancy_r=0.0,
            avg_r_multiple=0.0,
            max_drawdown_pct=0.0,
            max_drawdown_duration_bars=0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            total_fees_usd=0.0,
            total_slippage_usd=0.0,
            total_funding_cost_usd=0.0,
            trades=[],
            equity_curve=equity_curve,
            ruined=ruined,
            ruin_time_ms=ruin_time_ms,
            sizing_mode=sizing_mode,
            cumulative_r=0.0,
            max_drawdown_r=0.0,
            days_evaluated=0.0,
            pct_of_dataset_evaluated=100.0,
        )

    # Chronological sort for drawdown and equity path
    completed_trades.sort(key=lambda t: t.exit_time_ms)

    wins = [t for t in completed_trades if t.net_pnl_usd > 0]
    losses = [t for t in completed_trades if t.net_pnl_usd <= 0]
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades) * 100.0

    # Net Profit Factor: sum(net > 0) / abs(sum(net <= 0))
    net_profit = sum(t.net_pnl_usd for t in wins)
    net_loss = abs(sum(t.net_pnl_usd for t in losses))
    profit_factor = (net_profit / net_loss) if net_loss > 0 else (999.0 if net_profit > 0 else 0.0)

    # Gross Profit Factor (diagnostics)
    gross_profit = sum(t.gross_pnl_usd for t in completed_trades if t.gross_pnl_usd > 0)
    gross_loss = abs(sum(t.gross_pnl_usd for t in completed_trades if t.gross_pnl_usd <= 0))
    gross_profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

    net_profit_usd = current_equity - initial_equity_usd
    net_profit_pct = (net_profit_usd / initial_equity_usd) * 100.0

    avg_win_usd = (sum(t.net_pnl_usd for t in wins) / win_count) if win_count > 0 else 0.0
    avg_loss_usd = (abs(sum(t.net_pnl_usd for t in losses)) / loss_count) if loss_count > 0 else 0.0
    wr_frac = win_rate / 100.0
    lr_frac = 1.0 - wr_frac
    expectancy_usd = (wr_frac * avg_win_usd) - (lr_frac * avg_loss_usd)

    # Net expectancy R from net_r
    net_r_values = [t.net_r for t in completed_trades]
    avg_r = float(np.mean(net_r_values)) if net_r_values else 0.0
    win_net_r_mean = float(np.mean([t.net_r for t in wins])) if wins else 0.0
    loss_net_r_mean = float(np.mean([abs(t.net_r) for t in losses])) if losses else 0.0
    expectancy_r = (wr_frac * win_net_r_mean) - (lr_frac * loss_net_r_mean)

    # Gross expectancy R from realized_r
    gross_r_values = [t.realized_r for t in completed_trades]
    gross_win_r_mean = float(np.mean([t.realized_r for t in wins])) if wins else 0.0
    gross_loss_r_mean = float(np.mean([abs(t.realized_r) for t in losses])) if losses else 0.0
    gross_expectancy_r = (wr_frac * gross_win_r_mean) - (lr_frac * gross_loss_r_mean)

    total_fees = sum(t.fees_usd for t in completed_trades)
    total_slippage = sum(t.slippage_usd for t in completed_trades)
    total_funding = sum(t.funding_cost_usd for t in completed_trades)

    # Max Drawdown from chronological trade PnL sequence
    eq_peak = initial_equity_usd
    cur_eq = initial_equity_usd
    max_dd_pct = 0.0
    for t in completed_trades:
        cur_eq += t.net_pnl_usd
        if cur_eq > eq_peak:
            eq_peak = cur_eq
        else:
            dd = ((eq_peak - cur_eq) / eq_peak) * 100.0
            if dd > max_dd_pct:
                max_dd_pct = dd

    returns = np.array([t.net_return_pct for t in completed_trades])
    std_ret = np.std(returns)
    mean_ret = np.mean(returns)
    sharpe = float((mean_ret / std_ret) * np.sqrt(252.0)) if std_ret > 1e-8 else 0.0

    # Sortino semi-deviation from 0
    downside_diff = np.minimum(0.0, returns)
    downside_dev = float(np.sqrt(np.mean(downside_diff**2)))
    sortino = float((mean_ret / downside_dev) * np.sqrt(252.0)) if downside_dev > 1e-8 else 0.0

    # Cumulative net R and peak-to-trough max drawdown on net R curve
    cum_r = 0.0
    peak_r = 0.0
    max_dd_r = 0.0
    for t in completed_trades:
        cum_r += t.net_r
        if cum_r > peak_r:
            peak_r = cum_r
        else:
            dd_r = peak_r - cum_r
            if dd_r > max_dd_r:
                max_dd_r = dd_r

    dataset_min_ts = min(int(arr[0, 4]) for arr in candles_dict.values()) if candles_dict else 0
    dataset_max_ts = max(int(arr[-1, 4]) for arr in candles_dict.values()) if candles_dict else 0
    total_dataset_days = max(1.0, (dataset_max_ts - dataset_min_ts) / (86400 * 1000.0)) if dataset_max_ts > dataset_min_ts else 1.0

    if sizing_mode == "compounding" and ruined and ruin_time_ms:
        days_evaluated = round(max(1.0, (ruin_time_ms - dataset_min_ts) / (86400 * 1000.0)), 1)
    else:
        days_evaluated = round(total_dataset_days, 1)
    pct_of_dataset_evaluated = min(100.0, round((days_evaluated / total_dataset_days) * 100.0, 1))

    return SimulationResult(
        initial_equity_usd=initial_equity_usd,
        final_equity_usd=round(current_equity, 2),
        net_profit_usd=round(net_profit_usd, 2),
        net_profit_pct=round(net_profit_pct, 2),
        total_trades=total_trades,
        winning_trades=win_count,
        losing_trades=loss_count,
        win_rate_pct=round(win_rate, 2),
        gross_profit_usd=round(gross_profit, 2),
        gross_loss_usd=round(gross_loss, 2),
        profit_factor=round(profit_factor, 2),
        gross_profit_factor=round(gross_profit_factor, 2),
        expectancy_usd=round(expectancy_usd, 2),
        expectancy_r=round(expectancy_r, 4),
        gross_expectancy_r=round(gross_expectancy_r, 4),
        avg_r_multiple=round(avg_r, 4),
        max_drawdown_pct=round(max_dd_pct, 2),
        max_drawdown_duration_bars=0,
        sharpe_ratio=round(sharpe, 2),
        sortino_ratio=round(sortino, 2),
        total_fees_usd=round(total_fees, 2),
        total_slippage_usd=round(total_slippage, 2),
        total_funding_cost_usd=round(total_funding, 2),
        trades=completed_trades,
        equity_curve=equity_curve,
        ruined=ruined,
        ruin_time_ms=ruin_time_ms,
        sizing_mode=sizing_mode,
        cumulative_r=round(cum_r, 4),
        max_drawdown_r=round(max_dd_r, 4),
        days_evaluated=days_evaluated,
        pct_of_dataset_evaluated=pct_of_dataset_evaluated,
    )


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

        arr = df_1[["open", "high", "low", "close", "open_time"]].to_numpy(dtype=np.float64)
        candles_dict[sym] = arr

        # Fast lookup mapping: open_time_ms -> bar_idx
        bar_index_map[sym] = {int(arr[idx, 4]): idx for idx in range(len(arr))}

    logger.info("Loaded datasets. Initializing trading hypotheses...")

    strategies: List[BaseStrategy] = [
        TrendPullbackStrategy(),
        FVGRetestStrategy(),
        ConfluenceThresholdStrategy(),
        LiquiditySweepStrategy(),
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
            exit_config=cfg,
        )

        evaluation = evaluator.evaluate(
            s_name,
            s_desc,
            sim_res,
            matched_baseline=matched_res,
            bootstrap_ci=boot_ci,
        )
        per_sym = compute_per_symbol_breakdown(sim_res.trades)

        matched_p_val = evaluation.matched_p_value if evaluation.matched_p_value is not None else evaluation.p_composite
        logger.info(
            f"  Result for {s_name}: Trades={sim_res.total_trades}, "
            f"WR={sim_res.win_rate_pct:.1f}%, PF={sim_res.profit_factor:.2f}, "
            f"EV={sim_res.expectancy_r:+.4f}R, Sharpe={sim_res.sharpe_ratio:.2f}, "
            f"MaxDD={sim_res.max_drawdown_pct:.1f}%, Return={sim_res.net_profit_pct:+.1f}% | "
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
    dataset_days_val = (
        (max(int(arr[-1, 4]) for arr in candles_dict.values()) - min(int(arr[0, 4]) for arr in candles_dict.values())) / (86400 * 1000.0)
        if candles_dict
        else 1022.0
    )
    dataset_bars_val = len(next(iter(candles_dict.values()))) if candles_dict else 24528
    generate_markdown_report(
        results=run_results,
        output_path=output_report_path,
        baseline_hurdles=evaluator.hurdles,
        dataset_info={
            "symbols": syms,
            "bars_per_symbol": dataset_bars_val,
            "days": dataset_days_val,
        },
        friction=fric,
        exit_config=cfg,
    )
    save_strategy_results_json(run_results, output_json_path, candles_dict=candles_dict)
    return run_results


def get_git_commit_hash() -> str:
    """Return the current git commit hash, or 'unknown' if not in git."""
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        return "unknown"


def save_strategy_results_json(
    results: List[StrategyRunResult],
    output_json_path: str,
    candles_dict: Optional[Dict[str, np.ndarray]] = None,
) -> None:
    """Save machine-readable strategy validation outcomes to JSON."""
    os.makedirs(os.path.dirname(output_json_path), exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    git_hash = get_git_commit_hash()

    min_ts = 0
    max_ts = 0
    if candles_dict:
        min_ts = min(int(arr[0, 4]) for arr in candles_dict.values()) if candles_dict else 0
        max_ts = max(int(arr[-1, 4]) for arr in candles_dict.values()) if candles_dict else 0

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
        }

    payload = {
        "metadata": {
            "run_timestamp": now_iso,
            "git_commit": git_hash,
            "data_date_range": date_range_str,
            "start_time_ms": min_ts,
            "end_time_ms": max_ts,
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
        symbols_list = dataset_info.get("symbols", DEFAULT_SYMBOLS)
        total_symbols = len(symbols_list)
        dataset_days = float(dataset_info.get("days", 1022.0))
        dataset_bars = int(dataset_info.get("bars_per_symbol", 24528))
    elif results and results[0].per_symbol_metrics:
        symbols_list = sorted(list(results[0].per_symbol_metrics.keys()))
        total_symbols = len(symbols_list)
        dataset_days = float(results[0].sim_result.days_evaluated) if results[0].sim_result.days_evaluated > 0 else 1022.0
        dataset_bars = int(round(dataset_days * 24.0))
    else:
        symbols_list = DEFAULT_SYMBOLS
        total_symbols = len(symbols_list)
        dataset_days = 1022.0
        dataset_bars = 24528

    dataset_years = dataset_days / 365.25
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

        eval_days_str = f"{r.sim_result.days_evaluated:.0f}d ({r.sim_result.pct_of_dataset_evaluated:.1f}%)"
        row = (
            f"| **{ev.strategy_name}** | {ev.total_trades} | {ev.win_rate:.1f}% | "
            f"**{r.sim_result.profit_factor:.2f}** ({r.sim_result.gross_profit_factor:.2f}) | "
            f"**{r.sim_result.expectancy_r:+.4f}R** ({r.sim_result.gross_expectancy_r:+.4f}R) | "
            f"{r.sim_result.max_drawdown_r:.2f}R | "
            f"{ci_str} | {matched_med_str} | {matched_p_str} | "
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

        diag_list = "\n".join([f"- ℹ️ {d}" for d in ev.diagnostics]) or "- None"
        verdict_banner = (
            f"> **Statistical Verdict**: **`{ev.verdict}`**  \n"
            f"> Matched Rank $p$-value: **`{matched_p_display}`** (Empirical rank within {matched_passes_n:,} matched null passes)  \n"
            f"> Total Candidate Signals: `{r.total_signals_generated}` | Executed Trades: `{r.signals_executed}`  \n"
            f"> Net Profit Factor: **`{r.sim_result.profit_factor:.2f}`** (Gross: `{r.sim_result.gross_profit_factor:.2f}`)  \n"
            f"> Net Expectancy: **`{r.sim_result.expectancy_r:+.4f}R`** (Gross: `{r.sim_result.gross_expectancy_r:+.4f}R`)  \n"
            f"> Max Drawdown (R): **`{r.sim_result.max_drawdown_r:.2f}R`** | Cumulative Net R: **`{r.sim_result.cumulative_r:+.2f}R`**  \n"
            f"> Days Evaluated: **`{r.sim_result.days_evaluated:.0f} days ({r.sim_result.pct_of_dataset_evaluated:.1f}% of dataset)`**  \n"
            f"> Bootstrap 95% CI (Mean Net R): **`{ci_str}`** ({boot_resamples_n:,} resamples)  \n"
            f"> Matched Baseline Median Net EV: **`{matched_med_str}`** (Matched 95th Pct Hurdle: `{matched_p95_str}`)  \n"
            f"> Sizing Mode: `{r.sim_result.sizing_mode}` | Account Ruined: `{ruin_info}`"
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

    report_content = f"""# Strategy Validation Dossier: Evidence Before Infrastructure
**Generated:** {now_str}  
**Architecture Doctrine:** Claude's Option B (Discretionary Human Trading Terminal)  
**Null Hypothesis ($H_0$):** Phase 2 {mc_iterations:,}-pass Monte Carlo Baseline Hurdle  
**Dataset:** {total_symbols} Major Pairs (`{', '.join(symbols_list)}`), {dataset_years:.1f} Years ({dataset_bars:,} continuous 1h bars / coin from Binance USD-M Futures)  
**Execution Authority:** Single Exit Authority (`engine/exits.py`)  
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

| Strategy | Trades | Win Rate | Net PF (Gross) | Net EV (Gross EV) | Max DD (R) | Bootstrap 95% CI | Matched Median EV | Matched Rank $p$-value | Days Evaluated | Sizing | Ruined? | Alpha Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
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
        "--matched-passes",
        type=int,
        default=1000,
        help="Number of matched baseline passes (default: 1000)",
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
