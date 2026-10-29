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

from data.loader import DEVELOPMENT_END, candles_to_array, load_candles
from engine.simulator import CandidateSignal, SimulationResult, simulate_portfolio_signals
from research.strategies.base import BaseStrategy
from research.strategies.confluence_threshold import ConfluenceThresholdStrategy
from research.strategies.donchian_4h import Donchian4hN20, Donchian4hN55
from research.strategies.fvg_retest import FVGRetestStrategy
from research.strategies.liquidity_sweep import LiquiditySweepStrategy
from research.strategies.trend_pullback import TrendPullbackStrategy
from research.strategy_runner import DEFAULT_SYMBOLS

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
    "donchian4hn20": (Donchian4hN20, "Donchian4hN20", "research/strategies/donchian_4h.py"),
    "donchian4hn20strategy": (Donchian4hN20, "Donchian4hN20", "research/strategies/donchian_4h.py"),
    "donchian4hn55": (Donchian4hN55, "Donchian4hN55", "research/strategies/donchian_4h.py"),
    "donchian4hn55strategy": (Donchian4hN55, "Donchian4hN55", "research/strategies/donchian_4h.py"),
}


def get_source_file_git_hash(source_file: str) -> str:
    """
    Compute git blob object hash of the strategy's source file (matches `git hash-object`).
    Ensures that any source file modification updates the hash.
    Raises FileNotFoundError if source_file does not exist.
    Raises RuntimeError if neither git rev-parse/hash-object nor the blob-hash fallback works;
    never returns 'unknown'.
    """
    if not os.path.exists(source_file):
        raise FileNotFoundError(f"Strategy source file '{source_file}' does not exist.")

    # 1. Try git rev-parse HEAD:<source_file>
    try:
        res = subprocess.run(
            ["git", "rev-parse", f"HEAD:{source_file}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        h = res.stdout.strip()
        if len(h) == 40:
            return h
    except (subprocess.SubprocessError, OSError):
        pass

    # 2. Try git hash-object <source_file>
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
    except (subprocess.SubprocessError, OSError):
        pass

    # 3. Pure Python blob-hash fallback: sha1("blob " + len + "\0" + data)
    try:
        with open(source_file, "rb") as f:
            data = f.read()
        header = f"blob {len(data)}\0".encode()
        return hashlib.sha1(header + data).hexdigest()
    except OSError as e:
        raise RuntimeError(
            f"Failed to compute git hash for '{source_file}': git rev-parse, "
            f"git hash-object, and blob-hash fallback all failed: {e}"
        ) from e


def check_working_tree_clean(strategy_source_file: str) -> List[str]:
    """
    Check if the git working tree has uncommitted changes to the strategy source file
    or its dependencies (indicators.py, engine/*, domain/*).
    Returns a list of relative paths for dirty files.
    """
    targets = [strategy_source_file, "indicators.py", "engine", "domain"]
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain", "--"] + targets,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        lines = [line for line in res.stdout.splitlines() if line.strip()]
        dirty = []
        for line in lines:
            # git status --porcelain output is 'XY path' or 'XY path1 -> path2'
            file_path = line[3:].strip()
            dirty.append(file_path)
        return dirty
    except (subprocess.SubprocessError, OSError) as e:
        raise RuntimeError(f"Failed to check git working tree status for holdout check: {e}") from e


def is_holdout_run_recorded(
    strategy_name: str,
    git_hash: str,
    log_path: str = DEFAULT_HOLDOUT_LOG,
) -> bool:
    """Verify if a specific strategy with the same source git hash has completed holdout evaluation."""
    if not os.path.exists(log_path):
        return False
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)  # Advance past header row
            for row in reader:
                if len(row) >= 3:
                    strat_recorded = row[0].strip().lower()
                    hash_recorded = row[2].strip().lower()
                    status = row[3].strip().lower() if len(row) >= 4 else "completed"
                    if (
                        strat_recorded == strategy_name.lower()
                        and hash_recorded == git_hash.lower()
                        and status == "completed"
                    ):
                        return True
    except Exception as e:
        logger.warning(f"Error checking holdout log at {log_path}: {e}")
    return False


def is_strategy_name_recorded(
    strategy_name: str,
    log_path: str = DEFAULT_HOLDOUT_LOG,
) -> bool:
    """Verify if a strategy name has completed holdout evaluation previously, regardless of source hash."""
    if not os.path.exists(log_path):
        return False
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            next(reader, None)  # Advance past header row
            for row in reader:
                if len(row) >= 1:
                    strat_recorded = row[0].strip().lower()
                    status = row[3].strip().lower() if len(row) >= 4 else "completed"
                    if strat_recorded == strategy_name.lower() and status == "completed":
                        return True
    except Exception as e:
        logger.warning(f"Error checking holdout log at {log_path}: {e}")
    return False


def record_holdout_run(
    strategy_name: str,
    git_hash: str,
    status: str = "completed",
    log_path: str = DEFAULT_HOLDOUT_LOG,
    timestamp: Optional[str] = None,
):
    """Atomically append a run entry with status to research/reports/holdout_log.csv."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    file_exists = os.path.exists(log_path) and os.path.getsize(log_path) > 0
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["strategy", "timestamp", "git_hash", "status"])
        writer.writerow([strategy_name, ts, git_hash, status])


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
        avail = ", ".join(["TrendPullback", "FVGRetest", "ConfluenceThreshold", "LiquiditySweep", "Donchian4hN20", "Donchian4hN55"])
        raise ValueError(f"Unknown strategy '{strategy_name}'. Available strategies: {avail}")

    strat_cls, canonical_name, source_path = STRATEGY_REGISTRY[key]

    # 0. Refuse to run if working tree has uncommitted changes in strategy or dependencies
    dirty_files = check_working_tree_clean(source_path)
    if dirty_files:
        dirty_msg = "\n".join(f"  - {f}" for f in dirty_files)
        print(f"ERROR: Cannot run holdout validation with uncommitted changes in strategy or dependencies:\n{dirty_msg}")
        logger.error(f"Holdout run blocked: Dirty files detected in working tree:\n{dirty_msg}")
        raise RuntimeError(
            f"Holdout check refused: Git working tree has uncommitted changes in "
            f"strategy or dependencies ({len(dirty_files)} files dirty):\n{dirty_msg}"
        )

    git_hash = get_source_file_git_hash(source_path)

    # 1. Check holdout lock: refuse reruns on identical source code
    if is_holdout_run_recorded(canonical_name, git_hash, log_path=log_path):
        raise RuntimeError(
            f"Holdout run blocked: Strategy '{canonical_name}' has already been evaluated "
            f"on holdout data with source git hash '{git_hash}'. "
            "Holdout data is strictly one-pass to preserve statistical validity."
        )

    # Warn when the same strategy name is evaluated again, regardless of source hash
    if is_strategy_name_recorded(canonical_name, log_path=log_path):
        msg = (
            f"WARNING: Strategy '{canonical_name}' has already been evaluated on holdout data previously! "
            "Evaluating multiple variants on holdout data risks data snooping bias."
        )
        logger.warning(msg)
        import warnings
        warnings.warn(msg, UserWarning)

    # Record start in CSV ledger
    record_holdout_run(canonical_name, git_hash, status="started", log_path=log_path)

    try:
        # 2. Ingest candles: load full history for indicator warm-up
        syms = symbols or DEFAULT_SYMBOLS
        dev_end_dt = pd.to_datetime(DEVELOPMENT_END, utc=True)
        dev_end_ms = int(dev_end_dt.timestamp() * 1000)
        logger.info(f"Loading full history (for indicator warm-up) and holdout data (> {DEVELOPMENT_END}) for {len(syms)} symbols...")

        df_1h_dict: Dict[str, pd.DataFrame] = {}
        df_4h_dict: Dict[str, pd.DataFrame] = {}
        candles_dict: Dict[str, np.ndarray] = {}
        bar_index_map: Dict[str, Dict[int, int]] = {}
        max_ts = 0

        for sym in syms:
            raw_1h = load_candles(sym, "1h", data_dir=data_dir, allow_holdout=True)
            raw_4h = load_candles(sym, "4h", data_dir=data_dir, allow_holdout=True)

            holdout_count = (raw_1h["datetime"] > dev_end_dt).sum()
            if holdout_count == 0:
                raise ValueError(f"No holdout data available for {sym} 1h past {DEVELOPMENT_END}")

            df_1h_dict[sym] = raw_1h
            df_4h_dict[sym] = raw_4h

            # (b) build candle arrays with data.loader.candles_to_array (volume included)
            arr = candles_to_array(raw_1h)
            candles_dict[sym] = arr
            bar_index_map[sym] = {int(arr[idx, 4]): idx for idx in range(len(arr))}
            if len(arr) > 0 and int(arr[-1, 4]) > max_ts:
                max_ts = int(arr[-1, 4])

        max_holdout_str = datetime.fromtimestamp(max_ts / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        # 3. Generate candidate signals with indicator warm-up on full history, but only allow entries/signals after DEVELOPMENT_END
        strat = strat_cls()
        strat_cfg = getattr(strat, "exit_config", None)
        all_candidates: List[CandidateSignal] = []
        for sym in syms:
            sym_sigs = strat.generate_signals(sym, df_1h_dict[sym], df_4h_dict[sym])
            holdout_sigs = [s for s in sym_sigs if s.timestamp_ms > dev_end_ms]
            all_candidates.extend(holdout_sigs)

        # 4. Simulate portfolio execution across holdout passing strategy's exit_config
        sim_res: SimulationResult = simulate_portfolio_signals(
            candidates=all_candidates,
            candles_dict=candles_dict,
            bar_index_map=bar_index_map,
            sizing_mode=sizing_mode,
            exit_config=strat_cfg,
        )

        # Record successful completion in holdout log
        record_holdout_run(canonical_name, git_hash, status="completed", log_path=log_path)
    except Exception:
        record_holdout_run(canonical_name, git_hash, status="failed", log_path=log_path)
        raise

    # 5. Print summary
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
