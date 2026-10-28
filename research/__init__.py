"""Research package containing statistical baselines, hypothesis testing, and alpha audits."""

from research.random_baseline import (
    BaselineReport,
    MonteCarloDistributions,
    run_monte_carlo_baseline,
    run_random_baseline_iteration,
)
from research.universes import (
    DEFAULT_UNIVERSE,
    UNIVERSES,
    get_universe_symbols,
    validate_universe_data,
)

__all__ = [
    "BaselineReport",
    "MonteCarloDistributions",
    "run_random_baseline_iteration",
    "run_monte_carlo_baseline",
    "UNIVERSES",
    "DEFAULT_UNIVERSE",
    "get_universe_symbols",
    "validate_universe_data",
]
