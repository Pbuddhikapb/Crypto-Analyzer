#!/usr/bin/env bash
set -e

# Resolve repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SESSION_NAME="crypto-terminal"
HOST="127.0.0.1"
PORT="8008"
LOG_DIR="$PROJECT_ROOT/logs"
LOG_FILE="$LOG_DIR/server.log"

echo "=== Starting Institutional Crypto Terminal ==="

# 1. Ensure logs directory exists
mkdir -p "$LOG_DIR"

# 2. Build frontend static bundle
echo "[1/3] Building frontend static bundle..."
cd "$PROJECT_ROOT/frontend"
npm run build
cd "$PROJECT_ROOT"

# 3. Resolve uvicorn binary
if [ -f "$PROJECT_ROOT/venv/bin/uvicorn" ]; then
    UVICORN_BIN="$PROJECT_ROOT/venv/bin/uvicorn"
elif command -v uvicorn >/dev/null 2>&1; then
    UVICORN_BIN="$(command -v uvicorn)"
else
    echo "ERROR: uvicorn not found. Please activate your virtual environment." >&2
    exit 1
fi

# 4. Check if tmux session already exists
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "WARNING: tmux session '$SESSION_NAME' is already running. Stopping it first..."
    tmux kill-session -t "$SESSION_NAME"
    sleep 1
fi

# 5. Launch backend in tmux bound strictly to 127.0.0.1:8008
echo "[2/3] Launching backend inside tmux session '$SESSION_NAME' bound to $HOST:$PORT..."
tmux new-session -d -s "$SESSION_NAME" \
    "cd '$PROJECT_ROOT' && '$UVICORN_BIN' server:app --host $HOST --port $PORT >> '$LOG_FILE' 2>&1"

# 6. Verify health
echo "[3/3] Verifying terminal health..."
MAX_ATTEMPTS=15
ATTEMPT=0
READY=0

while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    sleep 1
    ATTEMPT=$((ATTEMPT + 1))
    if curl -s -f "http://$HOST:$PORT/api/health" >/dev/null 2>&1; then
        READY=1
        break
    fi
done

if [ $READY -eq 1 ]; then
    echo "=== Crypto Terminal Successfully Started ==="
    echo "  • Local Address: http://$HOST:$PORT"
    echo "  • Tmux Session:  $SESSION_NAME (attach via: tmux attach -t $SESSION_NAME)"
    echo "  • Server Log:    $LOG_FILE"
    echo "  • Remote Access: Forward local port 8008 via SSH:"
    echo "                   ssh -L 8008:127.0.0.1:8008 user@<VM_IP>"
else
    echo "WARNING: Server did not respond within ${MAX_ATTEMPTS}s. Check '$LOG_FILE' for details."
fi
