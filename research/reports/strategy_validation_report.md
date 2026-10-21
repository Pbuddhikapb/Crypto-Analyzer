# Strategy Validation Dossier: Evidence Before Infrastructure
**Generated:** 2026-10-21 16:26 UTC  
**Git Commit:** `f578398ad673303720cb28ff0802471693b72478`  
**Dataset Hash:** `61d0448603612d76`  
**Architecture Doctrine:** Claude's Option B (Discretionary Human Trading Terminal)  
**Null Hypothesis ($H_0$):** Phase 2 1,000-pass Monte Carlo Baseline Hurdle  
**Dataset:** 10 Major Pairs (`BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, SUIUSDT`), 5.3 Years (50,832 continuous 1h bars / coin from Binance USD-M Futures)  
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

4 core discretionary trading hypotheses were codified with strict zero lookahead (signal at bar $i$ close $\rightarrow$ entry at bar $i+1$ open) and simulated across all 10 coins.

---

## 2. Master Strategy Scorecard

| Strategy | Trades | Win Rate | Net PF (Gross) | Net EV (Gross EV) | Max DD (R) | Bootstrap 95% CI | Matched Median EV | Matched Rank $p$-value | Days Evaluated | Sizing | Ruined? | Alpha Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **TrendPullback** | 6625 | 49.2% | **0.78** (0.81) | **-0.1099R** (-0.0520R) | 732.51R | [-0.1323, -0.0876]R | -0.1030R | `p = 0.7373` | 1933d (100.0%) | `fixed_risk` | ⚠️ Ruined (2024-08-08) | 🔴 **REJECTED** |
| **FVGRetest** | 7856 | 46.9% | **0.84** (0.88) | **-0.0718R** (-0.0298R) | 597.04R | [-0.0911, -0.0523]R | -0.0675R | `p = 0.6633` | 1933d (100.0%) | `fixed_risk` | ⚠️ Ruined (2024-12-22) | 🔴 **REJECTED** |
| **ConfluenceThreshold** | 17166 | 47.0% | **0.71** (0.82) | **-0.1513R** (-0.0873R) | 2606.85R | [-0.1654, -0.1373]R | -0.0969R | `p = 1.0000` | 1933d (100.0%) | `fixed_risk` | ⚠️ Ruined (2024-05-09) | 🔴 **REJECTED** |
| **LiquiditySweep** | 22462 | 46.9% | **0.62** (0.75) | **-0.2157R** (-0.1239R) | 4846.73R | [-0.2285, -0.2029]R | -0.1654R | `p = 1.0000` | 1933d (100.0%) | `fixed_risk` | ⚠️ Ruined (2024-02-01) | 🔴 **REJECTED** |

---

## 3. Deep-Dive Strategy Evaluations

### Hypothesis: TrendPullback
**Concept:** 4h Macro Trend + 1h EMA 50 Pullback + RSI Momentum Recovery

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 0.7373`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `31853` | Executed Trades: `6625`  
> Net Profit Factor: **`0.78`** (Gross: `0.81`)  
> Net Expectancy: **`-0.1099R`** (Gross: `-0.0520R`)  
> Max Drawdown (R): **`732.51R`** | Cumulative Net R: **`-727.94R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.1323R, -0.0876R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.1030R`** (Matched 95th Pct Hurdle: `-0.0773R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2024-08-08 14:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 6625 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.1099R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.1323R <= 0.0R
- ❌ Matched Rank p-value 0.7373 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.78 (Null p95 Hurdle: 1.04)
- ℹ️ Net Win Rate: 49.20% (Null p95 Hurdle: 52.40%)

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 662 | 49.2% | 0.78 (0.81) | $-7,279.40 | -0.1099R (-0.0520R) |
| `AVAXUSDT` | 662 | 49.2% | 0.78 (0.81) | $-7,279.40 | -0.1099R (-0.0520R) |
| `BNBUSDT` | 662 | 49.2% | 0.78 (0.81) | $-7,279.40 | -0.1099R (-0.0520R) |
| `BTCUSDT` | 662 | 49.2% | 0.78 (0.81) | $-7,279.40 | -0.1099R (-0.0520R) |
| `DOGEUSDT` | 662 | 49.2% | 0.78 (0.81) | $-7,279.40 | -0.1099R (-0.0520R) |
| `ETHUSDT` | 662 | 49.2% | 0.78 (0.81) | $-7,279.40 | -0.1099R (-0.0520R) |
| `LINKUSDT` | 662 | 49.2% | 0.78 (0.81) | $-7,279.40 | -0.1099R (-0.0520R) |
| `SOLUSDT` | 662 | 49.2% | 0.78 (0.81) | $-7,279.40 | -0.1099R (-0.0520R) |
| `SUIUSDT` | 662 | 49.2% | 0.78 (0.81) | $-7,279.40 | -0.1099R (-0.0520R) |
| `XRPUSDT` | 662 | 49.2% | 0.78 (0.81) | $-7,279.40 | -0.1099R (-0.0520R) |

---

### Hypothesis: FVGRetest
**Concept:** HTF Trend + SMC Fair Value Gap Retest & Mitigation Rejection

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 0.6633`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `29845` | Executed Trades: `7856`  
> Net Profit Factor: **`0.84`** (Gross: `0.88`)  
> Net Expectancy: **`-0.0718R`** (Gross: `-0.0298R`)  
> Max Drawdown (R): **`597.04R`** | Cumulative Net R: **`-563.77R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.0911R, -0.0523R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.0675R`** (Matched 95th Pct Hurdle: `-0.0431R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2024-12-22 22:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 7856 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.0718R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.0911R <= 0.0R
- ❌ Matched Rank p-value 0.6633 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.84 (Null p95 Hurdle: 1.04)
- ℹ️ Net Win Rate: 46.90% (Null p95 Hurdle: 52.40%)

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 785 | 46.9% | 0.84 (0.88) | $-5,637.70 | -0.0718R (-0.0298R) |
| `AVAXUSDT` | 785 | 46.9% | 0.84 (0.88) | $-5,637.70 | -0.0718R (-0.0298R) |
| `BNBUSDT` | 785 | 46.9% | 0.84 (0.88) | $-5,637.70 | -0.0718R (-0.0298R) |
| `BTCUSDT` | 785 | 46.9% | 0.84 (0.88) | $-5,637.70 | -0.0718R (-0.0298R) |
| `DOGEUSDT` | 785 | 46.9% | 0.84 (0.88) | $-5,637.70 | -0.0718R (-0.0298R) |
| `ETHUSDT` | 785 | 46.9% | 0.84 (0.88) | $-5,637.70 | -0.0718R (-0.0298R) |
| `LINKUSDT` | 785 | 46.9% | 0.84 (0.88) | $-5,637.70 | -0.0718R (-0.0298R) |
| `SOLUSDT` | 785 | 46.9% | 0.84 (0.88) | $-5,637.70 | -0.0718R (-0.0298R) |
| `SUIUSDT` | 785 | 46.9% | 0.84 (0.88) | $-5,637.70 | -0.0718R (-0.0298R) |
| `XRPUSDT` | 785 | 46.9% | 0.84 (0.88) | $-5,637.70 | -0.0718R (-0.0298R) |

---

### Hypothesis: ConfluenceThreshold
**Concept:** Multi-Timeframe 0-100 Confluence Model Triggered at High Conviction (>= 75)

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 1.0000`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `187951` | Executed Trades: `17166`  
> Net Profit Factor: **`0.71`** (Gross: `0.82`)  
> Net Expectancy: **`-0.1513R`** (Gross: `-0.0873R`)  
> Max Drawdown (R): **`2606.85R`** | Cumulative Net R: **`-2597.26R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.1654R, -0.1373R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.0969R`** (Matched 95th Pct Hurdle: `-0.0650R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2024-05-09 22:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 17166 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.1513R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.1654R <= 0.0R
- ❌ Matched Rank p-value 1.0000 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.71 (Null p95 Hurdle: 1.04)
- ℹ️ Net Win Rate: 47.00% (Null p95 Hurdle: 52.40%)

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 1716 | 47.0% | 0.71 (0.82) | $-25,972.60 | -0.1513R (-0.0873R) |
| `AVAXUSDT` | 1716 | 47.0% | 0.71 (0.82) | $-25,972.60 | -0.1513R (-0.0873R) |
| `BNBUSDT` | 1716 | 47.0% | 0.71 (0.82) | $-25,972.60 | -0.1513R (-0.0873R) |
| `BTCUSDT` | 1716 | 47.0% | 0.71 (0.82) | $-25,972.60 | -0.1513R (-0.0873R) |
| `DOGEUSDT` | 1716 | 47.0% | 0.71 (0.82) | $-25,972.60 | -0.1513R (-0.0873R) |
| `ETHUSDT` | 1716 | 47.0% | 0.71 (0.82) | $-25,972.60 | -0.1513R (-0.0873R) |
| `LINKUSDT` | 1716 | 47.0% | 0.71 (0.82) | $-25,972.60 | -0.1513R (-0.0873R) |
| `SOLUSDT` | 1716 | 47.0% | 0.71 (0.82) | $-25,972.60 | -0.1513R (-0.0873R) |
| `SUIUSDT` | 1716 | 47.0% | 0.71 (0.82) | $-25,972.60 | -0.1513R (-0.0873R) |
| `XRPUSDT` | 1716 | 47.0% | 0.71 (0.82) | $-25,972.60 | -0.1513R (-0.0873R) |

---

### Hypothesis: LiquiditySweep
**Concept:** Key Liquidity Level Sweep with Microstructure Reversal Signal

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 1.0000`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `73577` | Executed Trades: `22462`  
> Net Profit Factor: **`0.62`** (Gross: `0.75`)  
> Net Expectancy: **`-0.2157R`** (Gross: `-0.1239R`)  
> Max Drawdown (R): **`4846.73R`** | Cumulative Net R: **`-4845.14R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.2285R, -0.2029R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.1654R`** (Matched 95th Pct Hurdle: `-0.1250R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2024-02-01 00:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 22462 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.2157R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.2285R <= 0.0R
- ❌ Matched Rank p-value 1.0000 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.62 (Null p95 Hurdle: 1.04)
- ℹ️ Net Win Rate: 46.88% (Null p95 Hurdle: 52.40%)

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 2246 | 46.9% | 0.62 (0.75) | $-48,451.40 | -0.2157R (-0.1239R) |
| `AVAXUSDT` | 2246 | 46.9% | 0.62 (0.75) | $-48,451.40 | -0.2157R (-0.1239R) |
| `BNBUSDT` | 2246 | 46.9% | 0.62 (0.75) | $-48,451.40 | -0.2157R (-0.1239R) |
| `BTCUSDT` | 2246 | 46.9% | 0.62 (0.75) | $-48,451.40 | -0.2157R (-0.1239R) |
| `DOGEUSDT` | 2246 | 46.9% | 0.62 (0.75) | $-48,451.40 | -0.2157R (-0.1239R) |
| `ETHUSDT` | 2246 | 46.9% | 0.62 (0.75) | $-48,451.40 | -0.2157R (-0.1239R) |
| `LINKUSDT` | 2246 | 46.9% | 0.62 (0.75) | $-48,451.40 | -0.2157R (-0.1239R) |
| `SOLUSDT` | 2246 | 46.9% | 0.62 (0.75) | $-48,451.40 | -0.2157R (-0.1239R) |
| `SUIUSDT` | 2246 | 46.9% | 0.62 (0.75) | $-48,451.40 | -0.2157R (-0.1239R) |
| `XRPUSDT` | 2246 | 46.9% | 0.62 (0.75) | $-48,451.40 | -0.2157R (-0.1239R) |

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
3. **No Out-of-Sample Holdout**:
   No holdout dataset has been used yet (until Prompt 7 is done). All metrics reflect in-sample backtesting across the complete historical timeline.
4. **Gross vs. Net Metrics**:
   Both gross P&L (pre-friction) and net P&L (post-fees, post-slippage, and post-funding) are reported side-by-side. The comparison reveals the decisive impact of transaction costs and funding drag on theoretical retail chart patterns.

