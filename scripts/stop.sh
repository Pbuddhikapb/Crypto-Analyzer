#!/usr/bin/env bash
set -e

SESSION_NAME="crypto-terminal"

echo "=== Stopping Institutional Crypto Terminal ==="

STOPPED=0

# 1. Terminate tmux session if active
if tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    echo "Killing tmux session '$SESSION_NAME'..."
    tmux kill-session -t "$SESSION_NAME"
    STOPPED=1
fi

# 2. Check for any remaining uvicorn process on port 8008
REMAINING_PIDS=$(lsof -ti :8008 2>/dev/null || fuser 8008/tcp 2>/dev/null || true)
if [ -n "$REMAINING_PIDS" ]; then
    echo "Terminating remaining processes on port 8008 (PIDs: $REMAINING_PIDS)..."
    kill -15 $REMAINING_PIDS 2>/dev/null || kill -9 $REMAINING_PIDS 2>/dev/null || true
    STOPPED=1
fi

if [ $STOPPED -eq 1 ]; then
    echo "=== Crypto Terminal Successfully Stopped ==="
else
    echo "No running Crypto Terminal instance found."
fi
