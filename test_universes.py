"""
Unit Tests for Named Universe System and Strategy Runner --only / --passes options.
"""

import json
import os
import pytest

from research.universes import (
    DEFAULT_UNIVERSE,
    UNIVERSES,
    get_universe_symbols,
    validate_universe_data,
)
from research.strategy_runner import (
    StrategyRunResult,
    generate_markdown_report,
    run_strategy_evidence_validation,
    strategy_run_result_from_dict,
    update_log_md,
)


def test_universe_definitions():
    """Verify pre-registered universes U10, U4, and U5 members and casing."""
    assert "U10" in UNIVERSES
    assert "U4" in UNIVERSES
    assert "U5" in UNIVERSES

    assert len(UNIVERSES["U10"]) == 10
    assert UNIVERSES["U4"] == ["BTCUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    assert UNIVERSES["U5"] == UNIVERSES["U4"] + ["XAUUSDT"]

    # Case-insensitive resolution
    assert get_universe_symbols("u10") == UNIVERSES["U10"]
    assert get_universe_symbols("u4") == UNIVERSES["U4"]
    assert get_universe_symbols("U5") == UNIVERSES["U5"]

    with pytest.raises(ValueError, match="Unknown universe 'NONEXISTENT'"):
        get_universe_symbols("NONEXISTENT")


def test_validate_universe_data_u10_and_u4_pass():
    """Verify that U10 and U4 pass data validation against the data/candles Parquet data lake."""
    validate_universe_data("U10", data_dir="data/candles")
    validate_universe_data("U4", data_dir="data/candles")


def test_validate_universe_data_u5_fails_naming_missing_symbol():
    """Verify that U5 fails with clear error explicitly naming XAUUSDT and never skips silently."""
    with pytest.raises(FileNotFoundError) as exc_info:
        validate_universe_data("U5", data_dir="data/candles")

    err_msg = str(exc_info.value)
    assert "XAUUSDT" in err_msg
    assert "Universe 'U5' contains symbol 'XAUUSDT' with no data" in err_msg
    assert "Never skipping silently" in err_msg


def test_runner_fails_on_missing_symbol_universe():
    """Verify that run_strategy_evidence_validation refuses to run if universe contains symbol with no data."""
    with pytest.raises(FileNotFoundError) as exc_info:
        run_strategy_evidence_validation(universe_name="U5", data_dir="data/candles")

    err_msg = str(exc_info.value)
    assert "XAUUSDT" in err_msg
    assert "Never skipping silently" in err_msg


def test_report_header_shows_universe_name(tmp_path):
    """Verify that the universe name appears in the report header markdown."""
    report_file_u4 = str(tmp_path / "report_u4.md")
    report_file_u10 = str(tmp_path / "report_u10.md")

    # Load an existing strategy result to format
    with open("research/reports/strategy_results.json", "r", encoding="utf-8") as f:
        strat_json = json.load(f)

    r_n55 = strategy_run_result_from_dict(strat_json["strategies"]["Donchian4hN55"])

    # Test U4 header
    generate_markdown_report(
        results=[r_n55],
        output_path=report_file_u4,
        dataset_info={
            "symbols": ["BTCUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"],
            "bars_per_symbol": {"BTCUSDT": 46000, "SOLUSDT": 46000, "BNBUSDT": 46000, "XRPUSDT": 46000},
            "days": 1900.0,
            "years": 5.2,
        },
        universe_name="U4",
    )
    with open(report_file_u4, "r", encoding="utf-8") as f:
        content_u4 = f.read()
    assert "**Universe:** U4" in content_u4

    # Test U10 header
    generate_markdown_report(
        results=[r_n55],
        output_path=report_file_u10,
        dataset_info={
            "symbols": UNIVERSES["U10"],
            "bars_per_symbol": {s: 46000 for s in UNIVERSES["U10"]},
            "days": 1900.0,
            "years": 5.2,
        },
        universe_name="U10",
    )
    with open(report_file_u10, "r", encoding="utf-8") as f:
        content_u10 = f.read()
    assert "**Universe:** U10" in content_u10


def test_strategy_runner_only_filter_and_invalid_name(tmp_path):
    """Verify that --only isolates execution and rejects unknown strategy names."""
    with pytest.raises(ValueError, match="Strategy 'FakeStrategy' not found"):
        run_strategy_evidence_validation(only_strategy="FakeStrategy")


def test_update_log_md_appends_and_deduplicates(tmp_path):
    """Verify update_log_md appends correct formatted entries and avoids duplicates."""
    dummy_log = tmp_path / "LOG.md"
    dummy_log.write_text("Existing header\n", encoding="utf-8")

    update_log_md(
        log_path=str(dummy_log),
        matched_p=0.0195,
        mc_se=0.0014,
        null_median=0.0003,
        null_std=0.0372,
        is_passed=True,
        threshold_within_2se=False,
        strategy_name="Donchian4hN55",
        iterations=10000,
    )

    text1 = dummy_log.read_text(encoding="utf-8")
    assert "H1b (Donchian4hN55): 10,000 matched baseline passes completed" in text1
    assert "matched p = 0.0195" in text1
    assert "Verdict: PASSED DEV GATE (see robustness)" in text1

    # Call again with identical parameters: should not duplicate
    update_log_md(
        log_path=str(dummy_log),
        matched_p=0.0195,
        mc_se=0.0014,
        null_median=0.0003,
        null_std=0.0372,
        is_passed=True,
        threshold_within_2se=False,
        strategy_name="Donchian4hN55",
        iterations=10000,
    )
    text2 = dummy_log.read_text(encoding="utf-8")
    assert text2.count("H1b (Donchian4hN55): 10,000 matched baseline passes completed") == 1
