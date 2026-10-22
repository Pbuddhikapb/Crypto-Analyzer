# Strategy Validation Dossier: Evidence Before Infrastructure
**Generated:** 2026-10-22 03:08 UTC  
**Git Commit:** `a94feccbf9f1df1edd8c8736ead052fe193f56a5+dirty`  
**Dataset Hash:** `61d0448603612d76`  
**Architecture Doctrine:** Claude's Option B (Discretionary Human Trading Terminal)  
**Null Hypothesis ($H_0$):** Phase 2 1,000-pass Monte Carlo Baseline Hurdle  
**Dataset:** 10 Major Pairs (`BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, SUIUSDT`), 5.3 Years (Binance USD-M Futures 1h candles)  
* **Per-Symbol 1h Bar Counts:** BTCUSDT: 46,393, ETHUSDT: 46,393, SOLUSDT: 46,273, BNBUSDT: 46,393, XRPUSDT: 46,273, DOGEUSDT: 46,393, ADAUSDT: 46,393, AVAXUSDT: 46,393, LINKUSDT: 46,393, SUIUSDT: 25,929  
* **Data Continuity & Coverage Notes:**
  - **SUIUSDT starts later:** SUI USD-M Perpetual contract was listed on Binance on 2023-05-03 16:00 UTC (25,929 bars vs 46,393 bars full period).
  - **Coins with maintenance gaps (SOLUSDT, XRPUSDT):** 120 bars missing due to Binance historical downtime (73h: 2022-02-25 23:00 to 2022-03-01 00:00 UTC, and 49h: 2022-03-31 23:00 to 2022-04-03 00:00 UTC).
**Execution Authority:** Single Exit Authority (`engine/exits.py`)  
**Institutional Friction:** Taker Fee 0.05%, Maker Fee 0.02%, Slippage 0.03%, 8h Funding Drag 0.01%  
**Exit Mechanics:** Stepped Scaling (50% TP1 @ +0.75R, BE armed @ +0.85R excursion, TP2 @ +1.33R, 12-bar stagnancy)

---

## 1. Executive Summary & Big Picture

Under Claude's doctrine (*"Evidence before infrastructure"*), no trading signal or indicator rule is permitted to enter the Discretionary Trading Terminal without empirical proof that it outperforms random noise ($p < 0.05$).

In Phase 2, we established the empirical null hypothesis ($H_0$) across 1,000 Monte Carlo runs on Binance USD-M Futures under full institutional friction:
* **Profit Factor Hurdle ($p_{95}$):** `> 1.04` (Median noise: `0.83`)
* **Win Rate Hurdle ($p_{95}$):** `> 52.40%` (Median noise: `47.27%`)
* **Expectancy Hurdle ($p_{95}$):** `> +0.0152R` (Median noise: `-0.0773R`)
* **Sharpe Ratio Hurdle ($p_{95}$):** `> 0.27` (Median noise: `-1.41`)
* **Max Drawdown Hurdle ($p_{50}$):** `< 25.53%` (Worst 5% noise: `45.12%`)

6 core discretionary trading hypotheses were codified with strict zero lookahead (signal at bar $i$ close $\rightarrow$ entry at bar $i+1$ open) and simulated across all 10 coins.

---

## 2. Master Strategy Scorecard

| Strategy | Trades | Win Rate | Net PF (Gross) | Net EV (Gross EV) | Max DD (R) | Bootstrap 95% CI | Matched Median EV | Matched Rank $p$-value | Days Evaluated | Sizing | Ruined? | Alpha Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **TrendPullback** | 6625 | 49.2% | **0.78** (0.89) | **-0.1099R** (-0.0497R) | 732.51R | [-0.1323, -0.0876]R | -0.1030R | `p = 0.7373` | 1933d (100.0%) | `fixed_risk` | ⚠️ Ruined (2022-02-07) | 🔴 **REJECTED** |
| **FVGRetest** | 7856 | 46.9% | **0.84** (0.93) | **-0.0718R** (-0.0285R) | 597.04R | [-0.0911, -0.0523]R | -0.0675R | `p = 0.6633` | 1933d (100.0%) | `fixed_risk` | ⚠️ Ruined (2022-05-24) | 🔴 **REJECTED** |
| **ConfluenceThreshold** | 17166 | 47.0% | **0.71** (0.80) | **-0.1513R** (-0.0940R) | 2606.85R | [-0.1654, -0.1373]R | -0.0969R | `p = 1.0000` | 1933d (100.0%) | `fixed_risk` | ⚠️ Ruined (2021-03-23) | 🔴 **REJECTED** |
| **LiquiditySweep** | 22462 | 46.9% | **0.62** (0.75) | **-0.2157R** (-0.1239R) | 4846.73R | [-0.2285, -0.2029]R | -0.1654R | `p = 1.0000` | 1933d (100.0%) | `fixed_risk` | ⚠️ Ruined (2021-01-27) | 🔴 **REJECTED** |
| **Donchian4hN20** | 1317 | 33.7% | **0.87** (0.93) | **-0.0522R** (-0.0271R) | 93.90R | [-0.1072, +0.0066]R | -0.2189R | `p = 0.0010` | 1933d (100.0%) | `fixed_risk` | No | 🔴 **REJECTED** |
| **Donchian4hN55** | 916 | 34.1% | **0.96** (1.02) | **-0.0153R** (+0.0078R) | 49.19R | [-0.0832, +0.0580]R | -0.2244R | `p = 0.0010` | 1933d (100.0%) | `fixed_risk` | No | 🔴 **REJECTED** |

---

## 3. Deep-Dive Strategy Evaluations

### Hypothesis: TrendPullback
**Concept:** 4h Macro Trend + 1h EMA 50 Pullback + RSI Momentum Recovery

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 0.7373`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `33501` | Executed Trades: `6625`  
> Net Profit Factor: **`0.78`** (Gross: `0.89`)  
> Net Expectancy: **`-0.1099R`** (Gross: `-0.0497R`)  
> Max Drawdown (R): **`732.51R`** | Cumulative Net R: **`-727.94R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.1323R, -0.0876R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.1030R`** (Matched 95th Pct Hurdle: `-0.0773R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2022-02-07 03:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 6625 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.1099R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.1323R <= 0.0R
- ❌ Matched Rank p-value 0.7373 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.78 (Null p95 Hurdle: 1.04, p=0.6783)
- ℹ️ Net Win Rate: 49.25% (Null p95 Hurdle: 52.40%, p=0.2663)
- ℹ️ Max Drawdown (R): 732.51R
- ℹ️ Sharpe Ratio: -1.66 (p=0.5983)
- ℹ️ Median Stop % of Price: 1.72%
- ℹ️ Cost Drag in R per Trade: +0.0602R
- ℹ️ Median Holding Time: 6 bars (6h)
- ℹ️ Trades per Symbol: ADAUSDT: 594, AVAXUSDT: 582, BNBUSDT: 741, BTCUSDT: 826, DOGEUSDT: 641, ETHUSDT: 784, LINKUSDT: 566, SOLUSDT: 817, SUIUSDT: 327, XRPUSDT: 747
- ℹ️ Per-Year Net R: 2021: -79.17R, 2022: -121.55R, 2023: -139.07R, 2024: -182.68R, 2025: -139.56R, 2026: -65.91R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 594 | 50.5% | 0.81 (0.92) | $-5,286.59 | -0.0890R (-0.0361R) |
| `AVAXUSDT` | 582 | 46.4% | 0.69 (0.77) | $-9,280.86 | -0.1595R (-0.1110R) |
| `BNBUSDT` | 741 | 51.3% | 0.82 (0.98) | $-6,233.33 | -0.0841R (-0.0093R) |
| `BTCUSDT` | 826 | 49.9% | 0.79 (0.96) | $-8,156.06 | -0.0987R (-0.0152R) |
| `DOGEUSDT` | 641 | 48.0% | 0.73 (0.82) | $-8,912.00 | -0.1390R (-0.0854R) |
| `ETHUSDT` | 784 | 52.4% | 0.89 (1.03) | $-4,034.52 | -0.0515R (+0.0137R) |
| `LINKUSDT` | 566 | 46.5% | 0.72 (0.81) | $-8,111.92 | -0.1433R (-0.0903R) |
| `SOLUSDT` | 817 | 48.7% | 0.77 (0.85) | $-9,166.58 | -0.1122R (-0.0659R) |
| `SUIUSDT` | 327 | 45.9% | 0.70 (0.77) | $-5,094.92 | -0.1558R (-0.1107R) |
| `XRPUSDT` | 747 | 49.7% | 0.76 (0.88) | $-8,517.45 | -0.1140R (-0.0515R) |

---

### Hypothesis: FVGRetest
**Concept:** HTF Trend + SMC Fair Value Gap Retest & Mitigation Rejection

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 0.6633`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `28990` | Executed Trades: `7856`  
> Net Profit Factor: **`0.84`** (Gross: `0.93`)  
> Net Expectancy: **`-0.0718R`** (Gross: `-0.0285R`)  
> Max Drawdown (R): **`597.04R`** | Cumulative Net R: **`-563.77R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.0911R, -0.0523R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.0675R`** (Matched 95th Pct Hurdle: `-0.0431R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2022-05-24 09:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 7856 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.0718R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.0911R <= 0.0R
- ❌ Matched Rank p-value 0.6633 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.84 (Null p95 Hurdle: 1.04, p=0.4768)
- ℹ️ Net Win Rate: 46.92% (Null p95 Hurdle: 52.40%, p=0.5443)
- ℹ️ Max Drawdown (R): 597.04R
- ℹ️ Sharpe Ratio: -1.18 (p=0.4105)
- ℹ️ Median Stop % of Price: 2.22%
- ℹ️ Cost Drag in R per Trade: +0.0432R
- ℹ️ Median Holding Time: 7 bars (7h)
- ℹ️ Trades per Symbol: ADAUSDT: 739, AVAXUSDT: 714, BNBUSDT: 902, BTCUSDT: 947, DOGEUSDT: 710, ETHUSDT: 999, LINKUSDT: 638, SOLUSDT: 967, SUIUSDT: 397, XRPUSDT: 843
- ℹ️ Per-Year Net R: 2021: -27.74R, 2022: -151.85R, 2023: -117.39R, 2024: -115.11R, 2025: -106.31R, 2026: -45.38R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 739 | 47.4% | 0.78 (0.87) | $-7,293.57 | -0.0987R (-0.0561R) |
| `AVAXUSDT` | 714 | 50.8% | 0.94 (1.02) | $-1,960.88 | -0.0275R (+0.0098R) |
| `BNBUSDT` | 902 | 44.1% | 0.86 (0.97) | $-5,282.56 | -0.0586R (-0.0108R) |
| `BTCUSDT` | 947 | 42.8% | 0.80 (0.93) | $-7,384.18 | -0.0780R (-0.0265R) |
| `DOGEUSDT` | 710 | 47.5% | 0.78 (0.86) | $-7,118.06 | -0.1003R (-0.0591R) |
| `ETHUSDT` | 999 | 48.8% | 0.95 (1.06) | $-2,055.32 | -0.0206R (+0.0238R) |
| `LINKUSDT` | 638 | 47.3% | 0.81 (0.90) | $-5,474.03 | -0.0858R (-0.0453R) |
| `SOLUSDT` | 967 | 46.3% | 0.73 (0.80) | $-12,743.11 | -0.1318R (-0.0919R) |
| `SUIUSDT` | 397 | 48.1% | 0.82 (0.90) | $-3,342.31 | -0.0842R (-0.0450R) |
| `XRPUSDT` | 843 | 47.9% | 0.90 (1.00) | $-3,723.26 | -0.0442R (-0.0012R) |

---

### Hypothesis: ConfluenceThreshold
**Concept:** Multi-Timeframe 0-100 Confluence Model Triggered at High Conviction (>= 75)

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 1.0000`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `187951` | Executed Trades: `17166`  
> Net Profit Factor: **`0.71`** (Gross: `0.80`)  
> Net Expectancy: **`-0.1513R`** (Gross: `-0.0940R`)  
> Max Drawdown (R): **`2606.85R`** | Cumulative Net R: **`-2597.26R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.1654R, -0.1373R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.0969R`** (Matched 95th Pct Hurdle: `-0.0750R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2021-03-23 20:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 17166 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.1513R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.1654R <= 0.0R
- ❌ Matched Rank p-value 1.0000 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.71 (Null p95 Hurdle: 1.04, p=0.8580)
- ℹ️ Net Win Rate: 46.97% (Null p95 Hurdle: 52.40%, p=0.5380)
- ℹ️ Max Drawdown (R): 2606.85R
- ℹ️ Sharpe Ratio: -2.40 (p=0.8368)
- ℹ️ Median Stop % of Price: 1.82%
- ℹ️ Cost Drag in R per Trade: +0.0573R
- ℹ️ Median Holding Time: 4 bars (4h)
- ℹ️ Trades per Symbol: ADAUSDT: 1555, AVAXUSDT: 1527, BNBUSDT: 1865, BTCUSDT: 2294, DOGEUSDT: 1721, ETHUSDT: 2285, LINKUSDT: 1232, SOLUSDT: 2168, SUIUSDT: 747, XRPUSDT: 1772
- ℹ️ Per-Year Net R: 2021: -504.47R, 2022: -416.65R, 2023: -546.25R, 2024: -480.77R, 2025: -449.06R, 2026: -200.05R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 1555 | 47.7% | 0.73 (0.82) | $-21,371.05 | -0.1374R (-0.0854R) |
| `AVAXUSDT` | 1527 | 44.9% | 0.67 (0.74) | $-27,276.82 | -0.1786R (-0.1310R) |
| `BNBUSDT` | 1865 | 46.2% | 0.67 (0.78) | $-34,000.28 | -0.1823R (-0.1105R) |
| `BTCUSDT` | 2294 | 47.2% | 0.70 (0.84) | $-34,934.43 | -0.1523R (-0.0738R) |
| `DOGEUSDT` | 1721 | 45.5% | 0.67 (0.75) | $-30,316.31 | -0.1762R (-0.1252R) |
| `ETHUSDT` | 2285 | 48.7% | 0.77 (0.88) | $-26,047.88 | -0.1140R (-0.0521R) |
| `LINKUSDT` | 1232 | 42.7% | 0.61 (0.67) | $-27,596.97 | -0.2240R (-0.1754R) |
| `SOLUSDT` | 2168 | 48.4% | 0.76 (0.84) | $-26,137.88 | -0.1206R (-0.0770R) |
| `SUIUSDT` | 747 | 48.2% | 0.79 (0.86) | $-8,000.31 | -0.1071R (-0.0657R) |
| `XRPUSDT` | 1772 | 48.6% | 0.73 (0.83) | $-24,043.98 | -0.1357R (-0.0779R) |

---

### Hypothesis: LiquiditySweep
**Concept:** Swing Failure Pattern (SFP) sweeping key liquidity with rejection

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 1.0000`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `73577` | Executed Trades: `22462`  
> Net Profit Factor: **`0.62`** (Gross: `0.75`)  
> Net Expectancy: **`-0.2157R`** (Gross: `-0.1239R`)  
> Max Drawdown (R): **`4846.73R`** | Cumulative Net R: **`-4845.14R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.2285R, -0.2029R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.1654R`** (Matched 95th Pct Hurdle: `-0.1350R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2021-01-27 10:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 22462 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.2157R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.2285R <= 0.0R
- ❌ Matched Rank p-value 1.0000 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.62 (Null p95 Hurdle: 1.04, p=0.9681)
- ℹ️ Net Win Rate: 46.90% (Null p95 Hurdle: 52.40%, p=0.5468)
- ℹ️ Max Drawdown (R): 4846.73R
- ℹ️ Sharpe Ratio: -2.64 (p=0.8886)
- ℹ️ Median Stop % of Price: 0.88%
- ℹ️ Cost Drag in R per Trade: +0.0919R
- ℹ️ Median Holding Time: 2 bars (2h)
- ℹ️ Trades per Symbol: ADAUSDT: 2165, AVAXUSDT: 2061, BNBUSDT: 2349, BTCUSDT: 2523, DOGEUSDT: 2413, ETHUSDT: 2658, LINKUSDT: 2110, SOLUSDT: 2567, SUIUSDT: 1187, XRPUSDT: 2429
- ℹ️ Per-Year Net R: 2021: -1031.82R, 2022: -808.11R, 2023: -825.46R, 2024: -972.91R, 2025: -920.41R, 2026: -286.42R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 2165 | 47.3% | 0.63 (0.75) | $-47,313.01 | -0.2185R (-0.1278R) |
| `AVAXUSDT` | 2061 | 44.8% | 0.60 (0.71) | $-49,668.52 | -0.2410R (-0.1563R) |
| `BNBUSDT` | 2349 | 47.3% | 0.63 (0.78) | $-48,383.08 | -0.2060R (-0.1066R) |
| `BTCUSDT` | 2523 | 47.2% | 0.63 (0.79) | $-51,041.03 | -0.2023R (-0.0991R) |
| `DOGEUSDT` | 2413 | 45.5% | 0.58 (0.70) | $-59,664.68 | -0.2473R (-0.1570R) |
| `ETHUSDT` | 2658 | 47.4% | 0.63 (0.76) | $-55,844.87 | -0.2101R (-0.1157R) |
| `LINKUSDT` | 2110 | 45.8% | 0.60 (0.72) | $-49,872.42 | -0.2364R (-0.1456R) |
| `SOLUSDT` | 2567 | 47.9% | 0.65 (0.77) | $-50,393.32 | -0.1963R (-0.1138R) |
| `SUIUSDT` | 1187 | 48.1% | 0.67 (0.80) | $-21,772.00 | -0.1834R (-0.0983R) |
| `XRPUSDT` | 2429 | 47.8% | 0.63 (0.77) | $-50,560.61 | -0.2082R (-0.1152R) |

---

### Hypothesis: Donchian4hN20
**Concept:** 4h Donchian Breakout N=20 + ATR(14) 3R Trailing Stop (H1a)

> **Statistical Verdict**: **`REJECTED (p >= 0.025)`**  
> Matched Rank $p$-value: **`p = 0.0010`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `9755` | Executed Trades: `1317`  
> Net Profit Factor: **`0.87`** (Gross: `0.93`)  
> Net Expectancy: **`-0.0522R`** (Gross: `-0.0271R`)  
> Max Drawdown (R): **`93.90R`** | Cumulative Net R: **`-68.70R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.1072R, +0.0066R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.2189R`** (Matched 95th Pct Hurdle: `-0.1655R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `NO`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 1317 >= 100 trades
- ✅ Matched Rank p-value 0.0010 < 0.025

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.0522R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.1072R <= 0.0R

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.87 (Null p95 Hurdle: 1.04, p=0.3749)
- ℹ️ Net Win Rate: 33.71% (Null p95 Hurdle: 52.40%, p=0.9999)
- ℹ️ Max Drawdown (R): 93.90R
- ℹ️ Sharpe Ratio: -1.39 (p=0.4927)
- ℹ️ Median Stop % of Price: 7.26%
- ℹ️ Cost Drag in R per Trade: +0.0252R
- ℹ️ Median Holding Time: 51 bars (51h)
- ℹ️ Trades per Symbol: ADAUSDT: 104, AVAXUSDT: 139, BNBUSDT: 145, BTCUSDT: 203, DOGEUSDT: 104, ETHUSDT: 161, LINKUSDT: 100, SOLUSDT: 178, SUIUSDT: 62, XRPUSDT: 121
- ℹ️ Per-Year Net R: 2021: -36.29R, 2022: -12.26R, 2023: -2.24R, 2024: +2.04R, 2025: -8.43R, 2026: -11.52R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 104 | 38.5% | 1.19 (1.27) | $+694.03 | +0.0667R (+0.0906R) |
| `AVAXUSDT` | 139 | 32.4% | 0.85 (0.89) | $-812.69 | -0.0585R (-0.0394R) |
| `BNBUSDT` | 145 | 31.7% | 0.91 (0.99) | $-546.37 | -0.0377R (-0.0033R) |
| `BTCUSDT` | 203 | 33.0% | 0.76 (0.85) | $-1,916.32 | -0.0944R (-0.0561R) |
| `DOGEUSDT` | 104 | 32.7% | 0.87 (0.91) | $-561.32 | -0.0540R (-0.0366R) |
| `ETHUSDT` | 161 | 36.0% | 1.13 (1.21) | $+810.87 | +0.0504R (+0.0767R) |
| `LINKUSDT` | 100 | 35.0% | 0.55 (0.59) | $-1,805.42 | -0.1805R (-0.1591R) |
| `SOLUSDT` | 178 | 34.3% | 0.65 (0.69) | $-2,417.76 | -0.1358R (-0.1167R) |
| `SUIUSDT` | 62 | 32.3% | 0.93 (0.97) | $-190.27 | -0.0307R (-0.0140R) |
| `XRPUSDT` | 121 | 31.4% | 0.97 (1.03) | $-124.27 | -0.0103R (+0.0122R) |

---

### Hypothesis: Donchian4hN55
**Concept:** 4h Donchian Breakout N=55 + ATR(14) 3R Trailing Stop (H1b)

> **Statistical Verdict**: **`REJECTED (p >= 0.025)`**  
> Matched Rank $p$-value: **`p = 0.0010`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `5274` | Executed Trades: `916`  
> Net Profit Factor: **`0.96`** (Gross: `1.02`)  
> Net Expectancy: **`-0.0153R`** (Gross: `+0.0078R`)  
> Max Drawdown (R): **`49.19R`** | Cumulative Net R: **`-14.06R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.0832R, +0.0580R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.2244R`** (Matched 95th Pct Hurdle: `-0.1570R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `NO`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 916 >= 100 trades
- ✅ Matched Rank p-value 0.0010 < 0.025

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.0153R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.0832R <= 0.0R

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.96 (Null p95 Hurdle: 1.04, p=0.1355)
- ℹ️ Net Win Rate: 34.06% (Null p95 Hurdle: 52.40%, p=0.9999)
- ℹ️ Max Drawdown (R): 49.19R
- ℹ️ Sharpe Ratio: -0.64 (p=0.2233)
- ℹ️ Median Stop % of Price: 7.52%
- ℹ️ Cost Drag in R per Trade: +0.0236R
- ℹ️ Median Holding Time: 46 bars (46h)
- ℹ️ Trades per Symbol: ADAUSDT: 78, AVAXUSDT: 100, BNBUSDT: 95, BTCUSDT: 119, DOGEUSDT: 78, ETHUSDT: 119, LINKUSDT: 75, SOLUSDT: 124, SUIUSDT: 48, XRPUSDT: 80
- ℹ️ Per-Year Net R: 2021: -17.88R, 2022: -5.22R, 2023: +20.39R, 2024: -4.29R, 2025: -1.39R, 2026: -5.67R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 78 | 39.7% | 1.36 (1.44) | $+963.32 | +0.1235R (+0.1455R) |
| `AVAXUSDT` | 100 | 36.0% | 1.22 (1.27) | $+811.94 | +0.0812R (+0.0987R) |
| `BNBUSDT` | 95 | 36.8% | 1.36 (1.48) | $+1,247.17 | +0.1313R (+0.1646R) |
| `BTCUSDT` | 119 | 30.3% | 0.85 (0.94) | $-750.35 | -0.0631R (-0.0255R) |
| `DOGEUSDT` | 78 | 35.9% | 1.05 (1.10) | $+167.22 | +0.0214R (+0.0373R) |
| `ETHUSDT` | 119 | 34.5% | 0.73 (0.78) | $-1,351.24 | -0.1135R (-0.0868R) |
| `LINKUSDT` | 75 | 28.0% | 0.72 (0.76) | $-910.41 | -0.1214R (-0.1019R) |
| `SOLUSDT` | 124 | 34.7% | 0.78 (0.82) | $-1,044.30 | -0.0842R (-0.0666R) |
| `SUIUSDT` | 48 | 22.9% | 0.71 (0.74) | $-652.40 | -0.1359R (-0.1195R) |
| `XRPUSDT` | 80 | 37.5% | 1.04 (1.09) | $+113.01 | +0.0141R (+0.0354R) |

---

## 4. Strategic Implications for Discretionary Terminal

1. **Evidence-Based Discretionary Radar Filtering**:
   No strategy passed; no pattern in this report has validated edge. Under rigorous institutional friction, all tested hypotheses exhibit negative net expectancy or fail to exceed random-entry baseline distributions at the statistical significance threshold. Consequently, the Discretionary Trading Terminal must not display unverified automated trade recommendations.

2. **Fee and Slippage Drag Reality**:
   Execution friction (0.05% taker fee, 0.03% slippage, and 0.01% 8h funding drag) decisively turns gross-positive or marginal setups into net losses. Identifying patterns that possess zero predictive edge under real market conditions prevents capital destruction from retail chart illusions.

3. **Capital Preservation Protocol**:
   Because no setup demonstrates a statistically validated edge against matched noise, the terminal operates in evidence-status and risk-calculator mode only, providing trade structure and sizing analytics without directional endorsement.

---

## 5. Limitations

1. **Position Sizing Mode (`fixed_risk`)**:
   Evaluations are performed under `fixed_risk` position sizing. In fixed-risk mode, risk is calibrated to initial equity without compounding, ensuring trade-level statistical edge is measured uniformly across the full dataset period without sample truncation from ruin guards.
2. **Sample Size Evaluated Per Strategy**:
   - **TrendPullback**: 6,625 executed trades (1933 days evaluated, 100.0% of dataset)
   - **FVGRetest**: 7,856 executed trades (1933 days evaluated, 100.0% of dataset)
   - **ConfluenceThreshold**: 17,166 executed trades (1933 days evaluated, 100.0% of dataset)
   - **LiquiditySweep**: 22,462 executed trades (1933 days evaluated, 100.0% of dataset)
   - **Donchian4hN20**: 1,317 executed trades (1933 days evaluated, 100.0% of dataset)
   - **Donchian4hN55**: 916 executed trades (1933 days evaluated, 100.0% of dataset)
3. **No Out-of-Sample Holdout**:
   No holdout dataset has been used yet (until Prompt 7 is done). All metrics reflect in-sample backtesting across the complete historical timeline.
4. **Gross vs. Net Metrics**:
   Both gross P&L (pre-friction) and net P&L (post-fees, post-slippage, and post-funding) are reported side-by-side. The comparison reveals the decisive impact of transaction costs and funding drag on theoretical retail chart patterns.

