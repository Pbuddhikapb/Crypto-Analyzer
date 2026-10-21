"""Engine package containing execution mechanics, exits, and simulation."""

from engine.exits import (
    ExitConfig,
    ExitDecision,
    TradeState,
    calculate_gross_return_pct,
    calculate_r_multiple,
    create_trade_state,
    evaluate_trade_exit,
)
from engine.simulator import (
    CandidateSignal,
    ExecutedTrade,
    SimulationEngine,
    SimulationFriction,
    SimulationResult,
    simulate_portfolio_signals,
    simulate_single_trade_lifecycle,
)

__all__ = [
    "ExitConfig",
    "TradeState",
    "ExitDecision",
    "create_trade_state",
    "calculate_r_multiple",
    "calculate_gross_return_pct",
    "evaluate_trade_exit",
    "SimulationFriction",
    "CandidateSignal",
    "ExecutedTrade",
    "SimulationResult",
    "SimulationEngine",
    "simulate_portfolio_signals",
    "simulate_single_trade_lifecycle",
]
