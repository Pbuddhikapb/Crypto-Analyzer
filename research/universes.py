"""
Named Universe System for Quantitative Strategy Research.

Provides pre-defined named universes of symbols:
- U10: Pre-registered primary evidence universe (10 major coins, default).
- U4: Operational monitoring subset (BTCUSDT, SOLUSDT, BNBUSDT, XRPUSDT).
- U5: U4 + XAUUSDT (BTCUSDT, SOLUSDT, BNBUSDT, XRPUSDT, XAUUSDT).
"""

import os
from typing import Dict, List, Optional

UNIVERSES: Dict[str, List[str]] = {
    "U10": [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
        "BNBUSDT",
        "XRPUSDT",
        "DOGEUSDT",
        "ADAUSDT",
        "AVAXUSDT",
        "LINKUSDT",
        "SUIUSDT",
    ],
    "U4": [
        "BTCUSDT",
        "SOLUSDT",
        "BNBUSDT",
        "XRPUSDT",
    ],
    "U5": [
        "BTCUSDT",
        "SOLUSDT",
        "BNBUSDT",
        "XRPUSDT",
        "XAUUSDT",
    ],
}

DEFAULT_UNIVERSE = "U10"


def get_universe_symbols(name: str) -> List[str]:
    """
    Return a list of symbols belonging to the requested named universe.

    Args:
        name: Universe identifier (case-insensitive, e.g. 'U10', 'u4', 'U5').

    Returns:
        List of symbol strings.

    Raises:
        ValueError: If universe name is not recognized.
    """
    key = name.upper()
    if key not in UNIVERSES:
        raise ValueError(
            f"Unknown universe '{name}'. Recognized universes are: {', '.join(sorted(UNIVERSES.keys()))}"
        )
    return list(UNIVERSES[key])


def validate_universe_data(
    universe_name: str,
    symbols: Optional[List[str]] = None,
    data_dir: str = "data/candles",
    timeframes: Optional[List[str]] = None,
) -> None:
    """
    Validate that complete candle parquet data exists for all symbols in the universe.
    Never skips missing symbols silently: fails immediately with a clear error naming the symbol.

    Args:
        universe_name: Name of the universe for diagnostic error reporting.
        symbols: Member symbols (if None, loaded from universe_name).
        data_dir: Parquet data lake directory path.
        timeframes: Timeframe suffixes to verify (defaults to ['1h', '4h']).

    Raises:
        FileNotFoundError: If any symbol has missing parquet candle files.
    """
    tfs = timeframes or ["1h", "4h"]
    syms = symbols if symbols is not None else get_universe_symbols(universe_name)

    missing_by_symbol: Dict[str, List[str]] = {}
    for sym in syms:
        missing_files = []
        for tf in tfs:
            expected_file = os.path.join(data_dir, f"{sym}_{tf}.parquet")
            if not os.path.exists(expected_file):
                missing_files.append(f"{sym}_{tf}.parquet")
        if missing_files:
            missing_by_symbol[sym] = missing_files

    if missing_by_symbol:
        details = "; ".join(
            f"symbol '{s}' missing [{', '.join(files)}]"
            for s, files in missing_by_symbol.items()
        )
        first_missing_sym = next(iter(missing_by_symbol.keys()))
        raise FileNotFoundError(
            f"Universe '{universe_name}' contains symbol '{first_missing_sym}' with no data ({details} in '{data_dir}'). "
            f"Cannot evaluate universe '{universe_name}' without complete data for all member symbols. Never skipping silently."
        )
