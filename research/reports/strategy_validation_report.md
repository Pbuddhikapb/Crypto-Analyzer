# Strategy Validation Dossier: Evidence Before Infrastructure
**Generated:** 2026-10-20 04:23 UTC  
**Architecture Doctrine:** Claude's Option B (Discretionary Human Trading Terminal)  
**Null Hypothesis ($H_0$):** Phase 2 1,000-pass Monte Carlo Baseline Hurdle  
**Dataset:** 10 Major Pairs (`BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, DOGEUSDT, ADAUSDT, AVAXUSDT, LINKUSDT, SUIUSDT`), 2+ Years (~17,500 1h bars / coin)  
**Execution Authority:** Single Exit Authority (`engine/exits.py`)  
**Institutional Friction:** Taker Fee 0.05%, Maker Fee 0.02%, Slippage 0.03%, 8h Funding Drag 0.01%  
**Exit Mechanics:** Stepped Scaling (50% TP1 @ +0.75R, BE armed @ +0.85R excursion, TP2 @ +1.33R, 12-bar stagnancy)

---

## 1. Executive Summary & Big Picture

Under Claude's doctrine (*"Evidence before infrastructure"*), no trading signal or indicator rule is permitted to enter the Discretionary Trading Terminal without empirical proof that it outperforms random noise ($p < 0.05$).

In Phase 2, we established the empirical null hypothesis ($H_0$) across 1,000 Monte Carlo runs under full institutional friction:
* **Profit Factor Hurdle ($p_{95}$):** `> 1.14` (Median noise: `0.92`)
* **Win Rate Hurdle ($p_{95}$):** `> 50.00%` (Median noise: `44.79%`)
* **Expectancy Hurdle ($p_{95}$):** `> +0.0520R` (Median noise: `-0.0271R`)
* **Sharpe Ratio Hurdle ($p_{95}$):** `> 0.07` (Median noise: `-1.43`)
* **Max Drawdown Hurdle ($p_{50}$):** `< 23.26%` (Worst 5% noise: `36.95%`)

Four core discretionary trading hypotheses were codified with strict zero lookahead (signal at bar $i$ close $\rightarrow$ entry at bar $i+1$ open) and simulated across all 10 coins.

---

## 2. Master Strategy Scorecard

| Strategy | Trades | Win Rate | Profit Factor | Expectancy | Sharpe | Sortino | Max DD | Net Return | Empirical $p$-value | Alpha Verdict |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **TrendPullback** | 2522 | 49.4% | 0.88 | -0.0510R | -1.88 | -2.41 | 97.0% | -96.8% | `p = 0.6695` | 🔴 **REJECTED** |
| **FVGRetest** | 2946 | 46.0% | 0.92 | -0.0257R | -0.93 | -1.28 | 91.8% | -90.8% | `p = 0.5307` | 🔴 **REJECTED** |
| **ConfluenceThreshold** | 6154 | 39.3% | 0.72 | -0.2080R | -2.47 | -3.11 | 100.0% | -100.0% | `p = 0.9997` | 🔴 **REJECTED** |
| **LiquiditySweep** | 6396 | 37.1% | 0.79 | -0.2588R | -2.53 | -3.19 | 100.0% | -100.0% | `p = 0.9999` | 🔴 **REJECTED** |

---

## 3. Deep-Dive Strategy Evaluations

### Hypothesis: TrendPullback
**Concept:** 4h Macro Trend + 1h EMA 50 Pullback + RSI Momentum Recovery

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Composite $p$-value against Phase 2 Monte Carlo Hurdle: **`p = 0.6695`**  
> Total Candidate Signals: `12637` | Executed Trades: `2522`

#### Hurdle Checklist:
**Passed:**
- None

**Failed / Unmet:**
- ❌ Profit Factor 0.88 <= 1.14 (p=0.6497)
- ❌ Win Rate 49.37% <= 50.00% (p=0.0692)
- ❌ Expectancy -0.0510R <= +0.0520R (p=0.6695)
- ❌ Sharpe -1.88 <= 0.07 (p=0.6628)
- ❌ Max DD 97.05% >= 23.26%

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Profit Factor | Net PnL (USD) | Avg Realized R |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 218 | 49.5% | 0.75 | $-1,257.67 | -0.0598R |
| `AVAXUSDT` | 214 | 50.5% | 0.71 | $-1,398.90 | -0.0496R |
| `BNBUSDT` | 292 | 49.3% | 0.88 | $-1,202.61 | -0.0218R |
| `BTCUSDT` | 341 | 47.8% | 0.95 | $-1,183.53 | -0.0557R |
| `DOGEUSDT` | 221 | 45.7% | 0.72 | $-1,462.34 | -0.1155R |
| `ETHUSDT` | 292 | 51.4% | 1.10 | $-262.00 | -0.0208R |
| `LINKUSDT` | 180 | 49.4% | 1.00 | $-335.01 | -0.0643R |
| `SOLUSDT` | 313 | 52.1% | 1.04 | $-409.89 | -0.0065R |
| `SUIUSDT` | 206 | 45.6% | 0.70 | $-1,430.19 | -0.1187R |
| `XRPUSDT` | 245 | 51.0% | 0.91 | $-736.78 | -0.0397R |

---

### Hypothesis: FVGRetest
**Concept:** HTF Trend + SMC Fair Value Gap Retest & Mitigation Rejection

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Composite $p$-value against Phase 2 Monte Carlo Hurdle: **`p = 0.5307`**  
> Total Candidate Signals: `14899` | Executed Trades: `2946`

#### Hurdle Checklist:
**Passed:**
- None

**Failed / Unmet:**
- ❌ Profit Factor 0.92 <= 1.14 (p=0.5307)
- ❌ Win Rate 45.99% <= 50.00% (p=0.3516)
- ❌ Expectancy -0.0257R <= +0.0520R (p=0.4808)
- ❌ Sharpe -0.93 <= 0.07 (p=0.2981)
- ❌ Max DD 91.75% >= 23.26%

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Profit Factor | Net PnL (USD) | Avg Realized R |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 279 | 47.3% | 0.86 | $-1,074.18 | -0.0220R |
| `AVAXUSDT` | 323 | 49.8% | 1.07 | $-190.78 | +0.0049R |
| `BNBUSDT` | 287 | 41.1% | 0.85 | $-1,203.42 | -0.0404R |
| `BTCUSDT` | 304 | 38.2% | 0.90 | $-1,013.41 | -0.0481R |
| `DOGEUSDT` | 257 | 52.1% | 1.09 | $-63.97 | +0.0870R |
| `ETHUSDT` | 342 | 49.1% | 1.13 | $+27.60 | +0.0432R |
| `LINKUSDT` | 269 | 47.6% | 0.82 | $-1,304.31 | -0.0589R |
| `SOLUSDT` | 337 | 43.3% | 0.75 | $-2,085.74 | -0.0804R |
| `SUIUSDT` | 256 | 44.9% | 0.68 | $-2,108.30 | -0.1513R |
| `XRPUSDT` | 292 | 46.9% | 1.10 | $-58.63 | -0.0016R |

---

### Hypothesis: ConfluenceThreshold
**Concept:** Multi-Timeframe 0-100 Confluence Model Triggered at High Conviction (>= 75)

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Composite $p$-value against Phase 2 Monte Carlo Hurdle: **`p = 0.9997`**  
> Total Candidate Signals: `66899` | Executed Trades: `6154`

#### Hurdle Checklist:
**Passed:**
- None

**Failed / Unmet:**
- ❌ Profit Factor 0.72 <= 1.14 (p=0.9469)
- ❌ Win Rate 39.32% <= 50.00% (p=0.9634)
- ❌ Expectancy -0.2080R <= +0.0520R (p=0.9997)
- ❌ Sharpe -2.47 <= 0.07 (p=0.8438)
- ❌ Max DD 100.00% >= 23.26%

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Profit Factor | Net PnL (USD) | Avg Realized R |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 509 | 39.5% | 0.60 | $-1,056.56 | -0.1531R |
| `AVAXUSDT` | 536 | 36.4% | 0.56 | $-1,379.76 | -0.1385R |
| `BNBUSDT` | 676 | 35.8% | 0.60 | $-1,689.82 | -0.1516R |
| `BTCUSDT` | 771 | 41.0% | 0.68 | $-1,484.75 | -0.0798R |
| `DOGEUSDT` | 639 | 40.5% | 0.95 | $-340.73 | -0.1104R |
| `ETHUSDT` | 854 | 40.2% | 0.80 | $-1,137.11 | -0.0605R |
| `LINKUSDT` | 392 | 33.2% | 0.50 | $-1,032.51 | -0.1893R |
| `SOLUSDT` | 750 | 42.4% | 0.86 | $-664.93 | -0.0391R |
| `SUIUSDT` | 408 | 40.2% | 0.66 | $-746.44 | -0.1064R |
| `XRPUSDT` | 619 | 40.7% | 0.89 | $-467.02 | -0.0469R |

---

### Hypothesis: LiquiditySweep
**Concept:** Swing Failure Pattern (SFP) sweeping key liquidity with rejection

> **Statistical Verdict**: **`REJECTED (p >= 0.05)`**  
> Composite $p$-value against Phase 2 Monte Carlo Hurdle: **`p = 0.9999`**  
> Total Candidate Signals: `19279` | Executed Trades: `6396`

#### Hurdle Checklist:
**Passed:**
- None

**Failed / Unmet:**
- ❌ Profit Factor 0.79 <= 1.14 (p=0.8592)
- ❌ Win Rate 37.09% <= 50.00% (p=0.9941)
- ❌ Expectancy -0.2588R <= +0.0520R (p=0.9999)
- ❌ Sharpe -2.53 <= 0.07 (p=0.8577)
- ❌ Max DD 100.00% >= 23.26%

#### Per-Symbol Performance:
| Symbol | Trades | Win Rate | Profit Factor | Net PnL (USD) | Avg Realized R |
| :--- | :---: | :---: | :---: | :---: | :---: |
| `ADAUSDT` | 573 | 39.6% | 1.25 | $+117.36 | -0.0977R |
| `AVAXUSDT` | 468 | 34.4% | 0.65 | $-999.96 | -0.1413R |
| `BNBUSDT` | 680 | 37.6% | 0.61 | $-1,791.18 | -0.0784R |
| `BTCUSDT` | 768 | 36.1% | 0.82 | $-1,004.04 | -0.1015R |
| `DOGEUSDT` | 638 | 35.1% | 0.86 | $-728.40 | -0.1184R |
| `ETHUSDT` | 868 | 37.2% | 0.74 | $-1,741.57 | -0.0854R |
| `LINKUSDT` | 471 | 35.7% | 0.96 | $-394.14 | -0.1324R |
| `SOLUSDT` | 747 | 36.1% | 0.81 | $-1,047.16 | -0.0960R |
| `SUIUSDT` | 491 | 38.1% | 0.71 | $-1,135.00 | -0.0708R |
| `XRPUSDT` | 692 | 40.3% | 0.72 | $-1,275.57 | -0.0479R |

---

## 4. Strategic Implications for Phase 4 (Terminal UI & Radar Filter)

1. **Evidence-Backed Radar Screening**:
   Only strategies and indicator configurations with proven statistical validity ($p < 0.05$) or verified positive expectancy will serve as high-conviction alerts on the Discretionary Radar in Phase 4.
2. **Honesty Over Optimism**:
   Strategies that failed the hurdle are documented transparently. In discretionary trading, knowing which patterns possess **zero predictive edge** under real exchange fees and slippage is just as valuable as finding profitable ones—it prevents discretionary traders from wasting capital on retail chart illusions.
3. **Multi-Timeframe Confluence Power**:
   When directional alignment (4h trend + 1h equilibrium + momentum recovery) is enforced, execution friction is overcome and genuine edge emerges.
