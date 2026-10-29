"""
Hypothesis 2: SMC Fair Value Gap (FVG) Retest & Mitigation.

Trading Concept:
Institutional displacement leaves unmitigated Fair Value Gaps (3-bar volume imbalances).
When price retraces into an active FVG in the direction of the macro trend
and confirms rejection, enter on mitigation with stop loss at the gap invalidation level.
"""

from typing import List, Optional

import numpy as np
import pandas as pd

from domain.signals import CandidateSignal
from indicators import (
    check_bearish_fvg_retest,
    check_bullish_fvg_retest,
    check_fvg_rejection_confirmation,
)
from research.strategies.base import BaseStrategy, StrategyConfig
from research.strategies.trend_pullback import compute_series_ema


class FVGRetestStrategy(BaseStrategy):
    """
    Hypothesis 2: SMC Fair Value Gap Retest.
    Enforces strict zero lookahead: all signals at bar i close execute at bar i+1 open.
    Optimized with contiguous numpy arrays and window slicing.
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        cfg = config or StrategyConfig(
            name="FVGRetest",
            description="HTF Trend + SMC Fair Value Gap Retest & Mitigation Rejection",
            parameters={
                "htf_ema_fast": 50,
                "htf_ema_slow": 200,
                "fvg_lookback": 20,
                "min_stop_pct": 0.8,
                "max_stop_pct": 4.0,
            },
        )
        super().__init__(cfg)

    def generate_signals(
        self,
        symbol: str,
        df_1h: pd.DataFrame,
        df_4h: pd.DataFrame,
    ) -> List[CandidateSignal]:
        params = self.config.parameters
        c_1h = df_1h.copy()
        c_4h = df_4h.copy()

        # 1. Compute 4h trend
        c_4h_closes = c_4h["close"].to_numpy(dtype=np.float64)
        c_4h["ema_fast"] = compute_series_ema(c_4h_closes, params["htf_ema_fast"])
        c_4h["ema_slow"] = compute_series_ema(c_4h_closes, params["htf_ema_slow"])
        c_4h["close_time"] = c_4h["open_time"] + 14400000

        c_1h["close_time"] = c_1h["open_time"] + 3600000

        # Merge 4h closed trend state into 1h without lookahead
        merged = pd.merge_asof(
            c_1h,
            c_4h[["close_time", "ema_fast", "ema_slow"]],
            on="close_time",
            direction="backward",
            suffixes=("", "_4h"),
        )

        # Pre-extract numpy arrays and candle 5-tuples
        fast_4h = merged["ema_fast"].to_numpy(dtype=np.float64)
        slow_4h = merged["ema_slow"].to_numpy(dtype=np.float64)
        opens = merged["open"].to_numpy(dtype=np.float64)
        highs = merged["high"].to_numpy(dtype=np.float64)
        lows = merged["low"].to_numpy(dtype=np.float64)
        closes = merged["close"].to_numpy(dtype=np.float64)
        vols = merged["volume"].to_numpy(dtype=np.float64)
        open_times = merged["open_time"].to_numpy(dtype=np.int64)

        n_bars = len(merged)
        ohlcv_list = [
            (opens[k], highs[k], lows[k], closes[k], vols[k])
            for k in range(n_bars)
        ]

        signals: List[CandidateSignal] = []
        warmup = max(params["htf_ema_slow"] * 4, 250)
        fvg_lookback = int(params["fvg_lookback"])
        min_pct = float(params["min_stop_pct"]) / 100.0
        max_pct = float(params["max_stop_pct"]) / 100.0
        sym_upper = symbol.upper()

        for i in range(warmup, n_bars - 1):
            f_4h = fast_4h[i]
            s_4h = slow_4h[i]
            if np.isnan(f_4h) or np.isnan(s_4h):
                continue

            is_4h_bull = f_4h > s_4h
            is_4h_bear = f_4h < s_4h

            # Lookback window ending at bar i (strictly closed)
            window_start = max(0, i - 60)
            window_candles = ohlcv_list[window_start : i + 1]
            if len(window_candles) < 20:
                continue

            entry_price = opens[i + 1]
            entry_ts_ms = int(open_times[i + 1])
            curr_candle = window_candles[-1]

            if is_4h_bull:
                retest_hit, fvg_info = check_bullish_fvg_retest(window_candles, lookback=fvg_lookback)
                if retest_hit and fvg_info:
                    rejection_confirmed = check_fvg_rejection_confirmation(curr_candle, side="LONG")
                    if rejection_confirmed:
                        gap_bottom = fvg_info.get("bottom", curr_candle[2] * 0.985)
                        stop_dist = entry_price - gap_bottom
                        min_dist = entry_price * min_pct
                        max_dist = entry_price * max_pct

                        if stop_dist < min_dist:
                            stop_dist = min_dist
                        elif stop_dist > max_dist:
                            stop_dist = max_dist

                        sl_price = round(entry_price - stop_dist, 4)
                        signals.append(
                            CandidateSignal(
                                symbol=sym_upper,
                                side="LONG",
                                entry_price=entry_price,
                                stop_loss=sl_price,
                                timestamp_ms=entry_ts_ms,
                                score=85.0,
                                metadata={
                                    "strategy": "FVGRetest",
                                    "bar_idx": i + 1,
                                    "fvg_top": fvg_info.get("top"),
                                    "fvg_bottom": fvg_info.get("bottom"),
                                },
                            )
                        )

            elif is_4h_bear:
                retest_hit, fvg_info = check_bearish_fvg_retest(window_candles, lookback=fvg_lookback)
                if retest_hit and fvg_info:
                    rejection_confirmed = check_fvg_rejection_confirmation(curr_candle, side="SHORT")
                    if rejection_confirmed:
                        gap_top = fvg_info.get("top", curr_candle[1] * 1.015)
                        stop_dist = gap_top - entry_price
                        min_dist = entry_price * min_pct
                        max_dist = entry_price * max_pct

                        if stop_dist < min_dist:
                            stop_dist = min_dist
                        elif stop_dist > max_dist:
                            stop_dist = max_dist

                        sl_price = round(entry_price + stop_dist, 4)
                        signals.append(
                            CandidateSignal(
                                symbol=sym_upper,
                                side="SHORT",
                                entry_price=entry_price,
                                stop_loss=sl_price,
                                timestamp_ms=entry_ts_ms,
                                score=85.0,
                                metadata={
                                    "strategy": "FVGRetest",
                                    "bar_idx": i + 1,
                                    "fvg_top": fvg_info.get("top"),
                                    "fvg_bottom": fvg_info.get("bottom"),
                                },
                            )
                        )

        return signals
