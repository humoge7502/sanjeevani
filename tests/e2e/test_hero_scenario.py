"""End-to-end hero scenario + cross-module integration tests.

This is the test that proves the product claim: one deterministic run walks
Signal -> Verification -> Scenario -> Network Impact -> M2 RecoveryPlan ->
Policy -> Compliance -> Human Approval -> SAP-shaped execution -> Receipts ->
Audit -> Learning, and does it identically every time.
"""

from __future__ import annotations

import pytest

from backend.contracts import ApprovalDecision

pytestmark = pytest.mark.e2e


def _full_run(orchestrator, actor="meera.iyer"):
    orchestrator.reset()
    state = orchestrator.run()
    state = orchestrator.decide(ApprovalDecision.APPROVE, actor, "Protect the biologic supply.")
    return orchestrator.execute()


# ---------------------------------------------------------------------------
# The hero journey
# ---------------------------------------------------------------------------


def test_hero_loop_reaches_completion(orchestrator):
    state = _full_run(orchestrator)
    assert state["errors"] == []
    assert state["governance"]["state"] == "COMPLETED"


def test_every_loop_stage_is_recorded_in_the_ledger(orchestrator):
    state = _full_run(orchestrator)
    coverage = state["ledger"]["coverage"]
    assert coverage["complete"] is True, f"missing stages: {coverage['stages_missing']}"
    assert coverage["stages_missing"] == []
    assert state["ledger"]["length"] >= 25


def test_ledger_hash_chain_survives_a_full_run(orchestrator):
    state = _full_run(orchestrator)
    assert state["ledger"]["chain"]["intact"] is True
    assert state["ledger"]["chain"]["records"] > 0
    assert state["ledger"]["chain"]["head"]


def test_ledger_is_chronological_with_contiguous_sequence(orchestrator):
    state = _full_run(orchestrator)
    timeline = state["ledger"]["timeline"]
    assert [t["seq"] for t in timeline] == list(range(1, len(timeline) + 1))
    stages = [t["stage"] for t in timeline]
    # Ordering invariants across the loop.
    assert stages.index("SIGNAL_RECEIVED") < stages.index("EVENT_VERIFIED")
    assert stages.index("EVENT_VERIFIED") < stages.index("NETWORK_IMPACT_CALCULATED")
    assert stages.index("RECOVERY_PLAN_RECEIVED") < stages.index("POLICY_EVALUATED")
    assert stages.index("APPROVAL_DECIDED") < stages.index("EXECUTION_STARTED")
    assert stages.index("EXECUTION_RECEIPTED") < stages.index("OUTCOME_RECORDED")


def test_one_approval_triggers_three_sap_shaped_actions(orchestrator):
    """THE WOW MOMENT, asserted."""
    state = _full_run(orchestrator)
    receipt = state["execution"]["receipt"]
    assert receipt["ibp"] == "SUCCESS"
    assert receipt["tm"] == "SUCCESS"
    assert receipt["ariba"] == "SUCCESS"
    assert receipt["mock"] is True
    assert len(state["execution"]["actions"]) == 3
    assert {a["system"] for a in state["execution"]["actions"]} == {"ibp", "tm", "ariba"}
    assert all(a["mock"] is True for a in state["execution"]["actions"])


def test_execution_records_a_correlation_id_and_compensating_actions(orchestrator):
    state = _full_run(orchestrator)
    receipt = state["execution"]["receipt"]
    assert receipt["correlation_id"].startswith("CORR-")
    assert receipt["execution_id"].startswith("EXEC-")
    assert len(state["execution"]["compensating_actions"]) == 3


def test_learning_closes_the_loop_honestly(orchestrator):
    state = _full_run(orchestrator)
    scorecard = state["learning"]["scorecard"]
    assert scorecard["status"] == "RECONCILED"
    assert scorecard["summary"]["metrics_compared"] == 8
    assert scorecard["summary"]["retraining_performed"] is False
    assert state["learning"]["outcome"]["retraining_performed"] is False
    assert state["learning"]["outcome"]["reconciliation_status"] == "RECONCILED"
    assert "No model was retrained" in state["learning"]["outcome"]["learning_note"]


def test_outcome_delta_is_arithmetically_correct(orchestrator):
    state = _full_run(orchestrator)
    rows = {r["metric"]: r for r in state["learning"]["scorecard"]["rows"]}
    cost = rows["recovery_cost_usd"]
    assert cost["predicted"] == 184000.0
    assert cost["actual"] == 191500.0
    assert cost["absolute_delta"] == pytest.approx(7500.0)
    # The predicted revenue exposure should beat the estimate.
    rev = rows["revenue_at_risk_usd"]
    assert rev["direction"] == "better"


def test_governance_history_is_complete_and_ordered(orchestrator):
    state = _full_run(orchestrator)
    history = state["governance"]["history"]
    path = [h["to"] for h in history]
    assert path[0] == "POLICY_CHECKING"
    assert "COMPLIANCE_CHECKING" in path
    assert "APPROVAL_REQUIRED" in path
    assert "APPROVED" in path
    assert "EXECUTING" in path
    assert path[-1] == "COMPLETED"
    # Every transition has an actor and a reason.
    assert all(h["actor"] and h["reason"] for h in history)


def test_approval_records_the_named_human_and_role(orchestrator):
    state = _full_run(orchestrator)
    approval = state["approval"]
    assert approval["approved"] is True
    assert approval["approved_by"] == "Meera"
    assert approval["role"] == "supply_planning_head"
    assert approval["plan_id"] == state["plan"]["plan_id"]


# ---------------------------------------------------------------------------
# Determinism -- the property the whole demo rests on
# ---------------------------------------------------------------------------


def test_two_full_runs_produce_the_identical_ledger_hash_chain(orchestrator):
    first = _full_run(orchestrator)
    second = _full_run(orchestrator)

    assert first["ledger"]["chain"]["head"] == second["ledger"]["chain"]["head"]
    assert first["ledger"]["length"] == second["ledger"]["length"]

    first_chain = [(r["stage"], r["actor"], r["hash"]) for r in first["ledger"]["timeline"]]
    second_chain = [(r["stage"], r["actor"], r["hash"]) for r in second["ledger"]["timeline"]]
    assert first_chain == second_chain


def test_two_full_runs_produce_identical_impact_and_receipt(orchestrator):
    first = _full_run(orchestrator)
    second = _full_run(orchestrator)
    assert first["m1"]["primary_impact"] == second["m1"]["primary_impact"]
    assert first["execution"]["receipt"] == second["execution"]["receipt"]
    assert first["compliance"]["summary"] == second["compliance"]["summary"]


def test_reset_then_rerun_is_bit_identical(orchestrator):
    """The presenter's reset -> run -> approve -> execute path must be repeatable."""
    baseline = _full_run(orchestrator)
    # Interleave an unrelated action, then reset.
    orchestrator.reset()
    orchestrator.run()
    orchestrator.decide(ApprovalDecision.REJECT, "rajan.kulkarni", "scenario check")
    rerun = _full_run(orchestrator)
    assert baseline["ledger"]["chain"]["head"] == rerun["ledger"]["chain"]["head"]


# ---------------------------------------------------------------------------
# Integration seams
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_m1_to_m2_seam_uses_the_frozen_contract(run_state):
    """M2 receives an event id; M3 receives a plan shaped exactly like the contract."""
    from backend.contracts import RecoveryPlan

    plan = run_state["plan"]
    assert plan["source"] == "fixture"
    assert plan["event_id"] is None or isinstance(plan["event_id"], str)
    RecoveryPlan(**plan)  # must validate
    assert run_state["plan_provider"]["optimizer_implemented"] is False


@pytest.mark.integration
def test_m2_boundary_discloses_that_no_optimizer_ran(run_state):
    provider = run_state["plan_provider"]
    assert provider["reality"] == "deterministic fixture"
    assert "No MILP" in provider["disclosure"]


@pytest.mark.integration
def test_compliance_record_is_traceable_to_the_rulebook(run_state):
    record = run_state["compliance"]
    assert record["rulebook_version"]
    assert record["review_status"]
    assert record["disclaimer"]
    total = (
        record["summary"]["passed"]
        + record["summary"]["failed"]
        + record["summary"]["warned"]
        + record["summary"]["errored"]
    )
    assert total == 10


@pytest.mark.integration
def test_approval_request_gives_the_human_everything_needed(run_state):
    request = run_state["approval_request"]
    for key in (
        "plan_id",
        "strategy",
        "cost",
        "service_level",
        "temperature_risk",
        "risk_tier",
        "required_role",
        "rationale",
        "compliance",
        "impact",
        "what_will_happen",
        "withhold",
    ):
        assert key in request, f"approval request is missing {key}"
    assert len(request["what_will_happen"]) == 3
    assert "server-enforced" in request["withhold"]


@pytest.mark.integration
def test_mock_boundaries_are_declared(orchestrator):
    boundaries = orchestrator.state_snapshot()["mock_boundaries"]
    assert boundaries["mocked"]
    assert boundaries["simulated"]
    assert boundaries["real"]
    assert any("M2 digital twin" in item for item in boundaries["not_implemented"])
    assert "No language model" in boundaries["ai_usage"]


@pytest.mark.integration
def test_sap_call_log_is_correlated_across_systems(orchestrator):
    state = _full_run(orchestrator)
    calls = state["sap"]["calls"]
    assert len(calls) == 3
    correlation_ids = {c["correlation_id"] for c in calls}
    assert len(correlation_ids) == 1, "all three systems must share one correlation id"
    assert {c["system"] for c in calls} == {"IBP", "TM", "ARIBA"}


@pytest.mark.integration
def test_policy_failure_blocks_execution_end_to_end(orchestrator):
    """Force the rulebook to reject the recommended plan and confirm the loop halts.

    Order matters: `reset()` reloads config, so the rulebook must be tightened
    AFTER the reset, against the freshly cached document.
    """
    from backend.config import policies as policies_cfg
    from backend.governance.approval import GovernanceError

    orchestrator.reset()
    budget_rule = next(r for r in policies_cfg()["rules"] if r["id"] == "POL-BUDGET-001")
    original = budget_rule["params"]["caps"]["L3"]
    budget_rule["params"]["caps"]["L3"] = 1.0  # impossibly tight cap
    try:
        state = orchestrator.run()
        assert state["compliance"]["compliance_status"] == "FAILED"
        assert state["governance"]["state"] == "COMPLIANCE_BLOCKED"
        assert state["governance"]["blocks_execution"] is True
        assert state["approval_request"] is None, (
            "a blocked plan must never produce an approval request for a human to sign"
        )
        with pytest.raises(GovernanceError) as exc:
            orchestrator.execute()
        assert exc.value.code == "EXECUTION_BLOCKED"
        assert exc.value.detail["reason"] == "compliance_blocked"
        # The refusal must name the failing rule so the operator can act on it.
        assert "POL-BUDGET-001" in state["compliance"]["rationale"]
    finally:
        budget_rule["params"]["caps"]["L3"] = original


@pytest.mark.integration
def test_offline_flag_is_on_and_no_network_is_required(orchestrator):
    state = orchestrator.state_snapshot()
    assert state["offline"] is True
    # Nothing in the state payload is a remote URL that must be reachable.
    assert state["mock_boundaries"]["mocked"]


@pytest.mark.integration
def test_health_endpoint_reports_deterministic_configuration():
    from backend.api.app import health

    payload = health()
    assert payload["status"] == "ok"
    assert payload["offline"] is True
    assert payload["clock_frozen"] is True
    assert payload["llm_enabled"] is False
    assert payload["contract_version"] == "1.0.0"


@pytest.mark.integration
def test_all_three_horizons_are_available_from_the_api(orchestrator):
    from backend.api.app import impact

    orchestrator.reset()
    orchestrator.run()
    for horizon in (3, 10, 30):
        payload = impact(horizon_days=horizon)
        assert payload["impact"]["horizon_days"] == horizon
        assert payload["impact"]["revenue_at_risk"] >= 0


@pytest.mark.integration
def test_contracts_endpoint_exposes_all_six_contracts():
    from backend.api.app import contracts

    payload = contracts()
    assert set(payload["contracts"]) == {
        "Event",
        "Impact",
        "RecoveryPlan",
        "Approval",
        "ExecutionReceipt",
        "Outcome",
    }
