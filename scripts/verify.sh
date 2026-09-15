#!/usr/bin/env bash
# Full pre-demo verification. Run this before every rehearsal and before any commit.
#
#   bash scripts/verify.sh
#
# Order matters: cheap checks first, so a broken environment fails in seconds
# rather than after a four-minute test run.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="$(command -v python3 || command -v python)"
FAILED=0

step() {
  echo
  echo "=============================================================="
  echo "  $1"
  echo "=============================================================="
}

run() {
  if "$@"; then
    echo "  -> OK"
  else
    echo "  -> FAILED: $*"
    FAILED=1
  fi
}

step "1/7  Environment health check"
run "$PYTHON" scripts/health.py

step "2/7  Static check (backend compiles)"
run "$PYTHON" -m compileall -q backend scripts

step "3/7  Backend test suite (unit + contract + integration + e2e + red team)"
run "$PYTHON" -m pytest -q

step "4/7  Frontend typecheck (app, design system and browser specs)"
if [[ -d frontend/node_modules ]]; then
  run bash -c "cd frontend && npx tsc -b"
else
  echo "  SKIPPED - run 'npm --prefix frontend install' first"
  FAILED=1
fi

step "5/7  Frontend unit tests"
if [[ -d frontend/node_modules ]]; then
  run bash -c "cd frontend && npx vitest run"
fi

# The browser suite boots its own backend and frontend on isolated ports
# (8788 / 5174), so it does not care whether a dev server is already running.
# It needs a Chromium download once: (cd frontend && npx playwright install chromium)
step "6/7  Browser tests (real Chromium: hero journey, governance, accessibility)"
if [[ -d frontend/node_modules && -d "${HOME}/.cache/ms-playwright" ]]; then
  run bash -c "cd frontend && npx playwright test"
else
  echo "  SKIPPED - install browsers first: (cd frontend && npx playwright install chromium)"
fi

step "7/7  Frontend production build"
if [[ -d frontend/node_modules ]]; then
  run bash -c "cd frontend && npm run build"
fi

echo
echo "=============================================================="
if [[ "$FAILED" -eq 0 ]]; then
  echo "  VERIFICATION PASSED - the demo is ready."
else
  echo "  VERIFICATION FAILED - fix the steps marked FAILED above."
fi
echo "=============================================================="
exit "$FAILED"
