# Monte Carlo Random-Entry Baseline: The Statistical Hurdle
**Generated:** 2026-10-21 09:36 UTC  
**Iterations:** 1,000 Monte Carlo Passes  
**Dataset:** 10 Major Pairs (`BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, SUIUSDT`), 1-Hour Timeframe (5.29 Years, 25,992 to 46,392 continuous bars/coin from Binance USD-M Futures)  
**Friction Model:** Taker Fee 0.05%, Maker Fee 0.02%, Adverse Slippage 0.03%, 8h Funding Drag 0.01%  
**Exit Mechanics:** Stepped Scaling (50% TP1 @ +0.75R, BE armed @ +0.85R excursion, TP2 @ +1.33R, 12-bar stagnancy stop)

---

## 1. Executive Summary & The Alpha Hurdle

> **The Null Hypothesis ($H_0$):** A proposed trading strategy or indicator confluence rule possesses zero predictive edge. Any measured performance is indistinguishable from random coin flips governed by identical exit mechanics and portfolio friction.
>
> **The $p < 0.05$ Decision Rule:** To reject $H_0$ and prove genuine alpha, a strategy must achieve performance exceeding the **95th percentile ($p_{95}$)** of this 1,000-pass random baseline.

### Primary Alpha Hurdles:
* **Minimum Required Profit Factor:** **`>1.04`** (Median noise achieves `0.83`)
* **Minimum Required Win Rate:** **`>52.40%`** (Median noise achieves `47.37%`)
* **Minimum Required Expectancy ($EV$):** **`>+0.0152R`** (Median noise achieves `-0.0769R`)
* **Minimum Required Sharpe Ratio:** **`>0.27`** (Median noise achieves `-1.40`)
* **Maximum Acceptable Drawdown:** **`<25.53%`** (Worst 5% of noise suffers `45.12%`)

---

## 2. Empirical Distribution Table

| Performance Metric | 5th Pct ($p_5$) | 25th Pct ($p_{25}$) | 50th Pct ($p_{50}$ / Median) | 75th Pct ($p_{75}$) | 95th Pct ($p_{95}$ Hurdle) | Mean ± Std |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Win Rate (%)** | `41.77%` | `45.16%` | `47.37%` | `49.40%` | **`52.40%`** | `47.27 ± 3.17%` |
| **Profit Factor** | `0.65` | `0.75` | `0.83` | `0.91` | **`1.04`** | `0.83 ± 0.12` |
| **Expectancy per Trade (USD)** | `$-17.07` | `$-11.54` | `$-7.70` | `$-3.92` | **`$+1.52`** | `$-7.73 ± 5.51` |
| **Expectancy per Trade (R)** | `-0.1707R` | `-0.1154R` | `-0.0769R` | `-0.0392R` | **`+0.0152R`** | `-0.0773R ± 0.0551` |
| **Average Realized R** | `-0.1707R` | `-0.1154R` | `-0.0769R` | `-0.0392R` | **`+0.0152R`** | `-0.0773R ± 0.0551` |
| **Max Drawdown (%)** | `10.86%` | `18.75%` | `25.53%` | `33.05%` | **`45.12%`** | `26.30 ± 10.29%` |
| **Sharpe Ratio** | `-3.12` | `-2.11` | `-1.40` | `-0.71` | **`0.27`** | `-1.41 ± 1.01` |
| **Sortino Ratio** | `-3.88` | `-2.72` | `-1.85` | `-0.97` | **`0.39`** | `-1.81 ± 1.27` |
| **Net Portfolio Return (%)** | `-42.46%` | `-28.66%` | `-19.08%` | `-9.73%` | **`+3.75%`** | `-19.20 ± 13.68%` |

*Average trades generated per portfolio run (5.29 years): 248.3 trades.*

---

## 3. Quantitative Takeaways for Strategy Development

1. **The Cost of Friction**:
   Without an edge, median random trading loses money over the full evaluation timeline (median net return is `-19.08%`, median expectancy is `-0.0769R`). This confirms the honesty of the simulation: exchange fees (0.05%), adverse slippage (0.03%), and 8h funding costs slowly bleed random entries.
2. **The Asymmetric Exit Effect**:
   Because of stepped scaling (+0.75R partial scale and breakeven arming), random entries can sometimes string together winning streaks that push profit factor up to `0.91`. This proves why examining simple backtests without a random benchmark leads retail traders to confuse asymmetric exit math with true entry edge.
3. **The Unbending Standard**:
   In Phase 3, any indicator signal, Smart Money Concept pattern, or confluence score must produce a Profit Factor >= 1.04 and an Expectancy >= +0.0152R over the exact same period to be declared statistically valid ($p < 0.05$).
