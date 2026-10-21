import pytest
from indicators import (
    calculate_ema,
    calculate_atr,
    calculate_rsi,
    calculate_adx,
    check_recent_bullish_fvg,
    check_recent_bearish_fvg,
    check_bullish_fvg_retest,
    check_bearish_fvg_retest,
    detect_rsi_divergences,
    detect_hidden_rsi_divergences,
    detect_swing_points,
    detect_market_structure,
    check_market_structure_alignment,
    detect_order_blocks,
    check_order_block_confluence,
    format_price,
)


def test_calculate_ema():
    # Constant prices should yield constant EMA
    constant_closes = [100.0] * 20
    assert pytest.approx(calculate_ema(constant_closes, 10), rel=1e-5) == 100.0

    # Trending prices
    closes = list(range(1, 31))
    ema = calculate_ema(closes, 10)
    assert isinstance(ema, float)
    assert ema > 0.0
    # In an uptrend, EMA should lag the latest close
    assert ema < closes[-1]


def test_calculate_atr():
    # Constant range candles: high=110, low=90, close=100 -> True range is 20
    highs = [110.0] * 20
    lows = [90.0] * 20
    closes = [100.0] * 20
    atr = calculate_atr(highs, lows, closes, period=14)
    assert pytest.approx(atr, rel=1e-5) == 20.0


def test_calculate_rsi():
    # Monotonically increasing prices -> RSI should be 100.0
    strictly_increasing = [float(x) for x in range(1, 25)]
    rsi_high = calculate_rsi(strictly_increasing, period=14)
    assert pytest.approx(rsi_high, rel=1e-4) == 100.0

    # Mixed series
    mixed_closes = [
        100.0, 102.0, 101.0, 104.0, 103.0, 105.0, 107.0, 106.0,
        108.0, 107.0, 109.0, 111.0, 110.0, 112.0, 114.0, 113.0,
        115.0, 117.0, 116.0, 118.0
    ]
    rsi_mixed = calculate_rsi(mixed_closes, period=14)
    assert 0.0 <= rsi_mixed <= 100.0


def test_check_recent_bullish_fvg():
    # Format: (open, high, low, close, volume)
    # Candle 0: high = 105.0
    # Candle 1 (middle): open = 103.0, high = 120.0, low = 102.0, close = 118.0
    #                    body = 15.0, range = 18.0 -> body/range = 83.3% >= 50%
    # Candle 2 (current): low = 110.0 -> 110.0 > 105.0
    bullish_candles = [
        (100.0, 105.0, 95.0, 102.0, 1000.0),
        (103.0, 120.0, 102.0, 118.0, 5000.0),
        (118.0, 125.0, 110.0, 122.0, 2000.0),
    ]
    assert check_recent_bullish_fvg(bullish_candles, lookback=5) is True

    # No gap: current low (104.0) <= high_2_bars_ago (105.0)
    no_gap_candles = [
        (100.0, 105.0, 95.0, 102.0, 1000.0),
        (103.0, 120.0, 102.0, 118.0, 5000.0),
        (118.0, 125.0, 104.0, 122.0, 2000.0),
    ]
    assert check_recent_bullish_fvg(no_gap_candles, lookback=5) is False

    # Small body middle candle (doji / body < 50% of range):
    # open = 111.0, close = 112.0 -> body = 1.0, range = 18.0 -> 1/18 = 5.5% < 50%
    small_body_candles = [
        (100.0, 105.0, 95.0, 102.0, 1000.0),
        (111.0, 120.0, 102.0, 112.0, 5000.0),
        (118.0, 125.0, 110.0, 122.0, 2000.0),
    ]
    assert check_recent_bullish_fvg(small_body_candles, lookback=5) is False


def test_check_recent_bearish_fvg():
    # Format: (open, high, low, close, volume)
    # Candle 0: low = 100.0
    # Candle 1 (middle): open = 101.0, high = 102.0, low = 80.0, close = 82.0
    #                    body = 19.0, range = 22.0 -> body/range = 86.4% >= 50%
    # Candle 2 (current): high = 95.0 -> 95.0 < 100.0
    bearish_candles = [
        (105.0, 108.0, 100.0, 102.0, 1000.0),
        (101.0, 102.0, 80.0, 82.0, 5000.0),
        (82.0, 95.0, 78.0, 85.0, 2000.0),
    ]
    assert check_recent_bearish_fvg(bearish_candles, lookback=5) is True

    # No gap: current high (101.0) >= low_2_bars_ago (100.0)
    no_gap_candles = [
        (105.0, 108.0, 100.0, 102.0, 1000.0),
        (101.0, 102.0, 80.0, 82.0, 5000.0),
        (82.0, 101.0, 78.0, 85.0, 2000.0),
    ]
    assert check_recent_bearish_fvg(no_gap_candles, lookback=5) is False

    # Small body middle candle (body < 50% range):
    # open = 91.0, close = 90.0 -> body = 1.0, range = 22.0 -> 1/22 = 4.5% < 50%
    small_body_candles = [
        (105.0, 108.0, 100.0, 102.0, 1000.0),
        (91.0, 102.0, 80.0, 90.0, 5000.0),
        (82.0, 95.0, 78.0, 85.0, 2000.0),
    ]
    assert check_recent_bearish_fvg(small_body_candles, lookback=5) is False

    # Green middle candle: open = 82.0, close = 95.0 (cannot be bearish displacement)
    green_middle_candles = [
        (105.0, 108.0, 100.0, 102.0, 1000.0),
        (82.0, 102.0, 80.0, 95.0, 5000.0),
        (95.0, 98.0, 78.0, 85.0, 2000.0),
    ]
    assert check_recent_bearish_fvg(green_middle_candles, lookback=5) is False


def test_fvg_invalidated_when_subsequent_candle_closes_through_gap():
    """Verify that an FVG is rejected if subsequent candles close through the gap boundary."""
    # Bullish FVG setup on candles 0-2 (demand gap from high 100.0 to low 105.0)
    bull_candles = [
        (95.0, 100.0, 92.0, 98.0, 1000.0),       # C0: high = 100.0
        (98.0, 120.0, 98.0, 119.0, 5000.0),      # C1: green displacement
        (119.0, 125.0, 105.0, 122.0, 2000.0),    # C2: low = 105.0 > 100.0 -> FVG
        (122.0, 122.0, 90.0, 92.0, 4000.0),      # C3: Crash closing at 92.0 < 100.0 (VIOLATED!)
    ]
    assert check_recent_bullish_fvg(bull_candles, lookback=5) is False

    # Bearish FVG setup on candles 0-2 (supply gap from low 100.0 to high 95.0)
    bear_candles = [
        (105.0, 108.0, 100.0, 102.0, 1000.0),    # C0: low = 100.0
        (101.0, 102.0, 80.0, 82.0, 5000.0),      # C1: red displacement
        (82.0, 95.0, 78.0, 85.0, 2000.0),        # C2: high = 95.0 < 100.0 -> FVG
        (85.0, 110.0, 84.0, 108.0, 4000.0),      # C3: Surge closing at 108.0 > 100.0 (VIOLATED!)
    ]
    assert check_recent_bearish_fvg(bear_candles, lookback=5) is False


def test_detect_bullish_and_bearish_rsi_divergences():
    """Verify detection of Bullish and Bearish RSI Divergences."""
    # 1. Synthetic Bullish Divergence:
    # 25 candles baseline: downtrend
    candles = []
    price = 100.0
    for i in range(15):
        price -= 1.0
        candles.append((price + 0.5, price + 1.0, price - 0.5, price, 1000.0))

    # Swing Low 1 at price ~80
    candles.append((82.0, 83.0, 80.0, 81.0, 1000.0))
    # Bounce
    candles.append((81.0, 86.0, 81.0, 85.0, 1000.0))
    candles.append((85.0, 87.0, 84.0, 86.0, 1000.0))
    # Swing Low 2 at price 78 (Lower Low in price, but shallower descent -> Higher Low in RSI)
    candles.append((85.0, 85.0, 78.0, 80.0, 1000.0))
    # Reversal bar up
    candles.append((80.0, 84.0, 80.0, 83.0, 1000.0))

    bull_div, bear_div = detect_rsi_divergences(candles, period=14, lookback=30)
    assert bull_div is True
    assert bear_div is False


def test_format_price_precision_tiers():
    """Verify format_price scales decimal precision according to price magnitude."""
    # 1. Zero
    assert format_price(0.0) == "$0.00"

    # 2. Institutional / Large assets (>= $1000)
    assert format_price(82545.421) == "$82,545.42"
    assert format_price(1000.0) == "$1,000.00"

    # 3. Mid-range assets ($1.00 to $999.99)
    assert format_price(104.943) == "$104.94"
    assert format_price(1.5) == "$1.50"

    # 4. Sub-dollar assets ($0.01 to $0.9999) - 4 decimals
    assert format_price(0.12345) == "$0.1235"
    assert format_price(0.3456) == "$0.3456"
    assert format_price(0.0800) == "$0.0800"

    # 5. Sub-cent micro-assets (< $0.01) - 6 decimals
    assert format_price(0.0004567) == "$0.000457"
    assert format_price(0.000012) == "$0.000012"


def test_calculate_adx_trend_vs_chop():
    """Verify ADX distinguishes trending markets from sideways chop."""
    # Strong persistent uptrend: price climbing steadily
    closes_trend = [100.0 + (i * 2.0) for i in range(50)]
    highs_trend = [c + 1.5 for c in closes_trend]
    lows_trend = [c - 1.0 for c in closes_trend]
    adx_trend = calculate_adx(highs_trend, lows_trend, closes_trend, period=14)
    assert adx_trend >= 50.0  # Strong trend

    # Sideways oscillating chop: alternating +1 and -1 around 100
    closes_chop = [100.0 + (1.0 if i % 2 == 0 else -1.0) for i in range(50)]
    highs_chop = [c + 1.0 for c in closes_chop]
    lows_chop = [c - 1.0 for c in closes_chop]
    adx_chop = calculate_adx(highs_chop, lows_chop, closes_chop, period=14)
    assert adx_chop < 15.0  # Extreme chop (well below the 20.0 cutoff)


def test_check_bullish_fvg_retest():
    """Verify Bullish FVG retest only triggers when price pulls back into the gap."""
    # Bar 0: high = 105.0
    # Bar 1 (green displacement): open = 104.0, high = 120.0, low = 103.0, close = 118.0
    # Bar 2 (creates gap): open = 118.0, high = 122.0, low = 110.0, close = 121.0
    # Gap zone: [105.0, 110.0]
    candles = [
        (100.0, 105.0, 99.0, 104.0, 100.0),
        (104.0, 120.0, 103.0, 118.0, 500.0),
        (118.0, 122.0, 110.0, 121.0, 300.0),
    ]

    # Bar 3: Price flies higher to 125.0 (extended, NOT tapping the gap)
    candles.append((121.0, 125.0, 115.0, 124.0, 200.0))
    is_retest, details = check_bullish_fvg_retest(candles, lookback=10)
    assert is_retest is False
    assert details is None

    # Bar 4: Price pulls back and taps into the gap: low = 107.0 (inside [105.0, 110.0]), close = 108.0
    candles.append((124.0, 124.0, 107.0, 108.0, 250.0))
    is_retest, details = check_bullish_fvg_retest(candles, lookback=10)
    assert is_retest is True
    assert details is not None
    assert details["upper"] == 110.0
    assert details["lower"] == 105.0
    assert details["ce"] == 107.5


def test_check_bearish_fvg_retest():
    """Verify Bearish FVG retest only triggers when price rallies into the gap."""
    # Bar 0: low = 100.0
    # Bar 1 (red displacement): open = 99.0, high = 100.0, low = 80.0, close = 82.0
    # Bar 2 (creates gap): open = 82.0, high = 92.0, low = 78.0, close = 80.0
    # Gap zone: [92.0, 100.0]
    candles = [
        (102.0, 104.0, 100.0, 101.0, 100.0),
        (99.0, 100.0, 80.0, 82.0, 500.0),
        (82.0, 92.0, 78.0, 80.0, 300.0),
    ]

    # Bar 3: Price drops lower to 75.0 (extended, NOT tapping the gap)
    candles.append((80.0, 81.0, 75.0, 76.0, 200.0))
    is_retest, details = check_bearish_fvg_retest(candles, lookback=10)
    assert is_retest is False

    # Bar 4: Price bounces up into the gap: high = 95.0 (inside [92.0, 100.0]), close = 94.0
    candles.append((76.0, 95.0, 76.0, 94.0, 250.0))
    is_retest, details = check_bearish_fvg_retest(candles, lookback=10)
    assert is_retest is True
    assert details is not None
    assert details["upper"] == 100.0
    assert details["lower"] == 92.0
    assert details["ce"] == 96.0


def test_detect_swing_points_zero_lookahead():
    """Verify that a 5-bar fractal pivot at index i is confirmed only at index i + 2."""
    # Build 7 bars with a distinct peak at index 2 (price 150)
    highs = [100.0, 110.0, 150.0, 120.0, 105.0, 100.0, 95.0]
    lows = [90.0, 95.0, 100.0, 95.0, 90.0, 85.0, 80.0]

    swings = detect_swing_points(highs, lows, window=2)
    assert len(swings) >= 1
    high_swings = [s for s in swings if s["type"] == "HIGH"]
    assert len(high_swings) == 1
    assert high_swings[0]["index"] == 2
    assert high_swings[0]["price"] == 150.0
    assert high_swings[0]["confirmed_index"] == 4  # index 2 + window 2 = 4 (zero lookahead!)


def test_detect_bullish_bos_on_body_close():
    """Verify that BOS triggers on candle body close above swing high, but NOT on a wick sweep."""
    candles = [
        (100.0, 105.0, 95.0, 100.0, 100.0),
        (100.0, 110.0, 98.0, 108.0, 100.0),
        (108.0, 130.0, 105.0, 125.0, 100.0),  # index 2: Swing High at 130.0
        (125.0, 126.0, 110.0, 112.0, 100.0),
        (112.0, 115.0, 105.0, 110.0, 100.0),  # index 4: confirms Swing High at index 2
    ]

    # Bar 5: Wick sweeps to 132.0, but closes at 128.0 (below 130.0) -> Liquidity Sweep, NOT BOS!
    candles.append((110.0, 132.0, 108.0, 128.0, 200.0))
    ms = detect_market_structure(candles, window=2)
    assert ms["last_event"] != "BOS"

    # Bar 6: Closes at 135.0 (strictly above 130.0) -> True Bullish BOS!
    candles.append((128.0, 136.0, 127.0, 135.0, 300.0))
    ms2 = detect_market_structure(candles, window=2)
    assert ms2["last_event"] == "BOS"
    assert ms2["last_event_side"] == "BULLISH"
    assert ms2["current_trend"] == "BULLISH"
    assert ms2["bars_since_last_event"] == 0


def test_detect_bullish_choch_trend_reversal():
    """Verify that when in a downtrend, closing above the last swing high triggers CHOCH and flips trend to BULLISH."""
    # 1. Swing High at index 2 (220.0, confirmed at index 4)
    # 2. Swing Low at index 4 (140.0, confirmed at index 6)
    # 3. Lower High at index 6 (175.0, confirmed at index 8)
    # 4. Bar 7 breaks Low 140.0 -> Bearish BOS!
    candles = [
        (190.0, 195.0, 185.0, 190.0, 100.0),
        (190.0, 198.0, 188.0, 195.0, 100.0),
        (195.0, 220.0, 190.0, 215.0, 100.0),  # index 2: Swing High at 220.0
        (210.0, 180.0, 160.0, 165.0, 100.0),
        (165.0, 165.0, 140.0, 145.0, 100.0),  # index 4: Swing Low at 140.0
        (145.0, 160.0, 145.0, 155.0, 100.0),
        (155.0, 175.0, 150.0, 170.0, 100.0),  # index 6: Lower High at 175.0
        (170.0, 150.0, 120.0, 125.0, 100.0),  # index 7: closes 125 < 140 -> Bearish BOS!
        (125.0, 140.0, 110.0, 115.0, 100.0),  # index 8: confirms High at index 6 (175.0)
    ]
    ms = detect_market_structure(candles, window=2)
    assert ms["current_trend"] == "BEARISH"
    assert ms["last_event"] == "BOS"
    assert ms["last_event_side"] == "BEARISH"

    # Bar 9: Explosive reversal candle closes at 182.0 (> 175.0) -> Bullish CHOCH!
    candles.append((115.0, 185.0, 115.0, 182.0, 500.0))
    ms_rev = detect_market_structure(candles, window=2)
    assert ms_rev["last_event"] == "CHOCH"
    assert ms_rev["last_event_side"] == "BULLISH"
    assert ms_rev["current_trend"] == "BULLISH"


def test_check_market_structure_alignment_gate():
    """Verify that check_market_structure_alignment blocks stale ranges and approves fresh momentum."""
    # Setup candles with a BOS
    candles = [
        (100.0, 105.0, 95.0, 100.0, 100.0),
        (100.0, 110.0, 98.0, 108.0, 100.0),
        (108.0, 130.0, 105.0, 125.0, 100.0),  # index 2: Swing High 130
        (125.0, 126.0, 110.0, 112.0, 100.0),
        (112.0, 115.0, 105.0, 110.0, 100.0),
        (110.0, 136.0, 108.0, 135.0, 200.0),  # index 5: BOS!
    ]

    # At index 5: bars_since_last_event == 0 <= 16 -> Approved
    ok, reason, _ = check_market_structure_alignment(candles, side="LONG", max_bars_since_event=16)
    assert ok is True
    assert "Aligned: Bullish BOS" in reason

    # Bearish check should be opposed
    ok_short, reason_short, _ = check_market_structure_alignment(candles, side="SHORT", max_bars_since_event=16)
    assert ok_short is False
    assert "opposed" in reason_short.lower()

    # If 20 bars of flat chop elapse without fresh structure, momentum expires
    for _ in range(20):
        candles.append((135.0, 136.0, 134.0, 135.0, 50.0))

    ok_stale, reason_stale, _ = check_market_structure_alignment(candles, side="LONG", max_bars_since_event=16)
    assert ok_stale is False
    assert "Structural momentum expired" in reason_stale


def test_detect_hidden_rsi_divergences_continuation():
    """Verify detection of Hidden Bullish RSI Divergence (Trend Continuation)."""
    # Baseline: steady uptrend
    candles = []
    p = 100.0
    for i in range(20):
        p += 0.5
        candles.append((p, p + 1.0, p - 0.5, p, 1000.0))

    # Swing Low 1: mild dip to 108.0 (RSI ~50)
    candles.append((110.0, 110.0, 108.0, 108.5, 1000.0))

    # Strong rally pushing price higher
    for i in range(8):
        p = 110.0 + i * 3.0
        candles.append((p, p + 2.0, p - 1.0, p + 1.0, 1000.0))

    # Consecutive red pullbacks bringing RSI down deeply
    for i in range(6):
        p = 131.0 - i * 2.0
        candles.append((p, p + 0.5, p - 2.0, p - 1.5, 1000.0))

    # Swing Low 2 at 116.0 (Higher Low: 116.0 > 108.0, but Lower Low in RSI)
    candles.append((119.0, 119.0, 116.0, 116.5, 1000.0))
    # Bounce candle
    candles.append((116.5, 122.0, 116.5, 121.0, 1000.0))

    # Hidden divergence detection
    hidden_bull, hidden_bear = detect_hidden_rsi_divergences(candles, period=14, lookback=35)
    assert hidden_bull is True
    assert hidden_bear is False

    # Check 4-element return when include_hidden=True
    res_all = detect_rsi_divergences(candles, period=14, lookback=35, include_hidden=True)
    assert len(res_all) == 4
    assert res_all == (False, False, True, False)

    # Check backward compatibility (default 2-element return)
    res_default = detect_rsi_divergences(candles, period=14, lookback=35)
    assert len(res_default) == 2
    assert res_default == (False, False)


def test_fvg_retest_allows_multibar_ce_penetration():
    """Verify that a shallow touch of the FVG outer boundary does not disqualify subsequent deep 50% CE retests."""
    # Bullish FVG creation: gap zone [105.0, 110.0], CE = 107.5
    candles = [
        (100.0, 105.0, 99.0, 104.0, 100.0),
        (104.0, 120.0, 103.0, 118.0, 500.0),
        (118.0, 122.0, 110.0, 121.0, 300.0),
    ]

    # Bar 3: Shallow dip touching outer edge (low = 109.5 <= 110.0, close = 111.0 > CE 107.5)
    candles.append((121.0, 122.0, 109.5, 111.0, 200.0))

    # Bar 4: Deep retest candle into the lower half (low = 106.0, close = 107.0 >= lower bound 105.0)
    # With the fix, Bar 3's shallow touch does NOT mark the FVG as already mitigated!
    candles.append((111.0, 111.0, 106.0, 107.0, 250.0))
    is_retest, details = check_bullish_fvg_retest(candles, lookback=10)
    assert is_retest is True
    assert details is not None
    assert details["upper"] == 110.0
    assert details["lower"] == 105.0
    assert details["ce"] == 107.5

    # Bar 5: True mitigation candle closing through 50% CE (close = 106.0 < 107.5)
    candles.append((107.0, 107.5, 105.5, 106.0, 300.0))

    # Bar 6: Subsequent pullback should now be rejected as already mitigated
    candles.append((106.0, 108.0, 105.5, 106.5, 150.0))
    is_retest6, details6 = check_bullish_fvg_retest(candles, lookback=10)
    assert is_retest6 is False
    assert details6 is None


def test_choch_requires_displacement():
    """Verify that CHoCH reversals reject doji fakeouts and require >= 40% body displacement."""
    # Downtrend setup:
    # 1. Swing High at index 2 (220.0, confirmed at index 4)
    # 2. Swing Low at index 4 (140.0, confirmed at index 6)
    # 3. Lower High at index 6 (175.0, confirmed at index 8)
    # 4. Bar 7 breaks Low 140.0 -> Bearish BOS! Trend is BEARISH
    candles = [
        (190.0, 195.0, 185.0, 190.0, 100.0),
        (190.0, 198.0, 188.0, 195.0, 100.0),
        (195.0, 220.0, 190.0, 215.0, 100.0),  # index 2: Swing High at 220.0
        (210.0, 180.0, 160.0, 165.0, 100.0),
        (165.0, 165.0, 140.0, 145.0, 100.0),  # index 4: Swing Low at 140.0
        (145.0, 160.0, 145.0, 155.0, 100.0),
        (155.0, 175.0, 150.0, 170.0, 100.0),  # index 6: Lower High at 175.0
        (170.0, 150.0, 120.0, 125.0, 100.0),  # index 7: closes 125 < 140 -> Bearish BOS!
        (125.0, 140.0, 110.0, 115.0, 100.0),  # index 8: confirms High at index 6 (175.0)
    ]

    ms = detect_market_structure(candles, window=2)
    assert ms["current_trend"] == "BEARISH"
    assert ms["last_event"] == "BOS"

    # Case A: Bar 9 closes at 176.0 (> 175.0 swing high) but is a Doji (body = 0.5, range = 12.0 -> body_ratio = 4.1% < 40%)
    doji_candles = list(candles)
    doji_candles.append((175.5, 182.0, 170.0, 176.0, 100.0))
    ms_doji = detect_market_structure(doji_candles, window=2)
    assert ms_doji["current_trend"] == "BEARISH"
    assert ms_doji["last_event"] == "BOS"

    # Case B: Bar 9 closes at 182.0 with decisive displacement (body = 12.0, range = 16.0 -> body_ratio = 75% >= 40%)
    disp_candles = list(candles)
    disp_candles.append((170.0, 184.0, 168.0, 182.0, 500.0))
    ms_disp = detect_market_structure(disp_candles, window=2)
    assert ms_disp["last_event"] == "CHOCH"
    assert ms_disp["last_event_side"] == "BULLISH"
    assert ms_disp["current_trend"] == "BULLISH"


def test_order_block_touch_decay():
    """Verify that Order Block touches are tracked and freshness score decays from 15.0 to 7.5 to 0.0."""
    # Construct a Bullish Order Block setup:
    # Down candle at index 1: open 100, high 102, low 95, close 96 (top = 100, bottom = 95)
    # Next candle at index 2: explosive green displacement closing at 115 (> 102)
    candles = [
        (105.0, 107.0, 103.0, 104.0, 100.0),
        (100.0, 102.0, 95.0, 96.0, 150.0),   # Index 1: Bullish OB (top=100.0, bottom=95.0)
        (96.0, 116.0, 96.0, 115.0, 500.0),   # Index 2: Displacement up (c=115 > 102)
        (115.0, 120.0, 114.0, 118.0, 200.0), # Index 3: Continues higher
    ]

    obs = detect_order_blocks(candles, lookback=10)
    assert len(obs) >= 1
    bull_ob = [ob for ob in obs if ob["type"] == "BULLISH_OB"][0]
    assert bull_ob["status"] == "UNMITIGATED"
    assert bull_ob["touch_count"] == 0

    # Bar 4: Touch #1 (dips to 98.0, within 95.0-100.0 zone)
    candles.append((118.0, 118.0, 98.0, 105.0, 200.0))
    has_conf1, _, details1 = check_order_block_confluence(98.0, candles, side="LONG")
    assert has_conf1 is True
    assert details1["touch_count"] == 1
    assert details1["freshness_score"] == 15.0

    # Bar 5: Touch #2 (dips to 97.0, within zone again)
    candles.append((105.0, 110.0, 97.0, 108.0, 200.0))
    has_conf2, _, details2 = check_order_block_confluence(97.0, candles, side="LONG")
    assert has_conf2 is True
    assert details2["touch_count"] == 2
    assert details2["freshness_score"] == 7.5

    # Bar 6: Touch #3 (dips to 96.0, third tap - liquidity depleted)
    candles.append((108.0, 109.0, 96.0, 102.0, 200.0))
    has_conf3, _, details3 = check_order_block_confluence(96.0, candles, side="LONG")
    assert has_conf3 is True
    assert details3["touch_count"] == 3
    assert details3["freshness_score"] == 0.0


def test_check_market_overextension_fast_mean():
    """Verify that price stretched > 2.0x ATR from EMA20 is vetoed as an overextension trap."""
    from indicators import check_market_overextension

    # Price = 50,000, EMA50 = 49,000, ATR = 200, RSI = 60 (well below 75 threshold)
    # Distance to EMA20 (49,500) = 500 = 2.5x ATR (> 2.0x ATR threshold)
    is_overextended, reason = check_market_overextension(
        curr_close=50000.0,
        ema50=49000.0,
        atr=200.0,
        rsi=60.0,
        side="LONG",
        ema20=49500.0,
    )
    assert is_overextended is True
    assert "Fast Mean" in reason

    # Within 2.0x ATR -> Permitted
    is_overextended2, _ = check_market_overextension(
        curr_close=49800.0,
        ema50=49000.0,
        atr=200.0,
        rsi=60.0,
        side="LONG",
        ema20=49500.0,
    )
    assert is_overextended2 is False

