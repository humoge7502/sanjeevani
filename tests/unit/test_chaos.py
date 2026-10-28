"""The chaos drill: the drill itself must be as tested as the paths it exercises.

    python -m pytest tests/unit/test_chaos.py -q
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_chaos():
    """Import scripts/chaos.py without it being a package."""
    if "sanjeevani_chaos" in sys.modules:
        return sys.modules["sanjeevani_chaos"]
    spec = importlib.util.spec_from_file_location(
        "sanjeevani_chaos", REPO_ROOT / "scripts" / "chaos.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["sanjeevani_chaos"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def chaos():
    return _load_chaos()


@pytest.mark.unit
def test_the_drill_holds_every_degradation(chaos):
    report = chaos.run_drill()
    assert report["scenarios"] == 4
    assert report["all_held"] is True, report["rows"]
    names = {r["scenario"] for r in report["rows"]}
    assert names == {
        "solver-unavailable",
        "ledger-tamper-caught",
        "des-veto-switch",
        "foreign-tenant-refused",
    }


@pytest.mark.unit
def test_the_report_leads_with_the_verdict(chaos):
    text = chaos.render(chaos.run_drill())
    assert "ALL DEGRADATIONS HELD" in text
    assert "[HELD ] ledger-tamper-caught" in text
