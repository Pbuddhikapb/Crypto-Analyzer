"""
Unit tests for price precision formatting and UTC clock rules specified in terminal requirements.
Rules:
- >=1000: 1 decimal
- >=1: 2 decimals
- >=0.1: 4 decimals
- else: 6 decimals
- Header clock: real UTC with 'UTC' label.
"""

from datetime import datetime, timezone


def get_price_precision(price: float) -> int:
    abs_p = abs(price)
    if abs_p >= 1000.0:
        return 1
    if abs_p >= 1.0:
        return 2
    if abs_p >= 0.1:
        return 4
    return 6


def format_price(price: float, reference_price: float = None) -> str:
    ref = reference_price if (reference_price is not None and reference_price > 0) else price
    prec = get_price_precision(ref)
    return f"{price:.{prec}f}"


def format_utc_clock(dt: datetime) -> str:
    utc_dt = dt.astimezone(timezone.utc)
    return utc_dt.strftime("%H:%M:%S UTC")


def test_price_precision_tiers():
    # >= 1000: 1 decimal
    assert get_price_precision(81671.7) == 1
    assert get_price_precision(1000.0) == 1
    assert get_price_precision(2397.45) == 1

    # >= 1: 2 decimals
    assert get_price_precision(999.99) == 2
    assert get_price_precision(184.24) == 2
    assert get_price_precision(1.32) == 2
    assert get_price_precision(1.0) == 2

    # >= 0.1: 4 decimals
    assert get_price_precision(0.999) == 4
    assert get_price_precision(0.2161) == 4
    assert get_price_precision(0.1) == 4

    # < 0.1: 6 decimals
    assert get_price_precision(0.0999) == 6
    assert get_price_precision(0.081234) == 6
    assert get_price_precision(0.00012) == 6


def test_format_price_with_reference():
    # BTC reference (>= 1000)
    btc_ref = 81671.7
    assert format_price(81671.72, btc_ref) == "81671.7"
    assert format_price(81600.26, btc_ref) == "81600.3"

    # SOL reference (>= 1)
    sol_ref = 184.24
    assert format_price(184.246, sol_ref) == "184.25"
    assert format_price(180.0, sol_ref) == "180.00"

    # ADA reference (>= 0.1)
    ada_ref = 0.2161
    assert format_price(0.21618, ada_ref) == "0.2162"

    # Sub-0.1 reference
    doge_ref = 0.0812
    assert format_price(0.0812345, doge_ref) == "0.081235"


def test_format_utc_clock():
    dt = datetime(2026, 10, 23, 15, 40, 6, tzinfo=timezone.utc)
    res = format_utc_clock(dt)
    assert res == "15:40:06 UTC"
    assert res.endswith(" UTC")
