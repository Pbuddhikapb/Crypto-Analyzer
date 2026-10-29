#!/usr/bin/env python3
"""
auxiliary/repair_vault_source.py — One-time repair script to label legacy vault data.

Usage:
  # Dry-run inspection (default):
  python -m auxiliary.repair_vault_source [--db-path data/market_vault.db]

  # Apply repair with explicit cutoff timestamp (in milliseconds):
  python -m auxiliary.repair_vault_source --apply --cutoff <ms> [--confirm-other-tables]

Rules:
1. Tables order_book_bbo_snapshots and candle_microstructure_bars:
   - Rows older than cutoff (< ms) -> source = 'binance_spot_legacy'
   - Rows from cutoff onward (>= ms) -> source = 'binance_futures_um'
2. Other tables (whale_trades, liquidation_events, derivatives_snapshots,
   sentiment_metrics, strategy_telemetry_audit):
   - Rows with NULL source become 'binance_futures_um' ONLY if --confirm-other-tables is passed.
3. The DB file is backed up to data/backups/ before any write operation.
4. Rows are NEVER deleted.
"""

import argparse
from datetime import datetime, timezone
import os
import sqlite3
import sys
from typing import Any, Dict, Optional


OTHER_TABLES = [
    "whale_trades",
    "liquidation_events",
    "derivatives_snapshots",
    "sentiment_metrics",
    "strategy_telemetry_audit",
]


def format_ts(ts: Optional[int]) -> str:
    """Format millisecond timestamp to human-readable UTC string."""
    if ts is None:
        return "None"
    try:
        dt = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
        return f"{ts} ({dt.strftime('%Y-%m-%d %H:%M:%S UTC')})"
    except Exception:
        return str(ts)


def inspect_table_stats(conn: sqlite3.Connection, table_name: str, time_col: str) -> Dict[str, Any]:
    """Inspect min/max timestamps and row counts per source value for a table."""
    cursor = conn.cursor()
    cursor.execute(f"SELECT MIN({time_col}), MAX({time_col}), COUNT(*) FROM {table_name}")
    min_ts, max_ts, total_rows = cursor.fetchone()

    cursor.execute(f"SELECT COALESCE(source, 'NULL') AS src, COUNT(*) FROM {table_name} GROUP BY source")
    breakdown = {row[0]: row[1] for row in cursor.fetchall()}

    return {
        "table": table_name,
        "time_col": time_col,
        "total_rows": total_rows,
        "min_ts": min_ts,
        "max_ts": max_ts,
        "breakdown": breakdown,
    }


def inspect_other_table_stats(conn: sqlite3.Connection, table_name: str) -> Dict[str, Any]:
    """Inspect row count and source breakdown for non-timeseries or secondary tables."""
    cursor = conn.cursor()
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    total_rows = cursor.fetchone()[0]

    cursor.execute(f"SELECT COALESCE(source, 'NULL') AS src, COUNT(*) FROM {table_name} GROUP BY source")
    breakdown = {row[0]: row[1] for row in cursor.fetchall()}

    return {
        "table": table_name,
        "total_rows": total_rows,
        "breakdown": breakdown,
    }


def backup_database(db_path: str, backup_dir: str = "data/backups") -> str:
    """Safely back up the SQLite database using SQLite backup API to avoid partial WAL reads."""
    os.makedirs(backup_dir, exist_ok=True)
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    base_name = os.path.basename(db_path)
    backup_path = os.path.join(backup_dir, f"{base_name}_{ts_str}.bak")

    with sqlite3.connect(db_path) as src_conn:
        with sqlite3.connect(backup_path) as dst_conn:
            src_conn.backup(dst_conn)

    return backup_path


def repair_vault_source(
    db_path: str = "data/market_vault.db",
    apply: bool = False,
    cutoff: Optional[int] = None,
    confirm_other_tables: bool = False,
    backup_dir: str = "data/backups",
) -> Dict[str, Any]:
    """
    Main function to inspect and optionally repair vault table sources.
    Returns diagnostic dictionary.
    """
    if not os.path.exists(db_path):
        print(f"[INFO] Vault database '{db_path}' does not exist on disk.")
        return {"status": "file_not_found", "db_path": db_path}

    conn = sqlite3.connect(db_path)

    # Check available tables
    cursor = conn.cursor()
    existing_tables = [
        row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    ]

    print("\n" + "=" * 80)
    print(" [VAULT SOURCE REPAIR] INSPECTION REPORT")
    print(f" Target Database: {os.path.abspath(db_path)}")
    print(f" Execution Mode:  {'APPLY (WRITE)' if apply else 'DRY-RUN (READ-ONLY)'}")
    print("=" * 80)

    # 1. Inspect key tables: order_book_bbo_snapshots and candle_microstructure_bars
    stats: Dict[str, Any] = {}

    if "order_book_bbo_snapshots" in existing_tables:
        s_bbo = inspect_table_stats(conn, "order_book_bbo_snapshots", "timestamp_ms")
        stats["order_book_bbo_snapshots"] = s_bbo
        print("\nTable: order_book_bbo_snapshots")
        print(f"  Total Rows:     {s_bbo['total_rows']:,}")
        print(f"  Min timestamp:  {format_ts(s_bbo['min_ts'])}")
        print(f"  Max timestamp:  {format_ts(s_bbo['max_ts'])}")
        print("  Source Breakdown:")
        for src, cnt in s_bbo["breakdown"].items():
            print(f"    - {src}: {cnt:,} rows")
    else:
        print("\nTable: order_book_bbo_snapshots (NOT FOUND in database)")

    if "candle_microstructure_bars" in existing_tables:
        s_candle = inspect_table_stats(conn, "candle_microstructure_bars", "open_time")
        stats["candle_microstructure_bars"] = s_candle
        print("\nTable: candle_microstructure_bars")
        print(f"  Total Rows:     {s_candle['total_rows']:,}")
        print(f"  Min open_time:  {format_ts(s_candle['min_ts'])}")
        print(f"  Max open_time:  {format_ts(s_candle['max_ts'])}")
        print("  Source Breakdown:")
        for src, cnt in s_candle["breakdown"].items():
            print(f"    - {src}: {cnt:,} rows")
    else:
        print("\nTable: candle_microstructure_bars (NOT FOUND in database)")

    # 2. Inspect other tables
    print("\nOther Telemetry Tables:")
    for tbl in OTHER_TABLES:
        if tbl in existing_tables:
            s_other = inspect_other_table_stats(conn, tbl)
            stats[tbl] = s_other
            print(f"  Table: {tbl} (Total: {s_other['total_rows']:,})")
            for src, cnt in s_other["breakdown"].items():
                print(f"    - {src}: {cnt:,} rows")
        else:
            print(f"  Table: {tbl} (NOT FOUND)")

    print("\n" + "=" * 80)

    # 3. Guard against applying without explicit cutoff
    if not apply or cutoff is None:
        print("\n[STOP] Cutoff time is required to apply the migration.")
        print("Please provide the cutoff time in milliseconds using: --apply --cutoff <ms>")
        print("Do not guess the cutoff time.")
        conn.close()
        return {"status": "dry_run", "stats": stats}

    # 4. Perform Backup Before Modifying
    backup_file = backup_database(db_path, backup_dir=backup_dir)
    print(f"\n[BACKUP] Safely copied database to: {backup_file}")

    # 5. Apply Updates (NEVER DELETE)
    cursor = conn.cursor()
    updates: Dict[str, int] = {}

    if "order_book_bbo_snapshots" in existing_tables:
        cursor.execute(
            "UPDATE order_book_bbo_snapshots SET source = 'binance_spot_legacy' WHERE timestamp_ms < ?",
            (cutoff,),
        )
        spot_count = cursor.rowcount
        cursor.execute(
            "UPDATE order_book_bbo_snapshots SET source = 'binance_futures_um' WHERE timestamp_ms >= ?",
            (cutoff,),
        )
        fut_count = cursor.rowcount
        updates["order_book_bbo_snapshots"] = {"spot_legacy": spot_count, "futures_um": fut_count}
        print(f"[APPLIED] order_book_bbo_snapshots: {spot_count:,} -> binance_spot_legacy, {fut_count:,} -> binance_futures_um")

    if "candle_microstructure_bars" in existing_tables:
        cursor.execute(
            "UPDATE candle_microstructure_bars SET source = 'binance_spot_legacy' WHERE open_time < ?",
            (cutoff,),
        )
        spot_count = cursor.rowcount
        cursor.execute(
            "UPDATE candle_microstructure_bars SET source = 'binance_futures_um' WHERE open_time >= ?",
            (cutoff,),
        )
        fut_count = cursor.rowcount
        updates["candle_microstructure_bars"] = {"spot_legacy": spot_count, "futures_um": fut_count}
        print(f"[APPLIED] candle_microstructure_bars: {spot_count:,} -> binance_spot_legacy, {fut_count:,} -> binance_futures_um")

    # 6. Other tables updated only with explicit confirmation
    if confirm_other_tables:
        for tbl in OTHER_TABLES:
            if tbl in existing_tables:
                cursor.execute(f"UPDATE {tbl} SET source = 'binance_futures_um' WHERE source IS NULL")
                cnt = cursor.rowcount
                updates[tbl] = {"futures_um_from_null": cnt}
                print(f"[APPLIED] {tbl}: {cnt:,} NULL rows -> binance_futures_um")
    else:
        print("\n[NOTE] Other tables were NOT modified because confirmation was not provided (--confirm-other-tables).")

    conn.commit()
    conn.close()
    print("\n[SUCCESS] Vault source labels repaired successfully. Zero rows deleted.\n")
    return {"status": "applied", "backup_file": backup_file, "updates": updates}


def main():
    parser = argparse.ArgumentParser(
        description="One-time repair script to back up and label vault data source."
    )
    parser.add_argument(
        "--db-path",
        default="data/market_vault.db",
        help="Path to MarketDataVault SQLite database file (default: data/market_vault.db)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply modifications to database (default: dry-run mode)",
    )
    parser.add_argument(
        "--cutoff",
        type=int,
        default=None,
        help="Cutoff timestamp in milliseconds. Rows < cutoff get 'binance_spot_legacy'; rows >= cutoff get 'binance_futures_um'.",
    )
    parser.add_argument(
        "--confirm-other-tables",
        action="store_true",
        help="Confirm that other tables (whale_trades, liquidations, etc.) were always futures streams and label NULL rows as 'binance_futures_um'.",
    )
    parser.add_argument(
        "--backup-dir",
        default="data/backups",
        help="Directory to store safety backups before modification (default: data/backups)",
    )

    args = parser.parse_args()

    if args.apply and args.cutoff is None:
        # Run inspection first so user sees timestamps, then stop
        repair_vault_source(
            db_path=args.db_path,
            apply=False,
            cutoff=None,
            confirm_other_tables=args.confirm_other_tables,
            backup_dir=args.backup_dir,
        )
        print("Error: --cutoff <ms> is required when --apply is specified.", file=sys.stderr)
        sys.exit(1)

    repair_vault_source(
        db_path=args.db_path,
        apply=args.apply,
        cutoff=args.cutoff,
        confirm_other_tables=args.confirm_other_tables,
        backup_dir=args.backup_dir,
    )


if __name__ == "__main__":
    main()
