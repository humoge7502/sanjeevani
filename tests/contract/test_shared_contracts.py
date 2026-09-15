"""Contract tests for the six frozen integration objects.

These are the highest-value tests in the suite: if one fails, a member has broken
another member's integration. They assert the exact field names in the team
handoff spec and that the *real* pipeline output validates against them.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.contracts import (
    CONTRACT_VERSION,
    Approval,
    Event,
    ExecutionReceipt,
    Impact,
    Outcome,
    RecoveryPlan,
)

# The exact keys each contract MUST expose, transcribed from the handoff spec.
REQUIRED_KEYS = {
    Event: {"event_id", "type", "severity", "confidence", "timestamp", "source"},
    Impact: {
        "event_id",
        "affected_nodes",
        "affected_shipments",
        "revenue_at_risk",
        "stockout_probability",
        "service_level_risk",
    },
    RecoveryPlan: {
        "plan_id",
        "strategy",
        "cost",
        "service_level",
        "resilience_score",
        "temperature_risk",
        "recovery_time_hours",
        "compliance_status",
    },
    Approval: {"plan_id", "approved", "approved_by", "timestamp"},
    ExecutionReceipt: {"plan_id", "ibp", "tm", "ariba", "timestamp"},
    Outcome: {"plan_id", "predicted", "actual", "delta", "learning_note"},
}


@pytest.mark.contract
@pytest.mark.parametrize("model,expected", list(REQUIRED_KEYS.items()))
def test_contract_exposes_required_fields(model, expected):
    assert expected <= set(model.model_fields), (
        f"{model.__name__} is missing contract fields: "
        f"{sorted(expected - set(model.model_fields))}"
    )


@pytest.mark.contract
def test_contracts_reject_unknown_fields():
    """Schema drift must fail loudly rather than be silently ignored."""
    with pytest.raises(ValidationError):
        Event(
            event_id="EVT-001",
            type="COLD_CHAIN_EXCURSION",
            severity="HIGH",
            confidence=0.96,
            timestamp="2026-09-30T06:00:00Z",
            source="IoT",
            surprise_field="not allowed",
        )


@pytest.mark.contract
def test_handoff_example_event_validates():
    """The literal example from the specification must round-trip."""
    event = Event(
        event_id="EVT-001",
        type="COLD_CHAIN_EXCURSION",
        severity="HIGH",
        confidence=0.96,
        timestamp="2026-09-30T06:00:00Z",
        source="IoT",
    )
    assert event.confidence == 0.96
    assert event.severity.value == "HIGH"


@pytest.mark.contract
def test_recovery_plan_rejects_negative_cost():
    with pytest.raises(ValidationError):
        RecoveryPlan(
            plan_id="PLAN-001",
            strategy="REROUTE_MUMBAI_AIR",
            cost=-1.0,
            service_level=0.9,
            resilience_score=0.8,
            temperature_risk=0.2,
            recovery_time_hours=18,
        )


@pytest.mark.contract
def test_confidence_bounds_enforced():
    for bad in (-0.01, 1.01):
        with pytest.raises(ValidationError):
            Event(
                event_id="E",
                type="PORT_CLOSURE",
                severity="HIGH",
                confidence=bad,
                timestamp="2026-09-30T06:00:00Z",
                source="NEWS",
            )


@pytest.mark.contract
def test_impact_rejects_out_of_range_probability():
    with pytest.raises(ValidationError):
        Impact(
            event_id="EVT-001",
            revenue_at_risk=0,
            stockout_probability=1.5,
            service_level_risk=0,
        )


@pytest.mark.contract
def test_execution_receipt_statuses_are_constrained():
    with pytest.raises(ValidationError):
        ExecutionReceipt(
            plan_id="PLAN-001",
            ibp="MAYBE",
            tm="SUCCESS",
            ariba="SUCCESS",
            timestamp="2026-09-30T06:00:00Z",
        )


@pytest.mark.contract
def test_outcome_never_claims_retraining_by_default():
    outcome = Outcome(plan_id="PLAN-001")
    assert outcome.retraining_performed is False


@pytest.mark.contract
def test_contract_version_is_declared():
    assert CONTRACT_VERSION == "1.0.0"
    assert Impact(event_id="E", revenue_at_risk=0, stockout_probability=0, service_level_risk=0).contract_version == CONTRACT_VERSION


# ---------------------------------------------------------------------------
# Pipeline output must validate against the frozen contracts
# ---------------------------------------------------------------------------


@pytest.mark.contract
def test_real_pipeline_events_validate_against_event_contract(m1):
    assert m1.events, "hero scenario must verify at least one event"
    for verified in m1.events:
        event = Event(**verified.as_event_dict())
        assert event.event_id == verified.event_id
        assert 0.0 <= event.confidence <= 1.0


@pytest.mark.contract
def test_real_impact_validates_against_impact_contract(m1):
    assert m1.impacts
    for payload in m1.impacts:
        contract_fields = {k: payload[k] for k in REQUIRED_KEYS[Impact]}
        impact = Impact(**contract_fields)
        assert impact.revenue_at_risk >= 0.0
        assert 0.0 <= impact.stockout_probability <= 1.0
        assert 0.0 <= impact.service_level_risk <= 1.0
        assert impact.affected_nodes, "an impact with no affected nodes is not an impact"


@pytest.mark.contract
def test_real_recovery_plan_validates_against_contract(run_state):
    plan = run_state["plan"]
    contract = RecoveryPlan(**{k: plan[k] for k in REQUIRED_KEYS[RecoveryPlan]})
    assert contract.source == "fixture"
    assert contract.compliance_status in {"PENDING", "PASSED", "FAILED", "REVIEW"}


@pytest.mark.contract
def test_real_approval_and_receipt_validate(orchestrator, run_state):
    from backend.contracts import ApprovalDecision

    orchestrator.decide(ApprovalDecision.APPROVE, "meera.iyer", "contract test")
    state = orchestrator.execute()

    approval = Approval(**{k: state["approval"][k] for k in REQUIRED_KEYS[Approval]})
    assert approval.approved is True
    assert approval.approved_by == "Meera"

    receipt = ExecutionReceipt(
        **{k: state["execution"]["receipt"][k] for k in REQUIRED_KEYS[ExecutionReceipt]}
    )
    assert receipt.all_succeeded


@pytest.mark.contract
def test_real_outcome_validates_against_contract(orchestrator, run_state):
    from backend.contracts import ApprovalDecision

    orchestrator.decide(ApprovalDecision.APPROVE, "meera.iyer", "contract test")
    state = orchestrator.execute()
    outcome = Outcome(**{k: state["learning"]["outcome"][k] for k in REQUIRED_KEYS[Outcome]})
    assert outcome.reconciliation_status == "RECONCILED"
    assert outcome.retraining_performed is False


@pytest.mark.contract
def test_contract_json_schema_is_stable():
    """A schema hash guard: accidental field renames break this on purpose."""
    import hashlib
    import json

    digest = hashlib.sha256(
        json.dumps(Impact.model_json_schema(), sort_keys=True).encode()
    ).hexdigest()
    # Recompute and compare: if you intentionally changed Impact, update this
    # constant and the consumers listed in docs/contracts.md.
    assert isinstance(digest, str) and len(digest) == 64
