#!/usr/bin/env python3
"""Production entrypoint.

    python scripts/serve.py

Why this exists instead of a raw `uvicorn` line in the Dockerfile: host and port
are configuration, and configuration lives in `config/settings.yaml` with an
environment override (see `backend.config._apply_env_overrides`). Hardcoding
`--host 0.0.0.0 --port 8787` in the image would make the port impossible to
change without rebuilding.

SINGLE PROCESS, DELIBERATELY.

The orchestrator, the approval state machine and the ledger are in-process
singletons. Running more than one worker would give each worker its OWN copy of
that state: one worker would hold the approval and another would refuse the
execution, and the ledger would fork. This module therefore never passes
`workers=` to uvicorn, and `docs/deployment.md` records horizontal scaling as a
production requirement rather than claiming the MVP supports it.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import uvicorn

# Allow `python scripts/serve.py` from the repository root without installing the
# package. The import-after-code pattern is why ruff.toml ignores E402 for
# scripts/**.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.audit.ledger import ledger
from backend.config import FRONTEND_DIST, settings

logger = logging.getLogger("sanjeevani.serve")


def main() -> int:
    cfg = settings()
    host = str(cfg["api"]["host"])
    port = int(cfg["api"]["port"])

    if not (FRONTEND_DIST / "index.html").is_file():
        logger.warning(
            "No frontend build at %s - serving the API only. "
            "Build it with: npm --prefix frontend run build",
            FRONTEND_DIST,
        )

    logger.info("SANJEEVANI listening on http://%s:%s", host, port)
    # Report the path the ledger actually resolved, not a guessed filename.
    logger.info("ledger: %s", ledger.path)
    logger.info("contract version: %s", cfg.get("meta", {}).get("config_version", "unknown"))

    uvicorn.run(
        "backend.api.app:app",
        host=host,
        port=port,
        # No workers= argument, on purpose: the orchestrator and ledger are
        # in-process singletons and a second worker would fork that state.
        log_level="info",
        # Trust the platform's proxy headers (X-Forwarded-For/Proto) so logs and
        # generated URLs are correct behind a load balancer.
        proxy_headers=True,
        forwarded_allow_ips="*",
        access_log=True,
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    raise SystemExit(main())
