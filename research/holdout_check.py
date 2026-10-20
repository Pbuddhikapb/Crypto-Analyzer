"""
Holdout Verification Module for Locked Out-of-Sample Alpha Validation.

Executes a single named hypothesis against strictly out-of-sample holdout data
(candles recorded after DEVELOPMENT_END = 2026-04-18).
Guarantees one-pass audit integrity:
- Refuses execution if the strategy's source file has already been evaluated on holdout.
- Appends audit record (strategy, timestamp, git_hash) to research/reports/holdout_log.csv.
"""

import argparse
import csv
import hashlib
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Type

import numpy as np
import pandas as pd

from data.loader import DEVELOPMENT_END, load_candles
from engine.simulator import CandidateSignal, SimulationResult
from research.strategies.base import BaseStrategy
from research.strategies.confluence_threshold import ConfluenceThresholdStrategy
from research.strategies.fvg_retest import FVGRetestStrategy
from research.strategies.liquidity_sweep import LiquiditySweepStrategy
from research.strategies.trend_pullback import TrendPullbackStrategy
from research.strategy_runner import DEFAULT_SYMBOLS, simulate_portfolio_signals

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("holdout_check")

DEFAULT_HOLDOUT_LOG = "research/reports/holdout_log.csv"

STRATEGY_REGISTRY: Dict[str, Tuple[Type[BaseStrategy], str, str]] = {
    "trendpullback": (TrendPullbackStrategy, "TrendPullback", "research/strategies/trend_pullback.py"),
    "trendpullbackstrategy": (TrendPullbackStrategy, "TrendPullback", "research/strategies/trend_pullback.py"),
    "fvgretest": (FVGRetestStrategy, "FVGRetest", "research/strategies/fvg_retest.py"),
    "fvgreteststrategy": (FVGRetestStrategy, "FVGRetest", "research/strategies/fvg_retest.py"),
    "confluencethreshold": (ConfluenceThresholdStrategy, "ConfluenceThreshold", "research/strategies/confluence_threshold.py"),
    "confluencethresholdstrategy": (ConfluenceThresholdStrategy, "ConfluenceThreshold", "research/strategies/confluence_threshold.py"),
    "liquiditysweep": (LiquiditySweepStrategy, "LiquiditySweep", "research/strategies/liquidity_sweep.py"),
    "liquiditysweepstrategy": (LiquiditySweepStrategy, "LiquiditySweep", "research/strategies/liquidity_sweep.py"),
}


def get_source_file_git_hash(source_file: str) -> str:
    """
    Compute git blob object hash of the strategy's source file (matches `git hash-object`).
    Ensures that any source file modification updates the hash.
    """
    if os.path.exists(source_file):
        try:
            res = subprocess.run(
                ["git", "hash-object", source_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
            )
            h = res.stdout.strip()
            if len(h) == 40:
                return h
        except Exception:
            pass

        try:
            with open(source_file, "rb") as f:
                data = f.read()
            header = f"blob {len(data)}\0".encode()
            return hashlib.sha1(header + data).hexdigest()
        except Exception:
            pass
    return "unknown"


def is_holdout_run_recorded(
    strategy_name: str,
    git_hash: str,
    log_path: str = DEFAULT_HOLDOUT_LOG,
) -> bool:
    """Verify if a specific strategy with the same source git hash has already been run on holdout."""
    if not os.path.exists(log_path):
        return False
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            for row in reader:
                if len(row) >= 3:
                    strat_recorded = row[0].strip().lower()
                    hash_recorded = row[2].strip().lower()
                    if strat_recorded == strategy_name.lower() and hash_recorded == git_hash.lower():
                        return True
    except Exception as e:
        logger.warning(f"Error checking holdout log at {log_path}: {e}")
    return False


def record_holdout_run(
    strategy_name: str,
    git_hash: str,
    log_path: str = DEFAULT_HOLDOUT_LOG,
    timestamp: Optional[str] = None,
):
    """Atomically append a run entry to research/reports/holdout_log.csv."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    file_exists = os.path.exists(log_path) and os.path.getsize(log_path) > 0
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["strategy", "timestamp", "git_hash"])
        writer.writerow([strategy_name, ts, git_hash])


def run_holdout_check(
    strategy_name: str,
    data_dir: str = "data/candles",
    log_path: str = DEFAULT_HOLDOUT_LOG,
    symbols: Optional[List[str]] = None,
    sizing_mode: str = "fixed_risk",
) -> Dict[str, Any]:
    """
    Execute a single named strategy on locked holdout data and record audit entry.
    Refuses execution if the strategy's source file was already run with the same git hash.
    """
    key = strategy_name.strip().lower()
    if key not in STRATEGY_REGISTRY:
        avail = ", ".join(["TrendPullback", "FVGRetest", "ConfluenceThreshold", "LiquiditySweep"])
        raise ValueError(f"Unknown strategy '{strategy_name}'. Available strategies: {avail}")

    strat_cls, canonical_name, source_path = STRATEGY_REGISTRY[key]
    git_hash = get_source_file_git_hash(source_path)

    # 1. Check holdout lock: refuse reruns on identical source code
    if is_holdout_run_recorded(canonical_name, git_hash, log_path=log_path):
        raise RuntimeError(
            f"Holdout run blocked: Strategy '{canonical_name}' has already been evaluated "
            f"on holdout data with source git hash '{git_hash}'. "
            "Holdout data is strictly one-pass to preserve statistical validity."
        )

    # 2. Ingest holdout candles strictly after DEVELOPMENT_END
    syms = symbols or DEFAULT_SYMBOLS
    dev_end_dt = pd.to_datetime(DEVELOPMENT_END, utc=True)
    logger.info(f"Loading holdout data (> {DEVELOPMENT_END}) for {len(syms)} symbols...")

    df_1h_dict: Dict[str, pd.DataFrame] = {}
    df_4h_dict: Dict[str, pd.DataFrame] = {}
    candles_dict: Dict[str, np.ndarray] = {}
    bar_index_map: Dict[str, Dict[int, int]] = {}
    max_ts = 0

    for sym in syms:
        raw_1h = load_candles(sym, "1h", data_dir=data_dir, allow_holdout=True)
        holdout_1h = raw_1h[raw_1h["datetime"] > dev_end_dt].reset_index(drop=True)

        raw_4h = load_candles(sym, "4h", data_dir=data_dir, allow_holdout=True)
        holdout_4h = raw_4h[raw_4h["datetime"] > dev_end_dt].reset_index(drop=True)

        if holdout_1h.empty:
            raise ValueError(f"No holdout data available for {sym} 1h past {DEVELOPMENT_END}")

        df_1h_dict[sym] = holdout_1h
        df_4h_dict[sym] = holdout_4h

        arr = holdout_1h[["open", "high", "low", "close", "open_time"]].to_numpy(dtype=np.float64)
        candles_dict[sym] = arr
        bar_index_map[sym] = {int(arr[idx, 4]): idx for idx in range(len(arr))}
        if len(arr) > 0 and int(arr[-1, 4]) > max_ts:
            max_ts = int(arr[-1, 4])

    max_holdout_str = datetime.fromtimestamp(max_ts / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # 3. Generate candidate signals on holdout bars
    strat = strat_cls()
    all_candidates: List[CandidateSignal] = []
    for sym in syms:
        sym_sigs = strat.generate_signals(sym, df_1h_dict[sym], df_4h_dict[sym])
        all_candidates.extend(sym_sigs)

    # 4. Simulate portfolio execution across holdout
    sim_res: SimulationResult = simulate_portfolio_signals(
        candidates=all_candidates,
        candles_dict=candles_dict,
        bar_index_map=bar_index_map,
        sizing_mode=sizing_mode,
    )

    # 5. Append audit line to holdout log
    record_holdout_run(canonical_name, git_hash, log_path=log_path)

    # 6. Print summary
    print("\n" + "=" * 80)
    print(f"HOLDOUT VALIDATION EXECUTION: {canonical_name}")
    print("=" * 80)
    print(f"Source File:         {source_path}")
    print(f"Source Git Hash:     {git_hash}")
    print(f"Holdout Window:      > {DEVELOPMENT_END} to {max_holdout_str}")
    print(f"Signals Generated:   {len(all_candidates):,}")
    print(f"Trades Executed:     {sim_res.total_trades:,}")
    print(f"Win Rate:            {sim_res.win_rate_pct:.2f}%")
    print(f"Net Profit Factor:   {sim_res.profit_factor:.2f} (Gross PF: {sim_res.gross_profit_factor:.2f})")
    print(f"Net Expectancy:      {sim_res.expectancy_r:+.4f}R (Gross EV: {sim_res.gross_expectancy_r:+.4f}R)")
    print(f"Max Drawdown (R):    {sim_res.max_drawdown_r:.2f}R")
    print(f"Net Profit:          ${sim_res.net_profit_usd:,.2f} ({sim_res.net_profit_pct:+.2f}%)")
    print(f"Holdout Log Audit:   {log_path} (appended)")
    print("=" * 80 + "\n")

    return {
        "strategy": canonical_name,
        "git_hash": git_hash,
        "total_trades": sim_res.total_trades,
        "win_rate_pct": sim_res.win_rate_pct,
        "profit_factor": sim_res.profit_factor,
        "expectancy_r": sim_res.expectancy_r,
        "max_drawdown_r": sim_res.max_drawdown_r,
        "net_profit_usd": sim_res.net_profit_usd,
        "net_profit_pct": sim_res.net_profit_pct,
        "sim_result": sim_res,
    }


def main():
    parser = argparse.ArgumentParser(description="Run one strategy hypothesis on locked holdout data.")
    parser.add_argument(
        "strategy",
        type=str,
        help="Strategy name (e.g. TrendPullback, FVGRetest, ConfluenceThreshold, LiquiditySweep)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/candles",
        help="Directory containing candle Parquet files (default: data/candles)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=DEFAULT_HOLDOUT_LOG,
        help="Path to holdout_log.csv (default: research/reports/holdout_log.csv)",
    )
    args = parser.parse_args()

    try:
        run_holdout_check(
            strategy_name=args.strategy,
            data_dir=args.data_dir,
            log_path=args.log_file,
        )
    except Exception as e:
        print(f"\n[BLOCKED / ERROR] {e}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
