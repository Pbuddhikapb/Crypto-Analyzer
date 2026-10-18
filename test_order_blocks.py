import pytest
from indicators import (
    detect_order_blocks,
    detect_breaker_blocks,
    check_order_block_confluence,
)


def test_detect_bullish_order_block():
    """Verify that a bearish down-candle preceding aggressive upward displacement
    is accurately classified as a Bullish Order Block (Demand)."""
    candles = [
        # (open, high, low, close, volume)
        (100.0, 102.0, 99.0, 101.0, 1000.0),
        (101.0, 103.0, 100.0, 102.0, 1200.0),
        # Candle 2: Bearish Down-Candle (OB origin)
        (102.0, 102.5, 98.0, 99.0, 1500.0),
        # Candle 3: Aggressive Bullish Displacement (breaks above candle 2 high of 102.5)
        (99.0, 106.0, 98.8, 105.5, 4500.0),
        # Candle 4: Follow-through expansion (creates Bullish FVG: low 103.5 > high 102.5)
        (105.5, 108.0, 103.5, 107.0, 3800.0),
    ]

    obs = detect_order_blocks(candles, lookback=10)
    assert len(obs) >= 1

    bull_ob = [ob for ob in obs if ob["type"] == "BULLISH_OB"][0]
    assert bull_ob["index"] == 2
    assert bull_ob["top"] == 102.0      # open of bearish candle
    assert bull_ob["bottom"] == 98.0    # low of candle
    assert bull_ob["mean_threshold"] == (102.0 + 98.0) / 2.0  # 100.0
    assert bull_ob["status"] == "UNMITIGATED"


def test_detect_bearish_order_block():
    """Verify that a bullish up-candle preceding aggressive downward displacement
    is accurately classified as a Bearish Order Block (Supply)."""
    candles = [
        (200.0, 202.0, 199.0, 201.0, 1000.0),
        # Candle 1: Bullish Up-Candle (OB origin)
        (201.0, 205.0, 200.5, 204.0, 1200.0),
        # Candle 2: Aggressive Bearish Displacement (breaks below candle 1 low of 200.5)
        (204.0, 204.5, 194.0, 195.0, 5000.0),
        # Candle 3: Follow-through expansion (creates Bearish FVG: high 198.0 < low 200.5)
        (195.0, 198.0, 192.0, 193.0, 4200.0),
    ]

    obs = detect_order_blocks(candles, lookback=10)
    assert len(obs) >= 1

    bear_ob = [ob for ob in obs if ob["type"] == "BEARISH_OB"][0]
    assert bear_ob["index"] == 1
    assert bear_ob["top"] == 205.0      # high of bullish candle
    assert bear_ob["bottom"] == 201.0   # open of bullish candle
    assert bear_ob["mean_threshold"] == (205.0 + 201.0) / 2.0  # 203.0
    assert bear_ob["status"] == "UNMITIGATED"


def test_order_block_mitigation_lifecycle():
    """Verify that an active Order Block transitions from UNMITIGATED to MITIGATED
    when subsequent price retests its zone without breaking invalidation."""
    candles = [
        (100.0, 101.0, 99.0, 100.5, 1000.0),
        # Candle 1: Bearish OB (top: 102.0, bottom: 98.0, MT: 100.0)
        (102.0, 102.2, 98.0, 99.0, 1500.0),
        # Candle 2: Displacement up
        (99.0, 106.0, 98.8, 105.5, 4500.0),
        # Candle 3: High expansion
        (105.5, 108.0, 104.0, 107.0, 3000.0),
        # Candle 4: Retest pullback dipping to 100.5 (inside [98.0, 102.0])
        (107.0, 107.2, 100.5, 103.0, 2500.0),
    ]

    obs = detect_order_blocks(candles, lookback=10)
    bull_ob = [ob for ob in obs if ob["type"] == "BULLISH_OB"][0]
    assert bull_ob["status"] == "MITIGATED"
    assert bull_ob["mitigated_index"] == 4


def test_order_block_violation_and_breaker_flip():
    """Verify that an Order Block violated by a decisive candle close flips
    into an institutional Breaker Block (failed demand flips to supply)."""
    candles = [
        (100.0, 101.0, 99.0, 100.5, 1000.0),
        # Candle 1: Bullish OB (top: 102.0, bottom: 98.0)
        (102.0, 102.2, 98.0, 99.0, 1500.0),
        # Candle 2: Displacement up
        (99.0, 106.0, 98.8, 105.5, 4500.0),
        # Candle 3: Violent collapse closing below bottom of 98.0 (violation)
        (105.5, 105.8, 95.0, 96.0, 6000.0),
        # Candle 4: Subsequent consolidation
        (96.0, 97.5, 94.0, 95.0, 2000.0),
    ]

    obs = detect_order_blocks(candles, lookback=10)
    bull_ob = [ob for ob in obs if ob["type"] == "BULLISH_OB"][0]
    assert bull_ob["status"] == "VIOLATED"
    assert bull_ob["violated_index"] == 3

    # Check Breaker Block generation
    bbs = detect_breaker_blocks(candles, order_blocks=obs, lookback=10)
    assert len(bbs) == 1
    breaker = bbs[0]
    assert breaker["type"] == "BEARISH_BREAKER"
    assert breaker["top"] == 102.0
    assert breaker["bottom"] == 98.0
    assert breaker["status"] == "UNMITIGATED"


def test_check_order_block_confluence_matching():
    """Verify check_order_block_confluence accurately confirms candidate entry prices
    tapping aligned active blocks."""
    candles = [
        (100.0, 101.0, 99.0, 100.5, 1000.0),
        # Bullish OB (top: 102.0, bottom: 98.0, MT: 100.0)
        (102.0, 102.2, 98.0, 99.0, 1500.0),
        (99.0, 106.0, 98.8, 105.5, 4500.0),
        (105.5, 108.0, 104.0, 107.0, 3000.0),
    ]

    # Test candidate Long entry at 100.5 (inside [98.0, 102.0])
    has_conf, msg, details = check_order_block_confluence(
        price=100.5,
        candles=candles,
        side="LONG",
        lookback=10,
    )
    assert has_conf is True
    assert details["block_type"] == "BULLISH_OB"
    assert details["invalidation_level"] == 98.0
    assert "INSTITUTIONAL DEMAND" in msg

    # Test candidate Long entry far away at 115.0 -> No confluence
    has_conf_far, msg_far, details_far = check_order_block_confluence(
        price=115.0,
        candles=candles,
        side="LONG",
        lookback=10,
    )
    assert has_conf_far is False
    assert details_far["block_type"] is None

    # Test candidate Short entry at 100.5 -> No aligned supply block
    has_conf_short, _, _ = check_order_block_confluence(
        price=100.5,
        candles=candles,
        side="SHORT",
        lookback=10,
    )
    assert has_conf_short is False


def test_mean_threshold_tap_detection():
    """Verify that an entry exactly tapping the 50% Mean Threshold is flagged."""
    candles = [
        (100.0, 101.0, 99.0, 100.5, 1000.0),
        # Bullish OB: top 102.0, bottom 98.0 -> MT = 100.0
        (102.0, 102.2, 98.0, 99.0, 1500.0),
        (99.0, 106.0, 98.8, 105.5, 4500.0),
        (105.5, 108.0, 104.0, 107.0, 3000.0),
    ]

    has_conf, msg, details = check_order_block_confluence(
        price=100.0,
        candles=candles,
        side="LONG",
        lookback=10,
    )
    assert has_conf is True
    assert details["mean_threshold_tapped"] is True
    assert "Mean Threshold 50% Sweet Spot" in msg


def test_empty_or_short_candles_edge_cases():
    """Verify edge cases with short or empty candle series return safely."""
    assert detect_order_blocks([]) == []
    assert detect_order_blocks([(100.0, 101.0, 99.0, 100.0, 100.0)]) == []
    assert detect_breaker_blocks([]) == []
    has_conf, _, details = check_order_block_confluence(100.0, [], side="LONG")
    assert has_conf is False
    assert details["has_confluence"] is False
