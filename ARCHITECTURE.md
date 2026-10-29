# System Architecture & Migration Contract

> **Status:** Research phase: 5 of 6 hypotheses REJECTED. Donchian4hN55 passed the preregistered dev gate narrowly (matched p=0.0195, alpha=0.025) but is NOT validated: holdout untouched; top-10 trades = 82% of total R; mean net R without them = +0.0144R; week-block bootstrap 95% CI [-0.0151, +0.1796]R includes zero; 2026 YTD is negative. Terminal operates strictly in evidence-status mode.

This document defines the authoritative architecture, dependency boundaries, single sources of truth, module ownership, and migration roadmap for the Crypto-Analyzer project.

All future feature development, bug fixes, refactoring, and code retirement must strictly adhere to this architectural contract unless explicitly approved by the project owner.

---

## 1. Executive Mission & Target Architecture

The Crypto-Analyzer platform is an institutional market microstructure and quantitative strategy research system designed to validate trading hypotheses against empirical noise hurdles before deployment. The platform operates in **evidence-status mode**, reporting statistically verified performance without directional bias or unhedged assumptions.

The architecture is organized into **four primary areas**, supported by shared cross-cutting concerns:

```
┌────────────────────────────────────────────────────────────────────────┐
│                               A. DATA                                  │
│  - Historical Archive Ingestion (Binance Vision USD-M Futures)         │
│  - Live Market Telemetry Collection (WS & REST Feeds)                  │
│  - Data Quality, Continuity, Integrity, & Schema Validation            │
│  - Dataset Identity, SHA-256 Provenance, & Holdout Boundaries          │
└──────────────────┬─────────────────────────────────┬───────────────────┘
                   │                                 │
                   │ (Historical & Live Bars)        │ (Historical Candles)
                   ▼                                 ▼
┌──────────────────────────────────────┐  ┌──────────────────────────────┐
│       B. ANALYSIS AND SIGNALS        │  │  C. SIMULATION & VALIDATION  │
│  - Technical Indicators & Overext.   │  │  - Single Exit Authority     │
│  - SMC & Market Structure (FVG/OB)   │  │  - Portfolio Sim Engine      │
│  - Microstructure Dynamics (OI/Liq)  │  │  - Institutional Frictions   │
│  - Standardized Candidate Signals    │─►│  - Monte Carlo Baseline (H0) │
│  - Strictly Zero-Lookahead Timestamps│  │  - Statistical Gate & Holdout│
└──────────────────┬───────────────────┘  └──────────────┬───────────────┘
                   │                                     │
                   │ (Features & Context)                │ (Empirical Results)
                   └──────────────────┬──────────────────┘
                                      ▼
┌────────────────────────────────────────────────────────────────────────┐
│                            D. PRESENTATION                             │
│  - FastAPI REST API & WebSocket Broadcasting Server                    │
│  - React / TypeScript Institutional Terminal Application               │
│  - Empirical Evidence Badges ("NO VALIDATED EDGE" / "VALIDATED")       │
│  - Market Radar, Top Coins Bar, Microstructure Depth, Active Cockpit   │
└────────────────────────────────────────────────────────────────────────┘
                                      ▲
                                      │
┌────────────────────────────────────────────────────────────────────────┐
│                         CROSS-CUTTING CONCERNS                         │
│  Configuration, Logging, Forensic Telemetry, CI/CD, Guardrail Tests    │
└────────────────────────────────────────────────────────────────────────┘
```

### Area A: Data
**Core Responsibilities:**
* Download historical market klines and funding rate archives from Binance Vision.
* Ingest live market data streams (WebSocket klines, bookTicker BBO, aggregate trades, liquidation orders, and REST derivatives metrics).
* Validate timestamps, intervals, continuity, gaps, duplicates, schema compliance, and liquidity anomalies.
* Store and load market datasets using high-efficiency formats (Parquet data lake and SQLite WAL vault).
* Provide clean, unified programmatic interfaces to historical and live data.
* Track data provenance, dataset identity, and enforce out-of-sample holdout cutoff dates (`DEVELOPMENT_END = "2026-04-18"`).

### Area B: Analysis and Signals
**Core Responsibilities:**
* Calculate technical indicators (EMA, ATR, RSI, ADX) and market regimes.
* Detect Smart Money Concepts (Fair Value Gaps, Order Blocks, Breaker Blocks, Swing Points, Market Structure BOS/CHoCH, Fibonacci OTE).
* Evaluate market microstructure (Whale trade order flow, forced liquidation clusters, Open Interest delta matrices, funding rate basis, order book depth imbalance).
* Implement strategy rules and generate standardized candidate trading signals (`CandidateSignal`).
* Guarantee strictly zero lookahead bias: signals triggered on bar $i$ close are explicitly timestamped and priced for execution at bar $i+1$ open.
* Maintain complete mathematical consistency across indicators and feature definitions.

### Area C: Simulation and Validation
**Core Responsibilities:**
* Authoritatively simulate trade order lifecycle and execution.
* Apply portfolio position sizing, leverage caps, concurrency limits, and symbol cooldowns.
* Manage trade exits through the Single Exit Authority (`engine/exits.py`): stop-loss, gap stops, partial scaling (TP1 50%), breakeven arming, final target (TP2), and kinetic stagnancy stops.
* Model institutional execution frictions: taker/maker exchange fees, adverse execution slippage, conservative gap fills, and cumulative 8-hour funding rate drag.
* Maintain chronological portfolio equity curves, drawdown calculations, and stationary risk accounting (`fixed_risk` vs. `compounding`).
* Evaluate strategy performance against the 1,000-pass Monte Carlo empirical null distribution ($H_0$).
* Run statistical validation gates: minimum trade count ($N \ge 100$), net expectancy ($R > 0$), bootstrap 95% CI lower bound ($> 0$), and matched rank $p$-value ($p < 0.05$ or pre-registered $p < 0.025$).
* Execute locked out-of-sample holdout tests with an immutable append-only audit trail (`research/reports/holdout_log.csv`).
* This area is the **sole authority** for historical execution simulation and performance evaluation.

### Area D: Presentation
**Core Responsibilities:**
* Serve the backend REST API and WebSocket streaming server (`server.py`).
* Deliver the React / TypeScript frontend terminal interface (`frontend/`).
* Display live market depth, candlestick charts, whale trades, liquidation events, and microstructure metrics.
* Present strategy research evidence, validation reports, and empirical metrics loaded directly from `research/reports/strategy_results.json`.
* Clearly distinguish validated findings from untested hypotheses via status badges (`NO VALIDATED EDGE`, `UNTESTED`, `VALIDATED (p < 0.05)`).
* Display system health, data freshness, latency, and operational limitations.
* Presentation code must **consume established domain interfaces** and must never reimplement research simulation, execution loops, or portfolio accounting.

### Cross-Cutting Concerns
Configuration management, structured logging, database migrations, forensic telemetry auditing (`auxiliary/forensic_monitor.py`), CI/CD automation, regression testing, and architectural guardrails support all four areas. They must remain lightweight, transparent, and free of unnecessary abstractions or third-party framework overhead.

---

## 2. Dependency Rules & Architectural Boundaries

To preserve architectural integrity and prevent structural decay, all components must adhere to these eight dependency rules:

1. **Data Supplies Inward**: Data components supply clean market data to Analysis and Simulation via documented interfaces (`load_candles`, `MarketDataVault`). Data modules must not depend on Analysis, Simulation, or Presentation.
2. **Analysis Defines Decisions**: Analysis and Signals define market features, regime filters, and candidate trading decisions. Analysis modules may consume Data, but must not depend on Simulation or Presentation.
3. **Simulation Evaluates Decisions**: Simulation and Validation consume market data from Data and candidate signals from Analysis to simulate execution and compute statistical validation metrics.
4. **Presentation Consumes Interfaces**: Presentation modules display data, features, and validated research findings. Presentation code must consume established interfaces rather than reimplementing feature calculation or backtesting logic.
5. **Strict Inward Flow (No Downward Inversions)**: Lower-level domain components (Data, Analysis, Simulation) must never import higher-level application or presentation components (e.g. `indicators.py` or `engine/` must never import `server.py` or `main.py`).
6. **Zero Circular Dependencies**: Modules within the same layer or across layers must never engage in circular import loops.
7. **Single Source of Financial Truth**: No duplicate implementations of the same financial calculation are permitted (e.g., fee modeling, bracket calculation, R-multiple accounting, ATR stop logic). Any alternative implementation must have a documented reason and equivalence tests.
8. **Operational Separation with Shared Contracts**: Historical batch processing (`data/loader.py`, `engine/simulator.py`) and live real-time streaming (`auxiliary/collector.py`, `server.py`) may utilize distinct transport and buffering mechanisms suited to their operational constraints, but they must share common domain contracts, data models, and exit definitions (`engine/exits.py`).

Before creating any new module, verify whether an existing module already owns that responsibility.

---

## 3. Current Module Mapping & Responsibilities

The existing codebase maps to the four primary areas and cross-cutting concerns as follows:

| Area | Module / Path | Primary Responsibilities | Current Architectural Status |
| :--- | :--- | :--- | :--- |
| **A. Data** | `data/downloader.py` | Downloads Binance Vision USD-M futures kline and funding archives; verifies SHA-256 checksums; validates candle continuity, schema, and liquidity; writes monthly Parquet files. | **Verified** |
| **A. Data** | `data/loader.py` | Loads Parquet candle datasets; computes SHA-256 `dataset_hash`; converts data to fast numpy arrays; enforces `DEVELOPMENT_END` holdout cutoff. | **Verified** |
| **A. Data** | `market_feed.py` | Binance USD-M Futures real-time market data transport: combined stream URL, parallel REST kline warmup, HTF polling, and microstructure stream manager. | **Verified** |
| **A. Data** | `data/candles/*.parquet` | Clean, verified Parquet data lake for 10 major pairs (1h and 4h klines). | **Verified** |
| **Auxiliary** | `auxiliary/collector.py` | Asynchronous daemon collecting live WebSocket streams (@aggTrade, !forceOrder, @bookTicker, @kline) and REST metrics into SQLite vault. | **Verified** |
| **Auxiliary** | `auxiliary/vault.py` | High-performance SQLite WAL storage (`MarketDataVault`, `AsyncVaultWriter`) with composite indices for 7 microstructure telemetry tiers. | **Verified** |
| **Auxiliary** | `auxiliary/repair_vault_source.py` | Retroactive schema migration and data provenance tagging utility for SQLite vault. | **Verified** |
| **Auxiliary** | `auxiliary/forensic_monitor.py` | Real-time trade excursion auditor and shadow signal loophole detector; manages `data/forensic_audit.db` and audits tape from `data/market_vault.db`. | **Verified** |
| **B. Analysis** | `indicators.py` | Pure quantitative technical indicators (EMA, ATR, RSI, ADX), Fair Value Gaps, swing detection, market structure (BOS/CHoCH), Order Blocks, Fibonacci OTE, candle normalization. | **Verified** |
| **B. Analysis** | `microstructure.py` | Institutional microstructure analytics: Whale trade filter, liquidation clustering, Open Interest matrix, funding rate basis, order book depth skew & imbalance, and `FuturesMicrostructureManager`. | **Verified** |
| **B. Analysis** | `research/strategies/base.py` | Abstract `BaseStrategy` interface defining zero-lookahead candidate signal contracts using `domain.signals`. | **Verified** |
| **B. Analysis** | `research/strategies/donchian_4h.py` | Donchian channel breakout strategy ($N=20$ and $N=55$) on 4h timeframe using `domain.signals` and `domain.exits`. | **Verified** |
| **B. Analysis** | `research/strategies/trend_pullback.py` | 1h trend pullback strategy with 4h HTF EMA trend filter using `domain.signals`. | **Verified** |
| **B. Analysis** | `research/strategies/fvg_retest.py` | 1h Fair Value Gap retest strategy with HTF directional bias using `domain.signals`. | **Verified** |
| **B. Analysis** | `research/strategies/liquidity_sweep.py` | 1h liquidity sweep strategy with swing high/low sweep confirmation using `domain.signals`. | **Verified** |
| **B. Analysis** | `research/strategies/confluence_threshold.py` | Multi-factor composite confluence threshold strategy using `domain.signals`. | **Verified** |
| **C. Simulation** | `engine/simulator.py` | Authoritative institutional simulation engine (`SimulationEngine`, `simulate_portfolio_signals`): fees, slippage, funding drag, gap fills, portfolio concurrency, cooldowns, ruin guards. | **Verified** (Single simulation authority) |
| **C. Simulation** | `engine/exits.py` | Single Exit Authority (`evaluate_trade_exit`, `calculate_bracket_geometry`, `create_trade_state`): stop loss, gap stops, TP1 50%, BE arming, TP2, kinetic stagnancy stop. | **Verified** (Single exit authority) |
| **C. Simulation** | `research/evaluator.py` | Statistical hurdle evaluator: sample size ($N \ge 100$), net $R > 0$, bootstrap 95% CI $> 0$, matched rank $p < 0.05$ (or $p < 0.025$). | **Verified** |
| **C. Simulation** | `research/random_baseline.py` | Monte Carlo 1,000-pass random entry baseline generator ($H_0$) and matched baseline runner. | **Verified** |
| **C. Simulation** | `research/robustness.py` | Robustness diagnostics: Monte Carlo standard error, 30-seed iid bootstrap, week-block bootstrap, calendar year stability, top-10 trade share, power analysis. | **Verified** |
| **C. Simulation** | `research/holdout_check.py` | Locked out-of-sample evaluation gate; enforces one-pass rule; logs to `research/reports/holdout_log.csv`. | **Verified** |
| **C. Simulation** | `research/universes.py` | Named universe registry (U10 primary evidence, U4 operational, U5 expanded) with strict validation. | **Verified** |
| **C. Simulation** | `research/strategy_runner.py` | Master research orchestrator: executes strategies, evaluates hurdles, outputs reports. | **Verified** |
| **C. Simulation** | `research/preregistration/*` | Pre-registration hypothesis registry (`H1_donchian_4h.md`, `LOG.md`) for locked empirical validation protocols. | **Verified** |
| **C. Simulation** | `research/reports/*` | Empirical research reports and distribution artifacts (`baseline_distribution.json`, `strategy_results.json`, `strategy_validation_report.md`, `random_baseline_hurdle.md`). | **Verified** |
| **D. Presentation** | `server.py` | FastAPI application serving REST endpoints, WebSocket hub `/ws`, static frontend bundle, and empirical evidence status mapping. | **In progress** (Decoupled from `main.py`; coupled to `risk.py`) |
| **D. Presentation** | `frontend/` | React 18 / TypeScript terminal SPA with lightweight-charts, depth ladder, whale tape, liquidation feed, and radar. | **Verified** |
| **D. Presentation** | `main.py` | CLI evidence scanner and multi-coin radar terminal. | **Verified** |
| **Cross-Cutting** | `domain/` | Shared domain data contracts (`domain/signals.py`: `CandidateSignal`, `domain/exits.py`: `ExitConfig`). | **Verified** |
| **Cross-Cutting** | `scripts/*.sh`, `local-cache-proxy.js` | Operational dev proxy, automated service launcher, and daemon management scripts (`start.sh`, `stop.sh`, `dev-local.sh`, `verify_architecture.sh`, `local-cache-proxy.js`). | **Verified** |
| **Cross-Cutting** | `test_*.py` (22 files) | Pytest test harness validating data pipelines, collectors, indicators, exits, guardrails, server, simulator, etc. | **Verified** (255 tests passing) |
| **Cross-Cutting** | `frontend/test/*.test.mjs` | Node.js test suites validating price formatters, UTC clock, cache proxy, and version handshake. | **Verified** (13 tests passing) |

---

## 4. Known Architectural Exceptions & Technical Debt

The following architectural exceptions currently exist in the codebase, along with their root cause and migration remedy:

1. **`server.py` imports from `main.py` [RESOLVED Oct 28, 2026]**:
   * *Resolution*: Extracted connection pooling, multiplexed stream URL building (`get_combined_stream_url`), parallel REST kline warmup (`warmup_symbol`, `warmup_all_traders`), HTF candle polling (`poll_htf_candles`), and `FuturesMicrostructureManager` into clean Area A module `market_feed.py`. `server.py` and `test_server.py` now import directly from `market_feed.py`, and `main.py` delegates to `market_feed.py`. Downward dependency from `server.py` to `main.py` is completely eliminated.

2. **`main.py` imports `legacy.trader.PaperTrader` [RESOLVED Oct 28, 2026]**:
   * *Resolution*: Transitioned CLI scanner `main.py` to evidence-status mode using `CandleFeedBuffer` (from `market_feed.py`). Removed `from legacy.trader import PaperTrader` and `from risk import PortfolioRiskManager`. `main.py` has zero dependencies on legacy modules.

3. **`indicators.py` imports `engine.exits.calculate_bracket_geometry` [RESOLVED Oct 28, 2026]**:
   * *Resolution*: Moved limit order trade bracket staging helper `calculate_optimal_trade_setup` out of pure math module `indicators.py` into Area D `server.py` (where it is exclusively consumed for terminal chart bracket previews). `indicators.py` has zero imports from `engine/` or Area C.

4. **Duplicate Risk & Portfolio State (`risk.py` vs. `engine/simulator.py`) [RESOLVED Oct 29, 2026]**:
   * *Resolution*: Decoupled presentation code from `risk.py` via `DEFAULT_PORTFOLIO_CONFIG`. Authoritative portfolio accounting, sizing limits, and drawdown math are maintained exclusively in `engine/simulator.py:SimulationEngine`. Legacy `risk.py` has been completely deleted. Exception is resolved.

5. **Historical Funding Rate Parquet Integration [RESOLVED Oct 28, 2026]**:
   * *Resolution*: Added dynamic funding rate loader `load_funding_rates()` in `data/loader.py` and dynamic funding rate integration in `engine/simulator.py:SimulationEngine`. Supports exact historical funding rates per 8h period for longs (drag) and shorts (rebate/drag) with automatic fallback to conservative constant when rates are unavailable. Tested in `test_simulator.py`.

6. **`auxiliary/collector.py` imports from `microstructure.py` (Area B) [RESOLVED Oct 28, 2026]**:
   * *Resolution*: Extracted exchange symbol formatting (`SymbolNormalizer`) and whale tier thresholds (`WHALE_TIER_THRESHOLDS`, `get_whale_thresholds`) into Area A module `market_feed.py`. `auxiliary/collector.py` now imports strictly from `market_feed.py`. `microstructure.py` consumes and re-exports from `market_feed.py`. Area A has zero imports from Area B.

7. **`research/strategies/*` (Area B) imports from `engine/` (Area C) [RESOLVED Oct 28, 2026]**:
   * *Resolution*: Extracted `CandidateSignal` and `ExitConfig` into shared cross-cutting contracts package `domain/` (`domain/signals.py`, `domain/exits.py`). All strategies in `research/strategies/` now import from `domain/`. `engine.simulator` and `engine.exits` re-export from `domain/` for backward compatibility. Area B has zero imports from Area C.

8. **`server.py` (Area D) coupling to `risk.py` / `signals_audit.db` [RESOLVED Oct 28, 2026]**:
   * *Resolution*: Severed `server.py`'s dependency on `risk.py` and `data/signals_audit.db`. Replaced `PortfolioRiskManager` with lightweight presentation config `DEFAULT_PORTFOLIO_CONFIG`. `/api/portfolio/summary` directly returns static evidence-only envelope with zero database connections. `server.py` has zero imports from `risk.py`.

9. **`auxiliary/forensic_monitor.py` coupling to `data/signals_audit.db` [RESOLVED Oct 28, 2026]**:
   * *Resolution*: Re-pointed telemetry and tape audits to authoritative vault `data/market_vault.db` and live REST endpoints. Safely handles absent or empty paper-trade tables.

---

## 5. Legacy Components Retired & Removed

All obsolete and unvalidated legacy components and persistent artifacts have been retired and deleted pursuant to the architectural mandate:

| Component / Artifact | Type / Location | Size / Detail | Evidence & Assessment | Status |
| :--- | :--- | :--- | :--- | :--- |
| `legacy/backtest.py` | Code (`legacy/`) | 1,763 lines | Unvalidated legacy backtester with custom exit logic and direct REST calls. Competed with authoritative `engine/simulator.py`. | **Removed** (Deleted Oct 29, 2026) |
| `legacy/trader.py` | Code (`legacy/`) | 1,927 lines | Legacy paper trading state machine with custom trailing exits. Replaced by `CandleFeedBuffer` and `engine/exits.py`. | **Removed** (Deleted Oct 29, 2026) |
| `legacy/validator/*` | Code (`legacy/validator/`) | 2,462 lines (8 files) | Obsolete regex parsing framework and SMTP notifier. Replaced by empirical Monte Carlo research pipeline. | **Removed** (Deleted Oct 29, 2026) |
| `risk.py` | Code (Root) | 572 lines | Duplicate portfolio risk manager. Replaced by `engine/simulator.py` and `DEFAULT_PORTFOLIO_CONFIG`. | **Removed** (Deleted Oct 29, 2026) |
| `data/backtest_runs.db` | Persistent DB | 0 tables (empty SQLite file) | Leftover database file created by legacy backtester runs. Contains 0 tables. | **Removed** (Deleted Oct 28, 2026) |
| `legacy/data/backtest_runs.db` | Persistent DB | 0 tables (empty SQLite file) | Leftover database file in legacy tree. Contains 0 tables. | **Removed** (Deleted Oct 28, 2026) |
| `'e:\Crypto Analyzer\data\backtest_runs.db'` | Persistent DB | 0 tables (empty SQLite file, 4,096 B) | Artifact created on a Windows development host with an unescaped path string. Contains 0 tables. | **Removed** (Deleted Oct 28, 2026) |
| `legacy/reports/*.md` | Reports (`legacy/reports/`) | 7 markdown reports | Historical legacy reports generated on Oct 17, 2026. Superseded by research reports. | **Removed** (Deleted Oct 29, 2026) |
| `legacy/forensic_report.md` | Report (`legacy/`) | 1 markdown file | Legacy forensic report. Superseded by research reports. | **Removed** (Deleted Oct 29, 2026) |
| `test_backtest.py` | Test file | 64 lines | Unit tests for deleted `legacy/backtest.py`. | **Removed** (Deleted Oct 29, 2026) |
| `test_trader.py` | Test file | 2,442 lines | Unit tests for deleted `legacy/trader.py`. | **Removed** (Deleted Oct 29, 2026) |
| `test_risk.py` | Test file | 460 lines | Unit tests for deleted `risk.py`. | **Removed** (Deleted Oct 29, 2026) |
| `test_validator.py` | Test file | 164 lines | Unit tests for deleted `legacy/validator/`. | **Removed** (Deleted Oct 29, 2026) |

---

## 6. Single Sources of Truth

Every critical domain rule in the system has exactly one authoritative implementation:

| Domain Rule / Component | Authoritative File | Exact Function(s) / Classes | Description |
| :--- | :--- | :--- | :--- |
| **Trade Exit Authority** | `engine/exits.py` | `evaluate_trade_exit()`<br>`calculate_bracket_geometry()`<br>`create_trade_state()` | Evaluates stop loss, gap stops, TP1 partial scale (50%), breakeven arming, TP2 target, and kinetic stagnancy stops under strict precedence order. |
| **Simulation Loop & Execution Engine** | `engine/simulator.py` | `SimulationEngine.process_bar()`<br>`SimulationEngine.open_position()`<br>`simulate_portfolio_signals()`<br>`simulate_single_trade_lifecycle()` | Orchestrates chronological bar-by-bar trade processing, position sizing, concurrency limits, 8h funding rate drag, taker/maker fee deduction, and slippage. |
| **Candidate Signal Contract** | `engine/simulator.py` | `CandidateSignal` | Standardized data contract for trade signals generated on bar $i$ close with execution at bar $i+1$ open. (Target: extract to shared domain contracts). |
| **Zero-Volume & Gap Handling** | `engine/simulator.py` | `SimulationEngine.can_open_position()`<br>`SimulationEngine.process_bar()` | Skips signals whose entry bar has `volume == 0.0`. Terminates open trades with `exit_reason="SESSION_BREAK"` if time delta between bars exceeds `bar_interval_ms`. |
| **Ruin Logic** | `engine/simulator.py` | `SimulationEngine.can_open_position()`<br>`SimulationEngine.compute_summary()` | In `compounding` mode, halts new entries when equity $\le 5\%$ of initial capital. In `fixed_risk` mode, ruin is report-only and never truncates backtest samples. |
| **Performance Metrics** | `engine/simulator.py` | `SimulationEngine.compute_summary()` | Computes net profit factor `sum(net > 0) / abs(sum(net <= 0))`, gross profit factor, net expectancy R (`mean(net_r)`), gross expectancy R, cumulative net R, and peak-to-trough `max_drawdown_r`. |
| **Empirical Null Hurdles** | `research/reports/baseline_distribution.json` | `BaselineHurdles.load()` (`research/evaluator.py`) | Provides empirical 95th percentile hurdles and null distributions generated by `research/random_baseline.py`. Enforces strict dataset hash, sizing mode, and friction validation. |
| **Universe Whitelists** | `research/universes.py` | `UNIVERSES`<br>`get_universe_symbols()`<br>`validate_universe_data()` | Authoritative registry for named universes (U10 primary evidence universe, U4 operational subset, U5 expanded). |
| **Evidence Shown in Terminal** | `research/reports/strategy_results.json` | `server.py`: `get_strategy_results()`<br>`server.py`: `get_historical_baseline()` | Reads empirical metrics from disk with mtime caching. Maps frontend patterns to strategy results via `PATTERN_STRATEGY_MAP`. |
| **Dataset Boundary & Holdout Lock** | `data/loader.py` | `load_candles()`<br>`compute_dataset_hash()`<br>`get_current_dataset_hash()` | Enforces `DEVELOPMENT_END = "2026-04-18"` boundary unless `allow_holdout=True` is explicitly passed. Computes deterministic dataset SHA-256 hash. |
| **Holdout Audit Gate** | `research/holdout_check.py` | `run_holdout_check()`<br>`is_holdout_run_recorded()` | One-pass gate for out-of-sample data. Refuses execution if strategy source git blob hash is already logged in `research/reports/holdout_log.csv`. |

---

## 7. Glossary of Core Concepts

### Net R vs. Gross R
* **Net R (`net_r`)**: Realized trade outcome expressed in multiples of the initial dollar risk budget ($R = \frac{\text{net\_pnl\_usd}}{\text{initial\_risk\_usd}}$).
  * In the simulation engine (`engine/simulator.py`), adverse execution slippage is incorporated directly into fill prices (shown for Longs; adverse slippage is inverted for Shorts):
    $$P_{\text{entry\_fill}} = P_{\text{entry}} \times (1 + \text{slip}), \quad P_{\text{exit\_fill}} = P_{\text{exit}} \times (1 - \text{slip})$$
    $$\text{gross\_pnl\_usd} = \text{position\_size} \times (P_{\text{exit\_fill}} - P_{\text{entry\_fill}})$$
    $$\text{net\_pnl\_usd} = \text{gross\_pnl\_usd} - \text{fees\_usd} - \text{funding\_cost\_usd}$$
  * Net R measures real institutional payoff after accounting for all market friction (fees, slippage, and funding drag).
* **Gross R (`gross_expectancy_r` / `realized_r`)**: Theoretical price-movement multiple achieved by the nominal exit price relative to stop distance ($\frac{P_{\text{exit}} - P_{\text{entry}}}{\text{risk\_dist}}$ for Longs), excluding transaction fees, slippage, and funding drag.

### Fixed Risk vs. Compounding
* **`fixed_risk` (Default Research Mode)**: Each trade risks a fixed percentage of initial equity (e.g. 1% of $\$10,000 = \$100$). Because risk does not contract after drawdowns or expand during winning streaks, trade metrics are stationary. Ruin threshold is **report-only** and does not halt testing, preventing sample truncation bias from losing streaks.
* **`compounding`**: Position size scales with floating account equity ($\text{equity\_usd} \times \text{risk\_pct}$). If account equity drops to or below the ruin threshold ($\le 5\%$ initial equity), the ruin guard halts all further entries.

### Matched Baseline
* An empirical Monte Carlo null distribution run per strategy ($N=1,000$ passes for exploratory screening; $N=10,000$ passes for pre-registered Gate evaluations). Rather than using arbitrary stops or fixed holding periods, each pass samples random entry timestamps while matching the strategy's **exact trade count**, **symbol distribution**, and **ATR stop-loss distance distribution (in %)** under identical exit rules and friction.
* The strategy's empirical rank $p$-value is calculated directly as:
  $$p = \frac{\text{Count}(\text{Null } \text{Net } R \ge \text{Strategy } \text{Net } R)}{N}$$

### Development vs. Holdout Data
* **Development Data**: Historical candles from inception up to `DEVELOPMENT_END = "2026-04-18"`. Used for all exploratory analysis, strategy design, indicator optimization, and baseline generation.
* **Holdout Data**: Historical candles recorded strictly after `2026-04-18`. Inaccessible to standard backtesting routines (`load_candles` truncates by default). Can only be evaluated through `python -m research.holdout_check <StrategyName>`, which enforces an append-only audit trail and blocks repeated evaluations on the same code.

---

## 8. Forbidden Patterns

The following anti-patterns compromise research integrity and are strictly forbidden:

1. **Second Simulation Loop or Exit Implementation**:
   All backtesting, random baselines, matched baselines, holdout checks, and paper execution must call `engine/simulator.py` and `engine/exits.py`. Never create parallel loops, custom fee calculations, or manual bracket evaluations. *(Note: Unvalidated legacy loops in `legacy/` are quarantined exceptions slated for staged retirement in Phase 3).*
2. **Hard-Coded Statistics or Evidence in Server/Reports**:
   Never fabricate or hard-code sample win rates, profit factors, trade counts, or numerical evidence metrics in `server.py` or report markdown generators. All numbers must be loaded dynamically from `strategy_results.json` or `baseline_distribution.json`. *(Static fallback status strings such as `"UNTESTED"` or `"NO VALIDATED EDGE"` are permitted when no evidence is loaded).*
3. **Silent Fallback Values for Missing Data**:
   Never use silent fallbacks that swallow missing data (e.g. `else 0.0`, `or 1.0`, `except: pass`). If a required metric, baseline distribution, or dataset file is absent or mismatched, **raise a clear error** immediately.
4. **Altering Parameters or Hurdles to Force a Pass**:
   Never adjust strategy parameters, exit configs, statistical thresholds ($p < 0.05$, bootstrap CI $> 0$), or hurdle values to make a failing hypothesis pass. A negative finding is a valid scientific result.
5. **Evaluating Strategies on Holdout Data Without Holdout Runner**:
   Never pass `allow_holdout=True` into exploratory research scripts, baseline runners, or unit tests. Holdout data may only be accessed via `research/holdout_check.py`.
6. **Lookahead Bias in Signal Generation**:
   Signals generated at candle $i$ must not consume data from candle $i+1$ or beyond. Entry must be modeled at candle $i+1$ open. Higher timeframe filters must only access completed HTF candles available at the time of the lower timeframe decision.
7. **Binance US Domains in Non-Legacy Code**:
   Never include `binance.us` endpoints in non-legacy production code or tests (enforced by `test_guardrail_no_binance_us_hosts`).
8. **Binance Spot Endpoints in Non-Legacy Futures Paths**:
   Never include `stream.binance.com` or `api.binance.com/api/v3` in non-legacy production code; all live market data must route to Binance USD-M Futures endpoints (enforced by `test_guardrail_no_spot_binance_endpoints`).

---

## 9. Architectural Rules for Future Development

All future code contributions and modifications must adhere to these development rules:

1. **Check Responsibility Ownership First**: Before adding a new module or function, search the codebase to determine whether an existing module in Area A, B, C, or D already owns that responsibility.
2. **Preserve Boundary Direction**: Data $\rightarrow$ Analysis $\rightarrow$ Simulation $\rightarrow$ Presentation. Never import across levels in reverse.
3. **Keep Presentation Code Thin**: Presentation code (`server.py`, `frontend/`) must strictly handle serialization, routing, display, and evidence reporting. It must never perform signal calculation, backtest simulation, or risk sizing.
4. **Enforce Unit and Architectural Tests**: Every financial calculation must have corresponding unit tests. Critical boundaries must be protected by AST-based guardrail tests in `test_guardrails.py`.
5. **Maintain Documentation Integrity**: Any change to module structure, data flows, or dependency contracts must be accompanied by an immediate update to this `ARCHITECTURE.md` file.

---

## 10. Prioritized Migration Checklist

This checklist tracks every significant component and migration step toward the fixed target architecture.

Status Legend:
- `Not assessed`: Awaiting formal inspection and verification.
- `In progress`: Baseline established, design mapped, active migration work underway.
- `Verified`: Formally inspected, conforms to target architecture and passing tests.
- `Deprecated`: Obsolete or duplicate component slated for retirement.
- `Removed`: Fully removed after verifying all dependencies and obtaining approval.

### Phase 1 & 2: Baseline and Architecture Contract
- [x] **Establish Repository Baseline**: Verify working tree, git commit (`e2b1662`), active daemon processes, and test execution (340 pytest tests, 13 frontend tests, build clean). `[Verified]`
- [x] **Map Existing Code to 4 Target Areas**: Classify every module into Data, Analysis and Signals, Simulation and Validation, Presentation, or Cross-cutting. `[Verified]`
- [x] **Establish ARCHITECTURE.md Contract**: Publish target architecture, dependency rules, single sources of truth, module mappings, and technical debt inventory. `[Verified]`
- [x] **Establish Prioritized Migration Checklist**: Document all items with explicit progress states. `[Verified]`

### Phase 3: Complexity Removal & Boundary Clean-Up
- [x] **Decouple `server.py` from `main.py`**: Extracted shared Binance USD-M WebSocket connection logic and warmup utilities into Area A module `market_feed.py`. `[Verified]`
- [x] **Decouple `auxiliary/collector.py` from `microstructure.py`**: Extracted exchange symbol normalization (`SymbolNormalizer`) and whale threshold definitions into Area A module `market_feed.py`. `[Verified]`
- [x] **Decouple Area B strategies from `engine/`**: Extracted `CandidateSignal` and `ExitConfig` into shared cross-cutting domain contracts (`domain/signals.py`, `domain/exits.py`). `[Verified]`
- [x] **Decouple `indicators.py` from `engine.exits`**: Moved trade bracket staging (`calculate_optimal_trade_setup`) out of `indicators.py` into Area D `server.py`. `[Verified]`
- [x] **Decouple `server.py` from `risk.py`**: Replaced `PortfolioRiskManager` in `server.py` with lightweight presentation config `DEFAULT_PORTFOLIO_CONFIG`. `[Verified]`
- [x] **Decouple `main.py` from `legacy/trader.py`**: Transitioned `main.py` CLI scanner to consume live feeds and evidence badges via `CandleFeedBuffer`. `[Verified]`
- [x] **Retire Empty Legacy Databases**: Formally inspected and removed orphaned empty databases (`data/backtest_runs.db`, `legacy/data/backtest_runs.db`, `'e:\Crypto Analyzer\data\backtest_runs.db'`). `[Removed]`
- [x] **Investigate and Retire `legacy/validator/`**: Verified zero production or research dependencies; completely removed obsolete framework. `[Removed]`
- [x] **Investigate and Retire `legacy/backtest.py`**: Verified all research uses authoritative `engine/simulator.py`; completely removed legacy backtester. `[Removed]`
- [x] **Investigate and Retire `legacy/trader.py`**: Decoupled operational modules; completely removed legacy trader. `[Removed]`
- [x] **Consolidate Risk Management**: Decoupled presentation code from `risk.py`; authoritative sizing maintained by `engine/simulator.py`; completely removed `risk.py`. `[Removed]`
- [x] **Audit `auxiliary/forensic_monitor.py` Telemetry Coupling**: Re-pointed telemetry audits to authoritative vault `data/market_vault.db` and live REST endpoints. `[Verified]`
- [x] **Align `ROADMAP.md`**: Updated outdated layered architecture description in `ROADMAP.md` to match `ARCHITECTURE.md`. `[Verified]`

### Phase 4: Strengthening Correctness
- [x] **Zero-Lookahead Verification**: Verified temporal causality across all strategies via dataset truncation invariance and candle $i+1$ open timestamp tests in `test_strategies.py` and `test_donchian_4h.py`. `[Verified]`
- [x] **Candle-Completion & Entry Timing**: Formally enforced entry execution strictly at candle $i+1$ open in `engine/simulator.py` and `research/strategies/`. `[Verified]`
- [x] **Friction & Funding Dynamic Integration**: Added `load_funding_rates` in `data/loader.py` and dynamic per-period funding calculation in `engine/simulator.py:SimulationEngine` with seamless constant fallback. `[Verified]`
- [x] **Unimplemented Radar Strategies**: Codified patterns mapped in `PATTERN_STRATEGY_MAP`; unvalidated hypotheses (e.g. RSI Divergence) explicitly display "NO VALIDATED EDGE" badge. `[Verified]`

### Phase 5: Architectural Enforcement
- [x] **Add Dependency Direction Guardrails**: Added AST-based `test_guardrail_architectural_boundaries` in `test_guardrails.py` enforcing strict unidirectional import rules across all 4 areas (Data, Analysis, Simulation, Presentation). `[Verified]`
- [x] **Add Clean Architecture CI Step**: Automated architectural boundary verification in `.github/workflows/ci.yml`, pre-commit hooks (`.pre-commit-config.yaml`), and standalone verification script (`scripts/verify_architecture.sh`). `[Verified]`

---

## 11. How to Run the Pipeline (Linux / Bash Commands)

### Step 0: Run Clean Architecture & Boundary Verification
Run the unified verification script to ensure all AST boundary guardrails, static checks, unit tests, and frontend build succeed:
```bash
./scripts/verify_architecture.sh
```

### Step 1: Download Clean Parquet Data Lake
Download 1h and 4h Binance USD-M futures historical klines with SHA-256 verification (comma-separated arguments):
```bash
python -m data.downloader --symbols BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,ADAUSDT,AVAXUSDT,LINKUSDT,SUIUSDT --timeframes 1h,4h
```
*(Or run `python -m data.downloader` to use the default 10-symbol universe and 1h/4h timeframes).*

### Step 2: Generate Monte Carlo Baseline Distribution
Run the 1,000-pass random baseline to establish $H_0$ empirical hurdles:
```bash
python -m research.random_baseline --passes 1000
```
*Outputs: `research/reports/baseline_distribution.json` and `research/reports/random_baseline_hurdle.md`*

### Step 3: Run Strategy Evidence Validation Pipeline
Run the codified trading hypotheses against the empirical baseline:
```bash
python -m research.strategy_runner --passes 1000
```
*Outputs: `research/reports/strategy_results.json` and `research/reports/strategy_validation_report.md`*

### Step 4: Run Complete Test Suite
Verify that all unit tests, zero-volume guards, session break checks, and hash synchronization pass:
```bash
python -m pytest -q
```

### Step 5: Run Frontend Tests and Build
```bash
cd frontend && npm test && npm run build && cd ..
```

### Step 6: Launch Live Terminal Server
Start the FastAPI server serving the market terminal and evidence-backed radar:
```bash
uvicorn server:app --host 127.0.0.1 --port 8008
```

### Step 7 (Locked Audit): Evaluate Single Strategy on Holdout Data
When a strategy is ready for final out-of-sample verification:
```bash
python -m research.holdout_check TrendPullback
```
*Logs audit record to `research/reports/holdout_log.csv` and blocks reruns on unchanged source code.*
