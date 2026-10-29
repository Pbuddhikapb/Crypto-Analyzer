# Institutional Crypto Analysis Terminal & Strategy Validation Pipeline

An institutional market microstructure terminal and empirical quantitative validation pipeline for Binance USD-M Futures. Built with Python (FastAPI, NumPy, Pandas, PyArrow) and React (TypeScript, Vite, Tailwind CSS, Lightweight Charts).

---

## Current Scientific Status: No Strategy Has Validated Edge

> **Current Finding:** **No strategy has validated edge.**
> 
> Across extensive empirical testing on historical development data (January 1, 2021 through April 18, 2026, comprising up to 46,392 continuous hourly bars per symbol across 10 major pairs), all six codified strategy hypotheses failed to reject the empirical null hypothesis ($H_0$). When subjected to institutional friction (0.05% taker fee, 0.02% maker fee, 0.03% adverse slippage, and 0.01% 8-hour funding drag), every strategy achieved negative net expectancy ($EV < 0$) and proved statistically indistinguishable from, or worse than, matched random noise baselines ($p \ge 0.05$).
>
> Consequently, the terminal operates strictly in **evidence-status mode**. Automated order generation and paper trade execution are completely disabled in `server.py`. The user interface displays live market data, order book microstructure, risk sizing constraints, and empirical statistical evidence badges from `strategy_results.json` without directional endorsement.

---

## Core Architecture Principles

1. **Single Exit Authority (`engine/exits.py`)**:
   All exit mechanics (trailing ATR stops, partial profit-taking, breakeven triggers, stagnancy time-stops) are evaluated by a single pure mathematical engine across all simulations, baseline null distributions, and terminal bracket previews.
2. **Zero-Lookahead Guarantee**:
   Signals generated at the close of bar $t$ execute exclusively at the open of bar $t+1$. Truncating data after bar $t$ leaves historical signals up to bar $t$ strictly invariant.
3. **Realistic Institutional Friction**:
   All backtests deduct exchange fees, adverse execution slippage, and 8-hour perpetual funding costs.
4. **Matched Random Null Baselines (`research/random_baseline.py`)**:
   Strategies are judged against matched Monte Carlo null distributions ($N=1,000$ passes) that match the strategy's exact trade count, coin distribution, and ATR stop distance percentage to isolate true entry timing edge from asymmetric exit mathematics.
5. **Strict Holdout Boundary**:
   Historical data after `2026-04-18` is locked and accessible exclusively via the one-way append-only audit gate (`research/holdout_check.py`).
6. **Clean Layered Architecture**:
   Unidirectional dependency flow (Data $\rightarrow$ Analysis $\rightarrow$ Simulation $\rightarrow$ Presentation). All unvalidated legacy backtesters, obsolete paper traders, and duplicate risk modules have been permanently retired.

---

## Linux VM Setup

### Prerequisites
* Linux (Ubuntu 22.04+ LTS or Debian 12 recommended)
* Python 3.10, 3.11, or 3.12
* Node.js 18+ and npm
* Git

### Step-by-Step Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Pbuddhikapb/Crypto-Analyzer.git
   cd Crypto-Analyzer
   ```

2. **Set up Python Virtual Environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.lock
   ```

3. **Install and Configure Pre-Commit Hooks:**
   ```bash
   pip install pre-commit
   pre-commit install
   ```

4. **Build the Frontend:**
   ```bash
   cd frontend
   npm install
   npm run build
   cd ..
   ```

---

## Run Order (Pipeline Execution)

To reproduce the evidence pipeline and launch the terminal from scratch, execute commands in the following order from the repository root:

### Step 1: Download Clean Parquet Data Lake
Download continuous 1h and 4h Binance USD-M futures historical klines with SHA-256 checksum verification:
```bash
python -m data.downloader --symbols BTCUSDT ETHUSDT SOLUSDT BNBUSDT XRPUSDT DOGEUSDT ADAUSDT AVAXUSDT LINKUSDT SUIUSDT --timeframes 1h 4h
```

### Step 2: Generate Monte Carlo Baseline Distribution ($H_0$)
Generate the 1,000-pass random baseline to establish empirical hurdles (outputs `research/reports/baseline_distribution.json` and `research/reports/random_baseline_hurdle.md`):
```bash
python -m research.random_baseline --passes 1000
```

### Step 3: Run Strategy Evidence Validation Pipeline
Run backtests and matched random null distributions for all codified strategies (outputs `research/reports/strategy_results.json` and `research/reports/strategy_validation_report.md`):
```bash
python -m research.strategy_runner --passes 1000
```

### Step 4: Run Complete Test Suite
Verify that all unit tests, zero-volume guards, session break checks, and hash synchronization pass cleanly:
```bash
pytest -q
ruff check .
```

### Step 5: Launch Live Terminal Backend Server
Start the high-performance FastAPI server delivering real-time Spot & Futures WebSockets and evidence APIs:
```bash
uvicorn server:app --host 127.0.0.1 --port 8008
```

### Step 6: Launch Frontend Development Server (or Serve Static Dist)
In a separate terminal:
```bash
cd frontend
npm run dev -- --host 127.0.0.1 --port 5174
```

### Step 7 (Locked One-Way Audit): Holdout Verification
When a strategy hypothesis is pre-registered and ready for out-of-sample verification on post-April 2026 data:
```bash
python -m research.holdout_check TrendPullback
```
*(Appends immutable audit record to `research/reports/holdout_log.csv` and permanently blocks re-evaluation on unchanged source code).*

---

## Operational Management Scripts (Linux / Tmux)

Production operations on Linux should be managed via the scripts in `scripts/`:

### Start the Terminal Server
```bash
./scripts/start.sh
```
* **Execution Flow:**
  1. Builds the frontend static bundle once (`cd frontend && npm run build`).
  2. Ensures log directory `logs/` exists.
  3. Launches `uvicorn server:app` bound strictly to `127.0.0.1:8008` inside an isolated tmux session (`crypto-terminal`).
  4. Serves the pre-compiled frontend directly from FastAPI static files on the same port (`http://127.0.0.1:8008/`).
  5. Streams all backend stdout/stderr to `logs/server.log`.
  6. Verifies system readiness by polling `/api/health`.

### Stop the Terminal Server
```bash
./scripts/stop.sh
```
* Gracefully terminates the `crypto-terminal` tmux session and frees port 8008.

---

## Secure Remote Access via SSH Port Forwarding

> **Security Guardrail:** The terminal server is bound strictly to `127.0.0.1:8008`. **Do not open any external firewall ports on the host VM.**

To securely access the web interface and real-time WebSocket streams from your local machine, open an SSH port-forwarding tunnel:

```bash
ssh -L 8008:127.0.0.1:8008 <user>@<VM_IP_ADDRESS>
```

Once the SSH connection is established, navigate to the following address in your local browser:
```
http://localhost:8008
```

All static assets, REST API requests (`/api/...`), and bi-directional WebSocket telemetry (`/ws`) will route securely through loopback over the encrypted SSH channel.

---

## Repository Structure

```
├── data/
│   ├── downloader.py          # Parquet downloader with SHA-256 verification
│   ├── loader.py              # Zero-lookahead candle loader & dataset hash
│   └── candles/               # Historical 1h and 4h Parquet klines
├── engine/
│   ├── exits.py               # Single Exit Authority (pure mathematical engine)
│   └── simulator.py           # Institutional portfolio simulation engine
├── research/
│   ├── strategies/            # Codified strategy implementations (zero lookahead)
│   ├── preregistration/       # Pre-registration specs & hypothesis audit log
│   ├── reports/               # Machine-readable results & validation dossiers
│   ├── random_baseline.py     # Monte Carlo unconditioned & matched null baseline
│   ├── strategy_runner.py     # Master Phase 3 validation pipeline orchestrator
│   ├── evaluator.py           # Statistical decision rules & verdict generator
│   └── holdout_check.py       # One-pass locked out-of-sample holdout gate
├── domain/                    # Shared cross-cutting domain contracts (signals, exit configs)
├── scripts/
│   ├── verify_architecture.sh # Unified architectural boundary & test verification script
│   ├── start.sh               # Builds frontend & runs FastAPI in tmux on 127.0.0.1:8008
│   └── stop.sh                # Gracefully terminates tmux session and port 8008
├── frontend/                  # React + TypeScript + Tailwind institutional terminal
├── server.py                  # FastAPI WebSocket hub & evidence REST server
├── main.py                    # Evidence radar terminal scanner
├── market_feed.py             # Live Binance USD-M WebSocket streams & warmup feeds
├── indicators.py              # Mathematical technical indicators (EMA, ATR, ADX, etc.)
├── microstructure.py          # Futures L2 order book, funding, and liquidation engines
├── requirements.lock          # Exact pinned dependencies for CI and production
└── ARCHITECTURE.md            # Comprehensive system architecture & governance rules
```
