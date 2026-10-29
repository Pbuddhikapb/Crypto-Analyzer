"""Unit tests for Pillar 2: Fibonacci Optimal Trade Entry (OTE) and Discount/Premium Matrix."""

import pytest
from indicators import (
    calculate_fibonacci_levels,
    classify_discount_premium,
    get_active_dealing_range,
)


def test_fibonacci_levels_long():
    swing_low = 100.0
    swing_high = 200.0
    fibs = calculate_fibonacci_levels(swing_low, swing_high, side="LONG")
    
    assert fibs["0.0"] == pytest.approx(200.0)
    assert fibs["0.5"] == pytest.approx(150.0)
    assert fibs["0.618"] == pytest.approx(138.2)
    assert fibs["0.705"] == pytest.approx(129.5)
    assert fibs["0.786"] == pytest.approx(121.4)
    assert fibs["1.0"] == pytest.approx(100.0)
    assert fibs["range_span"] == pytest.approx(100.0)


def test_fibonacci_levels_short():
    swing_low = 100.0
    swing_high = 200.0
    fibs = calculate_fibonacci_levels(swing_low, swing_high, side="SHORT")
    
    assert fibs["0.0"] == pytest.approx(100.0)
    assert fibs["0.5"] == pytest.approx(150.0)
    assert fibs["0.618"] == pytest.approx(161.8)
    assert fibs["0.705"] == pytest.approx(170.5)
    assert fibs["0.786"] == pytest.approx(178.6)
    assert fibs["1.0"] == pytest.approx(200.0)
    assert fibs["range_span"] == pytest.approx(100.0)


def test_invalid_range_raises():
    with pytest.raises(ValueError):
        calculate_fibonacci_levels(200.0, 100.0, side="LONG")


def test_discount_premium_classification_long():
    # Long: range 100 to 200
    # Price 170 -> retrace is (200 - 170) / 100 = 0.30 -> PREMIUM (< 0.50)
    zone, ratio = classify_discount_premium(170.0, 100.0, 200.0, side="LONG")
    assert zone == "PREMIUM"
    assert ratio == 0.3

    # Price 145 -> retrace is (200 - 145) / 100 = 0.55 -> SHALLOW_DISCOUNT (0.50 - 0.618)
    zone, ratio = classify_discount_premium(145.0, 100.0, 200.0, side="LONG")
    assert zone == "SHALLOW_DISCOUNT"
    assert ratio == 0.55

    # Price 130 -> retrace is (200 - 130) / 100 = 0.70 -> OTE (0.618 - 0.786)
    zone, ratio = classify_discount_premium(130.0, 100.0, 200.0, side="LONG")
    assert zone == "OTE"
    assert ratio == 0.70

    # Price 110 -> retrace is (200 - 110) / 100 = 0.90 -> DEEP_DISCOUNT (0.786 - 1.00)
    zone, ratio = classify_discount_premium(110.0, 100.0, 200.0, side="LONG")
    assert zone == "DEEP_DISCOUNT"
    assert ratio == 0.90

    # Price 90 -> retrace is (200 - 90) / 100 = 1.10 -> INVALIDATED (> 1.00)
    zone, ratio = classify_discount_premium(90.0, 100.0, 200.0, side="LONG")
    assert zone == "INVALIDATED"
    assert ratio == 1.10


def test_discount_premium_classification_short():
    # Short: range 100 to 200
    # Price 130 -> retrace is (130 - 100) / 100 = 0.30 -> DISCOUNT (< 0.50)
    zone, ratio = classify_discount_premium(130.0, 100.0, 200.0, side="SHORT")
    assert zone == "DISCOUNT"
    assert ratio == 0.3

    # Price 155 -> retrace is (155 - 100) / 100 = 0.55 -> SHALLOW_PREMIUM (0.50 - 0.618)
    zone, ratio = classify_discount_premium(155.0, 100.0, 200.0, side="SHORT")
    assert zone == "SHALLOW_PREMIUM"
    assert ratio == 0.55

    # Price 170 -> retrace is (170 - 100) / 100 = 0.70 -> OTE (0.618 - 0.786)
    zone, ratio = classify_discount_premium(170.0, 100.0, 200.0, side="SHORT")
    assert zone == "OTE"
    assert ratio == 0.70

    # Price 190 -> retrace is (190 - 100) / 100 = 0.90 -> DEEP_PREMIUM (0.786 - 1.00)
    zone, ratio = classify_discount_premium(190.0, 100.0, 200.0, side="SHORT")
    assert zone == "DEEP_PREMIUM"
    assert ratio == 0.90

    # Price 210 -> retrace is (210 - 100) / 100 = 1.10 -> INVALIDATED (> 1.00)
    zone, ratio = classify_discount_premium(210.0, 100.0, 200.0, side="SHORT")
    assert zone == "INVALIDATED"
    assert ratio == 1.10


def test_dealing_range_and_ote_confluence():
    # Construct synthetic candles with a clear swing low and swing high
    # Candle format: (open, high, low, close, volume)
    candles = [
        (100.0, 102.0, 99.0, 101.0, 1000.0),
        (101.0, 103.0, 95.0, 96.0, 1000.0), # Swing low candidate around 95.0
        (96.0, 105.0, 94.0, 104.0, 1000.0), # Low at 94.0 confirmed
        (104.0, 110.0, 103.0, 109.0, 1000.0),
        (109.0, 115.0, 108.0, 114.0, 1000.0),
        (114.0, 125.0, 113.0, 124.0, 1000.0), # Swing high candidate at 125.0
        (124.0, 122.0, 118.0, 119.0, 1000.0),
        (119.0, 120.0, 115.0, 116.0, 1000.0), # Swing high confirmed
        (116.0, 117.0, 110.0, 111.0, 1000.0),
        (111.0, 112.0, 104.0, 105.0, 1000.0), # Retracement into OTE
    ]
    
    dr = get_active_dealing_range(candles, lookback=20, window=1)
    assert dr is not None
    assert dr["swing_high"] >= 120.0
    assert dr["swing_low"] <= 100.0

