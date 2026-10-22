"""
validator/scheduler.py — Automated 4-hour background scheduler for Crypto Analyzer Validator.

Runs the backtest validator every N hours (default: 4 hours), validates results,
stores telemetry in SQLite, and dispatches email reports to the user.
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "validator"))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from legacy.validator.backtest_agent import agent_run, load_config


def run_scheduler(interval_hours: float = 4.0, run_mode: str = "portfolio", run_immediately: bool = True):
    """Run validation repeatedly every interval_hours."""
    config = load_config()
    cfg_schedule = config.get("schedule", {})
    if not interval_hours:
        interval_hours = float(cfg_schedule.get("interval_hours", 4.0))

    if not run_mode:
        run_mode = cfg_schedule.get("mode", "portfolio")

    interval_seconds = int(interval_hours * 3600)

    print("=" * 80)
    print("[SCHEDULER] Crypto Analyzer Validator Background Daemon")
    print(f"[SCHEDULER] Interval: Every {interval_hours:.1f} hours ({interval_seconds} seconds)")
    print(f"[SCHEDULER] Mode: {run_mode.upper()}")
    print(f"[SCHEDULER] Email Recipient: {config.get('notification', {}).get('email', {}).get('to_address', 'None')}")
    print("=" * 80)

    first_run = True
    while True:
        now_dt = datetime.now()
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

        if first_run and not run_immediately:
            print("[SCHEDULER] Skipping immediate first run. Waiting for initial interval...")
            first_run = False
        else:
            first_run = False
            print(f"\n[SCHEDULER] >>> Triggering scheduled validation run at {now_str}...")
            try:
                # Reload config each run in case user updated thresholds or files
                active_cfg = load_config()
                is_portfolio = (run_mode.lower() == "portfolio")
                run_id = agent_run(config=active_cfg, portfolio=is_portfolio)
                print(f"[SCHEDULER] >>> Validation run #{run_id} completed successfully.")
            except Exception as e:
                print(f"[SCHEDULER] [ERROR] Scheduled run encountered an exception: {e}")

        next_dt = datetime.now() + timedelta(seconds=interval_seconds)
        next_str = next_dt.strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[SCHEDULER] Sleeping for {interval_hours:.1f} hours. Next run at: {next_str}")

        # Sleep in 10-second increments so process can be stopped cleanly
        elapsed = 0
        while elapsed < interval_seconds:
            time.sleep(min(10, interval_seconds - elapsed))
            elapsed += 10


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Validator Automated Scheduler Daemon")
    parser.add_argument("--interval", type=float, default=None, help="Interval in hours (default: 4.0)")
    parser.add_argument("--mode", type=str, default=None, choices=["portfolio", "single"], help="Validation mode")
    parser.add_argument("--no-immediate", action="store_true", help="Do not run immediately on start")
    args = parser.parse_args()

    run_scheduler(
        interval_hours=args.interval or 4.0,
        run_mode=args.mode or "portfolio",
        run_immediately=not args.no_immediate,
    )
