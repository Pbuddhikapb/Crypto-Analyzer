# Binance Cryptocurrency Analysis Tool - Master Architectural Blueprint

> **Status:** Research phase: no strategy has passed validation. Terminal displays evidence status only.  
> **Architecture Pattern:** Institutional Market Microstructure & SMC Execution Engine

---

## 1. Executive Summary & Mission

The goal of this project is to build an **institutional-grade, real-time cryptocurrency analysis terminal** for Binance that bridges standard retail technical analysis with institutional market microstructure.

### Core Philosophy: "High Alpha, Zero Bloat"
* **Zero External Databases**: No Redis, PostgreSQL, or TimescaleDB daemons. High-frequency state lives in Python memory (`collections.deque`); historical bars are queried on demand via Binance REST.
* **Zero Audio Asset Files**: Whale trade sounds and alerts utilize the browser's native **Web Audio API** (`AudioContext` oscillators).
* **Zero API Key Requirements**: Operates 100% on public Binance market data endpoints (zero risk of credential exposure).
* **Sub-Millisecond Math**: All indicator, SMC, and microstructure math is calculated in pure Python/NumPy ($<1\text{ms}$).

---

## 2. Market Scope & Supported Assets

* **Dual-Market Toggle**: Instant header toggle between **Binance Spot** and **Binance USD-M Futures**.
* **Curated 10 Major Coins**:
  1. `BTC` (Bitcoin - `BTCUSDT`)
  2. `ETH` (Ethereum - `ETHUSDT`)
  3. `SOL` (Solana - `SOLUSDT`)
  4. `BNB` (BNB - `BNBUSDT`)
  5. `XRP` (XRP - `XRPUSDT`)
  6. `DOGE` (Dogecoin - `DOGEUSDT`)
  7. `ADA` (Cardano - `ADAUSDT`)
  8. `AVAX` (Avalanche - `AVAXUSDT`)
  9. `LINK` (Chainlink - `LINKUSDT`)
  10. `SUI` (Sui - `SUIUSDT`)
* **Timeframes**: Default **1-Hour (1h)** with 1-click switchers (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`) and background multi-timeframe trend confluence scoring.

---

## 3. Strict 4-Area Target Architecture

To prevent architectural decay, the system is strictly decoupled into 4 primary functional areas governed by authoritative contracts in `ARCHITECTURE.md`. **Dependencies flow strictly in one direction (A -> B -> C -> D).**

```
====================================================================================
CRYPTO-ANALYZER FIXED TARGET ARCHITECTURE
====================================================================================

[ AREA D: Presentation ]       (FastAPI server.py, CLI main.py, React frontend/)
        │                      * Consumes established domain contracts; 0 raw math *
        ▼
[ AREA C: Simulation & Valid ] (engine/simulator.py, engine/exits.py, research/)
        │                      * Single authority for exits, fees, slippage, validation *
        ▼
[ AREA B: Analysis & Signals ] (indicators.py, microstructure.py, research/strategies/)
        │                      * SMC, EMAs, order flow, CandidateSignal zero-lookahead *
        ▼
[ AREA A: Data & Ingestion ]   (market_feed.py, collector.py, vault.py, data/loader.py)
                               * Live WS/REST feeds, Parquet storage, buffer management *

[ CROSS-CUTTING: Domain ]      (domain/signals.py, domain/exits.py)
                               * Canonical contracts shared across all 4 areas *
====================================================================================
FRONTEND ARCHITECTURE (React / Vite / TypeScript / Tailwind)
====================================================================================

[ LAYER F0: Contract Types ]   (src/types/market.ts mirroring backend domain contracts)
        │
        ▼
[ LAYER F1: State & Transport ](src/state/api.ts, WebSocket hooks, message dispatcher)
        │
        ▼
[ LAYER F2: Presentation Views ](TradingView Chart, Whale Tape, Depth Ladder, Cockpit)
                               * Never touches raw WebSockets; consumes F1 state *
====================================================================================
```

### Area Breakdown:
* **Area A: Data (`market_feed.py`, `collector.py`, `vault.py`, `data/`)**: Live Binance Futures WS streams, REST warmup, rolling candle buffers (`CandleFeedBuffer`), historical Parquet storage.
* **Area B: Analysis & Signals (`indicators.py`, `microstructure.py`, `research/strategies/`)**: Pure TA math (EMAs, RSI, SMC Fair Value Gaps, Order Blocks), derivatives tape (Order Book depth, whale trades, liquidation cascades, OI regimes), and candidate signal generators emitting standard `CandidateSignal` objects.
* **Area C: Simulation & Validation (`engine/simulator.py`, `engine/exits.py`, `research/`)**: Single Exit Authority (`calculate_bracket_geometry`), realistic institutional frictions (slippage, maker/taker fees, funding costs), Monte Carlo permutation baselines ($H_0$), and holdout verification.
* **Area D: Presentation (`server.py`, `main.py`, `frontend/`)**: FastAPI REST API, WebSocket pub/sub broadcaster, CLI radar monitor, and React terminal SPA. Always presents evidence status badges clearly distinguishing validated findings from untested hypotheses.

---

## 4. Quantitative & Microstructure Analytics Suite

### 4.1 Smart Money Concepts (SMC)
* **Fair Value Gaps (FVG)**:
  - **Bullish FVG (Demand)**: Detected when $\text{Low}(\text{Candle 3}) > \text{High}(\text{Candle 1})$.
  - **Bearish FVG (Supply)**: Detected when $\text{High}(\text{Candle 3}) < \text{Low}(\text{Candle 1})$.
  - Tracks **Consequent Encroachment (50% midpoint)** and mitigation lifecycle (*Unmitigated / Partially Mitigated / Fully Filled*).
* **Institutional Order Blocks (OB)**:
  - Origin candle before displacement breaks structure; tracks the **50% Mean Threshold** as key institutional retest zone.

### 4.2 Technical Indicators & Divergence Engine
* **EMA Ribbon**: 9, 21, 50, and 200 EMAs. Evaluates trend stack (*Bull Stack, Bear Stack, Mixed*) and dynamic pullback corridors.
* **Wilder RSI (14)**: 14-period smoothed momentum oscillator with Overbought ($>70$) and Oversold ($<30$) bands.
* **Fractal Divergence Scanner**:
  - **Regular Bullish Divergence**: Price prints Lower Low while RSI prints Higher Low $\rightarrow$ Reversal upward signal.
  - **Regular Bearish Divergence**: Price prints Higher High while RSI prints Lower High $\rightarrow$ Reversal downward signal.

### 4.3 Derivatives & Microstructure (Coinglass & Aggr.trade Level)
* **Open Interest (OI) 4-Quadrant Diagnostic Matrix**:
  - **Price $\uparrow$ + OI $\uparrow$**: *Long Build-Up* (Healthy aggressive bull trend continuation).
  - **Price $\uparrow$ + OI $\downarrow$**: *Short Squeeze* (Fragile rally driven by short covering, vulnerable to dump).
  - **Price $\downarrow$ + OI $\uparrow$**: *Short Build-Up* (Aggressive institutional short accumulation).
  - **Price $\downarrow$ + OI $\downarrow$**: *Long Liquidation / Flush* (Forced capitulation; prime bottoming wick).
* **Liquidation Cascade Radar**:
  - Ingests global `!forceOrder@arr` feed. Flags an alert when 60s liquidation volume exceeds $\$1\text{M}$.
* **Real-Time Whale Trade Tape ($50,000+ Threshold)**:
  - Filters `aggTrade` stream with tiered notional badges ($50k-$100k Dolphin, $100k-$500k Shark, >$500k Whale).
  - Web Audio synthesis tone (chime for market buys, deep bass tone for market sells; muteable).
* **Order Book Imbalance (OBI)**:
  - Top-20 depth imbalance ratio measuring immediate bid wall vs. ask wall pressure.
* **Unified Confluence Score (0 to 100)**:
  - Synthesizes SMC (30%), Order Flow (25%), Derivatives (25%), and TA (20%) into a unified trade readiness score.

---

## 5. The 4 Engineering Safeguards

| Risk | Architectural Safeguard |
| :--- | :--- |
| **Regional Geo-blocking** *(Binance Futures HTTP 451/403)* | **Network Prober & Fallback**: On boot, probes Spot and Futures. If Futures is restricted by local ISP/region, it flags gracefully, displays an informative UI badge, and keeps Spot 100% operational with zero backend crashes. |
| **In-Memory Ephemeral State** *(Blank tape on restart)* | **Zero-DB REST Pre-Seeding**: Queries the last 200 public trades and recent liquidations via REST on boot. Tape is pre-populated in $<300\text{ms}$ before the WebSocket takes over. |
| **Binance 24h WebSocket Teardown** | **23h Socket Rotation & Backoff**: Proactively rotates sockets at hour 23; implements 1s/2s/5s exponential backoff with a live UI status badge (🟢 *Connected*, 🟡 *Reconnecting*, 🔴 *Offline*). |
| **Visual Clutter / Cognitive Overload** | **1-Click De-Clutter Toolbar**: Dedicated pills on the chart (`[EMAs]`, `[FVGs]`, `[OBs]`, `[RSI]`) plus a 1-click collapse button for the right panel to expand the chart to full width. |

---

## 6. Pro-Trader 3-Column Terminal Layout

```
+----------------------------------------------------------------------------------------------------+
|  TOP BAR: [SPOT / FUTURES] | [BTC] [ETH] [SOL] [BNB] [XRP] [DOGE] [ADA] [AVAX] [LINK] [SUI] | 🔊 [MUTE] |
+----------------------------------------------------------------------+-----------------------------+
|  CHART TOOLBAR: [1m] [5m] [15m] [1h*] [4h] [1D] | [EMAs] [FVGs] [OBs] |  ORDER BOOK & IMBALANCE     |
+----------------------------------------------------------------------+  Top-20 Bids (Green)        |
|                                                                      |  Spread / Mid-price         |
|  TRADINGVIEW LIGHTWEIGHT CANDLESTICK CHART                           |  Top-20 Asks (Red)          |
|  • Smooth 60 FPS HTML5 Canvas                                        |  Imbalance Ratio Gauge      |
|  • Dynamic EMA 9, 21, 50, 200 Ribbon                                 +-----------------------------+
|  • Active Unmitigated FVG Boxes (Green Demand / Red Supply)          |  FILTERED WHALE TAPE ($50k+)|
|  • Institutional Order Block Boxes (Cyan / Purple)                   |  Live trades with badges    |
|                                                                      +-----------------------------+
|  RSI OSCILLATOR SUB-PANE (Wilder 14-period + Divergence Flags)       |  LIQUIDATION RADAR          |
|                                                                      |  Live liquidations & Alerts |
+----------------------------------------------------------------------+-----------------------------+
|  BOTTOM BAR: [0-100 CONFLUENCE SCORE] | ACTIVE DIVERGENCES | OI REGIME TAG | FUNDING RATE & APR    |
+----------------------------------------------------------------------------------------------------+
```

---

## 7. Directory Tree Structure

```
Crypto-Analyzer/
├── collector.py           # Multi-market real-time and historical kline collector
├── backend/
│   ├── pyproject.toml             # Managed by uv (Python 3.12)
│   ├── run.py                     # Entry point (runs uvicorn on port 8000)
│   ├── tests/
│   │   ├── unit/                  # Pure math tests (zero network)
│   │   └── integration/           # Binance adapter tests
│   └── app/
│       ├── __init__.py
│       ├── config.py              # Symbol lists, endpoints, timeframes
│       ├── domain/                # Layer 0: Contracts & Models
│       ├── adapters/              # Layer 1: Binance REST & WS Ingestion
│       ├── engine/                # Layer 2: Pure Quant & SMC Calculation Engine
│       └── delivery/              # Layer 3: FastAPI REST & WebSocket Hub
└── frontend/
    ├── package.json               # React, Vite, TypeScript, Tailwind, lightweight-charts
    ├── vite.config.ts
    ├── tailwind.config.js
    ├── index.html
    └── src/
        ├── App.tsx                # Pro-Trader 3-Column Layout
        ├── types/market.ts        # Layer F0: TypeScript mirror of Layer 0 contracts
        ├── state/                 # Layer F1: Live WebSocket and Audio hooks
        └── components/            # Layer F2: Visual UI Components
            ├── Navbar.tsx
            ├── TopCoinsBar.tsx
            ├── ChartToolbar.tsx
            ├── TradingChart.tsx
            ├── WhaleTape.tsx
            ├── LiquidationCard.tsx
            ├── ConfluencePanel.tsx
            └── OrderBookDepth.tsx
```

---

## 8. Verification & Quality Gates

1. **Unit Testing (Layer 2 Pure Math)**:
   - `test_indicators.py`: Validates EMA calculations and RSI 5-bar fractal divergence detection.
   - `test_smc.py`: Validates 3-candle FVG identification and Order Block mitigation logic.
   - `test_confluence.py`: Validates 0–100 score boundaries and signal aggregation.
2. **Integration Testing (Layer 1 Adapters)**:
   - `test_binance.py`: Validates live REST parsing, pre-seeding logic, and geo-probing fallbacks.
3. **Frontend Build & Types**:
   - `npm run build`: Confirms zero TypeScript discrepancies against Layer F0 contracts.
