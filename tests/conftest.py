"""Shared pytest fixtures.

Every mutable singleton in the system (orchestrator run state, network cache,
telemetry cache, ledger, approval registry, SAP mocks) is reset between tests.
Determinism is only meaningful if a test cannot inherit state from the test
before it.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Test isolation: the suite must never touch the REAL runtime directory.
#
# `orchestrator.reset()` calls `ledger.reset()`, whose default is
# `write_file=True` -- it TRUNCATES the ledger file. Several tests call reset()
# as part of testing normal behaviour, so running the suite used to delete the
# on-disk audit ledger: a 43-record chain came back as 4 lines. Data loss caused
# by running tests is not an acceptable side effect, and it got worse the moment
# the ledger became genuinely persistent across restarts.
#
# Redirecting the runtime directory to a per-session temp directory fixes it at
# the source rather than by asking every test to remember a flag.
#
# ORDERING IS LOAD-BEARING: this must run before `backend.config` is first
# imported, because RUNTIME_DIR is resolved at import time. That is why it is
# module-level code here, and why conftest is loaded before any test module.
# ---------------------------------------------------------------------------
TEST_RUNTIME_DIR = Path(tempfile.mkdtemp(prefix="sanjeevani-test-runtime-"))
os.environ["SANJEEVANI_RUNTIME_DIR"] = str(TEST_RUNTIME_DIR)


@pytest.fixture(scope="session", autouse=True)
def _drop_test_runtime_dir():
    """Remove the sandboxed runtime directory when the session ends."""
    yield
    shutil.rmtree(TEST_RUNTIME_DIR, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean_state():
    """Reset every module-level singleton around each test."""
    from backend.agents import telemetry
    from backend.audit.ledger import ledger
    from backend.config import reload_config
    from backend.governance.approval import registry
    from backend.graph.network import Network
    from backend.orchestrator import orchestrator
    from backend.sap.execution import orchestrator as exec_orchestrator

    def wipe() -> None:
        # The orchestrator owns the run state and, through its own reset(), also
        # clears the ledger, approval registry and SAP mocks. It is listed first
        # because it is the superset; the explicit calls below keep each reset
        # visible and make this fixture correct even if that ever changes.
        orchestrator.reset()
        reload_config()
        Network.load.cache_clear()
        telemetry.reset_cache()
        ledger.reset(write_file=False)
        registry.reset()
        exec_orchestrator.reset()

    wipe()
    yield
    wipe()


@pytest.fixture
def network():
    from backend.graph.network import Network

    return Network.load()


@pytest.fixture
def m1():
    from backend.agents.pipeline import run_m1

    return run_m1()


@pytest.fixture
def run_state():
    """A full first half of the demo: M1 -> M2 -> governance, awaiting approval."""
    from backend.orchestrator import orchestrator

    return orchestrator.run()


@pytest.fixture
def orchestrator():
    from backend.orchestrator import orchestrator as orch

    return orch
