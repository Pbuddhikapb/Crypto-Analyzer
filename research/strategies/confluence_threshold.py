"""
Hypothesis 3: Multi-Timeframe Directional Confluence Radar Threshold (Score >= 75).

Trading Concept:
Evaluates the multi-factor confluence model across Macro Trend,
SMC Fair Value Gaps, Valuation Discount/Premium, ADX Volatility, and Momentum Divergences.
Triggers candidate entries only when directional conviction is high (score >= 75)
outside of choppy consolidation regimes.
"""

from typing import List, Optional

import numpy as np
import pandas as pd

from engine.simulator import CandidateSignal
from indicators import (
    check_recent_bearish_fvg,
    check_recent_bullish_fvg,
    classify_discount_premium,
    detect_rsi_divergences,
    get_active_dealing_range,
)
from research.strategies.base import BaseStrategy, StrategyConfig
from research.strategies.trend_pullback import compute_series_atr, compute_series_ema


def compute_series_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute rolling Wilder's Average Directional Index (ADX) series."""
    n = len(closes)
    adx = np.zeros(n, dtype=np.float64)
    if n < period * 2:
        return adx

    tr = np.empty(n, dtype=np.float64)
    pdm = np.empty(n, dtype=np.float64)
    mdm = np.empty(n, dtype=np.float64)
    tr[0] = highs[0] - lows[0]
    pdm[0] = 0.0
    mdm[0] = 0.0

    for i in range(1, n):
        h, l, c_prev = highs[i], lows[i], closes[i - 1]
        tr[i] = max(h - l, abs(h - c_prev), abs(l - c_prev))
        up = h - highs[i - 1]
        down = lows[i - 1] - l
        pdm[i] = up if up > down and up > 0.0 else 0.0
        mdm[i] = down if down > up and down > 0.0 else 0.0

    tr_s = np.zeros(n, dtype=np.float64)
    pdm_s = np.zeros(n, dtype=np.float64)
    mdm_s = np.zeros(n, dtype=np.float64)

    tr_s[period] = np.sum(tr[1 : period + 1])
    pdm_s[period] = np.sum(pdm[1 : period + 1])
    mdm_s[period] = np.sum(mdm[1 : period + 1])

    dx = np.zeros(n, dtype=np.float64)
    for i in range(period + 1, n):
        tr_s[i] = (tr_s[i - 1] * (period - 1) + tr[i]) / period
        pdm_s[i] = (pdm_s[i - 1] * (period - 1) + pdm[i]) / period
        mdm_s[i] = (mdm_s[i - 1] * (period - 1) + mdm[i]) / period

        denom = tr_s[i]
        pdi = 100.0 * (pdm_s[i] / denom) if denom > 1e-9 else 0.0
        mdi = 100.0 * (mdm_s[i] / denom) if denom > 1e-9 else 0.0
        di_sum = pdi + mdi
        dx[i] = 100.0 * (abs(pdi - mdi) / di_sum) if di_sum > 1e-9 else 0.0

    start_adx = 2 * period
    if start_adx < n:
        adx[start_adx] = np.mean(dx[period + 1 : start_adx + 1])
        for i in range(start_adx + 1, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
    return adx


class ConfluenceThresholdStrategy(BaseStrategy):
    """
    Hypothesis 3: Confluence Radar Threshold.
    Enforces strict zero lookahead: all signals at bar i close execute at bar i+1 open.
    Optimized with precomputed series for rolling ADX, ATR, and EMAs.
    """

    def __init__(self, config: Optional[StrategyConfig] = None):
        cfg = config or StrategyConfig(
            name="ConfluenceThreshold",
            description="Multi-Timeframe 0-100 Confluence Model Triggered at High Conviction (>= 75)",
            parameters={
                "min_score": 75.0,
                "min_adx": 20.0,
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

        # 1. Compute 4h macro trend
        c_4h_closes = c_4h["close"].to_numpy(dtype=np.float64)
        c_4h["ema_fast"] = compute_series_ema(c_4h_closes, 50)
        c_4h["ema_slow"] = compute_series_ema(c_4h_closes, 200)
        c_4h["close_time"] = c_4h["open_time"] + 14400000

        # 2. Compute 1h indicators
        c_1h_closes = c_1h["close"].to_numpy(dtype=np.float64)
        c_1h_highs = c_1h["high"].to_numpy(dtype=np.float64)
        c_1h_lows = c_1h["low"].to_numpy(dtype=np.float64)
        c_1h_vols = c_1h["volume"].to_numpy(dtype=np.float64)

        c_1h["ema_50"] = compute_series_ema(c_1h_closes, 50)
        c_1h["ema_200"] = compute_series_ema(c_1h_closes, 200)
        c_1h["atr"] = compute_series_atr(c_1h_highs, c_1h_lows, c_1h_closes, 14)
        c_1h["adx"] = compute_series_adx(c_1h_highs, c_1h_lows, c_1h_closes, 14)
        c_1h["close_time"] = c_1h["open_time"] + 3600000

        # Rolling 20 volume SMA
        vol_s = pd.Series(c_1h_vols)
        c_1h["vol_sma20"] = vol_s.rolling(20, min_periods=1).mean().to_numpy()

        # Merge 4h closed trend
        merged = pd.merge_asof(
            c_1h,
            c_4h[["close_time", "ema_fast", "ema_slow"]],
            on="close_time",
            direction="backward",
            suffixes=("", "_4h"),
        )

        # Pre-extract numpy arrays
        fast_4h = merged["ema_fast"].to_numpy(dtype=np.float64)
        slow_4h = merged["ema_slow"].to_numpy(dtype=np.float64)
        ema_50_1h = merged["ema_50"].to_numpy(dtype=np.float64)
        ema_200_1h = merged["ema_200"].to_numpy(dtype=np.float64)
        atr_arr = merged["atr"].to_numpy(dtype=np.float64)
        adx_arr = merged["adx"].to_numpy(dtype=np.float64)
        vol_sma_arr = merged["vol_sma20"].to_numpy(dtype=np.float64)
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
        warmup = max(200 * 4, 250)
        min_score = float(params["min_score"])
        min_adx = float(params["min_adx"])
        atr_mult = float(params["atr_multiplier"])
        min_pct = float(params["min_stop_pct"]) / 100.0
        max_pct = float(params["max_stop_pct"]) / 100.0
        sym_upper = symbol.upper()

        for i in range(warmup, n_bars - 1):
            f_4h = fast_4h[i]
            s_4h = slow_4h[i]
            if np.isnan(f_4h) or np.isnan(s_4h):
                continue

            adx_val = adx_arr[i]
            if adx_val < min_adx:
                continue

            # 4h Trend
            htf_bull = f_4h > s_4h
            htf_bear = f_4h < s_4h

            # 1h Trend
            ema50 = ema_50_1h[i]
            ema200 = ema_200_1h[i]
            if np.isnan(ema50) or np.isnan(ema200):
                continue
            ltf_bull = ema50 > ema200
            ltf_bear = ema50 < ema200

            # Only evaluate candidate if at least one side is macro-aligned
            if not (htf_bull and ltf_bull) and not (htf_bear and ltf_bear):
                continue

            window_candles = ohlcv_list[max(0, i - 40) : i + 1]
            if len(window_candles) < 20:
                continue

            c_close = closes[i]
            vol_val = vols[i]
            vol_sma = vol_sma_arr[i]
            atr_val = atr_arr[i]
            if atr_val <= 0:
                continue

            entry_price = opens[i + 1]
            entry_ts_ms = int(open_times[i + 1])
            dr = get_active_dealing_range(window_candles)

            # Check Confluence Score for LONG
            if htf_bull and ltf_bull:
                score_long = 65.0  # Base aligned score (50 base + 15 dual-trend)

                # SMC FVG presence
                if check_recent_bullish_fvg(window_candles, lookback=5):
                    score_long += 15.0

                # Valuation discount
                if dr:
                    zone, _ = classify_discount_premium(c_close, dr["swing_low"], dr["swing_high"], side="LONG")
                    if zone in ("DISCOUNT", "DEEP_DISCOUNT", "OTE"):
                        score_long += 10.0

                # Volatility
                if adx_val >= 25.0:
                    score_long += 10.0

                # Volume expansion
                vol_surge = (vol_val / vol_sma) if vol_sma > 0 else 1.0
                if vol_surge >= 1.3:
                    score_long += 10.0

                # Momentum divergence
                bull_div, _ = detect_rsi_divergences(window_candles, period=14, lookback=25, include_hidden=False)
                if bull_div:
                    score_long += 10.0

                score_long = min(100.0, score_long)

                if score_long >= min_score:
                    stop_dist = max(atr_val * atr_mult, entry_price * min_pct)
                    stop_dist = min(stop_dist, entry_price * max_pct)
                    sl_price = round(entry_price - stop_dist, 4)

                    signals.append(
                        CandidateSignal(
                            symbol=sym_upper,
                            side="LONG",
                            entry_price=entry_price,
                            stop_loss=sl_price,
                            timestamp_ms=entry_ts_ms,
                            score=score_long,
                            metadata={"strategy": "ConfluenceThreshold", "bar_idx": i + 1, "confluence_score": score_long},
                        )
                    )
                    continue

            # Check Confluence Score for SHORT
            if htf_bear and ltf_bear:
                score_short = 65.0  # Base aligned score

                if check_recent_bearish_fvg(window_candles, lookback=5):
                    score_short += 15.0

                if dr:
                    zone_s, _ = classify_discount_premium(c_close, dr["swing_low"], dr["swing_high"], side="SHORT")
                    if zone_s in ("PREMIUM", "DEEP_PREMIUM", "OTE"):
                        score_short += 10.0

                if adx_val >= 25.0:
                    score_short += 10.0

                vol_surge = (vol_val / vol_sma) if vol_sma > 0 else 1.0
                if vol_surge >= 1.3:
                    score_short += 10.0

                _, bear_div = detect_rsi_divergences(window_candles, period=14, lookback=25, include_hidden=False)
                if bear_div:
                    score_short += 10.0

                score_short = min(100.0, score_short)

                if score_short >= min_score:
                    stop_dist = max(atr_val * atr_mult, entry_price * min_pct)
                    stop_dist = min(stop_dist, entry_price * max_pct)
                    sl_price = round(entry_price + stop_dist, 4)

                    signals.append(
                        CandidateSignal(
                            symbol=sym_upper,
                            side="SHORT",
                            entry_price=entry_price,
                            stop_loss=sl_price,
                            timestamp_ms=entry_ts_ms,
                            score=score_short,
                            metadata={"strategy": "ConfluenceThreshold", "bar_idx": i + 1, "confluence_score": score_short},
                        )
                    )

        return signals
