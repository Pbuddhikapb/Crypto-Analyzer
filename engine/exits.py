"""
The Single Exit Authority.

Pure mathematical function evaluating trade exits across backtests,
simulations, and live execution with zero model drift.
Contains ZERO I/O, ZERO network, ZERO database access.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class ExitConfig:
    """Configuration parameters for trade exit evaluation."""

    tp1_r: float = 0.75  # Target R for partial 50% scale-out
    be_arm_r: float = 0.85  # Excursion required to arm breakeven stop
    tp2_r: float = 1.33  # Target R for full exit of remaining position
    be_offset_pct: float = 0.05  # Buffer above entry (0.05%) to cover fees
    underwater_bars_limit: int = 12  # Bars held before checking kinetic stagnancy
    underwater_r_threshold: float = -0.15  # Max R to trigger stagnancy exit
    use_trailing_stop: bool = False  # Dynamic trailing stop flag
    trailing_activation_r: float = 1.0  # R threshold to activate trailing
    trailing_callback_r: float = 0.3  # Distance behind peak R for trailing stop
    exit_mode: str = "stepped"  # "stepped" (default: stepped scaling TP1/BE/TP2/stagnancy) or "trailing_1r"
    trail_dist_r: float = 1.0  # Distance in R behind extreme since entry for trailing_1r mode


@dataclass
class TradeState:
    """State tracking for an active position."""

    trade_id: str
    symbol: str
    side: str  # 'LONG' or 'SHORT'
    entry_price: float
    initial_sl: float
    current_sl: float
    tp1_price: float
    tp2_price: float
    risk_dist: float
    size_remaining_pct: float = 1.0  # 1.0 -> 0.5 -> 0.0
    tp1_hit: bool = False
    be_armed: bool = False
    bars_held: int = 0
    highest_r: float = 0.0  # Maximum Favorable Excursion (MFE)
    lowest_r: float = 0.0  # Maximum Adverse Excursion (MAE)
    entry_time_ms: int = 0
    trailing_active: bool = False


@dataclass(frozen=True)
class ExitDecision:
    """Result of candle exit evaluation."""

    action: str  # 'HOLD', 'PARTIAL_TP1', 'FULL_EXIT'
    exit_reason: Optional[str]  # e.g., 'STOP_LOSS', 'GAP_STOP_LOSS', 'TAKE_PROFIT_1', 'TAKE_PROFIT_2', 'BREAKEVEN', 'TIME_STOP_UNDERWATER', 'TRAILING_STOP', 'SESSION_BREAK', 'END_OF_DATA'
    exit_price: Optional[float]
    closed_pct: float  # Percentage of original position closed in this decision (0.0, 0.5, 1.0)
    realized_r: float  # Net R-multiple achieved on this exit portion
    gross_return_pct: float  # Return % on this exit portion
    updated_state: TradeState


def create_trade_state(
    trade_id: str,
    symbol: str,
    side: str,
    entry_price: float,
    initial_sl: float,
    config: Optional[ExitConfig] = None,
    entry_time_ms: int = 0,
) -> TradeState:
    """
    Factory function to initialize TradeState with mathematically computed
    TP1, TP2, and risk distance.
    """
    cfg = config or ExitConfig()
    side_upper = side.upper()
    risk_dist = abs(entry_price - initial_sl)

    if risk_dist <= 0.0:
        raise ValueError("Stop loss cannot be identical to entry price (risk_dist must be > 0)")

    if cfg.exit_mode in ("trailing_1r", "atr_trailing"):
        tp1_price = float("inf") if side_upper == "LONG" else -float("inf")
        tp2_price = float("inf") if side_upper == "LONG" else -float("inf")
    elif side_upper == "LONG":
        tp1_price = entry_price + (cfg.tp1_r * risk_dist)
        tp2_price = entry_price + (cfg.tp2_r * risk_dist)
    else:
        tp1_price = entry_price - (cfg.tp1_r * risk_dist)
        tp2_price = entry_price - (cfg.tp2_r * risk_dist)

    return TradeState(
        trade_id=trade_id,
        symbol=symbol.upper(),
        side=side_upper,
        entry_price=entry_price,
        initial_sl=initial_sl,
        current_sl=initial_sl,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        risk_dist=risk_dist,
        size_remaining_pct=1.0,
        tp1_hit=False,
        be_armed=False,
        bars_held=0,
        highest_r=0.0,
        lowest_r=0.0,
        entry_time_ms=entry_time_ms,
        trailing_active=False,
    )


def calculate_bracket_geometry(
    side: str,
    entry_price: float,
    stop_loss: float,
    config: Optional[ExitConfig] = None,
) -> Dict[str, Any]:
    """
    Calculate the exact Single Exit Authority bracket levels for UI visualization
    and execution staging, eliminating any model drift.
    """
    cfg = config or ExitConfig()
    side_upper = side.upper()
    risk_dist = abs(entry_price - stop_loss)
    risk_pct = (risk_dist / entry_price * 100.0) if entry_price > 0 else 0.0

    if cfg.exit_mode in ("trailing_1r", "atr_trailing"):
        return {
            "side": side_upper,
            "limit_entry": round(entry_price, 4),
            "stop_loss": round(stop_loss, 4),
            "risk_dist": round(risk_dist, 4),
            "risk_pct": round(risk_pct, 2),
            "tp1": None,
            "tp1_r": None,
            "be_trigger": None,
            "be_trigger_r": None,
            "be_price": None,
            "tp2": None,
            "tp2_r": None,
            "stagnancy_bars": None,
            "stagnancy_r_threshold": None,
            "rr_ratio": None,
            "exit_mode": cfg.exit_mode,
            "trail_dist_r": cfg.trail_dist_r,
        }

    if side_upper == "LONG":
        tp1_price = entry_price + (cfg.tp1_r * risk_dist)
        be_trigger_price = entry_price + (cfg.be_arm_r * risk_dist)
        be_stop_price = entry_price * (1.0 + cfg.be_offset_pct / 100.0)
        tp2_price = entry_price + (cfg.tp2_r * risk_dist)
    else:
        tp1_price = entry_price - (cfg.tp1_r * risk_dist)
        be_trigger_price = entry_price - (cfg.be_arm_r * risk_dist)
        be_stop_price = entry_price * (1.0 - cfg.be_offset_pct / 100.0)
        tp2_price = entry_price - (cfg.tp2_r * risk_dist)

    return {
        "side": side_upper,
        "limit_entry": round(entry_price, 4),
        "stop_loss": round(stop_loss, 4),
        "risk_dist": round(risk_dist, 4),
        "risk_pct": round(risk_pct, 2),
        "tp1": round(tp1_price, 4),
        "tp1_r": cfg.tp1_r,
        "be_trigger": round(be_trigger_price, 4),
        "be_trigger_r": cfg.be_arm_r,
        "be_price": round(be_stop_price, 4),
        "tp2": round(tp2_price, 4),
        "tp2_r": cfg.tp2_r,
        "stagnancy_bars": cfg.underwater_bars_limit,
        "stagnancy_r_threshold": cfg.underwater_r_threshold,
        "rr_ratio": cfg.tp2_r,
    }


def calculate_r_multiple(
    side: str,
    entry_price: float,
    exit_price: float,
    risk_dist: float,
) -> float:
    """Compute R-multiple realized between entry and exit price."""
    if risk_dist <= 0.0:
        return 0.0
    if side == "LONG":
        return round((exit_price - entry_price) / risk_dist, 6)
    else:
        return round((entry_price - exit_price) / risk_dist, 6)


def calculate_gross_return_pct(
    side: str,
    entry_price: float,
    exit_price: float,
) -> float:
    """Compute gross return percentage between entry and exit price."""
    if entry_price <= 0.0:
        return 0.0
    if side == "LONG":
        return round(((exit_price - entry_price) / entry_price) * 100.0, 6)
    else:
        return round(((entry_price - exit_price) / entry_price) * 100.0, 6)


def evaluate_trade_exit(
    state: TradeState,
    candle: Tuple[float, float, float, float],  # (open, high, low, close)
    config: Optional[ExitConfig] = None,
) -> ExitDecision:
    """
    The Single Exit Authority evaluation function.

    Takes current TradeState and the latest Candle (open, high, low, close),
    evaluates all exit rules under strict precedence, and returns an ExitDecision.

    Precedence order:
    1. Stop Loss / Breakeven hit (adverse gap protection, conservative tie-breaking).
    2. TP1 Partial Scale-out (if not yet hit).
    3. Breakeven Arming & Trailing Stop Updates.
    4. TP2 Target Exit (if TP1 was already hit).
    5. Underwater Kinetic Stagnancy Time-Stop.
    """
    cfg = config or ExitConfig()
    c_open, c_high, c_low, c_close = candle
    side = state.side
    risk_dist = state.risk_dist
    entry = state.entry_price

    state.bars_held += 1

    # Update MFE and MAE
    if side == "LONG":
        bar_high_r = (c_high - entry) / risk_dist
        bar_low_r = (c_low - entry) / risk_dist
        bar_close_r = (c_close - entry) / risk_dist
    else:
        bar_high_r = (entry - c_low) / risk_dist
        bar_low_r = (entry - c_high) / risk_dist
        bar_close_r = (entry - c_close) / risk_dist

    if bar_high_r > state.highest_r:
        state.highest_r = bar_high_r
    if bar_low_r < state.lowest_r:
        state.lowest_r = bar_low_r

    # Specialized Exit Mode: Trailing 1R (No Take-Profit, No Breakeven, No Stagnancy)
    if cfg.exit_mode in ("trailing_1r", "atr_trailing"):
        sl_at_start = state.current_sl
        is_sl_hit = False
        exit_price = 0.0
        exit_reason = ""

        if side == "LONG":
            if c_low <= sl_at_start:
                is_sl_hit = True
                if c_open <= sl_at_start:
                    exit_price = c_open
                    exit_reason = "GAP_STOP_LOSS"
                else:
                    exit_price = sl_at_start
                    exit_reason = "TRAILING_STOP" if (state.trailing_active and state.current_sl != state.initial_sl) else "STOP_LOSS"
        else:  # SHORT
            if c_high >= sl_at_start:
                is_sl_hit = True
                if c_open >= sl_at_start:
                    exit_price = c_open
                    exit_reason = "GAP_STOP_LOSS"
                else:
                    exit_price = sl_at_start
                    exit_reason = "TRAILING_STOP" if (state.trailing_active and state.current_sl != state.initial_sl) else "STOP_LOSS"

        if is_sl_hit:
            closed_pct = state.size_remaining_pct
            state.size_remaining_pct = 0.0
            r_mult = calculate_r_multiple(side, entry, exit_price, risk_dist)
            ret_pct = calculate_gross_return_pct(side, entry, exit_price)
            return ExitDecision(
                action="FULL_EXIT",
                exit_reason=exit_reason,
                exit_price=exit_price,
                closed_pct=closed_pct,
                realized_r=r_mult,
                gross_return_pct=ret_pct,
                updated_state=state,
            )

        # Update dynamic trailing stop: 1R behind extreme since entry, moving only in profit direction
        if side == "LONG":
            new_sl = entry + (state.highest_r - cfg.trail_dist_r) * risk_dist
            if new_sl > state.current_sl:
                state.current_sl = new_sl
                state.trailing_active = True
            if c_close <= state.current_sl:
                closed_pct = state.size_remaining_pct
                state.size_remaining_pct = 0.0
                r_mult = calculate_r_multiple(side, entry, state.current_sl, risk_dist)
                ret_pct = calculate_gross_return_pct(side, entry, state.current_sl)
                return ExitDecision(
                    action="FULL_EXIT",
                    exit_reason="TRAILING_STOP",
                    exit_price=state.current_sl,
                    closed_pct=closed_pct,
                    realized_r=r_mult,
                    gross_return_pct=ret_pct,
                    updated_state=state,
                )
        else:  # SHORT
            new_sl = entry - (state.highest_r - cfg.trail_dist_r) * risk_dist
            if new_sl < state.current_sl:
                state.current_sl = new_sl
                state.trailing_active = True
            if c_close >= state.current_sl:
                closed_pct = state.size_remaining_pct
                state.size_remaining_pct = 0.0
                r_mult = calculate_r_multiple(side, entry, state.current_sl, risk_dist)
                ret_pct = calculate_gross_return_pct(side, entry, state.current_sl)
                return ExitDecision(
                    action="FULL_EXIT",
                    exit_reason="TRAILING_STOP",
                    exit_price=state.current_sl,
                    closed_pct=closed_pct,
                    realized_r=r_mult,
                    gross_return_pct=ret_pct,
                    updated_state=state,
                )

        return ExitDecision(
            action="HOLD",
            exit_reason=None,
            exit_price=None,
            closed_pct=0.0,
            realized_r=0.0,
            gross_return_pct=0.0,
            updated_state=state,
        )

    # Rule 1: Check Stop Loss Hit
    sl_at_start = state.current_sl
    is_sl_hit = False
    exit_price = 0.0
    exit_reason = ""

    if side == "LONG":
        if c_low <= sl_at_start:
            is_sl_hit = True
            # Adverse gap fill: if candle opened below stop loss, fill at open!
            if c_open <= sl_at_start:
                exit_price = c_open
                exit_reason = "GAP_STOP_LOSS" if not state.be_armed else "BREAKEVEN"
            else:
                exit_price = sl_at_start
                if state.trailing_active and state.current_sl != state.initial_sl:
                    exit_reason = "TRAILING_STOP"
                elif state.be_armed:
                    exit_reason = "BREAKEVEN"
                elif state.tp1_hit:
                    exit_reason = "PARTIAL_LOSS"
                else:
                    exit_reason = "STOP_LOSS"
    else:  # SHORT
        if c_high >= sl_at_start:
            is_sl_hit = True
            # Adverse gap fill: if candle opened above stop loss, fill at open!
            if c_open >= sl_at_start:
                exit_price = c_open
                exit_reason = "GAP_STOP_LOSS" if not state.be_armed else "BREAKEVEN"
            else:
                exit_price = sl_at_start
                if state.trailing_active and state.current_sl != state.initial_sl:
                    exit_reason = "TRAILING_STOP"
                elif state.be_armed:
                    exit_reason = "BREAKEVEN"
                elif state.tp1_hit:
                    exit_reason = "PARTIAL_LOSS"
                else:
                    exit_reason = "STOP_LOSS"

    if is_sl_hit:
        closed_pct = state.size_remaining_pct
        state.size_remaining_pct = 0.0
        r_mult = calculate_r_multiple(side, entry, exit_price, risk_dist)
        ret_pct = calculate_gross_return_pct(side, entry, exit_price)
        return ExitDecision(
            action="FULL_EXIT",
            exit_reason=exit_reason,
            exit_price=exit_price,
            closed_pct=closed_pct,
            realized_r=r_mult,
            gross_return_pct=ret_pct,
            updated_state=state,
        )

    # Rule 2: Check TP1 Partial Scale-Out (50%) or Same-Candle Multi-Target Hit
    if not state.tp1_hit:
        tp1_touched = (c_high >= state.tp1_price) if side == "LONG" else (c_low <= state.tp1_price)
        if tp1_touched:
            # Check if impulse candle traversed through BOTH TP1 and TP2
            tp2_touched = (c_high >= state.tp2_price) if side == "LONG" else (c_low <= state.tp2_price)
            if tp2_touched:
                state.tp1_hit = True
                closed_pct = state.size_remaining_pct
                state.size_remaining_pct = 0.0
                exit_price = (0.5 * state.tp1_price) + (0.5 * state.tp2_price)
                r_mult = calculate_r_multiple(side, entry, exit_price, risk_dist)
                ret_pct = calculate_gross_return_pct(side, entry, exit_price)
                return ExitDecision(
                    action="FULL_EXIT",
                    exit_reason="TAKE_PROFIT_2",
                    exit_price=exit_price,
                    closed_pct=closed_pct,
                    realized_r=r_mult,
                    gross_return_pct=ret_pct,
                    updated_state=state,
                )

            state.tp1_hit = True
            state.size_remaining_pct = 0.5
            exit_price = state.tp1_price
            r_mult = calculate_r_multiple(side, entry, exit_price, risk_dist)
            ret_pct = calculate_gross_return_pct(side, entry, exit_price)

            # Check if breakeven also arms immediately upon TP1 touch
            if state.highest_r >= cfg.be_arm_r:
                state.be_armed = True
                be_sl = entry * (1.0 + cfg.be_offset_pct / 100.0) if side == "LONG" else entry * (1.0 - cfg.be_offset_pct / 100.0)
                state.current_sl = be_sl

            return ExitDecision(
                action="PARTIAL_TP1",
                exit_reason="TAKE_PROFIT_1",
                exit_price=exit_price,
                closed_pct=0.5,
                realized_r=r_mult,
                gross_return_pct=ret_pct,
                updated_state=state,
            )

    # Rule 3: Breakeven Arming (if TP1 was previously hit and excursion threshold crossed)
    if state.tp1_hit and not state.be_armed:
        if state.highest_r >= cfg.be_arm_r:
            state.be_armed = True
            be_sl = entry * (1.0 + cfg.be_offset_pct / 100.0) if side == "LONG" else entry * (1.0 - cfg.be_offset_pct / 100.0)
            state.current_sl = be_sl
            # Check if candle closed back through new breakeven level
            if (side == "LONG" and c_close <= be_sl) or (side == "SHORT" and c_close >= be_sl):
                closed_pct = state.size_remaining_pct
                state.size_remaining_pct = 0.0
                r_mult = calculate_r_multiple(side, entry, be_sl, risk_dist)
                ret_pct = calculate_gross_return_pct(side, entry, be_sl)
                return ExitDecision(
                    action="FULL_EXIT",
                    exit_reason="BREAKEVEN",
                    exit_price=be_sl,
                    closed_pct=closed_pct,
                    realized_r=r_mult,
                    gross_return_pct=ret_pct,
                    updated_state=state,
                )

    # Rule 4: Dynamic Trailing Stop
    if cfg.use_trailing_stop:
        if state.highest_r >= cfg.trailing_activation_r:
            state.trailing_active = True
            trail_dist = (state.highest_r - cfg.trailing_callback_r) * risk_dist
            new_sl = (entry + trail_dist) if side == "LONG" else (entry - trail_dist)
            if side == "LONG" and new_sl > state.current_sl:
                state.current_sl = new_sl
            elif side == "SHORT" and new_sl < state.current_sl:
                state.current_sl = new_sl

    # Rule 5: Check TP2 Target (full remaining exit)
    if state.tp1_hit:
        tp2_touched = (c_high >= state.tp2_price) if side == "LONG" else (c_low <= state.tp2_price)
        if tp2_touched:
            closed_pct = state.size_remaining_pct
            state.size_remaining_pct = 0.0
            exit_price = state.tp2_price
            r_mult = calculate_r_multiple(side, entry, exit_price, risk_dist)
            ret_pct = calculate_gross_return_pct(side, entry, exit_price)
            return ExitDecision(
                action="FULL_EXIT",
                exit_reason="TAKE_PROFIT_2",
                exit_price=exit_price,
                closed_pct=closed_pct,
                realized_r=r_mult,
                gross_return_pct=ret_pct,
                updated_state=state,
            )

    # Rule 6: Kinetic Stagnancy Underwater Time-Stop
    if not state.tp1_hit and state.bars_held >= cfg.underwater_bars_limit:
        if bar_close_r <= cfg.underwater_r_threshold:
            closed_pct = state.size_remaining_pct
            state.size_remaining_pct = 0.0
            exit_price = c_close
            r_mult = calculate_r_multiple(side, entry, exit_price, risk_dist)
            ret_pct = calculate_gross_return_pct(side, entry, exit_price)
            return ExitDecision(
                action="FULL_EXIT",
                exit_reason="TIME_STOP_UNDERWATER",
                exit_price=exit_price,
                closed_pct=closed_pct,
                realized_r=r_mult,
                gross_return_pct=ret_pct,
                updated_state=state,
            )

    # No exit triggered on this bar
    return ExitDecision(
        action="HOLD",
        exit_reason=None,
        exit_price=None,
        closed_pct=0.0,
        realized_r=0.0,
        gross_return_pct=0.0,
        updated_state=state,
    )
