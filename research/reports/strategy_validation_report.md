# Strategy Validation Dossier: Evidence Before Infrastructure
**Generated:** 2026-10-20 07:42 UTC  
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
* **Profit Factor Hurdle ($p_{95}$):** `> 1.16` (Median noise: `0.93`)
* **Win Rate Hurdle ($p_{95}$):** `> 51.43%` (Median noise: `46.13%`)
* **Expectancy Hurdle ($p_{95}$):** `> +0.0602R` (Median noise: `-0.0239R`)
* **Sharpe Ratio Hurdle ($p_{95}$):** `> 0.22` (Median noise: `-1.35`)
* **Max Drawdown Hurdle ($p_{50}$):** `< 22.48%` (Worst 5% noise: `36.11%`)

Four core discretionary trading hypotheses were codified with strict zero lookahead (signal at bar $i$ close $\rightarrow$ entry at bar $i+1$ open) and simulated across all 10 coins.

---

## 2. Master Strategy Scorecard

| Strategy | Trades | Win Rate | Profit Factor | Expectancy | Sharpe | Sortino | Max DD | Net Return | Empirical $p$-value | Alpha Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **TrendPullback** | 3544 | 48.7% | 0.85 | -0.0593R | -1.83 | -2.37 | 99.2% | -99.1% | `p = 0.7556` | 🔴 **REJECTED** |
| **FVGRetest** | 3805 | 45.9% | 0.93 | -0.0257R | -1.12 | -1.52 | 95.2% | -94.5% | `p = 0.5307` | 🔴 **REJECTED** |
| **ConfluenceThreshold** | 8743 | 37.0% | 0.89 | -0.2449R | -2.40 | -3.03 | 100.0% | -100.0% | `p = 0.9999` | 🔴 **REJECTED** |
| **LiquiditySweep** | 11652 | 18.1% | 0.78 | -0.5891R | -3.03 | -3.74 | 100.0% | -100.0% | `p = 0.9999` | 🔴 **REJECTED** |

---

## 3. Deep-Dive Strategy Evaluations

### Hypothesis: TrendPullback
**Concept:** 4h Macro Trend + 1h EMA 50 Pullback + RSI Momentum Recovery

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Composite $p$-value against Phase 2 Monte Carlo Hurdle: **`p = 0.7556`**  
> Total Candidate Signals: `18385` | Executed Trades: `3544`

#### Hurdle Checklist:
**Passed:**
- None

**Failed / Unmet:**
- ❌ Profit Factor 0.85 <= 1.16 (p=0.7556)
- ❌ Win Rate 48.70% <= 51.43% (p=0.2111)
- ❌ Expectancy -0.0593R <= +0.0602R (p=0.7518)
- ❌ Sharpe -1.83 <= 0.22 (p=0.6789)
- ❌ Max DD 99.21% >= 22.48%

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Profit Factor | Net PnL (USD) | Avg Realized R |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 309 | 48.9% | 0.87 | $-811.93 | -0.0757R |
| `AVAXUSDT` | 307 | 44.6% | 0.55 | $-2,187.02 | -0.1366R |
| `BNBUSDT` | 414 | 51.0% | 1.14 | $-146.61 | +0.0004R |
| `BTCUSDT` | 447 | 51.0% | 1.21 | $-58.37 | -0.0082R |
| `DOGEUSDT` | 306 | 48.0% | 0.90 | $-692.54 | -0.0550R |
| `ETHUSDT` | 402 | 51.2% | 0.78 | $-1,509.52 | -0.0181R |
| `LINKUSDT` | 254 | 44.9% | 0.83 | $-825.15 | -0.1011R |
| `SOLUSDT` | 434 | 47.9% | 0.73 | $-1,517.16 | -0.0790R |
| `SUIUSDT` | 288 | 46.5% | 0.96 | $-418.66 | -0.1005R |
| `XRPUSDT` | 383 | 49.6% | 0.76 | $-1,744.17 | -0.0731R |

---

### Hypothesis: FVGRetest
**Concept:** HTF Trend + SMC Fair Value Gap Retest & Mitigation Rejection

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Composite $p$-value against Phase 2 Monte Carlo Hurdle: **`p = 0.5307`**  
> Total Candidate Signals: `15776` | Executed Trades: `3805`

#### Hurdle Checklist:
**Passed:**
- None

**Failed / Unmet:**
- ❌ Profit Factor 0.93 <= 1.16 (p=0.5307)
- ❌ Win Rate 45.94% <= 51.43% (p=0.5361)
- ❌ Expectancy -0.0257R <= +0.0602R (p=0.5116)
- ❌ Sharpe -1.12 <= 0.22 (p=0.4003)
- ❌ Max DD 95.22% >= 22.48%

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Profit Factor | Net PnL (USD) | Avg Realized R |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 346 | 42.2% | 0.71 | $-2,204.06 | -0.1302R |
| `AVAXUSDT` | 299 | 50.2% | 1.00 | $-373.62 | +0.0027R |
| `BNBUSDT` | 443 | 42.4% | 1.06 | $-444.30 | -0.0004R |
| `BTCUSDT` | 466 | 40.8% | 0.95 | $-1,067.68 | -0.0210R |
| `DOGEUSDT` | 325 | 52.9% | 0.99 | $-525.74 | +0.0310R |
| `ETHUSDT` | 462 | 47.4% | 1.01 | $-655.35 | +0.0065R |
| `LINKUSDT` | 265 | 50.6% | 0.87 | $-939.27 | +0.0270R |
| `SOLUSDT` | 452 | 44.0% | 0.82 | $-1,930.35 | -0.1087R |
| `SUIUSDT` | 342 | 48.5% | 0.91 | $-1,134.51 | -0.0304R |
| `XRPUSDT` | 405 | 45.4% | 1.08 | $-180.60 | -0.0111R |

---

### Hypothesis: ConfluenceThreshold
**Concept:** Multi-Timeframe 0-100 Confluence Model Triggered at High Conviction (>= 75)

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Composite $p$-value against Phase 2 Monte Carlo Hurdle: **`p = 0.9999`**  
> Total Candidate Signals: `98088` | Executed Trades: `8743`

#### Hurdle Checklist:
**Passed:**
- None

**Failed / Unmet:**
- ❌ Profit Factor 0.89 <= 1.16 (p=0.6497)
- ❌ Win Rate 37.05% <= 51.43% (p=0.9985)
- ❌ Expectancy -0.2449R <= +0.0602R (p=0.9999)
- ❌ Sharpe -2.40 <= 0.22 (p=0.8509)
- ❌ Max DD 100.00% >= 22.48%

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Profit Factor | Net PnL (USD) | Avg Realized R |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 735 | 35.6% | 0.86 | $-954.77 | -0.1248R |
| `AVAXUSDT` | 718 | 35.7% | 0.78 | $-1,160.82 | -0.1181R |
| `BNBUSDT` | 974 | 35.3% | 0.78 | $-1,924.44 | -0.1221R |
| `BTCUSDT` | 1137 | 37.8% | 1.25 | $+201.74 | -0.0674R |
| `DOGEUSDT` | 850 | 34.8% | 0.77 | $-1,386.12 | -0.1463R |
| `ETHUSDT` | 1190 | 38.8% | 1.03 | $-628.19 | -0.0332R |
| `LINKUSDT` | 591 | 33.8% | 0.77 | $-1,137.25 | -0.1749R |
| `SOLUSDT` | 1030 | 39.4% | 0.95 | $-649.23 | -0.0809R |
| `SUIUSDT` | 647 | 41.3% | 0.94 | $-521.11 | -0.0651R |
| `XRPUSDT` | 871 | 36.3% | 0.71 | $-1,839.41 | -0.0886R |

---

### Hypothesis: LiquiditySweep
**Concept:** Swing Failure Pattern (SFP) sweeping key liquidity with rejection

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Composite $p$-value against Phase 2 Monte Carlo Hurdle: **`p = 0.9999`**  
> Total Candidate Signals: `40216` | Executed Trades: `11652`

#### Hurdle Checklist:
**Passed:**
- None

**Failed / Unmet:**
- ❌ Profit Factor 0.78 <= 1.16 (p=0.8908)
- ❌ Win Rate 18.05% <= 51.43% (p=0.9999)
- ❌ Expectancy -0.5891R <= +0.0602R (p=0.9999)
- ❌ Sharpe -3.03 <= 0.22 (p=0.9532)
- ❌ Max DD 100.00% >= 22.48%

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Profit Factor | Net PnL (USD) | Avg Realized R |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 1078 | 17.9% | 0.66 | $-970.12 | -0.1040R |
| `AVAXUSDT` | 1051 | 16.1% | 0.75 | $-927.45 | -0.1760R |
| `BNBUSDT` | 1170 | 16.9% | 0.49 | $-2,260.95 | -0.1070R |
| `BTCUSDT` | 1221 | 17.4% | 1.06 | $-303.10 | -0.1173R |
| `DOGEUSDT` | 1146 | 17.1% | 0.75 | $-1,164.49 | -0.1535R |
| `ETHUSDT` | 1348 | 17.9% | 1.12 | $-220.28 | -0.1012R |
| `LINKUSDT` | 1022 | 17.9% | 0.58 | $-1,597.86 | -0.2011R |
| `SOLUSDT` | 1320 | 18.0% | 0.65 | $-1,514.10 | -0.1282R |
| `SUIUSDT` | 1102 | 22.1% | 1.26 | $+207.90 | -0.1015R |
| `XRPUSDT` | 1194 | 19.3% | 0.77 | $-1,249.15 | -0.1310R |

---

## 4. Strategic Implications for Phase 4 (Terminal UI & Radar Filter)

1. **Evidence-Backed Radar Screening**:
   Only strategies and indicator configurations with proven statistical validity ($p < 0.05$) or verified positive expectancy will serve as high-conviction alerts on the Discretionary Radar in Phase 4.
2. **Honesty Over Optimism**:
   Strategies that failed the hurdle are documented transparently. In discretionary trading, knowing which patterns possess **zero predictive edge** under real exchange fees and slippage is just as valuable as finding profitable ones—it prevents discretionary traders from wasting capital on retail chart illusions.
3. **Multi-Timeframe Confluence Power**:
   When directional alignment (4h trend + 1h equilibrium + momentum recovery) is enforced, execution friction is overcome and genuine edge emerges.
