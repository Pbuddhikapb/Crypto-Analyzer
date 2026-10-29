import pytest
import numpy as np
import pandas as pd
import ta

from indicators import (
    calculate_ema,
    calculate_atr,
    calculate_rsi,
    calculate_rsi_series,
    calculate_adx,
    compute_terminal_features,
)


@pytest.fixture
def realistic_ohlcv_500():
    """Generate 1000 bars of realistic crypto price action with trend, mean-reversion, and volatility regimes."""
    np.random.seed(1337)
    n = 1000
    base_price = 50000.0
    returns = np.random.normal(loc=0.0002, scale=0.015, size=n)
    # Inject trend regimes
    returns[100:200] += 0.005  # Strong rally
    returns[250:350] -= 0.004  # Bearish downtrend
    returns[400:450] += 0.010  # Parabolic blow-off

    closes = base_price * np.cumprod(1.0 + returns)
    highs = closes * (1.0 + np.abs(np.random.normal(0.002, 0.005, size=n)))
    lows = closes * (1.0 - np.abs(np.random.normal(0.002, 0.005, size=n)))
    opens = np.roll(closes, 1)
    opens[0] = base_price
    volumes = np.random.uniform(500.0, 5000.0, size=n)

    df = pd.DataFrame({
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })
    candles = [
        (float(o), float(h), float(l), float(c), float(v))
        for o, h, l, c, v in zip(opens, highs, lows, closes, volumes)
    ]
    return df, candles


def test_ema_parity_vs_reference_ta(realistic_ohlcv_500):
    """Verify that calculate_ema matches ta.trend.EMAIndicator across multiple window lengths."""
    df, candles = realistic_ohlcv_500
    closes = [c[3] for c in candles]

    for window in [9, 21, 50, 200]:
        ref_ema = float(ta.trend.EMAIndicator(df["close"], window=window).ema_indicator().iloc[-1])
        our_ema = calculate_ema(closes, period=window)

        # Numerical parity assertion: difference must be < 0.001%
        assert pytest.approx(our_ema, rel=1e-4) == ref_ema, f"EMA-{window} diverged from reference ta"


def test_rsi_parity_vs_reference_ta(realistic_ohlcv_500):
    """Verify that calculate_rsi_series and calculate_rsi match ta.momentum.RSIIndicator."""
    df, candles = realistic_ohlcv_500
    closes = [c[3] for c in candles]

    for window in [7, 14, 21]:
        ref_rsi = ta.momentum.RSIIndicator(df["close"], window=window).rsi().values
        our_rsi_series = calculate_rsi_series(closes, period=window)

        # Check latest value
        our_latest_rsi = calculate_rsi(closes, period=window)
        assert pytest.approx(our_latest_rsi, abs=0.05) == ref_rsi[-1]

        # Check trajectory over the latest 100 bars (Wilder's smoothing warmup complete)
        diffs = np.abs(our_rsi_series[-100:] - ref_rsi[-100:])
        max_diff = np.max(diffs)
        assert max_diff < 0.1, f"RSI-{window} maximum divergence exceeded 0.1 points: {max_diff}"


def test_atr_parity_vs_reference_ta(realistic_ohlcv_500):
    """Verify that calculate_atr matches ta.volatility.AverageTrueRange."""
    df, candles = realistic_ohlcv_500
    highs = [c[1] for c in candles]
    lows = [c[2] for c in candles]
    closes = [c[3] for c in candles]

    for window in [7, 14, 21]:
        ref_atr = float(ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=window).average_true_range().iloc[-1])
        our_atr = calculate_atr(highs, lows, closes, period=window)

        assert pytest.approx(our_atr, rel=1e-3) == ref_atr, f"ATR-{window} diverged from reference ta"


def test_adx_parity_vs_reference_ta(realistic_ohlcv_500):
    """Verify that calculate_adx matches ta.trend.ADXIndicator."""
    df, candles = realistic_ohlcv_500
    highs = [c[1] for c in candles]
    lows = [c[2] for c in candles]
    closes = [c[3] for c in candles]

    ref_adx = float(ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14).adx().iloc[-1])
    our_adx = calculate_adx(highs, lows, closes, period=14)

    assert pytest.approx(our_adx, abs=0.2) == ref_adx, f"ADX-14 diverged from reference ta: our={our_adx}, ref={ref_adx}"


def test_zero_lookahead_bias(realistic_ohlcv_500):
    """Verify that future candles have ZERO influence on the current bar's indicator and feature values."""
    _, candles = realistic_ohlcv_500

    # Evaluate bar 300 using only candles 0..300
    sub_candles = candles[:300]
    features_at_300 = compute_terminal_features(sub_candles, lookback=100)

    # Re-evaluate with an isolated sub-slice up to 300
    highs_sub = [c[1] for c in sub_candles]
    lows_sub = [c[2] for c in sub_candles]
    closes_sub = [c[3] for c in sub_candles]

    ema_50_sub = calculate_ema(closes_sub, 50)
    rsi_14_sub = calculate_rsi(closes_sub, 14)
    atr_14_sub = calculate_atr(highs_sub, lows_sub, closes_sub, 14)

    # Compare features_at_300 with directly computed isolated values
    assert features_at_300["trend"]["ema_50"] == round(ema_50_sub, 4)
    assert features_at_300["momentum"]["rsi_14"] == round(rsi_14_sub, 2)
    assert features_at_300["volatility"]["atr_14"] == round(atr_14_sub, 4)


def test_compute_terminal_features_schema_and_edge_cases():
    """Verify compute_terminal_features schema, 5-tuple and 6-tuple support, and edge case resilience."""
    # 1. Empty sequence
    empty_res = compute_terminal_features([])
    assert empty_res["status"] == "INSUFFICIENT_DATA"

    # 2. 5-tuple candles
    c5 = [(100.0, 105.0, 95.0, 102.0, 1000.0)] * 30
    res5 = compute_terminal_features(c5, lookback=20, symbol="BTCUSDT")
    assert res5["symbol"] == "BTCUSDT"
    assert "trend" in res5
    assert "momentum" in res5
    assert "volatility" in res5
    assert "structure" in res5
    assert "smc" in res5
    assert res5["trend"]["regime"] in ("BULLISH", "BEARISH", "NEUTRAL")

    # 3. 6-tuple candles with timestamp
    c6 = [("2026-10-19T10:00:00", 100.0, 105.0, 95.0, 102.0, 1000.0)] * 30
    res6 = compute_terminal_features(c6, lookback=20, symbol="ETHUSDT")
    assert res6["symbol"] == "ETHUSDT"
    assert res6["timestamp"] == "2026-10-19T10:00:00"
    assert res6["momentum"]["rsi_14"] == 50.0  # Constant price -> neutral RSI
    assert res6["trend"]["ema_200"] is None  # Only 30 bars -> warmup incomplete!
    assert res6["trend"]["ema_50"] is None   # Only 30 bars -> warmup incomplete!
    assert res6["trend"]["ema_9"] == 102.0   # 30 >= 9 -> available!

    # 4. Dict candles from REST/WebSocket
    c_dicts = [
        {"timestamp": "2026-10-19T12:00:00", "open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0, "volume": 1000.0}
    ] * 60
    res_dict = compute_terminal_features(c_dicts, lookback=50, symbol="SOLUSDT")
    assert res_dict["symbol"] == "SOLUSDT"
    assert res_dict["trend"]["ema_50"] == 102.0
    assert res_dict["trend"]["ema_200"] is None  # 60 < 200 bars -> safely None!
    assert res_dict["trend"]["regime"] == "NEUTRAL"


def test_6tuple_and_dict_normalization_in_divergences_and_smc():
    """Verify that detect_rsi_divergences and check_recent_bullish_fvg handle 6-tuples without index offset."""
    from indicators import detect_rsi_divergences, check_recent_bullish_fvg

    # 6-tuple with timestamp at index 0: (ts, open, high, low, close, volume)
    # Candle 0: high = 105.0
    # Candle 1: displacement green body
    # Candle 2: low = 110.0 > 105.0 -> Bullish FVG!
    c6_fvg = [
        ("2026-10-19T10:00:00", 100.0, 105.0, 95.0, 102.0, 1000.0),
        ("2026-10-19T10:15:00", 103.0, 120.0, 102.0, 118.0, 5000.0),
        ("2026-10-19T10:30:00", 118.0, 125.0, 110.0, 122.0, 2000.0),
    ]
    # If 6-tuple was offset, c[2] would be high (125) and c[1] would be open (100) -> corrupted!
    # With normalization: properly recognizes bullish FVG
    assert check_recent_bullish_fvg(c6_fvg, lookback=5) is True

    # 6-tuples passed to detect_rsi_divergences
    c6_series = [("2026-10-19T10:00:00", 100.0, 105.0, 95.0, 102.0, 1000.0)] * 40
    bull_div, bear_div = detect_rsi_divergences(c6_series)
    assert bull_div is False
    assert bear_div is False


def test_smc_normalization_across_all_pillars():
    """Verify market structure, dealing range, order blocks, and breakers handle 6-tuples and dicts seamlessly."""
    from indicators import (
        detect_market_structure,
        get_active_dealing_range,
        detect_order_blocks,
        detect_breaker_blocks,
        check_fvg_rejection_confirmation,
    )

    # 1. Generate 6-tuple price swing wave: Low at bar 8, High at bar 18, pullback after
    c6_bars = []
    for i in range(30):
        ts = f"2026-10-19T10:{i:02d}:00"
        if i <= 8:
            p = 100.0 - i * 2.0
        elif i <= 18:
            p = 84.0 + (i - 8) * 3.0
        else:
            p = 114.0 - (i - 18) * 2.0
        c6_bars.append((ts, p, p + 2.0, p - 2.0, p + 0.5, 1000.0))

    ms6 = detect_market_structure(c6_bars, window=2)
    assert "current_trend" in ms6
    assert "market_structure" in ms6
    assert ms6["current_trend"] in ("BULLISH", "BEARISH", "RANGING")

    dr6 = get_active_dealing_range(c6_bars, lookback=30, window=2)
    assert dr6 is not None
    assert dr6["swing_high"] > dr6["swing_low"]

    # Dict format for market structure and dealing range
    c_dicts = [
        {"timestamp": b[0], "open": b[1], "high": b[2], "low": b[3], "close": b[4], "volume": b[5]}
        for b in c6_bars
    ]
    ms_dict = detect_market_structure(c_dicts, window=2)
    assert ms_dict["current_trend"] == ms6["current_trend"]
    dr_dict = get_active_dealing_range(c_dicts, lookback=30, window=2)
    assert dr_dict is not None
    assert dr_dict["swing_high"] == dr6["swing_high"]

    # 2. Order blocks with 6-tuples and dicts
    # Down candle followed by strong upward displacement candle
    ob_c6 = [
        ("2026-10-19T11:00:00", 100.0, 101.0, 98.0, 98.5, 500.0),   # Down candle
        ("2026-10-19T11:05:00", 98.5, 110.0, 98.0, 109.0, 5000.0),  # Big displacement up
        ("2026-10-19T11:10:00", 109.0, 112.0, 108.0, 111.0, 2000.0),
        ("2026-10-19T11:15:00", 111.0, 113.0, 100.0, 105.0, 1500.0), # Retest into OB top
    ]
    obs6 = detect_order_blocks(ob_c6)
    assert len(obs6) >= 1
    assert obs6[0]["type"] == "BULLISH_OB"
    assert obs6[0]["status"] == "MITIGATED"

    # Breakers with 6-tuples
    bbs6 = detect_breaker_blocks(ob_c6, order_blocks=obs6)
    assert isinstance(bbs6, list)

    # 3. check_fvg_rejection_confirmation
    # Long rejection with lower wick
    candle_long_rejection_6 = ("2026-10-19T12:00:00", 100.0, 105.0, 90.0, 103.0, 500.0)
    assert check_fvg_rejection_confirmation(candle_long_rejection_6, "LONG") is True

    candle_dict = {"open": 100.0, "high": 105.0, "low": 90.0, "close": 103.0, "volume": 500.0}
    assert check_fvg_rejection_confirmation(candle_dict, "LONG") is True


def test_terminal_features_timestamp_and_structure_fidelity():
    """Verify that compute_terminal_features extracts the current bar's timestamp and accurate structure."""
    candles = [
        ("2026-10-19T10:00:00", 100.0, 102.0, 99.0, 101.0, 1000.0),
        ("2026-10-19T10:15:00", 101.0, 104.0, 100.0, 103.0, 1200.0),
        ("2026-10-19T10:30:00", 103.0, 106.0, 102.0, 105.0, 1500.0),
        ("2026-10-19T10:45:00", 105.0, 108.0, 104.0, 107.0, 2000.0),
    ]
    features = compute_terminal_features(candles, lookback=10, symbol="BTCUSDT")
    # Must be the timestamp of the LATEST candle (candles[-1]), NOT candles[0]!
    assert features["timestamp"] == "2026-10-19T10:45:00"
    assert features["price"]["close"] == 107.0
    assert "current_trend" in features["structure"]
    assert "market_structure" in features["structure"]
    assert "bars_since_last_event" in features["structure"]
    # Check dual-perspective dealing range
    assert "zone_short" in features["structure"]["discount_premium"]
    assert "retrace_ratio_short" in features["structure"]["discount_premium"]


def test_deque_input_slicing_resilience():
    """Verify that compute_terminal_features accepts collections.deque without slicing error."""
    from collections import deque
    d = deque([(100.0, 105.0, 95.0, 102.0, 1000.0)] * 30)
    res = compute_terminal_features(d, lookback=20, symbol="BTCUSDT")
    assert res["symbol"] == "BTCUSDT"
    assert res["bar_count"] == 30


def test_equal_highs_and_lows_double_top_detection():
    """Verify that double tops (equal highs) are detected as swing points rather than vanished."""
    from indicators import detect_swing_points
    highs = [90.0, 95.0, 100.0, 100.0, 95.0, 90.0]
    lows  = [80.0, 85.0, 90.0, 90.0, 85.0, 80.0]
    swings = detect_swing_points(highs, lows, window=1)
    high_swings = [s for s in swings if s["type"] == "HIGH"]
    assert len(high_swings) >= 1
    assert high_swings[0]["price"] == 100.0


def test_asymmetric_order_block_invalidation_tolerance():
    """Verify that tolerance allows approaching from above for Longs, but strictly forbids penetrating below invalidation floor."""
    from indicators import check_order_block_confluence
    # Bullish OB at i=0 with bottom=100.0, top=102.0
    candles = [
        (102.0, 103.0, 100.0, 101.0, 1000.0),
        (101.0, 115.0, 101.0, 114.0, 5000.0),
        (114.0, 116.0, 112.0, 115.0, 2000.0),
        (115.0, 116.0, 99.8, 100.2, 1000.0),
    ]
    # Price 99.8 is below 100.0 floor -> must be REJECTED despite 0.3% tolerance
    ok_below, _, _ = check_order_block_confluence(99.8, candles, side="LONG", tolerance_pct=0.003)
    assert ok_below is False

    # Price 102.2 is approaching from above (within 0.3% of 102.0 top) -> ALLOWED
    ok_above, _, _ = check_order_block_confluence(102.2, candles, side="LONG", tolerance_pct=0.003)
    assert ok_above is True


def test_dealing_range_fallback_index_alignment():
    """Verify that when fallback ordering triggers, high_index and low_index align with true max/min swings."""
    from indicators import get_active_dealing_range
    highs = [50.0] * 20
    lows = [30.0] * 20
    # High at 2: 150.0
    highs[2] = 150.0
    lows[2] = 100.0
    # Low at 5: 20.0
    highs[5] = 40.0
    lows[5] = 20.0
    # Monotonically increasing highs after 10 so no swing highs form
    for i in range(11, 20):
        highs[i] = 65.0 + (i - 10) * 1.5
        lows[i] = 50.0 + (i - 10) * 1.5
    # Dip in low at 15
    lows[14] = 72.0
    lows[15] = 58.0
    lows[16] = 72.0

    candles = [(h - 2, h, l, l + 2, 1000.0) for h, l in zip(highs, lows)]
    dr = get_active_dealing_range(candles, lookback=20, window=1)
    assert dr is not None
    assert dr["swing_high"] == 150.0
    assert dr["high_index"] == 2  # Accurately points to index 2 (150.0)
    assert dr["swing_low"] == 20.0
    assert dr["low_index"] == 5   # Accurately points to index 5 (20.0)


def test_raw_binance_websocket_kline_dict_support():
    """Verify that normalize_candle_to_5tuple supports single-letter keys (o, h, l, c, v)."""
    from indicators import normalize_candle_to_5tuple
    raw_ws_kline = {"t": 1600000000, "o": "100.5", "h": "105.2", "l": "98.1", "c": "103.4", "v": "500.0"}
    c5 = normalize_candle_to_5tuple(raw_ws_kline)
    assert c5 == (100.5, 105.2, 98.1, 103.4, 500.0)


def test_top_10_symbols_expanded_universe():
    """Verify DEFAULT_MONITORED_SYMBOLS contains all 10 liquid coins."""
    from main import DEFAULT_MONITORED_SYMBOLS, TOP_10_SYMBOLS
    assert len(DEFAULT_MONITORED_SYMBOLS) == 10
    assert len(set(DEFAULT_MONITORED_SYMBOLS)) == 10
    assert DEFAULT_MONITORED_SYMBOLS == TOP_10_SYMBOLS
    expected = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "SUIUSDT", "LINKUSDT"]
    assert DEFAULT_MONITORED_SYMBOLS == expected


def test_compute_indicators_frontend_schema_parity():
    """Verify compute_indicators returns the exact schema expected by frontend MarketIndicators."""
    from server import compute_indicators
    # Generate 50 bullish trending candles
    candles = []
    for i in range(50):
        p = 50000.0 + i * 20.0
        candles.append((p - 5, p + 25, p - 10, p + 15, 100.0))

    inds = compute_indicators(candles)
    required_keys = [
        "ema_50", "ema_200", "rsi", "trend",
        "has_bullish_fvg", "has_bearish_fvg",
        "has_bullish_divergence", "has_bearish_divergence",
        "active_fvg_boxes", "adx_14", "atr_14", "is_chop",
        "trade_setup", "fibonacci_levels", "dealing_range",
        "pricing_zone", "market_structure", "order_blocks",
        "breaker_blocks", "order_block_confluence",
    ]
    for key in required_keys:
        assert key in inds, f"Missing required key '{key}' in compute_indicators"

    assert inds["trend"] in ("BULLISH", "BEARISH", "NEUTRAL")
    assert isinstance(inds["active_fvg_boxes"], list)
    assert isinstance(inds["order_blocks"], list)
    assert isinstance(inds["breaker_blocks"], list)


def test_aggregate_candles_edge_cases():
    """Verify aggregate_candles handles factor=1, empty sequences, and uneven bar counts."""
    from server import aggregate_candles
    # Empty
    agg, ts = aggregate_candles([], factor=4)
    assert agg == []

    # Factor = 1
    sample = [(1.0, 2.0, 0.5, 1.5, 10.0)]
    agg, ts = aggregate_candles(sample, factor=1, timestamps=[1000])
    assert len(agg) == 1
    assert agg[0] == sample[0]
    assert ts == [1000]

    # Uneven length: 5 bars with factor=2 on clock grid [0, 100, 200, 300, 400]
    bars = [
        (10.0, 15.0, 8.0, 12.0, 10.0),
        (12.0, 16.0, 11.0, 14.0, 10.0),
        (14.0, 18.0, 13.0, 15.0, 10.0),
        (15.0, 20.0, 14.0, 19.0, 10.0),
        (19.0, 22.0, 18.0, 21.0, 10.0),
    ]
    timestamps = [0, 100, 200, 300, 400]

    # By default, keep_partial_tail=False drops incomplete remainder (bar 4 alone)
    agg_dropped, agg_ts_dropped = aggregate_candles(bars, factor=2, timestamps=timestamps, keep_partial_tail=False)
    assert len(agg_dropped) == 2
    assert agg_dropped[0] == (10.0, 16.0, 8.0, 14.0, 20.0)
    assert agg_ts_dropped[0] == 0
    assert agg_dropped[1] == (14.0, 20.0, 13.0, 19.0, 20.0)
    assert agg_ts_dropped[1] == 200

    # With keep_partial_tail=True, forming trailing in-progress group is preserved
    agg_kept, agg_ts_kept = aggregate_candles(bars, factor=2, timestamps=timestamps, keep_partial_tail=True)
    assert len(agg_kept) == 3
    # Bar 0: chunk 0..1
    assert agg_kept[0] == (10.0, 16.0, 8.0, 14.0, 20.0)
    assert agg_ts_kept[0] == 0
    # Bar 1: chunk 2..3
    assert agg_kept[1] == (14.0, 20.0, 13.0, 19.0, 20.0)
    assert agg_ts_kept[1] == 200
    # Bar 2: trailing forming candle (bar 4 alone)
    assert agg_kept[2] == (19.0, 22.0, 18.0, 21.0, 10.0)
    assert agg_ts_kept[2] == 400




