"""Shared pytest fixtures.

Every mutable singleton in the system (network cache, telemetry cache, ledger,
approval registry, SAP mocks) is reset between tests. Determinism is only
meaningful if a test cannot inherit state from the test before it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


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

    orchestrator.reset()
    return orchestrator.run()


@pytest.fixture
def orchestrator():
    from backend.orchestrator import orchestrator as orch

    return orch
