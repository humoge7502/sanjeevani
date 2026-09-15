#!/usr/bin/env bash
# Start the SANJEEVANI backend and frontend together, then tear both down on exit.
#
#   bash scripts/dev.sh
#
# Both are local only. Nothing here reaches the internet, which is the point:
# the demo must survive a venue network.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

API_PORT="${SANJEEVANI_API_PORT:-8787}"
WEB_PORT="${VITE_PORT:-5173}"

if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
  echo "Python 3.11+ is required." >&2
  exit 1
fi
PYTHON="$(command -v python3 || command -v python)"

cleanup() {
  echo
  echo "Shutting down..."
  [[ -n "${API_PID:-}" ]] && kill "$API_PID" 2>/dev/null || true
  [[ -n "${WEB_PID:-}" ]] && kill "$WEB_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting backend on http://127.0.0.1:${API_PORT} ..."
"$PYTHON" -m uvicorn backend.api.app:app --host 127.0.0.1 --port "$API_PORT" --log-level warning &
API_PID=$!

echo "Starting frontend on http://127.0.0.1:${WEB_PORT} ..."
( cd frontend && npx vite --host 127.0.0.1 --port "$WEB_PORT" ) &
WEB_PID=$!

sleep 3
cat <<BANNER

  SANJEEVANI is starting.

    Command center : http://127.0.0.1:${WEB_PORT}
    API            : http://127.0.0.1:${API_PORT}/api/health
    API docs       : http://127.0.0.1:${API_PORT}/docs

  Demo path:
    1. Click "Reset"
    2. Click "Run hero scenario"
    3. Approve as Meera Iyer in the approval console
    4. Click "Execute approved plan"
    5. Scroll to Audit, then Learning

  Press Ctrl+C to stop both.

BANNER

wait
