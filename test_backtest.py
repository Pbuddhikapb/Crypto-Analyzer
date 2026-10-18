import pytest
from backtest import replay_trade_1m


def test_replay_trade_1m_take_profit_precision(monkeypatch):
    """Verify that replay_trade_1m resolves Take Profit on the exact minute."""
    # Synthetic trade: LONG Entry at 100.0, SL at 98.0, TP at 103.0
    active_trade = {
        "side": "LONG",
        "entry_price": 100.0,
        "stop_loss": 98.0,
        "take_profit": 103.0,
        "risk_dist": 2.0,
    }

    # Synthetic 1m candles: minute 1 ranges 99.5 to 101.0, minute 2 ranges 100.5 to 103.5 (hits TP)
    synthetic_1m = [
        (1000000, 100.0, 101.0, 99.5, 100.5, 10.0),
        (1060000, 100.5, 103.5, 100.2, 103.0, 25.0),
    ]

    monkeypatch.setattr("backtest.fetch_1m_candles_for_window", lambda *args, **kwargs: synthetic_1m)

    res = replay_trade_1m("BTCUSDT", active_trade, 1000000)
    assert res is not None
    assert res["reason"] == "TAKE_PROFIT"
    assert res["exit_price"] == 103.0
    assert res["hold_minutes"] == 2
    assert res["net_return_pct"] > 0.0


def test_replay_trade_1m_breakeven_trailing_rescue(monkeypatch):
    """Verify that replay_trade_1m arms breakeven stop at +1.0R and stops out at breakeven if price reverses."""
    # Synthetic trade: LONG Entry 100.0, SL 98.0 (risk 2.0), TP 104.0 (+2.0R)
    active_trade = {
        "side": "LONG",
        "entry_price": 100.0,
        "stop_loss": 98.0,
        "take_profit": 104.0,
        "risk_dist": 2.0,
    }

    # Minute 1: reaches 102.2 (>= 100 + 2.0 -> +1.0R reached!), closes at 101.8
    # Minute 2: reverses and drops to 99.8 (below breakeven SL at 100.05)
    synthetic_1m = [
        (1000000, 100.0, 102.2, 99.9, 101.8, 15.0),
        (1060000, 101.8, 101.9, 99.8, 99.9, 20.0),
    ]

    monkeypatch.setattr("backtest.fetch_1m_candles_for_window", lambda *args, **kwargs: synthetic_1m)

    res = replay_trade_1m("BTCUSDT", active_trade, 1000000)
    assert res is not None
    assert res["reason"] == "STOP_LOSS"
    assert pytest.approx(res["exit_price"], rel=1e-4) == 100.05
    assert res["breakeven_triggered"] is True
    assert res["hold_minutes"] == 2
    # Net loss should only be the fee cushion drag (~ -0.10%), NOT the full -2.15% loss
    assert -0.20 < res["net_return_pct"] < 0.0
