﻿#!/usr/bin/env bash
# Full pre-demo verification. Run this before every rehearsal and before any commit.
#
#   bash scripts/verify.sh
#
# Order matters: cheap checks first, so a broken environment fails in seconds
# rather than after a four-minute test run.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Pick python3 first, then python -- but a `command -v` hit is not enough:
# Windows ships a Microsoft Store *alias* called python3 that resolves on PATH
# and then prints "Python was not found". Verify the interpreter actually
# executes before committing to it, and fail loudly when neither works, so the
# gates below can never be silently skipped or mis-run.
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys" >/dev/null 2>&1; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done
if [[ -z "$PYTHON" ]]; then
  echo "No working Python interpreter found (looked for python3 and python)." >&2
  exit 1
fi
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

step "1/10  Environment health check"
run "$PYTHON" scripts/health.py

step "2/10  Static check (backend compiles)"
run "$PYTHON" -m compileall -q backend scripts

# Ruff and mypy live in requirements-dev.txt, not requirements.txt, because they
# are not needed to RUN the demo. A missing tool is reported as a failure rather
# than skipped: "lint passes" is only meaningful if lint actually ran.
step "3/10  Python lint (ruff)"
if "$PYTHON" -m ruff --version >/dev/null 2>&1; then
  run "$PYTHON" -m ruff check .
else
  echo "  SKIPPED - install dev tooling: pip install -r requirements-dev.txt"
  FAILED=1
fi

step "4/10  Python typecheck (mypy)"
if "$PYTHON" -m mypy --version >/dev/null 2>&1; then
  run "$PYTHON" -m mypy backend scripts
else
  echo "  SKIPPED - install dev tooling: pip install -r requirements-dev.txt"
  FAILED=1
fi

# 396 tests. Markers: unit · contract · integration · e2e · redteam. The markers
# OVERLAP (integration cases live inside the e2e module), so the suite total is
# the honest number and summing the markers would double-count.
step "5/10  Backend test suite (unit · contract · integration · e2e · red team)"
run "$PYTHON" -m pytest -q

step "6/10  Frontend typecheck (app, design system and browser specs)"
if [[ -d frontend/node_modules ]]; then
  run bash -c "cd frontend && npx tsc -b"
else
  echo "  SKIPPED - run 'npm --prefix frontend install' first"
  FAILED=1
fi

step "7/10  Frontend unit tests and static a11y guards"
if [[ -d frontend/node_modules ]]; then
  run bash -c "cd frontend && npx vitest run"
fi

# The browser suite boots its own backend and frontend on isolated ports
# (8788 / 5174), so it does not care whether a dev server is already running.
# It needs a Chromium download once: (cd frontend && npx playwright install chromium)
step "8/10  Browser tests (real Chromium: hero journey, governance, accessibility)"
# Playwright's browser cache lives in ~/.cache/ms-playwright on Linux/macOS and
# in %LOCALAPPDATA%\ms-playwright on Windows; a path check for only one of them
# silently skipped the whole suite on the other platform.
PLAYWRIGHT_CACHE="${HOME}/.cache/ms-playwright"
if [[ ! -d frontend/node_modules ]]; then
  echo "  SKIPPED - run 'npm --prefix frontend install' first"
  FAILED=1
elif [[ -z "$(ls "${LOCALAPPDATA:-}/ms-playwright" 2>/dev/null)" && ! -d "$PLAYWRIGHT_CACHE" ]]; then
  echo "  SKIPPED - install browsers first: (cd frontend && npx playwright install chromium)"
else
  run bash -c "cd frontend && npx playwright test"
fi

step "9/10  Frontend production build"
if [[ -d frontend/node_modules ]]; then
  run bash -c "cd frontend && npm run build"
else
  echo "  SKIPPED - run 'npm --prefix frontend install' first"
  FAILED=1
fi

# The serving half of the nonce-based CSP is only testable once dist/ exists, and
# step 9 is what creates it. Running this last is deliberate: the contract test
# skips without a build, so ordering it earlier would let it pass vacuously.
step "10/10  Served-SPA security contract (CSP nonce, headers, traversal)"
if [[ -d frontend/dist ]]; then
  run "$PYTHON" -m pytest tests/contract/test_served_spa_security.py -q
else
  echo "  SKIPPED - no frontend build; step 9 did not produce dist/"
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
