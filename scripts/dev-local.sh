#!/usr/bin/env bash
# =============================================================================
# dev-local.sh — Crypto-Analyzer Hybrid Local+VM Dev Launcher
# =============================================================================
# Starts the local cache proxy (port 8009) and Vite dev server (port 5174).
#
# Prerequisites (run BEFORE this script, in a separate Termius tab):
#   ssh -L 8008:127.0.0.1:8008 ubuntu@<YOUR_VM_IP> -N -o ServerAliveInterval=30
#
# Usage:
#   ./scripts/dev-local.sh              # start everything
#   ./scripts/dev-local.sh --no-check  # skip SSH tunnel health check
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SKIP_CHECK=0
for arg in "$@"; do
  [[ "$arg" == "--no-check" ]] && SKIP_CHECK=1
done

# ── Colors ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓ ${*}${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ ${*}${NC}"; }
err()  { echo -e "${RED}  ✗ ${*}${NC}"; }

echo ""
echo "══════════════════════════════════════════════════════"
echo "   Crypto-Analyzer — Hybrid Local+VM Dev Mode"
echo "══════════════════════════════════════════════════════"
echo ""

# ── Step 1: Check SSH tunnel ──────────────────────────────────────────────────
echo "[1/3] Checking SSH tunnel to VM (localhost:8008)..."

if [[ $SKIP_CHECK -eq 1 ]]; then
  warn "Skipping SSH tunnel check (--no-check flag)"
else
  if curl -sf --max-time 3 http://127.0.0.1:8008/api/health > /dev/null 2>&1; then
    ok "SSH tunnel is active — VM reachable at localhost:8008"
  else
    err "VM not reachable at localhost:8008"
    echo ""
    echo "  Start the SSH tunnel in a Termius tab first:"
    echo "    ssh -L 8008:127.0.0.1:8008 ubuntu@<YOUR_VM_IP> -N -o ServerAliveInterval=30"
    echo ""
    echo "  Then re-run this script."
    echo ""
    exit 1
  fi
fi

# ── Step 2: Start cache proxy ─────────────────────────────────────────────────
echo "[2/3] Starting local cache proxy on port 8009..."

# Kill any existing proxy on 8009
EXISTING_PID=$(lsof -ti tcp:8009 2>/dev/null || true)
if [[ -n "$EXISTING_PID" ]]; then
  warn "Port 8009 in use (PID $EXISTING_PID) — killing..."
  kill "$EXISTING_PID" 2>/dev/null || true
  sleep 1
fi

node "$PROJECT_ROOT/scripts/local-cache-proxy.js" &
PROXY_PID=$!

# Wait for proxy to start
sleep 1
if kill -0 "$PROXY_PID" 2>/dev/null; then
  ok "Cache proxy started (PID $PROXY_PID)"
else
  err "Cache proxy failed to start. Check Node.js is installed: node --version"
  exit 1
fi

# ── Step 3: Start Vite dev server ─────────────────────────────────────────────
echo "[3/3] Starting Vite dev server..."
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Open in browser:  http://localhost:5174"
echo "  Cache proxy:      http://localhost:8009"
echo "  VM backend:       http://localhost:8008  (via SSH tunnel)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Cleanup proxy on exit
trap 'echo ""; echo "Stopping cache proxy (PID $PROXY_PID)..."; kill "$PROXY_PID" 2>/dev/null; echo "Done."; exit 0' EXIT INT TERM

cd "$PROJECT_ROOT/frontend"
npm run dev
