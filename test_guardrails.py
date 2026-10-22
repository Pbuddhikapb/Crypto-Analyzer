"""
test_guardrails.py — Architectural guardrails and integrity verification test suite.

Verifies:
1. Single simulator: simulate_portfolio_signals and ruin guard logic are defined only in engine/simulator.py,
   and imported from engine.simulator by strategy_runner.py, random_baseline.py, and holdout_check.py.
2. No hard-coded evidence: server.py contains no numeric literals assigned to evidence keys.
3. Report matches data: strategy_validation_report.md matches strategy_results.json exactly.
4. Dataset hash: strategy_results.json and baseline_distribution.json hashes match loader hash.
5. Real data path: exact function used to build candle arrays has volume at index 5.
6. Golden end-to-end test: 30-bar dataset with 3 signals matching hand-computed values to 4 decimals.
7. No holdout leakage: load_candles default excludes holdout data, and allow_holdout=True is isolated.
"""

import ast
import glob
import json
import os
import re
from typing import List, Optional

import numpy as np
import pandas as pd

from data.loader import (
    DEVELOPMENT_END,
    candles_to_array,
    clear_cache,
    get_current_dataset_hash,
    load_candles,
)
from engine.simulator import (
    CandidateSignal,
    simulate_portfolio_signals,
)
from research.strategies.base import BaseStrategy, StrategyConfig


# ==============================================================================
# 1. Single Simulator Guardrail (AST-Based)
# ==============================================================================
def test_guardrail_single_simulator_and_imports():
    """
    Assert that simulate_portfolio_signals and ruin guard logic are defined ONLY
    in engine/simulator.py, and that strategy_runner.py, random_baseline.py, and
    holdout_check.py import simulate_portfolio_signals from engine.simulator.
    """
    research_py_files = [f for f in glob.glob("research/*.py") if not f.endswith("__init__.py")]
    assert len(research_py_files) > 0, "No research python files found"

    # A. Verify simulate_portfolio_signals and ruin guard are NOT defined in research/*.py
    for py_file in research_py_files:
        with open(py_file, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=py_file)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assert node.name != "simulate_portfolio_signals", (
                    f"simulate_portfolio_signals is DEFINED in '{py_file}'. "
                    f"It must be defined ONLY in engine/simulator.py."
                )
                assert "ruin_guard" not in node.name.lower(), (
                    f"Ruin guard logic defined in '{py_file}:{node.name}'. "
                    f"Ruin logic must exist ONLY in engine/simulator.py."
                )
            elif isinstance(node, ast.ClassDef):
                assert "ruin" not in node.name.lower(), (
                    f"Ruin class '{node.name}' defined in '{py_file}'. "
                    f"Ruin logic must exist ONLY in engine/simulator.py."
                )

    # B. Verify simulate_portfolio_signals IS defined in engine/simulator.py
    with open("engine/simulator.py", "r", encoding="utf-8") as f:
        engine_tree = ast.parse(f.read(), filename="engine/simulator.py")

    engine_funcs = [
        node.name
        for node in ast.walk(engine_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert "simulate_portfolio_signals" in engine_funcs, (
        "simulate_portfolio_signals must be defined in engine/simulator.py"
    )

    # C. Verify strategy_runner.py, random_baseline.py, and holdout_check.py import it from engine.simulator
    target_files = [
        "research/strategy_runner.py",
        "research/random_baseline.py",
        "research/holdout_check.py",
    ]
    for tf in target_files:
        with open(tf, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=tf)

        imported_from_engine = False
        import_sources = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if any(alias.name == "simulate_portfolio_signals" for alias in node.names):
                    import_sources.append(node.module)
                    if node.module in ("engine.simulator", "engine"):
                        imported_from_engine = True

        assert imported_from_engine, (
            f"'{tf}' must import simulate_portfolio_signals from 'engine.simulator', "
            f"but found import from: {import_sources or 'NOT IMPORTED'}"
        )


# ==============================================================================
# 2. No Hard-Coded Evidence in server.py (AST-Based)
# ==============================================================================
def test_guardrail_no_hardcoded_evidence_in_server():
    """
    Assert that server.py contains no numeric literals assigned to keys or variables
    named win_rate, profit_factor, sample_trades, or expectancy.
    """
    with open("server.py", "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename="server.py")

    target_keys = {"win_rate", "profit_factor", "sample_trades", "expectancy"}

    for node in ast.walk(tree):
        # 1. Check dictionary key-value assignments
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and isinstance(k.value, str):
                    key_str = k.value.lower()
                    if any(target in key_str for target in target_keys):
                        # Positive numeric literal check
                        if isinstance(v, ast.Constant) and isinstance(v.value, (int, float)):
                            raise AssertionError(
                                f"server.py contains hard-coded numeric literal '{v.value}' "
                                f"for evidence key '{k.value}' at line {node.lineno}"
                            )
                        # Negative numeric literal check (ast.UnaryOp with ast.USub)
                        if isinstance(v, ast.UnaryOp) and isinstance(v.operand, ast.Constant):
                            if isinstance(v.operand.value, (int, float)):
                                raise AssertionError(
                                    f"server.py contains hard-coded negative numeric literal "
                                    f"for evidence key '{k.value}' at line {node.lineno}"
                                )

        # 2. Check variable assignments
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    var_str = target.id.lower()
                    if any(target_key in var_str for target_key in target_keys):
                        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
                            raise AssertionError(
                                f"server.py contains hard-coded numeric literal assigned to "
                                f"variable '{target.id}' at line {node.lineno}"
                            )


# ==============================================================================
# 3. Report Matches Data Exactly
# ==============================================================================
def test_guardrail_report_matches_strategy_results():
    """
    Parse the scorecard table in research/reports/strategy_validation_report.md and
    assert each strategy's trade count and win rate equal strategy_results.json.
    """
    json_path = "research/reports/strategy_results.json"
    report_path = "research/reports/strategy_validation_report.md"

    assert os.path.exists(json_path), f"{json_path} missing"
    assert os.path.exists(report_path), f"{report_path} missing"

    with open(json_path, "r", encoding="utf-8") as f:
        results_data = json.load(f)

    with open(report_path, "r", encoding="utf-8") as f:
        report_text = f.read()

    strategies = results_data.get("strategies", {})
    assert len(strategies) > 0, "No strategies found in strategy_results.json"

    for strat_name, strat_meta in strategies.items():
        expected_trades = int(strat_meta["trade_count"])
        expected_wr = float(strat_meta["net_win_rate"])

        # Scorecard regex pattern: | **StrategyName** | 2950 | 48.6% | ...
        pattern = rf"\|\s*\*\*{re.escape(strat_name)}\*\*\s*\|\s*(\d+)\s*\|\s*([\d\.]+)%\s*\|"
        match = re.search(pattern, report_text)
        assert match is not None, (
            f"Strategy '{strat_name}' not found in scorecard table of '{report_path}'"
        )

        report_trades = int(match.group(1))
        report_wr = float(match.group(2))

        assert report_trades == expected_trades, (
            f"Trade count mismatch for '{strat_name}': report={report_trades}, json={expected_trades}"
        )
        assert abs(report_wr - expected_wr) < 0.15, (
            f"Win rate mismatch for '{strat_name}': report={report_wr}%, json={expected_wr}%"
        )


# ==============================================================================
# 4. Dataset Hash Synchronization
# ==============================================================================
def test_guardrail_dataset_hash_synchronization():
    """
    Assert strategy_results.json and baseline_distribution.json dataset hashes
    equal data.loader.get_current_dataset_hash().
    """
    clear_cache()
    current_hash = get_current_dataset_hash()
    assert current_hash is not None and len(current_hash) == 16

    # Verify strategy_results.json
    with open("research/reports/strategy_results.json", "r", encoding="utf-8") as f:
        strat_json = json.load(f)
    strat_hash = strat_json.get("metadata", {}).get("dataset_hash")
    assert strat_hash == current_hash, (
        f"strategy_results.json dataset_hash ('{strat_hash}') != current loader hash ('{current_hash}')"
    )

    # Verify baseline_distribution.json
    with open("research/reports/baseline_distribution.json", "r", encoding="utf-8") as f:
        base_json = json.load(f)
    base_hash = base_json.get("metadata", {}).get("dataset_hash")
    assert base_hash == current_hash, (
        f"baseline_distribution.json dataset_hash ('{base_hash}') != current loader hash ('{current_hash}')"
    )


# ==============================================================================
# 5. Real Data Path Builds Candle Array with Volume at Index 5
# ==============================================================================
def test_guardrail_real_data_path_candle_array_has_volume():
    """
    Use the exact function the runner and baseline use to build candle arrays
    (data.loader.candles_to_array) and assert the array has a volume column at index 5
    for real parquet data.
    """
    df = load_candles("BTCUSDT", "1h")
    assert "volume" in df.columns, "Real candle DataFrame must have volume column"

    # Use exact extracted function
    arr = candles_to_array(df)

    assert isinstance(arr, np.ndarray), "Output must be a numpy ndarray"
    assert arr.ndim == 2, "Array must be 2-dimensional"
    assert arr.shape[1] >= 6, f"Array must have at least 6 columns, got {arr.shape[1]}"

    # Index 5 must be volume
    volume_col = arr[:, 5]
    assert np.all(volume_col >= 0.0), "Volume column values must be non-negative"
    assert np.sum(volume_col) > 0.0, "Volume column must contain non-zero total volume"

    # Verify column ordering: open=0, high=1, low=2, close=3, open_time=4, volume=5
    first_row = df.iloc[0]
    assert arr[0, 0] == float(first_row["open"])
    assert arr[0, 1] == float(first_row["high"])
    assert arr[0, 2] == float(first_row["low"])
    assert arr[0, 3] == float(first_row["close"])
    assert arr[0, 4] == float(first_row["open_time"])
    assert arr[0, 5] == float(first_row["volume"])


# ==============================================================================
# 6. Golden End-to-End Test (Hand-Computed Reference Verification)
# ==============================================================================
class FakeDeterministicStrategy(BaseStrategy):
    """Deterministic fake strategy emitting exactly 3 signals on bars 5, 12, and 20."""

    def __init__(self):
        super().__init__(StrategyConfig(name="FakeDeterministicStrategy", description="Fake strategy for golden test"))

    def generate_signals(
        self,
        symbol: str,
        df_1h: pd.DataFrame,
        df_4h: Optional[pd.DataFrame] = None,
    ) -> List[CandidateSignal]:
        # Emits signals at specific predefined bar indices
        t5 = int(df_1h.iloc[5]["open_time"])
        p5 = float(df_1h.iloc[5]["open"])

        t12 = int(df_1h.iloc[12]["open_time"])
        p12 = float(df_1h.iloc[12]["open"])

        t20 = int(df_1h.iloc[20]["open_time"])
        p20 = float(df_1h.iloc[20]["open"])

        return [
            # Signal 1: Long at Bar 5 ($100.00), Stop Loss at $96.00
            CandidateSignal(symbol=symbol, side="LONG", entry_price=p5, stop_loss=96.0, timestamp_ms=t5),
            # Signal 2: Long at Bar 12 ($200.00), Stop Loss at $196.00
            CandidateSignal(symbol=symbol, side="LONG", entry_price=p12, stop_loss=196.0, timestamp_ms=t12),
            # Signal 3: Long at Bar 20 ($300.00), Stop Loss at $294.00
            CandidateSignal(symbol=symbol, side="LONG", entry_price=p20, stop_loss=294.0, timestamp_ms=t20),
        ]


def test_guardrail_golden_end_to_end_hand_computed_trades():
    """
    Create a tiny synthetic 30-bar dataset and a deterministic fake strategy that emits
    exactly 3 signals. Compute expected entry fill, exit price, fees and net R for each
    trade BY HAND in code comments, then assert the engine matches to 4 decimals.
    """
    # --------------------------------------------------------------------------
    # Synthetic 30-Bar Dataset Construction
    # --------------------------------------------------------------------------
    # Initial Equity: $10,000.00 | Risk per trade: 1.0% ($100.00 initial risk)
    # Friction: taker_fee = 0.05% (0.0005), maker_fee = 0.02% (0.0002), slippage = 0.03% (0.0003)
    # ExitConfig: tp1_r = 0.75 (50% scale), be_arm_r = 0.85, be_offset_pct = 0.05%, tp2_r = 1.33
    t0 = 1700000000000
    interval = 3600 * 1000  # 1 hour

    bars = []
    for i in range(30):
        t = t0 + i * interval
        # Default neutral candle: open=100, high=101, low=99, close=100, volume=1000
        row = [100.0, 101.0, 99.0, 100.0, t, 1000.0]

        # Specific price paths for each trade:
        # Trade 1: Entry at bar 5 open=100.0. Bar 6 drops to low 95.0, triggering STOP_LOSS at 96.0.
        if i == 5:
            row = [100.0, 101.0, 99.0, 100.0, t, 1000.0]
        elif i == 6:
            row = [99.0, 100.0, 95.0, 95.5, t, 1000.0]

        # Trade 2: Entry at bar 12 open=200.0.
        # Bar 13 hits TP1 (high=204.0 >= 203.1050). Bar 14 hits TP2 (high=206.0 >= 205.4598).
        elif i == 12:
            row = [200.0, 201.0, 199.0, 200.0, t, 1000.0]
        elif i == 13:
            row = [201.0, 204.0, 200.0, 203.0, t, 1000.0]
        elif i == 14:
            row = [203.0, 206.0, 202.0, 205.0, t, 1000.0]

        # Trade 3: Entry at bar 20 open=300.0.
        # Bar 21 hits TP1 & arms Breakeven (high=306.0 >= 305.2665).
        # Bar 22 drops below breakeven stop (low=300.0 <= 300.240045).
        elif i == 20:
            row = [300.0, 301.0, 299.0, 300.0, t, 1000.0]
        elif i == 21:
            row = [302.0, 306.0, 301.0, 305.0, t, 1000.0]
        elif i == 22:
            row = [304.0, 304.5, 300.0, 300.1, t, 1000.0]

        bars.append(row)

    df_1h = pd.DataFrame(bars, columns=["open", "high", "low", "close", "open_time", "volume"])
    arr = df_1h[["open", "high", "low", "close", "open_time", "volume"]].to_numpy(dtype=np.float64)
    candles_dict = {"GOLDUSDT": arr}
    bar_index_map = {"GOLDUSDT": {int(arr[k, 4]): k for k in range(len(arr))}}

    # --------------------------------------------------------------------------
    # HAND CALCULATIONS:
    # --------------------------------------------------------------------------
    # TRADE 1 (STOP LOSS HIT):
    #   Nominal Entry = 100.00, Initial SL = 96.00
    #   Nominal Risk Distance = |100.00 - 96.00| = 4.00
    #   Initial Risk Budget = $10,000 * 0.01 = $100.00
    #   Position Shares = $100.00 / 4.00 = 25.0 shares
    #   Notional Size = 25.0 * 100.00 = $2,500.00
    #   Entry Fill Price (0.03% slippage) = 100.00 * (1 + 0.0003) = 100.0300
    #   Entry Fee (taker 0.05% on $2,500.00) = 2500.00 * 0.0005 = $1.2500
    #   State Risk Distance = 100.03 - 96.00 = 4.0300
    #   Bar 6: Low = 95.0 <= 96.0 -> STOP_LOSS triggered at nominal 96.00
    #   Exit Fill Price (adverse stop slippage 0.03%) = 96.00 * (1 - 0.0003) = 95.9712
    #   Exit Fee (taker 0.05% on $2,500.00) = 2500.00 * 0.0005 = $1.2500
    #   Total Fees = 1.2500 + 1.2500 = $2.5000
    #   Gross Return % = ((95.9712 - 100.03) / 100.03) * 100 = -4.057582725%
    #   Gross PnL = $2,500.00 * (-0.04057582725) = -$101.439568 -> round to 2 dec = -$101.44
    #   Net PnL = -$101.439568 - $2.5000 = -$103.939568 -> round to 2 dec = -$103.94
    #   Net R = -$103.939568 / $100.00 = -1.03939568 -> round to 4 dec = -1.0394 R
    #   Realized R = (95.9712 - 100.03) / 4.0300 = -4.0588 / 4.0300 = -1.0071464 -> -1.0071 R
    E1_ENTRY_FILL = 100.0300
    E1_EXIT_FILL = 95.9712
    E1_FEES = 2.5000
    E1_NET_PNL = -103.94
    E1_NET_R = -1.0394

    # TRADE 2 (TP1 HIT + TP2 TARGET HIT):
    #   Nominal Entry = 200.00, Initial SL = 196.00
    #   Nominal Risk Distance = |200.00 - 196.00| = 4.00
    #   Initial Risk Budget = $100.00 -> Shares = 25.0 -> Notional = $5,000.00
    #   Entry Fill Price (0.03% slippage) = 200.00 * (1 + 0.0003) = 200.0600
    #   Entry Fee (taker 0.05% on $5,000.00) = 5000.00 * 0.0005 = $2.5000
    #   State Risk Distance = 200.06 - 196.00 = 4.0600
    #   TP1 Target = 200.06 + (0.75 * 4.0600) = 200.06 + 3.0450 = 203.1050
    #   TP2 Target = 200.06 + (1.33 * 4.0600) = 200.06 + 5.3998 = 205.4598
    #   Bar 13: High 204.0 >= 203.1050 -> PARTIAL_TP1 at 203.1050 (50% scale, $2,500.00 notional)
    #     TP1 Fee (maker 0.02% on $2,500.00) = 2500.00 * 0.0002 = $0.5000
    #     TP1 Gross PnL = $2,500.00 * ((203.1050 - 200.06) / 200.06) = $38.05108467
    #     TP1 Realized R = 3.0450 / 4.0600 = +0.7500 R
    #   Bar 14: High 206.0 >= 205.4598 -> TAKE_PROFIT_2 limit fill at 205.4598 (0% slippage)
    #     TP2 Fee (maker 0.02% on remaining $2,500.00) = 2500.00 * 0.0002 = $0.5000
    #     TP2 Gross PnL = $2,500.00 * ((205.4598 - 200.06) / 200.06) = $67.47725682
    #     TP2 Realized R = 5.3998 / 4.0600 = +1.3300 R
    #   Total Gross PnL = $38.05108467 + $67.47725682 = $105.5283415 -> $105.53
    #   Total Fees = $2.5000 (entry) + $0.5000 (tp1) + $0.5000 (tp2) = $3.5000
    #   Net PnL = $105.5283415 - $3.5000 = $102.0283415 -> round to 2 dec = $102.03
    #   Net R = $102.0283415 / $100.00 = +1.020283415 -> round to 4 dec = +1.0203 R
    #   Combined Realized R = (0.5 * 0.75) + (0.5 * 1.33) = +1.0400 R
    E2_ENTRY_FILL = 200.0600
    E2_EXIT_FILL = 205.4598
    E2_FEES = 3.5000
    E2_NET_PNL = 102.03
    E2_NET_R = 1.0203

    # TRADE 3 (TP1 HIT + BREAKEVEN STOP HIT):
    #   Nominal Entry = 300.00, Initial SL = 294.00
    #   Nominal Risk Distance = |300.00 - 294.00| = 6.00
    #   Initial Risk Budget = $100.00 -> Shares = 16.66666667 -> Notional = $5,000.00
    #   Entry Fill Price (0.03% slippage) = 300.00 * (1 + 0.0003) = 300.0900
    #   Entry Fee (taker 0.05% on $5,000.00) = 5000.00 * 0.0005 = $2.5000
    #   State Risk Distance = 300.09 - 294.00 = 6.0900
    #   TP1 Target = 300.09 + (0.75 * 6.0900) = 300.09 + 4.5675 = 304.6575
    #   Breakeven Arm Excursion = 300.09 + (0.85 * 6.0900) = 300.09 + 5.1765 = 305.2665
    #   Breakeven Stop Price (0.05% offset) = 300.09 * (1 + 0.0005) = 300.240045
    #   Bar 21: High 306.0 >= 305.2665 -> PARTIAL_TP1 at 304.6575 AND Breakeven armed!
    #     TP1 Fee (maker 0.02% on $2,500.00) = 2500.00 * 0.0002 = $0.5000
    #     TP1 Gross PnL = $2,500.00 * ((304.6575 - 300.09) / 300.09) = $38.05108467
    #   Bar 22: Low 300.0 <= 300.240045 -> BREAKEVEN stop triggered at 300.240045
    #     Adverse stop slippage (0.03%): 300.240045 * (1 - 0.0003) = 300.1499729865
    #     Exit Fee (taker 0.05% on remaining $2,500.00) = 2500.00 * 0.0005 = $1.2500
    #     Remaining Gross PnL = $2,500.00 * ((300.1499729865 - 300.09) / 300.09) = $0.499625
    #   Total Gross PnL = $38.05108467 + $0.499625 = $38.550710 -> round to 2 dec = $38.55
    #   Total Fees = $2.5000 (entry) + $0.5000 (tp1) + $1.2500 (exit) = $4.2500
    #   Net PnL = $38.550710 - $4.2500 = $34.300710 -> round to 2 dec = $34.30
    #   Net R = $34.300710 / $100.00 = +0.3430071 -> round to 4 dec = +0.3430 R
    #   Remaining Realized R = (300.1499729865 - 300.09) / 6.0900 = +0.00984778 R
    #   Combined Realized R = (0.5 * 0.75) + (0.5 * 0.00984778) = +0.3799 R
    E3_ENTRY_FILL = 300.0900
    E3_EXIT_FILL = 300.1500  # round(300.149973, 4)
    E3_FEES = 4.2500
    E3_NET_PNL = 34.30
    E3_NET_R = 0.3430

    # --------------------------------------------------------------------------
    # Run the Simulation Engine
    # --------------------------------------------------------------------------
    strat = FakeDeterministicStrategy()
    signals = strat.generate_signals("GOLDUSDT", df_1h)
    assert len(signals) == 3

    result = simulate_portfolio_signals(
        candidates=signals,
        candles_dict=candles_dict,
        bar_index_map=bar_index_map,
        initial_equity_usd=10000.0,
        risk_per_trade_pct=1.0,
        max_open_positions=1,
        max_directional_positions=1,
        sizing_mode="fixed_risk",
    )

    assert result.total_trades == 3
    trades = result.trades
    assert len(trades) == 3

    t1, t2, t3 = trades[0], trades[1], trades[2]

    # Trade 1 Assertions
    assert round(t1.entry_fill_price, 4) == round(E1_ENTRY_FILL, 4)
    assert round(t1.exit_fill_price, 4) == round(E1_EXIT_FILL, 4)
    assert round(t1.fees_usd, 4) == round(E1_FEES, 4)
    assert round(t1.net_pnl_usd, 2) == round(E1_NET_PNL, 2)
    assert round(t1.net_r, 4) == round(E1_NET_R, 4)
    assert t1.exit_reason == "STOP_LOSS"

    # Trade 2 Assertions
    assert round(t2.entry_fill_price, 4) == round(E2_ENTRY_FILL, 4)
    assert round(t2.exit_fill_price, 4) == round(E2_EXIT_FILL, 4)
    assert round(t2.fees_usd, 4) == round(E2_FEES, 4)
    assert round(t2.net_pnl_usd, 2) == round(E2_NET_PNL, 2)
    assert round(t2.net_r, 4) == round(E2_NET_R, 4)
    assert t2.exit_reason == "TAKE_PROFIT_2"

    # Trade 3 Assertions
    assert round(t3.entry_fill_price, 4) == round(E3_ENTRY_FILL, 4)
    assert round(t3.exit_fill_price, 4) == round(E3_EXIT_FILL, 4)
    assert round(t3.fees_usd, 4) == round(E3_FEES, 4)
    assert round(t3.net_pnl_usd, 2) == round(E3_NET_PNL, 2)
    assert round(t3.net_r, 4) == round(E3_NET_R, 4)
    assert t3.exit_reason == "BREAKEVEN"


# ==============================================================================
# 7. No Holdout Leakage Guardrail
# ==============================================================================
def test_guardrail_no_holdout_leakage():
    """
    Assert load_candles() default never returns bars after DEVELOPMENT_END,
    and that only research/holdout_check.py passes allow_holdout=True (repo grep).
    """
    # 1. Verify load_candles default strictly excludes holdout bars
    df_dev = load_candles("BTCUSDT", "1h")
    dev_end_dt = pd.to_datetime(DEVELOPMENT_END, utc=True)
    assert "datetime" in df_dev.columns
    assert (df_dev["datetime"] <= dev_end_dt).all(), (
        f"load_candles default returned timestamps after DEVELOPMENT_END ({DEVELOPMENT_END})"
    )

    # 2. Grep all .py files in repository (excluding tests and venv)
    py_files = [
        f for f in glob.glob("**/*.py", recursive=True)
        if not f.startswith("test_")
        and not f.startswith("venv")
        and "/test_" not in f
    ]
    assert len(py_files) > 0

    holdout_allowed_files = []
    for filepath in py_files:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        if "allow_holdout=True" in content:
            holdout_allowed_files.append(filepath.replace("\\", "/"))

    # Assert that ONLY research/holdout_check.py passes allow_holdout=True
    assert holdout_allowed_files == ["research/holdout_check.py"], (
        f"Only research/holdout_check.py is permitted to pass allow_holdout=True, "
        f"but found in: {holdout_allowed_files}"
    )


# ==============================================================================
# 8. No Binance US Fallback Guardrail
# ==============================================================================
def test_guardrail_no_binance_us_hosts():
    """
    Assert that no '.us' Binance hosts (api.binance.us, stream.binance.us, etc.)
    remain in any non-legacy production code across the repository.
    """
    repo_files = [
        f for f in glob.glob("**/*", recursive=True)
        if os.path.isfile(f)
        and (f.endswith(".py") or f.endswith(".ts") or f.endswith(".tsx") or f.endswith(".sh"))
        and not f.startswith("venv")
        and not f.startswith("legacy")
        and "/legacy/" not in f
        and not f.startswith("test_")
        and "/test_" not in f
        and "node_modules" not in f
        and ".git" not in f
    ]
    assert len(repo_files) > 0, "Expected non-legacy source files to verify"

    us_matches = []
    for filepath in repo_files:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        if re.search(r"binance\.us", content, re.IGNORECASE):
            us_matches.append(filepath.replace("\\", "/"))

    assert us_matches == [], (
        f"Forbidden '.us' Binance hosts found in non-legacy files: {us_matches}"
    )
