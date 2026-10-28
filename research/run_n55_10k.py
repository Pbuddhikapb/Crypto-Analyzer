"""
Standalone runner to evaluate Donchian4hN55 with 10,000 matched baseline passes.

Execution:
    nohup ./venv/bin/python research/run_n55_10k.py > logs/n55_10k.log 2>&1 &

Features:
- Enforces zero lookahead and institutional friction via Single Exit Authority (engine/exits.py).
- Multiprocessing with worker-invariance (seed + pass_index).
- Chunked, atomic checkpointing in research/checkpoints/n55_10k_matched.json.
- Calculates matched rank p-value, Monte Carlo standard error sqrt(p(1-p)/N), null median, null std.
- Evaluates pre-registered gate at alpha = 0.025.
- Updates strategy_results.json, strategy_validation_report.md, and research/preregistration/LOG.md for Donchian4hN55 only.
"""

import json
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data.loader import candles_to_array, load_candles
from engine.simulator import (
    CandidateSignal,
    SimulationFriction,
    simulate_portfolio_signals,
)
from research.random_baseline import (
    MatchedBaselineResult,
    run_matched_baseline,
)
from research.robustness import RobustnessPanel, compute_robustness_panel
from research.strategies import Donchian4hN55

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("n55_10k_runner")

SYMBOLS = [
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


def update_strategy_results_json(
    json_path: str,
    matched_p: float,
    verdict: str,
    rob_panel: RobustnessPanel,
    matched_res: MatchedBaselineResult,
) -> None:
    """Update Donchian4hN55 fields in research/reports/strategy_results.json."""
    if not os.path.exists(json_path):
        raise FileNotFoundError(f"{json_path} not found")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "strategies" not in data or "Donchian4hN55" not in data["strategies"]:
        raise KeyError("Donchian4hN55 not found in strategy_results.json")

    n55 = data["strategies"]["Donchian4hN55"]
    n55["matched_p_value"] = round(matched_p, 4)
    n55["verdict"] = verdict
    n55["robustness_panel"] = rob_panel.to_dict()
    n55["run_timestamp"] = datetime.now(timezone.utc).isoformat()
    n55["dropped_trade_fraction"] = round(matched_res.dropped_trade_fraction, 6)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    logger.info(f"Updated {json_path} for Donchian4hN55 successfully.")


def update_validation_report_md(
    report_path: str,
    matched_p: float,
    mc_se: float,
    null_median: float,
    verdict: str,
    is_passed: bool,
    threshold_within_2se: bool,
    rob_panel: RobustnessPanel,
) -> None:
    """Update Donchian4hN55 metrics in research/reports/strategy_validation_report.md."""
    if not os.path.exists(report_path):
        raise FileNotFoundError(f"{report_path} not found")

    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update Scorecard row
    # Old pattern:
    # | **Donchian4hN55** | 832 | 37.6% | **1.22** (1.31) | **+0.0788R** (+0.1047R) | 16.69R | [+0.0021, +0.1590]R | +0.0000R | `p = 0.0240` | 0.00% | 1933d (100.0%) | `fixed_risk` | No | 🟢 **PASSED DEV GATE (see robustness)** |
    verdict_badge = (
        "🟢 **PASSED DEV GATE (see robustness)**"
        if is_passed
        else "🔴 **REJECTED**"
    )
    scorecard_sub = (
        f"| **Donchian4hN55** | 832 | 37.6% | **1.22** (1.31) | **+0.0788R** (+0.1047R) | 16.69R | "
        f"[+0.0021, +0.1590]R | {null_median:+.4f}R | `p = {matched_p:.4f}` | 0.00% | "
        f"1933d (100.0%) | `fixed_risk` | No | {verdict_badge} |"
    )
    content = re.sub(
        r"\|\s*\*\*Donchian4hN55\*\*\s*\|.*?\|",
        scorecard_sub,
        content,
        count=1,
    )

    # 2. Update Deep-Dive header block
    # > **Statistical Verdict**: **`PASSED DEV GATE (see robustness) (p < 0.025)`**
    # > Matched Rank $p$-value: **`p = 0.0240`** (Empirical rank within 1,000 matched null passes)
    content = re.sub(
        r"(### Hypothesis: Donchian4hN55\n\*\*Concept:\*\*.*?\n\n>\s*\*\*Statistical Verdict\*\*:\s*\*\*`)[^`]+(`\*\*)",
        rf"\g<1>{verdict}\g<2>",
        content,
    )
    content = re.sub(
        r"(>\s*Matched Rank \$p\$-value:\s*\*\*`p = )[\d\.]+(`\*\*\s*\(Empirical rank within )[\d,]+( matched null passes\))",
        rf"\g<1>{matched_p:.4f}\g<2>10,000\g<3>",
        content,
    )
    content = re.sub(
        r"(>\s*Matched Baseline Median Net EV:\s*\*\*`)[^`]+(`\*\*)",
        rf"\g<1>{null_median:+.4f}R\g<2>",
        content,
    )

    # 3. Update Requirements list in Donchian4hN55 deep-dive
    p_check_line = (
        f"- ✅ Matched Rank p-value {matched_p:.4f} < 0.025"
        if is_passed
        else f"- ❌ Matched Rank p-value {matched_p:.4f} >= 0.025 (pre-registered threshold: 0.025)"
    )

    if is_passed:
        # replace any previous p-check line
        content = re.sub(
            r"-\s*[✅❌]\s*Matched Rank p-value [\d\.]+ [<>]=? 0\.025.*",
            p_check_line,
            content,
        )
    else:
        # Move to failed/unmet
        content = re.sub(
            r"-\s*[✅❌]\s*Matched Rank p-value [\d\.]+ [<>]=? 0\.025.*",
            "",
            content,
        )
        content = re.sub(
            r"(#### Alpha Validation Requirements:\n\*\*Passed:\*\*.*?\n\n\*\*Failed / Unmet:\*\*\n)- None",
            rf"\g<1>{p_check_line}",
            content,
            flags=re.DOTALL,
        )

    # 4. Update Robustness Panel table in Donchian4hN55
    # | **Matched $p$-value** | `0.0240` (N=1,000) | Gate threshold $\alpha = 0.025$ |
    # | **Monte Carlo Standard Error** | `SE = 0.0048` | $\sqrt{p(1-p)/N}$ |
    # | **Threshold within 2 SE?** | ⚠️ **YES (p is within 2 MC SE of threshold)** | $|p - \alpha| \le 2 \times SE$ |
    content = re.sub(
        r"(\|\s*\*\*Matched \$p\$-value\*\*\s*\|\s*`)[^`]+(` \(N=)[\d,]+(\)\s*\|)",
        rf"\g<1>{matched_p:.4f}\g<2>10,000\g<3>",
        content,
    )
    content = re.sub(
        r"(\|\s*\*\*Monte Carlo Standard Error\*\*\s*\|\s*`SE = )[^`]+(`\s*\|)",
        rf"\g<1>{mc_se:.4f}\g<2>",
        content,
    )
    within_2se_str = (
        "⚠️ **YES (p is within 2 MC SE of threshold)**"
        if threshold_within_2se
        else "No"
    )
    content = re.sub(
        r"(\|\s*\*\*Threshold within 2 SE\?\*\*\s*\|\s*)[^|]+(\s*\|\s*\$\|p - \\alpha\|)",
        rf"\g<1>{within_2se_str}\g<2>",
        content,
    )

    # 5. Update Strategic Implications section
    if is_passed:
        strat_sec = (
            "1. **Passed Dev Gate (see robustness) (1/6)**:\n"
            "   The following strategies met all pre-registered dev gate criteria ($p < 0.05$, positive bootstrap CI lower bound):\n"
            f"   - **Donchian4hN55**: Net EV = `+0.0788R` (Gross: `+0.1047R`), Net PF = `1.22`, Bootstrap 95% CI = `[+0.0021, +0.1590]R`, Matched Rank $p$-value = `{matched_p:.4f}` (N=10,000)\n\n"
            "2. **Unvalidated Patterns**:\n"
            "   The following strategies failed validation: **TrendPullback**, **FVGRetest**, **ConfluenceThreshold**, **LiquiditySweep**, **Donchian4hN20**. These setups show no validated edge under institutional friction and will display warning banners."
        )
    else:
        strat_sec = (
            "1. **Passed Dev Gate (0/6)**:\n"
            "   Zero strategies met all pre-registered dev gate criteria. All 6 candidate hypotheses are statistically rejected.\n\n"
            "2. **Unvalidated Patterns (6/6)**:\n"
            f"   The following strategies failed validation: **TrendPullback**, **FVGRetest**, **ConfluenceThreshold**, **LiquiditySweep**, **Donchian4hN20**, **Donchian4hN55** (N=10,000 matched p={matched_p:.4f} >= 0.025). All setups show no validated edge over noise hurdles under institutional friction and will display warning banners."
        )

    content = re.sub(
        r"1\.\s*\*\*Passed Dev Gate.*?(?=3\.\s*\*\*Evidence-Backed Radar Screening\*\*:)",
        strat_sec + "\n\n",
        content,
        flags=re.DOTALL,
    )

    with open(report_path, "w", encoding="utf-8") as f:
        content = f.write(content)

    logger.info(f"Updated {report_path} successfully.")


def update_log_md(
    log_path: str,
    matched_p: float,
    mc_se: float,
    null_median: float,
    null_std: float,
    is_passed: bool,
    threshold_within_2se: bool,
) -> None:
    """Append the definitive 10,000-pass outcome for H1b / Donchian4hN55 to research/preregistration/LOG.md."""
    if not os.path.exists(log_path):
        raise FileNotFoundError(f"{log_path} not found")

    with open(log_path, "r", encoding="utf-8") as f:
        existing = f.read().strip()

    p_2se_below = (matched_p + 2.0 * mc_se) < 0.025

    if is_passed:
        entry = (
            f"H1b (Donchian4hN55): 10,000 matched baseline passes completed. "
            f"matched p = {matched_p:.4f}, Monte Carlo SE = {mc_se:.4f}, "
            f"null median = {null_median:+.4f}R, null std = {null_std:.4f}R. "
            f"p + 2*SE = {matched_p + 2.0 * mc_se:.4f} (below 0.025: {p_2se_below}). "
            f"Verdict: PASSED DEV GATE (see robustness) (p < 0.025, threshold=0.025). "
            f"Holdout pending."
        )
    else:
        entry = (
            f"H1b (Donchian4hN55): 10,000 matched baseline passes completed. "
            f"matched p = {matched_p:.4f}, Monte Carlo SE = {mc_se:.4f}, "
            f"null median = {null_median:+.4f}R, null std = {null_std:.4f}R. "
            f"p + 2*SE = {matched_p + 2.0 * mc_se:.4f} (below 0.025: {p_2se_below}). "
            f"Verdict: REJECTED per pre-registered rule (p = {matched_p:.4f} >= 0.025). "
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


def main() -> None:
    t_start = time.time()
    logger.info("Starting Donchian4hN55 10,000-pass evaluation...")

    # 1. Load historical development data
    logger.info("Loading 1h and 4h Parquet datasets for 10 symbols...")
    df_1h_dict: Dict[str, Any] = {}
    df_4h_dict: Dict[str, Any] = {}
    candles_dict: Dict[str, np.ndarray] = {}
    bar_index_map: Dict[str, Dict[int, int]] = {}

    for sym in SYMBOLS:
        df_1 = load_candles(sym, "1h", data_dir="data/candles")
        df_4 = load_candles(sym, "4h", data_dir="data/candles")
        df_1h_dict[sym] = df_1
        df_4h_dict[sym] = df_4
        arr = candles_to_array(df_1)
        candles_dict[sym] = arr
        bar_index_map[sym] = {int(arr[idx, 4]): idx for idx in range(len(arr))}

    # 2. Generate Candidate Signals
    strat = Donchian4hN55()
    all_candidates: List[CandidateSignal] = []
    for sym in SYMBOLS:
        sym_sigs = strat.generate_signals(sym, df_1h_dict[sym], df_4h_dict[sym])
        all_candidates.extend(sym_sigs)

    logger.info(f"Generated {len(all_candidates)} signals across {len(SYMBOLS)} symbols.")

    # 3. Simulate Institutional Portfolio Execution
    fric = SimulationFriction()
    sim_res = simulate_portfolio_signals(
        candidates=all_candidates,
        candles_dict=candles_dict,
        bar_index_map=bar_index_map,
        sizing_mode="fixed_risk",
        exit_config=strat.exit_config,
    )

    trade_count = len(sim_res.trades)
    logger.info(
        f"Portfolio execution simulated: {trade_count} trades. "
        f"Mean Net R: {sim_res.expectancy_r:+.4f}R, Net PF: {sim_res.profit_factor:.2f}"
    )

    if trade_count != 832:
        logger.warning(f"Unexpected trade count: {trade_count} (expected 832)")

    # 4. Run Matched Baseline with 10,000 passes
    checkpoint_file = "research/checkpoints/n55_10k_matched.json"
    iterations = 10000
    workers = os.cpu_count() or 4
    chunk_size = 250

    logger.info(
        f"Running matched baseline for Donchian4hN55: {iterations:,} passes, "
        f"workers={workers}, checkpoint={checkpoint_file}, chunk_size={chunk_size}..."
    )

    matched_res = run_matched_baseline(
        strategy_trades=sim_res.trades,
        candles_dict=candles_dict,
        iterations=iterations,
        seed=42,
        friction=fric,
        exit_config=strat.exit_config,
        workers=workers,
        checkpoint_path=checkpoint_file,
        chunk_size=chunk_size,
    )

    matched_p = matched_res.empirical_p_value
    null_median = matched_res.median_expectancy_r
    null_std = matched_res.std_expectancy_r
    mc_se = math.sqrt(matched_p * (1.0 - matched_p) / float(iterations))
    p_plus_2se = matched_p + 2.0 * mc_se
    p_plus_2se_below_threshold = p_plus_2se < 0.025
    is_passed = matched_p < 0.025
    threshold_within_2se = abs(matched_p - 0.025) <= (2.0 * mc_se)

    if is_passed:
        verdict = "PASSED DEV GATE (see robustness) (p < 0.025)"
    else:
        verdict = "REJECTED (p >= 0.025)"

    total_time = time.time() - t_start

    # Compute Robustness Panel
    rob_panel = compute_robustness_panel(
        strategy_name="Donchian4hN55",
        trades=sim_res.trades,
        matched_p=matched_p,
        mc_iterations=iterations,
        significance_level=0.025,
    )

    logger.info("=" * 70)
    logger.info("DONCHIAN4HN55 10,000-PASS EVALUATION RESULTS:")
    logger.info(f"  Iterations (N):              {iterations:,}")
    logger.info(f"  Strategy Mean Net R:         {sim_res.expectancy_r:+.4f}R")
    logger.info(f"  Matched Empirical p-value:   {matched_p:.4f}")
    logger.info(f"  Monte Carlo SE:              {mc_se:.4f}")
    logger.info(f"  p + 2*SE:                    {p_plus_2se:.4f}")
    logger.info(f"  p + 2*SE < 0.025:            {p_plus_2se_below_threshold}")
    logger.info(f"  Threshold within 2*SE:       {threshold_within_2se}")
    logger.info(f"  Null Median Expectancy:      {null_median:+.4f}R")
    logger.info(f"  Null Std Expectancy:         {null_std:.4f}R")
    logger.info(f"  Verdict:                     {verdict}")
    logger.info(f"  Elapsed Time:                {total_time:.1f}s ({total_time/60.0:.1f} min)")
    logger.info("=" * 70)

    # 5. Update Reports & Logs
    json_path = "research/reports/strategy_results.json"
    report_path = "research/reports/strategy_validation_report.md"
    log_path = "research/preregistration/LOG.md"

    update_strategy_results_json(json_path, matched_p, verdict, rob_panel, matched_res)
    update_validation_report_md(
        report_path,
        matched_p,
        mc_se,
        null_median,
        verdict,
        is_passed,
        threshold_within_2se,
        rob_panel,
    )
    update_log_md(
        log_path,
        matched_p,
        mc_se,
        null_median,
        null_std,
        is_passed,
        threshold_within_2se,
    )

    logger.info("All files updated successfully.")


if __name__ == "__main__":
    main()
