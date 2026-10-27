"""M2 -- the deterministic discrete-event simulation.

WHY THIS EXISTS. The closed-form twin answers "what does this action do to this
consignment" one consignment at a time. That is the right model for what it is,
and it cannot express three things:

1. **Contention.** Two consignments, two berths, and a third waiting behind them.
   A per-consignment delay has nowhere to put the third's wait.
2. **Position.** When does a consignment actually *reach* the chokepoint? The twin
   uses position only as a corridor-exposure multiplier, so a consignment three
   days away and one already alongside are treated as equally exposed.
3. **Overlap.** Two disruptions at the same chokepoint do not last the sum of
   their durations, they last the *union of two closed intervals*. Adding two
   durations is the intuitive answer and the wrong one.

A discrete-event simulation answers all three, and it does so **without becoming
stochastic**: SimPy schedules a deterministic event heap, every arrival and
service time here is an input derived from seeded data, and no random number
generator is imported anywhere in this module. That is asserted by a test, not
asserted in a comment.

WHAT THIS IS NOT. It is not a second opinion to be averaged with the twin, and it
is not the source of the audited numbers. It is a **cross-check**: the DES's
clearance time and the twin's recovery time are both reported, and when they
disagree past a configured tolerance the disagreement is surfaced rather than
hidden. Where they disagree, the DES is usually right -- and saying so is the
point of building it.

    arrivals -> [ closed window ] -> berths -> departure
                                       ^
    passage(c) requires (a) c has reached the chokepoint and (b) the chokepoint is
    open. Everything else is the event queue's ordering, which is FIFO by arrival
    then by a deterministic integer priority. Berths are non-preemptive: a passage
    already under way is not interrupted, which for a sub-day service time and
    multi-day closures never arises in the seeded scenario.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field, replace
from typing import Any

from backend.config import CONFIG_DIR, settings
from backend.graph.network import Network
from backend.m2.digital_twin import Catalogue, ConsignmentContext, ScenarioContext

MODEL_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PortServiceModel:
    """How fast a chokepoint processes traffic. Configured, never sampled."""

    node_id: str
    berths: int
    service_days_per_consignment: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "berths": self.berths,
            "service_days_per_consignment": self.service_days_per_consignment,
        }


@functools.cache
def _read_des_config() -> dict[str, Any]:
    import yaml

    path = CONFIG_DIR / "des.yaml"
    if not path.exists():  # pragma: no cover - configuration error path
        raise FileNotFoundError(
            f"DES service model missing: {path}. "
            "Without it the simulation has no berth capacity to queue against."
        )
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def reset_des_cache() -> None:
    _read_des_config.cache_clear()


def port_models() -> dict[str, PortServiceModel]:
    """Every configured port, plus the defaults for one that is not listed."""
    doc = _read_des_config()
    defaults = doc.get("defaults") or {}
    models: dict[str, PortServiceModel] = {
        "__defaults__": PortServiceModel(
            node_id="__defaults__",
            berths=int(defaults.get("berths", 1)),
            service_days_per_consignment=float(defaults.get("service_days_per_consignment", 0.5)),
        )
    }
    for node_id, entry in (doc.get("ports") or {}).items():
        raw = entry or {}
        models[str(node_id)] = PortServiceModel(
            node_id=str(node_id),
            berths=int(raw.get("berths", defaults.get("berths", 1))),
            service_days_per_consignment=float(
                raw.get(
                    "service_days_per_consignment",
                    defaults.get("service_days_per_consignment", 0.5),
                )
            ),
        )
    return models


def model_for(node_id: str) -> PortServiceModel:
    models = port_models()
    return models.get(node_id, models["__defaults__"])


def des_enabled() -> bool:
    """Is the DES's verdict part of the plan's hard constraints?"""
    return bool(settings().get("m2", {}).get("des", {}).get("enabled", False))


def contradiction_tolerance_hours() -> float:
    return float(settings().get("m2", {}).get("des", {}).get("contradiction_tolerance_hours", 24.0))


# ---------------------------------------------------------------------------
# Windows and arrivals
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClosureWindow:
    """A chokepoint is closed on ``[start_day, end_day)`` and reopens at the end."""

    node_id: str
    start_day: float
    end_day: float
    source: str = "disruption"

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "start_day": round(self.start_day, 4),
            "end_day": round(self.end_day, 4),
            "duration_days": round(self.end_day - self.start_day, 4),
            "source": self.source,
        }


def merge_windows(windows: list[ClosureWindow]) -> list[ClosureWindow]:
    """Union of overlapping or adjacent closures per node.

    The whole answer to "what if two disruptions overlap". Two ten-day closures
    overlapping by three days close the chokepoint for seventeen days, not twenty,
    and not ten. Interval union is the correct arithmetic and addition is not.
    """
    by_node: dict[str, list[ClosureWindow]] = {}
    for window in windows:
        if window.end_day <= window.start_day:
            continue
        by_node.setdefault(window.node_id, []).append(window)

    merged: list[ClosureWindow] = []
    for node_id in sorted(by_node):
        ordered = sorted(by_node[node_id], key=lambda w: (w.start_day, w.end_day, w.source))
        current = ordered[0]
        for nxt in ordered[1:]:
            if nxt.start_day <= current.end_day + 1e-12:
                sources = (
                    current.source
                    if nxt.source in current.source
                    else f"{current.source}+{nxt.source}"
                )
                current = ClosureWindow(
                    node_id=node_id,
                    start_day=current.start_day,
                    end_day=max(current.end_day, nxt.end_day),
                    source=sources,
                )
                continue
            merged.append(current)
            current = nxt
        merged.append(current)
    return merged


@dataclass(frozen=True)
class DesConsignment:
    """One unit of traffic the plan leaves on a chokepoint."""

    shipment_id: str
    node_id: str
    arrival_day: float
    priority: int
    units: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "shipment_id": self.shipment_id,
            "node_id": self.node_id,
            "arrival_day": round(self.arrival_day, 4),
            "priority": self.priority,
            "units": self.units,
        }


#: Priority bands. Lower runs first; the labels come from the seeded criticality.
_CRITICALITY_PRIORITY = {"critical": 0, "standard": 1}


def _hops_from(network: Network, ctx: ConsignmentContext, node_id: str) -> int | None:
    """Hops along the lane from the consignment's position to ``node_id``.

    None when the node is not on the lane, or is already behind the consignment --
    in which case it is not traffic that will ever queue at that chokepoint.
    """
    lane = network.lanes.get(ctx.lane_id)
    if lane is None:
        return None
    path = lane.path
    if node_id not in path or ctx.current_node not in path:
        return None
    behind = path.index(ctx.current_node)
    ahead = path.index(node_id)
    if ahead < behind:
        return None
    return ahead - behind


def build_consignments(
    network: Network,
    contexts: list[ConsignmentContext],
    catalogue: Catalogue,
    assignment: dict[str, str],
    windows: list[ClosureWindow],
) -> tuple[list[DesConsignment], list[dict[str, Any]]]:
    """Traffic the plan leaves on a disrupted chokepoint, and what it excluded.

    A consignment participates only when BOTH hold:

      * the plan's action for it keeps it corridor-exposed
        (``corridor_exposure_factor > 0``), so a plan that lifts everything off the
        corridor correctly generates an empty queue; and
      * the chokepoint is at or downstream of where the consignment is now, so
        traffic that has already cleared the chokepoint is not re-queued.
    """
    merged = merge_windows(windows)
    by_node: dict[str, ClosureWindow] = {w.node_id: w for w in merged}
    consignments: list[DesConsignment] = []
    excluded: list[dict[str, Any]] = []

    for ctx in sorted(contexts, key=lambda c: c.shipment_id):
        action = catalogue.by_id(assignment[ctx.shipment_id])
        if action.corridor_exposure_factor <= 0.0:
            excluded.append(
                {
                    "shipment_id": ctx.shipment_id,
                    "reason": f"{action.id} removes the consignment from the corridor",
                }
            )
            continue
        placed = False
        for node_id in sorted(by_node):
            hops = _hops_from(network, ctx, node_id)
            if hops is None:
                continue
            lane = network.lanes[ctx.lane_id]
            span = max(len(lane.path) - 1, 1)
            arrival = round(lane.transit_days * hops / span, 6)
            consignments.append(
                DesConsignment(
                    shipment_id=ctx.shipment_id,
                    node_id=node_id,
                    arrival_day=arrival,
                    priority=_CRITICALITY_PRIORITY.get(ctx.criticality, 2),
                    units=ctx.units,
                )
            )
            placed = True
        if not placed:
            excluded.append(
                {
                    "shipment_id": ctx.shipment_id,
                    "reason": (
                        f"{action.id} keeps the consignment corridor-exposed, but it has "
                        "already passed every disrupted chokepoint"
                    ),
                }
            )
    return consignments, excluded


def windows_from_disruption(
    disrupted_nodes: list[str], duration_days: float
) -> list[ClosureWindow]:
    """One closure per blocked node, starting at the scenario's t0."""
    return [
        ClosureWindow(node_id=node_id, start_day=0.0, end_day=float(duration_days))
        for node_id in sorted(set(disrupted_nodes))
    ]


# ---------------------------------------------------------------------------
# The simulation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DesResult:
    """What the event queue says happened."""

    model_version: str
    windows: tuple[ClosureWindow, ...]
    participants: tuple[DesConsignment, ...]
    #: When each consignment cleared the chokepoint (berth acquired + passage done).
    passed_at_day: dict[str, float]
    #: Time at anchor per consignment: arrival to berth acquisition, service excluded.
    wait_days: dict[str, float]
    queue_peak: dict[str, int]
    berth_utilisation: dict[str, float]
    clearance_day: float
    recovery_hours: float
    mean_wait_days: float
    max_wait_days: float
    events_processed: int
    excluded: tuple[dict[str, Any], ...] = ()

    @property
    def queued_anything(self) -> bool:
        return any(v > 1e-9 for v in self.wait_days.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "deterministic": True,
            "randomness": "none: no stochastic arrivals, no service-time distribution, no seed",
            "windows": [w.as_dict() for w in self.windows],
            "participants": [c.as_dict() for c in self.participants],
            "excluded": list(self.excluded),
            "clearance_day": round(self.clearance_day, 4),
            "recovery_hours": round(self.recovery_hours, 2),
            "mean_wait_days": round(self.mean_wait_days, 4),
            "max_wait_days": round(self.max_wait_days, 4),
            "queue_peak": dict(sorted(self.queue_peak.items())),
            "berth_utilisation": {
                k: round(v, 4) for k, v in sorted(self.berth_utilisation.items())
            },
            "passed_at_day": {k: round(v, 4) for k, v in sorted(self.passed_at_day.items())},
            "wait_days": {k: round(v, 4) for k, v in sorted(self.wait_days.items())},
            "events_processed": self.events_processed,
        }


def _open_windows(windows: list[ClosureWindow], node_id: str) -> list[ClosureWindow]:
    return [w for w in merge_windows(windows) if w.node_id == node_id]


def _blocked_until(windows: list[ClosureWindow], now: float) -> float | None:
    for window in windows:
        if window.start_day <= now < window.end_day:
            return window.end_day
    return None


@dataclass
class _PortState:
    busy_days: float = 0.0


def peak_backlog(
    participants: list[DesConsignment],
    passed_at_day: dict[str, float],
) -> dict[str, int]:
    """Deepest queue at each chokepoint, computed from the recorded timeline.

    Deliberately post-processed rather than sampled from SimPy's internal queue
    length. The definition is then the one a reviewer would write down: at the
    instant each consignment presents itself, count how many have arrived and not
    yet passed, and subtract the berths that can serve them. Computed this way it
    is a function of the simulation's outputs, so it is as reproducible as they
    are, and it does not depend on a library's internals.
    """
    peaks: dict[str, int] = {}
    nodes = sorted({c.node_id for c in participants})
    for node_id in nodes:
        here = [c for c in participants if c.node_id == node_id]
        capacity = max(model_for(node_id).berths, 1)
        peak = 0
        for moment in sorted({c.arrival_day for c in here}):
            arrived = sum(1 for c in here if c.arrival_day <= moment + 1e-12)
            passed = sum(
                1 for c in here if passed_at_day.get(c.shipment_id, float("inf")) <= moment + 1e-12
            )
            peak = max(peak, arrived - passed - capacity)
        peaks[node_id] = max(peak, 0)
    return peaks


def simulate(
    consignments: list[DesConsignment],
    windows: list[ClosureWindow],
) -> DesResult:
    """Run the event queue. Pure: same inputs, same outputs, no RNG anywhere.

    SimPy is imported lazily so that a deployment without it can still import
    ``backend.m2`` -- the DES is a cross-check, and a missing cross-check must
    degrade the evidence rather than break the loop.
    """
    import simpy

    merged = merge_windows(windows)
    env = simpy.Environment()
    resources: dict[str, Any] = {}
    states: dict[str, _PortState] = {}
    for node_id in sorted({w.node_id for w in merged}):
        model = model_for(node_id)
        resources[node_id] = simpy.PriorityResource(env, capacity=max(model.berths, 1))
        states[node_id] = _PortState()

    passed_at: dict[str, float] = {}
    wait_days: dict[str, float] = {}
    events = {"count": 0}

    def passage(ship: DesConsignment) -> Any:
        node_windows = _open_windows(merged, ship.node_id)
        resource = resources[ship.node_id]
        state = states[ship.node_id]
        model = model_for(ship.node_id)

        delay = ship.arrival_day - env.now
        if delay > 0:
            events["count"] += 1
            yield env.timeout(delay)

        arrived_at = env.now
        while True:
            reopen = _blocked_until(node_windows, env.now)
            if reopen is None:
                break
            events["count"] += 1
            yield env.timeout(reopen - env.now)

        request = resource.request(priority=ship.priority)
        events["count"] += 1
        yield request
        try:
            # The wait is the time at anchor: arrival to berth, service excluded.
            # Including the passage would make wait_days restate passed_at_day
            # and the queue's own cost would be invisible in the summary.
            wait_days[ship.shipment_id] = round(env.now - arrived_at, 6)
            events["count"] += 1
            yield env.timeout(model.service_days_per_consignment)
        finally:
            resource.release(request)

        state.busy_days += model.service_days_per_consignment
        passed_at[ship.shipment_id] = env.now

    for ship in sorted(
        consignments, key=lambda c: (c.node_id, c.arrival_day, c.priority, c.shipment_id)
    ):
        env.process(passage(ship))
    env.run()

    clearance = max(passed_at.values(), default=0.0)
    utilisation = {
        node_id: (states[node_id].busy_days / (model_for(node_id).berths * clearance))
        if clearance > 0
        else 0.0
        for node_id in sorted(states)
    }
    waits = list(wait_days.values())
    return DesResult(
        model_version=MODEL_VERSION,
        windows=tuple(merged),
        participants=tuple(sorted(consignments, key=lambda c: c.shipment_id)),
        passed_at_day=passed_at,
        wait_days=wait_days,
        queue_peak=peak_backlog(consignments, passed_at),
        berth_utilisation=utilisation,
        clearance_day=clearance,
        recovery_hours=round(clearance * 24.0, 6),
        mean_wait_days=round(sum(waits) / len(waits), 6) if waits else 0.0,
        max_wait_days=round(max(waits), 6) if waits else 0.0,
        events_processed=events["count"],
    )


# ---------------------------------------------------------------------------
# The cross-check
# ---------------------------------------------------------------------------


def clearance_for_plan(
    network: Network,
    contexts: list[ConsignmentContext],
    catalogue: Catalogue,
    assignment: dict[str, str],
    windows: list[ClosureWindow],
) -> DesResult:
    """Run the DES for one plan. Empty traffic is a valid, informative result."""
    consignments, excluded = build_consignments(network, contexts, catalogue, assignment, windows)
    result = simulate(consignments, windows)
    if excluded:
        return replace(result, excluded=tuple(excluded))
    return result


def compare_with_twin(des: DesResult, twin_recovery_hours: float) -> dict[str, Any]:
    """State the agreement or the disagreement. Never average the two.

    Averages hide exactly the case worth knowing about: the plan whose recovery
    time looks acceptable in closed form and is not, once traffic has to queue.

    Three verdicts, and the first is not a cop-out. When the plan leaves no
    corridor traffic at any disrupted chokepoint there is nothing to queue, so
    "the DES is faster" would be a misleading way of saying "not applicable".
    """
    tolerance = contradiction_tolerance_hours()
    base = {
        "twin_recovery_hours": round(twin_recovery_hours, 2),
        "des_recovery_hours": round(des.recovery_hours, 2),
        "tolerance_hours": tolerance,
        "note": (
            "The two models are not averaged. The twin applies a duration per "
            "consignment; the DES queues traffic against finite berths and unions "
            "overlapping closures. Agreement is evidence; disagreement is a finding."
        ),
    }
    if not des.participants:
        return {
            **base,
            "delta_hours": 0.0,
            "verdict": "not_applicable",
            "contradicts": False,
            "note": (
                "The plan removes every consignment from the disrupted corridor, so "
                "there is no traffic at the chokepoint to queue. The DES has nothing "
                "to contradict."
            ),
        }

    delta = round(des.recovery_hours - twin_recovery_hours, 6)
    if abs(delta) <= tolerance:
        verdict = "agrees"
    elif delta > 0:
        verdict = "des_slower"
    else:
        verdict = "des_faster"
    return {
        **base,
        "delta_hours": round(delta, 2),
        "verdict": verdict,
        "contradicts": verdict != "agrees",
    }


def overlaps(base: ClosureWindow, second: ClosureWindow) -> dict[str, Any]:
    """What two closures at one chokepoint actually cost, side by side.

    The naive answer -- add the durations -- is wrong, and this function states
    both so the difference is visible instead of argued about.
    """
    naive = ClosureWindow(
        node_id=base.node_id,
        start_day=min(base.start_day, second.start_day),
        end_day=min(base.start_day, second.start_day)
        + (base.end_day - base.start_day)
        + (second.end_day - second.start_day),
        source="naive_sum",
    )
    merged = merge_windows([base, second])
    union_days = sum(w.end_day - w.start_day for w in merged)
    naive_days = naive.end_day - naive.start_day
    return {
        "naive_duration_days": round(naive_days, 4),
        "union_duration_days": round(union_days, 4),
        "overlap_days": round(naive_days - union_days, 4),
        "merged": [w.as_dict() for w in merged],
        "note": (
            "Two closures at one chokepoint overlap; they do not concatenate. "
            "The union is the interval the port is actually shut."
        ),
    }


def describe_model() -> dict[str, Any]:
    """The configured service model, for the trace and for /api/mock-boundaries."""
    models = port_models()
    return {
        "model_version": MODEL_VERSION,
        "enabled_as_constraint": des_enabled(),
        "contradiction_tolerance_hours": contradiction_tolerance_hours(),
        "source": "config/des.yaml",
        "ports": [
            m.as_dict() for node_id, m in sorted(models.items()) if node_id != "__defaults__"
        ],
        "defaults": models["__defaults__"].as_dict(),
        "randomness": "none",
    }


def scenario_horizon_days(scenario: ScenarioContext) -> float:
    """Exposed so a caller can report the DES horizon without re-deriving it."""
    return float(scenario.horizon_days)


@dataclass
class DesEvidence:
    """Everything the trace needs, assembled in one place."""

    model: dict[str, Any]
    recommended: dict[str, Any]
    counterfactuals: list[dict[str, Any]] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
