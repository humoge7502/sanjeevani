"""M2 -- the recovery optimizer.

Closes the hole in the value chain. M1 measures the damage; M3 governs a plan;
this module is what *chooses* the plan, on evidence, and can prove how.

THE PROBLEM. Given a disruption, a set of affected consignments, and a catalogue
of feasible recovery actions (config/recovery_options.yaml), choose one action
per consignment that

    minimises   total_recovery_cost + w * expected_unrecovered_value

    subject to  recovery spend            <= budget cap
                achieved service level    >= service floor
                residual temperature risk <= cold-chain ceiling
                recovery time             <= time ceiling

The cost and the service level are value-weighted sums, so the whole thing is a
small binary program.

TWO SOLVERS, DELIBERATELY.

  * ``milp``   -- a real MILP built with PuLP and solved by the bundled CBC
                  binary. This is the production path.
  * ``exact``  -- exhaustive enumeration over the cartesian product of feasible
                  actions per consignment. Exponential in general, but this
                  network's plans have five consignments, so it is exact, fast,
                  and completely independent of the MILP formulation.

``exact`` runs alongside ``milp`` whenever the instance is small enough, and the
result is recorded in the trace. If the two ever disagree, the trace says so and
the exact answer is used -- an optimizer that cannot show its own arithmetic
being checked is just an opinion.

DETERMINISM. No sampling, no time limit races, no random seeds. CBC is invoked
with a single thread and a fixed formulation, so the same request produces the
same plan and therefore the same audit hashes. A test asserts two runs agree.
"""

from __future__ import annotations

import hashlib
import itertools
from dataclasses import dataclass, field
from typing import Any

from backend.config import isoformat, scenario_clock, scenarios_config, settings
from backend.contracts import RecoveryPlan
from backend.graph.network import Network
from backend.m2.des import (
    clearance_for_plan,
    compare_with_twin,
    des_enabled,
    describe_model,
    windows_from_disruption,
)
from backend.m2.digital_twin import (
    Catalogue,
    ConsignmentContext,
    PlanOutcome,
    RecoveryAction,
    ScenarioContext,
    build_catalogue,
    build_contexts,
    evaluate_plan,
    feasible_actions,
)
from backend.m2.inventory import load_inventory

#: Above this many candidate assignments the exhaustive oracle is skipped and
#: the MILP stands alone. 100k keeps the "exact" verification under ~1 second.
EXACT_ENUMERATION_LIMIT = 100_000

#: Recovery strategies M2 may emit, ordered by how decisively they fix a
#: disruption. Used to name a mixed assignment and to label alternatives.
_STRATEGY_ORDER = (
    "REROUTE_AIR_UPLIFT",
    "EMERGENCY_SOURCE",
    "QUARANTINE_QA",
    "EXPEDITE_FROM_HUB",
    "REROUTE_CAPE",
    "WAIT",
)


# ---------------------------------------------------------------------------
# The disruption view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DisruptionView:
    """M2's own reading of the disruption, derived from seeded scenario data.

    M2 receives only an ``event_id`` and a ``scenario_id`` across the frozen
    boundary, so it reconstructs the disruption from config/scenarios.yaml and the
    seeded network. It never sees M1's intermediate state -- if M1's arithmetic
    were wrong, M2 would still plan against the same seeded facts, which is what
    makes the two independent.
    """

    blocked_nodes: tuple[str, ...]
    disrupted_lanes: tuple[str, ...]
    excursed_shipments: tuple[str, ...]
    duration_days: float
    transit_delay_days: float
    capacity_factor: float
    horizon_days: int
    source: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "blocked_nodes": list(self.blocked_nodes),
            "disrupted_lanes": list(self.disrupted_lanes),
            "excursed_shipments": list(self.excursed_shipments),
            "duration_days": self.duration_days,
            "transit_delay_days": self.transit_delay_days,
            "capacity_factor": self.capacity_factor,
            "horizon_days": self.horizon_days,
            "source": self.source,
            "detail": self.detail,
        }


def parse_horizon(scenario_id: str | None, default: int) -> int:
    """Read the horizon out of a scenario id such as ``SCN-EVT-001-10D``."""
    if not scenario_id:
        return default
    tail = scenario_id.rsplit("-", 1)[-1]
    if tail.endswith("D") and tail[:-1].isdigit():
        return int(tail[:-1])
    return default


def derive_disruption(
    network: Network,
    *,
    scenario_id: str | None = None,
    event_id: str | None = None,
) -> DisruptionView:
    """Reconstruct the disruption from the seeded scenario library."""
    cfg = scenarios_config()
    hero = cfg["hero_scenario"]
    wanted = set(hero.get("disruption_ids") or [])
    library = [d for d in cfg["disruption_library"] if not wanted or d["id"] in wanted]

    blocked: list[str] = []
    excursed: list[str] = []
    duration = float(settings()["scenario"]["clamp"]["duration_days"][0])
    delay = 0.0
    capacity = 1.0
    used: list[dict[str, Any]] = []

    for entry in library:
        used.append({"id": entry["id"], "event_type": entry["event_type"], "target": entry.get("target")})
        if entry["event_type"] == "PORT_CLOSURE" and entry.get("target") in network.nodes:
            blocked.append(entry["target"])
            duration = float(entry.get("duration_days", duration))
            delay = float(entry.get("transit_delay_days", delay))
            capacity = float(entry.get("capacity_factor", capacity))
        elif entry["event_type"] == "COLD_CHAIN_EXCURSION":
            target = entry.get("target")
            if target in network.shipments:
                excursed.append(target)

    lanes: list[str] = []
    for node_id in sorted(set(blocked)):
        for lane in network.lanes_touching_node(node_id):
            if lane.id not in lanes:
                lanes.append(lane.id)
    lanes.sort()

    horizon = parse_horizon(scenario_id, int(hero["primary_horizon_days"]))
    return DisruptionView(
        blocked_nodes=tuple(sorted(set(blocked))),
        disrupted_lanes=tuple(lanes),
        excursed_shipments=tuple(sorted(set(excursed))),
        duration_days=duration,
        transit_delay_days=delay,
        capacity_factor=capacity,
        horizon_days=horizon,
        source="config/scenarios.yaml:disruption_library (seeded, deterministic)",
        detail={
            "event_id_requested": event_id,
            "scenario_id_requested": scenario_id,
            "entries_used": used,
            "hero_scenario": hero["id"],
        },
    )


# ---------------------------------------------------------------------------
# Candidate solutions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Assignment:
    """One complete candidate: an action chosen for every consignment."""

    choices: dict[str, str]
    outcome: PlanOutcome
    dominant_action_id: str
    strategy: str
    violations: tuple[str, ...]

    @property
    def feasible(self) -> bool:
        return not self.violations

    def key(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self.choices.items()))


def _dominant_action(
    choices: dict[str, str], contexts: list[ConsignmentContext]
) -> str:
    """The action carrying the most consignment value: the plan's headline move."""
    by_id = {c.shipment_id: c for c in contexts}
    weight: dict[str, float] = {}
    for shipment_id, action_id in choices.items():
        weight[action_id] = weight.get(action_id, 0.0) + by_id[shipment_id].value_usd
    rank = {a: i for i, a in enumerate(_STRATEGY_ORDER)}
    return min(weight, key=lambda a: (-weight[a], rank.get(a, len(rank)), a))


def _violations(outcome: PlanOutcome, catalogue: Catalogue) -> tuple[str, ...]:
    """Every hard constraint the plan breaks, with the arithmetic that broke it."""
    out: list[str] = []
    if outcome.total_cost_usd > catalogue.max_budget_usd:
        out.append(
            f"budget: cost {outcome.total_cost_usd:,.2f} > cap "
            f"{catalogue.max_budget_usd:,.2f}"
        )
    if outcome.service_level < catalogue.min_service_level:
        out.append(
            f"service: level {outcome.service_level:.4f} < floor "
            f"{catalogue.min_service_level:.4f}"
        )
    if outcome.temperature_risk > catalogue.max_temperature_risk:
        out.append(
            f"cold chain: temperature risk {outcome.temperature_risk:.4f} > ceiling "
            f"{catalogue.max_temperature_risk:.4f}"
        )
    if outcome.recovery_time_hours > catalogue.max_recovery_hours:
        out.append(
            f"time: recovery {outcome.recovery_time_hours:g}h > ceiling "
            f"{catalogue.max_recovery_hours:g}h"
        )
    return tuple(out)


def hub_demand(
    choices: dict[str, str],
    contexts: list[ConsignmentContext],
    catalogue: Catalogue,
) -> dict[tuple[str, str], int]:
    """Units of each (hub, SKU) a plan draws on, and the ceiling for it.

    Returned as demand only; the ceiling travels on the context
    (``hub_capacity_units``) because it is a fact about the consignment's hub, not
    about the plan.
    """
    demand: dict[tuple[str, str], int] = {}
    for ctx in contexts:
        action = catalogue.by_id(choices[ctx.shipment_id])
        if not action.consumes_hub_inventory or ctx.serving_hub is None:
            continue
        key = (ctx.serving_hub, ctx.sku_id)
        demand[key] = demand.get(key, 0) + ctx.units
    return demand


def _resource_violations(
    choices: dict[str, str],
    contexts: list[ConsignmentContext],
    catalogue: Catalogue,
) -> tuple[str, ...]:
    """Shared-capacity ceilings a plan breaks.

    This is the multi-echelon constraint: several consignments contend for one
    hub's stock for one SKU, and the plan may not collectively draw more than the
    hub holds. It is separate from :func:`_violations` because it depends on the
    *combination* of choices rather than on any single consignment's arithmetic --
    which is exactly the kind of constraint a per-consignment check cannot see.
    """
    demand = hub_demand(choices, contexts, catalogue)
    if not demand:
        return ()
    available: dict[tuple[str, str], int] = {}
    for ctx in contexts:
        if ctx.serving_hub is not None:
            available.setdefault((ctx.serving_hub, ctx.sku_id), ctx.hub_capacity_units)

    out: list[str] = []
    for (hub, sku), total in sorted(demand.items()):
        ceiling = available.get((hub, sku), 0)
        if total > ceiling:
            out.append(
                f"hub inventory: {hub}/{sku} drawn {total:,} units > {ceiling:,} available"
            )
    return tuple(out)


def _all_assignments(
    contexts: list[ConsignmentContext],
    catalogue: Catalogue,
    scenario: ScenarioContext,
    *,
    recovery_days: float,
    limit: int,
) -> list[Assignment] | None:
    """Exhaustively evaluate every feasible assignment, or None if too large."""
    option_lists = [feasible_actions(c, catalogue) for c in contexts]
    total = 1
    for options in option_lists:
        total *= len(options)
        if total > limit:
            return None

    assignments: list[Assignment] = []
    for combo in itertools.product(*option_lists):
        choices = {ctx.shipment_id: action.id for ctx, action in zip(contexts, combo, strict=True)}
        outcome = evaluate_plan(contexts, choices, catalogue, scenario, recovery_days=recovery_days)
        dominant = _dominant_action(choices, contexts)
        assignments.append(
            Assignment(
                choices=choices,
                outcome=outcome,
                dominant_action_id=dominant,
                strategy=catalogue.strategy_for(dominant),
                violations=_violations(outcome, catalogue)
                + _resource_violations(choices, contexts, catalogue),
            )
        )
    return assignments


# ---------------------------------------------------------------------------
# MILP
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MilpSolution:
    choices: dict[str, str]
    objective_value: float
    status: str
    available: bool
    note: str
    solver: str


def _hub_groups(
    contexts: list[ConsignmentContext],
    options: dict[str, list[RecoveryAction]],
) -> list[tuple[str, str, int, list[ConsignmentContext]]]:
    """``(hub, sku, ceiling, members)`` for every contended hub/SKU pair.

    Only groups where at least one member *could* take a hub-sourced action are
    returned, so the MILP gains no constraint for a hub nothing can draw on.
    """
    groups: dict[tuple[str, str], list[ConsignmentContext]] = {}
    for ctx in contexts:
        if ctx.serving_hub is None:
            continue
        if not any(a.consumes_hub_inventory for a in options[ctx.shipment_id]):
            continue
        groups.setdefault((ctx.serving_hub, ctx.sku_id), []).append(ctx)
    return [
        (hub, sku, members[0].hub_capacity_units, members)
        for (hub, sku), members in sorted(groups.items())
    ]


def _solve_milp(
    contexts: list[ConsignmentContext],
    catalogue: Catalogue,
    scenario: ScenarioContext,
    *,
    weight: float,
    recovery_days: float,
    constraints: bool,
) -> MilpSolution:
    """Build and solve the binary program with PuLP/CBC.

    Fails soft: if PuLP or the CBC binary is unavailable, the caller falls back to
    exact enumeration and the trace records why. A missing solver must degrade the
    *explanation*, never the correctness of the answer.
    """
    try:
        import pulp
    except Exception as exc:  # noqa: BLE001 - an optional dependency must not break the build
        return MilpSolution({}, 0.0, "UNAVAILABLE", False, f"PuLP unavailable: {exc}", "none")

    options: dict[str, list[RecoveryAction]] = {
        ctx.shipment_id: feasible_actions(ctx, catalogue) for ctx in contexts
    }
    total_value = sum(c.value_usd for c in contexts) or 1.0

    prob = pulp.LpProblem("sanjeevani_recovery", pulp.LpMinimize)
    x: dict[tuple[str, str], Any] = {}
    for ctx in contexts:
        for action in options[ctx.shipment_id]:
            x[(ctx.shipment_id, action.id)] = pulp.LpVariable(
                f"x_{ctx.shipment_id}_{action.id}", cat="Binary"
            )

    # Pre-compute every per-option coefficient once, from the SAME twin the exact
    # solver uses. The MILP is a reformulation of the twin, not a second model.
    cost: dict[tuple[str, str], float] = {}
    unrecovered: dict[tuple[str, str], float] = {}
    temp_weighted: dict[tuple[str, str], float] = {}
    served: dict[tuple[str, str], float] = {}
    hours: dict[tuple[str, str], float] = {}
    hub_units: dict[tuple[str, str], int] = {}
    for ctx in contexts:
        for action in options[ctx.shipment_id]:
            outcome = evaluate_plan(
                [ctx],
                {ctx.shipment_id: action.id},
                catalogue,
                scenario,
                recovery_days=recovery_days,
            )
            per = outcome.by_consignment[0]
            key = (ctx.shipment_id, action.id)
            cost[key] = per.cost_usd
            unrecovered[key] = per.unrecovered_value_usd
            temp_weighted[key] = ctx.value_usd * per.temperature_risk
            served[key] = per.served_value_usd
            hours[key] = per.recovery_hours
            if action.consumes_hub_inventory:
                hub_units[key] = ctx.units

    prob += pulp.lpSum(
        (cost[k] + weight * unrecovered[k]) * var for k, var in x.items()
    )

    for ctx in contexts:
        prob += pulp.lpSum(x[(ctx.shipment_id, a.id)] for a in options[ctx.shipment_id]) == 1

    if constraints:
        prob += (
            pulp.lpSum(cost[k] * var for k, var in x.items()) <= catalogue.max_budget_usd
        )
        prob += (
            pulp.lpSum(served[k] * var for k, var in x.items())
            >= catalogue.min_service_level * total_value
        )
        prob += (
            pulp.lpSum(temp_weighted[k] * var for k, var in x.items())
            <= catalogue.max_temperature_risk * total_value
        )
        prob += (
            pulp.lpSum(hours[k] * var for k, var in x.items())
            <= catalogue.max_recovery_hours * len(contexts)
        )
        # Multi-echelon hub capacity: one constraint per (hub, SKU) that any
        # consignment could draw on. This is the constraint a per-consignment
        # feasibility check structurally cannot express, and the reason the
        # optimizer needs a solver rather than a greedy pass.
        for hub, sku, ceiling, members in _hub_groups(contexts, options):
            prob += (
                pulp.lpSum(
                    hub_units.get((ctx.shipment_id, action.id), 0) * x[(ctx.shipment_id, action.id)]
                    for ctx in members
                    for action in options[ctx.shipment_id]
                    if action.consumes_hub_inventory
                )
                <= ceiling,
                f"hub_{hub}_{sku}",
            )

    try:
        status = prob.solve(pulp.PULP_CBC_CMD(msg=0, threads=1))
    except Exception as exc:  # noqa: BLE001 - solver failure is handled, not raised
        return MilpSolution({}, 0.0, "ERROR", False, f"CBC raised: {exc}", "none")

    label = pulp.LpStatus[status]
    if label != "Optimal":
        return MilpSolution(
            {}, 0.0, label, False, f"CBC returned {label}", "PuLP/CBC"
        )

    choices: dict[str, str] = {}
    for ctx in contexts:
        for action in options[ctx.shipment_id]:
            if x[(ctx.shipment_id, action.id)].value() > 0.5:
                choices[ctx.shipment_id] = action.id
                break
    return MilpSolution(
        choices=choices,
        objective_value=float(pulp.value(prob.objective) or 0.0),
        status=label,
        available=True,
        note="solved to optimality",
        solver="PuLP/CBC",
    )


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class OptimizationResult:
    recommended: RecoveryPlan
    ranked: list[RecoveryPlan]
    pareto_frontier: list[dict[str, Any]]
    trace: dict[str, Any]


def _plan_id(disruption: DisruptionView, event_id: str | None, scenario_id: str | None) -> str:
    digest = hashlib.sha256(
        "|".join(
            [
                event_id or "no-event",
                scenario_id or "no-scenario",
                f"H{disruption.horizon_days}",
                ",".join(disruption.blocked_nodes),
                ",".join(disruption.excursed_shipments),
            ]
        ).encode("utf-8")
    ).hexdigest()[:8].upper()
    return f"PLAN-OPT-{disruption.horizon_days}D-{digest}"


def _rationale(assignment: Assignment, contexts: list[ConsignmentContext]) -> str:
    by_id = {c.shipment_id: c for c in contexts}
    moves = sorted(
        assignment.choices.items(),
        key=lambda kv: -by_id[kv[0]].value_usd,
    )
    head = "; ".join(f"{ship} -> {action}" for ship, action in moves)
    return (
        f"Selected by M2 optimization under total economic impact "
        f"(recovery spend {assignment.outcome.total_cost_usd:,.0f} USD + expected "
        f"unrecovered value {assignment.outcome.expected_unrecovered_usd:,.0f} USD). "
        f"Actions: {head}. "
        f"Achieves service level {assignment.outcome.service_level:.4f}, residual "
        f"temperature risk {assignment.outcome.temperature_risk:.4f}, resilience "
        f"{assignment.outcome.resilience_score:.4f}, recovery "
        f"{assignment.outcome.recovery_time_hours:g}h."
    )


def _to_plan(
    assignment: Assignment,
    *,
    plan_id: str,
    contexts: list[ConsignmentContext],
    event_id: str | None,
) -> RecoveryPlan:
    return RecoveryPlan(
        plan_id=plan_id,
        strategy=assignment.strategy,
        cost=round(assignment.outcome.total_cost_usd, 2),
        service_level=round(assignment.outcome.service_level, 4),
        resilience_score=round(assignment.outcome.resilience_score, 4),
        temperature_risk=round(assignment.outcome.temperature_risk, 4),
        recovery_time_hours=round(assignment.outcome.recovery_time_hours, 2),
        compliance_status="PENDING",
        event_id=event_id,
        impacted_skus=sorted({c.sku_id for c in contexts}),
        impacted_lanes=sorted({c.lane_id for c in contexts}),
        rationale=_rationale(assignment, contexts),
        alternatives=[],
        source="optimizer",
    )


def _alternative_row(plan: RecoveryPlan, recommended: bool) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "strategy": plan.strategy,
        "cost": plan.cost,
        "service_level": plan.service_level,
        "resilience_score": plan.resilience_score,
        "temperature_risk": plan.temperature_risk,
        "recovery_time_hours": plan.recovery_time_hours,
        "recommended": recommended,
    }


def _pareto(assignments: list[Assignment]) -> list[Assignment]:
    """Non-dominated set on (cost, expected unrecovered value). Exact, not sampled."""
    ordered = sorted(
        assignments, key=lambda a: (a.outcome.total_cost_usd, a.outcome.expected_unrecovered_usd)
    )
    frontier: list[Assignment] = []
    best_loss = float("inf")
    for candidate in ordered:
        loss = candidate.outcome.expected_unrecovered_usd
        if loss < best_loss - 1e-9:
            frontier.append(candidate)
            best_loss = loss
    return frontier


def solve(
    contexts: list[ConsignmentContext],
    catalogue: Catalogue | None = None,
    scenario: ScenarioContext | None = None,
    *,
    disruption: DisruptionView | None = None,
    event_id: str | None = None,
    scenario_id: str | None = None,
    rank_limit: int = 3,
) -> OptimizationResult:
    """Full multi-objective solve. Deterministic for a fixed input."""
    cat = catalogue or build_catalogue()
    net = Network.load()
    view = disruption or derive_disruption(net, scenario_id=scenario_id, event_id=event_id)
    scn = scenario or ScenarioContext(
        scenario_id=scenario_id or view.detail.get("hero_scenario", "SCN-OPT"),
        horizon_days=float(view.horizon_days),
        duration_days=view.duration_days,
        transit_delay_days=view.transit_delay_days,
        capacity_factor=view.capacity_factor,
    )
    recovery_days = float(settings()["impact"]["service_recovery_days"])
    if not contexts:
        raise ValueError(
            "No affected consignments: there is nothing to optimize. "
            "M2 must not invent a plan for an empty disruption."
        )

    options = {c.shipment_id: feasible_actions(c, cat) for c in contexts}
    combo_count = 1
    for opts in options.values():
        combo_count *= len(opts)

    enumerated = _all_assignments(
        contexts,
        cat,
        scn,
        recovery_days=recovery_days,
        limit=EXACT_ENUMERATION_LIMIT,
    )

    weight = cat.value_of_risk_weight
    milp_default = _solve_milp(
        contexts, cat, scn, weight=weight, recovery_days=recovery_days, constraints=True
    )
    milp_grid = [
        _solve_milp(
            contexts, cat, scn, weight=w, recovery_days=recovery_days, constraints=True
        )
        for w in cat.pareto_weight_grid
    ]

    feasibility_note: str
    if enumerated is not None:
        feasible = [a for a in enumerated if a.feasible]
        needed_relaxation = not feasible
        if needed_relaxation:
            # No plan satisfies every hard ceiling. Rather than fail silently,
            # pick the plan that violates least and say so -- M3 will block it and
            # a human will see exactly why.
            feasible = sorted(enumerated, key=lambda a: (len(a.violations), a.outcome.objective_value))
        best = min(feasible, key=lambda a: (a.outcome.objective_value, a.key()))
        frontier = _pareto(enumerated)
        feasibility_note = (
            "no assignment satisfied every hard constraint; best-effort plan chosen and "
            "violations recorded"
            if needed_relaxation
            else "optimal among all feasible assignments (exhaustive)"
        )
        solver_used = "exact_enumeration"
        agreement = (
            milp_default.available and milp_default.choices == best.choices
        )
    else:
        if not milp_default.available:
            raise RuntimeError(
                "Instance is too large for exhaustive enumeration and no MILP solver is "
                f"available ({milp_default.note}). Install PuLP/CBC or reduce the plan scope."
            )
        choices = milp_default.choices
        outcome = evaluate_plan(contexts, choices, cat, scn, recovery_days=recovery_days)
        dominant = _dominant_action(choices, contexts)
        best = Assignment(
            choices=choices,
            outcome=outcome,
            dominant_action_id=dominant,
            strategy=cat.strategy_for(dominant),
            violations=_violations(outcome, cat)
            + _resource_violations(choices, contexts, cat),
        )
        frontier = []
        feasibility_note = "instance too large to verify exhaustively; MILP result used"
        solver_used = "milp_only"
        agreement = None

    plan_id = _plan_id(view, event_id, scenario_id)
    recommended = _to_plan(
        best,
        plan_id=plan_id,
        contexts=contexts,
        event_id=event_id,
    )

    # Ranked alternatives: one plan per distinct strategy, best objective first.
    # `pool` is sorted by objective and `best` is its minimum, so the first entry
    # is the recommended plan by construction -- no post-hoc patching.
    pool = sorted(
        ({a.key(): a for a in feasible}.values()),
        key=lambda a: (a.outcome.objective_value, a.key()),
    )
    ranked_assignments: list[Assignment] = []
    seen_strategies: set[str] = set()
    for candidate in pool:
        if candidate.strategy in seen_strategies:
            continue
        seen_strategies.add(candidate.strategy)
        ranked_assignments.append(candidate)
        if len(ranked_assignments) >= rank_limit:
            break

    built: list[tuple[Assignment, RecoveryPlan]] = []
    for idx, candidate in enumerate(ranked_assignments, start=1):
        built.append(
            (
                candidate,
                _to_plan(
                    candidate,
                    plan_id=f"{plan_id}-ALT{idx}",
                    contexts=contexts,
                    event_id=event_id,
                ),
            )
        )

    alternative_rows = [
        _alternative_row(candidate_plan, candidate.key() == best.key())
        for candidate, candidate_plan in built
    ]
    ranked = [candidate_plan for _, candidate_plan in built]
    recommended = recommended.model_copy(update={"alternatives": alternative_rows})

    # Hub capacity is a *combination* constraint, so the trace has to state both
    # sides of it: what the plan draws, and what the hubs hold.
    inv = load_inventory()
    hub_groups = _hub_groups(contexts, options)

    trace: dict[str, Any] = {
        "method": (
            "multi-objective binary program over a configured recovery catalogue, "
            "evaluated by a deterministic digital twin"
        ),
        "labelled_as": "optimizer",
        "solver": solver_used,
        "catalogue_version": cat.catalogue_version,
        "disruption": view.as_dict(),
        "scenario": scn.as_dict(),
        "decision_space": {
            "consignments": [c.as_dict() for c in contexts],
            "actions_considered": {
                c.shipment_id: [a.id for a in options[c.shipment_id]] for c in contexts
            },
            "combinations": combo_count,
            "exhaustive_limit": EXACT_ENUMERATION_LIMIT,
            #: True when at least one (hub, SKU) pair is contended, so a reader
            #: knows whether the hub ceiling shaped this decision space.
            "hub_ceiling_enforced": bool(hub_groups),
        },
        "objective": {
            "expression": "total_recovery_cost + w * expected_unrecovered_value",
            "value_of_risk_weight": weight,
            "selected_objective_value": round(best.outcome.objective_value, 2),
        },
        "constraints": {
            "max_budget_usd": cat.max_budget_usd,
            "min_service_level": cat.min_service_level,
            "max_temperature_risk": cat.max_temperature_risk,
            "max_recovery_hours": cat.max_recovery_hours,
            "violations": list(best.violations),
            "feasibility": feasibility_note,
        },
        # The multi-echelon constraint, stated so a reviewer can check the plan's
        # draw-down against the ceiling without recomputing anything.
        "hub_inventory": {
            "source": "data/inventory/hub_inventory.yaml",
            "as_of_utc": inv.as_of_utc,
            "provenance": inv.source,
            "contended": [
                {
                    "hub_id": hub,
                    "sku_id": sku,
                    "available_units": ceiling,
                    "demanded_units": hub_demand(best.choices, contexts, cat).get(
                        (hub, sku), 0
                    ),
                    "within_capacity": hub_demand(best.choices, contexts, cat).get(
                        (hub, sku), 0
                    )
                    <= ceiling,
                }
                for hub, sku, ceiling, _members in hub_groups
            ],
            "consignments": [
                {
                    "shipment_id": c.shipment_id,
                    "sku_id": c.sku_id,
                    "serving_hub": c.serving_hub,
                    "hub_available_units": c.hub_capacity_units,
                    "required_units": c.units,
                    "expeditable_from_hub": bool(
                        c.serving_hub and c.hub_capacity_units >= c.units
                    ),
                }
                for c in contexts
            ],
        },
        "verification": {
            "exhaustive_search_performed": enumerated is not None,
            "milp_available": milp_default.available,
            "milp_status": milp_default.status,
            "milp_note": milp_default.note,
            "milp_solver": milp_default.solver,
            "milp_choices": milp_default.choices,
            "milp_agrees_with_exhaustive": agreement,
            "pareto_points_from_grid": len(milp_grid),
        },
        "selected_plan": {
            "plan_id": plan_id,
            "strategy": recommended.strategy,
            "dominant_action": best.dominant_action_id,
            "choices": best.choices,
            "outcome": best.outcome.as_dict(),
        },
        "pareto_frontier": [
            {
                "strategy": a.strategy,
                "cost_usd": round(a.outcome.total_cost_usd, 2),
                "expected_unrecovered_usd": round(a.outcome.expected_unrecovered_usd, 2),
                "service_level": round(a.outcome.service_level, 4),
                "temperature_risk": round(a.outcome.temperature_risk, 4),
                "resilience_score": round(a.outcome.resilience_score, 4),
                "recovery_time_hours": round(a.outcome.recovery_time_hours, 2),
                "feasible": a.feasible,
                "choices": a.choices,
            }
            for a in frontier
        ],
        "computed_at": isoformat(scenario_clock()),
    }

    return OptimizationResult(
        recommended=recommended,
        ranked=ranked,
        pareto_frontier=trace["pareto_frontier"],
        trace=trace,
    )


def optimize_for_event(
    network: Network | None = None,
    *,
    event_id: str | None = None,
    scenario_id: str | None = None,
    rank_limit: int = 3,
) -> OptimizationResult:
    """The entry point the M2 provider calls: event in, optimized plan out.

    Also returns counterfactuals so a human can see what inaction would have cost.
    """
    net = network or Network.load()
    view = derive_disruption(net, scenario_id=scenario_id, event_id=event_id)
    contexts, scope = build_contexts(
        net,
        disrupted_lanes=list(view.disrupted_lanes),
        excursed_shipments=list(view.excursed_shipments),
    )
    result = solve(
        contexts,
        disruption=view,
        event_id=event_id,
        scenario_id=scenario_id,
        rank_limit=rank_limit,
    )
    result.trace["scope"] = scope
    result.trace["counterfactuals"] = _counterfactuals(contexts, view, net)
    _attach_des_evidence(result.trace, net, contexts, view)
    return result


def _attach_des_evidence(
    trace: dict[str, Any],
    network: Network,
    contexts: list[ConsignmentContext],
    view: DisruptionView,
) -> None:
    """Run the discrete-event cross-check on the recommended plan.

    Always runs, so the disagreement is always visible; only participates in the
    plan's hard constraints when ``m2.des.enabled`` is true. That split is the
    same one the duration forecaster uses, and for the same reason: evidence a
    reviewer can see, versus evidence the system acts on.
    """
    catalogue = build_catalogue()
    choices = trace["selected_plan"]["choices"]
    windows = windows_from_disruption(list(view.blocked_nodes), view.duration_days)
    des = clearance_for_plan(network, contexts, catalogue, choices, windows)
    twin_hours = float(trace["selected_plan"]["outcome"]["recovery_time_hours"])

    payload: dict[str, Any] = {
        **describe_model(),
        "recommended_plan": {
            "clearance_day": round(des.clearance_day, 4),
            "recovery_hours": round(des.recovery_hours, 2),
            "queue_peak": des.queue_peak,
            "berth_utilisation": {
                k: round(v, 4) for k, v in sorted(des.berth_utilisation.items())
            },
            "participants": [c.as_dict() for c in des.participants],
            "excluded": list(des.excluded),
            "passed_at_day": {k: round(v, 4) for k, v in sorted(des.passed_at_day.items())},
            "wait_days": {k: round(v, 4) for k, v in sorted(des.wait_days.items())},
            "mean_wait_days": round(des.mean_wait_days, 4),
            "max_wait_days": round(des.max_wait_days, 4),
            "events_processed": des.events_processed,
            "windows": [w.as_dict() for w in des.windows],
        },
        "cross_check": compare_with_twin(des, twin_hours),
        "violations": [],
    }

    if des_enabled():
        ceiling_days = catalogue.max_recovery_hours / 24.0
        if des.clearance_day > ceiling_days:
            payload["violations"].append(
                f"des: corridor traffic the plan leaves behind takes "
                f"{des.recovery_hours:.0f}h to clear, over the "
                f"{catalogue.max_recovery_hours:g}h ceiling"
            )
        if des.queue_peak and max(des.queue_peak.values(), default=0) > 0:
            payload["violations"].append(
                f"des: berth contention observed (peak backlog "
                f"{max(des.queue_peak.values())} at "
                f"{max(des.queue_peak, key=lambda k: des.queue_peak[k])})"
            )
        trace["constraints"]["violations"] = (
            list(trace["constraints"]["violations"]) + payload["violations"]
        )

    trace["des"] = payload


def _counterfactuals(
    contexts: list[ConsignmentContext],
    view: DisruptionView,
    network: Network | None = None,
) -> list[dict[str, Any]]:
    """What the alternatives would have cost. The digital twin's real payoff.

    Each entry answers "what if we had made this decision instead?" for a single
    uniform policy. That is the question a post-incident review actually asks.

    A uniform policy is evaluated even when it cannot actually be executed for
    every consignment -- whether because a hub does not hold enough stock or
    because a qualified alternate source does not exist. Skipping those rows would
    hide the most informative comparison of all: the policy that looks cheapest
    and is not executable. They are evaluated, flagged and explained instead.
    """
    catalogue = build_catalogue()
    scn = ScenarioContext(
        scenario_id="COUNTERFACTUAL",
        horizon_days=float(view.horizon_days),
        duration_days=view.duration_days,
        transit_delay_days=view.transit_delay_days,
        capacity_factor=view.capacity_factor,
    )
    recovery_days = float(settings()["impact"]["service_recovery_days"])
    rows: list[dict[str, Any]] = []
    for action in catalogue.actions:
        blocked = [c.shipment_id for c in contexts if not action.applies_to(c)]
        if blocked and not action.consumes_hub_inventory:
            # Categorically inapplicable to this disruption -- wrong temperature
            # class, or no qualified alternate source. Not a policy anyone could
            # have run, so it is not a counterfactual.
            continue
        assignment = {c.shipment_id: action.id for c in contexts}
        outcome = evaluate_plan(contexts, assignment, catalogue, scn, recovery_days=recovery_days)
        violations = list(_violations(outcome, catalogue)) + list(
            _resource_violations(assignment, contexts, catalogue)
        )
        if blocked:
            violations.append(
                f"cannot be sourced for {', '.join(blocked)}: the hub holds less than "
                "the consignment requires, or there is no downstream hub"
            )
        row: dict[str, Any] = {
            "uniform_policy": action.id,
            "label": action.label,
            "cost_usd": round(outcome.total_cost_usd, 2),
            "expected_unrecovered_usd": round(outcome.expected_unrecovered_usd, 2),
            "total_economic_impact_usd": round(
                outcome.total_cost_usd + outcome.expected_unrecovered_usd, 2
            ),
            "service_level": round(outcome.service_level, 4),
            "temperature_risk": round(outcome.temperature_risk, 4),
            # A uniform policy can look cheap on total economic impact and
            # still be un-executable, because it breaches a hard ceiling or
            # cannot be sourced at all. The flag is what stops that policy
            # being read as "the better answer".
            "executable": not violations,
            "violations": violations,
        }
        if network is not None:
            # The DES cross-check, on the same uniform policy. This is where the
            # two models are most likely to disagree, because a policy that leaves
            # traffic on the corridor is exactly what queueing penalises.
            des = clearance_for_plan(
                network,
                contexts,
                catalogue,
                assignment,
                windows_from_disruption(list(view.blocked_nodes), view.duration_days),
            )
            row["des"] = {
                "clearance_day": round(des.clearance_day, 4),
                "recovery_hours": round(des.recovery_hours, 2),
                "queue_peak": des.queue_peak,
                "mean_wait_days": round(des.mean_wait_days, 4),
                "participants": [c.shipment_id for c in des.participants],
                "clears_all_corridor_traffic": des.clearance_day <= catalogue.max_recovery_hours / 24.0,
            }
        rows.append(row)
    rows.sort(key=lambda r: (r["total_economic_impact_usd"], r["uniform_policy"]))
    return rows
