"""
Base Strategy Interface for Phase 3 Evidence Validation.

Defines the contract for candidate signal generators with strict enforcement
of zero lookahead bias: signals triggered on bar i close are timestamped
for execution at bar i+1 open.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List

import pandas as pd

from domain.signals import CandidateSignal


@dataclass
class StrategyConfig:
    """Configuration for a strategy hypothesis."""

    name: str
    description: str
    timeframe_signal: str = "1h"
    timeframe_htf: str = "4h"
    parameters: Dict[str, Any] = field(default_factory=dict)


class BaseStrategy(ABC):
    """Abstract base class for all trading hypothesis generators."""

    def __init__(self, config: StrategyConfig):
        self.config = config

    @abstractmethod
    def generate_signals(
        self,
        symbol: str,
        df_1h: pd.DataFrame,
        df_4h: pd.DataFrame,
    ) -> List[CandidateSignal]:
        """
        Scan historical candle datasets and generate candidate signals.

        Args:
            symbol: Symbol string (e.g., 'BTCUSDT')
            df_1h: DataFrame of 1-hour candles [open_time, open, high, low, close, volume]
            df_4h: DataFrame of 4-hour candles [open_time, open, high, low, close, volume]

        Returns:
            List of CandidateSignal objects sorted chronologically.
            Every signal must be timestamped with candle i+1's open_time and
            priced at candle i+1's open to guarantee zero lookahead bias.
        """
        pass
