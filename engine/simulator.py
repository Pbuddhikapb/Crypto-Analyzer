"""
Institutional-Grade Simulation Engine.

Simulates trading execution with realistic market frictions:
- Taker and maker fee models
- Realistic adverse execution slippage on market entries and stop exits
- Conservative gap-down / gap-up fill calculations
- Cumulative 8-hour funding rate drag
- Portfolio concurrency constraints and symbol cooldowns
- Full performance reporting (WR, PF, EV, MaxDD, Sharpe, Sortino).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from engine.exits import (
    ExitConfig,
    ExitDecision,
    TradeState,
    calculate_gross_return_pct,
    calculate_r_multiple,
    create_trade_state,
    evaluate_trade_exit,
)


@dataclass(frozen=True)
class SimulationFriction:
    """Institutional execution frictions."""

    taker_fee_pct: float = 0.05  # 0.05% taker fee per side (Binance USD-M VIP0)
    maker_fee_pct: float = 0.02  # 0.02% maker fee per side
    slippage_pct: float = 0.03  # 0.03% adverse execution slippage on market/stop fills
    funding_rate_8h_pct: float = 0.01  # 0.01% conservative funding drag per 8 hours


@dataclass
class CandidateSignal:
    """Incoming trade signal candidate."""

    symbol: str
    side: str  # 'LONG' or 'SHORT'
    entry_price: float
    stop_loss: float
    timestamp_ms: int
    score: float = 100.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutedTrade:
    """Audit record of a fully completed trade."""

    trade_id: str
    symbol: str
    side: str
    entry_time_ms: int
    exit_time_ms: int
    entry_price: float
    entry_fill_price: float
    exit_price: float
    exit_fill_price: float
    exit_reason: str
    position_size_usd: float
    gross_pnl_usd: float
    fees_usd: float
    slippage_usd: float
    funding_cost_usd: float
    net_pnl_usd: float
    net_return_pct: float
    realized_r: float
    bars_held: int
    mfe_r: float
    mae_r: float
    initial_risk_usd: float = 0.0
    net_r: float = 0.0


@dataclass
class SimulationResult:
    """Comprehensive performance analytics from simulation run."""

    initial_equity_usd: float
    final_equity_usd: float
    net_profit_usd: float
    net_profit_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    gross_profit_usd: float
    gross_loss_usd: float
    profit_factor: float
    expectancy_usd: float
    expectancy_r: float
    avg_r_multiple: float
    max_drawdown_pct: float
    max_drawdown_duration_bars: int
    sharpe_ratio: float
    sortino_ratio: float
    total_fees_usd: float
    total_slippage_usd: float
    total_funding_cost_usd: float
    trades: List[ExecutedTrade] = field(default_factory=list)
    equity_curve: List[Tuple[int, float]] = field(default_factory=list)
    ruined: bool = False
    ruin_time_ms: Optional[int] = None
    sizing_mode: str = "fixed_risk"
    gross_profit_factor: float = 0.0
    gross_expectancy_r: float = 0.0
    cumulative_r: float = 0.0
    max_drawdown_r: float = 0.0
    days_evaluated: float = 0.0
    pct_of_dataset_evaluated: float = 100.0


class SimulationEngine:
    """
    Event-driven bar-by-bar simulation engine enforcing strict friction models
    and portfolio constraints.
    """

    def __init__(
        self,
        initial_equity_usd: float = 10000.0,
        risk_per_trade_pct: float = 1.0,
        max_open_positions: int = 3,
        max_directional_positions: int = 2,
        symbol_cooldown_bars: int = 3,
        max_leverage: float = 3.0,
        friction: Optional[SimulationFriction] = None,
        exit_config: Optional[ExitConfig] = None,
        ruin_threshold_usd: Optional[float] = None,
        sizing_mode: str = "fixed_risk",
    ):
        self.initial_equity_usd = float(initial_equity_usd)
        self.equity_usd = float(initial_equity_usd)
        self.risk_per_trade_pct = float(risk_per_trade_pct)
        self.max_open_positions = max_open_positions
        self.max_directional_positions = max_directional_positions
        self.symbol_cooldown_bars = symbol_cooldown_bars
        self.max_leverage = float(max_leverage)
        self.friction = friction or SimulationFriction()
        self.exit_config = exit_config or ExitConfig()
        self.ruin_threshold_usd = (
            float(ruin_threshold_usd)
            if ruin_threshold_usd is not None
            else (0.05 * self.initial_equity_usd)
        )
        self.sizing_mode = sizing_mode
        self.ruined = False
        self.ruin_time_ms: Optional[int] = None

        # Active state
        # trade_id -> dict of trade bookkeeping
        self.active_positions: Dict[str, Dict[str, Any]] = {}
        # symbol -> bar_index when last closed
        self.cooldown_tracker: Dict[str, int] = {}
        # Completed trades
        self.completed_trades: List[ExecutedTrade] = []
        # Equity curve: (timestamp_ms, equity)
        self.equity_curve: List[Tuple[int, float]] = []

    def can_open_position(self, symbol: str, side: str, current_bar_idx: int) -> Tuple[bool, str]:
        """Verify whether portfolio constraints allow opening a new position."""
        # 0. Ruin guard check: reject new positions only in compounding mode
        if self.sizing_mode == "compounding":
            if self.ruined or self.equity_usd <= self.ruin_threshold_usd:
                if not self.ruined:
                    self.ruined = True
                return False, f"Account ruined (equity {self.equity_usd:.2f} <= ruin threshold {self.ruin_threshold_usd:.2f})"
        else:
            # In fixed_risk mode: ruin is report-only, never stops entry
            if self.equity_usd <= self.ruin_threshold_usd and not self.ruined:
                self.ruined = True

        sym = symbol.upper()
        side_upper = side.upper()

        # 1. Total open positions limit
        if len(self.active_positions) >= self.max_open_positions:
            return False, f"Max open positions reached ({self.max_open_positions})"

        # 2. Duplicate symbol check
        for pos in self.active_positions.values():
            if pos["state"].symbol == sym:
                return False, f"Position already open for {sym}"

        # 3. Directional concurrency limit
        directional_count = sum(1 for pos in self.active_positions.values() if pos["state"].side == side_upper)
        if directional_count >= self.max_directional_positions:
            return False, f"Max {side_upper} positions reached ({self.max_directional_positions})"

        # 4. Symbol cooldown check
        last_closed_bar = self.cooldown_tracker.get(sym, -999)
        if (current_bar_idx - last_closed_bar) < self.symbol_cooldown_bars:
            return False, f"Symbol {sym} is in cooldown ({current_bar_idx - last_closed_bar}/{self.symbol_cooldown_bars} bars)"

        return True, "OK"

    def open_position(self, signal: CandidateSignal, current_bar_idx: int) -> Optional[str]:
        """
        Execute market entry with slippage and fee accounting.
        Returns trade_id if filled, None if rejected.
        """
        if self.sizing_mode != "compounding" and self.equity_usd <= self.ruin_threshold_usd:
            if not self.ruined:
                self.ruined = True
            if self.ruin_time_ms is None:
                self.ruin_time_ms = signal.timestamp_ms

        can_open, reason = self.can_open_position(signal.symbol, signal.side, current_bar_idx)
        if not can_open:
            if self.ruined and self.ruin_time_ms is None:
                self.ruin_time_ms = signal.timestamp_ms
            return None

        # Position sizing calculation
        sizing_equity = self.initial_equity_usd if self.sizing_mode == "fixed_risk" else self.equity_usd
        risk_usd = sizing_equity * (self.risk_per_trade_pct / 100.0)
        risk_dist = abs(signal.entry_price - signal.stop_loss)
        if risk_dist <= 0.0:
            return None

        shares = risk_usd / risk_dist
        notional_usd = shares * signal.entry_price

        # Cap notional at max leverage
        max_notional = sizing_equity * self.max_leverage
        if notional_usd > max_notional:
            notional_usd = max_notional
            shares = notional_usd / signal.entry_price

        # Apply adverse entry slippage
        # Long entry slips higher; Short entry slips lower
        slip_rate = self.friction.slippage_pct / 100.0
        if signal.side.upper() == "LONG":
            fill_price = signal.entry_price * (1.0 + slip_rate)
        else:
            fill_price = signal.entry_price * (1.0 - slip_rate)

        entry_fee_usd = notional_usd * (self.friction.taker_fee_pct / 100.0)
        slippage_cost_usd = notional_usd * slip_rate

        trade_id = f"{signal.symbol}_{signal.timestamp_ms}_{len(self.completed_trades) + len(self.active_positions)}"
        state = create_trade_state(
            trade_id=trade_id,
            symbol=signal.symbol,
            side=signal.side,
            entry_price=fill_price,  # Base state on true executed fill price
            initial_sl=signal.stop_loss,
            config=self.exit_config,
            entry_time_ms=signal.timestamp_ms,
        )

        self.active_positions[trade_id] = {
            "state": state,
            "nominal_entry_price": signal.entry_price,
            "entry_fill_price": fill_price,
            "notional_usd": notional_usd,
            "shares": shares,
            "entry_time_ms": signal.timestamp_ms,
            "entry_fee_usd": entry_fee_usd,
            "slippage_usd": slippage_cost_usd,
            "funding_cost_usd": 0.0,
            "last_funding_period": signal.timestamp_ms // (8 * 3600 * 1000),
            "tp1_realized_usd": 0.0,
            "tp1_fees_usd": 0.0,
            "tp1_r": 0.0,
            "risk_usd": risk_usd,
        }

        # Deduct entry fee immediately from cash equity
        self.equity_usd -= entry_fee_usd
        return trade_id

    def process_bar(
        self,
        symbol: str,
        candle: Tuple[float, float, float, float],  # (open, high, low, close)
        timestamp_ms: int,
        current_bar_idx: int,
    ) -> List[ExecutedTrade]:
        """
        Process a candle update for a specific symbol across open positions.
        Evaluates exits, funding rate accrual, and returns any closed trades.
        """
        closed_trades: List[ExecutedTrade] = []
        trades_to_remove: List[str] = []

        for trade_id, pos in self.active_positions.items():
            state = pos["state"]
            if state.symbol != symbol.upper():
                continue

            # 1. 8-hour funding rate check
            current_funding_period = timestamp_ms // (8 * 3600 * 1000)
            if current_funding_period > pos["last_funding_period"]:
                periods = current_funding_period - pos["last_funding_period"]
                funding_drag = pos["notional_usd"] * state.size_remaining_pct * (self.friction.funding_rate_8h_pct / 100.0) * periods
                pos["funding_cost_usd"] += funding_drag
                self.equity_usd -= funding_drag
                pos["last_funding_period"] = current_funding_period

            # 2. Evaluate exit authority pure logic
            decision = evaluate_trade_exit(state, candle, self.exit_config)

            if decision.action == "PARTIAL_TP1":
                # 50% scale-out at TP1
                tp1_notional = pos["notional_usd"] * 0.5
                # Maker fee for TP limit fill
                tp1_fee = tp1_notional * (self.friction.maker_fee_pct / 100.0)
                gross_tp1_pnl = tp1_notional * (decision.gross_return_pct / 100.0)
                pos["tp1_realized_usd"] = gross_tp1_pnl
                pos["tp1_fees_usd"] = tp1_fee
                pos["tp1_r"] = decision.realized_r

                self.equity_usd += (gross_tp1_pnl - tp1_fee)

            elif decision.action == "FULL_EXIT":
                exit_price = decision.exit_price or candle[3]
                remaining_notional = pos["notional_usd"] * decision.closed_pct

                # Calculate exit fill with slippage and fees based on exit reason
                # All full exits other than TP2 limit order are adverse market stops
                is_stop_type = decision.exit_reason != "TAKE_PROFIT_2"
                if is_stop_type:
                    # Adverse slippage on market stop
                    slip_rate = self.friction.slippage_pct / 100.0
                    if state.side == "LONG":
                        exit_fill = exit_price * (1.0 - slip_rate)
                    else:
                        exit_fill = exit_price * (1.0 + slip_rate)
                    exit_fee = remaining_notional * (self.friction.taker_fee_pct / 100.0)
                    slip_cost = remaining_notional * slip_rate
                else:
                    # TP2 limit order fill (maker fee, zero slippage)
                    exit_fill = exit_price
                    exit_fee = remaining_notional * (self.friction.maker_fee_pct / 100.0)
                    slip_cost = 0.0

                pos["slippage_usd"] += slip_cost
                exit_gross_return_pct = calculate_gross_return_pct(state.side, pos["entry_fill_price"], exit_fill)
                remaining_gross_pnl = remaining_notional * (exit_gross_return_pct / 100.0)

                total_gross_pnl = pos["tp1_realized_usd"] + remaining_gross_pnl
                total_fees = pos["entry_fee_usd"] + pos["tp1_fees_usd"] + exit_fee
                total_slippage = pos["slippage_usd"]
                total_funding = pos["funding_cost_usd"]

                # Gross PnL already incorporates entry & exit slippage via fill prices.
                # Slippage is tracked in metadata/reporting, not double-deducted here.
                net_pnl = total_gross_pnl - total_fees - total_funding
                net_return_pct = (net_pnl / pos["notional_usd"]) * 100.0

                # Compute combined realized R
                if pos["tp1_realized_usd"] != 0.0:
                    exit_r = calculate_r_multiple(state.side, pos["entry_fill_price"], exit_fill, state.risk_dist)
                    combined_r = (0.5 * pos["tp1_r"]) + (0.5 * exit_r)
                else:
                    combined_r = calculate_r_multiple(state.side, pos["entry_fill_price"], exit_fill, state.risk_dist)

                # Update remaining cash equity (gross pnl already incorporates exit fill slippage)
                self.equity_usd += (remaining_gross_pnl - exit_fee)

                # Initial risk and net R
                init_risk = pos.get("risk_usd", pos["notional_usd"] * 0.01)
                net_r = (net_pnl / init_risk) if init_risk > 0 else 0.0

                executed = ExecutedTrade(
                    trade_id=trade_id,
                    symbol=state.symbol,
                    side=state.side,
                    entry_time_ms=pos["entry_time_ms"],
                    exit_time_ms=timestamp_ms,
                    entry_price=pos["nominal_entry_price"],
                    entry_fill_price=pos["entry_fill_price"],
                    exit_price=exit_price,
                    exit_fill_price=exit_fill,
                    exit_reason=decision.exit_reason or "UNKNOWN",
                    position_size_usd=pos["notional_usd"],
                    gross_pnl_usd=round(total_gross_pnl, 2),
                    fees_usd=round(total_fees, 2),
                    slippage_usd=round(total_slippage, 2),
                    funding_cost_usd=round(total_funding, 2),
                    net_pnl_usd=round(net_pnl, 2),
                    net_return_pct=round(net_return_pct, 4),
                    realized_r=round(combined_r, 4),
                    bars_held=state.bars_held,
                    mfe_r=round(state.highest_r, 4),
                    mae_r=round(state.lowest_r, 4),
                    initial_risk_usd=round(init_risk, 2),
                    net_r=round(net_r, 4),
                )

                closed_trades.append(executed)
                self.completed_trades.append(executed)
                trades_to_remove.append(trade_id)
                self.cooldown_tracker[state.symbol] = current_bar_idx

        for tid in trades_to_remove:
            del self.active_positions[tid]

        # Record equity curve point (update existing point if same timestamp to avoid multi-symbol inflation)
        if self.equity_curve and self.equity_curve[-1][0] == timestamp_ms:
            self.equity_curve[-1] = (timestamp_ms, round(self.equity_usd, 2))
        else:
            self.equity_curve.append((timestamp_ms, round(self.equity_usd, 2)))
        return closed_trades

    def compute_summary(self) -> SimulationResult:
        """Calculate complete statistical metrics over simulation history."""
        trades = self.completed_trades
        total_trades = len(trades)

        if total_trades == 0:
            return SimulationResult(
                initial_equity_usd=self.initial_equity_usd,
                final_equity_usd=self.equity_usd,
                net_profit_usd=0.0,
                net_profit_pct=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate_pct=0.0,
                gross_profit_usd=0.0,
                gross_loss_usd=0.0,
                profit_factor=0.0,
                gross_profit_factor=0.0,
                expectancy_usd=0.0,
                expectancy_r=0.0,
                gross_expectancy_r=0.0,
                avg_r_multiple=0.0,
                max_drawdown_pct=0.0,
                max_drawdown_duration_bars=0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                total_fees_usd=0.0,
                total_slippage_usd=0.0,
                total_funding_cost_usd=0.0,
                trades=[],
                equity_curve=self.equity_curve,
                ruined=self.ruined,
                ruin_time_ms=self.ruin_time_ms,
                sizing_mode=self.sizing_mode,
                cumulative_r=0.0,
                max_drawdown_r=0.0,
                days_evaluated=0.0,
                pct_of_dataset_evaluated=100.0,
            )

        wins = [t for t in trades if t.net_pnl_usd > 0]
        losses = [t for t in trades if t.net_pnl_usd <= 0]
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_trades) * 100.0

        # Net Profit Factor: sum(net > 0) / abs(sum(net <= 0))
        net_profit = sum(t.net_pnl_usd for t in wins)
        net_loss = abs(sum(t.net_pnl_usd for t in losses))
        profit_factor = (net_profit / net_loss) if net_loss > 0 else (999.0 if net_profit > 0 else 0.0)

        # Gross Profit Factor (diagnostics)
        gross_profit = sum(t.gross_pnl_usd for t in trades if t.gross_pnl_usd > 0)
        gross_loss = abs(sum(t.gross_pnl_usd for t in trades if t.gross_pnl_usd <= 0))
        gross_profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

        net_profit_usd = self.equity_usd - self.initial_equity_usd
        net_profit_pct = (net_profit_usd / self.initial_equity_usd) * 100.0

        avg_win_usd = (sum(t.net_pnl_usd for t in wins) / win_count) if win_count > 0 else 0.0
        avg_loss_usd = (abs(sum(t.net_pnl_usd for t in losses)) / loss_count) if loss_count > 0 else 0.0
        wr_frac = win_rate / 100.0
        lr_frac = 1.0 - wr_frac
        expectancy_usd = (wr_frac * avg_win_usd) - (lr_frac * avg_loss_usd)

        # Net expectancy R from net_r
        net_r_values = [t.net_r for t in trades]
        avg_r = float(np.mean(net_r_values)) if net_r_values else 0.0
        win_net_r_mean = float(np.mean([t.net_r for t in wins])) if wins else 0.0
        loss_net_r_mean = float(np.mean([abs(t.net_r) for t in losses])) if losses else 0.0
        expectancy_r = (wr_frac * win_net_r_mean) - (lr_frac * loss_net_r_mean)

        # Gross expectancy R from realized_r
        gross_r_values = [t.realized_r for t in trades]
        gross_win_r_mean = float(np.mean([t.realized_r for t in wins])) if wins else 0.0
        gross_loss_r_mean = float(np.mean([abs(t.realized_r) for t in losses])) if losses else 0.0
        gross_expectancy_r = (wr_frac * gross_win_r_mean) - (lr_frac * gross_loss_r_mean)

        # Cumulative net R and peak-to-trough max drawdown on net R curve
        cum_r = 0.0
        peak_r = 0.0
        max_dd_r = 0.0
        for t in trades:
            cum_r += t.net_r
            if cum_r > peak_r:
                peak_r = cum_r
            else:
                dd_r = peak_r - cum_r
                if dd_r > max_dd_r:
                    max_dd_r = dd_r

        # Dataset days evaluated
        if self.equity_curve:
            start_ts = self.equity_curve[0][0]
            end_ts = self.equity_curve[-1][0]
            total_days = max(1.0, (end_ts - start_ts) / (86400 * 1000.0))
            if self.sizing_mode == "compounding" and self.ruined and self.ruin_time_ms:
                days_eval = max(1.0, (self.ruin_time_ms - start_ts) / (86400 * 1000.0))
            else:
                days_eval = total_days
            pct_eval = min(100.0, round((days_eval / total_days) * 100.0, 1))
        else:
            days_eval = 0.0
            pct_eval = 100.0

        total_fees = sum(t.fees_usd for t in trades)
        total_slippage = sum(t.slippage_usd for t in trades)
        total_funding = sum(t.funding_cost_usd for t in trades)

        # Max Drawdown & Duration Calculation
        equities = [eq for _, eq in self.equity_curve]
        if not equities:
            equities = [self.initial_equity_usd, self.equity_usd]

        peak = equities[0]
        max_dd_pct = 0.0
        current_dd_bars = 0
        max_dd_duration = 0

        for eq in equities:
            if eq > peak:
                peak = eq
                current_dd_bars = 0
            else:
                dd_pct = ((peak - eq) / peak) * 100.0
                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct
                current_dd_bars += 1
                if current_dd_bars > max_dd_duration:
                    max_dd_duration = current_dd_bars

        # Sharpe and Sortino Ratios (from trade net returns)
        returns = np.array([t.net_return_pct for t in trades])
        mean_ret = np.mean(returns)
        std_ret = np.std(returns)

        # Annualized scaling factor (assumes ~500 trades per year or root 252)
        annualize_factor = np.sqrt(252.0)
        sharpe = float((mean_ret / std_ret) * annualize_factor) if std_ret > 1e-8 else 0.0

        # Downside semi-deviation with target return T = 0 (Sortino & van der Meer, 1991)
        downside_diff = np.minimum(0.0, returns)
        downside_dev = float(np.sqrt(np.mean(downside_diff**2)))
        sortino = float((mean_ret / downside_dev) * annualize_factor) if downside_dev > 1e-8 else 0.0

        return SimulationResult(
            initial_equity_usd=round(self.initial_equity_usd, 2),
            final_equity_usd=round(self.equity_usd, 2),
            net_profit_usd=round(net_profit_usd, 2),
            net_profit_pct=round(net_profit_pct, 2),
            total_trades=total_trades,
            winning_trades=win_count,
            losing_trades=loss_count,
            win_rate_pct=round(win_rate, 2),
            gross_profit_usd=round(gross_profit, 2),
            gross_loss_usd=round(gross_loss, 2),
            profit_factor=round(profit_factor, 2),
            gross_profit_factor=round(gross_profit_factor, 2),
            expectancy_usd=round(expectancy_usd, 2),
            expectancy_r=round(expectancy_r, 4),
            gross_expectancy_r=round(gross_expectancy_r, 4),
            avg_r_multiple=round(avg_r, 4),
            max_drawdown_pct=round(max_dd_pct, 2),
            max_drawdown_duration_bars=max_dd_duration,
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=round(sortino, 2),
            total_fees_usd=round(total_fees, 2),
            total_slippage_usd=round(total_slippage, 2),
            total_funding_cost_usd=round(total_funding, 2),
            trades=trades,
            equity_curve=self.equity_curve,
            ruined=self.ruined,
            ruin_time_ms=self.ruin_time_ms,
            sizing_mode=self.sizing_mode,
            cumulative_r=round(cum_r, 4),
            max_drawdown_r=round(max_dd_r, 4),
            days_evaluated=round(days_eval, 1),
            pct_of_dataset_evaluated=pct_eval,
        )
