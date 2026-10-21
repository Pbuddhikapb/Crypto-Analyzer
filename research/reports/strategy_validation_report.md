# Strategy Validation Dossier: Evidence Before Infrastructure
**Generated:** 2026-10-20 21:34 UTC  
**Git Commit:** `ca33b706e819ce16d72fca30759166727bdda59c`  
**Dataset Hash:** `a92b86d2b18c6415`  
**Architecture Doctrine:** Claude's Option B (Discretionary Human Trading Terminal)  
**Null Hypothesis ($H_0$):** Phase 2 1,000-pass Monte Carlo Baseline Hurdle  
**Dataset:** 10 Major Pairs (`BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, SUIUSDT`), 2.3 Years (20,113 continuous 1h bars / coin from Binance USD-M Futures)  
**Execution Authority:** Single Exit Authority (`engine/exits.py`)  
**Institutional Friction:** Taker Fee 0.05%, Maker Fee 0.02%, Slippage 0.03%, 8h Funding Drag 0.01%  
**Exit Mechanics:** Stepped Scaling (50% TP1 @ +0.75R, BE armed @ +0.85R excursion, TP2 @ +1.33R, 12-bar stagnancy)

---

## 1. Executive Summary & Big Picture

Under Claude's doctrine (*"Evidence before infrastructure"*), no trading signal or indicator rule is permitted to enter the Discretionary Trading Terminal without empirical proof that it outperforms random noise ($p < 0.05$).

In Phase 2, we established the empirical null hypothesis ($H_0$) across 1,000 Monte Carlo runs on Binance USD-M Futures under full institutional friction:
* **Profit Factor Hurdle ($p_{95}$):** `> 1.04` (Median noise: `0.84`)
* **Win Rate Hurdle ($p_{95}$):** `> 52.08%` (Median noise: `46.83%`)
* **Expectancy Hurdle ($p_{95}$):** `> +0.0164R` (Median noise: `-0.0758R`)
* **Sharpe Ratio Hurdle ($p_{95}$):** `> 0.30` (Median noise: `-1.39`)
* **Max Drawdown Hurdle ($p_{50}$):** `< 25.26%` (Worst 5% noise: `44.61%`)

4 core discretionary trading hypotheses were codified with strict zero lookahead (signal at bar $i$ close $\rightarrow$ entry at bar $i+1$ open) and simulated across all 10 coins.

---

## 2. Master Strategy Scorecard

| Strategy | Trades | Win Rate | Net PF (Gross) | Net EV (Gross EV) | Max DD (R) | Bootstrap 95% CI | Matched Median EV | Matched Rank $p$-value | Days Evaluated | Sizing | Ruined? | Alpha Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **TrendPullback** | 2950 | 48.6% | **0.75** (0.87) | **-0.1255R** (-0.0610R) | 379.18R | [-0.1584, -0.0920]R | -0.1030R | `p = 0.9111` | 838d (100.0%) | `fixed_risk` | ⚠️ Ruined (2024-08-08) | 🔴 **REJECTED** |
| **FVGRetest** | 3166 | 46.0% | **0.82** (0.92) | **-0.0789R** (-0.0336R) | 254.34R | [-0.1088, -0.0488]R | -0.0675R | `p = 0.7932` | 838d (100.0%) | `fixed_risk` | ⚠️ Ruined (2024-12-22) | 🔴 **REJECTED** |
| **ConfluenceThreshold** | 7252 | 47.4% | **0.72** (0.82) | **-0.1466R** (-0.0855R) | 1065.91R | [-0.1681, -0.1250]R | -0.0969R | `p = 1.0000` | 838d (100.0%) | `fixed_risk` | ⚠️ Ruined (2024-05-09) | 🔴 **REJECTED** |
| **LiquiditySweep** | 9741 | 46.9% | **0.61** (0.75) | **-0.2234R** (-0.1273R) | 2179.83R | [-0.2423, -0.2044]R | -0.1676R | `p = 1.0000` | 838d (100.0%) | `fixed_risk` | ⚠️ Ruined (2024-02-01) | 🔴 **REJECTED** |

---

## 3. Deep-Dive Strategy Evaluations

### Hypothesis: TrendPullback
**Concept:** 4h Macro Trend + 1h EMA 50 Pullback + RSI Momentum Recovery

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 0.9111`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `15021` | Executed Trades: `2950`  
> Net Profit Factor: **`0.75`** (Gross: `0.87`)  
> Net Expectancy: **`-0.1255R`** (Gross: `-0.0610R`)  
> Max Drawdown (R): **`379.18R`** | Cumulative Net R: **`-370.34R`**  
> Days Evaluated: **`838 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.1584R, -0.0920R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.1030R`** (Matched 95th Pct Hurdle: `-0.0773R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2024-08-08 14:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 2950 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.1255R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.1584R <= 0.0R
- ❌ Matched Rank p-value 0.9111 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.75 (Null p95 Hurdle: 1.04, p=0.7609)
- ℹ️ Net Win Rate: 48.64% (Null p95 Hurdle: 52.08%, p=0.2937)
- ℹ️ Max Drawdown (R): 379.18R
- ℹ️ Sharpe Ratio: -1.76 (p=0.6361)

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 258 | 46.9% | 0.67 (0.76) | $-4,512.89 | -0.1749R (-0.1191R) |
| `AVAXUSDT` | 247 | 44.9% | 0.65 (0.73) | $-4,786.05 | -0.1938R (-0.1392R) |
| `BNBUSDT` | 336 | 54.2% | 0.95 (1.14) | $-773.41 | -0.0230R (+0.0558R) |
| `BTCUSDT` | 366 | 51.6% | 0.83 (1.03) | $-2,912.94 | -0.0796R (+0.0112R) |
| `DOGEUSDT` | 260 | 49.2% | 0.80 (0.90) | $-2,584.24 | -0.0994R (-0.0474R) |
| `ETHUSDT` | 335 | 49.9% | 0.78 (0.91) | $-3,572.97 | -0.1067R (-0.0369R) |
| `LINKUSDT` | 217 | 45.2% | 0.70 (0.79) | $-3,383.84 | -0.1559R (-0.0995R) |
| `SOLUSDT` | 348 | 47.4% | 0.73 (0.82) | $-4,752.29 | -0.1366R (-0.0819R) |
| `SUIUSDT` | 244 | 45.1% | 0.68 (0.75) | $-4,058.66 | -0.1663R (-0.1214R) |
| `XRPUSDT` | 339 | 48.4% | 0.67 (0.79) | $-5,697.14 | -0.1681R (-0.0977R) |

---

### Hypothesis: FVGRetest
**Concept:** HTF Trend + SMC Fair Value Gap Retest & Mitigation Rejection

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 0.7932`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `12687` | Executed Trades: `3166`  
> Net Profit Factor: **`0.82`** (Gross: `0.92`)  
> Net Expectancy: **`-0.0789R`** (Gross: `-0.0336R`)  
> Max Drawdown (R): **`254.34R`** | Cumulative Net R: **`-249.69R`**  
> Days Evaluated: **`838 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.1088R, -0.0488R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.0675R`** (Matched 95th Pct Hurdle: `-0.0431R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2024-12-22 22:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 3166 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.0789R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.1088R <= 0.0R
- ❌ Matched Rank p-value 0.7932 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.82 (Null p95 Hurdle: 1.04, p=0.5509)
- ℹ️ Net Win Rate: 45.99% (Null p95 Hurdle: 52.08%, p=0.5997)
- ℹ️ Max Drawdown (R): 254.34R
- ℹ️ Sharpe Ratio: -1.23 (p=0.4384)

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 287 | 41.5% | 0.62 (0.69) | $-5,549.54 | -0.1934R (-0.1483R) |
| `AVAXUSDT` | 245 | 51.0% | 0.95 (1.04) | $-542.94 | -0.0222R (+0.0176R) |
| `BNBUSDT` | 367 | 43.9% | 0.88 (1.01) | $-1,679.74 | -0.0458R (+0.0050R) |
| `BTCUSDT` | 389 | 41.1% | 0.80 (0.93) | $-3,062.14 | -0.0787R (-0.0250R) |
| `DOGEUSDT` | 263 | 52.1% | 0.93 (1.03) | $-803.51 | -0.0306R (+0.0104R) |
| `ETHUSDT` | 393 | 48.6% | 0.94 (1.06) | $-926.90 | -0.0236R (+0.0220R) |
| `LINKUSDT` | 213 | 47.4% | 0.83 (0.91) | $-1,702.65 | -0.0799R (-0.0383R) |
| `SOLUSDT` | 379 | 44.3% | 0.68 (0.75) | $-6,093.96 | -0.1608R (-0.1166R) |
| `SUIUSDT` | 296 | 48.0% | 0.82 (0.89) | $-2,580.83 | -0.0872R (-0.0481R) |
| `XRPUSDT` | 334 | 45.5% | 0.86 (0.96) | $-2,027.12 | -0.0607R (-0.0150R) |

---

### Hypothesis: ConfluenceThreshold
**Concept:** Multi-Timeframe 0-100 Confluence Model Triggered at High Conviction (>= 75)

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 1.0000`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `80315` | Executed Trades: `7252`  
> Net Profit Factor: **`0.72`** (Gross: `0.82`)  
> Net Expectancy: **`-0.1466R`** (Gross: `-0.0855R`)  
> Max Drawdown (R): **`1065.91R`** | Cumulative Net R: **`-1063.39R`**  
> Days Evaluated: **`838 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.1681R, -0.1250R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.0969R`** (Matched 95th Pct Hurdle: `-0.0807R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2024-05-09 22:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 7252 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.1466R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.1681R <= 0.0R
- ❌ Matched Rank p-value 1.0000 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.72 (Null p95 Hurdle: 1.04, p=0.8311)
- ℹ️ Net Win Rate: 47.39% (Null p95 Hurdle: 52.08%, p=0.4335)
- ℹ️ Max Drawdown (R): 1065.91R
- ℹ️ Sharpe Ratio: -2.26 (p=0.7946)

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 615 | 45.7% | 0.68 (0.76) | $-10,370.32 | -0.1686R (-0.1155R) |
| `AVAXUSDT` | 595 | 44.9% | 0.70 (0.77) | $-9,928.50 | -0.1669R (-0.1165R) |
| `BNBUSDT` | 799 | 45.8% | 0.67 (0.79) | $-14,761.46 | -0.1847R (-0.1020R) |
| `BTCUSDT` | 948 | 49.5% | 0.74 (0.90) | $-12,323.57 | -0.1300R (-0.0432R) |
| `DOGEUSDT` | 706 | 44.5% | 0.65 (0.72) | $-13,820.56 | -0.1958R (-0.1457R) |
| `ETHUSDT` | 979 | 50.4% | 0.83 (0.95) | $-8,195.03 | -0.0837R (-0.0219R) |
| `LINKUSDT` | 479 | 43.0% | 0.61 (0.68) | $-10,647.61 | -0.2223R (-0.1694R) |
| `SOLUSDT` | 859 | 49.4% | 0.77 (0.86) | $-9,798.58 | -0.1141R (-0.0634R) |
| `SUIUSDT` | 568 | 49.1% | 0.81 (0.88) | $-5,371.59 | -0.0946R (-0.0539R) |
| `XRPUSDT` | 704 | 48.0% | 0.70 (0.80) | $-11,122.20 | -0.1580R (-0.0946R) |

---

### Hypothesis: LiquiditySweep
**Concept:** Swing Failure Pattern (SFP) sweeping key liquidity with rejection

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 1.0000`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `32884` | Executed Trades: `9741`  
> Net Profit Factor: **`0.61`** (Gross: `0.75`)  
> Net Expectancy: **`-0.2234R`** (Gross: `-0.1273R`)  
> Max Drawdown (R): **`2179.83R`** | Cumulative Net R: **`-2175.93R`**  
> Days Evaluated: **`838 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.2423R, -0.2044R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.1676R`** (Matched 95th Pct Hurdle: `-0.1516R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2024-02-01 01:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 9741 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.2234R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.2423R <= 0.0R
- ❌ Matched Rank p-value 1.0000 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.61 (Null p95 Hurdle: 1.04, p=0.9694)
- ℹ️ Net Win Rate: 46.89% (Null p95 Hurdle: 52.08%, p=0.4930)
- ℹ️ Max Drawdown (R): 2179.83R
- ℹ️ Sharpe Ratio: -2.86 (p=0.9180)

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 901 | 48.6% | 0.66 (0.79) | $-17,625.78 | -0.1956R (-0.1034R) |
| `AVAXUSDT` | 859 | 44.8% | 0.58 (0.69) | $-21,994.10 | -0.2560R (-0.1644R) |
| `BNBUSDT` | 973 | 45.7% | 0.59 (0.74) | $-22,749.69 | -0.2338R (-0.1277R) |
| `BTCUSDT` | 1015 | 46.4% | 0.62 (0.78) | $-21,425.26 | -0.2111R (-0.1036R) |
| `DOGEUSDT` | 968 | 45.6% | 0.56 (0.68) | $-25,531.76 | -0.2638R (-0.1695R) |
| `ETHUSDT` | 1127 | 47.8% | 0.63 (0.77) | $-23,593.61 | -0.2093R (-0.1106R) |
| `LINKUSDT` | 867 | 44.3% | 0.53 (0.65) | $-25,296.57 | -0.2918R (-0.1937R) |
| `SOLUSDT` | 1085 | 47.6% | 0.64 (0.77) | $-22,710.80 | -0.2093R (-0.1191R) |
| `SUIUSDT` | 931 | 49.9% | 0.72 (0.85) | $-14,502.58 | -0.1558R (-0.0719R) |
| `XRPUSDT` | 1015 | 47.7% | 0.62 (0.76) | $-22,163.17 | -0.2184R (-0.1210R) |

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
   - **TrendPullback**: 2,950 executed trades (838 days evaluated, 100.0% of dataset)
   - **FVGRetest**: 3,166 executed trades (838 days evaluated, 100.0% of dataset)
   - **ConfluenceThreshold**: 7,252 executed trades (838 days evaluated, 100.0% of dataset)
   - **LiquiditySweep**: 9,741 executed trades (838 days evaluated, 100.0% of dataset)
3. **No Out-of-Sample Holdout**:
   No holdout dataset has been used yet (until Prompt 7 is done). All metrics reflect in-sample backtesting across the complete historical timeline.
4. **Gross vs. Net Metrics**:
   Both gross P&L (pre-friction) and net P&L (post-fees, post-slippage, and post-funding) are reported side-by-side. The comparison reveals the decisive impact of transaction costs and funding drag on theoretical retail chart patterns.

