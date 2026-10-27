"""M2 multi-echelon hub inventory: the constraint, and what it changes.

The test that matters here is not "does the loader parse the YAML". It is:

* a hub-sourced action is **unavailable** to a consignment whose hub cannot cover
  it, so a plan cannot recover a disruption out of stock that is not there;
* two consignments contending for one hub may not **collectively** exceed it, and
  the plan says so by name;
* the MILP and the exhaustive search agree **with the capacity constraint in
  place**, which is the only way to know the MILP reformulated it correctly;
* the ceiling is a stated, auditable number, not a silent allowance.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from backend.config import DATA_DIR
from backend.graph.network import Network
from backend.m2.digital_twin import (
    build_catalogue,
    build_contexts,
    evaluate_plan,
    feasible_actions,
)
from backend.m2.inventory import (
    HubInventory,
    hub_capacity_for,
    load_inventory,
    reset_inventory_cache,
    serving_hub,
)
from backend.m2.optimizer import (
    _hub_groups,
    _resource_violations,
    derive_disruption,
    hub_demand,
    optimize_for_event,
)

#: The promised positions in data/inventory/hub_inventory.yaml. Asserted rather
#: than looked up, so an accidental edit to the seed fails a test instead of
#: silently changing the optimizer's decision space.
WH_BOM = {"BIO-001": 6000, "BIO-002": 2600}
WH_DEL = {"GEN-001": 320000, "GEN-003": 180000, "GEN-007": 120000}
WH_FRA = {
    "BIO-001": 12000,
    "BIO-002": 3000,
    "GEN-001": 240000,
    "GEN-003": 90000,
    "GEN-007": 150000,
}


@pytest.fixture
def network():
    return Network.load()


@pytest.fixture
def inventory():
    return load_inventory()


# ---------------------------------------------------------------------------
# The seed
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_inventory_seed_exists_and_is_at_the_documented_path():
    assert (DATA_DIR / "inventory" / "hub_inventory.yaml").is_file()


@pytest.mark.unit
def test_seed_positions_are_what_the_tests_and_the_docs_claim(inventory):
    assert inventory.positions["WH-BOM"] == WH_BOM
    assert inventory.positions["WH-DEL"] == WH_DEL
    assert inventory.positions["WH-FRA"] == WH_FRA
    assert inventory.as_of_utc == "2026-09-30T06:00:00Z"
    assert inventory.source == "synthetic_seed"


@pytest.mark.unit
def test_an_absent_hub_or_sku_means_zero_and_says_so(inventory):
    assert inventory.available("WH-BOM", "GEN-001") == 0
    assert inventory.available("WH-NOPE", "BIO-001") == 0


@pytest.mark.unit
def test_inventory_cache_is_clearable(monkeypatch):
    first = load_inventory()
    reset_inventory_cache()
    second = load_inventory()
    assert first is not second
    assert first.positions == second.positions


@pytest.mark.unit
def test_a_position_is_an_integer_unit_count_not_a_float(inventory):
    for hub, positions in inventory.positions.items():
        for sku, units in positions.items():
            assert isinstance(units, int), f"{hub}/{sku} is {type(units).__name__}"


# ---------------------------------------------------------------------------
# Which hub a consignment draws from
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_serving_hub_is_the_next_warehouse_downstream(network):
    """Derived from the lane's own waypoints, not from a lookup table."""
    expected = {
        "SHIP-001": "WH-FRA",  # past Suez, next warehouse is Frankfurt
        "SHIP-002": "WH-BOM",  # sitting in the Mumbai hub
        "SHIP-003": "WH-BOM",  # at the plant, first warehouse downstream
        "SHIP-004": "WH-FRA",  # past JNPT
        "SHIP-005": "WH-FRA",  # already at Frankfurt
    }
    for shipment_id, hub in expected.items():
        assert serving_hub(network, network.shipments[shipment_id]) == hub


@pytest.mark.unit
def test_a_lane_without_a_downstream_warehouse_has_no_hub(network):
    """Absence is a fact, not a default.

    LANE-IND-A-API runs Hyderabad supplier -> Hyderabad plant, with no warehouse
    waypoint, so a consignment on it has nothing to expedite from. The seeded
    network has no such consignment, so one is constructed -- the point is that
    the function answers None rather than inventing a hub.
    """
    shipment = replace(
        network.shipments["SHIP-006"],
        id="SHIP-SYNTH",
        lane_id="LANE-IND-A-API",
        origin="SUP-IND-A",
        current_node="SUP-IND-A",
        destination="PLANT-HYD",
    )
    assert serving_hub(network, shipment) is None
    hub, units = hub_capacity_for(network, shipment)
    assert hub is None
    assert units == 0


@pytest.mark.unit
def test_every_hero_consignment_resolves_to_a_hub(network):
    """The complement of the test above: the seeded network does have hubs."""
    for shipment in network.shipments.values():
        assert serving_hub(network, shipment) is not None, shipment.id


@pytest.mark.unit
def test_hub_capacity_matches_the_seed_for_every_consignment(network, inventory):
    for shipment in network.shipments.values():
        hub, units = hub_capacity_for(network, shipment, inventory)
        if hub is not None:
            assert units == inventory.available(hub, shipment.sku_id)


# ---------------------------------------------------------------------------
# Per-consignment feasibility
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_consignments_are_carried_with_their_hub_and_ceiling(network):
    view = derive_disruption(network, scenario_id="SCN-EVT-001-10D", event_id="EVT-001")
    contexts, _ = build_contexts(
        network,
        disrupted_lanes=list(view.disrupted_lanes),
        excursed_shipments=list(view.excursed_shipments),
    )
    by_id = {c.shipment_id: c for c in contexts}
    assert by_id["SHIP-001"].serving_hub == "WH-FRA"
    assert by_id["SHIP-001"].hub_capacity_units == 3000
    assert by_id["SHIP-001"].units == 4200
    assert by_id["SHIP-003"].serving_hub == "WH-BOM"
    assert by_id["SHIP-003"].hub_capacity_units == 2600
    assert by_id["SHIP-003"].units == 2600


@pytest.mark.unit
def test_expedite_is_unavailable_when_the_hub_cannot_cover_the_consignment(network):
    view = derive_disruption(network, scenario_id="SCN-EVT-001-10D", event_id="EVT-001")
    contexts, _ = build_contexts(
        network,
        disrupted_lanes=list(view.disrupted_lanes),
        excursed_shipments=list(view.excursed_shipments),
    )
    catalogue = build_catalogue()
    by_id = {c.shipment_id: c for c in contexts}

    def can_expedite(shipment_id: str) -> bool:
        return any(
            a.id == "EXPEDITE_FROM_HUB" for a in feasible_actions(by_id[shipment_id], catalogue)
        )

    # WH-FRA holds 3,000 BIO-002 against SHIP-001's 4,200 -> no.
    assert can_expedite("SHIP-001") is False
    # WH-BOM holds 6,000 BIO-001 against SHIP-002's 9,000 -> no.
    assert can_expedite("SHIP-002") is False
    # WH-BOM holds exactly SHIP-003's 2,600 BIO-002 -> yes.
    assert can_expedite("SHIP-003") is True
    # WH-FRA covers either of the BIO-001 consignments on its own -> yes.
    assert can_expedite("SHIP-004") is True
    assert can_expedite("SHIP-005") is True


@pytest.mark.unit
def test_the_decision_space_shrank_because_of_stock_not_because_of_a_setting():
    """6x5x6x6x6 = 5400: only the two un-coverable consignments lost an option."""
    result = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    options = result.trace["decision_space"]["actions_considered"]
    assert len(options["SHIP-001"]) == 5
    assert len(options["SHIP-002"]) == 5
    assert all(len(options[s]) == 6 for s in ("SHIP-003", "SHIP-004", "SHIP-005"))
    assert result.trace["decision_space"]["combinations"] == 5400
    assert "EXPEDITE_FROM_HUB" not in options["SHIP-001"]
    assert "EXPEDITE_FROM_HUB" not in options["SHIP-002"]


# ---------------------------------------------------------------------------
# The shared, multi-echelon constraint
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hub_demand_aggregates_across_consignments_sharing_a_hub(network):
    view = derive_disruption(network, scenario_id="SCN-EVT-001-10D", event_id="EVT-001")
    contexts, _ = build_contexts(
        network,
        disrupted_lanes=list(view.disrupted_lanes),
        excursed_shipments=list(view.excursed_shipments),
    )
    catalogue = build_catalogue()
    choices = {
        "SHIP-001": "WAIT",
        "SHIP-002": "WAIT",
        "SHIP-003": "EXPEDITE_FROM_HUB",
        "SHIP-004": "EXPEDITE_FROM_HUB",
        "SHIP-005": "EXPEDITE_FROM_HUB",
    }
    demand = hub_demand(choices, contexts, catalogue)
    assert demand[("WH-BOM", "BIO-002")] == 2600
    assert demand[("WH-FRA", "BIO-001")] == 5400 + 7200


@pytest.mark.unit
def test_two_consignments_may_not_collectively_overdraw_one_hub(network):
    """The constraint a per-consignment check structurally cannot see.

    SHIP-004 and SHIP-005 are each individually executable from WH-FRA, and
    together they need 12,600 units against 12,000 on hand. The plan is rejected
    by name rather than by a silent reduction in benefit.
    """
    view = derive_disruption(network, scenario_id="SCN-EVT-001-10D", event_id="EVT-001")
    contexts, _ = build_contexts(
        network,
        disrupted_lanes=list(view.disrupted_lanes),
        excursed_shipments=list(view.excursed_shipments),
    )
    catalogue = build_catalogue()
    together = {
        "SHIP-001": "WAIT",
        "SHIP-002": "WAIT",
        "SHIP-003": "WAIT",
        "SHIP-004": "EXPEDITE_FROM_HUB",
        "SHIP-005": "EXPEDITE_FROM_HUB",
    }
    violations = _resource_violations(together, contexts, catalogue)
    assert violations == (
        "hub inventory: WH-FRA/BIO-001 drawn 12,600 units > 12,000 available",
    )

    # ...and exactly one of them alone is fine.
    alone = {**together, "SHIP-005": "WAIT"}
    assert _resource_violations(alone, contexts, catalogue) == ()
    alone = {**together, "SHIP-004": "WAIT"}
    assert _resource_violations(alone, contexts, catalogue) == ()


@pytest.mark.unit
def test_the_recommended_plan_stays_within_every_hub_ceiling():
    result = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    trace = result.trace
    assert trace["constraints"]["violations"] == []
    for row in trace["hub_inventory"]["contended"]:
        assert row["within_capacity"] is True, row
        assert row["demanded_units"] <= row["available_units"]


@pytest.mark.unit
def test_the_inventory_trace_names_both_sides_of_the_constraint():
    trace = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D").trace
    inventory = trace["hub_inventory"]
    assert inventory["source"] == "data/inventory/hub_inventory.yaml"
    assert inventory["as_of_utc"] == "2026-09-30T06:00:00Z"
    contended = {(r["hub_id"], r["sku_id"]): r for r in inventory["contended"]}
    assert contended[("WH-FRA", "BIO-001")]["available_units"] == 12000
    by_shipment = {c["shipment_id"]: c for c in inventory["consignments"]}
    assert by_shipment["SHIP-001"]["expeditable_from_hub"] is False
    assert by_shipment["SHIP-003"]["expeditable_from_hub"] is True


@pytest.mark.unit
def test_hub_groups_are_only_created_for_contended_hubs(network):
    view = derive_disruption(network, scenario_id="SCN-EVT-001-10D", event_id="EVT-001")
    contexts, _ = build_contexts(
        network,
        disrupted_lanes=list(view.disrupted_lanes),
        excursed_shipments=list(view.excursed_shipments),
    )
    catalogue = build_catalogue()
    options = {c.shipment_id: feasible_actions(c, catalogue) for c in contexts}
    groups = {(hub, sku) for hub, sku, _ceiling, _members in _hub_groups(contexts, options)}
    # SHIP-002 lost the option entirely, so WH-BOM/BIO-001 is not a group.
    assert groups == {("WH-BOM", "BIO-002"), ("WH-FRA", "BIO-001")}


# ---------------------------------------------------------------------------
# The two solvers must agree *with* the constraint
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_milp_and_exhaustive_search_agree_under_hub_capacity():
    """Without this, the MILP could be silently ignoring the constraint.

    A formulation that dropped the hub constraints would still return an optimum
    and would still be "optimal" -- for a different problem. Agreement with an
    independent enumeration that *does* enforce them is the check.
    """
    verification = optimize_for_event(
        event_id="EVT-001", scenario_id="SCN-EVT-001-10D"
    ).trace["verification"]
    assert verification["milp_available"] is True
    assert verification["milp_agrees_with_exhaustive"] is True
    assert verification["exhaustive_search_performed"] is True


@pytest.mark.unit
def test_a_hub_overdraw_is_not_a_choice_the_optimizer_can_make():
    """SHIP-004 + SHIP-005 on EXPEDITE, SHIP-003 also expedited: never chosen."""
    result = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    choices = result.trace["selected_plan"]["choices"]
    expedited = [
        s for s in ("SHIP-004", "SHIP-005") if choices.get(s) == "EXPEDITE_FROM_HUB"
    ]
    assert len(expedited) <= 1, f"the plan overdraws WH-FRA: {choices}"


@pytest.mark.unit
def test_a_tighter_ceiling_changes_the_answer_so_the_constraint_is_load_bearing(
    monkeypatch, network
):
    """If tightening the ceiling changed nothing, it would be decoration.

    The seed is replaced with one that cannot cover any BIO-001 consignment at
    WH-FRA, and EXPEDITE_FROM_HUB must disappear from that consignment's options.
    """
    starved = HubInventory(
        as_of_utc="2026-09-30T06:00:00Z",
        source="tightened-for-test",
        positions={"WH-BOM": {"BIO-002": 2600}, "WH-FRA": {"BIO-001": 1000, "BIO-002": 3000}},
    )
    monkeypatch.setattr(
        "backend.m2.digital_twin.load_inventory", lambda: starved
    )
    view = derive_disruption(network, scenario_id="SCN-EVT-001-10D", event_id="EVT-001")
    contexts, _ = build_contexts(
        network,
        disrupted_lanes=list(view.disrupted_lanes),
        excursed_shipments=list(view.excursed_shipments),
        inventory=starved,
    )
    catalogue = build_catalogue()
    by_id = {c.shipment_id: c for c in contexts}
    for shipment_id in ("SHIP-004", "SHIP-005"):
        assert by_id[shipment_id].hub_capacity_units == 1000
        assert "EXPEDITE_FROM_HUB" not in {
            a.id for a in feasible_actions(by_id[shipment_id], catalogue)
        }


@pytest.mark.unit
def test_the_uniform_expedite_policy_is_reported_as_unexecutable_not_hidden():
    """The most informative counterfactual is the cheapest-looking bad one."""
    rows = {
        r["uniform_policy"]: r
        for r in optimize_for_event(
            event_id="EVT-001", scenario_id="SCN-EVT-001-10D"
        ).trace["counterfactuals"]
    }
    expedite = rows["EXPEDITE_FROM_HUB"]
    assert expedite["executable"] is False
    joined = " ".join(expedite["violations"])
    assert "hub inventory: WH-FRA/BIO-001 drawn 12,600 units > 12,000 available" in joined
    assert "cannot be sourced for SHIP-001, SHIP-002" in joined


@pytest.mark.unit
def test_a_hub_sourced_action_costs_the_same_but_can_now_be_unavailable(network):
    """The action's coefficients are untouched; only its feasibility moved."""
    catalogue = build_catalogue()
    action = catalogue.by_id("EXPEDITE_FROM_HUB")
    assert action.consumes_hub_inventory is True
    assert action.per_pallet_usd == 900.0
    assert action.fixed_usd == 800.0
    assert action.as_dict()["consumes_hub_inventory"] is True


@pytest.mark.unit
def test_evaluating_an_unavailable_action_still_produces_arithmetic(network):
    """Feasibility and arithmetic are separate concerns, deliberately.

    The counterfactual rows need to be able to *price* a policy that cannot be
    run. Feasibility is enforced by the plan builder and the capacity check, not
    by making the twin refuse to compute.
    """
    view = derive_disruption(network, scenario_id="SCN-EVT-001-10D", event_id="EVT-001")
    contexts, _ = build_contexts(
        network,
        disrupted_lanes=list(view.disrupted_lanes),
        excursed_shipments=list(view.excursed_shipments),
    )
    catalogue = build_catalogue()
    scenario = result_scenario(view)
    assignment = {c.shipment_id: "EXPEDITE_FROM_HUB" for c in contexts}
    outcome = evaluate_plan(contexts, assignment, catalogue, scenario)
    assert outcome.total_cost_usd > 0
    assert 0.0 <= outcome.service_level <= 1.0


def result_scenario(view):
    from backend.m2.digital_twin import ScenarioContext

    return ScenarioContext(
        scenario_id="TEST",
        horizon_days=float(view.horizon_days),
        duration_days=view.duration_days,
        transit_delay_days=view.transit_delay_days,
        capacity_factor=view.capacity_factor,
    )
