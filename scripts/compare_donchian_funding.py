#!/usr/bin/env python3
"""
Standalone Comparison Script: Donchian4hN55 Flat vs. Real Funding Rate Impact.

Evaluates Donchian4hN55 on strictly dev data (<= DEVELOPMENT_END = 2026-04-18)
under two friction models:
  1. Flat funding fallback (0.01% / 8 hours flat drag)
  2. Real Binance USD-M Futures funding rates (loaded from data/funding/*.parquet)

DOES NOT modify or overwrite strategy_results.json or strategy_validation_report.md.
"""

import argparse
import os
import sys

# Ensure repository root is on sys.path when script is executed directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Dict, List

import numpy as np
import pandas as pd

from data.downloader import DEFAULT_SYMBOLS, download_universe_funding
from data.loader import (
    DEVELOPMENT_END,
    candles_to_array,
    load_all_funding_rates,
    load_candles,
)
from domain.signals import CandidateSignal
from engine.simulator import SimulationResult, simulate_portfolio_signals
from research.strategies.donchian_4h import Donchian4hN55


def run_comparison(
    funding_dir: str = "data/funding",
    data_dir: str = "data/candles",
    auto_fetch: bool = False,
    symbols: List[str] = DEFAULT_SYMBOLS,
) -> int:
    """Run Donchian4hN55 with flat vs real funding rates and print comparison table."""
    print("=" * 80)
    print("Donchian4hN55: Funding Rate Impact Comparison (Dev Data)")
    print(f"Development Cutoff: <= {DEVELOPMENT_END}")
    print(f"Universe: {len(symbols)} symbols ({', '.join(symbols)})")
    print(f"Funding Data Directory: {funding_dir}")
    print("=" * 80)

    # 1. Check if funding data exists locally
    funding_files_exist = (
        os.path.isdir(funding_dir)
        and any(f.endswith(".parquet") for f in os.listdir(funding_dir))
    )

    if not funding_files_exist:
        if auto_fetch:
            print(f"\n[INFO] Funding data not found in '{funding_dir}'. Fetching from Binance Vision...")
            download_universe_funding(
                symbols=symbols,
                start_year=2021,
                start_month=1,
                end_year=2026,
                end_month=10,
                output_dir=funding_dir,
            )
        else:
            print(f"\n[ERROR] No funding rate Parquet files found in '{funding_dir}'.")
            print("To download official Binance USD-M Futures funding history, run:")
            print("    ./venv/bin/python -m data.downloader --funding --start-year 2021 --start-month 1 --end-year 2026 --end-month 10")
            print("Or run this script with --fetch to automatically download:")
            print("    ./venv/bin/python scripts/compare_donchian_funding.py --fetch\n")
            return 1

    # 2. Ingest dev candles (strictly <= DEVELOPMENT_END)
    print("\n[1/4] Loading dev candle datasets (1h and 4h)...")
    df_1h_dict: Dict[str, pd.DataFrame] = {}
    df_4h_dict: Dict[str, pd.DataFrame] = {}
    candles_dict: Dict[str, np.ndarray] = {}
    bar_index_map: Dict[str, Dict[int, int]] = {}

    for sym in symbols:
        df_1 = load_candles(sym, "1h", data_dir=data_dir)
        df_4 = load_candles(sym, "4h", data_dir=data_dir)
        if df_1.empty or df_4.empty:
            raise ValueError(f"Candle data for {sym} is empty in '{data_dir}'.")
        df_1h_dict[sym] = df_1
        df_4h_dict[sym] = df_4
        arr = candles_to_array(df_1)
        candles_dict[sym] = arr
        bar_index_map[sym] = {int(arr[idx, 4]): idx for idx in range(len(arr))}

    # 3. Generate candidate signals for Donchian4hN55
    print("[2/4] Generating candidate signals for Donchian4hN55...")
    strat = Donchian4hN55()
    all_candidates: List[CandidateSignal] = []
    for sym in symbols:
        sym_sigs = strat.generate_signals(sym, df_1h_dict[sym], df_4h_dict[sym])
        all_candidates.extend(sym_sigs)
    print(f"      Total candidate signals generated: {len(all_candidates):,}")

    # 4. Simulation 1: Flat Fallback Funding (0.01%/8h)
    print("[3/4] Simulating with Flat Funding (0.01%/8h fallback)...")
    res_flat: SimulationResult = simulate_portfolio_signals(
        candidates=all_candidates,
        candles_dict=candles_dict,
        bar_index_map=bar_index_map,
        exit_config=strat.exit_config,
        funding_rates=None,
    )

    # 5. Simulation 2: Real Binance Funding Rates
    print("[4/4] Simulating with Real Binance USD-M Futures Funding Rates...")
    funding_rates = load_all_funding_rates(
        symbols,
        data_dir=funding_dir,
        allow_holdout=False,
    )
    if not funding_rates:
        print(f"[ERROR] Failed to load funding rates from '{funding_dir}'.")
        return 1

    res_real: SimulationResult = simulate_portfolio_signals(
        candidates=all_candidates,
        candles_dict=candles_dict,
        bar_index_map=bar_index_map,
        exit_config=strat.exit_config,
        funding_rates=funding_rates,
        max_funding_fallback_fraction=0.01,
    )

    # 6. Comparative Presentation
    flat_fb_pct = (res_flat.funding_fallback_periods / res_flat.funding_total_periods * 100.0) if res_flat.funding_total_periods > 0 else 0.0
    real_fb_pct = (res_real.funding_fallback_periods / res_real.funding_total_periods * 100.0) if res_real.funding_total_periods > 0 else 0.0

    delta_trades = res_real.total_trades - res_flat.total_trades
    delta_wr = res_real.win_rate_pct - res_flat.win_rate_pct
    delta_pf = res_real.profit_factor - res_flat.profit_factor
    delta_ev = res_real.expectancy_r - res_flat.expectancy_r
    delta_cum_r = res_real.cumulative_r - res_flat.cumulative_r
    delta_max_dd = res_real.max_drawdown_r - res_flat.max_drawdown_r
    delta_net_usd = res_real.net_profit_usd - res_flat.net_profit_usd

    print("\n" + "=" * 80)
    print("DONCHIAN4HN55 FUNDING FRICTION IMPACT ANALYSIS (DEV DATA)")
    print("=" * 80)
    header = f"{'Metric':<28} | {'Flat Funding (0.01%/8h)':<23} | {'Real Binance Funding':<23} | {'Change (Delta)':<14}"
    print(header)
    print("-" * len(header))
    print(f"{'Total Trades Executed':<28} | {res_flat.total_trades:<23} | {res_real.total_trades:<23} | {delta_trades:+d}")
    print(f"{'Win Rate (%)':<28} | {res_flat.win_rate_pct:<22.2f}% | {res_real.win_rate_pct:<22.2f}% | {delta_wr:+.2f}%")
    print(f"{'Net Profit Factor':<28} | {res_flat.profit_factor:<23.4f} | {res_real.profit_factor:<23.4f} | {delta_pf:+.4f}")
    print(f"{'Mean Net R (Expectancy)':<28} | {res_flat.expectancy_r:<+23.4f}R | {res_real.expectancy_r:<+23.4f}R | {delta_ev:+.4f}R")
    print(f"{'Cumulative Realized Net R':<28} | {res_flat.cumulative_r:<+23.3f}R | {res_real.cumulative_r:<+23.3f}R | {delta_cum_r:+.3f}R")
    print(f"{'Max Drawdown (R)':<28} | {res_flat.max_drawdown_r:<23.2f}R | {res_real.max_drawdown_r:<23.2f}R | {delta_max_dd:+.2f}R")
    print(f"{'Net Profit (USD)':<28} | ${res_flat.net_profit_usd:<22,.2f} | ${res_real.net_profit_usd:<22,.2f} | ${delta_net_usd:+,.2f}")
    print(f"{'Gross Profit Factor':<28} | {res_flat.gross_profit_factor:<23.4f} | {res_real.gross_profit_factor:<23.4f} | {res_real.gross_profit_factor - res_flat.gross_profit_factor:+.4f}")
    print(f"{'Gross Expectancy R':<28} | {res_flat.gross_expectancy_r:<+23.4f}R | {res_real.gross_expectancy_r:<+23.4f}R | {res_real.gross_expectancy_r - res_flat.gross_expectancy_r:+.4f}R")
    print("-" * len(header))
    print(f"{'Funding Periods (Total)':<28} | {res_flat.funding_total_periods:<23} | {res_real.funding_total_periods:<23} | {res_real.funding_total_periods - res_flat.funding_total_periods:+d}")
    print(f"{'Funding Fallback Periods':<28} | {res_flat.funding_fallback_periods:<15} ({flat_fb_pct:.1f}%) | {res_real.funding_fallback_periods:<15} ({real_fb_pct:.2f}%) | {res_real.funding_fallback_periods - res_flat.funding_fallback_periods:+d}")
    print("=" * 80)
    print("\nPreregistration / Audit Note:")
    print("strategy_results.json and strategy_validation_report.md have NOT been modified.")
    print("Review the delta above to evaluate whether dynamic funding requires a hypothesis version revision.\n")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Compare Donchian4hN55 performance under flat vs. real Binance funding rates."
    )
    parser.add_argument(
        "--funding-dir",
        type=str,
        default="data/funding",
        help="Directory containing funding rate Parquet archives (default: data/funding)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/candles",
        help="Directory containing candle Parquet archives (default: data/candles)",
    )
    parser.add_argument(
        "--fetch",
        action="store_true",
        help="Download funding archives from Binance Vision if data/funding is missing",
    )
    args = parser.parse_args()

    sys.exit(
        run_comparison(
            funding_dir=args.funding_dir,
            data_dir=args.data_dir,
            auto_fetch=args.fetch,
        )
    )


if __name__ == "__main__":
    main()
