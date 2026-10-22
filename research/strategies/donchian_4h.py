"""
Hypothesis 1: 4h Donchian Breakout with ATR Trailing Exit.

Preregistration Reference: research/preregistration/H1_donchian_4h.md

Trading Concept:
Trend-following breakout strategy operating on 4h bars with 1h execution.
- LONG if 4h close > highest high of previous N 4h bars.
- SHORT if 4h close < lowest low of previous N 4h bars.
- Entry: Open of the next 1h bar after the 4h bar closes (zero lookahead).
- Initial Stop: 3.0 x ATR(14, 4h). R = that distance.
- Exit: Trailing stop 1R behind the best price since entry, moving only
  in the profit direction. No take-profit, no breakeven rule, no stagnancy exit.
- Variants:
  - H1a: Donchian4hN20 (N=20)
  - H1b: Donchian4hN55 (N=55)
- Significance level: alpha = 0.025 (preregistered hurdle).
"""

from typing import List, Optional

import numpy as np
import pandas as pd

from engine.exits import ExitConfig
from engine.simulator import CandidateSignal
from research.strategies.base import BaseStrategy, StrategyConfig
from research.strategies.trend_pullback import compute_series_atr


class Donchian4hStrategy(BaseStrategy):
    """
    Base implementation for 4h Donchian Breakout strategies with 1h execution.
    Enforces strict zero lookahead: all signals evaluated at 4h bar close enter
    at the open of the very next 1h candle.
    """

    def __init__(self, config: StrategyConfig, significance_level: float = 0.025):
        super().__init__(config)
        self.significance_level = significance_level
        trail_dist_r = float(self.config.parameters.get("trail_dist_r", 1.0))
        self.exit_config = ExitConfig(
            exit_mode="trailing_1r",
            trail_dist_r=trail_dist_r,
        )

    def generate_signals(
        self,
        symbol: str,
        df_1h: pd.DataFrame,
        df_4h: pd.DataFrame,
    ) -> List[CandidateSignal]:
        params = self.config.parameters
        n_period = int(params["donchian_period"])
        atr_period = int(params.get("atr_period", 14))
        atr_multiplier = float(params.get("atr_multiplier", 3.0))

        c_1h = df_1h.copy()
        c_4h = df_4h.copy()

        if len(c_4h) < max(n_period + 1, atr_period + 1) or len(c_1h) == 0:
            return []

        # 1. Compute 4h indicators
        c_4h_highs = c_4h["high"].to_numpy(dtype=np.float64)
        c_4h_lows = c_4h["low"].to_numpy(dtype=np.float64)
        c_4h_closes = c_4h["close"].to_numpy(dtype=np.float64)

        c_4h["atr_4h"] = compute_series_atr(c_4h_highs, c_4h_lows, c_4h_closes, period=atr_period)
        # Highest high and lowest low of the strictly previous N 4h bars
        c_4h["highest_high"] = c_4h["high"].shift(1).rolling(n_period).max()
        c_4h["lowest_low"] = c_4h["low"].shift(1).rolling(n_period).min()
        c_4h["close_time"] = c_4h["open_time"] + 14400000
        c_4h["c4_close_time"] = c_4h["close_time"]

        # 2. Compute 1h close times
        c_1h["close_time"] = c_1h["open_time"] + 3600000

        # 3. Merge 4h closed breakout state into 1h without lookahead
        merged = pd.merge_asof(
            c_1h,
            c_4h[["close_time", "c4_close_time", "close", "highest_high", "lowest_low", "atr_4h"]],
            on="close_time",
            direction="backward",
            suffixes=("", "_4h"),
        )

        opens_1h = merged["open"].to_numpy(dtype=np.float64)
        open_times_1h = merged["open_time"].to_numpy(dtype=np.int64)
        close_times_1h = merged["close_time"].to_numpy(dtype=np.int64)
        c4_close_times = merged["c4_close_time"].fillna(0).to_numpy(dtype=np.int64)
        c4_closes = merged["close_4h"].to_numpy(dtype=np.float64)
        c4_hhs = merged["highest_high"].to_numpy(dtype=np.float64)
        c4_lls = merged["lowest_low"].to_numpy(dtype=np.float64)
        c4_atrs = merged["atr_4h"].to_numpy(dtype=np.float64)

        signals: List[CandidateSignal] = []
        n_bars = len(merged)
        sym_upper = symbol.upper()

        for i in range(n_bars - 1):
            # Only evaluate when the current 1h candle exactly coincides with the 4h bar close
            if close_times_1h[i] != c4_close_times[i]:
                continue

            c4_close = c4_closes[i]
            hh = c4_hhs[i]
            ll = c4_lls[i]
            atr_val = c4_atrs[i]

            if np.isnan(c4_close) or np.isnan(hh) or np.isnan(ll) or np.isnan(atr_val) or atr_val <= 0:
                continue

            entry_price = opens_1h[i + 1]
            entry_ts_ms = int(open_times_1h[i + 1])
            stop_dist = atr_multiplier * atr_val

            # Preregistered Rule:
            # LONG if close > highest high of previous N 4h bars
            # SHORT if close < lowest low of previous N 4h bars
            if c4_close > hh:
                sl_price = round(entry_price - stop_dist, 4)
                signals.append(
                    CandidateSignal(
                        symbol=sym_upper,
                        side="LONG",
                        entry_price=entry_price,
                        stop_loss=sl_price,
                        timestamp_ms=entry_ts_ms,
                        score=80.0,
                        metadata={
                            "strategy": self.config.name,
                            "bar_idx": i + 1,
                            "atr_4h": atr_val,
                            "donchian_period": n_period,
                            "c4_close": c4_close,
                            "highest_high": hh,
                            "exit_mode": "trailing_1r",
                        },
                    )
                )
            elif c4_close < ll:
                sl_price = round(entry_price + stop_dist, 4)
                signals.append(
                    CandidateSignal(
                        symbol=sym_upper,
                        side="SHORT",
                        entry_price=entry_price,
                        stop_loss=sl_price,
                        timestamp_ms=entry_ts_ms,
                        score=80.0,
                        metadata={
                            "strategy": self.config.name,
                            "bar_idx": i + 1,
                            "atr_4h": atr_val,
                            "donchian_period": n_period,
                            "c4_close": c4_close,
                            "lowest_low": ll,
                            "exit_mode": "trailing_1r",
                        },
                    )
                )

        return signals


class Donchian4hN20(Donchian4hStrategy):
    """
    Hypothesis 1a: 4h Donchian Breakout N=20 with ATR(14) 3R Trailing Stop.
    Preregistration: H1a. Significance Level: alpha = 0.025.
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        cfg = config or StrategyConfig(
            name="Donchian4hN20",
            description="4h Donchian Breakout N=20 + ATR(14) 3R Trailing Stop (H1a)",
            timeframe_signal="1h",
            timeframe_htf="4h",
            parameters={
                "donchian_period": 20,
                "atr_period": 14,
                "atr_multiplier": 3.0,
                "trail_dist_r": 1.0,
            },
        )
        super().__init__(cfg, significance_level=0.025)


class Donchian4hN55(Donchian4hStrategy):
    """
    Hypothesis 1b: 4h Donchian Breakout N=55 with ATR(14) 3R Trailing Stop.
    Preregistration: H1b. Significance Level: alpha = 0.025.
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        cfg = config or StrategyConfig(
            name="Donchian4hN55",
            description="4h Donchian Breakout N=55 + ATR(14) 3R Trailing Stop (H1b)",
            timeframe_signal="1h",
            timeframe_htf="4h",
            parameters={
                "donchian_period": 55,
                "atr_period": 14,
                "atr_multiplier": 3.0,
                "trail_dist_r": 1.0,
            },
        )
        super().__init__(cfg, significance_level=0.025)
