"""
Phase 3 Strategy Package for Evidence Validation.
Contains modular hypothesis generators evaluating trend, SMC order flow,
confluence radar, and liquidity sweeps across historical Parquet datasets.
"""

from research.strategies.base import BaseStrategy, StrategyConfig
from research.strategies.confluence_threshold import ConfluenceThresholdStrategy
from research.strategies.donchian_4h import (
    Donchian4hN20,
    Donchian4hN55,
    Donchian4hStrategy,
)
from research.strategies.fvg_retest import FVGRetestStrategy
from research.strategies.liquidity_sweep import LiquiditySweepStrategy
from research.strategies.trend_pullback import TrendPullbackStrategy

__all__ = [
    "BaseStrategy",
    "StrategyConfig",
    "TrendPullbackStrategy",
    "FVGRetestStrategy",
    "ConfluenceThresholdStrategy",
    "LiquiditySweepStrategy",
    "Donchian4hStrategy",
    "Donchian4hN20",
    "Donchian4hN55",
]
