"""A6 learning: the PENDING scorecard must have the same shape as RECONCILED.

The optimizer produces plan ids (PLAN-OPT-...) that have no entry in
``data/events/outcome_observations.yaml`` — the seeded observations are keyed to
the fixture's plan id. The learning stage must then report PENDING **without
changing shape**: ``scripts/demo.py`` and the UI's LearningPanel read
``scorecard.summary`` and ``scorecard.method`` unconditionally, and both crashed
(or rendered blanks) when PENDING omitted them. These tests fail if the two
scorecard shapes ever drift apart again.

    python -m pytest tests/unit/test_learning.py -q
"""

from __future__ import annotations

import pytest

from backend.contracts import RecoveryPlan
from backend.learning.reconcile import reconcile


def _plan(plan_id: str, strategy: str = "WAIT") -> RecoveryPlan:
    """A contract-valid RecoveryPlan carrying an arbitrary plan id."""
    return RecoveryPlan(
        plan_id=plan_id,
        strategy=strategy,
        cost=184_000.0,
        service_level=0.92,
        resilience_score=0.74,
        temperature_risk=0.11,
        recovery_time_hours=72.0,
        impacted_skus=["BIO-002"],
        impacted_lanes=["LANE-MUM-BIO-EU"],
        rationale="test plan",
        source="optimizer",
    )


# What M1 claimed before execution; the reconciler only reads these three keys.
_IMPACT = {
    "revenue_at_risk": 3_908_933.39,
    "stockout_probability": 0.6559,
    "service_level_risk": 0.6314,
}


@pytest.mark.unit
def test_a_plan_without_a_seeded_observation_reports_pending():
    result = reconcile(_plan("PLAN-OPT-10D-F81A4EC7"), _IMPACT)
    assert result.outcome.reconciliation_status == "PENDING"
    assert result.scorecard["status"] == "PENDING"
    # The honesty clause lives on the outcome: no actual was invented, and no
    # predicted-vs-actual rows were fabricated to fill the table.
    assert result.outcome.actual == {}
    assert result.scorecard["rows"] == []
    assert result.outcome.calibration_signals == []


@pytest.mark.unit
def test_the_pending_scorecard_carries_the_reconciled_shape():
    """The regression: demo.py and the UI read summary/method/strategy unconditionally."""
    result = reconcile(_plan("PLAN-OPT-10D-F81A4EC7"), _IMPACT)
    scorecard = result.scorecard
    assert scorecard["summary"]["metrics_compared"] == 0
    assert scorecard["summary"]["better_than_predicted"] == 0
    assert scorecard["summary"]["worse_than_predicted"] == 0
    assert scorecard["summary"]["calibration_signals"] == 0
    assert scorecard["summary"]["retraining_performed"] is False
    # Identity fields the UI renders even when nothing was compared.
    assert scorecard["plan_id"] == "PLAN-OPT-10D-F81A4EC7"
    assert scorecard["strategy"] == "WAIT"
    # A method note must exist and must say why nothing was compared.
    assert "No outcome observation" in scorecard["method"]


@pytest.mark.unit
def test_the_two_scorecard_shapes_expose_the_same_keys():
    """Shape drift between PENDING and RECONCILED is the bug this file exists for."""
    pending = reconcile(_plan("PLAN-UNKNOWN-PENDING"), _IMPACT).scorecard
    # A plan id that IS seeded: the fixture's hero plan.
    seeded = reconcile(_plan("PLAN-001"), _IMPACT).scorecard
    assert seeded["status"] == "RECONCILED", (
        "PLAN-001 must stay seeded for this test to mean anything"
    )
    assert set(pending.keys()) == set(seeded.keys()), (
        f"PENDING scorecard keys drifted: {sorted(set(seeded) - set(pending))} missing"
    )
    assert set(pending["summary"].keys()) == set(seeded["summary"].keys())
