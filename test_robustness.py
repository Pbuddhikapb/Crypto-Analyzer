"""
Unit tests for research.robustness module:
(a) Matched p and Monte Carlo standard error with 2 SE threshold flag;
(b) iid bootstrap 2.5% bound over 30 seeds;
(c) Week-block bootstrap 95% CI;
(d) Calendar year performance breakdown;
(e) Top-10 outlier dependency analysis;
(f) Per-trade standard deviation and sample size for 80% power.
"""

import math
from datetime import datetime, timezone

from engine.simulator import ExecutedTrade
from research.robustness import (
    compute_annual_breakdown,
    compute_iid_bootstrap_seeds,
    compute_outlier_impact,
    compute_robustness_panel,
    compute_sample_power,
    compute_week_block_bootstrap_ci,
)


def _make_trade(
    trade_id: str,
    net_r: float,
    entry_time_ms: int,
    symbol: str = "BTCUSDT",
) -> ExecutedTrade:
    """Helper to create minimal valid ExecutedTrade for testing."""
    return ExecutedTrade(
        trade_id=trade_id,
        symbol=symbol,
        side="LONG" if net_r >= 0 else "SHORT",
        entry_time_ms=entry_time_ms,
        exit_time_ms=entry_time_ms + 3600000 * 5,
        entry_price=100.0,
        entry_fill_price=100.0,
        exit_price=100.0 + net_r * 5.0,
        exit_fill_price=100.0 + net_r * 5.0,
        exit_reason="STOP_LOSS" if net_r < 0 else "TRAILING_STOP",
        position_size_usd=1000.0,
        gross_pnl_usd=net_r * 50.0,
        fees_usd=1.0,
        slippage_usd=1.0,
        funding_cost_usd=0.0,
        net_pnl_usd=net_r * 50.0 - 2.0,
        net_return_pct=net_r * 5.0,
        realized_r=net_r,
        bars_held=5,
        mfe_r=max(0.0, net_r + 0.5),
        mae_r=min(0.0, net_r - 0.2),
        initial_risk_usd=50.0,
        net_r=net_r,
    )


def test_mc_standard_error_and_flag():
    """Verify (a) Monte Carlo standard error and 2-SE threshold flagging."""
    # H1b scenario: p = 0.0240, N = 10,000 passes, alpha = 0.025
    panel = compute_robustness_panel(
        strategy_name="Donchian4hN55",
        trades=[_make_trade("t1", 0.1, 1609459200000)],
        matched_p=0.0240,
        mc_iterations=10000,
        significance_level=0.025,
    )

    # SE = sqrt(0.024 * 0.976 / 10000) ~ 0.001530
    expected_se = math.sqrt(0.0240 * (1.0 - 0.0240) / 10000)
    assert panel.mc_se is not None
    assert abs(panel.mc_se - expected_se) < 1e-4
    # |0.0240 - 0.0250| = 0.0010 <= 2 * 0.001530 = 0.003061 -> MUST BE FLAGGED
    assert panel.threshold_within_2se is True

    # Far-from-threshold scenario: p = 0.0600, alpha = 0.025
    panel_unflagged = compute_robustness_panel(
        strategy_name="Donchian4hN20",
        trades=[_make_trade("t1", 0.1, 1609459200000)],
        matched_p=0.0600,
        mc_iterations=10000,
        significance_level=0.025,
    )
    # |0.0600 - 0.0250| = 0.0350 > 2 * SE (~0.0047) -> NOT FLAGGED
    assert panel_unflagged.threshold_within_2se is False


def test_iid_bootstrap_30_seeds():
    """Verify (b) iid bootstrap 2.5% quantile bounds across 30 seeds."""
    # Clearly positive data
    pos_data = [0.10, 0.15, -0.05, 0.20, 0.08, -0.02, 0.12] * 20
    share_above, min_b, max_b, mean_b, bounds = compute_iid_bootstrap_seeds(pos_data, num_seeds=30)

    assert len(bounds) == 30
    assert share_above == 1.0  # All seeds should have 2.5% bound > 0
    assert min_b <= mean_b <= max_b
    assert min_b > 0.0

    # Clearly negative data
    neg_data = [-0.10, -0.15, 0.05, -0.20, -0.08, 0.02, -0.12] * 20
    share_neg, _, _, _, _ = compute_iid_bootstrap_seeds(neg_data, num_seeds=30)
    assert share_neg == 0.0  # None of the seeds should have bound > 0


def test_week_block_bootstrap_ci():
    """Verify (c) week-block bootstrap 95% CI."""
    base_ts = 1609459200000  # 2021-01-01 00:00 UTC
    one_week_ms = 7 * 24 * 3600 * 1000

    trades = []
    # 20 weeks of trades, 5 trades per week
    for w in range(20):
        w_ts = base_ts + w * one_week_ms
        for i in range(5):
            t_ts = w_ts + i * 3600000
            net_r = 0.10 if (w % 2 == 0) else -0.05
            trades.append(_make_trade(f"w_{w}_{i}", net_r, t_ts))

    ci_low, ci_high = compute_week_block_bootstrap_ci(trades, n_resamples=2000, seed=42)
    assert ci_low < ci_high
    # Mean of (0.10 and -0.05) is +0.025
    assert ci_low < 0.025 < ci_high


def test_annual_breakdown():
    """Verify (d) mean net R and trade count per calendar year."""
    # 2021 trade
    t2021_1 = _make_trade("t1", 0.5, int(datetime(2021, 3, 1, tzinfo=timezone.utc).timestamp() * 1000))
    t2021_2 = _make_trade("t2", -0.1, int(datetime(2021, 8, 1, tzinfo=timezone.utc).timestamp() * 1000))

    # 2022 trade
    t2022_1 = _make_trade("t3", 0.2, int(datetime(2022, 5, 1, tzinfo=timezone.utc).timestamp() * 1000))

    breakdown = compute_annual_breakdown([t2021_1, t2021_2, t2022_1])
    assert 2021 in breakdown
    assert 2022 in breakdown
    assert breakdown[2021]["n"] == 2
    assert breakdown[2021]["win_rate_pct"] == 50.0
    assert abs(breakdown[2021]["mean_net_r"] - 0.2) < 1e-4
    assert breakdown[2022]["n"] == 1
    assert breakdown[2022]["win_rate_pct"] == 100.0


def test_outlier_impact():
    """Verify (e) top-10 trades' share of total R and mean without them."""
    # 15 trades: 10 large winners (+1.0R each) and 5 normal trades (+0.1R each)
    net_r_vals = [1.0] * 10 + [0.1] * 5
    tot_r, top10_share, top10_tot, mean_without = compute_outlier_impact(net_r_vals, top_n=10)

    # Total R = 10.5R, top 10 = 10.0R
    assert abs(tot_r - 10.5) < 1e-4
    assert abs(top10_tot - 10.0) < 1e-4
    assert abs(top10_share - (10.0 / 10.5 * 100.0)) < 1e-2
    # Mean without top 10 = 0.5 / 5 = 0.10R
    assert abs(mean_without - 0.10) < 1e-4


def test_power_sample_size():
    """Verify (f) standard deviation and sample size for 80% power."""
    # Power for positive mean: mu = 0.08R, s = 0.80R
    # (z_0.975 + z_0.80) = (1.95996 + 0.84162) = 2.80158
    # N = ceil((2.80158 * 0.80 / 0.08)^2) = ceil(28.0158^2) = ceil(784.88) = 785
    n_one, n_two = compute_sample_power(mean_net_r=0.08, std_net_r=0.80, significance_level=0.025)
    assert n_one is not None
    assert n_two is not None
    # For alpha=0.025 one-sided, z = 1.960, so n_one == 785
    assert n_one == 785

    # If mean <= 0, power is undefined / None
    n_neg_one, n_neg_two = compute_sample_power(mean_net_r=-0.05, std_net_r=0.80, significance_level=0.05)
    assert n_neg_one is None
    assert n_neg_two is None


def test_robustness_panel_full_orchestration():
    """Verify full RobustnessPanel creation, dict serialization, and markdown generation."""
    trades = [
        _make_trade(f"t_{i}", 0.05 + 0.01 * (i % 5), 1609459200000 + i * 3600000 * 24)
        for i in range(50)
    ]
    panel = compute_robustness_panel(
        strategy_name="TestAlpha",
        trades=trades,
        matched_p=0.020,
        mc_iterations=10000,
        significance_level=0.025,
    )

    d = panel.to_dict()
    assert d["strategy_name"] == "TestAlpha"
    assert "week_block_ci" in d
    assert "annual_breakdown" in d
    assert "top10_share_pct" in d
    assert "trades_needed_80_power_onesided" in d

    md = panel.format_markdown()
    assert "#### Robustness Panel: TestAlpha" in md
    assert "Monte Carlo Standard Error" in md
    assert "Week-Block Bootstrap 95% CI" in md
    assert "Top-10 Outlier Share" in md
    assert "Calendar Year Performance Breakdown" in md
