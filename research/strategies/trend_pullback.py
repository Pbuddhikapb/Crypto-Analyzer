"""
Hypothesis 1: Multi-Timeframe Trend Pullback (Momentum Recovery).

Trading Concept:
When the higher timeframe (4h) is trending and the 1h timeframe pulls back
to an equilibrium moving average (EMA 50) with temporary momentum exhaustion,
enter upon confirmed momentum inflection (RSI recovery).
"""

from typing import List, Optional

import numpy as np
import pandas as pd

from engine.simulator import CandidateSignal
from indicators import calculate_rsi_series
from research.strategies.base import BaseStrategy, StrategyConfig


def compute_series_ema(values: np.ndarray, period: int) -> np.ndarray:
    """Compute rolling EMA across a 1D numpy array."""
    n = len(values)
    ema = np.empty(n, dtype=np.float64)
    if n == 0:
        return ema
    if n < period:
        ema[:] = values
        return ema

    alpha = 2.0 / (period + 1.0)
    ema[:period - 1] = np.nan
    ema[period - 1] = np.mean(values[:period])
    for i in range(period, n):
        ema[i] = alpha * values[i] + (1.0 - alpha) * ema[i - 1]
    return ema


def compute_series_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute rolling ATR series using Wilder's smoothing."""
    n = len(closes)
    atr = np.empty(n, dtype=np.float64)
    if n == 0:
        return atr
    if n == 1:
        atr[0] = highs[0] - lows[0]
        return atr

    tr = np.empty(n, dtype=np.float64)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))

    if n < period:
        atr[:] = np.mean(tr)
        return atr

    atr[:period - 1] = np.nan
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


class TrendPullbackStrategy(BaseStrategy):
    """
    Hypothesis 1: Multi-Timeframe Trend Pullback.
    Enforces strict zero lookahead: all signals at bar i close execute at bar i+1 open.
    Optimized with contiguous numpy arrays for institutional execution speed.
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        cfg = config or StrategyConfig(
            name="TrendPullback",
            description="4h Macro Trend + 1h EMA 50 Pullback + RSI Momentum Recovery",
            parameters={
                "htf_ema_fast": 50,
                "htf_ema_slow": 200,
                "ltf_ema": 50,
                "rsi_period": 14,
                "rsi_oversold_recovery": 40.0,
                "rsi_overbought_recovery": 60.0,
                "atr_multiplier": 1.5,
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

        # 1. Compute 4h indicators
        c_4h_closes = c_4h["close"].to_numpy(dtype=np.float64)
        c_4h["ema_fast"] = compute_series_ema(c_4h_closes, params["htf_ema_fast"])
        c_4h["ema_slow"] = compute_series_ema(c_4h_closes, params["htf_ema_slow"])
        c_4h["close_time"] = c_4h["open_time"] + 14400000

        # 2. Compute 1h indicators
        c_1h_closes = c_1h["close"].to_numpy(dtype=np.float64)
        c_1h_highs = c_1h["high"].to_numpy(dtype=np.float64)
        c_1h_lows = c_1h["low"].to_numpy(dtype=np.float64)

        c_1h["ema_50"] = compute_series_ema(c_1h_closes, params["ltf_ema"])
        c_1h["rsi"] = calculate_rsi_series(c_1h_closes, period=params["rsi_period"])
        c_1h["atr"] = compute_series_atr(c_1h_highs, c_1h_lows, c_1h_closes, period=14)
        c_1h["close_time"] = c_1h["open_time"] + 3600000

        # 3. Merge 4h closed trend state into 1h without lookahead
        merged = pd.merge_asof(
            c_1h,
            c_4h[["close_time", "ema_fast", "ema_slow"]],
            on="close_time",
            direction="backward",
            suffixes=("", "_4h"),
        )

        # Pre-extract contiguous numpy arrays for fast indexing
        fast_4h = merged["ema_fast"].to_numpy(dtype=np.float64)
        slow_4h = merged["ema_slow"].to_numpy(dtype=np.float64)
        ema_50_1h = merged["ema_50"].to_numpy(dtype=np.float64)
        rsi_arr = merged["rsi"].to_numpy(dtype=np.float64)
        atr_arr = merged["atr"].to_numpy(dtype=np.float64)
        opens = merged["open"].to_numpy(dtype=np.float64)
        highs = merged["high"].to_numpy(dtype=np.float64)
        lows = merged["low"].to_numpy(dtype=np.float64)
        open_times = merged["open_time"].to_numpy(dtype=np.int64)

        signals: List[CandidateSignal] = []
        n_bars = len(merged)
        warmup = max(params["htf_ema_slow"] * 4, 250)

        atr_mult = float(params["atr_multiplier"])
        min_stop_pct = float(params["min_stop_pct"]) / 100.0
        max_stop_pct = float(params["max_stop_pct"]) / 100.0
        rsi_os_rec = float(params["rsi_oversold_recovery"])
        rsi_ob_rec = float(params["rsi_overbought_recovery"])
        sym_upper = symbol.upper()

        for i in range(warmup, n_bars - 1):
            f_4h = fast_4h[i]
            s_4h = slow_4h[i]
            if np.isnan(f_4h) or np.isnan(s_4h):
                continue

            # 4h Trend Direction
            is_4h_bull = f_4h > s_4h
            is_4h_bear = f_4h < s_4h

            ema_val = ema_50_1h[i]
            rsi_curr = rsi_arr[i]
            atr_val = atr_arr[i]

            if np.isnan(ema_val) or np.isnan(rsi_curr) or np.isnan(atr_val) or atr_val <= 0:
                continue

            entry_price = opens[i + 1]
            entry_ts_ms = int(open_times[i + 1])

            # Check Long Pullback
            if is_4h_bull:
                low_val = lows[i]
                if low_val <= (ema_val * 1.002):
                    start_idx = max(0, i - 4)
                    recent_rsi = rsi_arr[start_idx : i + 1]
                    if np.any(recent_rsi < 38.0) and rsi_curr >= rsi_os_rec:
                        stop_dist = max(atr_val * atr_mult, entry_price * min_stop_pct)
                        stop_dist = min(stop_dist, entry_price * max_stop_pct)
                        sl_price = round(entry_price - stop_dist, 4)

                        signals.append(
                            CandidateSignal(
                                symbol=sym_upper,
                                side="LONG",
                                entry_price=entry_price,
                                stop_loss=sl_price,
                                timestamp_ms=entry_ts_ms,
                                score=80.0,
                                metadata={"strategy": "TrendPullback", "bar_idx": i + 1, "atr": atr_val},
                            )
                        )

            # Check Short Pullback
            elif is_4h_bear:
                high_val = highs[i]
                if high_val >= (ema_val * 0.998):
                    start_idx = max(0, i - 4)
                    recent_rsi = rsi_arr[start_idx : i + 1]
                    if np.any(recent_rsi > 62.0) and rsi_curr <= rsi_ob_rec:
                        stop_dist = max(atr_val * atr_mult, entry_price * min_stop_pct)
                        stop_dist = min(stop_dist, entry_price * max_stop_pct)
                        sl_price = round(entry_price + stop_dist, 4)

                        signals.append(
                            CandidateSignal(
                                symbol=sym_upper,
                                side="SHORT",
                                entry_price=entry_price,
                                stop_loss=sl_price,
                                timestamp_ms=entry_ts_ms,
                                score=80.0,
                                metadata={"strategy": "TrendPullback", "bar_idx": i + 1, "atr": atr_val},
                            )
                        )

        return signals
