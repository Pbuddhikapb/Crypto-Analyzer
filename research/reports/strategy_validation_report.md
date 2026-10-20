# Strategy Validation Dossier: Evidence Before Infrastructure
**Generated:** 2026-10-20 16:03 UTC  
**Architecture Doctrine:** Claude's Option B (Discretionary Human Trading Terminal)  
**Null Hypothesis ($H_0$):** Phase 2 1,000-pass Monte Carlo Baseline Hurdle  
**Dataset:** 10 Major Pairs (`BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, SUIUSDT`), 2.8 Years (24,528 continuous 1h bars / coin from Binance USD-M Futures)  
**Execution Authority:** Single Exit Authority (`engine/exits.py`)  
**Institutional Friction:** Taker Fee 0.05%, Maker Fee 0.02%, Slippage 0.03%, 8h Funding Drag 0.01%  
**Exit Mechanics:** Stepped Scaling (50% TP1 @ +0.75R, BE armed @ +0.85R excursion, TP2 @ +1.33R, 12-bar stagnancy)

---

## 1. Executive Summary & Big Picture

Under Claude's doctrine (*"Evidence before infrastructure"*), no trading signal or indicator rule is permitted to enter the Discretionary Trading Terminal without empirical proof that it outperforms random noise ($p < 0.05$).

In Phase 2, we established the empirical null hypothesis ($H_0$) across 1,000 Monte Carlo runs on Binance USD-M Futures under full institutional friction:
* **Profit Factor Hurdle ($p_{95}$):** `> 1.03` (Median noise: `0.84`)
* **Win Rate Hurdle ($p_{95}$):** `> 51.43%` (Median noise: `46.22%`)
* **Expectancy Hurdle ($p_{95}$):** `> +0.0119R` (Median noise: `-0.0737R`)
* **Sharpe Ratio Hurdle ($p_{95}$):** `> 0.22` (Median noise: `-1.37`)
* **Max Drawdown Hurdle ($p_{50}$):** `< 24.08%` (Worst 5% noise: `43.17%`)

4 core discretionary trading hypotheses were codified with strict zero lookahead (signal at bar $i$ close $\rightarrow$ entry at bar $i+1$ open) and simulated across all 10 coins.

---

## 2. Master Strategy Scorecard

| Strategy | Trades | Win Rate | Net PF (Gross) | Net EV (Gross EV) | Max DD (R) | Bootstrap 95% CI | Matched Median EV | Matched Rank $p$-value | Days Evaluated | Sizing | Ruined? | Alpha Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **TrendPullback** | 718 | 48.2% | **0.73** (0.84) | **-0.1351R** (-0.0724R) | 0.00R | [-0.2016, -0.0670]R | -0.0240R | `p = 0.8531` | 1022d (100.0%) | `fixed_risk` | ⚠️ Ruined (2024-08-04) | 🔴 **REJECTED** |
| **FVGRetest** | 1386 | 46.5% | **0.84** (0.94) | **-0.0689R** (-0.0249R) | 0.00R | [-0.1144, -0.0226]R | -0.0240R | `p = 0.5345` | 1022d (100.0%) | `fixed_risk` | ⚠️ Ruined (2024-12-22) | 🔴 **REJECTED** |
| **ConfluenceThreshold** | 861 | 48.9% | **0.77** (0.87) | **-0.1110R** (-0.0569R) | 0.00R | [-0.1721, -0.0498]R | -0.0240R | `p = 0.7892` | 1022d (100.0%) | `fixed_risk` | ⚠️ Ruined (2024-05-09) | 🔴 **REJECTED** |
| **LiquiditySweep** | 424 | 46.2% | **0.62** (0.74) | **-0.2277R** (-0.1329R) | 0.00R | [-0.3200, -0.1374]R | -0.0240R | `p = 0.9421` | 1022d (100.0%) | `fixed_risk` | ⚠️ Ruined (2024-02-01) | 🔴 **REJECTED** |

---

## 3. Deep-Dive Strategy Evaluations

### Hypothesis: TrendPullback
**Concept:** 4h Macro Trend + 1h EMA 50 Pullback + RSI Momentum Recovery

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 0.8531`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `2154` | Executed Trades: `718`  
> Net Profit Factor: **`0.73`** (Gross: `0.84`)  
> Net Expectancy: **`-0.1351R`** (Gross: `-0.0724R`)  
> Max Drawdown (R): **`0.00R`** | Cumulative Net R: **`+0.00R`**  
> Days Evaluated: **`1022 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.2016R, -0.0670R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.0240R`** (Matched 95th Pct Hurdle: `+0.0400R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2024-08-04 19:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- None

**Failed / Unmet:**
- ❌ Mean Net Expectancy <= 0

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.73
- ℹ️ Net Win Rate: 48.19%
- ℹ️ Max Drawdown (R): 0.00R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |


---

### Hypothesis: FVGRetest
**Concept:** HTF Trend + SMC Fair Value Gap Retest & Mitigation Rejection

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 0.5345`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `4158` | Executed Trades: `1386`  
> Net Profit Factor: **`0.84`** (Gross: `0.94`)  
> Net Expectancy: **`-0.0689R`** (Gross: `-0.0249R`)  
> Max Drawdown (R): **`0.00R`** | Cumulative Net R: **`+0.00R`**  
> Days Evaluated: **`1022 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.1144R, -0.0226R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.0240R`** (Matched 95th Pct Hurdle: `+0.0400R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2024-12-22 18:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- None

**Failed / Unmet:**
- ❌ Mean Net Expectancy <= 0

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.84
- ℹ️ Net Win Rate: 46.54%
- ℹ️ Max Drawdown (R): 0.00R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |


---

### Hypothesis: ConfluenceThreshold
**Concept:** Multi-Timeframe 0-100 Confluence Model Triggered at High Conviction (>= 75)

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 0.7892`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `2583` | Executed Trades: `861`  
> Net Profit Factor: **`0.77`** (Gross: `0.87`)  
> Net Expectancy: **`-0.1110R`** (Gross: `-0.0569R`)  
> Max Drawdown (R): **`0.00R`** | Cumulative Net R: **`+0.00R`**  
> Days Evaluated: **`1022 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.1721R, -0.0498R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.0240R`** (Matched 95th Pct Hurdle: `+0.0400R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2024-05-09 22:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- None

**Failed / Unmet:**
- ❌ Mean Net Expectancy <= 0

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.77
- ℹ️ Net Win Rate: 48.90%
- ℹ️ Max Drawdown (R): 0.00R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |


---

### Hypothesis: LiquiditySweep
**Concept:** Swing Failure Pattern (SFP) sweeping key liquidity with rejection

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 0.9421`** (Empirical rank within 1,000 matched null passes)  
> Total Candidate Signals: `1272` | Executed Trades: `424`  
> Net Profit Factor: **`0.62`** (Gross: `0.74`)  
> Net Expectancy: **`-0.2277R`** (Gross: `-0.1329R`)  
> Max Drawdown (R): **`0.00R`** | Cumulative Net R: **`+0.00R`**  
> Days Evaluated: **`1022 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.3200R, -0.1374R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`-0.0240R`** (Matched 95th Pct Hurdle: `+0.0400R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2024-02-01 01:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- None

**Failed / Unmet:**
- ❌ Mean Net Expectancy <= 0

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.62
- ℹ️ Net Win Rate: 46.23%
- ℹ️ Max Drawdown (R): 0.00R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |


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
   - **TrendPullback**: 718 executed trades (1022 days evaluated, 100.0% of dataset)
   - **FVGRetest**: 1,386 executed trades (1022 days evaluated, 100.0% of dataset)
   - **ConfluenceThreshold**: 861 executed trades (1022 days evaluated, 100.0% of dataset)
   - **LiquiditySweep**: 424 executed trades (1022 days evaluated, 100.0% of dataset)
3. **No Out-of-Sample Holdout**:
   No holdout dataset has been used yet (until Prompt 7 is done). All metrics reflect in-sample backtesting across the complete historical timeline.
4. **Gross vs. Net Metrics**:
   Both gross P&L (pre-friction) and net P&L (post-fees, post-slippage, and post-funding) are reported side-by-side. The comparison reveals the decisive impact of transaction costs and funding drag on theoretical retail chart patterns.

