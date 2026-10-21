# H1: 4h Donchian breakout with ATR trailing exit
Registered: 2026-10-21, before any H1 code or results.
Signal (4h bars, at bar close, long and short):
  LONG if close > highest high of the previous N 4h bars
  SHORT if close < lowest low of the previous N 4h bars
Entry: open of the next 1h bar after the 4h bar closes.
Stop: 3.0 x ATR(14, 4h). R = that distance.
Exit: trailing stop 1R behind the best price since entry, moving only in the profit direction. No take-profit, no breakeven rule, no stagnancy exit.
Sizing: fixed_risk 1% of initial equity. Same costs as before.
Variants: H1a N=20, H1b N=55. Nothing else may change.
Pass rule: at least 100 trades, mean net R > 0, bootstrap CI lower bound > 0, matched p < 0.025 per variant.
Random baseline must use the SAME exit mode.
Kill rule: if a variant fails it stays failed. No tuning.
