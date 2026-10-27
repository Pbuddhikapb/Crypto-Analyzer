"""
Unit tests for locked holdout loader protection and research.holdout_check CLI.
"""

import os
import shutil
import tempfile
from unittest.mock import patch

import pandas as pd
import pytest

from data.loader import DEVELOPMENT_END, clear_cache, load_candles
from research.holdout_check import (
    is_holdout_run_recorded,
    record_holdout_run,
    run_holdout_check,
)


def test_default_load_excludes_holdout_bars():
    """Verify load_candles defaults to returning only data up to DEVELOPMENT_END unless allow_holdout=True."""
    assert DEVELOPMENT_END == "2026-04-18"
    clear_cache()

    dev_end_dt = pd.to_datetime(DEVELOPMENT_END, utc=True)

    # 1. Default call (allow_holdout=False)
    df_default = load_candles("BTCUSDT", "1h")
    assert not df_default.empty
    assert df_default["datetime"].max() <= dev_end_dt
    assert (df_default["datetime"] > dev_end_dt).sum() == 0

    # 2. Explicit end_date beyond DEVELOPMENT_END without allow_holdout is still capped
    df_capped = load_candles("BTCUSDT", "1h", end_date="2026-09-01", allow_holdout=False)
    assert df_capped["datetime"].max() <= dev_end_dt

    # 3. Explicit allow_holdout=True returns full dataset including holdout bars
    df_holdout = load_candles("BTCUSDT", "1h", allow_holdout=True)
    assert len(df_holdout) > len(df_default)
    assert df_holdout["datetime"].max() > dev_end_dt
    assert (df_holdout["datetime"] > dev_end_dt).sum() > 0


def test_holdout_command_logs_and_blocks_reruns():
    """Verify holdout command logs executed runs and strictly blocks reruns on the same git hash."""
    temp_dir = tempfile.mkdtemp()
    try:
        log_path = os.path.join(temp_dir, "holdout_log.csv")

        # 1. Initial run on BTCUSDT
        res = run_holdout_check(
            strategy_name="TrendPullback",
            symbols=["BTCUSDT"],
            log_path=log_path,
        )

        assert res["strategy"] == "TrendPullback"
        assert res["total_trades"] > 0
        assert os.path.exists(log_path)

        # 2. Check log file contents
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]

        assert len(lines) == 2  # Header + 1 record
        assert lines[0] == "strategy,timestamp,git_hash"
        cols = lines[1].split(",")
        assert cols[0] == "TrendPullback"
        assert len(cols[2]) == 40  # 40-character git hash

        # 3. Second run must be BLOCKED
        with pytest.raises(RuntimeError, match="Holdout run blocked"):
            run_holdout_check(
                strategy_name="TrendPullback",
                symbols=["BTCUSDT"],
                log_path=log_path,
            )

        # Ensure no additional line was appended
        with open(log_path, "r", encoding="utf-8") as f:
            lines_after = [line.strip() for line in f if line.strip()]
        assert len(lines_after) == 2

    finally:
        shutil.rmtree(temp_dir)


def test_holdout_check_unknown_strategy():
    """Verify passing an unregistered strategy name raises a clean ValueError."""
    with pytest.raises(ValueError, match="Unknown strategy"):
        run_holdout_check("FantasyMoonStrategy")


def test_holdout_check_allows_new_run_if_hash_differs():
    """Verify holdout runner permits execution if the strategy's source hash has changed."""
    temp_dir = tempfile.mkdtemp()
    try:
        log_path = os.path.join(temp_dir, "holdout_log.csv")

        # Simulate first run with hash "1111..."
        record_holdout_run("TrendPullback", "1" * 40, log_path=log_path)
        assert is_holdout_run_recorded("TrendPullback", "1" * 40, log_path=log_path) is True
        assert is_holdout_run_recorded("TrendPullback", "2" * 40, log_path=log_path) is False

        # Mock git hash to "2222..." (simulating source code edit)
        with patch("research.holdout_check.get_source_file_git_hash", return_value="2" * 40):
            with pytest.warns(UserWarning, match="has already been evaluated on holdout data previously"):
                res = run_holdout_check(
                    strategy_name="TrendPullback",
                    symbols=["BTCUSDT"],
                    log_path=log_path,
                )
            assert res["git_hash"] == "2" * 40

        # Now both runs are recorded in log
        with open(log_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        assert len(lines) == 3  # Header + 2 records

    finally:
        shutil.rmtree(temp_dir)


def test_holdout_check_applies_strategy_exit_config_donchian_trailing():
    """
    Verify holdout_check passes the strategy's exit_config to simulate_portfolio_signals,
    confirming Donchian4hN20 uses trailing_1r exit mode rather than stepped.
    """
    temp_dir = tempfile.mkdtemp()
    try:
        log_path = os.path.join(temp_dir, "holdout_log.csv")

        captured_kwargs = {}
        from engine.simulator import simulate_portfolio_signals as real_sim

        def mock_simulate(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return real_sim(*args, **kwargs)

        with patch("research.holdout_check.simulate_portfolio_signals", side_effect=mock_simulate):
            res = run_holdout_check(
                strategy_name="Donchian4hN20",
                symbols=["BTCUSDT"],
                log_path=log_path,
            )

        assert "exit_config" in captured_kwargs
        passed_cfg = captured_kwargs["exit_config"]
        assert passed_cfg is not None
        assert passed_cfg.exit_mode == "trailing_1r", f"Expected exit_mode='trailing_1r', got '{passed_cfg.exit_mode}'"
        assert passed_cfg.trail_dist_r == 1.0

        # Confirm that executed trades do NOT exit via stepped take-profit rules (TP1 or TP2)
        sim_res = res["sim_result"]
        for trade in sim_res.trades:
            assert trade.exit_reason not in ("TAKE_PROFIT_1", "TAKE_PROFIT_2"), (
                f"Trade {trade.trade_id} exited with {trade.exit_reason}, but stepped TPs are forbidden in trailing mode"
            )
    finally:
        shutil.rmtree(temp_dir)


def test_holdout_check_warns_on_repeat_strategy_name():
    """Verify warning is emitted when evaluating the same strategy name again, regardless of source hash."""
    temp_dir = tempfile.mkdtemp()
    try:
        log_path = os.path.join(temp_dir, "holdout_log.csv")

        # First run with hash '1111...'
        record_holdout_run("Donchian4hN20", "1" * 40, log_path=log_path)

        # Second run with different hash '2222...' should emit a warning
        with patch("research.holdout_check.get_source_file_git_hash", return_value="2" * 40):
            with pytest.warns(UserWarning, match="has already been evaluated on holdout data previously"):
                run_holdout_check(
                    strategy_name="Donchian4hN20",
                    symbols=["BTCUSDT"],
                    log_path=log_path,
                )
    finally:
        shutil.rmtree(temp_dir)

