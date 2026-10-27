"""The scenario stress harness: the sweep is evidence only if the sweep is tested.

The harness re-runs the exact solve path the API uses, so its two guarantees —
determinism across solves and admissibility across the matrix — are assertions
about the system, and they belong in the suite, not just in a CLI report.

    python -m pytest tests/unit/test_stress.py -q
"""

from __future__ import annotations

import pytest

from scripts.stress import render_markdown, run_cell, run_matrix

MODULE = "tests.unit.test_stress"


@pytest.mark.unit
def test_a_cell_is_deterministic_and_carries_its_cross_check():
    row = run_cell("EVT-001", "SCN-EVT-001-10D")
    assert row["deterministic"] is True
    assert row["admissible"] is True
    assert row["plan_id"].startswith("PLAN-OPT-10D-")
    assert row["des_verdict"] in {"agrees", "des_slower", "des_faster", "not_applicable"}
    assert row["uniform_policies_evaluated"] > 0


@pytest.mark.unit
def test_the_matrix_sweeps_every_cell_and_holds_both_guarantees():
    report = run_matrix(event_ids=["EVT-001", "EVT-002"], horizons=[3, 10])
    assert report["cells"] == 4
    assert {r["scenario_id"] for r in report["rows"]} == {
        "SCN-EVT-001-3D",
        "SCN-EVT-001-10D",
        "SCN-EVT-002-3D",
        "SCN-EVT-002-10D",
    }
    assert report["all_deterministic"] is True
    assert report["all_admissible"] is True
    assert report["problems"] == []


@pytest.mark.unit
def test_the_report_states_the_guarantees_and_names_every_cell():
    report = run_matrix(event_ids=["EVT-001"], horizons=[10])
    text = render_markdown(report)
    assert "determinism HELD" in text
    assert "admissibility HELD" in text
    assert "SCN-EVT-001-10D" in text
    assert "| Scenario |" in text
