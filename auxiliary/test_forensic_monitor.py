import sqlite3
import pytest

from auxiliary.forensic_monitor import ForensicAnomalyAuditor


@pytest.fixture
def temp_forensic_env(tmp_path):
    signals_db = str(tmp_path / "test_signals.db")
    vault_db = str(tmp_path / "test_vault.db")
    forensic_db = str(tmp_path / "test_forensic.db")

    # Seed mock signals_db
    with sqlite3.connect(signals_db) as conn:
        conn.execute(
            """
            CREATE TABLE paper_trades (
                id INTEGER PRIMARY KEY,
                symbol TEXT,
                side TEXT,
                status TEXT,
                entry_price REAL,
                stop_loss REAL,
                take_profit REAL,
                tp1_price REAL,
                tp1_hit INTEGER DEFAULT 0,
                be_armed INTEGER DEFAULT 0,
                timestamp TEXT,
                initial_stop_loss REAL
            )
            """
        )
        conn.commit()

    return {
        "signals_db": signals_db,
        "vault_db": vault_db,
        "forensic_db": forensic_db,
    }


def test_forensic_db_initialization(temp_forensic_env):
    """Verify that ForensicAnomalyAuditor initializes all tables in WAL mode."""
    ForensicAnomalyAuditor(
        signals_db=temp_forensic_env["signals_db"],
        vault_db=temp_forensic_env["vault_db"],
        forensic_db=temp_forensic_env["forensic_db"],
    )

    with sqlite3.connect(temp_forensic_env["forensic_db"]) as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        assert "trade_excursion_audit" in tables
        assert "shadow_blocked_signals" in tables
        assert "telemetry_anomaly_events" in tables


def test_audit_active_trades_mfe_and_loophole_detection(temp_forensic_env):
    """Verify that an active trade peaking near TP1 and decaying triggers ALMOST_TP1_FADE."""
    signals_db = temp_forensic_env["signals_db"]
    with sqlite3.connect(signals_db) as conn:
        conn.execute(
            """
            INSERT INTO paper_trades (
                id, symbol, side, status, entry_price, stop_loss, take_profit,
                tp1_price, tp1_hit, be_armed, timestamp, initial_stop_loss
            ) VALUES (1, 'BTCUSDT', 'LONG', 'OPEN', 80000.0, 79200.0, 81400.0, 80640.0, 0, 0, '1728000000000', 79200.0)
            """
        )
        conn.commit()

    auditor = ForensicAnomalyAuditor(
        signals_db=signals_db,
        vault_db=temp_forensic_env["vault_db"],
        forensic_db=temp_forensic_env["forensic_db"],
    )

    # 1. Price surges to 80560 (+0.70R excursion)
    auditor.audit_active_trades({"BTCUSDT": 80560.0})

    # 2. Price decays back down to 80080 (+0.10R) without TP1 hitting
    anomalies = auditor.audit_active_trades({"BTCUSDT": 80080.0})

    assert len(anomalies) == 1
    assert anomalies[0]["type"] == "ALMOST_TP1_FADE"
    assert "BTCUSDT" in anomalies[0]["details"]

    with sqlite3.connect(temp_forensic_env["forensic_db"]) as f_conn:
        row = f_conn.execute("SELECT * FROM trade_excursion_audit WHERE trade_id = 1").fetchone()
        assert row is not None
        assert "ALMOST_TP1_FADE" in row[15]


def test_record_and_evaluate_shadow_signals(temp_forensic_env):
    """Verify that blocked setups are recorded into shadow tracking and resolve to SAVED_LOSS on SL."""
    auditor = ForensicAnomalyAuditor(
        signals_db=temp_forensic_env["signals_db"],
        vault_db=temp_forensic_env["vault_db"],
        forensic_db=temp_forensic_env["forensic_db"],
    )

    s_id = auditor.record_candidate_blocked_signal(
        symbol="SOLUSDT",
        side="LONG",
        score=55.0,
        block_reason="ADX Chop Regime",
        current_price=150.0,
        atr=2.0,
    )
    assert s_id is not None

    # Evaluate bar that drops below candidate SL (150 - 3.6 = 146.4)
    resolved = auditor.evaluate_shadow_signals({
        "SOLUSDT": {"high": 151.0, "low": 145.0, "close": 146.0}
    })

    assert len(resolved) == 1
    assert resolved[0]["status"] == "SAVED_LOSS"
    assert "prevented loss" in resolved[0]["outcome"].lower()


def test_shadow_signals_blocked_winner(temp_forensic_env):
    """Verify that a blocked setup hitting TP1 resolves to BLOCKED_WINNER."""
    auditor = ForensicAnomalyAuditor(
        signals_db=temp_forensic_env["signals_db"],
        vault_db=temp_forensic_env["vault_db"],
        forensic_db=temp_forensic_env["forensic_db"],
    )

    s_id = auditor.record_candidate_blocked_signal(
        symbol="XRPUSDT",
        side="LONG",
        score=60.0,
        block_reason="Whale Delta Dumping",
        current_price=1.00,
        atr=0.015,
    )
    assert s_id is not None

    # Evaluate bar that surges past TP1 (1.00 + (0.027 * 0.8) = 1.0216)
    resolved = auditor.evaluate_shadow_signals({
        "XRPUSDT": {"high": 1.05, "low": 0.995, "close": 1.04}
    })

    assert len(resolved) == 1
    assert resolved[0]["status"] == "BLOCKED_WINNER"
    assert "blocked potential" in resolved[0]["outcome"].lower()


def test_generate_and_format_report(temp_forensic_env):
    """Verify that generate_forensic_report and format_markdown_report run cleanly."""
    auditor = ForensicAnomalyAuditor(
        signals_db=temp_forensic_env["signals_db"],
        vault_db=temp_forensic_env["vault_db"],
        forensic_db=temp_forensic_env["forensic_db"],
    )

    report_data = auditor.generate_forensic_report()
    assert "filter_efficiency_pct" in report_data
    assert "shadow_breakdown" in report_data

    md_text = auditor.format_markdown_report(report_data)
    assert "# Live Forensic Loophole Analysis" in md_text
    assert "Counterfactual Filter Efficiency" in md_text
