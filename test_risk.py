import sqlite3
import pytest
from risk import PortfolioRiskManager


@pytest.fixture
def temp_risk_db(tmp_path):
    return str(tmp_path / "test_risk_signals.db")


def test_can_open_position_under_limit(temp_risk_db):
    risk_manager = PortfolioRiskManager(
        max_open_positions=3, symbol_cooldown_bars=3, db_path=temp_risk_db
    )
    assert risk_manager.get_open_positions_count() == 0

    allowed, reason = risk_manager.can_open_position("BTCUSDT", current_bar_index=10)
    assert allowed is True
    assert reason == "Approved"


def test_max_open_positions_limit_enforcement(temp_risk_db):
    risk_manager = PortfolioRiskManager(
        max_open_positions=3, symbol_cooldown_bars=3, db_path=temp_risk_db
    )

    # Insert 3 open positions for different symbols
    with risk_manager._get_connection() as conn:
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            conn.execute(
                """
                INSERT INTO paper_trades (
                    symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit
                ) VALUES (?, '1m', 'LONG', 'OPEN', '2026-10-14T10:00:00', 100.0, 95.0, 110.0)
                """,
                (sym,),
            )
        conn.commit()

    assert risk_manager.get_open_positions_count() == 3

    # Attempting to open a 4th position must be rejected
    allowed, reason = risk_manager.can_open_position("DOGEUSDT", current_bar_index=10)
    assert allowed is False
    assert "Portfolio limit reached" in reason
    assert "3/3 positions active" in reason


def test_symbol_cooldown_enforcement(temp_risk_db):
    risk_manager = PortfolioRiskManager(
        max_open_positions=3, symbol_cooldown_bars=3, db_path=temp_risk_db
    )

    # Record BTC position closed at bar 100
    risk_manager.record_position_closed("BTCUSDT", current_bar_index=100)

    # Bar 101: 1 bar elapsed (< 3) -> rejected
    allowed, reason = risk_manager.can_open_position("BTCUSDT", current_bar_index=101)
    assert allowed is False
    assert "in cooldown" in reason
    assert "1/3 bars elapsed" in reason

    # Bar 102: 2 bars elapsed (< 3) -> rejected
    allowed, reason = risk_manager.can_open_position("BTCUSDT", current_bar_index=102)
    assert allowed is False
    assert "2/3 bars elapsed" in reason

    # Bar 103: 3 bars elapsed (cooldown over) -> approved
    allowed, reason = risk_manager.can_open_position("BTCUSDT", current_bar_index=103)
    assert allowed is True
    assert reason == "Approved"

    # Different symbol (e.g. ETHUSDT) is not blocked by BTC cooldown
    allowed_eth, _ = risk_manager.can_open_position("ETHUSDT", current_bar_index=101)
    assert allowed_eth is True


def test_portfolio_summary_aggregation(temp_risk_db):
    risk_manager = PortfolioRiskManager(
        max_open_positions=3, symbol_cooldown_bars=3, db_path=temp_risk_db
    )

    # Initially empty
    summary = risk_manager.get_portfolio_summary()
    assert summary["total_trades"] == 0
    assert summary["win_rate_pct"] == 0.0
    assert summary["symbols_traded"] == []

    # Insert trades across 3 symbols:
    # BTCUSDT: Win (+3.85%, +2.0R)
    # ETHUSDT: Win (+2.50%, +1.5R)
    # SOLUSDT: Loss (-1.65%, -1.0R)
    with risk_manager._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit,
                exit_price, exit_timestamp, reason, net_return_pct, r_multiple
            ) VALUES 
            ('BTCUSDT', '1m', 'LONG', 'CLOSED', '2026-10-14T01:00:00', 50000, 49000, 52000, 52000, '2026-10-14T02:00:00', 'TAKE_PROFIT', 3.85, 2.0),
            ('ETHUSDT', '1m', 'SHORT', 'CLOSED', '2026-10-14T01:10:00', 3000, 3100, 2800, 2800, '2026-10-14T02:10:00', 'TAKE_PROFIT', 2.50, 1.5),
            ('SOLUSDT', '1m', 'LONG', 'CLOSED', '2026-10-14T01:20:00', 150, 140, 170, 140, '2026-10-14T02:20:00', 'STOP_LOSS', -1.65, -1.0)
            """
        )
        conn.commit()

    summary = risk_manager.get_portfolio_summary()
    assert summary["total_trades"] == 3
    assert summary["winning_trades"] == 2
    assert summary["losing_trades"] == 1
    assert pytest.approx(summary["win_rate_pct"], rel=1e-2) == 66.67
    assert pytest.approx(summary["total_net_return_pct"], rel=1e-2) == 4.70
    assert pytest.approx(summary["avg_r_multiple"], rel=1e-2) == 0.833
    assert summary["symbols_traded"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_wal_concurrency(temp_risk_db):
    risk_manager = PortfolioRiskManager(db_path=temp_risk_db)
    with risk_manager._get_connection() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert journal_mode.lower() == "wal"


def test_portfolio_summary_handles_null_values(temp_risk_db):
    """Verify get_portfolio_summary does not crash when database rows contain NULLs."""
    risk_manager = PortfolioRiskManager(db_path=temp_risk_db)
    with risk_manager._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit,
                exit_price, exit_timestamp, reason, net_return_pct, r_multiple
            ) VALUES ('BTCUSDT', '1m', 'LONG', 'CLOSED', '2026-10-14', 50000, 49000, 52000, 52000, '2026-10-14', 'TAKE_PROFIT', 4.0, NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit,
                exit_price, exit_timestamp, reason, net_return_pct, r_multiple
            ) VALUES ('ETHUSDT', '1m', 'SHORT', 'CLOSED', '2026-10-14', 3000, 3100, 2800, 2800, '2026-10-14', 'TAKE_PROFIT', NULL, 1.5)
            """
        )

    summary = risk_manager.get_portfolio_summary()
    assert summary["total_trades"] == 2
    assert summary["total_net_return_pct"] == 4.0
    assert summary["avg_r_multiple"] == 1.5
    assert "BTCUSDT" in summary["symbols_traded"]
    assert "ETHUSDT" in summary["symbols_traded"]


def test_can_open_position_rejects_duplicate_symbol_when_already_open(temp_risk_db):
    """Verify that a coin with an existing active trade cannot open another position."""
    risk_manager = PortfolioRiskManager(
        max_open_positions=3, symbol_cooldown_bars=3, db_path=temp_risk_db
    )

    # Insert an OPEN trade for BTCUSDT
    with risk_manager._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit
            ) VALUES ('BTCUSDT', '1m', 'LONG', 'OPEN', '2026-10-14T10:00:00', 50000.0, 49000.0, 52000.0)
            """
        )

    # 1. BTCUSDT must be rejected due to duplicate open position
    allowed, reason = risk_manager.can_open_position("BTCUSDT", current_bar_index=10)
    assert allowed is False
    assert "already has an active OPEN position" in reason

    # 2. ETHUSDT (flat) must still be allowed since open count = 1 < 3
    allowed_eth, reason_eth = risk_manager.can_open_position("ETHUSDT", current_bar_index=10)
    assert allowed_eth is True
    assert reason_eth == "Approved"


def test_has_open_position_detection(temp_risk_db):
    """Verify has_open_position accurately detects active vs closed trades."""
    risk_manager = PortfolioRiskManager(db_path=temp_risk_db)

    assert risk_manager.has_open_position("BTCUSDT") is False

    with risk_manager._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit
            ) VALUES ('BTCUSDT', '1m', 'LONG', 'OPEN', '2026-10-14T10:00:00', 50000.0, 49000.0, 52000.0)
            """
        )

    assert risk_manager.has_open_position("BTCUSDT") is True
    assert risk_manager.has_open_position("SOLUSDT") is False


def test_portfolio_risk_manager_checkpoint(temp_risk_db):
    """Verify PortfolioRiskManager.checkpoint flushes WAL successfully."""
    risk_manager = PortfolioRiskManager(db_path=temp_risk_db)
    with risk_manager._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit
            ) VALUES ('SOLUSDT', '1m', 'LONG', 'OPEN', '2026-10-14T10:00:00', 150.0, 140.0, 170.0)
            """
        )
    # Must execute checkpoint without error
    risk_manager.checkpoint()


def test_calculate_position_size_normal(temp_risk_db):
    """Verify position sizing for a standard stop loss distance."""
    risk_manager = PortfolioRiskManager(
        account_equity_usd=10000.0,
        risk_per_trade_pct=1.0,  # $100 target risk
        max_leverage=3.0,
        db_path=temp_risk_db,
    )
    # Entry 50,000, SL 49,000 (risk dist = $1,000 = 2.0%)
    res = risk_manager.calculate_position_size(entry_price=50000.0, stop_loss=49000.0)
    assert pytest.approx(res["quantity"], rel=1e-4) == 0.1
    assert pytest.approx(res["notional_usd"], rel=1e-4) == 5000.0
    assert pytest.approx(res["risk_usd"], rel=1e-4) == 100.0
    assert pytest.approx(res["leverage"], rel=1e-4) == 0.5
    assert res["is_clamped"] is False


def test_calculate_position_size_hyper_tight_stop_clamped(temp_risk_db):
    """Verify that a hyper-tight stop loss is safely clamped to max 3.0x leverage."""
    risk_manager = PortfolioRiskManager(
        account_equity_usd=10000.0,
        risk_per_trade_pct=1.0,  # $100 target risk
        max_leverage=3.0,        # Max notional = $30,000
        db_path=temp_risk_db,
    )
    # Entry 50,000, SL 49,950 (risk dist = $50 = 0.10%)
    # Unclamped would require $100 / $50 = 2.0 BTC = $100,000 notional (10x leverage!)
    res = risk_manager.calculate_position_size(entry_price=50000.0, stop_loss=49950.0)
    assert res["is_clamped"] is True
    assert pytest.approx(res["notional_usd"], rel=1e-4) == 30000.0
    assert pytest.approx(res["quantity"], rel=1e-4) == 0.6
    assert pytest.approx(res["leverage"], rel=1e-4) == 3.0
    assert pytest.approx(res["risk_usd"], rel=1e-4) == 30.0  # Safe reduced risk


def test_portfolio_summary_capital_allocation_metrics(temp_risk_db):
    """Verify that get_portfolio_summary computes allocated notional, leverage, and available equity."""
    risk_manager = PortfolioRiskManager(
        account_equity_usd=10000.0,
        max_leverage=3.0,
        db_path=temp_risk_db,
    )
    with risk_manager._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit,
                quantity, notional_usd, leverage
            ) VALUES 
            ('BTCUSDT', '15m', 'LONG', 'OPEN', '2026-10-14T10:00:00', 50000.0, 49000.0, 52000.0, 0.1, 5000.0, 0.5),
            ('ETHUSDT', '15m', 'SHORT', 'OPEN', '2026-10-14T10:00:00', 3000.0, 3100.0, 2800.0, 1.0, 3000.0, 0.3)
            """
        )

    summary = risk_manager.get_portfolio_summary()
    assert summary["account_equity_usd"] == 10000.0
    assert summary["allocated_notional_usd"] == 8000.0
    assert pytest.approx(summary["portfolio_leverage"], rel=1e-3) == 0.8
    assert pytest.approx(summary["available_equity_usd"], rel=1e-2) == 7333.33


def test_find_preemption_candidate_identifies_stagnant_trade(temp_risk_db):
    """Verify that an active position held >= 8 bars within deadband [-0.15R, 0.15R] is selected for preemption."""
    risk_manager = PortfolioRiskManager(
        max_open_positions=1,
        allow_preemption=True,
        preemption_min_candidate_score=85.0,
        preemption_min_bars_held=8,
        db_path=temp_risk_db,
    )
    with risk_manager._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit,
                entry_bar, quantity, notional_usd, leverage
            ) VALUES ('SOLUSDT', '15m', 'LONG', 'OPEN', '2026-10-14T10:00:00', 100.0, 95.0, 110.0, 10, 1.0, 100.0, 0.01)
            """
        )

    class MockTrader:
        def __init__(self):
            self.bar_count = 20  # 20 - 10 = 10 bars held (>= 8)
            self.candles = [(100.0, 101.0, 99.0, 100.2, 100.0)]  # close 100.2 -> (100.2 - 100)/5 = +0.04R (within deadband)
        def get_active_trade(self):
            return {
                "symbol": "SOLUSDT",
                "side": "LONG",
                "entry_price": 100.0,
                "stop_loss": 95.0,
                "initial_stop_loss": 95.0,
                "entry_bar": 10,
                "tp1_hit": False,
            }

    active_traders = {"SOLUSDT": MockTrader()}
    sym, reason = risk_manager.find_preemption_candidate(active_traders, candidate_score=90.0)
    assert sym == "SOLUSDT"
    assert "Preempting stagnant SOLUSDT" in reason


def test_find_preemption_candidate_protects_profitable_or_tp1_trade(temp_risk_db):
    """Verify that a position with TP1 banked or floating > +0.15R is immune to preemption."""
    risk_manager = PortfolioRiskManager(
        max_open_positions=1,
        allow_preemption=True,
        preemption_min_candidate_score=85.0,
        db_path=temp_risk_db,
    )
    with risk_manager._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit
            ) VALUES ('BTCUSDT', '15m', 'LONG', 'OPEN', '2026-10-14T10:00:00', 50000.0, 49000.0, 52000.0)
            """
        )

    # Case 1: TP1 is hit
    class MockTraderTP1:
        def __init__(self):
            self.bar_count = 25
            self.candles = [(50000.0, 51000.0, 49900.0, 50100.0, 100.0)]
        def get_active_trade(self):
            return {"symbol": "BTCUSDT", "entry_bar": 5, "tp1_hit": True, "entry_price": 50000.0, "stop_loss": 49000.0}

    sym, _ = risk_manager.find_preemption_candidate({"BTCUSDT": MockTraderTP1()}, candidate_score=95.0)
    assert sym is None

    # Case 2: In healthy profit (+0.60R)
    class MockTraderProfit:
        def __init__(self):
            self.bar_count = 25
            self.candles = [(50000.0, 51000.0, 49900.0, 50600.0, 100.0)]  # +0.60R
        def get_active_trade(self):
            return {"symbol": "BTCUSDT", "entry_bar": 5, "tp1_hit": False, "entry_price": 50000.0, "stop_loss": 49000.0, "side": "LONG"}

    sym2, _ = risk_manager.find_preemption_candidate({"BTCUSDT": MockTraderProfit()}, candidate_score=95.0)
    assert sym2 is None


def test_correlation_gating_caps_correlated_altcoins_when_btc_open(temp_risk_db):
    """Verify that when BTC is open, at most 1 concurrent altcoin can open in the same direction."""
    risk_manager = PortfolioRiskManager(
        max_open_positions=4, max_directional_positions=3, db_path=temp_risk_db
    )

    # 1. Open BTC LONG
    with risk_manager._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit
            ) VALUES ('BTCUSDT', '15m', 'LONG', 'OPEN', '2026-10-14T10:00:00', 50000.0, 49000.0, 52000.0)
            """
        )

    # 2. First correlated altcoin (ETH LONG) should be approved
    allowed_eth, reason_eth = risk_manager.can_open_position("ETHUSDT", current_bar_index=10, side="LONG")
    assert allowed_eth is True
    assert reason_eth == "Approved"

    # Insert ETH LONG as active
    with risk_manager._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit
            ) VALUES ('ETHUSDT', '15m', 'LONG', 'OPEN', '2026-10-14T10:00:00', 3000.0, 2900.0, 3200.0)
            """
        )

    # 3. Second correlated altcoin (SOL LONG) must be rejected by correlation gate
    allowed_sol, reason_sol = risk_manager.can_open_position("SOLUSDT", current_bar_index=10, side="LONG")
    assert allowed_sol is False
    assert "Correlation Gate: BTCUSDT is already LONG" in reason_sol
    assert "max 1 concurrent correlated altcoin allowed" in reason_sol

    # 4. Uncorrelated/opposing altcoin (SOL SHORT) should be allowed
    allowed_sol_short, reason_short = risk_manager.can_open_position("SOLUSDT", current_bar_index=10, side="SHORT")
    assert allowed_sol_short is True
    assert reason_short == "Approved"


def test_dynamic_risk_scaling_halves_altcoin_risk_when_btc_open(temp_risk_db):
    """Verify that altcoin risk is dynamically halved (1.0% -> 0.5%) when correlated BTC position is open."""
    risk_manager = PortfolioRiskManager(
        account_equity_usd=10000.0, risk_per_trade_pct=1.0, db_path=temp_risk_db
    )

    # Baseline: No BTC position open -> full 1.0% ($100) risk
    sizing_baseline = risk_manager.calculate_position_size(entry_price=2000.0, stop_loss=1950.0, symbol="ETHUSDT")
    assert sizing_baseline["risk_usd"] == 100.0

    # Insert BTC LONG position
    with risk_manager._get_connection() as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit
            ) VALUES ('BTCUSDT', '15m', 'LONG', 'OPEN', '2026-10-14T10:00:00', 50000.0, 49000.0, 52000.0)
            """
        )

    # Correlated ETH LONG: Risk scaled to 0.5% ($50)
    sizing_scaled = risk_manager.calculate_position_size(entry_price=2000.0, stop_loss=1950.0, symbol="ETHUSDT")
    assert sizing_scaled["risk_usd"] == 50.0

    # Opposing ETH SHORT: Uncorrelated hedge -> full 1.0% ($100) risk
    sizing_hedged = risk_manager.calculate_position_size(entry_price=2000.0, stop_loss=2050.0, symbol="ETHUSDT")
    assert sizing_hedged["risk_usd"] == 100.0




