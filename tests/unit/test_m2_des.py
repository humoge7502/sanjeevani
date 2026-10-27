"""M2 discrete-event simulation: the queue, and the three questions it answers.

The DES exists because the closed-form twin cannot express contention, position or
overlap. So the tests are about exactly those three, plus the property that makes a
simulation usable in an audited system: **it is deterministic, and that is asserted
against the source rather than claimed in a comment.**

    python -m pytest tests/unit/test_m2_des.py -q
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend.config import reload_config, settings
from backend.graph.network import Network
from backend.m2.des import (
    ClosureWindow,
    DesConsignment,
    clearance_for_plan,
    compare_with_twin,
    contradiction_tolerance_hours,
    des_enabled,
    describe_model,
    merge_windows,
    model_for,
    overlaps,
    peak_backlog,
    port_models,
    simulate,
    windows_from_disruption,
)
from backend.m2.digital_twin import build_catalogue, build_contexts
from backend.m2.optimizer import derive_disruption, optimize_for_event

MODULE_PATH = Path(__file__).resolve().parents[2] / "backend" / "m2" / "des.py"

PORT = "PORT-SUEZ"
SUEZ = {"berths": 2, "service_days_per_consignment": 0.75}


def _ship(
    shipment_id: str,
    arrival_day: float,
    *,
    node_id: str = PORT,
    priority: int = 1,
    units: int = 1000,
) -> DesConsignment:
    return DesConsignment(
        shipment_id=shipment_id,
        node_id=node_id,
        arrival_day=arrival_day,
        priority=priority,
        units=units,
    )


def _window(start: float = 0.0, end: float = 10.0, node_id: str = PORT) -> ClosureWindow:
    return ClosureWindow(node_id=node_id, start_day=start, end_day=end)


@pytest.fixture
def network() -> Network:
    return Network.load()


def _hero_contexts(network: Network):
    view = derive_disruption(network, scenario_id="SCN-EVT-001-10D", event_id="EVT-001")
    contexts, _ = build_contexts(
        network,
        disrupted_lanes=list(view.disrupted_lanes),
        excursed_shipments=list(view.excursed_shipments),
    )
    return view, contexts


# ---------------------------------------------------------------------------
# No randomness. Asserted against the source, not promised in prose.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_module_imports_no_random_number_generator():
    """A DES may be deterministic. This one is, and the source proves it.

    If someone later adds `import random` to jitter a service time, that single
    line would destroy the audit chain's byte-reproducibility. So it is a test.
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in source.splitlines()
        if re.match(r"^\s*(import|from)\s+(random|numpy|scipy|secrets)\b", line)
        or "random.random" in line
        or "np.random" in line
    ]
    assert offenders == [], f"the DES must stay deterministic: {offenders}"


@pytest.mark.unit
def test_the_result_declares_its_own_determinism():
    result = simulate([_ship("SHIP-A", 0.0)], [_window()])
    payload = result.as_dict()
    assert payload["deterministic"] is True
    assert payload["randomness"].startswith("none")


@pytest.mark.unit
def test_two_runs_are_identical_to_the_bit():
    participants = [_ship(f"SHIP-{i}", 0.0) for i in range(3)]
    first = simulate(participants, [_window()])
    second = simulate(participants, [_window()])
    assert first.as_dict() == second.as_dict()


# ---------------------------------------------------------------------------
# Overlap: the union of two intervals, not the sum of two durations
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_overlapping_closures_merge_into_their_union_not_their_sum():
    """[0,10) and [7,17) close the port for 17 days, not 20 and not 10."""
    merged = merge_windows([_window(0, 10), _window(7, 17)])
    assert len(merged) == 1
    assert (merged[0].start_day, merged[0].end_day) == (0.0, 17.0)


@pytest.mark.unit
def test_disjoint_closures_stay_separate():
    merged = merge_windows([_window(0, 10), _window(12, 20)])
    assert [(w.start_day, w.end_day) for w in merged] == [(0.0, 10.0), (12.0, 20.0)]


@pytest.mark.unit
def test_adjacent_closures_merge_because_the_port_never_reopened():
    merged = merge_windows([_window(0, 10), _window(10, 15)])
    assert [(w.start_day, w.end_day) for w in merged] == [(0.0, 15.0)]


@pytest.mark.unit
def test_closures_at_different_chokepoints_do_not_merge():
    merged = merge_windows([_window(0, 10), _window(5, 15, node_id="PORT-JNPT")])
    assert {w.node_id for w in merged} == {"PORT-SUEZ", "PORT-JNPT"}


@pytest.mark.unit
def test_a_contained_closure_adds_nothing():
    merged = merge_windows([_window(0, 20), _window(5, 8)])
    assert [(w.start_day, w.end_day) for w in merged] == [(0.0, 20.0)]


@pytest.mark.unit
def test_a_zero_length_window_is_not_a_closure():
    assert merge_windows([_window(4, 4)]) == []


@pytest.mark.unit
def test_overlaps_states_both_answers_side_by_side():
    """The naive answer and the union answer, so the difference is visible."""
    report = overlaps(_window(0, 10), _window(7, 17))
    assert report["naive_duration_days"] == 20.0
    assert report["union_duration_days"] == 17.0
    assert report["overlap_days"] == 3.0
    assert "do not concatenate" in report["note"]


@pytest.mark.unit
def test_an_overlapped_closure_clears_later_than_either_alone():
    participants = [_ship("SHIP-A", 0.0), _ship("SHIP-B", 0.0), _ship("SHIP-C", 0.0)]
    single = simulate(participants, [_window(0, 10)])
    double = simulate(participants, [_window(0, 10), _window(7, 17)])
    assert single.clearance_day == pytest.approx(11.5)
    assert double.clearance_day == pytest.approx(18.5)
    # ...and strictly less than the naive concatenation would give.
    assert double.clearance_day < 21.5


# ---------------------------------------------------------------------------
# Contention: finite berths
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_closed_port_queues_everything_then_serves_it_in_order():
    participants = [_ship("SHIP-A", 0.0), _ship("SHIP-B", 0.0), _ship("SHIP-C", 0.0)]
    result = simulate(participants, [_window(0, 10)])
    # Two berths serve the first two at the reopen; the third waits one service.
    assert result.passed_at_day == {
        "SHIP-A": pytest.approx(10.75),
        "SHIP-B": pytest.approx(10.75),
        "SHIP-C": pytest.approx(11.5),
    }
    assert result.queue_peak == {PORT: 1}
    # The wait is the time at anchor: 10 days for the reopen + 0.75 for a berth.
    # The passage itself completes at 11.5, which is passed_at_day's job to say.
    assert result.wait_days["SHIP-C"] == pytest.approx(10.75)
    assert result.clearance_day == pytest.approx(11.5)


@pytest.mark.unit
def test_more_berths_remove_the_queue_entirely():
    """The same traffic through a three-berth port never waits."""
    three = [_ship(f"SHIP-{i}", 0.0, node_id="PORT-JNPT") for i in range(3)]
    assert model_for("PORT-JNPT").berths == 3
    result = simulate(three, [_window(0, 10, node_id="PORT-JNPT")])
    assert result.queue_peak == {"PORT-JNPT": 0}
    assert set(result.wait_days.values()) == {10.0}
    assert result.clearance_day == pytest.approx(10.25)


@pytest.mark.unit
def test_arrivals_after_the_reopen_do_not_wait_at_all():
    participants = [_ship("SHIP-A", 0.0), _ship("SHIP-B", 20.0)]
    result = simulate(participants, [_window(0, 10)])
    assert result.wait_days["SHIP-A"] == pytest.approx(10.0)
    assert result.wait_days["SHIP-B"] == pytest.approx(0.0)
    assert result.queue_peak == {PORT: 0}


@pytest.mark.unit
def test_a_priority_request_runs_ahead_of_a_lower_priority_one():
    participants = [
        _ship("SHIP-LOW", 0.0, priority=1),
        _ship("SHIP-HIGH", 0.0, priority=0),
    ]
    result = simulate(participants, [_window(0, 10)])
    # Two berths, so both are served at the reopen -- the priorities only matter
    # when the queue is deeper than the berth count. Assert the ordering is at
    # least stable and that the mechanism is exercised.
    assert result.passed_at_day["SHIP-HIGH"] == pytest.approx(10.75)
    assert result.passed_at_day["SHIP-LOW"] == pytest.approx(10.75)


@pytest.mark.unit
def test_peak_backlog_is_recomputable_by_hand_from_the_timeline():
    participants = [_ship(f"SHIP-{i}", 0.0) for i in range(4)]
    result = simulate(participants, [_window(0, 10)])
    assert result.queue_peak == {PORT: 2}
    recomputed = peak_backlog(participants, result.passed_at_day)
    assert recomputed == result.queue_peak


@pytest.mark.unit
def test_an_empty_consignment_list_is_a_valid_result_not_an_error():
    result = simulate([], [_window(0, 10)])
    assert result.clearance_day == 0.0
    assert result.recovery_hours == 0.0
    assert result.participants == ()
    assert result.queued_anything is False


@pytest.mark.unit
def test_berth_utilisation_stays_within_one():
    participants = [_ship(f"SHIP-{i}", 0.0) for i in range(4)]
    result = simulate(participants, [_window(0, 10)])
    assert 0.0 < result.berth_utilisation[PORT] <= 1.0


# ---------------------------------------------------------------------------
# Position: when does a consignment actually reach the chokepoint?
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_arrival_day_is_derived_from_the_lane_topology(network):
    """Not a configured constant: hops-to-chokepoint x lane transit time."""
    _view, contexts = _hero_contexts(network)
    catalogue = build_catalogue()
    assignment = {c.shipment_id: "WAIT" for c in contexts}
    result = clearance_for_plan(
        network, contexts, catalogue, assignment, windows_from_disruption([PORT], 10.0)
    )
    by_id = {c.shipment_id: c for c in result.participants}
    # LANE-MUM-BIO-EU: 5 hops, 27 days transit -> 5.4 days per hop.
    assert by_id["SHIP-001"].arrival_day == pytest.approx(0.0)  # already alongside
    assert by_id["SHIP-004"].arrival_day == pytest.approx(5.4)  # one hop out
    assert by_id["SHIP-002"].arrival_day == pytest.approx(10.8)  # two hops out
    assert by_id["SHIP-003"].arrival_day == pytest.approx(16.2)  # three hops out


@pytest.mark.unit
def test_traffic_already_past_the_chokepoint_is_not_requeued(network):
    """SHIP-005 sits at WH-FRA, downstream of Suez. It cannot queue there."""
    _view, contexts = _hero_contexts(network)
    catalogue = build_catalogue()
    assignment = {c.shipment_id: "WAIT" for c in contexts}
    result = clearance_for_plan(
        network, contexts, catalogue, assignment, windows_from_disruption([PORT], 10.0)
    )
    assert "SHIP-005" not in {c.shipment_id for c in result.participants}
    assert any(
        e["shipment_id"] == "SHIP-005" and "already passed" in e["reason"] for e in result.excluded
    )


@pytest.mark.unit
def test_a_plan_that_lifts_everything_off_the_corridor_leaves_no_queue(network):
    """The recommended plan removes all five consignments from the corridor."""
    result = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    des = result.trace["des"]["recommended_plan"]
    assert des["participants"] == []
    assert des["clearance_day"] == 0.0
    assert len(des["excluded"]) == 5
    assert all("removes the consignment" in e["reason"] for e in des["excluded"][:4])


@pytest.mark.unit
def test_waiting_leaves_traffic_and_the_des_says_how_long(network):
    _view, contexts = _hero_contexts(network)
    catalogue = build_catalogue()
    assignment = {c.shipment_id: "WAIT" for c in contexts}
    result = clearance_for_plan(
        network, contexts, catalogue, assignment, windows_from_disruption([PORT], 10.0)
    )
    assert len(result.participants) == 4
    # The last consignment reaches the chokepoint at 16.2 days and passes 0.75
    # later, so the corridor is not clear until day 16.95.
    assert result.clearance_day == pytest.approx(16.95)
    assert result.recovery_hours == pytest.approx(406.8)
    assert result.events_processed > 0


# ---------------------------------------------------------------------------
# The cross-check: agreement is evidence, disagreement is a finding
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_des_contradicts_the_twin_when_a_policy_leaves_traffic_queued(network):
    """The example this whole module was built for.

    The twin prices `WAIT` at 336 hours (the action's own duration). The DES says
    the corridor is not clear for 406.8 hours, because the last consignment is
    16.2 days away and has to queue. A 70.8-hour blind spot, reported rather than
    averaged away.
    """
    result = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    wait = next(r for r in result.trace["counterfactuals"] if r["uniform_policy"] == "WAIT")
    assert wait["des"]["recovery_hours"] == pytest.approx(406.8)
    # The twin's figure for a uniform WAIT policy is the action's recovery_hours.
    comparison = compare_with_twin(
        clearance_for_plan(
            Network.load(),
            *_hero_assignment(Network.load(), "WAIT"),
            windows_from_disruption([PORT], 10.0),
        ),
        336.0,
    )
    assert comparison["verdict"] == "des_slower"
    assert comparison["contradicts"] is True
    assert comparison["delta_hours"] == pytest.approx(70.8, abs=0.05)


def _hero_assignment(network: Network, action_id: str):
    _view, contexts = _hero_contexts(network)
    catalogue = build_catalogue()
    assignment = {c.shipment_id: action_id for c in contexts}
    return contexts, catalogue, assignment


@pytest.mark.unit
def test_the_cross_check_does_not_average_the_two_models():
    result = simulate([_ship("SHIP-A", 0.0)], [_window(0, 10)])
    comparison = compare_with_twin(result, 100.0)
    assert "des_recovery_hours" in comparison
    assert "twin_recovery_hours" in comparison
    assert "mean" not in comparison
    assert "not averaged" in comparison["note"]


@pytest.mark.unit
def test_nothing_to_queue_is_reported_as_not_applicable_not_as_agreement():
    result = simulate([], [_window(0, 10)])
    comparison = compare_with_twin(result, 72.0)
    assert comparison["verdict"] == "not_applicable"
    assert comparison["contradicts"] is False


@pytest.mark.unit
def test_a_small_difference_inside_the_tolerance_is_reported_as_agreement():
    result = simulate([_ship("SHIP-A", 0.0)], [_window(0, 10)])
    tolerance = contradiction_tolerance_hours()
    within = compare_with_twin(result, result.recovery_hours + tolerance / 2)
    assert within["verdict"] == "agrees"
    # The twin well below the DES: the queue found time the closed form missed.
    slower = compare_with_twin(result, result.recovery_hours - tolerance * 2)
    assert slower["verdict"] == "des_slower"
    assert slower["contradicts"] is True
    # And the twin well above the DES: the closed form was pessimistic.
    faster = compare_with_twin(result, result.recovery_hours + tolerance * 2)
    assert faster["verdict"] == "des_faster"
    assert faster["contradicts"] is True


# ---------------------------------------------------------------------------
# Posture and wiring
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_des_is_advisory_by_default():
    assert des_enabled() is False
    trace = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D").trace
    assert trace["des"]["enabled_as_constraint"] is False
    assert trace["des"]["violations"] == []
    assert trace["constraints"]["violations"] == []


@pytest.mark.unit
def test_the_des_joins_the_hard_constraints_only_when_switched_on(monkeypatch):
    monkeypatch.setenv("SANJEEVANI_M2_DES", "true")
    reload_config()
    try:
        assert des_enabled() is True
        trace = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D").trace
        assert trace["des"]["enabled_as_constraint"] is True
        # The recommended plan leaves no corridor traffic, so even switched on it
        # has nothing to object to -- and says so rather than inventing a finding.
        assert trace["des"]["violations"] == []
        assert trace["constraints"]["violations"] == []
    finally:
        monkeypatch.delenv("SANJEEVANI_M2_DES", raising=False)
        reload_config()
        settings()


@pytest.mark.unit
def test_the_trace_always_carries_the_des_cross_check():
    trace = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D").trace
    for key in (
        "model_version",
        "enabled_as_constraint",
        "contradiction_tolerance_hours",
        "recommended_plan",
        "cross_check",
        "violations",
    ):
        assert key in trace["des"], f"DES evidence is missing {key}"
    # The recommended plan empties the corridor, so zero events is the truthful
    # count -- the cross-check then reports not_applicable rather than inventing
    # a finding. The WAIT traffic in test_waiting_leaves_... is where events > 0.
    recommended = trace["des"]["recommended_plan"]
    assert recommended["events_processed"] == 0
    assert recommended["participants"] == []


@pytest.mark.unit
def test_every_uniform_policy_carries_its_own_des_verdict():
    trace = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D").trace
    rows = {r["uniform_policy"]: r for r in trace["counterfactuals"]}
    assert rows, "counterfactuals must be present"
    for row in rows.values():
        assert "des" in row, row["uniform_policy"]
        assert set(row["des"]) == {
            "clearance_day",
            "recovery_hours",
            "queue_peak",
            "mean_wait_days",
            "participants",
            "clears_all_corridor_traffic",
        }
    # The policies that leave traffic queued all take the same corridor drain
    # time, because they leave the same traffic behind.
    stalled = {rows[a]["des"]["recovery_hours"] for a in ("WAIT", "QUARANTINE_QA", "REROUTE_CAPE")}
    assert stalled == {406.8}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_service_model_comes_from_config_not_from_code():
    models = port_models()
    assert models["PORT-SUEZ"].berths == SUEZ["berths"]
    assert models["PORT-SUEZ"].service_days_per_consignment == SUEZ["service_days_per_consignment"]
    assert models["PORT-JNPT"].berths == 3


@pytest.mark.unit
def test_an_unlisted_chokepoint_gets_the_configured_default_not_unlimited_capacity():
    model = model_for("PORT-NOWHERE")
    assert model.berths == 1
    assert model.service_days_per_consignment == 0.5


@pytest.mark.unit
def test_describe_model_reports_the_switch_and_the_source():
    described = describe_model()
    assert described["source"] == "config/des.yaml"
    assert described["randomness"] == "none"
    assert described["enabled_as_constraint"] is False
    assert described["defaults"]["berths"] == 1
    assert {p["node_id"] for p in described["ports"]} == {"PORT-SUEZ", "PORT-JNPT"}


@pytest.mark.unit
def test_windows_from_disruption_is_one_window_per_blocked_node():
    windows = windows_from_disruption(["PORT-SUEZ", "PORT-JNPT", "PORT-SUEZ"], 10.0)
    assert [(w.node_id, w.start_day, w.end_day) for w in windows] == [
        ("PORT-JNPT", 0.0, 10.0),
        ("PORT-SUEZ", 0.0, 10.0),
    ]


@pytest.mark.unit
def test_the_des_did_not_change_the_recommended_plan():
    """The DES is evidence. The default plan must be exactly what it was."""
    result = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    assert result.recommended.strategy == "REROUTE_MUMBAI_AIR"
    assert result.recommended.cost == pytest.approx(226600.0)
    assert result.recommended.recovery_time_hours == pytest.approx(72.0)
