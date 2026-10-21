"""
Backtest Validator Agent – Main Orchestrator
=============================================
Runs backtest.py, parses output, validates metrics, detects config drift,
and generates a markdown report. No engine files are modified.

Usage:
    python validator/backtest_agent.py                      # Single-asset default
    python validator/backtest_agent.py --portfolio           # Portfolio mode
    python validator/backtest_agent.py --symbol BTCUSDT      # Override symbol
    python validator/backtest_agent.py --skip-research       # Skip web research
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "validator"))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from validator.output_parser import parse_backtest_output, parse_trade_rows
from validator.db_manager import init_db, save_run, save_trades, save_config_snapshot, save_alert
from validator.validation import run_all_checks, compute_config_hashes, check_config_drift
from validator.report_generator import generate_report, save_report

# ---------------------------------------------------------------------------
# Configuration loader
# ---------------------------------------------------------------------------

def load_config(config_path: str = None) -> dict:
    """Load agent_config.yaml or return sensible defaults."""
    if config_path is None:
        config_path = str(Path(__file__).resolve().parent / "agent_config.yaml")

    defaults = {
        "backtest": {
            "script": str(PROJECT_ROOT / "backtest.py"),
            "timeout": 3600,
            "single_defaults": {
                "symbol": "XRPUSDT",
                "timeframe": "15m",
                "candles": 2500,
                "hifi": True,
                "htf_filter": True,
                "tf1h_filter": True,
                "adaptive_sl": True,
            },
            "portfolio_defaults": {
                "timeframe": "15m",
                "candles": 2500,
                "hifi": True,
                "htf_filter": True,
                "tf1h_filter": True,
                "adaptive_sl": True,
                "tier1_only": False,
            },
        },
        "monitored_files": [
            str(PROJECT_ROOT / "indicators.py"),
            str(PROJECT_ROOT / "trader.py"),
            str(PROJECT_ROOT / "risk.py"),
            str(PROJECT_ROOT / "microstructure.py"),
            str(PROJECT_ROOT / "backtest.py"),
        ],
        "notification": {
            "method": "file",
            "report_dir": str(PROJECT_ROOT / "reports"),
        },
        "database": {
            "path": str(PROJECT_ROOT / "data" / "backtest_runs.db"),
        },
    }

    if os.path.exists(config_path):
        try:
            import yaml
            with open(config_path, "r") as f:
                user_cfg = yaml.safe_load(f) or {}
            # Shallow merge – user overrides take precedence
            for key in user_cfg:
                if isinstance(user_cfg[key], dict) and key in defaults and isinstance(defaults[key], dict):
                    defaults[key].update(user_cfg[key])
                else:
                    defaults[key] = user_cfg[key]
        except ImportError:
            print("[AGENT] PyYAML not installed – using built-in defaults.")
        except Exception as e:
            print(f"[AGENT] Warning: Could not load config ({e}) – using defaults.")

    return defaults


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------

def build_cli_args(config: dict, portfolio: bool = False, overrides: dict = None) -> list:
    """Build the CLI argument list for backtest.py."""
    args = [sys.executable, config["backtest"]["script"]]

    if portfolio:
        defs = config["backtest"]["portfolio_defaults"].copy()
    else:
        defs = config["backtest"]["single_defaults"].copy()

    if overrides:
        defs.update(overrides)

    if portfolio:
        args.append("--portfolio")
        if defs.get("tier1_only"):
            args.append("--tier1-only")

    if not portfolio and defs.get("symbol"):
        args.extend(["--symbol", defs["symbol"]])

    if defs.get("timeframe"):
        args.extend(["--timeframe", defs["timeframe"]])
    if defs.get("candles"):
        args.extend(["--candles", str(defs["candles"])])
    if defs.get("hifi"):
        args.append("--hifi")
    if defs.get("htf_filter"):
        args.append("--htf-filter")
    if defs.get("tf1h_filter"):
        args.append("--tf1h-filter")
    if defs.get("adaptive_sl"):
        args.append("--adaptive-sl")
    if defs.get("micro"):
        args.append("--micro")
    if defs.get("market_structure"):
        args.append("--market-structure")
    if defs.get("fibonacci_ote"):
        args.append("--fibonacci-ote")
    if defs.get("order_blocks"):
        args.append("--order-blocks")
    if defs.get("fresh"):
        args.append("--fresh")

    return args


def run_backtest(config: dict, portfolio: bool = False, overrides: dict = None) -> dict:
    """Execute backtest.py as a subprocess and capture output."""
    cli_args = build_cli_args(config, portfolio, overrides)
    cli_str = " ".join(cli_args)
    timeout = config["backtest"].get("timeout", 3600)

    print(f"\n{'=' * 80}")
    print(f"[AGENT] Launching backtest...")
    print(f"[AGENT] Command: {cli_str}")
    print(f"[AGENT] Timeout: {timeout}s")
    print(f"{'=' * 80}\n")

    start_time = time.time()
    try:
        result = subprocess.run(
            cli_args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(PROJECT_ROOT),
        )
        duration = time.time() - start_time
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "duration_seconds": duration,
            "cli_args": cli_args[2:],  # skip 'python' and script path
            "portfolio": portfolio,
        }
    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        return {
            "stdout": "",
            "stderr": f"TIMEOUT: Backtest exceeded {timeout}s limit.",
            "exit_code": -1,
            "duration_seconds": duration,
            "cli_args": cli_args[2:],
            "portfolio": portfolio,
        }
    except Exception as e:
        duration = time.time() - start_time
        return {
            "stdout": "",
            "stderr": f"EXCEPTION: {str(e)}",
            "exit_code": -2,
            "duration_seconds": duration,
            "cli_args": cli_args[2:],
            "portfolio": portfolio,
        }


# ---------------------------------------------------------------------------
# Main agent pipeline
# ---------------------------------------------------------------------------

def agent_run(config: dict, portfolio: bool = False, overrides: dict = None, skip_research: bool = False) -> int:
    """
    Full agent pipeline:
      1. Initialize DB
      2. Run backtest
      3. Parse output
      4. Persist run
      5. Validate metrics
      6. Check config drift
      7. Generate report
      8. Return run_id
    """
    # Step 1: Initialize persistence
    init_db()
    print("[AGENT] Database initialized.")

    # Step 2: Run backtest
    bt_result = run_backtest(config, portfolio, overrides)

    # Print live output for user visibility
    if bt_result["stdout"]:
        print("\n" + "-" * 80)
        print("[BACKTEST OUTPUT]")
        print("-" * 80)
        print(bt_result["stdout"])
        print("-" * 80)

    if bt_result["stderr"]:
        print("\n[BACKTEST STDERR]")
        for line in bt_result["stderr"].strip().split("\n"):
            print(f"  ⚠ {line}")

    # Step 3: Parse output
    print("\n[AGENT] Parsing backtest output...")
    parsed = parse_backtest_output(bt_result["stdout"])
    trade_rows = parse_trade_rows(bt_result["stdout"])
    parsed["stderr_lines"] = bt_result["stderr"].strip().split("\n") if bt_result["stderr"].strip() else []
    parsed["exit_code"] = bt_result["exit_code"]
    parsed["cli_args"] = " ".join(bt_result["cli_args"])
    parsed["duration"] = f"{bt_result['duration_seconds']:.1f}s"

    print(f"[AGENT] Parsed {parsed.get('total_trades', 0)} trades, mode='{parsed.get('mode', 'unknown')}'")

    # Step 4: Persist run to DB
    print("[AGENT] Saving run to database...")
    cli_args_dict = {}
    for i, arg in enumerate(bt_result["cli_args"]):
        if arg.startswith("--"):
            key = arg.lstrip("-").replace("-", "_")
            if i + 1 < len(bt_result["cli_args"]) and not bt_result["cli_args"][i + 1].startswith("--"):
                cli_args_dict[key] = bt_result["cli_args"][i + 1]
            else:
                cli_args_dict[key] = True

    run_id = save_run(
        parsed_report=parsed,
        cli_args=cli_args_dict,
        stderr=bt_result["stderr"],
        exit_code=bt_result["exit_code"],
        duration_s=bt_result["duration_seconds"],
    )
    print(f"[AGENT] Run saved with ID: {run_id}")

    # Save individual trades
    if trade_rows:
        save_trades(run_id, trade_rows)
        print(f"[AGENT] Saved {len(trade_rows)} trade records.")

    # Step 5: Configuration drift detection
    print("[AGENT] Checking configuration drift...")
    monitored = config.get("monitored_files", [])
    existing_files = [f for f in monitored if os.path.exists(f)]
    config_hashes = compute_config_hashes(existing_files)
    for fpath, fhash in config_hashes.items():
        save_config_snapshot(run_id, fpath, fhash)

    drift_alerts = check_config_drift(run_id, config_hashes)
    if drift_alerts:
        print(f"[AGENT] ⚠ Config drift detected in {len(drift_alerts)} file(s):")
        for da in drift_alerts:
            print(f"  → {da['message']}")
            save_alert(run_id, da["severity"], da["category"], da["message"], da.get("details"))

    # Step 6: Run validation checks
    print("[AGENT] Running validation checks...")
    alerts = run_all_checks(parsed, run_id)
    total_critical = sum(1 for a in alerts if a["severity"] == "CRITICAL")
    total_warning = sum(1 for a in alerts if a["severity"] == "WARNING")
    total_info = sum(1 for a in alerts if a["severity"] == "INFO")
    print(f"[AGENT] Validation complete: {total_critical} CRITICAL, {total_warning} WARNING, {total_info} INFO")

    # Merge drift alerts into the full alert list
    all_alerts = alerts + drift_alerts

    # Step 7: Research notes (placeholder – can be extended with web search)
    research_notes = ""
    if not skip_research and total_critical > 0:
        research_notes = _generate_research_notes(all_alerts)

    # Step 8: Generate report
    print("[AGENT] Generating report...")
    report_md = generate_report(
        run_id=run_id,
        parsed_report=parsed,
        alerts=all_alerts,
        trade_rows=trade_rows,
        research_notes=research_notes,
    )

    report_dir = config.get("notification", {}).get("report_dir", str(PROJECT_ROOT / "reports"))
    report_path = save_report(report_md, run_id, output_dir=report_dir)
    print(f"[AGENT] Report saved: {report_path}")

    # Step 9: Dispatch notification if configured
    notif_method = config.get("notification", {}).get("method", "file").lower()
    if notif_method == "email":
        from validator.notifier import send_email_report
        send_email_report(
            report_md=report_md,
            run_id=run_id,
            config=config,
            report_file_path=report_path,
            alerts_count=len(all_alerts),
            critical_count=total_critical,
        )

    # Print summary
    print(f"\n{'=' * 80}")
    print(f"[AGENT] ✅ Validation run #{run_id} complete.")
    print(f"[AGENT] Duration: {bt_result['duration_seconds']:.1f}s")
    print(f"[AGENT] Trades: {parsed.get('total_trades', 0)}")
    print(f"[AGENT] Alerts: {total_critical} critical, {total_warning} warning, {total_info} info")
    print(f"[AGENT] Report: {report_path}")
    if total_critical > 0:
        print(f"[AGENT] 🚨 CRITICAL issues found – review the report immediately!")
    elif total_warning > 0:
        print(f"[AGENT] ⚠️  Warnings detected – review the report when convenient.")
    else:
        print(f"[AGENT] ✅ All checks passed – engine looks healthy.")
    print(f"{'=' * 80}\n")

    return run_id


def _generate_research_notes(alerts: list) -> str:
    """Generate research notes based on alert types (stub for web-search integration)."""
    notes = []
    categories = set(a.get("category", "") for a in alerts if a["severity"] == "CRITICAL")

    if "metric_regression" in categories:
        notes.append(
            "**Metric Regression Detected**: Consider reviewing recent parameter changes. "
            "Common causes include: over-optimization to recent data (curve fitting), "
            "market regime change (trending → ranging), or data quality issues."
        )
    if "runtime_error" in categories:
        notes.append(
            "**Runtime Errors**: The backtest encountered exceptions. Check for: "
            "missing data files, API connectivity issues, or division-by-zero edge cases "
            "in indicator calculations."
        )
    if "metric_sanity" in categories:
        notes.append(
            "**Sanity Check Failures**: Impossible metric values detected. This typically "
            "indicates a bug in the calculation logic (e.g., win rate > 100%, negative "
            "profit factor). Review the fee deduction and R-multiple computation in trader.py."
        )

    if not notes:
        return ""

    return "## Research Notes (Auto-Generated)\n\n" + "\n\n".join(f"- {n}" for n in notes)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Backtest Validator Agent – Automated validation for Crypto Analyzer"
    )
    parser.add_argument(
        "--portfolio", action="store_true",
        help="Run portfolio backtest instead of single-asset"
    )
    parser.add_argument(
        "--symbol", type=str, default=None,
        help="Override symbol for single-asset mode"
    )
    parser.add_argument(
        "--timeframe", type=str, default=None,
        help="Override timeframe"
    )
    parser.add_argument(
        "--candles", type=int, default=None,
        help="Override number of candles"
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help="Path to agent_config.yaml"
    )
    parser.add_argument(
        "--skip-research", action="store_true",
        help="Skip web research step"
    )
    parser.add_argument(
        "--hifi", action="store_true", default=None,
        help="Enable 1m intra-bar replay"
    )
    parser.add_argument(
        "--no-hifi", action="store_true",
        help="Disable 1m intra-bar replay"
    )
    parser.add_argument(
        "--email", action="store_true",
        help="Send validation report via email"
    )

    args = parser.parse_args()
    config = load_config(args.config)
    if args.email:
        if "notification" not in config:
            config["notification"] = {}
        config["notification"]["method"] = "email"

    # Build overrides from CLI
    overrides = {}
    if args.symbol:
        overrides["symbol"] = args.symbol
    if args.timeframe:
        overrides["timeframe"] = args.timeframe
    if args.candles:
        overrides["candles"] = args.candles
    if args.hifi is True:
        overrides["hifi"] = True
    if args.no_hifi:
        overrides["hifi"] = False

    run_id = agent_run(
        config=config,
        portfolio=args.portfolio,
        overrides=overrides if overrides else None,
        skip_research=args.skip_research,
    )

    # Exit with non-zero if critical alerts were found
    from validator.db_manager import get_alerts_for_run
    run_alerts = get_alerts_for_run(run_id)
    critical_count = sum(1 for a in run_alerts if a.get("severity") == "CRITICAL")
    sys.exit(1 if critical_count > 0 else 0)


if __name__ == "__main__":
    main()
