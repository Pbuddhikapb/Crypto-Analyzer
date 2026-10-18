"""
output_parser.py — Parse human-readable stdout from backtest.py into structured dicts.

Supports two report formats:
  1. Single-asset report  (print_backtest_report)
  2. Portfolio report     (run_portfolio_backtest)

Exports:
  parse_backtest_output(stdout)  -> dict   with all metrics
  parse_trade_rows(stdout)       -> list   of individual trade dicts
  parse_stderr(stderr)           -> list   of warning/error strings
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _float_or_none(val: Optional[str]) -> Optional[float]:
    """Convert a regex-captured string to float, stripping +/%/$ signs."""
    if val is None:
        return None
    val = val.strip().replace("%", "").replace(",", "").replace("$", "")
    # Handle leading '+' that float() already accepts
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _int_or_none(val: Optional[str]) -> Optional[int]:
    """Convert a regex-captured string to int, stripping commas."""
    if val is None:
        return None
    val = val.strip().replace(",", "")
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _search(pattern: str, text: str, flags: int = 0):
    """Convenience wrapper returning the match or None."""
    return re.search(pattern, text, flags)


# ---------------------------------------------------------------------------
# Single-asset header / summary
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(
    r"\[BACKTEST REPORT\]\s+"
    r"(?P<symbol>\S+)\s+\((?P<timeframe>[^)]+)\)\s*\|\s*"
    r"MODE:\s*(?P<mode_features>.+?)\s*\|\s*"
    r"EVALUATED BARS:\s*(?P<evaluated_bars>[\d,]+)\s*\|\s*"
    r"TOTAL TRADES:\s*(?P<total_trades>[\d,]+)",
)

_WIN_RATE_RE = re.compile(
    r"WIN RATE:\s*(?P<win_rate>[\d.]+)%\s*\(\s*(?P<win_count>\d+)W\s*/\s*(?P<loss_count>\d+)L\s*\)"
)

_PROFIT_FACTOR_RE = re.compile(
    r"PROFIT FACTOR:\s*(?P<profit_factor>[\d.]+)"
)

_CUMULATIVE_RETURN_RE = re.compile(
    r"CUMULATIVE RETURN:\s*(?P<cumulative_return>[+\-]?[\d.]+)%"
)

_AVG_R_RE = re.compile(
    r"AVG R-MULTIPLE:\s*(?P<avg_r>[+\-]?[\d.]+)R"
)

_CLOSED_DD_RE = re.compile(
    r"CLOSED MAX DRAWDOWN:\s*(?P<closed_dd>[\d.]+)%"
)

_M2M_DD_RE = re.compile(
    r"M2M MAX DRAWDOWN:\s*(?P<m2m_dd>[\d.]+)%"
)

_INTRA_DD_RE = re.compile(
    r"WORST INTRA-BAR DD:\s*(?P<intra_dd>[\d.]+)%"
)

_LONG_PERF_RE = re.compile(
    r"LONG PERFORMANCE:\s*(?P<long_trades>\d+)\s+trades?\s*\(Win Rate:\s*(?P<long_wr>[\d.]+)%\)"
)

_SHORT_PERF_RE = re.compile(
    r"SHORT PERFORMANCE:\s*(?P<short_trades>\d+)\s+trades?\s*\(Win Rate:\s*(?P<short_wr>[\d.]+)%\)"
)

_AVG_HOLD_RE = re.compile(
    r"AVG HOLD TIME:\s*(?P<avg_hold>[\d.]+)\s*mins?"
)

# Whale analysis
_WHALE_LOSSES_RE = re.compile(
    r"Losses from Abnormal Whale Volume[^:]*:\s*(?P<whale_losses>\d+)"
)

_NORMAL_TECH_LOSSES_RE = re.compile(
    r"Losses from Normal Technical Invalidation:\s*(?P<normal_tech>\d+)"
)

_ADJ_WR_RE = re.compile(
    r"Technical Strategy Baseline Win Rate \(Excluding Whale Blowouts\):\s*(?P<adj_wr>[\d.]+)%"
)


# ---------------------------------------------------------------------------
# Portfolio header / summary
# ---------------------------------------------------------------------------

_PORT_HEADER_RE = re.compile(
    r"\[PORTFOLIO AUDIT REPORT\]\s*\|\s*(?P<num_assets>\d+)\s*ASSETS?\s*\|\s*"
    r"EVALUATED BARS:\s*(?P<evaluated_bars>[\d,]+)\s*\|\s*"
    r"TOTAL TRADES:\s*(?P<total_trades>[\d,]+)"
)

_PORT_TOTAL_TRADES_RE = re.compile(
    r"TOTAL PORTFOLIO TRADES:\s*(?P<total_portfolio_trades>[\d,]+)"
)

_PORT_OVERALL_WR_RE = re.compile(
    r"OVERALL WIN RATE:\s*(?P<overall_win_rate>[\d.]+)%\s*\(\s*(?P<win_count>\d+)W\s*/\s*(?P<loss_count>\d+)L\s*\)"
)

_PORT_PROFIT_FACTOR_RE = re.compile(
    r"PORTFOLIO PROFIT FACTOR:\s*(?P<portfolio_pf>[\d.]+)"
)

_PORT_CUM_R_RE = re.compile(
    r"CUMULATIVE R-MULTIPLE:\s*(?P<cum_r>[+\-]?[\d.]+)R"
)

_PORT_EV_RE = re.compile(
    r"EXPECTED VALUE \(EV / T\):\s*(?P<ev>[+\-]?[\d.]+)R"
)

_PORT_PAYOFF_RE = re.compile(
    r"WIN/LOSS PAYOFF RATIO:\s*(?P<payoff>[\d.]+)"
)

_PORT_NET_RETURN_RE = re.compile(
    r"PORTFOLIO NET RETURN:\s*(?P<net_return>[+\-]?[\d.]+)%"
)

_PORT_CLOSED_DD_RE = re.compile(
    r"CLOSED-TRADE MAX DD:\s*(?P<closed_dd>[\d.]+)%"
)

_PORT_SHARPE_RE = re.compile(
    r"TRADE-LEVEL SHARPE:\s*(?P<sharpe>[+\-]?[\d.]+)"
)

_PORT_M2M_DD_RE = re.compile(
    r"MARK-TO-MARKET MAX DD:\s*(?P<m2m_dd>[\d.]+)%"
)

_PORT_INTRA_DD_RE = re.compile(
    r"WORST INTRA-BAR DD:\s*(?P<intra_dd>[\d.]+)%"
)

_PORT_ANN_RETURN_RE = re.compile(
    r"ANNUALIZED RETURN:\s*(?P<ann_return>[+\-]?[\d.]+)%"
)

_PORT_CALMAR_RE = re.compile(
    r"CALMAR RATIO \(M2M\):\s*(?P<calmar_m2m>[+\-]?[\d.]+)\s*\(Closed:\s*(?P<calmar_closed>[+\-]?[\d.]+)\)"
)

# Capacity utilization
_CAP_0_RE = re.compile(r"0 Positions Active[^:]*:\s*(?P<pct>[\d.]+)%\s*\((?P<bars>[\d,]+)\s*bars?\)")
_CAP_1_RE = re.compile(r"1 Position Active[^:]*:\s*(?P<pct>[\d.]+)%\s*\((?P<bars>[\d,]+)\s*bars?\)")
_CAP_2_RE = re.compile(r"2 Positions Active[^:]*:\s*(?P<pct>[\d.]+)%\s*\((?P<bars>[\d,]+)\s*bars?\)")
_CAP_3_RE = re.compile(r"3 Positions Active[^:]*:\s*(?P<pct>[\d.]+)%\s*\((?P<bars>[\d,]+)\s*bars?\)")

_SIGNALS_BLOCKED_CAP_RE = re.compile(
    r"Signals Blocked by 3-Position Cap:\s*(?P<blocked>\d+)"
)

_SIGNALS_BLOCKED_COOL_RE = re.compile(
    r"Signals Blocked by Symbol Cooldown:\s*(?P<blocked>\d+)"
)

# Per-symbol stats table row  (portfolio breakdown)
# Format: SYMBOL   | TRADES | WINS | LOSS | WIN RATE | PROFIT FAC | NET RET % | AVG R | MAX DD % | ADJ WR %
_PER_SYMBOL_ROW_RE = re.compile(
    r"^\s*(?P<symbol>[A-Z0-9]+)\s*\|\s*"
    r"(?P<trades>\d+)\s*\|\s*"
    r"(?P<wins>\d+)\s*\|\s*"
    r"(?P<losses>\d+)\s*\|\s*"
    r"(?P<win_rate>[+\-]?[\d.]+)%?\s*\|\s*"
    r"(?P<profit_factor>[+\-]?[\d.]+)\s*\|\s*"
    r"(?P<net_return>[+\-]?[\d.]+)%?\s*\|\s*"
    r"(?P<avg_r>[+\-]?[\d.]+)R?\s*\|\s*"
    r"(?P<max_dd>[+\-]?[\d.]+)%?\s*\|\s*"
    r"(?P<adj_wr>[+\-]?[\d.]+)%?",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Trade-row parsing
# ---------------------------------------------------------------------------

# Single-asset trade row (no SYMBOL column, optional HOLD column for hifi):
#   ENTRY TIME       | SIDE  | ENTRY      | EXIT       | REASON       | NET %    | R-MULT  | HOLD      | VOL RATIO | CLASSIFICATION
#   ENTRY TIME       | SIDE  | ENTRY      | EXIT       | REASON       | NET %    | R-MULT  | VOL RATIO | CLASSIFICATION
_SINGLE_TRADE_RE = re.compile(
    r"^(?P<entry_time>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*\|\s*"
    r"(?P<side>LONG|SHORT)\s*\|\s*"
    r"(?P<entry_price>\$?[\d.]+)\s*\|\s*"
    r"(?P<exit_price>\$?[\d.]+)\s*\|\s*"
    r"(?P<reason>\S+)\s*\|\s*"
    r"(?P<net_return>[+\-]?[\d.]+)%\s*\|\s*"
    r"(?P<r_multiple>[+\-]?[\d.]+)R\s*\|\s*"
    r"(?:(?P<hold_minutes>\d+)m\s*\|\s*)?"
    r"(?P<vol_ratio>[\d.]+)x\s*\|\s*"
    r"(?P<classification>.+?)\s*$",
    re.MULTILINE,
)

# Portfolio trade row (has SYMBOL column between ENTRY TIME and SIDE):
#   ENTRY TIME       | SYMBOL   | SIDE  | ENTRY      | EXIT       | REASON       | NET %    | R-MULT  | VOL RATIO | CLASSIFICATION
_PORT_TRADE_RE = re.compile(
    r"^(?P<entry_time>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*\|\s*"
    r"(?P<symbol>[A-Z0-9]+)\s*\|\s*"
    r"(?P<side>LONG|SHORT)\s*\|\s*"
    r"(?P<entry_price>\$?[\d.]+)\s*\|\s*"
    r"(?P<exit_price>\$?[\d.]+)\s*\|\s*"
    r"(?P<reason>\S+)\s*\|\s*"
    r"(?P<net_return>[+\-]?[\d.]+)%\s*\|\s*"
    r"(?P<r_multiple>[+\-]?[\d.]+)R\s*\|\s*"
    r"(?P<vol_ratio>[\d.]+)x\s*\|\s*"
    r"(?P<classification>.+?)\s*$",
    re.MULTILINE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_backtest_output(stdout: str) -> Dict[str, Any]:
    """Parse human-readable backtest stdout into a structured metrics dict.

    Returns a dict whose ``mode`` key is either ``'single'`` or ``'portfolio'``.
    Metrics that cannot be located in the text are set to ``None``.
    """
    result: Dict[str, Any] = {}

    # Determine mode
    is_portfolio = bool(_PORT_HEADER_RE.search(stdout))
    result["mode"] = "portfolio" if is_portfolio else "single"

    if is_portfolio:
        _parse_portfolio(stdout, result)
    else:
        _parse_single(stdout, result)

    return result


def parse_trade_rows(stdout: str) -> List[Dict[str, Any]]:
    """Extract individual trade rows from the backtest stdout.

    Automatically detects whether the output is single-asset or portfolio
    format and returns a list of trade dicts.
    """
    trades: List[Dict[str, Any]] = []

    # Try portfolio rows first (they have a SYMBOL column)
    for m in _PORT_TRADE_RE.finditer(stdout):
        trades.append(_trade_dict_from_match(m, is_portfolio=True))

    if trades:
        return trades

    # Fall back to single-asset rows
    for m in _SINGLE_TRADE_RE.finditer(stdout):
        trades.append(_trade_dict_from_match(m, is_portfolio=False))

    return trades


def parse_stderr(stderr: str) -> List[str]:
    """Extract warning and error lines from backtest stderr.

    Returns a list of non-empty stripped lines that contain 'warning',
    'error', 'traceback', 'exception', or start with common Python
    error prefixes.
    """
    if not stderr:
        return []

    lines: List[str] = []
    collecting_traceback = False

    for raw_line in stderr.splitlines():
        line = raw_line.strip()
        if not line:
            collecting_traceback = False
            continue

        lower = line.lower()

        # Start of a Python traceback block
        if lower.startswith("traceback"):
            collecting_traceback = True
            lines.append(line)
            continue

        if collecting_traceback:
            lines.append(line)
            # The final line of a traceback is the exception itself
            if not line.startswith(" ") and not line.startswith("File"):
                collecting_traceback = False
            continue

        # Standalone warning / error lines
        if any(kw in lower for kw in ("warning", "error", "exception", "fatal", "critical")):
            lines.append(line)

    return lines


# ---------------------------------------------------------------------------
# Internal parsers
# ---------------------------------------------------------------------------

def _parse_single(stdout: str, result: Dict[str, Any]) -> None:
    """Populate *result* with single-asset report metrics."""

    m = _HEADER_RE.search(stdout)
    result["symbol"] = m.group("symbol") if m else None
    result["timeframe"] = m.group("timeframe") if m else None
    result["mode_features"] = m.group("mode_features").strip() if m else None
    result["evaluated_bars"] = _int_or_none(m.group("evaluated_bars")) if m else None
    result["total_trades"] = _int_or_none(m.group("total_trades")) if m else None

    m = _WIN_RATE_RE.search(stdout)
    result["win_rate"] = _float_or_none(m.group("win_rate")) if m else None
    result["win_count"] = _int_or_none(m.group("win_count")) if m else None
    result["loss_count"] = _int_or_none(m.group("loss_count")) if m else None

    m = _PROFIT_FACTOR_RE.search(stdout)
    result["profit_factor"] = _float_or_none(m.group("profit_factor")) if m else None

    m = _CUMULATIVE_RETURN_RE.search(stdout)
    result["cumulative_return_pct"] = _float_or_none(m.group("cumulative_return")) if m else None

    m = _AVG_R_RE.search(stdout)
    result["avg_r_multiple"] = _float_or_none(m.group("avg_r")) if m else None

    m = _CLOSED_DD_RE.search(stdout)
    result["closed_max_drawdown_pct"] = _float_or_none(m.group("closed_dd")) if m else None

    m = _M2M_DD_RE.search(stdout)
    result["m2m_max_drawdown_pct"] = _float_or_none(m.group("m2m_dd")) if m else None

    m = _INTRA_DD_RE.search(stdout)
    result["worst_intra_bar_dd_pct"] = _float_or_none(m.group("intra_dd")) if m else None

    m = _LONG_PERF_RE.search(stdout)
    result["long_trades"] = _int_or_none(m.group("long_trades")) if m else None
    result["long_win_rate"] = _float_or_none(m.group("long_wr")) if m else None

    m = _SHORT_PERF_RE.search(stdout)
    result["short_trades"] = _int_or_none(m.group("short_trades")) if m else None
    result["short_win_rate"] = _float_or_none(m.group("short_wr")) if m else None

    # Whale analysis
    m = _WHALE_LOSSES_RE.search(stdout)
    result["whale_losses"] = _int_or_none(m.group("whale_losses")) if m else None

    m = _NORMAL_TECH_LOSSES_RE.search(stdout)
    result["normal_tech_losses"] = _int_or_none(m.group("normal_tech")) if m else None

    m = _ADJ_WR_RE.search(stdout)
    result["adj_win_rate_ex_whale"] = _float_or_none(m.group("adj_wr")) if m else None

    # HiFi only
    m = _AVG_HOLD_RE.search(stdout)
    result["avg_hold_minutes"] = _float_or_none(m.group("avg_hold")) if m else None


def _parse_portfolio(stdout: str, result: Dict[str, Any]) -> None:
    """Populate *result* with portfolio report metrics."""

    # --- Header ---
    m = _PORT_HEADER_RE.search(stdout)
    result["evaluated_bars"] = _int_or_none(m.group("evaluated_bars")) if m else None
    # total_trades from header (fallback)
    header_total = _int_or_none(m.group("total_trades")) if m else None

    # --- Portfolio Summary ---
    m = _PORT_TOTAL_TRADES_RE.search(stdout)
    result["total_portfolio_trades"] = _int_or_none(m.group("total_portfolio_trades")) if m else header_total

    m = _PORT_OVERALL_WR_RE.search(stdout)
    result["overall_win_rate"] = _float_or_none(m.group("overall_win_rate")) if m else None
    result["win_count"] = _int_or_none(m.group("win_count")) if m else None
    result["loss_count"] = _int_or_none(m.group("loss_count")) if m else None

    m = _PORT_PROFIT_FACTOR_RE.search(stdout)
    result["portfolio_profit_factor"] = _float_or_none(m.group("portfolio_pf")) if m else None

    m = _PORT_CUM_R_RE.search(stdout)
    result["cumulative_r_multiple"] = _float_or_none(m.group("cum_r")) if m else None

    m = _PORT_EV_RE.search(stdout)
    result["ev_per_trade"] = _float_or_none(m.group("ev")) if m else None

    m = _PORT_PAYOFF_RE.search(stdout)
    result["payoff_ratio"] = _float_or_none(m.group("payoff")) if m else None

    m = _PORT_NET_RETURN_RE.search(stdout)
    result["portfolio_net_return_pct"] = _float_or_none(m.group("net_return")) if m else None

    m = _PORT_CLOSED_DD_RE.search(stdout)
    result["closed_trade_max_dd_pct"] = _float_or_none(m.group("closed_dd")) if m else None

    m = _PORT_SHARPE_RE.search(stdout)
    result["trade_level_sharpe"] = _float_or_none(m.group("sharpe")) if m else None

    m = _PORT_M2M_DD_RE.search(stdout)
    result["m2m_max_drawdown_pct"] = _float_or_none(m.group("m2m_dd")) if m else None

    m = _PORT_INTRA_DD_RE.search(stdout)
    result["worst_intra_bar_dd_pct"] = _float_or_none(m.group("intra_dd")) if m else None

    # Normalise standard keys for DB storage and reports
    result["total_trades"] = result.get("total_portfolio_trades")
    result["win_rate"] = result.get("overall_win_rate")
    result["profit_factor"] = result.get("portfolio_profit_factor")
    result["cumulative_return_pct"] = result.get("portfolio_net_return_pct")
    result["avg_r_multiple"] = result.get("ev_per_trade")
    result["closed_max_drawdown_pct"] = result.get("closed_trade_max_dd_pct")
    result["sharpe"] = result.get("trade_level_sharpe")

    m = _PORT_ANN_RETURN_RE.search(stdout)
    result["annualized_return_pct"] = _float_or_none(m.group("ann_return")) if m else None

    m = _PORT_CALMAR_RE.search(stdout)
    result["calmar_ratio_m2m"] = _float_or_none(m.group("calmar_m2m")) if m else None
    result["calmar_ratio_closed"] = _float_or_none(m.group("calmar_closed")) if m else None

    # --- Capacity utilization ---
    m = _CAP_0_RE.search(stdout)
    result["capacity_0_pct"] = _float_or_none(m.group("pct")) if m else None

    m = _CAP_1_RE.search(stdout)
    result["capacity_1_pct"] = _float_or_none(m.group("pct")) if m else None

    m = _CAP_2_RE.search(stdout)
    result["capacity_2_pct"] = _float_or_none(m.group("pct")) if m else None

    m = _CAP_3_RE.search(stdout)
    result["capacity_3_pct"] = _float_or_none(m.group("pct")) if m else None

    m = _SIGNALS_BLOCKED_CAP_RE.search(stdout)
    result["signals_blocked_cap"] = _int_or_none(m.group("blocked")) if m else None

    m = _SIGNALS_BLOCKED_COOL_RE.search(stdout)
    result["signals_blocked_cooldown"] = _int_or_none(m.group("blocked")) if m else None

    # --- Per-symbol breakdown ---
    per_symbol: List[Dict[str, Any]] = []
    for m in _PER_SYMBOL_ROW_RE.finditer(stdout):
        per_symbol.append({
            "symbol": m.group("symbol"),
            "trades": _int_or_none(m.group("trades")),
            "wins": _int_or_none(m.group("wins")),
            "losses": _int_or_none(m.group("losses")),
            "win_rate": _float_or_none(m.group("win_rate")),
            "profit_factor": _float_or_none(m.group("profit_factor")),
            "net_return_pct": _float_or_none(m.group("net_return")),
            "avg_r_multiple": _float_or_none(m.group("avg_r")),
            "max_dd_pct": _float_or_none(m.group("max_dd")),
            "adj_win_rate": _float_or_none(m.group("adj_wr")),
        })
    result["per_symbol_stats"] = per_symbol if per_symbol else None


def _trade_dict_from_match(m: re.Match, is_portfolio: bool) -> Dict[str, Any]:
    """Build a normalised trade dict from a regex match object."""
    d: Dict[str, Any] = {
        "entry_time": m.group("entry_time").strip(),
        "side": m.group("side").strip(),
        "entry_price": _float_or_none(m.group("entry_price")),
        "exit_price": _float_or_none(m.group("exit_price")),
        "reason": m.group("reason").strip(),
        "net_return_pct": _float_or_none(m.group("net_return")),
        "r_multiple": _float_or_none(m.group("r_multiple")),
        "vol_ratio": _float_or_none(m.group("vol_ratio")),
        "classification": m.group("classification").strip(),
    }
    if is_portfolio:
        d["symbol"] = m.group("symbol").strip()
    # hold_minutes only present in hifi single-asset rows
    hold = m.groupdict().get("hold_minutes")
    if hold is not None:
        d["hold_minutes"] = _int_or_none(hold)
    return d
