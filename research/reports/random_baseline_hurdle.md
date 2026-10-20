# Monte Carlo Random-Entry Baseline: The Statistical Hurdle
**Generated:** 2026-10-20 01:48 UTC  
**Iterations:** 1,000 Monte Carlo Passes  
**Dataset:** 10 Major Pairs (`BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, SUIUSDT`), 1-Hour Timeframe (2+ Years, ~17,500 bars/coin)  
**Friction Model:** Taker Fee 0.05%, Maker Fee 0.02%, Adverse Slippage 0.03%, 8h Funding Drag 0.01%  
**Exit Mechanics:** Stepped Scaling (50% TP1 @ +0.75R, BE armed @ +0.85R excursion, TP2 @ +1.33R, 12-bar stagnancy stop)

---

## 1. Executive Summary & The Alpha Hurdle

> **The Null Hypothesis ($H_0$):** A proposed trading strategy or indicator confluence rule possesses zero predictive edge. Any measured performance is indistinguishable from random coin flips governed by identical exit mechanics and portfolio friction.
>
> **The $p < 0.05$ Decision Rule:** To reject $H_0$ and prove genuine alpha, a strategy must achieve performance exceeding the **95th percentile ($p_{95}$)** of this 1,000-pass random baseline.

### Primary Alpha Hurdles:
* **Minimum Required Profit Factor:** **`>1.14`** (Median noise achieves `0.92`)
* **Minimum Required Win Rate:** **`>50.00%`** (Median noise achieves `44.79%`)
* **Minimum Required Expectancy ($EV$):** **`>+0.0520R`** (Median noise achieves `-0.0271R`)
* **Minimum Required Sharpe Ratio:** **`>0.07`** (Median noise achieves `-1.43`)
* **Maximum Acceptable Drawdown:** **`<23.26%`** (Worst 5% of noise suffers `36.95%`)

---

## 2. Empirical Distribution Table

| Performance Metric | 5th Pct ($p_5$) | 25th Pct ($p_{25}$) | 50th Pct ($p_{50}$ / Median) | 75th Pct ($p_{75}$) | 95th Pct ($p_{95}$ Hurdle) | Mean ± Std |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Win Rate (%)** | `39.92%` | `42.74%` | `44.79%` | `46.75%` | **`50.00%`** | `44.82 ± 3.07%` |
| **Profit Factor** | `0.73` | `0.84` | `0.92` | `1.01` | **`1.14`** | `0.93 ± 0.13` |
| **Expectancy per Trade (USD)** | `$-13.92` | `$-10.27` | `$-7.34` | `$-4.33` | **`$+0.03`** | `$-7.15 ± 4.37` |
| **Expectancy per Trade (R)** | `-0.1144R` | `-0.0639R` | `-0.0271R` | `+0.0076R` | **`+0.0520R`** | `-0.0282R ± 0.0520` |
| **Average Realized R** | `-0.1144R` | `-0.0639R` | `-0.0271R` | `+0.0076R` | **`+0.0520R`** | `-0.0282R ± 0.0520` |
| **Max Drawdown (%)** | `11.17%` | `17.71%` | `23.26%` | `28.83%` | **`36.95%`** | `23.49 ± 7.78%` |
| **Sharpe Ratio** | `-3.14` | `-2.12` | `-1.43` | `-0.78` | **`0.07`** | `-1.46 ± 1.00` |
| **Sortino Ratio** | `-3.93` | `-2.77` | `-1.91` | `-1.06` | **`0.10`** | `-1.90 ± 1.25` |
| **Net Portfolio Return (%)** | `-34.09%` | `-25.14%` | `-17.95%` | `-10.56%` | **`+0.07%`** | `-17.47 ± 10.68%` |

*Average trades generated per 2-year portfolio run: 244.3 trades.*

---

## 3. Quantitative Takeaways for Strategy Development

1. **The Cost of Friction**:
   Without an edge, median random trading loses money over 2 years (median net return is `-17.95%`, median expectancy is `-0.0271R`). This confirms the honesty of the simulation: exchange fees (0.05%), adverse slippage (0.03%), and 8h funding costs slowly bleed random entries.
2. **The Asymmetric Exit Effect**:
   Because of stepped scaling (+0.75R partial scale and breakeven arming), random entries can sometimes string together winning streaks that push profit factor up to `1.01`. This proves why examining simple backtests without a random benchmark leads retail traders to confuse asymmetric exit math with true entry edge.
3. **The Unbending Standard**:
   In Phase 3, any indicator signal, Smart Money Concept pattern, or confluence score must produce a Profit Factor >= 1.14 and an Expectancy >= +0.0520R over the exact same period to be declared statistically valid ($p < 0.05$).
