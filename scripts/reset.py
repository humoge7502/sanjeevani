#!/usr/bin/env python3
"""Reset the demo to a pristine state.

    python scripts/reset.py

Clears the decision ledger (in memory and on disk), all approval authority, the
SAP-shaped mock call logs, and the cached network/telemetry. Deliberately
destroys execution authority: an approval must never survive a reset, because a
stale approval is exactly the kind of state that turns a strict gate into a
rubber stamp.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.orchestrator import orchestrator


def main() -> int:
    result = orchestrator.reset()
    print("SANJEEVANI reset complete.")
    print(f"  reset_at: {result['reset_at']}")
    print(f"  {result['note']}")
    print("\n  Cleared: decision ledger · approval registry · SAP mock call logs")
    print("  Reloaded: config, network seed, telemetry cache")
    print("\n  Next: python scripts/demo.py   (or POST /api/demo/run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
