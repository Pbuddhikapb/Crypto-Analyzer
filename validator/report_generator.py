"""
Report generator for backtest validation runs.

Produces a structured markdown report from parsed backtest data,
alerts, trade rows, and optional research notes — using plain
Python f-strings (no Jinja2).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import db_manager


# ── Emoji / icon look-ups ────────────────────────────────────────────
_SEVERITY_EMOJI = {
    "CRITICAL": "🔴",
    "WARNING": "🟡",
    "INFO": "🔵",
}

_SEVERITY_ORDER = ["CRITICAL", "WARNING", "INFO"]

_TREND_ARROWS = {
    "up": "↑",
    "down": "↓",
    "flat": "→",
}

# Metrics where a *lower* value is better (e.g. max drawdown).
_LOWER_IS_BETTER = {
    "max_drawdown",
    "max_drawdown_pct",
    "avg_loss",
    "losing_streak",
    "max_losing_streak",
    "avg_drawdown",
    "volatility",
}


# ── Helpers ───────────────────────────────────────────────────────────

def _fmt(value: Any) -> str:
    """Format a metric value for display."""
    if isinstance(value, float):
        return f"{value:,.4f}" if abs(value) < 1 else f"{value:,.2f}"
    if value is None:
        return "N/A"
    return str(value)


def _trend_arrow(metric_name: str, current: float, historical_avg: float) -> str:
    """Return an arrow character comparing *current* to *historical_avg*."""
    if historical_avg == 0:
        return _TREND_ARROWS["flat"]

    pct_change = (current - historical_avg) / abs(historical_avg)

    # Dead-band: treat < 2 % change as flat
    if abs(pct_change) < 0.02:
        return _TREND_ARROWS["flat"]

    improved = pct_change > 0
    if metric_name.lower() in _LOWER_IS_BETTER:
        improved = not improved

    return _TREND_ARROWS["up"] if improved else _TREND_ARROWS["down"]


def _section(title: str, body: str) -> str:
    """Wrap *body* in a markdown section with a horizontal rule."""
    return f"\n---\n\n## {title}\n\n{body}\n"


# ── Section builders ─────────────────────────────────────────────────

def _build_header(timestamp: str) -> str:
    return f"# Backtest Validation Report — {timestamp}\n"


def _build_run_metadata(parsed_report: dict) -> str:
    meta = parsed_report.get("metadata", {})
    mode = meta.get("mode", parsed_report.get("mode", "N/A"))
    symbols = meta.get("symbols", meta.get("symbol", parsed_report.get("symbol", "N/A")))
    if isinstance(symbols, list):
        symbols = ", ".join(symbols)
    timeframe = meta.get("timeframe", parsed_report.get("timeframe", "N/A"))
    evaluated_bars = meta.get("evaluated_bars", parsed_report.get("evaluated_bars", "N/A"))
    cli_args = meta.get("cli_args", parsed_report.get("cli_args", "N/A"))
    duration = meta.get("duration", parsed_report.get("duration", "N/A"))

    lines = [
        f"| Field | Value |",
        f"|-------|-------|",
        f"| **Mode** | {mode} |",
        f"| **Symbol(s)** | {symbols} |",
        f"| **Timeframe** | {timeframe} |",
        f"| **Evaluated Bars** | {evaluated_bars} |",
        f"| **CLI Args** | `{cli_args}` |",
        f"| **Duration** | {duration} |",
    ]
    return "\n".join(lines)


def _build_summary_metrics(parsed_report: dict) -> str:
    metrics: dict = parsed_report.get("metrics")
    if metrics is None:
        # Extract numeric metrics from flat parsed_report dict
        skip_keys = {"mode", "symbol", "timeframe", "metadata", "cli_args", "duration", "per_symbol_stats", "stderr_lines", "exit_code"}
        metrics = {k: v for k, v in parsed_report.items() if k not in skip_keys and v is not None}

    if not metrics:
        return "_No metrics available._"

    rows = ["| Metric | Value |", "|--------|-------|"]
    for key, value in metrics.items():
        label = key.replace("_", " ").title()
        rows.append(f"| {label} | {_fmt(value)} |")
    return "\n".join(rows)


def _build_trend_analysis(run_id: int, parsed_report: dict) -> str:
    """Compare current metrics against the average of the last 5 runs."""
    current_metrics: dict = parsed_report.get("metrics")
    if current_metrics is None:
        skip_keys = {"mode", "symbol", "timeframe", "metadata", "cli_args", "duration", "per_symbol_stats", "stderr_lines", "exit_code"}
        current_metrics = {k: v for k, v in parsed_report.items() if k not in skip_keys and isinstance(v, (int, float))}

    if not current_metrics:
        return "_Not enough data for trend analysis._"

    try:
        raw_history: List[dict] = db_manager.get_last_n_runs(n=6)
        history = [r for r in raw_history if r.get("id") != run_id][:5]
    except Exception:
        history = []

    if not history:
        return "_No prior runs found — trend analysis requires at least one historical run._"

    # Compute per-metric averages across historical runs
    avg_metrics: Dict[str, float] = {}
    for metric_name in current_metrics:
        values = []
        for run in history:
            val = run.get(metric_name)
            if val is not None and isinstance(val, (int, float)):
                values.append(float(val))
        if values:
            avg_metrics[metric_name] = sum(values) / len(values)

    if not avg_metrics:
        return "_Historical runs lack comparable metrics — cannot compute trend._"

    rows = [
        "| Metric | Current | Avg (Last 5) | Trend |",
        "|--------|---------|--------------|-------|",
    ]
    for key in current_metrics:
        current_val = current_metrics[key]
        if key not in avg_metrics or not isinstance(current_val, (int, float)):
            continue
        avg_val = avg_metrics[key]
        arrow = _trend_arrow(key, float(current_val), avg_val)
        label = key.replace("_", " ").title()
        rows.append(f"| {label} | {_fmt(current_val)} | {_fmt(avg_val)} | {arrow} |")

    if len(rows) == 2:
        return "_No numeric metrics available for trend comparison._"

    return "\n".join(rows)


def _build_alerts_section(alerts: list) -> str:
    if not alerts:
        return "_No alerts raised — all checks passed._ ✅"

    grouped: Dict[str, list] = {sev: [] for sev in _SEVERITY_ORDER}
    for alert in alerts:
        sev = alert.get("severity", "INFO").upper()
        if sev not in grouped:
            grouped[sev] = []
        grouped.setdefault(sev, []).append(alert)

    parts: list[str] = []
    for severity in _SEVERITY_ORDER:
        items = grouped.get(severity, [])
        if not items:
            continue
        emoji = _SEVERITY_EMOJI.get(severity, "⚪")
        parts.append(f"### {emoji} {severity} ({len(items)})\n")
        for alert in items:
            rule = alert.get("category", alert.get("rule", "Notice"))
            message = alert.get("message", "")
            detail = alert.get("detail", alert.get("details", ""))
            line = f"- **{rule}**: {message}"
            if detail:
                line += f"  \n  _{detail}_"
            parts.append(line)
        parts.append("")  # blank line between groups

    return "\n".join(parts)


def _build_trade_breakdown(trade_rows: list) -> str:
    """Summarise wins/losses by trade classification."""
    if not trade_rows:
        return "_No trade data available._"

    # Classify
    stats: Dict[str, Dict[str, int]] = {}
    for trade in trade_rows:
        classification = trade.get("classification", "UNKNOWN")
        pnl = trade.get("net_return_pct", trade.get("r_multiple", trade.get("pnl", 0.0)))
        if classification not in stats:
            stats[classification] = {"wins": 0, "losses": 0, "breakeven": 0, "total": 0}
        stats[classification]["total"] += 1
        if isinstance(pnl, (int, float)):
            if pnl > 0:
                stats[classification]["wins"] += 1
            elif pnl < 0:
                stats[classification]["losses"] += 1
            else:
                stats[classification]["breakeven"] += 1

    # Canonical order (known types first, then alphabetical remainder)
    preferred_order = [
        "TAKE_PROFIT", "BREAKEVEN", "NORMAL_STOP",
        "WHALE_SPIKE", "TIME_STOP",
    ]
    ordered_keys = [k for k in preferred_order if k in stats]
    ordered_keys += sorted(k for k in stats if k not in preferred_order)

    rows = [
        "| Classification | Total | Wins | Losses | Breakeven | Win Rate |",
        "|----------------|-------|------|--------|-----------|----------|",
    ]
    for cls in ordered_keys:
        s = stats[cls]
        win_rate = (s["wins"] / s["total"] * 100) if s["total"] else 0.0
        rows.append(
            f"| {cls} | {s['total']} | {s['wins']} | {s['losses']} "
            f"| {s['breakeven']} | {win_rate:.1f}% |"
        )

    # Grand totals
    total_all = sum(s["total"] for s in stats.values())
    wins_all = sum(s["wins"] for s in stats.values())
    losses_all = sum(s["losses"] for s in stats.values())
    be_all = sum(s["breakeven"] for s in stats.values())
    wr_all = (wins_all / total_all * 100) if total_all else 0.0
    rows.append(
        f"| **TOTAL** | **{total_all}** | **{wins_all}** | **{losses_all}** "
        f"| **{be_all}** | **{wr_all:.1f}%** |"
    )
    return "\n".join(rows)


def _build_research_notes(research_notes: str) -> str:
    return research_notes.strip()


def _build_suggested_actions(alerts: list) -> str:
    """Derive actionable recommendations from the alert list."""
    if not alerts:
        return "- ✅ No issues detected — no actions required."

    actions: list[str] = []
    seen: set[str] = set()

    for alert in alerts:
        rule = alert.get("rule", "").lower()
        severity = alert.get("severity", "INFO").upper()
        message = alert.get("message", "")

        # De-duplicate by rule
        if rule in seen:
            continue
        seen.add(rule)

        # ── Rule-specific recommendations ────────────────────────
        if "drawdown" in rule:
            actions.append(
                "- 📉 **Reduce position sizing** or tighten stop-loss levels "
                "to mitigate excessive drawdown."
            )
        elif "win_rate" in rule or "win rate" in rule:
            actions.append(
                "- 🎯 **Review entry criteria** — the win rate has deteriorated. "
                "Consider adding confirmation filters."
            )
        elif "sharpe" in rule:
            actions.append(
                "- 📊 **Evaluate risk-adjusted returns** — a low Sharpe ratio "
                "suggests returns don't justify the risk taken."
            )
        elif "profit_factor" in rule or "profit factor" in rule:
            actions.append(
                "- 💰 **Investigate losing trades** — the profit factor is below "
                "acceptable thresholds."
            )
        elif "whale" in rule or "spike" in rule:
            actions.append(
                "- 🐋 **Add whale-spike filters** or extend cooldown periods "
                "after abnormal volume events."
            )
        elif "slippage" in rule:
            actions.append(
                "- ⚡ **Account for slippage** — consider using limit orders or "
                "adjusting fill assumptions."
            )
        elif "overfit" in rule or "curve_fit" in rule:
            actions.append(
                "- 🔬 **Run walk-forward or out-of-sample tests** to confirm the "
                "strategy is not over-fitted."
            )
        elif "duration" in rule or "time" in rule:
            actions.append(
                "- ⏱️ **Review trade holding periods** — some trades may be "
                "held too long without resolution."
            )
        elif "correlation" in rule:
            actions.append(
                "- 🔗 **Check asset correlation** — highly correlated positions "
                "amplify portfolio risk."
            )
        else:
            # Generic recommendation
            if severity == "CRITICAL":
                actions.append(
                    f"- 🚨 **Address critical alert** _{rule}_: {message}"
                )
            elif severity == "WARNING":
                actions.append(
                    f"- ⚠️ **Investigate warning** _{rule}_: {message}"
                )
            else:
                actions.append(
                    f"- ℹ️ **Review info** _{rule}_: {message}"
                )

    if not actions:
        return "- ✅ No specific actions required."

    return "\n".join(actions)


# ── Public API ────────────────────────────────────────────────────────

def generate_report(
    run_id: int,
    parsed_report: dict,
    alerts: list,
    trade_rows: list,
    research_notes: str = "",
) -> str:
    """Build a complete markdown validation report.

    Parameters
    ----------
    run_id:
        Database identifier for this backtest run.
    parsed_report:
        Dict with at least ``metadata`` and ``metrics`` sub-dicts.
    alerts:
        List of alert dicts, each with ``severity``, ``rule``, ``message``,
        and optionally ``detail``.
    trade_rows:
        List of trade dicts, each with ``classification`` and ``pnl``
        (or ``profit``) keys.
    research_notes:
        Free-form markdown string with external research findings.

    Returns
    -------
    str
        The full report as a markdown string.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    sections: list[str] = []

    # 1. Header
    sections.append(_build_header(timestamp))

    # 2. Run Metadata
    sections.append(_section("Run Metadata", _build_run_metadata(parsed_report)))

    # 3. Summary Metrics
    sections.append(_section("Summary Metrics", _build_summary_metrics(parsed_report)))

    # 4. Trend Analysis
    sections.append(_section("Trend Analysis", _build_trend_analysis(run_id, parsed_report)))

    # 5. Alerts
    sections.append(_section("Alerts", _build_alerts_section(alerts)))

    # 6. Trade Breakdown
    sections.append(_section("Trade Breakdown", _build_trade_breakdown(trade_rows)))

    # 7. Research Notes (optional)
    if research_notes and research_notes.strip():
        sections.append(_section("Research Notes", _build_research_notes(research_notes)))

    # 8. Suggested Actions
    sections.append(_section("Suggested Actions", _build_suggested_actions(alerts)))

    # Footer
    sections.append(
        f"\n---\n\n_Report generated for run **#{run_id}** at {timestamp}._\n"
    )

    return "\n".join(sections)


from pathlib import Path
DEFAULT_REPORTS_DIR = str(Path(__file__).resolve().parent.parent / "reports")


def save_report(
    report_md: str,
    run_id: int,
    output_dir: Optional[str] = None,
) -> str:
    """Write the markdown report to disk and return the file path.

    Parameters
    ----------
    report_md:
        The rendered markdown string.
    run_id:
        Database run identifier (used in the filename).
    output_dir:
        Directory to store reports. Created if it doesn't exist.

    Returns
    -------
    str
        Absolute path to the saved report file.
    """
    if output_dir is None:
        output_dir = DEFAULT_REPORTS_DIR
    os.makedirs(output_dir, exist_ok=True)
    timestamp_slug = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"validation_run{run_id}_{timestamp_slug}.md"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write(report_md)

    return filepath
