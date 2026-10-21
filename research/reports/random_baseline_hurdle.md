# Monte Carlo Random-Entry Baseline: The Statistical Hurdle
**Generated:** 2026-10-20 20:10 UTC  
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
* **Minimum Required Profit Factor:** **`>1.04`** (Median noise achieves `0.83`)
* **Minimum Required Win Rate:** **`>52.08%`** (Median noise achieves `46.76%`)
* **Minimum Required Expectancy ($EV$):** **`>+0.0164R`** (Median noise achieves `-0.0762R`)
* **Minimum Required Sharpe Ratio:** **`>0.30`** (Median noise achieves `-1.40`)
* **Maximum Acceptable Drawdown:** **`<25.26%`** (Worst 5% of noise suffers `44.61%`)

---

## 2. Empirical Distribution Table

| Performance Metric | 5th Pct ($p_5$) | 25th Pct ($p_{25}$) | 50th Pct ($p_{50}$ / Median) | 75th Pct ($p_{75}$) | 95th Pct ($p_{95}$ Hurdle) | Mean ± Std |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Win Rate (%)** | `41.46%` | `44.76%` | `46.76%` | `49.00%` | **`52.08%`** | `46.83 ± 3.33%` |
| **Profit Factor** | `0.66` | `0.76` | `0.83` | `0.91` | **`1.04`** | `0.84 ± 0.12` |
| **Expectancy per Trade (USD)** | `$-16.40` | `$-11.14` | `$-7.62` | `$-3.94` | **`$+1.64`** | `$-7.58 ± 5.69` |
| **Expectancy per Trade (R)** | `-0.1640R` | `-0.1114R` | `-0.0762R` | `-0.0394R` | **`+0.0164R`** | `-0.0758R ± 0.0569` |
| **Average Realized R** | `-0.1640R` | `-0.1114R` | `-0.0762R` | `-0.0394R` | **`+0.0164R`** | `-0.0758R ± 0.0569` |
| **Max Drawdown (%)** | `11.05%` | `17.99%` | `25.26%` | `32.21%` | **`44.61%`** | `25.91 ± 10.54%` |
| **Sharpe Ratio** | `-3.06` | `-2.06` | `-1.40` | `-0.72` | **`0.30`** | `-1.39 ± 1.05` |
| **Sortino Ratio** | `-3.80` | `-2.67` | `-1.85` | `-0.98` | **`0.42`** | `-1.79 ± 1.33` |
| **Net Portfolio Return (%)** | `-40.43%` | `-27.39%` | `-18.82%` | `-9.77%` | **`+4.00%`** | `-18.66 ± 14.03%` |

*Average trades generated per 2-year portfolio run: 246.3 trades.*

---

## 3. Quantitative Takeaways for Strategy Development

1. **The Cost of Friction**:
   Without an edge, median random trading loses money over 2 years (median net return is `-18.82%`, median expectancy is `-0.0762R`). This confirms the honesty of the simulation: exchange fees (0.05%), adverse slippage (0.03%), and 8h funding costs slowly bleed random entries.
2. **The Asymmetric Exit Effect**:
   Because of stepped scaling (+0.75R partial scale and breakeven arming), random entries can sometimes string together winning streaks that push profit factor up to `0.91`. This proves why examining simple backtests without a random benchmark leads retail traders to confuse asymmetric exit math with true entry edge.
3. **The Unbending Standard**:
   In Phase 3, any indicator signal, Smart Money Concept pattern, or confluence score must produce a Profit Factor >= 1.04 and an Expectancy >= +0.0164R over the exact same period to be declared statistically valid ($p < 0.05$).
