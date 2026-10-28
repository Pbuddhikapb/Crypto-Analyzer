# Strategy Validation Dossier: Evidence Before Infrastructure
**Generated:** 2026-10-27 23:47 UTC  
**Git Commit:** `2fc38be31f36e62c8d4b0423a88aac7358893f02+dirty`  
**Dataset Hash:** `61d0448603612d76`  
**Architecture Doctrine:** Claude's Option B (Discretionary Human Trading Terminal)  
**Null Hypothesis ($H_0$):** Phase 2 10-pass Monte Carlo Baseline Hurdle  
**Dataset:** 10 Major Pairs (`BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, SUIUSDT`), 5.3 Years (Binance USD-M Futures 1h candles)  
* **Per-Symbol 1h Bar Counts:** BTCUSDT: 46,393, ETHUSDT: 46,393, SOLUSDT: 46,273, BNBUSDT: 46,393, XRPUSDT: 46,273, DOGEUSDT: 46,393, ADAUSDT: 46,393, AVAXUSDT: 46,393, LINKUSDT: 46,393, SUIUSDT: 25,929  
* **Data Continuity & Coverage Notes:**
  - **SUIUSDT starts later:** SUI USD-M Perpetual contract was listed on Binance on 2023-05-03 16:00 UTC (25,929 bars vs 46,393 bars full period).
  - **Coins with maintenance gaps (SOLUSDT, XRPUSDT):** 120 bars missing in downloaded archive, cause unverified (73h: 2022-02-25 23:00 to 2022-03-01 00:00 UTC, and 49h: 2022-03-31 23:00 to 2022-04-03 00:00 UTC).
**Execution Authority:** Single Exit Authority (`engine/exits.py`)  
**Institutional Friction:** Taker Fee 0.05%, Maker Fee 0.02%, Slippage 0.03%, 8h Funding Drag 0.01%  
**Exit Mechanics:** Stepped Scaling (50% TP1 @ +0.75R, BE armed @ +0.85R excursion, TP2 @ +1.33R, 12-bar stagnancy)

---

## 1. Executive Summary & Big Picture

Under Claude's doctrine (*"Evidence before infrastructure"*), no trading signal or indicator rule is permitted to enter the Discretionary Trading Terminal without empirical proof that it outperforms random noise ($p < 0.05$).

In Phase 2, we established the empirical null hypothesis ($H_0$) across 10 Monte Carlo runs on Binance USD-M Futures under full institutional friction:
* **Profit Factor Hurdle ($p_{95}$):** `> 0.00` (Median noise: `0.00`)
* **Win Rate Hurdle ($p_{95}$):** `> 0.00%` (Median noise: `0.00%`)
* **Expectancy Hurdle ($p_{95}$):** `> -0.0318R` (Median noise: `-0.0339R`)
* **Sharpe Ratio Hurdle ($p_{95}$):** `> -19.09` (Median noise: `-30.37`)
* **Max Drawdown Hurdle ($p_{50}$):** `< 8.50%` (Worst 5% noise: `8.87%`)

6 core discretionary trading hypotheses were codified with strict zero lookahead (signal at bar $i$ close $\rightarrow$ entry at bar $i+1$ open) and simulated across all 10 coins.

---

## 2. Master Strategy Scorecard

| Strategy | Trades | Win Rate | Net PF (Gross) | Net EV (Gross EV) | Max DD (R) | Bootstrap 95% CI | Matched Median EV | Matched Rank $p$-value | Dropped Null % | Days Evaluated | Sizing | Ruined? | Alpha Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **TrendPullback** | 6627 | 49.3% | **0.78** (0.89) | **-0.1097R** (-0.0496R) | 732.51R | [-0.1320, -0.0875]R | +0.0000R | `p = 0.7742` | 0.00% | 1933d (100.0%) | `fixed_risk` | ⚠️ Ruined (2022-02-07) | 🔴 **REJECTED** |
| **FVGRetest** | 7828 | 46.9% | **0.84** (0.93) | **-0.0715R** (-0.0283R) | 594.76R | [-0.0908, -0.0519]R | +0.0000R | `p = 0.7073` | 0.00% | 1933d (100.0%) | `fixed_risk` | ⚠️ Ruined (2022-05-24) | 🔴 **REJECTED** |
| **ConfluenceThreshold** | 17171 | 47.0% | **0.71** (0.80) | **-0.1513R** (-0.0940R) | 2606.85R | [-0.1657, -0.1375]R | +0.0000R | `p = 1.0000` | 0.00% | 1933d (100.0%) | `fixed_risk` | ⚠️ Ruined (2021-03-23) | 🔴 **REJECTED** |
| **LiquiditySweep** | 22464 | 46.9% | **0.62** (0.75) | **-0.2157R** (-0.1239R) | 4846.73R | [-0.2286, -0.2031]R | +0.0000R | `p = 1.0000` | 0.00% | 1933d (100.0%) | `fixed_risk` | ⚠️ Ruined (2021-01-27) | 🔴 **REJECTED** |
| **Donchian4hN20** | 1055 | 38.3% | **1.14** (1.23) | **+0.0495R** (+0.0774R) | 24.59R | [-0.0162, +0.1187]R | +0.0000R | `p = 0.0669` | 0.00% | 1933d (100.0%) | `fixed_risk` | No | 🔴 **REJECTED** |
| **Donchian4hN55** | 832 | 37.6% | **1.22** (1.31) | **+0.0788R** (+0.1047R) | 16.69R | [+0.0021, +0.1590]R | +0.0003R | `p = 0.0195` | 0.00% | 1933d (100.0%) | `fixed_risk` | No | 🟢 **PASSED DEV GATE (see robustness)** |

---

## 3. Deep-Dive Strategy Evaluations

### Hypothesis: TrendPullback
**Concept:** 4h Macro Trend + 1h EMA 50 Pullback + RSI Momentum Recovery

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 0.7742`** (Empirical rank within 1,000 matched null passes)  
> Matched Null Dropped Trade Share: **`0.00%`**  
> Total Candidate Signals: `33501` | Executed Trades: `6627`  
> Net Profit Factor: **`0.78`** (Gross: `0.89`)  
> Net Expectancy: **`-0.1097R`** (Gross: `-0.0496R`)  
> Max Drawdown (R): **`732.51R`** | Cumulative Net R: **`-727.28R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.1320R, -0.0875R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`+0.0000R`** (Matched 95th Pct Hurdle: `+0.0000R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2022-02-07 03:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 6627 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.1097R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.1320R <= 0.0R
- ❌ Matched Rank p-value 0.7742 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.78 (Null p95 Hurdle: 0.00, p=0.5000)
- ℹ️ Net Win Rate: 49.27% (Null p95 Hurdle: 0.00%, p=0.5000)
- ℹ️ Max Drawdown (R): 732.51R
- ℹ️ Sharpe Ratio: -1.66 (p=0.0097)
- ℹ️ Median Stop % of Price: 1.72%
- ℹ️ Cost Drag in R per Trade: +0.0602R
- ℹ️ Median Holding Time: 6 bars (6h)
- ℹ️ Trades per Symbol: ADAUSDT: 595, AVAXUSDT: 582, BNBUSDT: 741, BTCUSDT: 826, DOGEUSDT: 641, ETHUSDT: 784, LINKUSDT: 566, SOLUSDT: 818, SUIUSDT: 327, XRPUSDT: 747
- ℹ️ Per-Year Net R: 2021: -79.17R, 2022: -121.55R, 2023: -139.07R, 2024: -182.68R, 2025: -139.56R, 2026: -65.25R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 595 | 50.6% | 0.82 (0.92) | $-5,224.24 | -0.0878R (-0.0349R) |
| `AVAXUSDT` | 582 | 46.4% | 0.69 (0.77) | $-9,280.86 | -0.1595R (-0.1110R) |
| `BNBUSDT` | 741 | 51.3% | 0.82 (0.98) | $-6,233.33 | -0.0841R (-0.0093R) |
| `BTCUSDT` | 826 | 49.9% | 0.79 (0.96) | $-8,156.06 | -0.0987R (-0.0152R) |
| `DOGEUSDT` | 641 | 48.0% | 0.73 (0.82) | $-8,912.00 | -0.1390R (-0.0854R) |
| `ETHUSDT` | 784 | 52.4% | 0.89 (1.03) | $-4,034.52 | -0.0515R (+0.0137R) |
| `LINKUSDT` | 566 | 46.5% | 0.72 (0.81) | $-8,111.92 | -0.1433R (-0.0903R) |
| `SOLUSDT` | 818 | 48.8% | 0.77 (0.85) | $-9,162.67 | -0.1120R (-0.0657R) |
| `SUIUSDT` | 327 | 45.9% | 0.70 (0.77) | $-5,094.92 | -0.1558R (-0.1107R) |
| `XRPUSDT` | 747 | 49.7% | 0.76 (0.88) | $-8,517.45 | -0.1140R (-0.0515R) |

#### Robustness Panel: TrendPullback
*(Report-only diagnostics; does not modify pre-registered hypothesis verdict)*

| Diagnostic Metric | Value | Reference / Standard |
| :--- | :--- | :--- |
| **Matched $p$-value** | `0.7742` (N=1,000) | Gate threshold $\alpha = 0.05$ |
| **Monte Carlo Standard Error** | `SE = 0.0132` | $\sqrt{p(1-p)/N}$ |
| **Threshold within 2 SE?** | No (threshold outside 2 SE) | $|p - \alpha| \le 2 \times SE$ |
| **iid Bootstrap 2.5% Bound (30 seeds)** | **`0.0%`** above zero | Min: `-0.1328R`, Max: `-0.1307R` |
| **Week-Block Bootstrap 95% CI** | `[-0.1390, -0.0810]R` | 7-day block temporal resampling |
| **Top-10 Outlier Share of Total R** | **`-1.4%`** (`+10.31R` / `-727.28R`) | Lower indicates broader distribution |
| **Mean Net R Without Top-10 Outliers** | **`-0.1115R`** (vs `-0.1097R` all) | Baseline expectancy excluding windfalls |
| **Per-Trade Standard Deviation ($s$)** | `0.9207R` | Sample trade dispersion ($ddof=1$) |
| **Trades for 80% Power (One-sided $\alpha$)** | `N/A (mean <= 0)` | $N = \lceil ((z_{1-\alpha} + z_{0.80}) s / \mu)^2 \rceil$ |
| **Trades for 80% Power (Two-sided $\alpha=0.05$)** | `N/A (mean <= 0)` | $N = \lceil ((1.960 + 0.842) s / \mu)^2 \rceil$ |

**Calendar Year Performance Breakdown**:
| Year | Trades ($n$) | Win Rate | Mean Net R | Total Net R |
| :--- | :---: | :---: | :---: | :---: |
| 2021 | 1,112 | 51.0% | -0.0712R | -79.17R |
| 2022 | 1,208 | 49.7% | -0.1006R | -121.55R |
| 2023 | 1,213 | 49.1% | -0.1147R | -139.07R |
| 2024 | 1,449 | 48.1% | -0.1261R | -182.68R |
| 2025 | 1,318 | 49.7% | -0.1059R | -139.56R |
| 2026 | 327 | 46.2% | -0.1995R | -65.25R |


---

### Hypothesis: FVGRetest
**Concept:** HTF Trend + SMC Fair Value Gap Retest & Mitigation Rejection

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 0.7073`** (Empirical rank within 1,000 matched null passes)  
> Matched Null Dropped Trade Share: **`0.00%`**  
> Total Candidate Signals: `28990` | Executed Trades: `7828`  
> Net Profit Factor: **`0.84`** (Gross: `0.93`)  
> Net Expectancy: **`-0.0715R`** (Gross: `-0.0283R`)  
> Max Drawdown (R): **`594.76R`** | Cumulative Net R: **`-559.69R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.0908R, -0.0519R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`+0.0000R`** (Matched 95th Pct Hurdle: `+0.0000R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2022-05-24 09:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 7828 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.0715R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.0908R <= 0.0R
- ❌ Matched Rank p-value 0.7073 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.84 (Null p95 Hurdle: 0.00, p=0.5000)
- ℹ️ Net Win Rate: 46.93% (Null p95 Hurdle: 0.00%, p=0.5000)
- ℹ️ Max Drawdown (R): 594.76R
- ℹ️ Sharpe Ratio: -1.18 (p=0.0087)
- ℹ️ Median Stop % of Price: 2.22%
- ℹ️ Cost Drag in R per Trade: +0.0432R
- ℹ️ Median Holding Time: 7 bars (7h)
- ℹ️ Trades per Symbol: ADAUSDT: 737, AVAXUSDT: 713, BNBUSDT: 902, BTCUSDT: 948, DOGEUSDT: 701, ETHUSDT: 995, LINKUSDT: 633, SOLUSDT: 967, SUIUSDT: 394, XRPUSDT: 838
- ℹ️ Per-Year Net R: 2021: -27.74R, 2022: -151.85R, 2023: -115.28R, 2024: -113.80R, 2025: -107.63R, 2026: -43.38R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 737 | 47.5% | 0.79 (0.87) | $-7,203.39 | -0.0977R (-0.0553R) |
| `AVAXUSDT` | 713 | 51.1% | 0.94 (1.03) | $-1,773.70 | -0.0249R (+0.0123R) |
| `BNBUSDT` | 902 | 44.2% | 0.86 (0.98) | $-5,152.66 | -0.0571R (-0.0093R) |
| `BTCUSDT` | 948 | 42.6% | 0.80 (0.93) | $-7,349.95 | -0.0775R (-0.0260R) |
| `DOGEUSDT` | 701 | 46.9% | 0.77 (0.85) | $-7,596.64 | -0.1084R (-0.0671R) |
| `ETHUSDT` | 995 | 48.9% | 0.95 (1.07) | $-1,984.35 | -0.0199R (+0.0244R) |
| `LINKUSDT` | 633 | 47.7% | 0.82 (0.91) | $-5,105.14 | -0.0806R (-0.0403R) |
| `SOLUSDT` | 967 | 46.3% | 0.73 (0.80) | $-12,830.70 | -0.1327R (-0.0928R) |
| `SUIUSDT` | 394 | 48.5% | 0.84 (0.92) | $-3,018.06 | -0.0766R (-0.0376R) |
| `XRPUSDT` | 838 | 47.7% | 0.89 (0.99) | $-3,954.61 | -0.0472R (-0.0042R) |

#### Robustness Panel: FVGRetest
*(Report-only diagnostics; does not modify pre-registered hypothesis verdict)*

| Diagnostic Metric | Value | Reference / Standard |
| :--- | :--- | :--- |
| **Matched $p$-value** | `0.7073` (N=1,000) | Gate threshold $\alpha = 0.05$ |
| **Monte Carlo Standard Error** | `SE = 0.0144` | $\sqrt{p(1-p)/N}$ |
| **Threshold within 2 SE?** | No (threshold outside 2 SE) | $|p - \alpha| \le 2 \times SE$ |
| **iid Bootstrap 2.5% Bound (30 seeds)** | **`0.0%`** above zero | Min: `-0.0918R`, Max: `-0.0897R` |
| **Week-Block Bootstrap 95% CI** | `[-0.0945, -0.0488]R` | 7-day block temporal resampling |
| **Top-10 Outlier Share of Total R** | **`-1.8%`** (`+10.31R` / `-559.69R`) | Lower indicates broader distribution |
| **Mean Net R Without Top-10 Outliers** | **`-0.0729R`** (vs `-0.0715R` all) | Baseline expectancy excluding windfalls |
| **Per-Trade Standard Deviation ($s$)** | `0.8713R` | Sample trade dispersion ($ddof=1$) |
| **Trades for 80% Power (One-sided $\alpha$)** | `N/A (mean <= 0)` | $N = \lceil ((z_{1-\alpha} + z_{0.80}) s / \mu)^2 \rceil$ |
| **Trades for 80% Power (Two-sided $\alpha=0.05$)** | `N/A (mean <= 0)` | $N = \lceil ((1.960 + 0.842) s / \mu)^2 \rceil$ |

**Calendar Year Performance Breakdown**:
| Year | Trades ($n$) | Win Rate | Mean Net R | Total Net R |
| :--- | :---: | :---: | :---: | :---: |
| 2021 | 1,685 | 52.2% | -0.0165R | -27.74R |
| 2022 | 1,489 | 46.4% | -0.1020R | -151.85R |
| 2023 | 1,322 | 43.0% | -0.0872R | -115.28R |
| 2024 | 1,598 | 46.8% | -0.0712R | -113.80R |
| 2025 | 1,367 | 46.3% | -0.0787R | -107.63R |
| 2026 | 367 | 42.0% | -0.1182R | -43.38R |


---

### Hypothesis: ConfluenceThreshold
**Concept:** Multi-Timeframe 0-100 Confluence Model Triggered at High Conviction (>= 75)

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 1.0000`** (Empirical rank within 1,000 matched null passes)  
> Matched Null Dropped Trade Share: **`0.00%`**  
> Total Candidate Signals: `187951` | Executed Trades: `17171`  
> Net Profit Factor: **`0.71`** (Gross: `0.80`)  
> Net Expectancy: **`-0.1513R`** (Gross: `-0.0940R`)  
> Max Drawdown (R): **`2606.85R`** | Cumulative Net R: **`-2597.83R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.1657R, -0.1375R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`+0.0000R`** (Matched 95th Pct Hurdle: `+0.0000R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2021-03-23 20:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 17171 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.1513R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.1657R <= 0.0R
- ❌ Matched Rank p-value 1.0000 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.71 (Null p95 Hurdle: 0.00, p=0.5000)
- ℹ️ Net Win Rate: 46.96% (Null p95 Hurdle: 0.00%, p=0.5000)
- ℹ️ Max Drawdown (R): 2606.85R
- ℹ️ Sharpe Ratio: -2.40 (p=0.0114)
- ℹ️ Median Stop % of Price: 1.82%
- ℹ️ Cost Drag in R per Trade: +0.0573R
- ℹ️ Median Holding Time: 4 bars (4h)
- ℹ️ Trades per Symbol: ADAUSDT: 1555, AVAXUSDT: 1527, BNBUSDT: 1865, BTCUSDT: 2295, DOGEUSDT: 1722, ETHUSDT: 2285, LINKUSDT: 1233, SOLUSDT: 2169, SUIUSDT: 748, XRPUSDT: 1772
- ℹ️ Per-Year Net R: 2021: -504.47R, 2022: -416.65R, 2023: -546.25R, 2024: -480.77R, 2025: -449.06R, 2026: -200.63R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 1555 | 47.7% | 0.73 (0.82) | $-21,371.05 | -0.1374R (-0.0854R) |
| `AVAXUSDT` | 1527 | 44.9% | 0.67 (0.74) | $-27,276.82 | -0.1786R (-0.1310R) |
| `BNBUSDT` | 1865 | 46.2% | 0.67 (0.78) | $-34,000.28 | -0.1823R (-0.1105R) |
| `BTCUSDT` | 2295 | 47.2% | 0.70 (0.84) | $-34,965.50 | -0.1524R (-0.0738R) |
| `DOGEUSDT` | 1722 | 45.5% | 0.67 (0.75) | $-30,331.02 | -0.1761R (-0.1252R) |
| `ETHUSDT` | 2285 | 48.7% | 0.77 (0.88) | $-26,047.88 | -0.1140R (-0.0521R) |
| `LINKUSDT` | 1233 | 42.7% | 0.61 (0.67) | $-27,602.36 | -0.2239R (-0.1752R) |
| `SOLUSDT` | 2169 | 48.5% | 0.76 (0.84) | $-26,133.54 | -0.1205R (-0.0769R) |
| `SUIUSDT` | 748 | 48.1% | 0.79 (0.86) | $-8,010.86 | -0.1071R (-0.0657R) |
| `XRPUSDT` | 1772 | 48.6% | 0.73 (0.83) | $-24,043.98 | -0.1357R (-0.0779R) |

#### Robustness Panel: ConfluenceThreshold
*(Report-only diagnostics; does not modify pre-registered hypothesis verdict)*

| Diagnostic Metric | Value | Reference / Standard |
| :--- | :--- | :--- |
| **Matched $p$-value** | `1.0000` (N=1,000) | Gate threshold $\alpha = 0.05$ |
| **Monte Carlo Standard Error** | `SE = 0.0000` | $\sqrt{p(1-p)/N}$ |
| **Threshold within 2 SE?** | No (threshold outside 2 SE) | $|p - \alpha| \le 2 \times SE$ |
| **iid Bootstrap 2.5% Bound (30 seeds)** | **`0.0%`** above zero | Min: `-0.1662R`, Max: `-0.1646R` |
| **Week-Block Bootstrap 95% CI** | `[-0.1760, -0.1260]R` | 7-day block temporal resampling |
| **Top-10 Outlier Share of Total R** | **`-0.4%`** (`+11.37R` / `-2597.83R`) | Lower indicates broader distribution |
| **Mean Net R Without Top-10 Outliers** | **`-0.1520R`** (vs `-0.1513R` all) | Baseline expectancy excluding windfalls |
| **Per-Trade Standard Deviation ($s$)** | `0.9333R` | Sample trade dispersion ($ddof=1$) |
| **Trades for 80% Power (One-sided $\alpha$)** | `N/A (mean <= 0)` | $N = \lceil ((z_{1-\alpha} + z_{0.80}) s / \mu)^2 \rceil$ |
| **Trades for 80% Power (Two-sided $\alpha=0.05$)** | `N/A (mean <= 0)` | $N = \lceil ((1.960 + 0.842) s / \mu)^2 \rceil$ |

**Calendar Year Performance Breakdown**:
| Year | Trades ($n$) | Win Rate | Mean Net R | Total Net R |
| :--- | :---: | :---: | :---: | :---: |
| 2021 | 3,240 | 46.9% | -0.1557R | -504.47R |
| 2022 | 3,235 | 47.6% | -0.1288R | -416.65R |
| 2023 | 3,100 | 45.8% | -0.1762R | -546.25R |
| 2024 | 3,501 | 47.8% | -0.1373R | -480.77R |
| 2025 | 3,145 | 47.4% | -0.1428R | -449.06R |
| 2026 | 950 | 44.2% | -0.2112R | -200.63R |


---

### Hypothesis: LiquiditySweep
**Concept:** Swing Failure Pattern (SFP) sweeping key liquidity with rejection

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Matched Rank $p$-value: **`p = 1.0000`** (Empirical rank within 1,000 matched null passes)  
> Matched Null Dropped Trade Share: **`0.00%`**  
> Total Candidate Signals: `73577` | Executed Trades: `22464`  
> Net Profit Factor: **`0.62`** (Gross: `0.75`)  
> Net Expectancy: **`-0.2157R`** (Gross: `-0.1239R`)  
> Max Drawdown (R): **`4846.73R`** | Cumulative Net R: **`-4845.32R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.2286R, -0.2031R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`+0.0000R`** (Matched 95th Pct Hurdle: `+0.0000R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `YES (at 2021-01-27 10:00 UTC)`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 22464 >= 100 trades

**Failed / Unmet:**
- ❌ Mean Net Expectancy -0.2157R <= 0.0R
- ❌ Bootstrap 95% CI lower bound -0.2286R <= 0.0R
- ❌ Matched Rank p-value 1.0000 >= 0.05

#### Diagnostics:
- ℹ️ Net Profit Factor: 0.62 (Null p95 Hurdle: 0.00, p=0.5000)
- ℹ️ Net Win Rate: 46.90% (Null p95 Hurdle: 0.00%, p=0.5000)
- ℹ️ Max Drawdown (R): 4846.73R
- ℹ️ Sharpe Ratio: -2.64 (p=0.0119)
- ℹ️ Median Stop % of Price: 0.88%
- ℹ️ Cost Drag in R per Trade: +0.0919R
- ℹ️ Median Holding Time: 2 bars (2h)
- ℹ️ Trades per Symbol: ADAUSDT: 2165, AVAXUSDT: 2061, BNBUSDT: 2349, BTCUSDT: 2524, DOGEUSDT: 2413, ETHUSDT: 2658, LINKUSDT: 2110, SOLUSDT: 2567, SUIUSDT: 1187, XRPUSDT: 2430
- ℹ️ Per-Year Net R: 2021: -1031.82R, 2022: -808.11R, 2023: -825.46R, 2024: -972.91R, 2025: -920.41R, 2026: -286.61R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 2165 | 47.3% | 0.63 (0.75) | $-47,313.01 | -0.2185R (-0.1278R) |
| `AVAXUSDT` | 2061 | 44.8% | 0.60 (0.71) | $-49,668.52 | -0.2410R (-0.1563R) |
| `BNBUSDT` | 2349 | 47.3% | 0.63 (0.78) | $-48,383.08 | -0.2060R (-0.1066R) |
| `BTCUSDT` | 2524 | 47.2% | 0.63 (0.79) | $-51,044.08 | -0.2022R (-0.0990R) |
| `DOGEUSDT` | 2413 | 45.5% | 0.58 (0.70) | $-59,664.68 | -0.2473R (-0.1570R) |
| `ETHUSDT` | 2658 | 47.4% | 0.63 (0.76) | $-55,844.87 | -0.2101R (-0.1157R) |
| `LINKUSDT` | 2110 | 45.8% | 0.60 (0.72) | $-49,872.42 | -0.2364R (-0.1456R) |
| `SOLUSDT` | 2567 | 47.9% | 0.65 (0.77) | $-50,393.32 | -0.1963R (-0.1138R) |
| `SUIUSDT` | 1187 | 48.1% | 0.67 (0.80) | $-21,772.00 | -0.1834R (-0.0983R) |
| `XRPUSDT` | 2430 | 47.8% | 0.63 (0.77) | $-50,576.37 | -0.2081R (-0.1152R) |

#### Robustness Panel: LiquiditySweep
*(Report-only diagnostics; does not modify pre-registered hypothesis verdict)*

| Diagnostic Metric | Value | Reference / Standard |
| :--- | :--- | :--- |
| **Matched $p$-value** | `1.0000` (N=1,000) | Gate threshold $\alpha = 0.05$ |
| **Monte Carlo Standard Error** | `SE = 0.0000` | $\sqrt{p(1-p)/N}$ |
| **Threshold within 2 SE?** | No (threshold outside 2 SE) | $|p - \alpha| \le 2 \times SE$ |
| **iid Bootstrap 2.5% Bound (30 seeds)** | **`0.0%`** above zero | Min: `-0.2290R`, Max: `-0.2274R` |
| **Week-Block Bootstrap 95% CI** | `[-0.2324, -0.1989]R` | 7-day block temporal resampling |
| **Top-10 Outlier Share of Total R** | **`-0.2%`** (`+10.31R` / `-4845.32R`) | Lower indicates broader distribution |
| **Mean Net R Without Top-10 Outliers** | **`-0.2162R`** (vs `-0.2157R` all) | Baseline expectancy excluding windfalls |
| **Per-Trade Standard Deviation ($s$)** | `0.9630R` | Sample trade dispersion ($ddof=1$) |
| **Trades for 80% Power (One-sided $\alpha$)** | `N/A (mean <= 0)` | $N = \lceil ((z_{1-\alpha} + z_{0.80}) s / \mu)^2 \rceil$ |
| **Trades for 80% Power (Two-sided $\alpha=0.05$)** | `N/A (mean <= 0)` | $N = \lceil ((1.960 + 0.842) s / \mu)^2 \rceil$ |

**Calendar Year Performance Breakdown**:
| Year | Trades ($n$) | Win Rate | Mean Net R | Total Net R |
| :--- | :---: | :---: | :---: | :---: |
| 2021 | 4,849 | 46.3% | -0.2128R | -1031.82R |
| 2022 | 4,209 | 48.2% | -0.1920R | -808.11R |
| 2023 | 3,722 | 46.6% | -0.2218R | -825.46R |
| 2024 | 4,470 | 47.3% | -0.2177R | -972.91R |
| 2025 | 4,110 | 46.6% | -0.2239R | -920.41R |
| 2026 | 1,104 | 45.5% | -0.2596R | -286.61R |


---

### Hypothesis: Donchian4hN20
**Concept:** 4h Donchian Breakout N=20 + ATR(14) 3R Trailing Stop (H1a)

> **Statistical Verdict**: **`REJECTED (p >= 0.025)`**  
> Matched Rank $p$-value: **`p = 0.0669`** (Empirical rank within 1,000 matched null passes)  
> Matched Null Dropped Trade Share: **`0.00%`**  
> Total Candidate Signals: `9755` | Executed Trades: `1055`  
> Net Profit Factor: **`1.14`** (Gross: `1.23`)  
> Net Expectancy: **`+0.0495R`** (Gross: `+0.0774R`)  
> Max Drawdown (R): **`24.59R`** | Cumulative Net R: **`+52.17R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[-0.0162R, +0.1187R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`+0.0000R`** (Matched 95th Pct Hurdle: `+0.0000R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `NO`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 1055 >= 100 trades
- ✅ Mean Net Expectancy +0.0495R > 0.0R

**Failed / Unmet:**
- ❌ Bootstrap 95% CI lower bound -0.0162R <= 0.0R
- ❌ Matched Rank p-value 0.0669 >= 0.025

#### Diagnostics:
- ℹ️ Net Profit Factor: 1.14 (Null p95 Hurdle: 0.00, p=0.5000)
- ℹ️ Net Win Rate: 38.29% (Null p95 Hurdle: 0.00%, p=0.5000)
- ℹ️ Max Drawdown (R): 24.59R
- ℹ️ Sharpe Ratio: -0.19 (p=0.0070)
- ℹ️ Median Stop % of Price: 7.60%
- ℹ️ Cost Drag in R per Trade: +0.0288R
- ℹ️ Median Holding Time: 60 bars (60h)
- ℹ️ Trades per Symbol: ADAUSDT: 81, AVAXUSDT: 83, BNBUSDT: 105, BTCUSDT: 185, DOGEUSDT: 86, ETHUSDT: 145, LINKUSDT: 78, SOLUSDT: 138, SUIUSDT: 57, XRPUSDT: 97
- ℹ️ Per-Year Net R: 2021: -9.34R, 2022: +10.19R, 2023: +22.55R, 2024: +12.99R, 2025: +22.88R, 2026: -7.08R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 81 | 38.3% | 1.15 (1.24) | $+416.98 | +0.0515R (+0.0770R) |
| `AVAXUSDT` | 83 | 37.3% | 1.11 (1.17) | $+338.68 | +0.0408R (+0.0604R) |
| `BNBUSDT` | 105 | 38.1% | 1.19 (1.31) | $+692.30 | +0.0659R (+0.1026R) |
| `BTCUSDT` | 185 | 39.5% | 1.17 (1.32) | $+1,123.95 | +0.0608R (+0.1045R) |
| `DOGEUSDT` | 86 | 39.5% | 1.23 (1.29) | $+750.21 | +0.0872R (+0.1069R) |
| `ETHUSDT` | 145 | 39.3% | 1.49 (1.60) | $+2,569.63 | +0.1772R (+0.2064R) |
| `LINKUSDT` | 78 | 43.6% | 0.99 (1.07) | $-31.23 | -0.0040R (+0.0209R) |
| `SOLUSDT` | 138 | 39.1% | 0.90 (0.96) | $-457.67 | -0.0332R (-0.0120R) |
| `SUIUSDT` | 57 | 35.1% | 1.21 (1.28) | $+490.93 | +0.0861R (+0.1075R) |
| `XRPUSDT` | 97 | 30.9% | 0.83 (0.89) | $-676.39 | -0.0697R (-0.0420R) |

#### Robustness Panel: Donchian4hN20
*(Report-only diagnostics; does not modify pre-registered hypothesis verdict)*

| Diagnostic Metric | Value | Reference / Standard |
| :--- | :--- | :--- |
| **Matched $p$-value** | `0.0669` (N=1,000) | Gate threshold $\alpha = 0.025$ |
| **Monte Carlo Standard Error** | `SE = 0.0079` | $\sqrt{p(1-p)/N}$ |
| **Threshold within 2 SE?** | No (threshold outside 2 SE) | $|p - \alpha| \le 2 \times SE$ |
| **iid Bootstrap 2.5% Bound (30 seeds)** | **`0.0%`** above zero | Min: `-0.0204R`, Max: `-0.0149R` |
| **Week-Block Bootstrap 95% CI** | `[-0.0278, +0.1319]R` | 7-day block temporal resampling |
| **Top-10 Outlier Share of Total R** | **`112.1%`** (`+58.51R` / `+52.17R`) | Lower indicates broader distribution |
| **Mean Net R Without Top-10 Outliers** | **`-0.0061R`** (vs `+0.0495R` all) | Baseline expectancy excluding windfalls |
| **Per-Trade Standard Deviation ($s$)** | `1.1281R` | Sample trade dispersion ($ddof=1$) |
| **Trades for 80% Power (One-sided $\alpha$)** | `4,077` | $N = \lceil ((z_{1-\alpha} + z_{0.80}) s / \mu)^2 \rceil$ |
| **Trades for 80% Power (Two-sided $\alpha=0.05$)** | `4,937` | $N = \lceil ((1.960 + 0.842) s / \mu)^2 \rceil$ |

**Calendar Year Performance Breakdown**:
| Year | Trades ($n$) | Win Rate | Mean Net R | Total Net R |
| :--- | :---: | :---: | :---: | :---: |
| 2021 | 219 | 35.2% | -0.0427R | -9.34R |
| 2022 | 186 | 43.5% | +0.0548R | +10.19R |
| 2023 | 208 | 37.0% | +0.1084R | +22.55R |
| 2024 | 201 | 39.8% | +0.0646R | +12.99R |
| 2025 | 181 | 38.7% | +0.1264R | +22.88R |
| 2026 | 60 | 31.7% | -0.1181R | -7.08R |


---

### Hypothesis: Donchian4hN55
**Concept:** 4h Donchian Breakout N=55 + ATR(14) 3R Trailing Stop (H1b)

> **Statistical Verdict**: **`PASSED DEV GATE (see robustness) (p < 0.025)`**  
> Matched Rank $p$-value: **`p = 0.0195`** (Empirical rank within 10,000 matched null passes)  
> Matched Null Dropped Trade Share: **`0.00%`**  
> Total Candidate Signals: `5274` | Executed Trades: `832`  
> Net Profit Factor: **`1.22`** (Gross: `1.31`)  
> Net Expectancy: **`+0.0788R`** (Gross: `+0.1047R`)  
> Max Drawdown (R): **`16.69R`** | Cumulative Net R: **`+65.56R`**  
> Days Evaluated: **`1933 days (100.0% of dataset)`**  
> Bootstrap 95% CI (Mean Net R): **`[+0.0021R, +0.1590R]`** (10,000 resamples)  
> Matched Baseline Median Net EV: **`+0.0003R`** (Matched 95th Pct Hurdle: `+0.0000R`)  
> Sizing Mode: `fixed_risk` | Account Ruined: `NO`

#### Alpha Validation Requirements:
**Passed:**
- ✅ Sample size 832 >= 100 trades
- ✅ Mean Net Expectancy +0.0788R > 0.0R
- ✅ Bootstrap 95% CI lower bound +0.0021R > 0.0R
- ✅ Matched Rank p-value 0.0195 < 0.025

**Failed / Unmet:**
- None

#### Diagnostics:
- ℹ️ Net Profit Factor: 1.22 (Null p95 Hurdle: 0.00, p=0.5000)
- ℹ️ Net Win Rate: 37.62% (Null p95 Hurdle: 0.00%, p=0.5000)
- ℹ️ Max Drawdown (R): 16.69R
- ℹ️ Sharpe Ratio: 0.47 (p=0.0060)
- ℹ️ Median Stop % of Price: 7.55%
- ℹ️ Cost Drag in R per Trade: +0.0264R
- ℹ️ Median Holding Time: 52 bars (52h)
- ℹ️ Trades per Symbol: ADAUSDT: 66, AVAXUSDT: 77, BNBUSDT: 84, BTCUSDT: 129, DOGEUSDT: 75, ETHUSDT: 107, LINKUSDT: 66, SOLUSDT: 109, SUIUSDT: 46, XRPUSDT: 73
- ℹ️ Per-Year Net R: 2021: +10.25R, 2022: +3.83R, 2023: +23.21R, 2024: +13.11R, 2025: +18.11R, 2026: -2.94R

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Net PF (Gross) | Net PnL (USD) | Net R (Gross R) |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 66 | 42.4% | 1.49 (1.60) | $+1,070.79 | +0.1622R (+0.1872R) |
| `AVAXUSDT` | 77 | 36.4% | 1.31 (1.37) | $+895.02 | +0.1162R (+0.1338R) |
| `BNBUSDT` | 84 | 44.0% | 1.82 (1.99) | $+2,249.96 | +0.2679R (+0.3035R) |
| `BTCUSDT` | 129 | 34.9% | 1.19 (1.32) | $+894.14 | +0.0693R (+0.1115R) |
| `DOGEUSDT` | 75 | 42.7% | 1.41 (1.48) | $+1,056.30 | +0.1408R (+0.1597R) |
| `ETHUSDT` | 107 | 38.3% | 1.11 (1.19) | $+412.53 | +0.0386R (+0.0667R) |
| `LINKUSDT` | 66 | 31.8% | 0.98 (1.03) | $-52.38 | -0.0079R (+0.0126R) |
| `SOLUSDT` | 109 | 37.6% | 0.92 (0.97) | $-299.49 | -0.0275R (-0.0084R) |
| `SUIUSDT` | 46 | 28.3% | 1.05 (1.10) | $+93.54 | +0.0203R (+0.0404R) |
| `XRPUSDT` | 73 | 37.0% | 1.09 (1.16) | $+235.19 | +0.0322R (+0.0560R) |

#### Robustness Panel: Donchian4hN55
*(Report-only diagnostics; does not modify pre-registered hypothesis verdict)*

| Diagnostic Metric | Value | Reference / Standard |
| :--- | :--- | :--- |
| **Matched $p$-value** | `0.0195` (N=10,000) | Gate threshold $\alpha = 0.025$ |
| **Monte Carlo Standard Error** | `SE = 0.0014` | $\sqrt{p(1-p)/N}$ |
| **Threshold within 2 SE?** | No | $|p - \alpha| \le 2 \times SE$ |
| **iid Bootstrap 2.5% Bound (30 seeds)** | **`93.3%`** above zero | Min: `-0.0018R`, Max: `+0.0078R` |
| **Week-Block Bootstrap 95% CI** | `[-0.0151, +0.1796]R` | 7-day block temporal resampling |
| **Top-10 Outlier Share of Total R** | **`82.0%`** (`+53.76R` / `+65.56R`) | Lower indicates broader distribution |
| **Mean Net R Without Top-10 Outliers** | **`+0.0144R`** (vs `+0.0788R` all) | Baseline expectancy excluding windfalls |
| **Per-Trade Standard Deviation ($s$)** | `1.1415R` | Sample trade dispersion ($ddof=1$) |
| **Trades for 80% Power (One-sided $\alpha$)** | `1,648` | $N = \lceil ((z_{1-\alpha} + z_{0.80}) s / \mu)^2 \rceil$ |
| **Trades for 80% Power (Two-sided $\alpha=0.05$)** | `1,995` | $N = \lceil ((1.960 + 0.842) s / \mu)^2 \rceil$ |

**Calendar Year Performance Breakdown**:
| Year | Trades ($n$) | Win Rate | Mean Net R | Total Net R |
| :--- | :---: | :---: | :---: | :---: |
| 2021 | 176 | 35.8% | +0.0582R | +10.25R |
| 2022 | 142 | 40.1% | +0.0270R | +3.83R |
| 2023 | 171 | 36.3% | +0.1357R | +23.21R |
| 2024 | 166 | 39.2% | +0.0789R | +13.11R |
| 2025 | 138 | 39.9% | +0.1313R | +18.11R |
| 2026 | 39 | 28.2% | -0.0755R | -2.94R |


---

## 4. Strategic Implications for Discretionary Terminal

1. **Passed Dev Gate (see robustness) (1/6)**:
   The following strategies met all pre-registered dev gate criteria ($p < 0.05$, positive bootstrap CI lower bound):
   - **Donchian4hN55**: Net EV = `+0.0788R` (Gross: `+0.1047R`), Net PF = `1.22`, Bootstrap 95% CI = `[+0.0021, +0.1590]R`, Matched Rank $p$-value = `0.0195` (N=10,000)

2. **Unvalidated Patterns**:
   The following strategies failed validation: **TrendPullback**, **FVGRetest**, **ConfluenceThreshold**, **LiquiditySweep**, **Donchian4hN20**. These setups show no validated edge under institutional friction and will display warning banners.

3. **Evidence-Backed Radar Screening**:
   Only configurations meeting strict statistical criteria are highlighted as candidate setups on the Discretionary Radar.

---

## 5. Limitations

1. **Position Sizing Mode (`fixed_risk`)**:
   Evaluations are performed under `fixed_risk` position sizing. In fixed-risk mode, risk is calibrated to initial equity without compounding, ensuring trade-level statistical edge is measured uniformly across the full dataset period without sample truncation from ruin guards.
2. **Sample Size Evaluated Per Strategy**:
   - **TrendPullback**: 6,627 executed trades (1933 days evaluated, 100.0% of dataset)
   - **FVGRetest**: 7,828 executed trades (1933 days evaluated, 100.0% of dataset)
   - **ConfluenceThreshold**: 17,171 executed trades (1933 days evaluated, 100.0% of dataset)
   - **LiquiditySweep**: 22,464 executed trades (1933 days evaluated, 100.0% of dataset)
   - **Donchian4hN20**: 1,055 executed trades (1933 days evaluated, 100.0% of dataset)
   - **Donchian4hN55**: 832 executed trades (1933 days evaluated, 100.0% of dataset)
3. **No Out-of-Sample Holdout**:
   No holdout dataset has been used yet. All metrics reflect in-sample backtesting across the complete historical timeline.
4. **Gross vs. Net Metrics**:
   Both gross P&L (pre-friction) and net P&L (post-fees, post-slippage, and post-funding) are reported side-by-side. The comparison reveals the decisive impact of transaction costs and funding drag on theoretical retail chart patterns.

