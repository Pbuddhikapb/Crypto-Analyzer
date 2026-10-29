#!/usr/bin/env bash
set -e

# ==============================================================================
# verify_architecture.sh — Automated Architectural & Regression Verification
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

echo "========================================================================"
echo " Crypto-Analyzer Architectural & Boundary Verification"
echo " Target Architecture: Area A (Data) -> Area B (Analysis) -> Area C (Sim) -> Area D (Presentation)"
echo "========================================================================"

# 1. Resolve Python binary
if [ -f "$PROJECT_ROOT/venv/bin/python" ]; then
    PYTHON_BIN="$PROJECT_ROOT/venv/bin/python"
    PYTEST_BIN="$PROJECT_ROOT/venv/bin/pytest"
    RUFF_BIN="$PROJECT_ROOT/venv/bin/ruff"
else
    PYTHON_BIN="$(command -v python3)"
    PYTEST_BIN="$(command -v pytest)"
    RUFF_BIN="$(command -v ruff)"
fi

# Step 1: Static code linting
echo ""
echo "[1/4] Running Ruff static analysis..."
"$RUFF_BIN" check .
echo ">>> Ruff check PASSED."

# Step 2: AST architectural boundary & evidence guardrails
echo ""
echo "[2/4] Verifying architectural boundaries & zero-lookahead guardrails..."
"$PYTEST_BIN" -q test_guardrails.py
echo ">>> Architectural boundary guardrails PASSED."

# Step 3: Full pytest test suite
echo ""
echo "[3/4] Running full unit, strategy, simulation, and integration test suite..."
"$PYTEST_BIN" -q
echo ">>> Test suite PASSED."

# Step 4: Frontend verification
echo ""
echo "[4/4] Verifying frontend: unit tests, typecheck, and production bundle..."
cd "$PROJECT_ROOT/frontend"
npm test
npx tsc --noEmit
npm run build
cd "$PROJECT_ROOT"
echo ">>> Frontend verification PASSED."

echo ""
echo "========================================================================"
echo " ALL ARCHITECTURAL CHECKS & VERIFICATIONS PASSED SUCCESSFULLY"
echo "========================================================================"
