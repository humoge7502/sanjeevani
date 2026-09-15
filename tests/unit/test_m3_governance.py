"""M3 unit tests: policy engine, approval machine, SAP mocks, ledger, learning."""

from __future__ import annotations

import pytest

from backend.audit.ledger import DecisionLedger
from backend.contracts import RecoveryPlan
from backend.governance import policy_engine
from backend.governance.approval import (
    ALLOWED,
    ApprovalState,
    GovernanceError,
    PlanGovernance,
    resolve_actor,
    role_satisfies,
)
from backend.learning.reconcile import reconcile
from backend.m2.recovery_plan_provider import FixtureRecoveryPlanProvider
from backend.sap.mocks import AribaMock, IbpMock, SapMockError, TmMock


def _plan(**overrides) -> RecoveryPlan:
    base = {
        "plan_id": "PLAN-TEST",
        "strategy": "REROUTE_MUMBAI_AIR",
        "cost": 184000.0,
        "service_level": 0.965,
        "resilience_score": 0.81,
        "temperature_risk": 0.22,
        "recovery_time_hours": 18.0,
        "impacted_skus": ["BIO-002"],
        "impacted_lanes": ["LANE-MUM-BIO-EU"],
    }
    base.update(overrides)
    return RecoveryPlan(**base)


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_rulebook_is_fully_implemented(network):
    """An executable rule with no evaluator must not exist (it would fail closed)."""
    catalogue = policy_engine.rule_catalogue()
    assert len(catalogue) == 10
    assert all(r["implemented"] for r in catalogue), [
        r["id"] for r in catalogue if not r["implemented"]
    ]


@pytest.mark.unit
def test_recommended_fixture_plan_passes_policy(network):
    plan = FixtureRecoveryPlanProvider().plan_for_event("EVT-REDSEA-COLDCHAIN")
    evaluation = policy_engine.evaluate(
        plan,
        network=network,
        excursed_shipments=["SHIP-001"],
        temperature_sensitive=True,
        critical_scope=True,
    )
    assert not evaluation.blocking
    assert evaluation.risk_tier == "L3"
    assert evaluation.required_role == "supply_planning_head"
    assert evaluation.approval_required is True


@pytest.mark.unit
def test_wait_strategy_is_blocked_by_cold_chain_and_service_rules(network):
    plan = FixtureRecoveryPlanProvider().plan_by_id("PLAN-002")
    evaluation = policy_engine.evaluate(
        plan,
        network=network,
        excursed_shipments=["SHIP-001"],
        temperature_sensitive=True,
        critical_scope=True,
    )
    assert evaluation.blocking
    failed = {c.rule_id for c in evaluation.failed}
    assert "POL-TEMP-001" in failed
    assert "POL-SERVICE-001" in failed
    assert "POL-TEMP-002" in failed


@pytest.mark.unit
def test_every_check_carries_a_provenance_label_and_config_key(network):
    plan = FixtureRecoveryPlanProvider().plan_for_event("EVT-REDSEA-COLDCHAIN")
    evaluation = policy_engine.evaluate(
        plan,
        network=network,
        excursed_shipments=["SHIP-001"],
        temperature_sensitive=True,
        critical_scope=True,
    )
    for check in evaluation.passed + evaluation.failed + evaluation.warned:
        payload = check.as_dict()
        assert payload["provenance_label"], check.rule_id
        assert payload["evidence"]["config_key"].startswith("config/policies.yaml")
        assert payload["message"]


@pytest.mark.unit
def test_cost_band_escalates_with_cost(network):
    cheap = policy_engine.classify_tier(_plan(cost=1000.0), temperature_sensitive=False)
    mid = policy_engine.classify_tier(_plan(cost=30000.0), temperature_sensitive=False)
    dear = policy_engine.classify_tier(_plan(cost=184000.0), temperature_sensitive=False)
    extreme = policy_engine.classify_tier(_plan(cost=900000.0), temperature_sensitive=False)
    assert [cheap["risk_tier"], mid["risk_tier"], dear["risk_tier"], extreme["risk_tier"]] == [
        "L1", "L2", "L3", "L4",
    ]
    assert cheap["approval_required"] is False
    assert extreme["required_role"] == "coo"


@pytest.mark.unit
def test_compliance_sensitivity_escalates_a_cheap_plan(network):
    tier = policy_engine.classify_tier(_plan(cost=1000.0), temperature_sensitive=True)
    assert tier["risk_tier"] == "L3"
    assert tier["escalated_for_compliance_sensitivity"] is True


@pytest.mark.unit
def test_budget_rule_blocks_a_plan_over_its_tier_cap(network):
    plan = _plan(cost=200000.0)  # L3 cap is 250k -> passes
    evaluation = policy_engine.evaluate(
        plan, network=network, excursed_shipments=[], temperature_sensitive=False, critical_scope=False
    )
    assert not [c for c in evaluation.failed if c.rule_id == "POL-BUDGET-001"]


@pytest.mark.unit
def test_unevidenced_plan_is_blocked(network):
    plan = _plan(impacted_skus=[], impacted_lanes=[])
    evaluation = policy_engine.evaluate(
        plan, network=network, excursed_shipments=[], temperature_sensitive=False, critical_scope=False
    )
    assert [c.rule_id for c in evaluation.failed] == ["POL-EVIDENCE-001"]


@pytest.mark.unit
def test_unregistered_rule_fails_closed(network, monkeypatch):
    """A rule added to config without an evaluator must not silently pass."""
    from backend.config import policies as policies_cfg

    doc = policies_cfg()
    doc["rules"].append(
        {
            "id": "POL-NOT-IMPLEMENTED",
            "name": "Ghost rule",
            "dimension": "test",
            "provenance": "configured_policy",
            "severity": "FAIL",
            "description": "no evaluator registered",
            "params": {},
        }
    )
    evaluation = policy_engine.evaluate(
        _plan(),
        network=network,
        excursed_shipments=[],
        temperature_sensitive=False,
        critical_scope=False,
    )
    assert evaluation.blocking
    assert [c.rule_id for c in evaluation.errored] == ["POL-NOT-IMPLEMENTED"]
    doc["rules"].pop()


@pytest.mark.unit
def test_policy_engine_fails_closed_on_evaluator_exception(network, monkeypatch):
    def boom(_plan, _params, _ctx):
        raise RuntimeError("evaluator exploded")

    monkeypatch.setitem(policy_engine.EVALUATORS, "POL-BUDGET-001", boom)
    evaluation = policy_engine.evaluate(
        _plan(),
        network=network,
        excursed_shipments=[],
        temperature_sensitive=False,
        critical_scope=False,
    )
    assert evaluation.blocking
    assert "POL-BUDGET-001" in {c.rule_id for c in evaluation.errored}


# ---------------------------------------------------------------------------
# Approval state machine
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_executing_is_reachable_only_from_approved():
    inbound = [s for s, targets in ALLOWED.items() if ApprovalState.EXECUTING in targets]
    assert inbound == [ApprovalState.APPROVED]


@pytest.mark.unit
def test_illegal_transition_raises():
    gov = PlanGovernance(plan_id="P")
    with pytest.raises(GovernanceError) as exc:
        gov._transition(ApprovalState.EXECUTED, "test", None, "skip everything")
    assert exc.value.code == "ILLEGAL_TRANSITION"
    assert "APPROVED" in exc.value.detail["allowed"] or "POLICY_CHECKING" in exc.value.detail["allowed"]


@pytest.mark.unit
def test_role_matching_is_exact_not_seniority_based():
    assert role_satisfies("supply_planning_head", "supply_planning_head") is True
    assert role_satisfies("planner", "supply_planning_head") is False
    # A COO does NOT implicitly satisfy a planner requirement: the model is narrow
    # and refuses, which is the safe direction for an authority check.
    assert role_satisfies("coo", "planner") is False
    assert role_satisfies(None, None) is True
    assert role_satisfies(None, "coo") is False


@pytest.mark.unit
def test_unknown_actor_resolves_to_no_role():
    actor = resolve_actor("some.random.person")
    assert actor["role"] is None
    assert role_satisfies(actor["role"], "coo") is False


@pytest.mark.unit
def test_known_approvers_resolve():
    assert resolve_actor("meera.iyer")["role"] == "supply_planning_head"
    assert resolve_actor("Meera")["role"] == "supply_planning_head"
    assert resolve_actor("rajan.kulkarni")["role"] == "coo"


@pytest.mark.unit
def test_state_reports_can_execute_only_when_approved():
    gov = PlanGovernance(plan_id="P")
    assert gov.as_dict()["can_execute"] is False
    gov.state = ApprovalState.APPROVED
    assert gov.as_dict()["can_execute"] is True
    gov.state = ApprovalState.EXECUTED
    assert gov.as_dict()["can_execute"] is False


# ---------------------------------------------------------------------------
# SAP mocks
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ibp_mock_is_deterministic():
    a, b = IbpMock(), IbpMock()
    payload = {"plan_id": "PLAN-001", "strategy": "REROUTE_MUMBAI_AIR", "cost": 184000.0}
    first = a.create_scenario(payload, "CORR-1", "KEY-1")
    second = b.create_scenario(payload, "CORR-1", "KEY-1")
    assert first.response == second.response


@pytest.mark.unit
def test_ibp_mock_idempotency_replays_and_does_not_double_post():
    mock = IbpMock()
    payload = {"plan_id": "PLAN-001", "strategy": "S", "cost": 1.0}
    first = mock.create_scenario(payload, "CORR-1", "SAME-KEY")
    second = mock.create_scenario(payload, "CORR-1", "SAME-KEY")
    assert second.status == "REPLAYED"
    assert second.response == first.response
    assert len(mock.calls) == 2
    # Only one unique document was ever created.
    assert len({c.response["d"]["PlanningScenarioID"] for c in mock.calls}) == 1


@pytest.mark.unit
def test_ibp_mock_rejects_negative_cost():
    with pytest.raises(SapMockError) as exc:
        IbpMock().create_scenario(
            {"plan_id": "P", "strategy": "S", "cost": -5.0}, "CORR", "K"
        )
    assert exc.value.code == "INVALID_KEY_FIGURE"


@pytest.mark.unit
def test_tm_mock_rejects_bad_transport_mode():
    with pytest.raises(SapMockError) as exc:
        TmMock().rebook_freight_order({"plan_id": "P", "mode": "TELEPORT"}, "CORR", "K")
    assert exc.value.code == "INVALID_MODE"


@pytest.mark.unit
def test_ariba_mock_enforces_the_documented_batch_limit():
    with pytest.raises(SapMockError) as exc:
        AribaMock().request_supplier_risk_scores(
            {"plan_id": "P", "supplier_ids": [f"S{i}" for i in range(501)]}, "CORR", "K"
        )
    assert exc.value.code == "BATCH_TOO_LARGE"


@pytest.mark.unit
def test_ariba_mock_agrees_with_the_seeded_network_risk_scores(network):
    """A mock that contradicted the graph would be indefensible in Q&A."""
    record = AribaMock().request_supplier_risk_scores(
        {"plan_id": "P", "supplier_ids": ["SUP-EU-A", "SUP-CHN-A"]}, "CORR", "K"
    )
    by_id = {r["SupplierID"]: r for r in record.response["d"]["Results"]}
    assert by_id["SUP-EU-A"]["RiskExposureScore"] == network.nodes["SUP-EU-A"].risk_score
    assert by_id["SUP-EU-A"]["ScoreOrigin"] == "network_model"
    assert by_id["SUP-CHN-A"]["RiskExposureScore"] == network.nodes["SUP-CHN-A"].risk_score


@pytest.mark.unit
def test_all_sap_responses_disclose_the_mock_boundary():
    from backend.sap.mocks import surface

    surface.reset()
    record = surface.ibp.create_scenario(
        {"plan_id": "P", "strategy": "S", "cost": 1.0}, "CORR", "K"
    )
    description = surface.describe()
    assert description["real_integration"] is False
    assert description["surfaces"]
    assert record.response["d"]["SourcePlanID"] == "P"


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_ledger_is_append_only_and_chained():
    ledger = DecisionLedger(path=None)
    ledger.reset(write_file=False)
    ledger.append("SIGNAL_RECEIVED", "NEWS", "ingest")
    ledger.append("EVENT_VERIFIED", "A2", "verify")
    records = ledger.records()
    assert [r["seq"] for r in records] == [1, 2]
    assert records[0]["prev_hash"] == "GENESIS"
    assert records[1]["prev_hash"] == records[0]["hash"]
    assert ledger.verify_chain()["intact"] is True


@pytest.mark.unit
def test_ledger_exposes_no_mutation_api():
    ledger = DecisionLedger(path=None)
    for forbidden in ("update", "delete", "remove", "edit", "set"):
        assert not hasattr(ledger, forbidden), f"ledger exposes a mutation method: {forbidden}"


@pytest.mark.unit
def test_ledger_detects_tampering_with_a_record_body():
    ledger = DecisionLedger(path=None)
    ledger.reset(write_file=False)
    ledger.append("SIGNAL_RECEIVED", "NEWS", "ingest")
    ledger.append("EVENT_VERIFIED", "A2", "verify")
    # Simulate an out-of-band edit to the stored record.
    ledger._records[0].detail["tampered"] = True
    report = ledger.verify_chain()
    assert report["intact"] is False
    assert any(i["issue"] == "hash_mismatch" for i in report["issues"])


@pytest.mark.unit
def test_ledger_declares_its_own_limitations():
    report = DecisionLedger(path=None).verify_chain()
    assert "Does NOT protect" in report["scope"]


@pytest.mark.unit
def test_ledger_rehydrates_an_existing_file_and_keeps_the_chain_continuous(tmp_path):
    """A restart must not silently restart the chain.

    Regression test for a real deployment defect: the singleton was empty on
    process start while the file on the mounted volume still held a chain, so the
    next append chained from GENESIS and produced a file with two disconnected
    chains -- in a ledger whose only claim is continuity.
    """
    path = tmp_path / "audit_ledger.jsonl"

    first = DecisionLedger(path=path)
    first.reset(write_file=True)
    first.append("SIGNAL_RECEIVED", "NEWS", "ingest")
    first.append("EVENT_VERIFIED", "A2", "verify")
    head_before = first.records()[-1]["hash"]

    # Simulate a restart: a brand-new instance over the same persisted file.
    restarted = DecisionLedger(path=path)
    assert len(restarted) == 2, "existing records were not rehydrated"
    assert restarted.health()["rehydrated_from_disk"] is True

    restarted.append("SCENARIO_GENERATED", "A2", "scenario")
    records = restarted.records()

    assert [r["seq"] for r in records] == [1, 2, 3], "sequence did not continue"
    assert records[2]["prev_hash"] == head_before, "chain head was not carried over"
    assert restarted.verify_chain()["intact"] is True


@pytest.mark.unit
def test_reset_establishes_the_baseline_and_blocks_rehydration(tmp_path):
    """reset() must win over lazy loading, or it would read the old file back."""
    path = tmp_path / "audit_ledger.jsonl"

    seeded = DecisionLedger(path=path)
    seeded.reset(write_file=True)
    seeded.append("SIGNAL_RECEIVED", "NEWS", "ingest")

    fresh = DecisionLedger(path=path)
    fresh.reset(write_file=True)
    fresh.append("SIGNAL_RECEIVED", "NEWS", "ingest")

    records = fresh.records()
    assert len(records) == 1
    assert records[0]["seq"] == 1
    assert records[0]["prev_hash"] == "GENESIS"


@pytest.mark.unit
def test_truncated_trailing_line_is_skipped_not_fatal(tmp_path):
    """A process killed mid-append must not prevent the next one from starting."""
    path = tmp_path / "audit_ledger.jsonl"

    seeded = DecisionLedger(path=path)
    seeded.reset(write_file=True)
    seeded.append("SIGNAL_RECEIVED", "NEWS", "ingest")

    # Append a half-written record, exactly as a hard kill would leave it.
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"seq": 2, "stage": "EVENT_VERI')

    restarted = DecisionLedger(path=path)
    health = restarted.health()
    assert health["records"] == 1, "the intact prefix should still load"
    assert health["skipped_malformed_lines"] == 1, "the partial line should be reported"

    # And the ledger stays usable afterwards.
    restarted.append("EVENT_VERIFIED", "A2", "verify")
    assert [r["seq"] for r in restarted.records()] == [1, 2]
    assert restarted.verify_chain()["intact"] is True


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_reconciliation_never_claims_retraining():
    plan = _plan(plan_id="PLAN-001")
    result = reconcile(plan, {"revenue_at_risk": 3908933.39, "stockout_probability": 0.6559})
    assert result.outcome.retraining_performed is False
    assert "No model was retrained" in result.outcome.learning_note
    assert all(s["auto_applied"] is False for s in result.calibration_signals)
    assert all(s["requires_human_approval"] for s in result.calibration_signals)


@pytest.mark.unit
def test_reconciliation_without_feedback_reports_pending_not_invented_values():
    plan = _plan(plan_id="PLAN-UNKNOWN-999")
    result = reconcile(plan, {})
    assert result.outcome.reconciliation_status == "PENDING"
    assert result.outcome.actual == {}
    assert result.outcome.delta == {}
    assert "nothing is assumed" in result.outcome.learning_note


@pytest.mark.unit
def test_delta_arithmetic_and_direction_conventions():
    plan = _plan(plan_id="PLAN-001")
    result = reconcile(plan, {"revenue_at_risk": 3908933.39})
    rows = {r["metric"]: r for r in result.scorecard["rows"]}
    cost = rows["recovery_cost_usd"]
    assert cost["predicted"] == 184000.0
    assert cost["actual"] == 191500.0
    assert cost["absolute_delta"] == pytest.approx(7500.0)
    assert cost["direction"] == "worse"  # higher cost is worse
    service = rows["service_level"]
    assert service["direction"] == "worse"  # lower service is worse


@pytest.mark.unit
def test_observation_feed_is_labelled_simulated():
    plan = _plan(plan_id="PLAN-001")
    result = reconcile(plan, {})
    assert result.observation_source["labelled_as"] == "simulated"
