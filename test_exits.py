"""
Unit tests for the Single Exit Authority (engine/exits.py).
Tests pure mathematical exit mechanics, gap adverse fills, tie-breaking,
stepped scaling (TP1 -> BE -> TP2), and underwater stagnancy time-stops.
"""

import pytest

from engine.exits import (
    ExitConfig,
    TradeState,
    calculate_gross_return_pct,
    calculate_r_multiple,
    create_trade_state,
    evaluate_trade_exit,
)


def test_create_trade_state_long():
    state = create_trade_state(
        trade_id="T1",
        symbol="BTCUSDT",
        side="LONG",
        entry_price=100.0,
        initial_sl=90.0,
        config=ExitConfig(tp1_r=0.75, tp2_r=1.33),
    )
    assert state.risk_dist == 10.0
    assert state.tp1_price == 107.5
    assert round(state.tp2_price, 2) == 113.3
    assert state.size_remaining_pct == 1.0
    assert state.tp1_hit is False
    assert state.be_armed is False


def test_create_trade_state_short():
    state = create_trade_state(
        trade_id="T2",
        symbol="ETHUSDT",
        side="SHORT",
        entry_price=100.0,
        initial_sl=110.0,
        config=ExitConfig(tp1_r=0.75, tp2_r=1.33),
    )
    assert state.risk_dist == 10.0
    assert state.tp1_price == 92.5
    assert round(state.tp2_price, 2) == 86.7
    assert state.size_remaining_pct == 1.0


def test_create_trade_state_invalid_sl():
    with pytest.raises(ValueError):
        create_trade_state("T3", "SOLUSDT", "LONG", 100.0, 100.0)


def test_normal_stop_loss_long():
    state = create_trade_state("T_SL", "BTCUSDT", "LONG", 100.0, 90.0)
    # Candle dips to 89.0, crossing SL 90.0
    candle = (98.0, 99.0, 89.0, 92.0)  # open, high, low, close
    decision = evaluate_trade_exit(state, candle)

    assert decision.action == "FULL_EXIT"
    assert decision.exit_reason == "STOP_LOSS"
    assert decision.exit_price == 90.0
    assert decision.closed_pct == 1.0
    assert decision.realized_r == -1.0
    assert decision.gross_return_pct == -10.0
    assert state.size_remaining_pct == 0.0


def test_adverse_gap_down_stop_loss_long():
    state = create_trade_state("T_GAP", "BTCUSDT", "LONG", 100.0, 90.0)
    # Candle opens at 85.0 (gap past 90.0 SL)
    candle = (85.0, 87.0, 84.0, 86.0)
    decision = evaluate_trade_exit(state, candle)

    assert decision.action == "FULL_EXIT"
    assert decision.exit_reason == "GAP_STOP_LOSS"
    # MUST fill at open (85.0), NEVER optimistic 90.0!
    assert decision.exit_price == 85.0
    assert decision.closed_pct == 1.0
    assert decision.realized_r == -1.5
    assert decision.gross_return_pct == -15.0


def test_adverse_gap_up_stop_loss_short():
    state = create_trade_state("T_GAP_SHORT", "BTCUSDT", "SHORT", 100.0, 110.0)
    # Candle opens at 115.0 (gap up past 110.0 SL)
    candle = (115.0, 118.0, 114.0, 116.0)
    decision = evaluate_trade_exit(state, candle)

    assert decision.action == "FULL_EXIT"
    assert decision.exit_reason == "GAP_STOP_LOSS"
    # MUST fill at open (115.0)
    assert decision.exit_price == 115.0
    assert decision.closed_pct == 1.0
    assert decision.realized_r == -1.5


def test_conservative_tie_break_sl_over_tp():
    state = create_trade_state("T_TIE", "BTCUSDT", "LONG", 100.0, 90.0)
    # Giant volatile bar that touches both TP2 (113.3) and SL (90.0)
    candle = (98.0, 115.0, 85.0, 92.0)
    decision = evaluate_trade_exit(state, candle)

    # Conservative rule: SL MUST take precedence over TP
    assert decision.action == "FULL_EXIT"
    assert decision.exit_reason == "STOP_LOSS"
    assert decision.exit_price == 90.0
    assert decision.realized_r == -1.0


def test_stepped_partial_scaling_lifecycle():
    cfg = ExitConfig(tp1_r=0.75, be_arm_r=0.85, tp2_r=1.33, be_offset_pct=0.05)
    state = create_trade_state("T_STEP", "BTCUSDT", "LONG", 100.0, 90.0, config=cfg)
    # Risk dist = 10.0 -> TP1 = 107.5, TP2 = 113.3

    # Bar 1: Hits TP1 but high is 108.0 (excursion 0.8R < 0.85R, so BE not yet armed)
    candle1 = (100.0, 108.0, 99.5, 107.0)
    dec1 = evaluate_trade_exit(state, candle1, config=cfg)
    assert dec1.action == "PARTIAL_TP1"
    assert dec1.exit_reason == "TAKE_PROFIT_1"
    assert dec1.exit_price == 107.5
    assert dec1.closed_pct == 0.5
    assert dec1.realized_r == 0.75
    assert state.tp1_hit is True
    assert state.size_remaining_pct == 0.5
    assert state.be_armed is False

    # Bar 2: Excursion reaches 109.0 (0.9R >= 0.85R), arms breakeven
    candle2 = (107.0, 109.0, 106.5, 108.5)
    dec2 = evaluate_trade_exit(state, candle2, config=cfg)
    assert dec2.action == "HOLD"
    assert state.be_armed is True
    assert state.current_sl == 100.05  # entry * 1.0005

    # Bar 3: Hits TP2 at 113.3
    candle3 = (108.5, 114.0, 108.0, 113.5)
    dec3 = evaluate_trade_exit(state, candle3, config=cfg)
    assert dec3.action == "FULL_EXIT"
    assert dec3.exit_reason == "TAKE_PROFIT_2"
    assert dec3.exit_price == 113.3
    assert dec3.closed_pct == 0.5
    assert dec3.realized_r == 1.33
    assert state.size_remaining_pct == 0.0


def test_breakeven_exit_after_tp1():
    cfg = ExitConfig(tp1_r=0.75, be_arm_r=0.85, tp2_r=1.33, be_offset_pct=0.05)
    state = create_trade_state("T_BE", "BTCUSDT", "LONG", 100.0, 90.0, config=cfg)

    # Bar 1 hits TP1 and pushes to 109.0 (arming BE)
    candle1 = (100.0, 109.0, 99.5, 107.5)
    dec1 = evaluate_trade_exit(state, candle1, config=cfg)
    assert dec1.action == "PARTIAL_TP1"
    assert state.be_armed is True
    assert state.current_sl == 100.05

    # Bar 2 collapses back to 98.0, hitting the BE SL at 100.05
    candle2 = (106.0, 106.5, 98.0, 99.0)
    dec2 = evaluate_trade_exit(state, candle2, config=cfg)
    assert dec2.action == "FULL_EXIT"
    assert dec2.exit_reason == "BREAKEVEN"
    assert dec2.exit_price == 100.05
    assert dec2.closed_pct == 0.5
    assert round(dec2.realized_r, 4) == 0.0050
    assert state.size_remaining_pct == 0.0


def test_underwater_time_stop():
    cfg = ExitConfig(underwater_bars_limit=5, underwater_r_threshold=-0.15)
    state = create_trade_state("T_TIME", "BTCUSDT", "LONG", 100.0, 90.0, config=cfg)

    # Bars 1 through 4: Chop slightly underwater (-0.2R)
    for _ in range(4):
        candle = (99.0, 99.5, 97.5, 98.0)  # close r = -0.2
        dec = evaluate_trade_exit(state, candle, config=cfg)
        assert dec.action == "HOLD"

    # Bar 5: Reaches limit (bars_held=5), close is 97.0 (close r = -0.3 <= -0.15)
    candle5 = (98.0, 98.5, 96.5, 97.0)
    dec5 = evaluate_trade_exit(state, candle5, config=cfg)
    assert dec5.action == "FULL_EXIT"
    assert dec5.exit_reason == "TIME_STOP_UNDERWATER"
    assert dec5.exit_price == 97.0
    assert dec5.realized_r == -0.3
    assert state.size_remaining_pct == 0.0


def test_trailing_stop_activation():
    cfg = ExitConfig(
        tp1_r=0.5,
        tp2_r=5.0,  # High TP2 to allow trailing stop to control exit
        use_trailing_stop=True,
        trailing_activation_r=1.0,
        trailing_callback_r=0.3,
    )
    state = create_trade_state("T_TRAIL", "BTCUSDT", "LONG", 100.0, 90.0, config=cfg)
    # Risk dist = 10.0

    # Bar 1: Surges to 112.0 (+1.2R, touches TP1 at 105.0)
    candle1 = (100.0, 112.0, 99.0, 111.0)
    dec1 = evaluate_trade_exit(state, candle1, config=cfg)
    assert dec1.action == "PARTIAL_TP1"

    # Bar 2: Excursion pushes to 120.0 (+2.0R)
    candle2 = (111.0, 120.0, 110.0, 118.0)
    dec2 = evaluate_trade_exit(state, candle2, config=cfg)
    assert dec2.action == "HOLD"
    assert state.trailing_active is True
    # Highest R = 2.0. Trail SL = 100 + (2.0 - 0.3) * 10 = 117.0
    assert state.current_sl == 117.0

    # Bar 3: Pullback drops through 117.0 to 115.0
    candle3 = (118.0, 118.5, 115.0, 116.0)
    dec3 = evaluate_trade_exit(state, candle3, config=cfg)
    assert dec3.action == "FULL_EXIT"
    assert dec3.exit_reason == "TRAILING_STOP"
    assert dec3.exit_price == 117.0
    assert dec3.realized_r == 1.7


def test_same_candle_multi_target_hit_long():
    state = create_trade_state("T_MULTI_LONG", "BTCUSDT", "LONG", 100.0, 90.0)
    # Candle surges from 100 to 115, touching both TP1 (107.5) and TP2 (113.3)
    candle = (100.0, 115.0, 99.0, 114.0)
    dec = evaluate_trade_exit(state, candle)

    assert dec.action == "FULL_EXIT"
    assert dec.exit_reason == "TAKE_PROFIT_2"
    assert dec.closed_pct == 1.0
    assert state.size_remaining_pct == 0.0
    # Weighted exit price: 0.5 * 107.5 + 0.5 * 113.3 = 110.4
    assert dec.exit_price == pytest.approx(110.4, rel=1e-4)
    # Realized R: 0.5 * 0.75 + 0.5 * 1.33 = 1.04R
    assert dec.realized_r == pytest.approx(1.04, abs=0.01)
    assert dec.gross_return_pct == pytest.approx(10.4, abs=0.01)


def test_same_candle_multi_target_hit_short():
    state = create_trade_state("T_MULTI_SHORT", "BTCUSDT", "SHORT", 100.0, 110.0)
    # Candle plunges from 100 to 85, touching both TP1 (92.5) and TP2 (86.7)
    candle = (100.0, 101.0, 85.0, 86.0)
    dec = evaluate_trade_exit(state, candle)

    assert dec.action == "FULL_EXIT"
    assert dec.exit_reason == "TAKE_PROFIT_2"
    assert dec.closed_pct == 1.0
    assert state.size_remaining_pct == 0.0
    # Weighted exit price: 0.5 * 92.5 + 0.5 * 86.7 = 89.6
    assert dec.exit_price == pytest.approx(89.6, rel=1e-4)
    assert dec.realized_r == pytest.approx(1.04, abs=0.01)


def test_calculate_bracket_geometry():
    from engine.exits import calculate_bracket_geometry, ExitConfig

    # Long: Entry 100, Stop Loss 90 (risk_dist = 10, risk_pct = 10%)
    b_long = calculate_bracket_geometry("LONG", 100.0, 90.0)
    assert b_long["side"] == "LONG"
    assert b_long["limit_entry"] == 100.0
    assert b_long["stop_loss"] == 90.0
    assert b_long["risk_dist"] == 10.0
    assert b_long["risk_pct"] == 10.0
    assert b_long["tp1"] == 107.5  # +0.75R
    assert b_long["be_trigger"] == 108.5  # +0.85R excursion
    assert b_long["be_price"] == pytest.approx(100.05, abs=0.01)  # entry + 0.05%
    assert b_long["tp2"] == 113.3  # +1.33R
    assert b_long["stagnancy_bars"] == 12

    # Short: Entry 100, Stop Loss 110 (risk_dist = 10, risk_pct = 10%)
    b_short = calculate_bracket_geometry("SHORT", 100.0, 110.0)
    assert b_short["side"] == "SHORT"
    assert b_short["limit_entry"] == 100.0
    assert b_short["stop_loss"] == 110.0
    assert b_short["risk_dist"] == 10.0
    assert b_short["tp1"] == 92.5  # -0.75R
    assert b_short["be_trigger"] == 91.5  # -0.85R excursion
    assert b_short["be_price"] == pytest.approx(99.95, abs=0.01)  # entry - 0.05%
    assert b_short["tp2"] == 86.7  # -1.33R

