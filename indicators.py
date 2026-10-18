import numpy as np
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

Candle = Union[Tuple[float, float, float, float, float], Sequence[float]]


def calculate_ema(closes: Sequence[float], period: int) -> float:
    """Calculate Exponential Moving Average (EMA) and return the latest value."""
    arr = np.asarray(closes, dtype=float)
    if len(arr) == 0:
        return 0.0
    if len(arr) < period:
        return float(arr[-1])

    alpha = 2.0 / (period + 1.0)
    ema = np.empty_like(arr)
    # Initialize with SMA of first 'period' elements
    ema[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        ema[i] = alpha * arr[i] + (1.0 - alpha) * ema[i - 1]

    return float(ema[-1])


def calculate_atr(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float:
    """Calculate Average True Range (ATR) using Wilder's smoothing and return the latest value."""
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)
    c = np.asarray(closes, dtype=float)

    n = len(c)
    if n == 0:
        return 0.0
    if n == 1:
        return float(h[0] - l[0])

    # True Range calculation
    tr = np.empty(n, dtype=float)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))

    if n < period:
        return float(np.mean(tr))

    # Wilder's Smoothing for ATR
    atr = np.empty(n, dtype=float)
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    return float(atr[-1])


def calculate_rsi_series(closes: Sequence[float], period: int = 14) -> np.ndarray:
    """Calculate the full rolling Relative Strength Index (RSI) series using Wilder's smoothing."""
    arr = np.asarray(closes, dtype=float)
    n = len(arr)
    if n <= period:
        return np.full(n, 50.0)

    deltas = np.diff(arr)
    gains = np.maximum(deltas, 0.0)
    losses = np.maximum(-deltas, 0.0)

    rsi = np.empty(n, dtype=float)
    rsi[:period] = 50.0

    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    if avg_loss == 0.0:
        rsi[period] = 100.0 if avg_gain > 0.0 else 50.0
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0.0:
            rsi[i + 1] = 100.0 if avg_gain > 0.0 else 50.0
        else:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return rsi


def calculate_rsi(closes: Sequence[float], period: int = 14) -> float:
    """Calculate Relative Strength Index (RSI) using Wilder's smoothing and return the latest value."""
    series = calculate_rsi_series(closes, period=period)
    return float(series[-1])


def detect_rsi_divergences(
    candles: Sequence[Candle],
    period: int = 14,
    lookback: int = 30,
    include_hidden: bool = False,
) -> Union[Tuple[bool, bool], Tuple[bool, bool, bool, bool]]:
    """Detect Regular and Hidden RSI Divergences over a fractal lookback window.
    
    Regular Divergences (Reversal):
    - Bullish: Price Lower Low + RSI Higher Low
    - Bearish: Price Higher High + RSI Lower High
    
    Hidden Divergences (Trend Continuation):
    - Bullish: Price Higher Low + RSI Lower Low
    - Bearish: Price Lower High + RSI Higher High
    
    Returns:
        (has_bullish, has_bearish) if include_hidden=False
        (has_bullish, has_bearish, has_hidden_bullish, has_hidden_bearish) if include_hidden=True
    """
    n = len(candles)
    if n < period + 5:
        return (False, False, False, False) if include_hidden else (False, False)

    full_closes = [float(c[3]) for c in candles]
    rsi_series = calculate_rsi_series(full_closes, period=period)
    start_idx = max(period, n - lookback)

    # Detect swing lows and highs (3-bar fractal pivots)
    swing_lows = []
    swing_highs = []
    for i in range(start_idx + 1, n - 1):
        low_i = float(candles[i][2])
        high_i = float(candles[i][1])
        if low_i < float(candles[i - 1][2]) and low_i <= float(candles[i + 1][2]):
            swing_lows.append((i, low_i, float(rsi_series[i])))
        if high_i > float(candles[i - 1][1]) and high_i >= float(candles[i + 1][1]):
            swing_highs.append((i, high_i, float(rsi_series[i])))

    bull_div = False
    hidden_bull_div = False
    if len(swing_lows) >= 2:
        prev_low = swing_lows[-2]
        curr_low = swing_lows[-1]
        is_fresh = (n - 1 - curr_low[0]) <= 5
        # Regular Bullish: Price prints Lower Low while RSI prints Higher Low (within oversold/recovery band)
        if is_fresh and curr_low[1] < prev_low[1] and curr_low[2] > prev_low[2] and curr_low[2] <= 50.0:
            bull_div = True
        # Hidden Bullish: Price prints Higher Low (trend intact) while RSI resets to Lower Low (deep discount)
        if is_fresh and curr_low[1] > prev_low[1] and curr_low[2] < prev_low[2] and curr_low[2] <= 55.0:
            hidden_bull_div = True

    bear_div = False
    hidden_bear_div = False
    if len(swing_highs) >= 2:
        prev_high = swing_highs[-2]
        curr_high = swing_highs[-1]
        is_fresh = (n - 1 - curr_high[0]) <= 5
        # Regular Bearish: Price prints Higher High while RSI prints Lower High (within overbought/exhaustion band)
        if is_fresh and curr_high[1] > prev_high[1] and curr_high[2] < prev_high[2] and curr_high[2] >= 50.0:
            bear_div = True
        # Hidden Bearish: Price prints Lower High (downward trend intact) while RSI prints Higher High
        if is_fresh and curr_high[1] < prev_high[1] and curr_high[2] > prev_high[2] and curr_high[2] >= 45.0:
            hidden_bear_div = True

    if include_hidden:
        return bull_div, bear_div, hidden_bull_div, hidden_bear_div
    return bull_div, bear_div


def detect_hidden_rsi_divergences(
    candles: Sequence[Candle],
    period: int = 14,
    lookback: int = 30,
) -> Tuple[bool, bool]:
    """Detect Hidden Bullish and Hidden Bearish RSI Divergences (Institutional Trend Continuation).
    
    Returns:
        (has_hidden_bullish, has_hidden_bearish)
    """
    res = detect_rsi_divergences(candles, period=period, lookback=lookback, include_hidden=True)
    return res[2], res[3]


def check_recent_bullish_fvg(candles: Sequence[Candle], lookback: int = 5) -> bool:
    """Check if within the last `lookback` candles, a valid Bullish Fair Value Gap (FVG) exists.
    
    A Bullish FVG occurs when:
    current_low > high_2_bars_ago AND middle candle body is at least 50% of its high-low range.
    Subsequent candles must not have closed below high_2_bars_ago (which would invalidate the gap).
    Candle format: (open, high, low, close, volume)
    """
    n = len(candles)
    if n < 3:
        return False

    start_idx = max(2, n - lookback)
    for i in range(start_idx, n):
        curr_low = float(candles[i][2])
        high_2_bars_ago = float(candles[i - 2][1])

        mid_open = float(candles[i - 1][0])
        mid_high = float(candles[i - 1][1])
        mid_low = float(candles[i - 1][2])
        mid_close = float(candles[i - 1][3])

        mid_body = mid_close - mid_open  # Must be positive (green candle)
        mid_range = mid_high - mid_low

        if curr_low > high_2_bars_ago and mid_range > 0.0 and mid_body > 0.0:
            if (mid_body / mid_range) >= 0.5:
                # Invalidation guard: check that no subsequent candle closed below the gap boundary
                invalidated = False
                for k in range(i + 1, n):
                    if float(candles[k][3]) < high_2_bars_ago:
                        invalidated = True
                        break
                if not invalidated:
                    return True

    return False


def check_recent_bearish_fvg(candles: Sequence[Candle], lookback: int = 5) -> bool:
    """Check if within the last `lookback` candles, a valid Bearish Fair Value Gap (FVG) exists.
    
    A Bearish FVG occurs when:
    current_high < low_2_bars_ago AND middle candle is a red displacement candle 
    with body at least 50% of its high-low range.
    Subsequent candles must not have closed above low_2_bars_ago (which would invalidate the gap).
    Candle format: (open, high, low, close, volume)
    """
    n = len(candles)
    if n < 3:
        return False

    start_idx = max(2, n - lookback)
    for i in range(start_idx, n):
        curr_high = float(candles[i][1])
        low_2_bars_ago = float(candles[i - 2][2])

        mid_open = float(candles[i - 1][0])
        mid_high = float(candles[i - 1][1])
        mid_low = float(candles[i - 1][2])
        mid_close = float(candles[i - 1][3])

        mid_body = mid_open - mid_close  # Must be positive (red candle)
        mid_range = mid_high - mid_low

        if curr_high < low_2_bars_ago and mid_range > 0.0 and mid_body > 0.0:
            if (mid_body / mid_range) >= 0.5:
                # Invalidation guard: check that no subsequent candle closed above the gap boundary
                invalidated = False
                for k in range(i + 1, n):
                    if float(candles[k][3]) > low_2_bars_ago:
                        invalidated = True
                        break
                if not invalidated:
                    return True

    return False


def calculate_adx(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> float:
    """Calculate Wilder's Average Directional Index (ADX) over `period` bars.
    
    Returns:
        ADX value between 0.0 and 100.0. Values < 20 indicate chop/ranging, values >= 25 indicate strong trend.
    """
    n = len(closes)
    if n < period + 1:
        return 0.0

    tr = []
    pdm = []
    mdm = []
    for i in range(1, n):
        h, l, c_prev = float(highs[i]), float(lows[i]), float(closes[i - 1])
        tr.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
        up = float(highs[i]) - float(highs[i - 1])
        down = float(lows[i - 1]) - float(lows[i])
        pdm.append(up if up > down and up > 0.0 else 0.0)
        mdm.append(down if down > up and down > 0.0 else 0.0)

    if len(tr) < period:
        return 0.0

    tr_smooth = sum(tr[:period])
    pdm_smooth = sum(pdm[:period])
    mdm_smooth = sum(mdm[:period])
    dx_list = []

    for i in range(period, len(tr)):
        if i > period:
            tr_smooth = tr_smooth - (tr_smooth / period) + tr[i]
            pdm_smooth = pdm_smooth - (pdm_smooth / period) + pdm[i]
            mdm_smooth = mdm_smooth - (mdm_smooth / period) + mdm[i]

        pdi = 100.0 * (pdm_smooth / tr_smooth) if tr_smooth > 0.0 else 0.0
        mdi = 100.0 * (mdm_smooth / tr_smooth) if tr_smooth > 0.0 else 0.0
        di_sum = pdi + mdi
        dx = 100.0 * abs(pdi - mdi) / di_sum if di_sum > 0.0 else 0.0
        dx_list.append(dx)

    if not dx_list:
        return 0.0
    if len(dx_list) < period:
        return sum(dx_list) / len(dx_list)

    adx = sum(dx_list[:period]) / period
    for dx in dx_list[period:]:
        adx = (adx * (period - 1) + dx) / period

    return float(np.clip(adx, 0.0, 100.0))


def check_bullish_fvg_retest(
    candles: Sequence[Candle],
    lookback: int = 15,
) -> Tuple[bool, Optional[Dict[str, float]]]:
    """Check if the current candle is actively retesting (mitigating) a recent Bullish FVG.
    
    A Bullish FVG is formed between candle i-2 high and candle i low when middle candle is green displacement.
    A valid retest occurs when:
    1. The FVG was created within `lookback` candles (before the current candle).
    2. The FVG was not previously fully invalidated (no close below candle i-2 high).
    3. The current candle taps into the gap zone: current_low <= fvg_upper (candle i low)
       and holds above or inside the gap: current_close >= fvg_lower (candle i-2 high).
    
    Returns:
        (is_retesting, fvg_details_dict)
    """
    n = len(candles)
    if n < 4:
        return False, None

    curr_idx = n - 1
    curr_low = float(candles[curr_idx][2])
    curr_close = float(candles[curr_idx][3])

    start_idx = max(2, n - lookback)
    for i in range(start_idx, curr_idx):
        fvg_lower = float(candles[i - 2][1])  # Candle i-2 High
        fvg_upper = float(candles[i][2])      # Candle i Low

        mid_open = float(candles[i - 1][0])
        mid_high = float(candles[i - 1][1])
        mid_low = float(candles[i - 1][2])
        mid_close = float(candles[i - 1][3])

        mid_body = mid_close - mid_open
        mid_range = mid_high - mid_low

        # Valid Bullish FVG creation
        if fvg_upper > fvg_lower and mid_range > 0.0 and mid_body > 0.0:
            if (mid_body / mid_range) >= 0.5:
                # Invalidation guard: check that no candle between i and curr_idx closed below fvg_lower
                invalidated = False
                for k in range(i + 1, curr_idx):
                    if float(candles[k][3]) < fvg_lower:
                        invalidated = True
                        break

                if not invalidated:
                    # SMC Mitigation guard: an FVG is only mitigated if a prior candle closed beyond the 50% CE or completely filled the gap
                    ce = (fvg_upper + fvg_lower) / 2.0  # Consequent Encroachment (50%)
                    already_mitigated = any(
                        float(candles[k][3]) < ce or float(candles[k][2]) <= fvg_lower
                        for k in range(i + 1, curr_idx)
                    )
                    if not already_mitigated:
                        # Check if the CURRENT candle is tapping into the gap zone
                        if curr_low <= fvg_upper and curr_close >= fvg_lower:
                            return True, {
                                "upper": fvg_upper,
                                "lower": fvg_lower,
                                "ce": ce,
                                "age_bars": float(curr_idx - i),
                            }

    return False, None


def check_bearish_fvg_retest(
    candles: Sequence[Candle],
    lookback: int = 15,
) -> Tuple[bool, Optional[Dict[str, float]]]:
    """Check if the current candle is actively retesting (mitigating) a recent Bearish FVG.
    
    A Bearish FVG is formed between candle i-2 low and candle i high when middle candle is red displacement.
    A valid retest occurs when:
    1. The FVG was created within `lookback` candles (before the current candle).
    2. The FVG was not previously fully invalidated (no close above candle i-2 low).
    3. The current candle taps into the gap zone: current_high >= fvg_lower (candle i high)
       and holds below or inside the gap: current_close <= fvg_upper (candle i-2 low).
    
    Returns:
        (is_retesting, fvg_details_dict)
    """
    n = len(candles)
    if n < 4:
        return False, None

    curr_idx = n - 1
    curr_high = float(candles[curr_idx][1])
    curr_close = float(candles[curr_idx][3])

    start_idx = max(2, n - lookback)
    for i in range(start_idx, curr_idx):
        fvg_upper = float(candles[i - 2][2])  # Candle i-2 Low
        fvg_lower = float(candles[i][1])      # Candle i High

        mid_open = float(candles[i - 1][0])
        mid_high = float(candles[i - 1][1])
        mid_low = float(candles[i - 1][2])
        mid_close = float(candles[i - 1][3])

        mid_body = mid_open - mid_close  # Red candle
        mid_range = mid_high - mid_low

        # Valid Bearish FVG creation
        if fvg_lower < fvg_upper and mid_range > 0.0 and mid_body > 0.0:
            if (mid_body / mid_range) >= 0.5:
                # Invalidation guard: check that no candle between i and curr_idx closed above fvg_upper
                invalidated = False
                for k in range(i + 1, curr_idx):
                    if float(candles[k][3]) > fvg_upper:
                        invalidated = True
                        break

                if not invalidated:
                    # SMC Mitigation guard: an FVG is only mitigated if a prior candle closed beyond 50% CE or completely filled the gap
                    ce = (fvg_upper + fvg_lower) / 2.0  # Consequent Encroachment (50%)
                    already_mitigated = any(
                        float(candles[k][3]) > ce or float(candles[k][1]) >= fvg_upper
                        for k in range(i + 1, curr_idx)
                    )
                    if not already_mitigated:
                        # Check if the CURRENT candle is tapping into the gap zone
                        if curr_high >= fvg_lower and curr_close <= fvg_upper:
                            return True, {
                                "upper": fvg_upper,
                                "lower": fvg_lower,
                                "ce": ce,
                                "age_bars": float(curr_idx - i),
                            }

    return False, None


def format_price(price: float) -> str:
    """Format price dynamically with appropriate decimal precision based on magnitude.
    
    Sub-dollar prices (< $1.00) use 4 decimal places.
    Sub-cent prices (< $0.01) use 6 decimal places.
    Larger prices (>= $1.00) use 2 decimal places with comma thousand separators.
    """
    abs_p = abs(price)
    if abs_p == 0.0:
        return "$0.00"
    elif abs_p < 0.01:
        return f"${price:,.6f}"
    elif abs_p < 1.0:
        return f"${price:,.4f}"
    else:
        return f"${price:,.2f}"


def check_fvg_rejection_confirmation(candle: Sequence[Any], side: str) -> bool:
    """Check if a candle tapping an FVG exhibits buying/selling rejection or positive close.
    
    Prevents entering on aggressive displacement bars falling directly through the gap.
    Candle format: (open, high, low, close, volume)
    """
    raw_c = candle[1:] if len(candle) >= 6 else candle
    o, h, l, c = float(raw_c[0]), float(raw_c[1]), float(raw_c[2]), float(raw_c[3])
    bar_range = h - l
    if bar_range <= 0.0:
        return True

    side_upper = side.upper()
    if side_upper == "LONG":
        lower_wick = min(o, c) - l
        has_absorption_wick = (lower_wick / bar_range) >= 0.30
        close_in_upper_half = ((c - l) / bar_range) >= 0.50 and c >= o
        return has_absorption_wick or close_in_upper_half
    else:  # SHORT
        upper_wick = h - max(o, c)
        has_defense_wick = (upper_wick / bar_range) >= 0.30
        close_in_lower_half = ((h - c) / bar_range) >= 0.50 and c <= o
        return has_defense_wick or close_in_lower_half


def check_market_overextension(
    curr_close: float,
    ema50: float,
    atr: float,
    rsi: float,
    side: str,
) -> Tuple[bool, str]:
    """Check if price momentum is excessively overextended relative to EMA and RSI.
    
    Prevents buying at parabolic wave exhaustion or shorting into oversold climatic bottoms.
    """
    side_upper = side.upper()
    if side_upper == "LONG":
        if rsi > 75.0:
            return True, f"RSI Overbought ({rsi:.1f} > 75.0)"
        if rsi > 65.0 and atr > 0.0 and (curr_close - ema50) > (3.0 * atr):
            return True, f"Price Overextended ({curr_close - ema50:.2f} > 3.0x ATR {3.0 * atr:.2f})"
    else:  # SHORT
        if rsi < 25.0:
            return True, f"RSI Oversold ({rsi:.1f} < 25.0)"
        if rsi < 35.0 and atr > 0.0 and (ema50 - curr_close) > (3.0 * atr):
            return True, f"Price Overextended ({ema50 - curr_close:.2f} > 3.0x ATR {3.0 * atr:.2f})"

    return False, ""


def calculate_optimal_trade_setup(
    side: str,
    current_price: float,
    atr: float,
    fvg_info: Optional[Dict[str, float]] = None,
    macro_bias: str = "NEUTRAL",
) -> Dict[str, Any]:
    """Calculate institutional limit order trade staging parameters.
    
    Uses FVG 50% Consequent Encroachment (CE) for limit entry, structural invalidation
    stop loss, and asymmetric 1:2.5+ targets.
    """
    side_upper = side.upper()
    is_counter_trend = (
        (side_upper == "SHORT" and macro_bias == "BULLISH")
        or (side_upper == "LONG" and macro_bias == "BEARISH")
    )

    if side_upper == "LONG":
        if fvg_info and "ce" in fvg_info:
            limit_entry = float(fvg_info["ce"])
            fvg_lower = float(fvg_info.get("lower", limit_entry - atr))
            stop_loss = max(fvg_lower - (0.35 * atr), limit_entry * 0.01)
        else:
            limit_entry = current_price
            stop_loss = max(current_price - (1.5 * atr), current_price * 0.01)

        risk_dist = max(limit_entry - stop_loss, limit_entry * 0.003)
        stop_loss = limit_entry - risk_dist
        tp1 = limit_entry + (0.80 * risk_dist)
        tp2 = limit_entry + (2.75 * risk_dist)
    else:  # SHORT
        if fvg_info and "ce" in fvg_info:
            limit_entry = float(fvg_info["ce"])
            fvg_upper = float(fvg_info.get("upper", limit_entry + atr))
            stop_loss = fvg_upper + (0.35 * atr)
        else:
            limit_entry = current_price
            stop_loss = current_price + (1.5 * atr)

        risk_dist = max(stop_loss - limit_entry, limit_entry * 0.003)
        stop_loss = limit_entry + risk_dist
        tp1 = max(limit_entry - (0.80 * risk_dist), limit_entry * 0.01)
        tp2 = max(limit_entry - (2.75 * risk_dist), limit_entry * 0.01)

    risk_pct = (risk_dist / limit_entry) * 100.0 if limit_entry > 0 else 0.0
    reward_dist = abs(tp2 - limit_entry)
    rr_ratio = reward_dist / risk_dist if risk_dist > 0 else 2.75

    return {
        "side": side_upper,
        "limit_entry": limit_entry,
        "stop_loss": stop_loss,
        "risk_dist": risk_dist,
        "risk_pct": risk_pct,
        "tp1": tp1,
        "tp2": tp2,
        "rr_ratio": round(rr_ratio, 2),
        "macro_bias": macro_bias,
        "is_counter_trend": is_counter_trend,
        "recommendation": (
            "⚠️ HIGH-RISK COUNTER-TREND (Stand down or size down 50%)"
            if is_counter_trend
            else "🟢 MACRO-ALIGNED (Full institutional size approved)"
        ),
    }


def detect_swing_points(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Optional[Sequence[float]] = None,
    window: int = 2,
) -> List[Dict[str, Any]]:
    """Detect fractal swing highs and swing lows using a symmetric window (default 5-bar: 2 left, 1 pivot, 2 right).
    
    Zero-Lookahead Guarantee:
    A swing point occurring at candle index `i` is ONLY confirmed and usable at index `i + window`.
    Therefore, for any evaluation at index `t`, only swing points where `i + window <= t` are confirmed.
    
    Returns a chronologically ordered list of confirmed swing points:
    [
        {"index": i, "confirmed_index": i + window, "type": "HIGH"|"LOW", "price": float},
        ...
    ]
    """
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)
    n = len(h)
    if n < (2 * window + 1):
        return []

    swings: List[Dict[str, Any]] = []

    for i in range(window, n - window):
        pivot_high = True
        pivot_low = True

        for k in range(1, window + 1):
            if h[i] <= h[i - k] or h[i] <= h[i + k]:
                pivot_high = False
            if l[i] >= l[i - k] or l[i] >= l[i + k]:
                pivot_low = False

        confirmed_idx = i + window
        if pivot_high:
            swings.append({
                "index": i,
                "confirmed_index": confirmed_idx,
                "type": "HIGH",
                "price": float(h[i]),
            })
        if pivot_low:
            swings.append({
                "index": i,
                "confirmed_index": confirmed_idx,
                "type": "LOW",
                "price": float(l[i]),
            })

    swings.sort(key=lambda s: (s["confirmed_index"], s["index"]))
    return swings


def detect_market_structure(
    candles: Sequence[Candle],
    window: int = 2,
) -> Dict[str, Any]:
    """Analyze Market Structure (Fractal Swings, Break of Structure BOS, and Change of Character CHoCH).
    
    Evaluates:
    1. Confirmed Swing Highs & Swing Lows up to the current candle (index n - 1).
    2. Structural breaks (BOS):
       - Bullish BOS: Candle body close > Last confirmed Swing High in an ongoing Bullish trend.
       - Bearish BOS: Candle body close < Last confirmed Swing Low in an ongoing Bearish trend.
    3. Structural reversals (CHoCH):
       - Bullish CHoCH: In a Bearish trend, candle body close > Last confirmed Swing High.
       - Bearish CHoCH: In a Bullish trend, candle body close < Last confirmed Swing Low.
    4. Current trend state ("BULLISH", "BEARISH", or "RANGING").
    5. Mid-range status (is_in_range: True if current price is trapped inside un-broken swing points).
    """
    n = len(candles)
    default_state: Dict[str, Any] = {
        "current_trend": "RANGING",
        "last_swing_high": None,
        "last_swing_low": None,
        "active_swing_high": None,
        "active_swing_low": None,
        "last_event": None,
        "last_event_side": None,
        "last_event_bar_index": None,
        "bars_since_last_event": 999,
        "events": [],
        "is_in_range": False,
    }
    if n < (2 * window + 1):
        return default_state

    highs = [c[1] for c in candles]
    lows = [c[2] for c in candles]
    closes = [c[3] for c in candles]

    all_swings = detect_swing_points(highs, lows, closes, window=window)
    if not all_swings:
        return default_state

    current_trend = "RANGING"
    active_high: Optional[Dict[str, Any]] = None
    active_low: Optional[Dict[str, Any]] = None
    latest_high: Optional[Dict[str, Any]] = None
    latest_low: Optional[Dict[str, Any]] = None
    events: List[Dict[str, Any]] = []

    swings_by_conf: Dict[int, List[Dict[str, Any]]] = {}
    for sw in all_swings:
        swings_by_conf.setdefault(sw["confirmed_index"], []).append(sw)

    min_conf = all_swings[0]["confirmed_index"]
    for t in range(min_conf, n):
        if t in swings_by_conf:
            for sw in swings_by_conf[t]:
                if sw["type"] == "HIGH":
                    active_high = sw
                    latest_high = sw
                elif sw["type"] == "LOW":
                    active_low = sw
                    latest_low = sw

        close_t = closes[t]
        open_t = float(candles[t][0])
        high_t = float(candles[t][1])
        low_t = float(candles[t][2])
        c_range = high_t - low_t
        body = abs(close_t - open_t)
        body_ratio = (body / c_range) if c_range > 0 else 0.0

        # Bullish break test (Close > active swing high)
        if active_high is not None and close_t > active_high["price"]:
            if current_trend == "BEARISH":
                # Structural Reversal (CHoCH) requires decisive displacement (body >= 40% of range)
                if body_ratio >= 0.40:
                    current_trend = "BULLISH"
                    events.append({
                        "bar_index": t,
                        "event": "CHOCH",
                        "side": "BULLISH",
                        "broken_price": active_high["price"],
                        "close_price": close_t,
                    })
                    active_high = None
            elif current_trend in ("BULLISH", "RANGING"):
                current_trend = "BULLISH"
                events.append({
                    "bar_index": t,
                    "event": "BOS",
                    "side": "BULLISH",
                    "broken_price": active_high["price"],
                    "close_price": close_t,
                })
                active_high = None

        # Bearish break test (Close < active swing low)
        elif active_low is not None and close_t < active_low["price"]:
            if current_trend == "BULLISH":
                # Structural Reversal (CHoCH) requires decisive displacement (body >= 40% of range)
                if body_ratio >= 0.40:
                    current_trend = "BEARISH"
                    events.append({
                        "bar_index": t,
                        "event": "CHOCH",
                        "side": "BEARISH",
                        "broken_price": active_low["price"],
                        "close_price": close_t,
                    })
                    active_low = None
            elif current_trend in ("BEARISH", "RANGING"):
                current_trend = "BEARISH"
                events.append({
                    "bar_index": t,
                    "event": "BOS",
                    "side": "BEARISH",
                    "broken_price": active_low["price"],
                    "close_price": close_t,
                })
                active_low = None

    last_event = events[-1] if events else None
    current_bar_index = n - 1
    bars_since_last_event = (current_bar_index - last_event["bar_index"]) if last_event else 999

    curr_close = closes[-1]
    is_in_range = False
    if active_high is not None and active_low is not None:
        if active_low["price"] <= curr_close <= active_high["price"]:
            is_in_range = True

    return {
        "current_trend": current_trend,
        "last_swing_high": latest_high["price"] if latest_high else None,
        "last_swing_low": latest_low["price"] if latest_low else None,
        "active_swing_high": active_high["price"] if active_high else None,
        "active_swing_low": active_low["price"] if active_low else None,
        "last_event": last_event["event"] if last_event else None,
        "last_event_side": last_event["side"] if last_event else None,
        "last_event_bar_index": last_event["bar_index"] if last_event else None,
        "bars_since_last_event": bars_since_last_event,
        "events": events[-10:],
        "is_in_range": is_in_range,
    }


def check_market_structure_alignment(
    candles: Sequence[Candle],
    side: str = "LONG",
    max_bars_since_event: int = 16,
    window: int = 2,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Verify whether a candidate trade setup aligns with Market Structure (BOS / CHoCH).
    
    Rules for LONG:
    1. Market structure trend must be BULLISH (or last event was BULLISH CHOCH / BOS).
    2. A BULLISH BOS or BULLISH CHOCH must have occurred within the last `max_bars_since_event` bars (default: 16 bars = 4h on 15m).
    3. Price must NOT be trapped in a sideways range where bars_since_last_event > max_bars_since_event.
    
    Rules for SHORT:
    1. Market structure trend must be BEARISH (or last event was BEARISH CHOCH / BOS).
    2. A BEARISH BOS or BEARISH CHOCH must have occurred within the last `max_bars_since_event` bars.
    3. Price must NOT be trapped in a sideways range without recent downward displacement.
    """
    ms = detect_market_structure(candles, window=window)
    side_upper = side.upper()

    if ms["current_trend"] == "RANGING" and ms["last_event"] is None:
        return False, "Market structure is undefined / initial ranging regime", ms

    if side_upper == "LONG":
        if ms["last_event_side"] != "BULLISH":
            return False, f"Market structure opposed: Last event was {ms['last_event_side']} {ms['last_event']}", ms
        if ms["bars_since_last_event"] > max_bars_since_event:
            return False, f"Structural momentum expired: {ms['bars_since_last_event']} bars since Bullish {ms['last_event']} (max allowed: {max_bars_since_event})", ms
        return True, f"Aligned: Bullish {ms['last_event']} confirmed {ms['bars_since_last_event']} bars ago", ms

    elif side_upper == "SHORT":
        if ms["last_event_side"] != "BEARISH":
            return False, f"Market structure opposed: Last event was {ms['last_event_side']} {ms['last_event']}", ms
        if ms["bars_since_last_event"] > max_bars_since_event:
            return False, f"Structural momentum expired: {ms['bars_since_last_event']} bars since Bearish {ms['last_event']} (max allowed: {max_bars_since_event})", ms
        return True, f"Aligned: Bearish {ms['last_event']} confirmed {ms['bars_since_last_event']} bars ago", ms

    return False, f"Invalid side: {side}", ms


# ----------------------------------------------------
# PILLAR 2: FIBONACCI OPTIMAL TRADE ENTRY (OTE) & PRICING MATRIX
# ----------------------------------------------------

def calculate_fibonacci_levels(
    swing_low: float,
    swing_high: float,
    side: str = "LONG",
) -> Dict[str, float]:
    """Calculate institutional Fibonacci retracement levels for a defined dealing range.
    
    Levels:
    - 0.000: Origin of Retracement (Swing High for Longs, Swing Low for Shorts)
    - 0.500: Equilibrium (50% Fair Value Transition Line)
    - 0.618: Golden Ratio (Primary OTE Boundary)
    - 0.705: Institutional Sweet Spot (Exact Midpoint of OTE Zone)
    - 0.786: Deep OTE Boundary (Final Value Retracement Line)
    - 1.000: Invalidation Level (Swing Low for Longs, Swing High for Shorts)
    """
    if swing_high <= swing_low:
        raise ValueError(f"Invalid dealing range: swing_high ({swing_high}) must be > swing_low ({swing_low})")

    range_span = swing_high - swing_low
    side_upper = side.upper()

    if side_upper == "LONG":
        # Retracing downward from swing_high towards swing_low
        return {
            "0.0": float(swing_high),
            "0.5": float(swing_high - 0.500 * range_span),
            "0.618": float(swing_high - 0.618 * range_span),
            "0.705": float(swing_high - 0.705 * range_span),
            "0.786": float(swing_high - 0.786 * range_span),
            "1.0": float(swing_low),
            "range_span": float(range_span),
        }
    elif side_upper == "SHORT":
        # Retracing upward from swing_low towards swing_high
        return {
            "0.0": float(swing_low),
            "0.5": float(swing_low + 0.500 * range_span),
            "0.618": float(swing_low + 0.618 * range_span),
            "0.705": float(swing_low + 0.705 * range_span),
            "0.786": float(swing_low + 0.786 * range_span),
            "1.0": float(swing_high),
            "range_span": float(range_span),
        }
    else:
        raise ValueError(f"Invalid side: {side}. Must be 'LONG' or 'SHORT'")


def classify_discount_premium(
    price: float,
    swing_low: float,
    swing_high: float,
    side: str = "LONG",
) -> Tuple[str, float]:
    """Classify where a price sits within the dealing range according to institutional pricing theory.
    
    Returns:
    - zone: "PREMIUM", "SHALLOW_DISCOUNT", "OTE", "DEEP_DISCOUNT", or "INVALIDATED" for LONG
            "DISCOUNT", "SHALLOW_PREMIUM", "OTE", "DEEP_PREMIUM", or "INVALIDATED" for SHORT
    - retracement_ratio: Normalized ratio (0.0 to 1.0+)
    """
    if swing_high <= swing_low:
        return "UNDEFINED", 0.0

    range_span = swing_high - swing_low
    side_upper = side.upper()

    if side_upper == "LONG":
        retrace = (swing_high - price) / range_span
        ratio = round(float(retrace), 4)

        if ratio < 0.50:
            return "PREMIUM", ratio
        elif ratio < 0.618:
            return "SHALLOW_DISCOUNT", ratio
        elif ratio <= 0.786:
            return "OTE", ratio
        elif ratio <= 1.00:
            return "DEEP_DISCOUNT", ratio
        else:
            return "INVALIDATED", ratio

    elif side_upper == "SHORT":
        retrace = (price - swing_low) / range_span
        ratio = round(float(retrace), 4)

        if ratio < 0.50:
            return "DISCOUNT", ratio
        elif ratio < 0.618:
            return "SHALLOW_PREMIUM", ratio
        elif ratio <= 0.786:
            return "OTE", ratio
        elif ratio <= 1.00:
            return "DEEP_PREMIUM", ratio
        else:
            return "INVALIDATED", ratio

    return "UNDEFINED", 0.0


def get_active_dealing_range(
    candles: Sequence[Candle],
    lookback: int = 60,
    window: int = 2,
) -> Optional[Dict[str, Any]]:
    """Extract the active fractal dealing range (dominant Swing High and Swing Low) up to the current candle.
    
    Zero-Lookahead Guarantee:
    Only swing points confirmed by index n - 1 are utilized.
    """
    n = len(candles)
    if n < (2 * window + 1):
        return None

    # Slice lookback window
    start_idx = max(0, n - lookback)
    active_candles = candles[start_idx:]
    highs = [c[1] for c in active_candles]
    lows = [c[2] for c in active_candles]
    closes = [c[3] for c in active_candles]

    swings = detect_swing_points(highs, lows, closes, window=window)
    if not swings:
        return None

    confirmed_highs = [s for s in swings if s["type"] == "HIGH"]
    confirmed_lows = [s for s in swings if s["type"] == "LOW"]

    if not confirmed_highs or not confirmed_lows:
        return None

    # Use the most recent confirmed swing high and low
    last_high = confirmed_highs[-1]
    last_low = confirmed_lows[-1]

    swing_high = float(last_high["price"])
    swing_low = float(last_low["price"])

    if swing_high <= swing_low:
        # Fallback to absolute max/min of confirmed swings if ordering was inverted
        max_h = max(float(s["price"]) for s in confirmed_highs)
        min_l = min(float(s["price"]) for s in confirmed_lows)
        if max_h > min_l:
            swing_high = max_h
            swing_low = min_l
        else:
            return None

    range_span = swing_high - swing_low
    mid_price = (swing_high + swing_low) / 2.0
    range_span_pct = (range_span / mid_price) * 100.0 if mid_price > 0 else 0.0

    return {
        "swing_high": swing_high,
        "swing_low": swing_low,
        "high_index": last_high["index"] + start_idx,
        "low_index": last_low["index"] + start_idx,
        "range_span": range_span,
        "range_span_pct": range_span_pct,
    }


def check_fibonacci_ote_confluence(
    price: float,
    candles: Sequence[Candle],
    side: str = "LONG",
    lookback: int = 60,
    window: int = 2,
    min_range_pct: float = 0.50,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Verify whether a candidate entry price satisfies Pillar 2 Fibonacci OTE / Discount-Premium pricing.
    
    Gating Rules:
    - Longs:
      - REJECT if in PREMIUM (retracement < 50%) -> Buying at expensive retail pricing.
      - REJECT if INVALIDATED (retracement > 100%) -> Swing low broken.
      - ACCEPT if in OTE (0.618 - 0.786), SHALLOW_DISCOUNT (0.50 - 0.618), or DEEP_DISCOUNT (0.786 - 1.00).
    - Shorts:
      - REJECT if in DISCOUNT (retracement < 50%) -> Selling at cheap wholesale pricing.
      - REJECT if INVALIDATED (retracement > 100%) -> Swing high broken.
      - ACCEPT if in OTE (0.618 - 0.786), SHALLOW_PREMIUM (0.50 - 0.618), or DEEP_PREMIUM (0.786 - 1.00).
    """
    dr = get_active_dealing_range(candles, lookback=lookback, window=window)
    if not dr:
        return False, "Dealing range undefined (insufficient confirmed swing points)", {}

    if dr["range_span_pct"] < min_range_pct:
        return False, f"Dealing range too compressed ({dr['range_span_pct']:.2f}% < {min_range_pct:.2f}% threshold)", dr

    swing_low = dr["swing_low"]
    swing_high = dr["swing_high"]
    side_upper = side.upper()

    fibs = calculate_fibonacci_levels(swing_low, swing_high, side=side_upper)
    zone, ratio = classify_discount_premium(price, swing_low, swing_high, side=side_upper)

    info = {
        "dealing_range": dr,
        "fibonacci_levels": fibs,
        "zone": zone,
        "retracement_ratio": ratio,
        "is_ote": (zone == "OTE"),
    }

    if side_upper == "LONG":
        if zone == "PREMIUM":
            return False, f"Price {format_price(price)} is in PREMIUM ({ratio:.1%} retracement) - Longs forbidden in Premium", info
        elif zone == "INVALIDATED":
            return False, f"Price {format_price(price)} has broken below Swing Low {format_price(swing_low)} ({ratio:.1%})", info
        elif zone == "OTE":
            return True, f"🎯 OPTIMAL TRADE ENTRY: Price in OTE Golden Zone ({ratio:.1%} retracement, 0.618-0.786 OTE: {format_price(fibs['0.618'])} - {format_price(fibs['0.786'])})", info
        else:
            return True, f"Aligned: Price in {zone} ({ratio:.1%} retracement)", info

    elif side_upper == "SHORT":
        if zone == "DISCOUNT":
            return False, f"Price {format_price(price)} is in DISCOUNT ({ratio:.1%} retracement) - Shorts forbidden in Discount", info
        elif zone == "INVALIDATED":
            return False, f"Price {format_price(price)} has broken above Swing High {format_price(swing_high)} ({ratio:.1%})", info
        elif zone == "OTE":
            return True, f"🎯 OPTIMAL TRADE ENTRY: Price in OTE Golden Zone ({ratio:.1%} retracement, 0.618-0.786 OTE: {format_price(fibs['0.618'])} - {format_price(fibs['0.786'])})", info
        else:
            return True, f"Aligned: Price in {zone} ({ratio:.1%} retracement)", info

    return False, f"Invalid side: {side}", info


# =====================================================================
# Pillar 3: Institutional Order Blocks (OB) & Breaker Blocks (BB)
# =====================================================================

def detect_order_blocks(
    candles: Sequence[Candle],
    lookback: int = 100,
    min_displacement_ratio: float = 0.35,
) -> List[Dict[str, Any]]:
    """Detect Institutional Order Blocks (OB) across historical candles.
    
    An Order Block is the final candle of institutional counter-trend accumulation/distribution
    immediately preceding an aggressive directional displacement (breakout/imbalance).
    
    - Bullish Order Block (Demand):
      The last down-close candle (close < open) before an energetic upward displacement
      that breaks above the high of the down candle (or forms a Bullish FVG).
      Upper boundary (top): Body top (open of the down candle).
      Lower boundary (bottom): Candle low (invalidation floor).
      Mean Threshold (MT): Midpoint (top + bottom) / 2.0.
      
    - Bearish Order Block (Supply):
      The last up-close candle (close > open) before an energetic downward displacement
      that breaks below the low of the up candle (or forms a Bearish FVG).
      Upper boundary (top): Candle high (invalidation ceiling).
      Lower boundary (bottom): Body bottom (open of the up candle).
      Mean Threshold (MT): Midpoint (top + bottom) / 2.0.
    """
    n = len(candles)
    if n < 4:
        return []

    start_idx = max(0, n - lookback)
    order_blocks: List[Dict[str, Any]] = []

    for i in range(start_idx, n - 2):
        o_i = float(candles[i][0])
        h_i = float(candles[i][1])
        l_i = float(candles[i][2])
        c_i = float(candles[i][3])
        v_i = float(candles[i][4]) if len(candles[i]) > 4 else 0.0

        # Next candle i+1 (displacement candle)
        o_next = float(candles[i + 1][0])
        h_next = float(candles[i + 1][1])
        l_next = float(candles[i + 1][2])
        c_next = float(candles[i + 1][3])
        rng_next = h_next - l_next
        body_next = abs(c_next - o_next)
        disp_ratio = (body_next / rng_next) if rng_next > 0 else 0.0

        # Subsequent candle i+2 (for FVG formation check)
        h_sub = float(candles[i + 2][1])
        l_sub = float(candles[i + 2][2])

        # 1. Bullish Order Block Candidate: Down candle followed by upward displacement
        if c_i < o_i:
            has_break_disp = (c_next > o_next and c_next > h_i and disp_ratio >= min_displacement_ratio)
            has_fvg_disp = (l_sub > h_i)
            if has_break_disp or has_fvg_disp:
                top = max(o_i, c_i)
                bottom = l_i
                mt = (top + bottom) / 2.0

                status = "UNMITIGATED"
                mitigated_idx: Optional[int] = None
                violated_idx: Optional[int] = None
                touch_count = 0

                for k in range(i + 2, n):
                    l_k = float(candles[k][2])
                    h_k = float(candles[k][1])
                    c_k = float(candles[k][3])

                    if c_k < bottom:
                        status = "VIOLATED"
                        violated_idx = k
                        break
                    elif l_k <= top and h_k >= bottom:
                        touch_count += 1
                        if status == "UNMITIGATED":
                            status = "MITIGATED"
                            mitigated_idx = k

                order_blocks.append({
                    "index": i,
                    "type": "BULLISH_OB",
                    "top": top,
                    "bottom": bottom,
                    "high": h_i,
                    "low": l_i,
                    "mean_threshold": mt,
                    "status": status,
                    "mitigated_index": mitigated_idx,
                    "violated_index": violated_idx,
                    "displacement_ratio": disp_ratio,
                    "volume": v_i,
                    "touch_count": touch_count,
                })

        # 2. Bearish Order Block Candidate: Up candle followed by downward displacement
        elif c_i > o_i:
            has_break_disp = (c_next < o_next and c_next < l_i and disp_ratio >= min_displacement_ratio)
            has_fvg_disp = (h_sub < l_i)
            if has_break_disp or has_fvg_disp:
                top = h_i
                bottom = min(o_i, c_i)
                mt = (top + bottom) / 2.0

                status = "UNMITIGATED"
                mitigated_idx = None
                violated_idx = None
                touch_count = 0

                for k in range(i + 2, n):
                    l_k = float(candles[k][2])
                    h_k = float(candles[k][1])
                    c_k = float(candles[k][3])

                    if c_k > top:
                        status = "VIOLATED"
                        violated_idx = k
                        break
                    elif h_k >= bottom and l_k <= top:
                        touch_count += 1
                        if status == "UNMITIGATED":
                            status = "MITIGATED"
                            mitigated_idx = k

                order_blocks.append({
                    "index": i,
                    "type": "BEARISH_OB",
                    "top": top,
                    "bottom": bottom,
                    "high": h_i,
                    "low": l_i,
                    "mean_threshold": mt,
                    "status": status,
                    "mitigated_index": mitigated_idx,
                    "violated_index": violated_idx,
                    "displacement_ratio": disp_ratio,
                    "volume": v_i,
                    "touch_count": touch_count,
                })

    return order_blocks


def detect_breaker_blocks(
    candles: Sequence[Candle],
    order_blocks: Optional[List[Dict[str, Any]]] = None,
    lookback: int = 100,
) -> List[Dict[str, Any]]:
    """Detect Institutional Breaker Blocks (BB) arising from violated Order Blocks.
    
    A Breaker Block represents an institutional polarity shift:
    - Violated Bullish OB (Demand violated) -> Promotes to Bearish Breaker Block (New Supply).
    - Violated Bearish OB (Supply violated) -> Promotes to Bullish Breaker Block (New Demand).
    """
    n = len(candles)
    if n < 4:
        return []

    if order_blocks is None:
        order_blocks = detect_order_blocks(candles, lookback=lookback)

    breaker_blocks: List[Dict[str, Any]] = []

    for ob in order_blocks:
        if ob["status"] == "VIOLATED" and ob["violated_index"] is not None:
            v_idx = ob["violated_index"]
            top = ob["top"]
            bottom = ob["bottom"]
            mt = ob["mean_threshold"]

            if ob["type"] == "BULLISH_OB":
                # Failed Demand flips to Bearish Breaker (Resistance)
                bb_type = "BEARISH_BREAKER"
                status = "UNMITIGATED"
                mitigated_idx: Optional[int] = None
                touch_count = 0

                for k in range(v_idx + 1, n):
                    h_k = float(candles[k][1])
                    c_k = float(candles[k][3])

                    if c_k > top:
                        status = "INVALIDATED"
                        break
                    elif h_k >= bottom:
                        touch_count += 1
                        if status == "UNMITIGATED":
                            status = "MITIGATED"
                            mitigated_idx = k

                breaker_blocks.append({
                    "origin_index": ob["index"],
                    "violated_index": v_idx,
                    "type": bb_type,
                    "top": top,
                    "bottom": bottom,
                    "mean_threshold": mt,
                    "status": status,
                    "mitigated_index": mitigated_idx,
                    "touch_count": touch_count,
                })

            elif ob["type"] == "BEARISH_OB":
                # Failed Supply flips to Bullish Breaker (Support)
                bb_type = "BULLISH_BREAKER"
                status = "UNMITIGATED"
                mitigated_idx = None
                touch_count = 0

                for k in range(v_idx + 1, n):
                    l_k = float(candles[k][2])
                    c_k = float(candles[k][3])

                    if c_k < bottom:
                        status = "INVALIDATED"
                        break
                    elif l_k <= top:
                        touch_count += 1
                        if status == "UNMITIGATED":
                            status = "MITIGATED"
                            mitigated_idx = k

                breaker_blocks.append({
                    "origin_index": ob["index"],
                    "violated_index": v_idx,
                    "type": bb_type,
                    "top": top,
                    "bottom": bottom,
                    "mean_threshold": mt,
                    "status": status,
                    "mitigated_index": mitigated_idx,
                    "touch_count": touch_count,
                })

    return breaker_blocks


def check_order_block_confluence(
    price: float,
    candles: Sequence[Candle],
    side: str = "LONG",
    lookback: int = 100,
    tolerance_pct: float = 0.003,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Check whether a candidate trade price is tapping an active, aligned Order Block or Breaker Block.
    
    Returns:
        (has_confluence, reason_description, details_dict)
    """
    obs = detect_order_blocks(candles, lookback=lookback)
    bbs = detect_breaker_blocks(candles, order_blocks=obs, lookback=lookback)

    side_upper = side.upper()
    active_blocks = []

    if side_upper == "LONG":
        for ob in obs:
            if ob["type"] == "BULLISH_OB" and ob["status"] in ("UNMITIGATED", "MITIGATED"):
                active_blocks.append(ob)
        for bb in bbs:
            if bb["type"] == "BULLISH_BREAKER" and bb["status"] in ("UNMITIGATED", "MITIGATED"):
                active_blocks.append(bb)
    elif side_upper == "SHORT":
        for ob in obs:
            if ob["type"] == "BEARISH_OB" and ob["status"] in ("UNMITIGATED", "MITIGATED"):
                active_blocks.append(ob)
        for bb in bbs:
            if bb["type"] == "BEARISH_BREAKER" and bb["status"] in ("UNMITIGATED", "MITIGATED"):
                active_blocks.append(bb)

    matching_block: Optional[Dict[str, Any]] = None
    min_dist = float("inf")

    for block in active_blocks:
        top = block["top"] * (1.0 + tolerance_pct)
        bottom = block["bottom"] * (1.0 - tolerance_pct)

        if bottom <= price <= top:
            dist = abs(price - block["mean_threshold"])
            if dist < min_dist:
                min_dist = dist
                matching_block = block

    if matching_block is not None:
        block_type = matching_block["type"]
        mt = matching_block["mean_threshold"]
        is_mt_tap = abs(price - mt) / mt <= 0.003 if mt > 0 else False
        invalidation = matching_block["bottom"] if side_upper == "LONG" else matching_block["top"]
        touches = matching_block.get("touch_count", 1)
        freshness_score = 15.0 if touches <= 1 else (7.5 if touches == 2 else 0.0)

        details = {
            "has_confluence": True,
            "block_type": block_type,
            "block": matching_block,
            "mean_threshold": mt,
            "mean_threshold_tapped": is_mt_tap,
            "invalidation_level": invalidation,
            "all_active_blocks_count": len(active_blocks),
            "touch_count": touches,
            "freshness_score": freshness_score,
        }

        tag = "🎯 INSTITUTIONAL DEMAND" if side_upper == "LONG" else "🎯 INSTITUTIONAL SUPPLY"
        mt_str = " (Mean Threshold 50% Sweet Spot)" if is_mt_tap else ""
        touch_str = f" [Touch #{touches}: +{freshness_score:.1f} pts]"
        msg = (
            f"{tag}: Price {format_price(price)} tapping {block_type} zone "
            f"({format_price(matching_block['bottom'])} - {format_price(matching_block['top'])}){mt_str}{touch_str}"
        )
        return True, msg, details

    return False, "No active aligned Order Block or Breaker Block tapped at current price", {
        "has_confluence": False,
        "block_type": None,
        "block": None,
        "all_active_blocks_count": len(active_blocks),
        "touch_count": 0,
        "freshness_score": 0.0,
    }




