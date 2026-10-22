"""
Backtest validation module.

Runs sanity checks on parsed backtest metrics and produces structured alerts.
Each alert is a dict with keys: severity, category, message, details.
"""

import hashlib
import statistics
from typing import Dict, List, Optional

from . import db_manager


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_all_checks(parsed_report: dict, run_id: int) -> List[dict]:
    """Run every validation check against *parsed_report* and persist alerts.

    Parameters
    ----------
    parsed_report : dict
        Parsed backtest output containing metric keys such as ``win_rate``,
        ``profit_factor``, ``total_trades``, ``cumulative_return_pct``, etc.
    run_id : int
        The database run identifier to associate alerts with.

    Returns
    -------
    List[dict]
        A list of alert dicts, each with keys
        ``severity``, ``category``, ``message``, ``details``.
    """
    alerts: List[dict] = []

    alerts.extend(_check_metric_regression(parsed_report, run_id))
    alerts.extend(_check_metric_sanity(parsed_report))
    alerts.extend(_check_runtime_errors(parsed_report))
    alerts.extend(_check_data_integrity(parsed_report))
    alerts.extend(_check_fee_sanity(parsed_report))

    # Persist every alert to the database.
    for alert in alerts:
        db_manager.save_alert(
            run_id,
            severity=alert["severity"],
            category=alert["category"],
            message=alert["message"],
            details=alert["details"],
        )

    return alerts


def compute_config_hashes(file_paths: List[str]) -> Dict[str, str]:
    """Compute SHA-256 hashes for a list of file paths.

    Parameters
    ----------
    file_paths : List[str]
        Absolute or relative paths to configuration files.

    Returns
    -------
    Dict[str, str]
        Mapping of *file_path* → hex-digest SHA-256 hash.
    """
    hashes: Dict[str, str] = {}
    for path in file_paths:
        sha = hashlib.sha256()
        try:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(8192), b""):
                    sha.update(chunk)
            hashes[path] = sha.hexdigest()
        except OSError:
            # If the file is unreadable we store a sentinel so callers can
            # still detect drift (the previous hash will never match).
            hashes[path] = "UNREADABLE"
    return hashes


def check_config_drift(
    run_id: int, config_hashes: Dict[str, str]
) -> List[dict]:
    """Compare current config hashes against the most recent DB snapshot.

    Parameters
    ----------
    run_id : int
        The database run identifier to associate alerts with.
    config_hashes : Dict[str, str]
        Mapping of *file_path* → current SHA-256 hex-digest.

    Returns
    -------
    List[dict]
        A list of INFO-level alert dicts for every file whose hash changed.
    """
    alerts: List[dict] = []
    snapshot = db_manager.get_latest_config_snapshot() or {}

    for file_path, current_hash in config_hashes.items():
        previous_hash = snapshot.get(file_path)
        if previous_hash is not None and previous_hash != current_hash:
            alert = _make_alert(
                severity="INFO",
                category="config_drift",
                message=f"Configuration file changed: {file_path}",
                details={
                    "file_path": file_path,
                    "previous_hash": previous_hash,
                    "current_hash": current_hash,
                },
            )
            alerts.append(alert)

    # Persist alerts.
    for alert in alerts:
        db_manager.save_alert(
            run_id,
            severity=alert["severity"],
            category=alert["category"],
            message=alert["message"],
            details=alert["details"],
        )

    return alerts


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _make_alert(
    severity: str, category: str, message: str, details: Optional[dict] = None
) -> dict:
    """Build a standardised alert dict."""
    return {
        "severity": severity,
        "category": category,
        "message": message,
        "details": details or {},
    }


def _safe_median(values: list) -> Optional[float]:
    """Return median of *values*, or ``None`` if the list is empty."""
    if not values:
        return None
    return statistics.median(values)


# -- 1. Metric Regression Detection -----------------------------------------

def _check_metric_regression(parsed_report: dict, run_id: int) -> List[dict]:
    """Compare current metrics against the median of the last 30 runs."""
    alerts: List[dict] = []

    history = db_manager.get_last_n_runs(30)
    if not history:
        return alerts

    # Collect historical series for each metric of interest.
    hist_win_rates = [r["win_rate"] for r in history if r.get("win_rate") is not None]
    hist_profit_factors = [r["profit_factor"] for r in history if r.get("profit_factor") is not None]
    hist_avg_r = [r["avg_r_multiple"] for r in history if r.get("avg_r_multiple") is not None]
    hist_max_dd = [r["max_drawdown"] for r in history if r.get("max_drawdown") is not None]
    hist_sharpe = [r["sharpe"] for r in history if r.get("sharpe") is not None]

    # --- win_rate ---
    med_wr = _safe_median(hist_win_rates)
    cur_wr = parsed_report.get("win_rate")
    if med_wr is not None and cur_wr is not None and med_wr > 0:
        pct_drop = (med_wr - cur_wr) / med_wr * 100
        if pct_drop > 20:
            alerts.append(_make_alert(
                "CRITICAL", "metric_regression",
                f"win_rate dropped {pct_drop:.1f}% below historical median",
                {"current": cur_wr, "median": med_wr, "drop_pct": round(pct_drop, 2)},
            ))
        elif pct_drop > 10:
            alerts.append(_make_alert(
                "WARNING", "metric_regression",
                f"win_rate dropped {pct_drop:.1f}% below historical median",
                {"current": cur_wr, "median": med_wr, "drop_pct": round(pct_drop, 2)},
            ))

    # --- profit_factor ---
    med_pf = _safe_median(hist_profit_factors)
    cur_pf = parsed_report.get("profit_factor")
    if med_pf is not None and cur_pf is not None and med_pf > 0:
        pct_drop = (med_pf - cur_pf) / med_pf * 100
        if pct_drop > 30:
            alerts.append(_make_alert(
                "CRITICAL", "metric_regression",
                f"profit_factor dropped {pct_drop:.1f}% below historical median",
                {"current": cur_pf, "median": med_pf, "drop_pct": round(pct_drop, 2)},
            ))

    # --- avg_r_multiple ---
    med_ar = _safe_median(hist_avg_r)
    cur_ar = parsed_report.get("avg_r_multiple")
    if med_ar is not None and cur_ar is not None:
        if cur_ar < 0 and med_ar > 0:
            alerts.append(_make_alert(
                "WARNING", "metric_regression",
                "avg_r_multiple turned negative (historical median was positive)",
                {"current": cur_ar, "median": med_ar},
            ))

    # --- max_drawdown ---
    med_dd = _safe_median(hist_max_dd)
    cur_dd = parsed_report.get("max_drawdown")
    if med_dd is not None and cur_dd is not None and med_dd > 0:
        pct_increase = (cur_dd - med_dd) / med_dd * 100
        if pct_increase > 50:
            alerts.append(_make_alert(
                "WARNING", "metric_regression",
                f"max_drawdown increased {pct_increase:.1f}% above historical median",
                {"current": cur_dd, "median": med_dd, "increase_pct": round(pct_increase, 2)},
            ))

    # --- sharpe ---
    med_sh = _safe_median(hist_sharpe)
    cur_sh = parsed_report.get("sharpe")
    if med_sh is not None and cur_sh is not None:
        if cur_sh < 0 and med_sh > 0.5:
            alerts.append(_make_alert(
                "WARNING", "metric_regression",
                "Sharpe ratio dropped below 0 (historical median > 0.5)",
                {"current": cur_sh, "median": med_sh},
            ))

    return alerts


# -- 2. Absolute Sanity Checks ----------------------------------------------

def _check_metric_sanity(parsed_report: dict) -> List[dict]:
    """Validate that individual metric values fall within sane ranges."""
    alerts: List[dict] = []

    win_rate = parsed_report.get("win_rate")
    if win_rate is not None:
        if win_rate > 100 or win_rate < 0:
            alerts.append(_make_alert(
                "CRITICAL", "metric_sanity",
                f"win_rate out of bounds: {win_rate}",
                {"win_rate": win_rate},
            ))

    profit_factor = parsed_report.get("profit_factor")
    if profit_factor is not None and profit_factor < 0:
        alerts.append(_make_alert(
            "CRITICAL", "metric_sanity",
            f"profit_factor is negative ({profit_factor}), which is mathematically impossible",
            {"profit_factor": profit_factor},
        ))

    total_trades = parsed_report.get("total_trades")
    if total_trades is not None and total_trades == 0:
        alerts.append(_make_alert(
            "WARNING", "metric_sanity",
            "total_trades is 0 — no trades were executed",
            {"total_trades": total_trades},
        ))

    cumulative_return_pct = parsed_report.get("cumulative_return_pct")
    if cumulative_return_pct is not None and cumulative_return_pct < -50:
        alerts.append(_make_alert(
            "CRITICAL", "metric_sanity",
            f"Excessive loss: cumulative_return_pct is {cumulative_return_pct}%",
            {"cumulative_return_pct": cumulative_return_pct},
        ))

    m2m_max_dd = parsed_report.get("m2m_max_drawdown_pct")
    if m2m_max_dd is not None and m2m_max_dd > 30:
        alerts.append(_make_alert(
            "WARNING", "metric_sanity",
            f"m2m_max_drawdown_pct is {m2m_max_dd}%, exceeding 30% threshold",
            {"m2m_max_drawdown_pct": m2m_max_dd},
        ))

    ev_per_trade = parsed_report.get("ev_per_trade")
    if ev_per_trade is not None and ev_per_trade < -0.5:
        alerts.append(_make_alert(
            "CRITICAL", "metric_sanity",
            f"Negative expectancy: ev_per_trade is {ev_per_trade}",
            {"ev_per_trade": ev_per_trade},
        ))

    return alerts


# -- 3. Runtime Error Detection ----------------------------------------------

def _check_runtime_errors(parsed_report: dict) -> List[dict]:
    """Scan stderr output and exit code for runtime problems."""
    alerts: List[dict] = []

    stderr_lines = parsed_report.get("stderr_lines")
    if stderr_lines:
        error_keywords = ("Traceback", "Error", "Exception")
        error_lines = [
            line for line in stderr_lines
            if any(kw in line for kw in error_keywords)
        ]
        if error_lines:
            alerts.append(_make_alert(
                "CRITICAL", "runtime_error",
                "Runtime errors detected in stderr output",
                {"matching_lines": error_lines[:20]},  # cap to avoid huge payloads
            ))

        warning_lines = [line for line in stderr_lines if "WARNING" in line]
        if warning_lines:
            alerts.append(_make_alert(
                "INFO", "runtime_error",
                "Warning messages detected in stderr output",
                {"matching_lines": warning_lines[:20]},
            ))

    exit_code = parsed_report.get("exit_code")
    if exit_code is not None and exit_code != 0:
        alerts.append(_make_alert(
            "CRITICAL", "runtime_error",
            f"Non-zero exit code: {exit_code}",
            {"exit_code": exit_code},
        ))

    return alerts


# -- 4. Data Integrity -------------------------------------------------------

def _check_data_integrity(parsed_report: dict) -> List[dict]:
    """Verify that enough data was present for a meaningful backtest."""
    alerts: List[dict] = []

    evaluated_bars = parsed_report.get("evaluated_bars")
    if evaluated_bars is None or evaluated_bars < 100:
        alerts.append(_make_alert(
            "WARNING", "data_integrity",
            "Insufficient data for reliable backtest results",
            {"evaluated_bars": evaluated_bars},
        ))

    total_trades = parsed_report.get("total_trades")
    if (
        total_trades is not None
        and total_trades == 0
        and evaluated_bars is not None
        and evaluated_bars > 500
    ):
        alerts.append(_make_alert(
            "WARNING", "data_integrity",
            "No signals generated despite sufficient evaluated bars",
            {"evaluated_bars": evaluated_bars, "total_trades": total_trades},
        ))

    return alerts


# -- 6. Fee Sanity -----------------------------------------------------------

def _check_fee_sanity(parsed_report: dict) -> List[dict]:
    """Flag suspiciously high returns relative to trade count (0.15% fee drag)."""
    alerts: List[dict] = []

    cumulative_return_pct = parsed_report.get("cumulative_return_pct")
    total_trades = parsed_report.get("total_trades")

    if (
        cumulative_return_pct is not None
        and total_trades is not None
        and cumulative_return_pct > 200
        and total_trades < 500
    ):
        alerts.append(_make_alert(
            "WARNING", "fee_sanity",
            (
                f"Cumulative return of {cumulative_return_pct}% on only "
                f"{total_trades} trades is suspiciously high given 0.15% fee drag"
            ),
            {
                "cumulative_return_pct": cumulative_return_pct,
                "total_trades": total_trades,
                "fee_per_trade_pct": 0.15,
            },
        ))

    return alerts
