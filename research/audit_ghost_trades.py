"""
Audit Ghost Trades Script.

Diagnoses position sizing collapse and ghost trades across the four trading hypotheses:
1. Total trades
2. Trades with position_size_usd < $10
3. Date equity first fell below $100
4. Share of trades closing after that date
5. Win rate on all trades vs Win rate on trades with position_size_usd >= $10
"""

import os
import sys
from datetime import datetime, timezone
from typing import Dict, List

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.loader import load_candles
from engine.simulator import CandidateSignal, ExecutedTrade
from research.strategies import (
    ConfluenceThresholdStrategy,
    FVGRetestStrategy,
    LiquiditySweepStrategy,
    TrendPullbackStrategy,
)
from research.strategy_runner import DEFAULT_SYMBOLS, simulate_portfolio_signals


def run_ghost_trade_audit():
    data_dir = "data/candles"
    syms = DEFAULT_SYMBOLS

    print("=" * 80)
    print("GHOST TRADE AUDIT (FIXED SIMULATOR: FIXED_RISK + RUIN GUARD)")
    print("=" * 80)
    print(f"Loading 1h and 4h Parquet datasets for {len(syms)} symbols...")

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
        bar_index_map[sym] = {int(arr[idx, 4]): idx for idx in range(len(arr))}

    strategies = [
        TrendPullbackStrategy(),
        FVGRetestStrategy(),
        ConfluenceThresholdStrategy(),
        LiquiditySweepStrategy(),
    ]

    for strat in strategies:
        s_name = strat.config.name
        print(f"\n--- Running Strategy: {s_name} ---")

        all_candidates: List[CandidateSignal] = []
        for sym in syms:
            sym_sigs = strat.generate_signals(sym, df_1h_dict[sym], df_4h_dict[sym])
            all_candidates.extend(sym_sigs)

        sim_res = simulate_portfolio_signals(
            candidates=all_candidates,
            candles_dict=candles_dict,
            bar_index_map=bar_index_map,
        )

        trades: List[ExecutedTrade] = sim_res.trades
        total_trades = len(trades)

        if total_trades == 0:
            print(f"[{s_name}] No trades executed.")
            continue

        # 1. Trades with position_size_usd < $10
        ghost_trades = [t for t in trades if t.position_size_usd < 10.0]
        ghost_count = len(ghost_trades)
        ghost_pct = (ghost_count / total_trades) * 100.0

        # 2. Date equity first fell below $100
        equity_below_100_ts = None
        for ts_ms, eq in sim_res.equity_curve:
            if eq < 100.0:
                equity_below_100_ts = ts_ms
                break

        if equity_below_100_ts is not None:
            date_below_100_str = datetime.fromtimestamp(equity_below_100_ts / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            trades_after_ruin = [t for t in trades if t.exit_time_ms >= equity_below_100_ts]
            after_ruin_count = len(trades_after_ruin)
            after_ruin_pct = (after_ruin_count / total_trades) * 100.0
        else:
            date_below_100_str = "Never (< $100 not reached)"
            after_ruin_count = 0
            after_ruin_pct = 0.0

        # Win rates
        all_wins = sum(1 for t in trades if t.net_pnl_usd > 0)
        overall_wr = (all_wins / total_trades) * 100.0

        normal_trades = [t for t in trades if t.position_size_usd >= 10.0]
        normal_wins = sum(1 for t in normal_trades if t.net_pnl_usd > 0)
        normal_wr = (normal_wins / len(normal_trades)) * 100.0 if normal_trades else 0.0

        print(f"Strategy: {s_name}")
        print(f"  Sizing Mode:                      {sim_res.sizing_mode}")
        print(f"  Account Ruined:                   {sim_res.ruined}")
        print(f"  Total Trades:                     {total_trades}")
        print(f"  Reported Win Rate (All):          {overall_wr:.2f}%")
        print(f"  Trades with position < $10:       {ghost_count} ({ghost_pct:.1f}%)")
        print(f"  Win Rate on trades >= $10:        {normal_wr:.2f}% (on {len(normal_trades)} trades)")
        print(f"  Date Equity fell below $100:      {date_below_100_str}")
        print(f"  Trades closing after that date:   {after_ruin_count} ({after_ruin_pct:.1f}%)")


if __name__ == "__main__":
    run_ghost_trade_audit()
