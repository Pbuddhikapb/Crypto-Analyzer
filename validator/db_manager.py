"""
db_manager.py — SQLite persistence layer for backtest runs.

Manages the backtest_runs.db database including schema creation,
inserting runs / trades / config snapshots / alerts, and querying
historical data.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = r"e:\Crypto Analyzer\data\backtest_runs.db"

# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

@contextmanager
def _connect(db_path: str = DB_PATH):
    """Yield a sqlite3 connection with row_factory set to sqlite3.Row."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict."""
    return dict(row)


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp               TEXT    NOT NULL,
    mode                    TEXT    NOT NULL,
    symbol                  TEXT,
    cli_args                TEXT,
    total_trades            INTEGER,
    win_rate                REAL,
    profit_factor           REAL,
    avg_r_multiple          REAL,
    cumulative_return_pct   REAL,
    closed_max_drawdown_pct REAL,
    m2m_max_drawdown_pct    REAL,
    worst_intra_bar_dd_pct  REAL,
    sharpe                  REAL,
    ev_per_trade            REAL,
    annualized_return_pct   REAL,
    calmar_ratio            REAL,
    json_report             TEXT,
    stderr_log              TEXT,
    exit_code               INTEGER,
    duration_seconds        REAL
);

CREATE TABLE IF NOT EXISTS run_trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES runs(id),
    symbol          TEXT,
    entry_time      TEXT,
    exit_time       TEXT,
    side            TEXT,
    entry_price     REAL,
    exit_price      REAL,
    reason          TEXT,
    net_return_pct  REAL,
    r_multiple      REAL,
    vol_ratio       REAL,
    classification  TEXT
);

CREATE TABLE IF NOT EXISTS config_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES runs(id),
    file_path       TEXT,
    file_hash       TEXT,
    snapshot_time   TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER REFERENCES runs(id),
    severity    TEXT,
    category    TEXT,
    message     TEXT,
    details     TEXT
);
"""


def init_db(db_path: str = DB_PATH) -> None:
    """Create all tables if they do not already exist."""
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA_SQL)


# ---------------------------------------------------------------------------
# Insert helpers
# ---------------------------------------------------------------------------

def save_run(
    parsed_report: dict,
    cli_args: dict,
    stderr: str,
    exit_code: int,
    duration_s: float,
    db_path: str = DB_PATH,
) -> int:
    """
    Persist a backtest run and return the new run_id.

    ``parsed_report`` is expected to contain top-level keys that map to the
    ``runs`` table columns (e.g. ``mode``, ``symbol``, ``total_trades``, …).
    The entire dict is also stored verbatim as ``json_report``.
    """
    now = datetime.now(timezone.utc).isoformat()
    rpt = parsed_report or {}

    row = {
        "timestamp":               now,
        "mode":                    rpt.get("mode", "single"),
        "symbol":                  rpt.get("symbol"),
        "cli_args":                json.dumps(cli_args) if cli_args else None,
        "total_trades":            rpt.get("total_trades"),
        "win_rate":                rpt.get("win_rate"),
        "profit_factor":           rpt.get("profit_factor"),
        "avg_r_multiple":          rpt.get("avg_r_multiple"),
        "cumulative_return_pct":   rpt.get("cumulative_return_pct"),
        "closed_max_drawdown_pct": rpt.get("closed_max_drawdown_pct"),
        "m2m_max_drawdown_pct":    rpt.get("m2m_max_drawdown_pct"),
        "worst_intra_bar_dd_pct":  rpt.get("worst_intra_bar_dd_pct"),
        "sharpe":                  rpt.get("sharpe"),
        "ev_per_trade":            rpt.get("ev_per_trade"),
        "annualized_return_pct":   rpt.get("annualized_return_pct"),
        "calmar_ratio":            rpt.get("calmar_ratio"),
        "json_report":             json.dumps(parsed_report),
        "stderr_log":              stderr,
        "exit_code":               exit_code,
        "duration_seconds":        duration_s,
    }

    columns = ", ".join(row.keys())
    placeholders = ", ".join(["?"] * len(row))
    sql = f"INSERT INTO runs ({columns}) VALUES ({placeholders})"

    with _connect(db_path) as conn:
        cursor = conn.execute(sql, list(row.values()))
        return cursor.lastrowid


def save_trades(run_id: int, trades: list, db_path: str = DB_PATH) -> None:
    """Bulk-insert trade rows linked to *run_id*."""
    if not trades:
        return

    sql = """
        INSERT INTO run_trades
            (run_id, symbol, entry_time, exit_time, side,
             entry_price, exit_price, reason,
             net_return_pct, r_multiple, vol_ratio, classification)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    rows = [
        (
            run_id,
            t.get("symbol"),
            t.get("entry_time"),
            t.get("exit_time"),
            t.get("side"),
            t.get("entry_price"),
            t.get("exit_price"),
            t.get("reason"),
            t.get("net_return_pct"),
            t.get("r_multiple"),
            t.get("vol_ratio"),
            t.get("classification"),
        )
        for t in trades
    ]

    with _connect(db_path) as conn:
        conn.executemany(sql, rows)


def save_config_snapshot(
    run_id: int,
    file_path: str,
    file_hash: str,
    db_path: str = DB_PATH,
) -> None:
    """Record a config-file snapshot (path + SHA-256 hash)."""
    now = datetime.now(timezone.utc).isoformat()
    sql = """
        INSERT INTO config_snapshots (run_id, file_path, file_hash, snapshot_time)
        VALUES (?, ?, ?, ?)
    """
    with _connect(db_path) as conn:
        conn.execute(sql, (run_id, file_path, file_hash, now))


def save_alert(
    run_id: int,
    severity: str,
    category: str,
    message: str,
    details: Optional[dict] = None,
    db_path: str = DB_PATH,
) -> None:
    """Insert an alert row for a given run."""
    sql = """
        INSERT INTO alerts (run_id, severity, category, message, details)
        VALUES (?, ?, ?, ?, ?)
    """
    with _connect(db_path) as conn:
        conn.execute(
            sql,
            (run_id, severity, category, message,
             json.dumps(details) if details else None),
        )


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_last_n_runs(n: int = 30, db_path: str = DB_PATH) -> list:
    """Return the most recent *n* runs as a list of dicts (newest first)."""
    sql = "SELECT * FROM runs ORDER BY id DESC LIMIT ?"
    with _connect(db_path) as conn:
        rows = conn.execute(sql, (n,)).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_run_by_id(run_id: int, db_path: str = DB_PATH) -> Optional[dict]:
    """Return a single run dict, or ``None`` if not found."""
    sql = "SELECT * FROM runs WHERE id = ?"
    with _connect(db_path) as conn:
        row = conn.execute(sql, (run_id,)).fetchone()
        return _row_to_dict(row) if row else None


def get_alerts_for_run(run_id: int, db_path: str = DB_PATH) -> list:
    """Return all alert dicts for a given run."""
    sql = "SELECT * FROM alerts WHERE run_id = ? ORDER BY id"
    with _connect(db_path) as conn:
        rows = conn.execute(sql, (run_id,)).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_config_history(
    file_path: str,
    limit: int = 10,
    db_path: str = DB_PATH,
) -> list:
    """Return the most recent config snapshots for *file_path*."""
    sql = """
        SELECT * FROM config_snapshots
        WHERE file_path = ?
        ORDER BY id DESC
        LIMIT ?
    """
    with _connect(db_path) as conn:
        rows = conn.execute(sql, (file_path, limit)).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_latest_config_snapshot(db_path: str = DB_PATH) -> dict:
    """Return a mapping of file_path -> most recent file_hash across all snapshots."""
    sql = """
        SELECT file_path, file_hash
        FROM config_snapshots
        WHERE id IN (
            SELECT MAX(id) FROM config_snapshots GROUP BY file_path
        )
    """
    with _connect(db_path) as conn:
        rows = conn.execute(sql).fetchall()
        return {r["file_path"]: r["file_hash"] for r in rows}


def get_recent_runs(
    limit: int = 5,
    before_run_id: Optional[int] = None,
    db_path: str = DB_PATH,
) -> list:
    """Return recent runs, optionally before a specific run_id."""
    if before_run_id is not None:
        sql = "SELECT * FROM runs WHERE id < ? ORDER BY id DESC LIMIT ?"
        with _connect(db_path) as conn:
            rows = conn.execute(sql, (before_run_id, limit)).fetchall()
            return [_row_to_dict(r) for r in rows]
    return get_last_n_runs(n=limit, db_path=db_path)
