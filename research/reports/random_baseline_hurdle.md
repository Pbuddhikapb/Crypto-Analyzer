# Monte Carlo Random-Entry Baseline: The Statistical Hurdle
**Generated:** 2026-10-20 07:11 UTC  
**Iterations:** 1,000 Monte Carlo Passes  
**Dataset:** 10 Major Pairs (`BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, SUIUSDT`), 1-Hour Timeframe (2.8 Years, 24,528 continuous bars/coin from Binance USD-M Futures)  
**Friction Model:** Taker Fee 0.05%, Maker Fee 0.02%, Adverse Slippage 0.03%, 8h Funding Drag 0.01%  
**Exit Mechanics:** Stepped Scaling (50% TP1 @ +0.75R, BE armed @ +0.85R excursion, TP2 @ +1.33R, 12-bar stagnancy stop)

---

## 1. Executive Summary & The Alpha Hurdle

> **The Null Hypothesis ($H_0$):** A proposed trading strategy or indicator confluence rule possesses zero predictive edge. Any measured performance is indistinguishable from random coin flips governed by identical exit mechanics and portfolio friction.
>
> **The $p < 0.05$ Decision Rule:** To reject $H_0$ and prove genuine alpha, a strategy must achieve performance exceeding the **95th percentile ($p_{95}$)** of this 1,000-pass random baseline.

### Primary Alpha Hurdles:
* **Minimum Required Profit Factor:** **`>1.16`** (Median noise achieves `0.93`)
* **Minimum Required Win Rate:** **`>51.43%`** (Median noise achieves `46.13%`)
* **Minimum Required Expectancy ($EV$):** **`>+0.0602R`** (Median noise achieves `-0.0239R`)
* **Minimum Required Sharpe Ratio:** **`>0.22`** (Median noise achieves `-1.35`)
* **Maximum Acceptable Drawdown:** **`<22.48%`** (Worst 5% of noise suffers `36.11%`)

---

## 2. Empirical Distribution Table

| Performance Metric | 5th Pct ($p_5$) | 25th Pct ($p_{25}$) | 50th Pct ($p_{50}$ / Median) | 75th Pct ($p_{75}$) | 95th Pct ($p_{95}$ Hurdle) | Mean ± Std |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Win Rate (%)** | `41.06%` | `44.20%` | `46.13%` | `48.16%` | **`51.43%`** | `46.22 ± 3.09%` |
| **Profit Factor** | `0.74` | `0.85` | `0.93` | `1.01` | **`1.16`** | `0.94 ± 0.13` |
| **Expectancy per Trade (USD)** | `$-13.46` | `$-9.70` | `$-7.04` | `$-4.20` | **`$+0.84`** | `$-6.76 ± 4.43` |
| **Expectancy per Trade (R)** | `-0.1087R` | `-0.0570R` | `-0.0239R` | `+0.0082R` | **`+0.0602R`** | `-0.0242R ± 0.0516` |
| **Average Realized R** | `-0.1087R` | `-0.0570R` | `-0.0239R` | `+0.0082R` | **`+0.0602R`** | `-0.0241R ± 0.0516` |
| **Max Drawdown (%)** | `11.35%` | `17.23%` | `22.48%` | `28.30%` | **`36.11%`** | `22.99 ± 7.58%` |
| **Sharpe Ratio** | `-2.99` | `-1.98` | `-1.35` | `-0.77` | **`0.22`** | `-1.37 ± 0.99` |
| **Sortino Ratio** | `-3.74` | `-2.58` | `-1.80` | `-1.03` | **`0.32`** | `-1.78 ± 1.25` |
| **Net Portfolio Return (%)** | `-33.17%` | `-23.92%` | `-17.38%` | `-10.31%` | **`+2.06%`** | `-16.66 ± 10.93%` |

*Average trades generated per 2-year portfolio run: 246.4 trades.*

---

## 3. Quantitative Takeaways for Strategy Development

1. **The Cost of Friction**:
   Without an edge, median random trading loses money over 2 years (median net return is `-17.38%`, median expectancy is `-0.0239R`). This confirms the honesty of the simulation: exchange fees (0.05%), adverse slippage (0.03%), and 8h funding costs slowly bleed random entries.
2. **The Asymmetric Exit Effect**:
   Because of stepped scaling (+0.75R partial scale and breakeven arming), random entries can sometimes string together winning streaks that push profit factor up to `1.01`. This proves why examining simple backtests without a random benchmark leads retail traders to confuse asymmetric exit math with true entry edge.
3. **The Unbending Standard**:
   In Phase 3, any indicator signal, Smart Money Concept pattern, or confluence score must produce a Profit Factor >= 1.16 and an Expectancy >= +0.0602R over the exact same period to be declared statistically valid ($p < 0.05$).
