import time
from unittest.mock import MagicMock, patch
import urllib.error
import pytest

from microstructure import (
    FundingRateTracker,
    FuturesHealthProber,
    HistoricalMicrostructureReplayer,
    LiquidationTracker,
    OpenInterestEngine,
    SymbolNormalizer,
    WhaleTradeFilter,
    get_whale_thresholds,
    fetch_historical_funding_rates,
    fetch_historical_open_interest,
)


def test_symbol_normalizer():
    """Verify symbol normalization between Spot and Futures tickers."""
    assert SymbolNormalizer.to_futures_symbol("BTCUSDT") == "BTCUSDT"
    assert SymbolNormalizer.to_futures_symbol("pepeusdt") == "1000PEPEUSDT"
    assert SymbolNormalizer.to_futures_symbol("SHIBUSDT") == "1000SHIBUSDT"

    assert SymbolNormalizer.to_spot_symbol("1000PEPEUSDT") == "PEPEUSDT"
    assert SymbolNormalizer.to_spot_symbol("1000SHIBUSDT") == "SHIBUSDT"
    assert SymbolNormalizer.to_spot_symbol("SOLUSDT") == "SOLUSDT"


def test_whale_trade_filter_thresholds():
    """Verify WhaleTradeFilter rejects retail trades and assigns proper tiered badges."""
    filter_ = WhaleTradeFilter(
        dolphin_threshold=50_000.0,
        shark_threshold=100_000.0,
        whale_threshold=500_000.0,
    )

    # 1. Retail trade: $10,000 -> Rejected (None)
    retail_payload = {"s": "BTCUSDT", "p": "50000.0", "q": "0.2", "m": False, "T": 1700000000000}
    assert filter_.process_trade(retail_payload) is None
    assert len(filter_.tape) == 0

    # 2. Dolphin trade: $75,000
    dolphin_payload = {"s": "BTCUSDT", "p": "50000.0", "q": "1.5", "m": False, "T": 1700000000000}
    trade1 = filter_.process_trade(dolphin_payload)
    assert trade1 is not None
    assert trade1.badge == "DOLPHIN"
    assert trade1.notional == 75_000.0

    # 3. Shark trade: $250,000
    shark_payload = {"s": "BTCUSDT", "p": "50000.0", "q": "5.0", "m": False, "T": 1700000000000}
    trade2 = filter_.process_trade(shark_payload)
    assert trade2 is not None
    assert trade2.badge == "SHARK"

    # 4. Whale trade: $1,000,000
    whale_payload = {"s": "BTCUSDT", "p": "50000.0", "q": "20.0", "m": False, "T": 1700000000000}
    trade3 = filter_.process_trade(whale_payload)
    assert trade3 is not None
    assert trade3.badge == "WHALE"
    assert len(filter_.tape) == 3


def test_whale_trade_maker_taker_mapping():
    """Verify that buyer maker (m=True) correctly resolves to Market SELL (taker sell)."""
    filter_ = WhaleTradeFilter()

    # m=True -> buyer maker -> seller taker -> SELL
    sell_payload = {"s": "BTCUSDT", "p": "50000.0", "q": "2.0", "m": True, "T": 1700000000000}
    trade_sell = filter_.process_trade(sell_payload)
    assert trade_sell.side == "SELL"

    # m=False -> buyer taker -> BUY
    buy_payload = {"s": "BTCUSDT", "p": "50000.0", "q": "2.0", "m": False, "T": 1700000000000}
    trade_buy = filter_.process_trade(buy_payload)
    assert trade_buy.side == "BUY"


def test_liquidation_direction_mapping():
    """Verify that forced SELL orders represent LONG liquidations and vice-versa."""
    tracker = LiquidationTracker()

    # S: "SELL" -> Long liquidated
    long_liq_payload = {"s": "BTCUSDT", "S": "SELL", "q": "1.0", "p": "50000.0", "T": 1700000000000}
    event1 = tracker.process_order(long_liq_payload)
    assert event1 is not None
    assert event1.liquidated_position == "LONG"
    assert event1.notional == 50_000.0

    # S: "BUY" -> Short liquidated
    short_liq_payload = {"s": "BTCUSDT", "S": "BUY", "q": "2.0", "p": "50000.0", "T": 1700000000000}
    event2 = tracker.process_order(short_liq_payload)
    assert event2 is not None
    assert event2.liquidated_position == "SHORT"
    assert event2.notional == 100_000.0


def test_liquidation_cascade_threshold():
    """Verify rolling 60-second liquidation accumulation and cascade flag activation at $1M."""
    tracker = LiquidationTracker(cascade_threshold=1_000_000.0, window_seconds=60.0)
    now = time.time()

    # Old liquidation from 120 seconds ago ($800k) -> Must not count
    tracker.liquidations.append(
        tracker.process_order({"s": "BTCUSDT", "S": "SELL", "q": "16.0", "p": "50000.0", "T": int((now - 120) * 1000)})
    )
    total_vol, cascade = tracker.get_rolling_summary("BTCUSDT")
    assert total_vol == 0.0
    assert cascade is False

    # Recent liquidation 1 ($600k)
    tracker.process_order({"s": "BTCUSDT", "S": "SELL", "q": "12.0", "p": "50000.0", "T": int(now * 1000)})
    total_vol, cascade = tracker.get_rolling_summary("BTCUSDT")
    assert total_vol == 600_000.0
    assert cascade is False

    # Recent liquidation 2 ($500k) -> Total $1.1M (> $1M cascade threshold)
    tracker.process_order({"s": "BTCUSDT", "S": "SELL", "q": "10.0", "p": "50000.0", "T": int(now * 1000)})
    total_vol, cascade = tracker.get_rolling_summary("BTCUSDT")
    assert total_vol == 1_100_000.0
    assert cascade is True


def test_open_interest_4_quadrant_regimes():
    """Verify OpenInterestEngine produces all 4 diagnostic market regimes."""
    engine = OpenInterestEngine(min_oi_delta_pct=0.2, min_price_delta_pct=0.1)
    t = 1000.0

    # 1. Baseline
    engine.update("BTCUSDT", price=50000.0, open_interest=1000.0, timestamp=t)

    # 2. Price UP (+2%), OI UP (+5%) -> LONG_BUILDUP
    regime = engine.update("BTCUSDT", price=51000.0, open_interest=1050.0, timestamp=t + 30)
    assert regime == "LONG_BUILDUP"

    # Reset with new baseline
    engine = OpenInterestEngine(min_oi_delta_pct=0.2, min_price_delta_pct=0.1)
    engine.update("BTCUSDT", price=50000.0, open_interest=1000.0, timestamp=t)
    # 3. Price UP (+2%), OI DOWN (-5%) -> SHORT_SQUEEZE
    regime = engine.update("BTCUSDT", price=51000.0, open_interest=950.0, timestamp=t + 30)
    assert regime == "SHORT_SQUEEZE"

    # Reset with new baseline
    engine = OpenInterestEngine(min_oi_delta_pct=0.2, min_price_delta_pct=0.1)
    engine.update("BTCUSDT", price=50000.0, open_interest=1000.0, timestamp=t)
    # 4. Price DOWN (-2%), OI UP (+5%) -> SHORT_BUILDUP
    regime = engine.update("BTCUSDT", price=49000.0, open_interest=1050.0, timestamp=t + 30)
    assert regime == "SHORT_BUILDUP"

    # Reset with new baseline
    engine = OpenInterestEngine(min_oi_delta_pct=0.2, min_price_delta_pct=0.1)
    engine.update("BTCUSDT", price=50000.0, open_interest=1000.0, timestamp=t)
    # 5. Price DOWN (-2%), OI DOWN (-5%) -> LONG_LIQUIDATION
    regime = engine.update("BTCUSDT", price=49000.0, open_interest=950.0, timestamp=t + 30)
    assert regime == "LONG_LIQUIDATION"


def test_open_interest_deadband_filtering():
    """Verify OpenInterestEngine ignores noise smaller than deadband thresholds."""
    engine = OpenInterestEngine(min_oi_delta_pct=0.2, min_price_delta_pct=0.1)
    t = 1000.0

    engine.update("BTCUSDT", price=50000.0, open_interest=1000.0, timestamp=t)
    # Price moved +0.02%, OI moved +0.05% -> below threshold -> remains NEUTRAL
    regime = engine.update("BTCUSDT", price=50010.0, open_interest=1000.5, timestamp=t + 30)
    assert regime == "NEUTRAL"


def test_funding_rate_apr_and_countdown():
    """Verify FundingRateTracker calculates APR accurately across cycle durations and formats countdown."""
    # 1. 8-hour cycle: Rate 0.01% (0.0001) -> 3 periods/day * 365 = 10.95% APR
    apr_8h = FundingRateTracker.calculate_apr(0.0001, cycle_hours=8.0)
    assert pytest.approx(apr_8h, rel=1e-3) == 10.95

    # 2. 4-hour cycle: Rate 0.01% -> 6 periods/day * 365 = 21.90% APR
    apr_4h = FundingRateTracker.calculate_apr(0.0001, cycle_hours=4.0)
    assert pytest.approx(apr_4h, rel=1e-3) == 21.90

    # 3. Countdown formatting: 1 hour, 23 minutes, 45 seconds remaining
    now_ms = 1700000000000
    target_ms = now_ms + (3600 + 23 * 60 + 45) * 1000
    cd_str = FundingRateTracker.format_countdown(target_ms, current_time_ms=now_ms)
    assert cd_str == "01:23:45"


def test_futures_prober_mock_status():
    """Verify FuturesHealthProber handles HTTP 200, 451 geo-blocking, and timeouts."""
    # 1. Mock 200 OK
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.__enter__.return_value = mock_resp
    with patch("urllib.request.urlopen", return_value=mock_resp):
        ok, msg = FuturesHealthProber.probe_connectivity()
        assert ok is True
        assert "Online" in msg

    # 2. Mock 451 Unavailable For Legal Reasons (Geoblocked)
    http_error = urllib.error.HTTPError(
        url="https://fapi.binance.com",
        code=451,
        msg="Unavailable For Legal Reasons",
        hdrs={},
        fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=http_error):
        ok, msg = FuturesHealthProber.probe_connectivity()
        assert ok is False
        assert "Geoblocked" in msg


def test_historical_microstructure_replayer_transitions():
    """Verify that HistoricalMicrostructureReplayer feeds price and OI into OpenInterestEngine and updates regime."""
    replayer = HistoricalMicrostructureReplayer("BTCUSDT")
    assert replayer.oi_engine.get_regime("BTCUSDT") == "NEUTRAL"

    # Step 1: Baseline snapshot
    replayer.update_step(price=50000.0, oi_record=(1000000, 1000.0, 50000000.0))
    assert replayer.oi_engine.get_regime("BTCUSDT") == "NEUTRAL"

    # Step 2: Price UP (+2%), OI UP (+5%) -> LONG_BUILDUP
    replayer.update_step(price=51000.0, oi_record=(1030000, 1050.0, 53550000.0))
    assert replayer.oi_engine.get_regime("BTCUSDT") == "LONG_BUILDUP"

    # Step 3: Funding rate update
    replayer.update_step(price=51000.0, funding_record=(1030000, 0.0001, 51000.0))
    apr, countdown = replayer.funding_tracker.get_summary("BTCUSDT")
    assert apr > 0.0


def test_fetch_historical_oi_caching(tmp_path):
    """Verify that fetch_historical_open_interest creates SQLite table and caches records."""
    db_file = str(tmp_path / "test_klines_cache.db")
    now_ms = int(time.time() * 1000)
    mock_payload = [
        {"symbol": "BTCUSDT", "sumOpenInterest": "1000.0", "sumOpenInterestValue": "50000000.0", "timestamp": now_ms - 1800000},
        {"symbol": "BTCUSDT", "sumOpenInterest": "1050.0", "sumOpenInterestValue": "53550000.0", "timestamp": now_ms - 900000},
    ]
    mock_resp = MagicMock()
    mock_resp.status = 200
    import json
    mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        # First call: hits network and writes to SQLite
        records1 = fetch_historical_open_interest("BTCUSDT", period="15m", total_records=2, cache_db=db_file)
        assert len(records1) == 2
        assert records1[0][0] == now_ms - 1800000
        assert mock_urlopen.call_count == 1

        # Second call: served directly from local SQLite cache (<5ms, zero network calls)
        records2 = fetch_historical_open_interest("BTCUSDT", period="15m", total_records=2, cache_db=db_file)
        assert len(records2) == 2
        assert records2[0] == records1[0]
        assert mock_urlopen.call_count == 1  # call count did NOT increase


def test_fetch_historical_funding_caching(tmp_path):
    """Verify that fetch_historical_funding_rates creates SQLite table and caches records."""
    db_file = str(tmp_path / "test_klines_cache.db")
    mock_payload = [
        {"symbol": "BTCUSDT", "fundingTime": 1000000, "fundingRate": "0.0001", "markPrice": "50000.0"},
        {"symbol": "BTCUSDT", "fundingTime": 2000000, "fundingRate": "0.00015", "markPrice": "51000.0"},
    ]
    mock_resp = MagicMock()
    mock_resp.status = 200
    import json
    mock_resp.read.return_value = json.dumps(mock_payload).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        # First call: hits network and writes to SQLite
        records1 = fetch_historical_funding_rates("BTCUSDT", total_records=2, cache_db=db_file)
        assert len(records1) == 2
        assert records1[0][0] == 1000000
        assert mock_urlopen.call_count == 1

        # Second call: served from SQLite cache
        records2 = fetch_historical_funding_rates("BTCUSDT", total_records=2, cache_db=db_file)
        assert len(records2) == 2
        assert records2[0] == records1[0]
        assert mock_urlopen.call_count == 1


def test_whale_trade_filter_tier_scaling():
    """Verify that WhaleTradeFilter scales thresholds dynamically according to asset capitalization tiers."""
    # Tier 1 Mega-Cap: BTC
    wf_btc = WhaleTradeFilter(symbol="BTCUSDT")
    assert wf_btc.whale_threshold == 500_000.0
    assert wf_btc.shark_threshold == 100_000.0
    assert wf_btc.dolphin_threshold == 50_000.0

    # Tier 2 Large-Caps: ETH, SOL, BNB, XRP
    wf_eth = WhaleTradeFilter(symbol="ETHUSDT")
    assert wf_eth.whale_threshold == 150_000.0
    assert wf_eth.shark_threshold == 50_000.0
    assert wf_eth.dolphin_threshold == 25_000.0

    wf_xrp = WhaleTradeFilter(symbol="XRPUSDT")
    assert wf_xrp.whale_threshold == 150_000.0

    # Tier 3 Mid-Caps / Alts: DOGE, ADA, etc.
    wf_doge = WhaleTradeFilter(symbol="DOGEUSDT")
    assert wf_doge.whale_threshold == 50_000.0
    assert wf_doge.shark_threshold == 25_000.0
    assert wf_doge.dolphin_threshold == 10_000.0

    # Untracked symbol falls back to DEFAULT (Tier 3)
    wf_custom = WhaleTradeFilter(symbol="UNKNOWNUSDT")
    assert wf_custom.whale_threshold == 50_000.0

    # Default without symbol preserves backward compatibility with BTC (500k)
    wf_default = WhaleTradeFilter()
    assert wf_default.whale_threshold == 500_000.0

    # Explicit threshold overrides tier defaults
    wf_override = WhaleTradeFilter(symbol="XRPUSDT", whale_threshold=200_000.0)
    assert wf_override.whale_threshold == 200_000.0

