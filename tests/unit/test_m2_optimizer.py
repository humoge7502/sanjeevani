"""Unit tests: M2 digital twin and recovery optimizer.

What is being protected here, in order of importance:

  1. The optimizer's answer is *verifiable*. The MILP and an independent
     exhaustive search must agree, or the test says so loudly.
  2. The optimizer is *deterministic*. Two runs must produce the same plan, or the
     audit chain it feeds stops meaning anything.
  3. The optimizer is *governable*. Its plan must satisfy every hard ceiling the
     rulebook enforces -- an optimizer that proposes plans governance always
     blocks is useless.
  4. The twin shares M1's physics. Where the twin re-uses A3's definitions it must
     agree with A3 exactly, or M2 would be solving a different world.
"""

from __future__ import annotations

import pytest

from backend.agents import impact as impact_mod
from backend.agents.pipeline import run_m1
from backend.contracts import RecoveryPlan
from backend.governance import policy_engine
from backend.graph.network import Network, corridor_remaining_fraction
from backend.m2.digital_twin import (
    RESILIENCE_WEIGHTS,
    ScenarioContext,
    build_catalogue,
    build_contexts,
    evaluate_plan,
    feasible_actions,
)
from backend.m2.optimizer import (
    derive_disruption,
    optimize_for_event,
    parse_horizon,
    solve,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def net() -> Network:
    return Network.load()


@pytest.fixture
def view(net: Network):
    return derive_disruption(net, event_id="EVT-001", scenario_id="SCN-EVT-001-10D")


@pytest.fixture
def contexts(net: Network, view):
    built, _scope = build_contexts(
        net,
        disrupted_lanes=list(view.disrupted_lanes),
        excursed_shipments=list(view.excursed_shipments),
    )
    return built


@pytest.fixture
def scenario(view):
    return ScenarioContext(
        scenario_id="SCN-TEST-10D",
        horizon_days=float(view.horizon_days),
        duration_days=view.duration_days,
        transit_delay_days=view.transit_delay_days,
        capacity_factor=view.capacity_factor,
    )


# ---------------------------------------------------------------------------
# The catalogue is well formed
# ---------------------------------------------------------------------------


def test_resilience_weights_sum_to_one():
    assert abs(sum(RESILIENCE_WEIGHTS.values()) - 1.0) < 1e-9


def test_catalogue_parses_and_is_versioned():
    catalogue = build_catalogue()
    assert catalogue.catalogue_version
    assert len(catalogue.actions) >= 5
    ids = {a.id for a in catalogue.actions}
    assert {"WAIT", "REROUTE_AIR_UPLIFT", "EMERGENCY_SOURCE"} <= ids
    for action in catalogue.actions:
        assert 0.0 <= action.corridor_exposure_factor <= 1.0
        assert 0.0 <= action.outage_factor <= 1.0
        assert 0.0 <= action.condemn_recovery_fraction <= 1.0
        assert action.recovery_hours > 0.0


def test_every_configured_strategy_name_is_covered_by_an_action():
    catalogue = build_catalogue()
    action_ids = {a.id for a in catalogue.actions}
    assert set(catalogue.strategy_names) <= action_ids


# ---------------------------------------------------------------------------
# The disruption view is derived, not guessed
# ---------------------------------------------------------------------------


def test_disruption_is_derived_from_the_seeded_library(view):
    assert view.blocked_nodes == ("PORT-SUEZ",)
    assert view.disrupted_lanes == ("LANE-MUM-BIO-EU",)
    assert view.excursed_shipments == ("SHIP-001",)
    assert view.duration_days == 10.0
    assert view.transit_delay_days == 12.0
    assert view.horizon_days == 10
    assert "seeded" in view.source


def test_horizon_is_read_from_the_scenario_id():
    assert parse_horizon("SCN-EVT-001-30D", 10) == 30
    assert parse_horizon("SCN-EVT-001-3D", 10) == 3
    assert parse_horizon(None, 10) == 10
    assert parse_horizon("NOT-A-HORIZON", 10) == 10


def test_scope_matches_a3_blast_radius(net, view, contexts):
    """M2 must plan against exactly the consignments A3 says are exposed."""
    m1 = run_m1(net)
    horizon = view.horizon_days
    primary = next(i for i in m1.impacts if i["horizon_days"] == horizon)
    assert sorted(c.shipment_id for c in contexts) == sorted(primary["affected_shipments"])


def test_scope_reasons_explain_membership(net, view):
    _ctx, reasons = build_contexts(
        net,
        disrupted_lanes=list(view.disrupted_lanes),
        excursed_shipments=list(view.excursed_shipments),
    )
    assert len(reasons) == 5
    assert any("cargo-damage" in r for r in reasons)


def test_empty_disruption_is_refused(net):
    """No affected consignments must raise, never invent a plan."""
    empty_view = derive_disruption(net, scenario_id="SCN-EVT-001-10D")
    contexts, _ = build_contexts(net, disrupted_lanes=[], excursed_shipments=[])
    assert contexts == []
    with pytest.raises(ValueError, match="nothing to optimize"):
        solve([], disruption=empty_view, event_id="EVT-001", scenario_id="SCN-EVT-001-10D")


# ---------------------------------------------------------------------------
# The twin shares M1's physics
# ---------------------------------------------------------------------------


def test_twin_agrees_with_a3_on_remaining_corridor_fraction(net):
    """A3 and the twin must not disagree about how exposed a consignment is."""
    for shipment in net.shipments.values():
        lane = net.lanes[shipment.lane_id]
        assert impact_mod._remaining_fraction(shipment, net) == pytest.approx(
            corridor_remaining_fraction(shipment, lane), abs=1e-12
        )


def test_wait_reproduces_a3_impact_for_the_hero_horizon(net, contexts, scenario):
    """The twin's 'do nothing' case must land on A3's own numbers.

    If the twin's baseline ever drifts from M1's impact model, every optimization
    above it is measured against a different world. This is the anchor.
    """
    catalogue = build_catalogue()
    assignment = {c.shipment_id: "WAIT" for c in contexts}
    outcome = evaluate_plan(contexts, assignment, catalogue, scenario)
    m1 = run_m1(net)
    primary = next(i for i in m1.impacts if i["horizon_days"] == 10)
    assert outcome.expected_unrecovered_usd == pytest.approx(
        primary["revenue_at_risk"], rel=2e-3
    )


def test_twin_is_deterministic(contexts, scenario):
    catalogue = build_catalogue()
    assignment = {c.shipment_id: "REROUTE_AIR_UPLIFT" for c in contexts}
    first = evaluate_plan(contexts, assignment, catalogue, scenario)
    second = evaluate_plan(contexts, assignment, catalogue, scenario)
    assert first == second


def test_air_uplift_strictly_beats_waiting(contexts, scenario):
    """The catalogue must encode the trade it claims to encode."""
    catalogue = build_catalogue()
    wait = evaluate_plan(
        contexts, {c.shipment_id: "WAIT" for c in contexts}, catalogue, scenario
    )
    air = evaluate_plan(
        contexts, {c.shipment_id: "REROUTE_AIR_UPLIFT" for c in contexts}, catalogue, scenario
    )
    assert air.total_cost_usd > wait.total_cost_usd
    assert air.expected_unrecovered_usd < wait.expected_unrecovered_usd
    assert air.service_level > wait.service_level
    assert air.temperature_risk < wait.temperature_risk


# ---------------------------------------------------------------------------
# Feasibility is a network fact
# ---------------------------------------------------------------------------


def test_emergency_source_is_offered_only_when_a_source_exists(net, contexts):
    catalogue = build_catalogue()
    emergency = catalogue.by_id("EMERGENCY_SOURCE")
    for ctx in contexts:
        offered = emergency in feasible_actions(ctx, catalogue)
        assert offered == ctx.alternate_source_available


def test_ambient_cargo_has_no_temperature_risk(net, contexts, scenario):
    """A generics consignment on an ambient lane is not a cold-chain risk."""
    catalogue = build_catalogue()
    ambient = [c for c in contexts if c.temp_class != "2_8C"]
    for ctx in ambient:
        outcome = evaluate_plan(
            [ctx], {ctx.shipment_id: "WAIT"}, catalogue, scenario
        )
        assert outcome.by_consignment[0].temperature_risk == 0.0


# ---------------------------------------------------------------------------
# The solve
# ---------------------------------------------------------------------------


def test_milp_agrees_with_exhaustive_enumeration(contexts):
    result = solve(contexts, event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    verification = result.trace["verification"]
    assert verification["exhaustive_search_performed"] is True
    assert verification["milp_available"] is True, verification["milp_note"]
    assert verification["milp_status"] == "Optimal"
    assert verification["milp_agrees_with_exhaustive"] is True, (
        "The MILP and the exhaustive search disagree. One of them is wrong; "
        f"milp={verification['milp_choices']} "
        f"chosen={result.trace['selected_plan']['choices']}"
    )


def test_optimize_is_deterministic():
    first = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    second = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    assert first.recommended.model_dump() == second.recommended.model_dump()
    assert first.trace["selected_plan"] == second.trace["selected_plan"]


def test_recommended_plan_satisfies_every_hard_constraint(contexts):
    result = solve(contexts, event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    assert result.trace["constraints"]["violations"] == []
    plan = result.recommended
    catalogue = build_catalogue()
    assert plan.cost <= catalogue.max_budget_usd
    assert plan.service_level >= catalogue.min_service_level
    assert plan.temperature_risk <= catalogue.max_temperature_risk
    assert plan.recovery_time_hours <= catalogue.max_recovery_hours


def test_recommended_plan_passes_the_m3_rulebook(net, contexts, view):
    """The optimizer must not propose a plan its own governance will block."""
    result = solve(contexts, event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    from backend.governance.compliance import build_context

    ctx = build_context(net, result.recommended, list(run_m1(net).events))
    evaluation = policy_engine.evaluate(
        result.recommended,
        network=net,
        excursed_shipments=ctx["excursed_shipments"],
        temperature_sensitive=ctx["temperature_sensitive"],
        critical_scope=ctx["critical_scope"],
    )
    assert evaluation.failed == [], [c.message for c in evaluation.failed]
    assert evaluation.errored == []


def test_recommended_strategy_is_an_accepted_corrective_action(contexts):
    """POL-TEMP-002 lists the acceptable strategies; the optimizer must land in it."""
    result = solve(contexts, event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    from backend.config import policies as policies_cfg

    rule = next(r for r in policies_cfg()["rules"] if r["id"] == "POL-TEMP-002")
    assert result.recommended.strategy in rule["params"]["acceptable_strategies"]


def test_plan_is_contract_conformant(contexts):
    result = solve(contexts, event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    plan = result.recommended
    assert isinstance(plan, RecoveryPlan)
    assert plan.source == "optimizer"
    assert plan.compliance_status == "PENDING"
    assert plan.impacted_skus and plan.impacted_lanes
    assert plan.rationale
    # Re-validating the dump proves no field escaped the frozen schema.
    RecoveryPlan.model_validate(plan.model_dump())


def test_plan_id_is_stable_and_content_derived():
    a = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    b = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    assert a.recommended.plan_id == b.recommended.plan_id
    assert a.recommended.plan_id.startswith("PLAN-OPT-10D-")


def test_ranked_plans_are_distinct_strategies_with_one_recommended(contexts):
    result = solve(contexts, event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    strategies = [p.strategy for p in result.ranked]
    assert len(strategies) == len(set(strategies))
    recommended_flags = [
        alt["recommended"]
        for alt in result.recommended.alternatives
    ]
    assert recommended_flags.count(True) == 1


def test_pareto_frontier_is_non_dominated(contexts):
    result = solve(contexts, event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    frontier = result.trace["pareto_frontier"]
    assert frontier
    best_loss = float("inf")
    for point in sorted(frontier, key=lambda p: p["cost_usd"]):
        assert point["expected_unrecovered_usd"] < best_loss
        best_loss = point["expected_unrecovered_usd"]


def test_no_uniform_policy_is_admissible_and_the_optimum_is_a_mix():
    """The strongest evidence that the optimizer does real work.

    Every single-action policy breaches at least one hard ceiling. Waiting and
    quarantine lose the service floor; air uplift and emergency sourcing breach
    the budget cap; the Cape re-route adds delay and buys nothing inside the
    horizon; expediting cannot cover the gap. The only admissible answer is a
    per-consignment mix -- which is exactly what a planner cannot compute by hand
    and what the optimizer returns. If a uniform policy were ever admissible, the
    optimizer would have to return it, so this test also guards against the mix
    being decorative.
    """
    result = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    uniform = result.trace["counterfactuals"]
    assert uniform
    assert all(not c["executable"] for c in uniform), (
        "a uniform policy became admissible; re-check the catalogue, the "
        "constraints, or whether the optimizer is still needed"
    )
    assert result.trace["constraints"]["violations"] == []
    actions_used = set(result.trace["selected_plan"]["choices"].values())
    assert len(actions_used) > 1, "the optimal plan is uniform after all"


def test_every_uniform_policy_violation_names_its_ceiling():
    """A violation must be legible: which ceiling, with which numbers."""
    result = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    for policy in result.trace["counterfactuals"]:
        if policy["executable"]:
            assert policy["violations"] == []
            continue
        assert policy["violations"]
        assert any(
            keyword in v
            for v in policy["violations"]
            for keyword in ("budget", "service", "cold chain", "time")
        ), policy["violations"]


def test_optimized_plan_beats_the_best_admissible_frontier_point():
    """Self-consistency: the chosen plan is the best of the ones it considered."""
    result = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    selected = result.trace["selected_plan"]["outcome"]
    chosen_impact = selected["total_cost_usd"] + selected["expected_unrecovered_usd"]
    admissible = [p for p in result.trace["pareto_frontier"] if p["feasible"]]
    assert admissible
    best_admissible = min(
        p["cost_usd"] + p["expected_unrecovered_usd"] for p in admissible
    )
    assert chosen_impact <= best_admissible + 1e-6


def test_cape_reroute_is_worse_than_doing_nothing_in_a_10_day_window():
    """A real finding the product should surface, not a quirk.

    The Cape re-route adds three days of transit. Because the closure duration
    (10d) already equals the horizon (10d), the extra delay buys no additional
    coverage: the consignment still suffers the full outage, and the re-route
    costs money on top. So it is strictly dominated by waiting, and the optimizer
    must never pick it. That is exactly the kind of counter-intuitive, arithmetic
    -backed conclusion a planner cannot get from a vendor claim.
    """
    result = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    impacts = {
        c["uniform_policy"]: c["total_economic_impact_usd"]
        for c in result.trace["counterfactuals"]
    }
    assert impacts["REROUTE_CAPE"] > impacts["WAIT"]
    selected = result.trace["selected_plan"]["dominant_action"]
    assert selected != "REROUTE_CAPE"


def test_inaction_is_never_the_optimum_when_cargo_is_excursed():
    """The optimizer must not recommend 'do nothing' for damaged biologics."""
    result = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    assert result.recommended.strategy != "WAIT"
    assert "WAIT" not in result.trace["selected_plan"]["choices"].values()


def test_evidence_trace_is_complete(contexts):
    result = solve(contexts, event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    trace = result.trace
    for key in (
        "method",
        "labelled_as",
        "solver",
        "catalogue_version",
        "disruption",
        "scenario",
        "decision_space",
        "objective",
        "constraints",
        "verification",
        "selected_plan",
        "pareto_frontier",
    ):
        assert key in trace, f"optimizer trace is missing {key}"
    # The decision space is the product of the per-consignment option counts.
    # It is NOT 6**5: two consignments cannot take EXPEDITE_FROM_HUB because their
    # hub does not hold enough stock for them. See tests/unit/test_m2_inventory.py.
    options = trace["decision_space"]["actions_considered"]
    expected = 1
    for opts in options.values():
        expected *= len(opts)
    assert trace["decision_space"]["combinations"] == expected == 5400
    assert trace["decision_space"]["actions_considered"]
    assert trace["decision_space"]["hub_ceiling_enforced"] is True
