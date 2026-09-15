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

step "1/9  Environment health check"
run "$PYTHON" scripts/health.py

step "2/9  Static check (backend compiles)"
run "$PYTHON" -m compileall -q backend scripts

# Ruff and mypy live in requirements-dev.txt, not requirements.txt, because they
# are not needed to RUN the demo. A missing tool is reported as a failure rather
# than skipped: "lint passes" is only meaningful if lint actually ran.
step "3/9  Python lint (ruff)"
if "$PYTHON" -m ruff --version >/dev/null 2>&1; then
  run "$PYTHON" -m ruff check .
else
  echo "  SKIPPED - install dev tooling: pip install -r requirements-dev.txt"
  FAILED=1
fi

step "4/9  Python typecheck (mypy)"
if "$PYTHON" -m mypy --version >/dev/null 2>&1; then
  run "$PYTHON" -m mypy backend scripts
else
  echo "  SKIPPED - install dev tooling: pip install -r requirements-dev.txt"
  FAILED=1
fi

# 159 tests. Markers: unit 73 · contract 36 · integration 11 · e2e 24 · redteam 26.
# Note the markers OVERLAP: the integration cases are marked inside the e2e
# module, so they are counted by both. The suite total is the honest number.
step "5/9  Backend test suite (159: unit · contract · integration · e2e · red team)"
run "$PYTHON" -m pytest -q

step "6/9  Frontend typecheck (app, design system and browser specs)"
if [[ -d frontend/node_modules ]]; then
  run bash -c "cd frontend && npx tsc -b"
else
  echo "  SKIPPED - run 'npm --prefix frontend install' first"
  FAILED=1
fi

step "7/9  Frontend unit tests and static a11y guards"
if [[ -d frontend/node_modules ]]; then
  run bash -c "cd frontend && npx vitest run"
fi

# The browser suite boots its own backend and frontend on isolated ports
# (8788 / 5174), so it does not care whether a dev server is already running.
# It needs a Chromium download once: (cd frontend && npx playwright install chromium)
step "8/9  Browser tests (real Chromium: hero journey, governance, accessibility)"
if [[ -d frontend/node_modules && -d "${HOME}/.cache/ms-playwright" ]]; then
  run bash -c "cd frontend && npx playwright test"
else
  echo "  SKIPPED - install browsers first: (cd frontend && npx playwright install chromium)"
fi

step "9/9  Frontend production build"
if [[ -d frontend/node_modules ]]; then
  run bash -c "cd frontend && npm run build"
else
  echo "  SKIPPED - run 'npm --prefix frontend install' first"
  FAILED=1
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
