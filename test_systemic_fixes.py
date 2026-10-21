import sqlite3
from indicators import detect_rsi_divergences, check_bullish_fvg_retest
from risk import PortfolioRiskManager
from trader import PaperTrader


from test_trader import setup_trader_ready_for_trigger


def test_initial_stop_loss_preserved_after_breakeven(tmp_path):
    """Verify that initial_stop_loss is persisted in SQLite and preserved when stop_loss moves to BE."""
    db_path = str(tmp_path / "test_initial_sl.db")
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=db_path)
    
    trigger_candle = setup_trader_ready_for_trigger(trader)
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14 10:30")
    
    assert trade is not None
    entry_p = trade["entry_price"]
    initial_sl = trade["stop_loss"]
    assert trade["initial_stop_loss"] == initial_sl
    risk = entry_p - initial_sl
    
    # Push price up to +1.25R to cross TP1 (+1.00R) and arm Breakeven (+1.20R)
    c4 = (entry_p, entry_p + (1.25 * risk), entry_p, entry_p + (1.20 * risk), 200.0)
    trader.on_candle(c4, timestamp="2026-10-14 10:45")
    
    assert trader.active_trade["be_armed"] is True
    assert trader.active_trade["stop_loss"] > entry_p  # BE stop set above entry!
    assert trader.active_trade["initial_stop_loss"] == initial_sl  # Original SL unchanged!
    
    # Check SQLite record
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT stop_loss, initial_stop_loss FROM paper_trades WHERE id = ?", (trade["id"],)).fetchone()
    assert row[0] > entry_p
    assert row[1] == initial_sl


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
    # Pivot is within 5 bars


def test_fvg_retest_first_mitigation_only():
    """Verify that an FVG is only mitigated once price penetrates the 50% CE or fills the gap."""
    candles = []
    # 3 bars forming a bullish FVG: gap is between 102 and 106, CE = 104.0
    c0 = (100.0, 102.0, 99.0, 101.0, 1000.0)      # High = 102 (fvg_lower)
    c1 = (101.0, 110.0, 101.0, 109.0, 5000.0)     # Displacement green body
    c2 = (109.0, 112.0, 106.0, 111.0, 2000.0)     # Low = 106 (fvg_upper)
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


def test_portfolio_risk_manager_same_bar_and_directional_limits(tmp_path):
    """Verify that risk manager blocks multiple entries on the same bar and caps directional exposure."""
    db_path = str(tmp_path / "test_risk_limits.db")
    risk_mgr = PortfolioRiskManager(
        max_open_positions=3,
        symbol_cooldown_bars=3,
        max_entries_per_bar=1,
        max_directional_positions=2,
        db_path=db_path,
    )
    
    # Entry 1 on bar 10: Approved
    ok1, reason1 = risk_mgr.can_open_position("BTCUSDT", current_bar_index=10, side="LONG")
    assert ok1 is True
    risk_mgr.record_position_opened("BTCUSDT", current_bar_index=10)
    
    # Entry 2 on same bar 10: Blocked by max_entries_per_bar!
    ok2, reason2 = risk_mgr.can_open_position("ETHUSDT", current_bar_index=10, side="LONG")
    assert ok2 is False
    assert "Max entries per bar reached" in reason2
    
    # Insert 2 active LONG positions into DB to test directional limit
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO paper_trades (symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit) VALUES ('BTCUSDT', '15m', 'LONG', 'OPEN', '2026-10-14', 100, 90, 120)")
        conn.execute("INSERT INTO paper_trades (symbol, timeframe, side, status, timestamp, entry_price, stop_loss, take_profit) VALUES ('ETHUSDT', '15m', 'LONG', 'OPEN', '2026-10-14', 100, 90, 120)")
        
    # On bar 11: 3rd Long is blocked by max_directional_positions=2!
    ok3, reason3 = risk_mgr.can_open_position("SOLUSDT", current_bar_index=11, side="LONG")
    assert ok3 is False
    assert "Directional exposure limit reached" in reason3
    
    # But a SHORT position on bar 11 is approved!
    ok4, reason4 = risk_mgr.can_open_position("SOLUSDT", current_bar_index=11, side="SHORT")
    assert ok4 is True


def test_hierarchical_trend_gating(tmp_path):
    """Verify that Hierarchical Trend Gating allows 1H to lead when 4H is NEUTRAL, but blocks opposing trends."""
    db_path = str(tmp_path / "test_hierarchical_trend.db")
    trader = PaperTrader(symbol="BTCUSDT", timeframe="15m", db_path=db_path, htf_trend_filter=True, intermediate_1h_filter=True)
    
    # Setup ready trader
    trigger_candle = setup_trader_ready_for_trigger(trader)
    
    # 1. 4H NEUTRAL + 1H BULLISH -> Long is permitted!
    trader.set_htf_trend("NEUTRAL")
    trader.set_1h_trend("BULLISH")
    trade = trader.on_candle(trigger_candle, timestamp="2026-10-14 10:00")
    assert trade is not None
    assert trade["side"] == "LONG"
    
    # Reset active trade
    trader.active_trade = None
    
    # 2. 4H BEARISH + 1H BULLISH -> Long is strictly blocked (opposing macro downtrend)
    trader.set_htf_trend("BEARISH")
    trader.set_1h_trend("BULLISH")
    trade_blocked = trader.on_candle(trigger_candle, timestamp="2026-10-14 10:15")
    assert trade_blocked is None
    
    # 3. 4H BULLISH + 1H BEARISH -> Long is strictly blocked (opposing intermediate trend)
    trader.set_htf_trend("BULLISH")
    trader.set_1h_trend("BEARISH")
    trade_blocked_1h = trader.on_candle(trigger_candle, timestamp="2026-10-14 10:30")
    assert trade_blocked_1h is None


def setup_ready_candle(trader):
    price = 50000.0
    for i in range(198):
        trader.load_historical_candle((price, price + 10, price - 10, price, 100))
    c1 = (50000.0, 50050.0, 49950.0, 50000.0, 100.0)
    trader.on_candle(c1, timestamp="2026-10-14 10:00")
    c2 = (50000.0, 50300.0, 49950.0, 50250.0, 100.0)
    trader.on_candle(c2, timestamp="2026-10-14 10:15")
    return (50250.0, 50500.0, 50200.0, 50450.0, 500.0)
