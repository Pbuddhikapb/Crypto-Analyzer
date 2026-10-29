"""
Hypothesis 4: Liquidity Sweep / Swing Failure Pattern (SFP).

Trading Concept:
Smart money / institutional participants engineer liquidity by pushing price beyond
prominent swing highs or swing lows to trigger resting stop-loss orders (buy/sell stops).
When price sweeps a key swing level but immediately fails to hold outside the range,
closing back inside the key level with volume or wick rejection, a Swing Failure Pattern
(SFP) is confirmed.
"""

from typing import List, Optional

import numpy as np
import pandas as pd

from domain.signals import CandidateSignal
from indicators import detect_swing_points
from research.strategies.base import BaseStrategy, StrategyConfig
from research.strategies.trend_pullback import compute_series_ema


class LiquiditySweepStrategy(BaseStrategy):
    """
    Hypothesis 4: Liquidity Sweep / Swing Failure Pattern (SFP).
    Enforces strict zero lookahead: all signals at bar i close execute at bar i+1 open.
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        cfg = config or StrategyConfig(
            name="LiquiditySweep",
            description="Swing Failure Pattern (SFP) sweeping key liquidity with rejection",
            parameters={
                "swing_window": 3,
                "lookback_bars": 60,
                "min_wick_ratio": 0.25,
                "vol_multiplier": 1.1,
                "buffer_pct": 0.1,
                "min_stop_pct": 0.8,
                "max_stop_pct": 4.0,
                "macro_trend_filter": True,
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

        # 1. 4h Macro Trend
        c_4h_closes = c_4h["close"].to_numpy(dtype=np.float64)
        c_4h["ema_fast"] = compute_series_ema(c_4h_closes, 50)
        c_4h["ema_slow"] = compute_series_ema(c_4h_closes, 200)
        c_4h["close_time"] = c_4h["open_time"] + 14400000

        c_1h["close_time"] = c_1h["open_time"] + 3600000

        merged = pd.merge_asof(
            c_1h,
            c_4h[["close_time", "ema_fast", "ema_slow"]],
            on="close_time",
            direction="backward",
            suffixes=("", "_4h"),
        )

        n_bars = len(merged)
        if n_bars < 100:
            return []

        highs = merged["high"].to_numpy(dtype=np.float64)
        lows = merged["low"].to_numpy(dtype=np.float64)
        closes = merged["close"].to_numpy(dtype=np.float64)
        opens = merged["open"].to_numpy(dtype=np.float64)
        vols = merged["volume"].to_numpy(dtype=np.float64)

        # 2. Compute 20-bar rolling volume mean
        vol_s = pd.Series(vols)
        vol_sma20 = vol_s.rolling(20, min_periods=1).mean().to_numpy()

        # 3. Detect all fractal swing points across the series
        # Note: detect_swing_points guarantees swing at k is confirmed at k + swing_window
        swing_window = params["swing_window"]
        all_swings = detect_swing_points(highs, lows, closes, window=swing_window)

        # Group swings by confirmed_index for fast lookup
        swings_by_conf: dict = {}
        for s in all_swings:
            c_idx = s["confirmed_index"]
            if c_idx not in swings_by_conf:
                swings_by_conf[c_idx] = []
            swings_by_conf[c_idx].append(s)

        signals: List[CandidateSignal] = []
        lookback = params["lookback_bars"]
        macro_filter = params.get("macro_trend_filter", True)

        # Active confirmed swings tracker
        confirmed_highs: List[dict] = []
        confirmed_lows: List[dict] = []

        warmup = max(swing_window * 2 + 10, 100)

        for i in range(warmup, n_bars - 1):
            # Ingest newly confirmed swings at bar i
            if i in swings_by_conf:
                for s in swings_by_conf[i]:
                    if s["type"] == "HIGH":
                        confirmed_highs.append(s)
                    else:
                        confirmed_lows.append(s)

            # Purge swings older than lookback
            min_bar_idx = i - lookback
            confirmed_highs = [s for s in confirmed_highs if s["index"] >= min_bar_idx]
            confirmed_lows = [s for s in confirmed_lows if s["index"] >= min_bar_idx]

            row = merged.iloc[i]
            next_bar = merged.iloc[i + 1]

            ema_fast_4h = row["ema_fast"]
            ema_slow_4h = row["ema_slow"]
            is_4h_bull = True
            is_4h_bear = True
            if macro_filter and not pd.isna(ema_fast_4h) and not pd.isna(ema_slow_4h):
                is_4h_bull = ema_fast_4h >= ema_slow_4h
                is_4h_bear = ema_fast_4h <= ema_slow_4h

            bar_open = opens[i]
            bar_high = highs[i]
            bar_low = lows[i]
            bar_close = closes[i]
            bar_vol = vols[i]
            avg_vol = vol_sma20[i]
            candle_range = bar_high - bar_low

            if candle_range <= 0:
                continue

            entry_price = float(next_bar["open"])
            entry_ts_ms = int(next_bar["open_time"])

            # Check Bullish SFP (Sweep of Swing Low)
            # Conditions:
            # 1. 4h macro trend is bullish (or macro_filter=False)
            # 2. Bar i low wicks below a confirmed swing low
            # 3. Bar i close finishes back above that swing low
            # 4. Wick rejection: lower wick is significant OR volume surge
            if is_4h_bull and confirmed_lows:
                # Find most recent swing low that was swept
                for sw in reversed(confirmed_lows):
                    # Make sure the swing low occurred at least swing_window bars ago
                    if sw["index"] >= i:
                        continue

                    level = sw["price"]
                    # Check sweep: low dips below level, but close is above level
                    if bar_low < level and bar_close > level:
                        lower_wick = min(bar_open, bar_close) - bar_low
                        wick_ratio = lower_wick / candle_range
                        vol_elevated = bar_vol >= (avg_vol * params["vol_multiplier"])

                        if wick_ratio >= params["min_wick_ratio"] or vol_elevated:
                            # Valid Bullish SFP
                            # Stop loss placed beyond sweep wick with buffer
                            raw_stop_dist = entry_price - (bar_low * (1.0 - params["buffer_pct"] / 100.0))
                            min_dist = entry_price * (params["min_stop_pct"] / 100.0)
                            max_dist = entry_price * (params["max_stop_pct"] / 100.0)

                            stop_dist = max(raw_stop_dist, min_dist)
                            stop_dist = min(stop_dist, max_dist)
                            sl_price = round(entry_price - stop_dist, 4)

                            signals.append(
                                CandidateSignal(
                                    symbol=symbol.upper(),
                                    side="LONG",
                                    entry_price=entry_price,
                                    stop_loss=sl_price,
                                    timestamp_ms=entry_ts_ms,
                                    score=85.0,
                                    metadata={
                                        "strategy": "LiquiditySweep",
                                        "bar_idx": i + 1,
                                        "swept_level": level,
                                        "wick_ratio": round(wick_ratio, 3),
                                    },
                                )
                            )
                            # Only trigger once per bar
                            break

            # Check Bearish SFP (Sweep of Swing High)
            # Conditions:
            # 1. 4h macro trend is bearish (or macro_filter=False)
            # 2. Bar i high wicks above a confirmed swing high
            # 3. Bar i close finishes back below that swing high
            # 4. Wick rejection: upper wick is significant OR volume surge
            if is_4h_bear and confirmed_highs:
                for sw in reversed(confirmed_highs):
                    if sw["index"] >= i:
                        continue

                    level = sw["price"]
                    if bar_high > level and bar_close < level:
                        upper_wick = bar_high - max(bar_open, bar_close)
                        wick_ratio = upper_wick / candle_range
                        vol_elevated = bar_vol >= (avg_vol * params["vol_multiplier"])

                        if wick_ratio >= params["min_wick_ratio"] or vol_elevated:
                            raw_stop_dist = (bar_high * (1.0 + params["buffer_pct"] / 100.0)) - entry_price
                            min_dist = entry_price * (params["min_stop_pct"] / 100.0)
                            max_dist = entry_price * (params["max_stop_pct"] / 100.0)

                            stop_dist = max(raw_stop_dist, min_dist)
                            stop_dist = min(stop_dist, max_dist)
                            sl_price = round(entry_price + stop_dist, 4)

                            signals.append(
                                CandidateSignal(
                                    symbol=symbol.upper(),
                                    side="SHORT",
                                    entry_price=entry_price,
                                    stop_loss=sl_price,
                                    timestamp_ms=entry_ts_ms,
                                    score=85.0,
                                    metadata={
                                        "strategy": "LiquiditySweep",
                                        "bar_idx": i + 1,
                                        "swept_level": level,
                                        "wick_ratio": round(wick_ratio, 3),
                                    },
                                )
                            )
                            break

        return signals
