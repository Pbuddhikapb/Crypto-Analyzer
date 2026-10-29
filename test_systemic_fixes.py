"""Regression tests for indicator edge cases."""

from indicators import detect_rsi_divergences, check_bullish_fvg_retest


def test_rsi_divergence_recency_constraint():
    """Verify that a divergence older than 5 bars expires and returns False."""
    # Synthetic candles where swing low occurs 10 bars ago
    candles = []
    for i in range(40):
        # Create a swing low at bar 15, then flat/rally
        if i == 15:
            l = 85.0
        elif i == 25:
            l = 80.0  # lower low at bar 25
        else:
            l = 95.0
        h = l + 10.0
        o = l + 5.0
        cl = l + 5.0
        candles.append((o, h, l, cl, 1000.0))

    # At length 40, bar 25 is 15 bars old (40 - 1 - 25 = 14 > 5)
    bull_div, bear_div = detect_rsi_divergences(candles, period=14, lookback=30)
    assert bull_div is False

    # At length 30, bar 25 is 4 bars old (30 - 1 - 25 = 4 <= 5) -> fresh!
    bull_div_fresh, _ = detect_rsi_divergences(candles[:30], period=14, lookback=30)
    assert bull_div_fresh is True


def test_fvg_retest_first_mitigation_only():
    """Verify that an FVG is only mitigated once price penetrates the 50% CE or fills the gap."""
    candles = []
    # 3 bars forming a bullish FVG: gap is between 102 and 106, CE = 104.0
    c0 = (100.0, 102.0, 99.0, 101.0, 1000.0)  # High = 102 (fvg_lower)
    c1 = (101.0, 110.0, 101.0, 109.0, 5000.0)  # Displacement green body
    c2 = (109.0, 112.0, 106.0, 111.0, 2000.0)  # Low = 106 (fvg_upper)
    candles.extend([c0, c1, c2])

    # Candle 3: First tap into upper half of gap (above CE 104.0): Low = 104.5, Close = 105.0
    c3 = (111.0, 111.0, 104.5, 105.0, 1000.0)
    candles.append(c3)
    is_retest, _ = check_bullish_fvg_retest(candles, lookback=10)
    assert is_retest is True  # FIRST TAP is valid!

    # Candle 4: Deep retest into 50% CE closing below CE (103.5 < 104.0): Mitigates the gap!
    c4 = (105.0, 105.0, 103.0, 103.5, 1000.0)
    candles.append(c4)
    is_retest_2, _ = check_bullish_fvg_retest(candles, lookback=10)
    assert is_retest_2 is True  # Deep 50% CE retest is permitted!

    # Candle 5: Subsequent attempt after CE mitigation: Rejected!
    c5 = (103.5, 105.0, 103.0, 104.0, 1000.0)
    candles.append(c5)
    is_retest_3, _ = check_bullish_fvg_retest(candles, lookback=10)
    assert is_retest_3 is False  # Fully mitigated gap is rejected!
