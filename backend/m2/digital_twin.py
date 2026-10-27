"""M2 -- deterministic digital twin.

WHAT THIS IS. A reproducible simulation of *what a chosen recovery action does to
this network*. Given the seeded network, the seeded device traces and a
parameterised disruption, it computes, per consignment:

    cost, expected unrecovered value, residual temperature risk, service
    contribution, resilience contribution, recovery time

and aggregates those into plan-level figures.

WHAT THIS IS NOT. It is not a stochastic simulator. There is no sampling, no
Monte Carlo and no random seed, because the audit ledger's whole claim rests on
"run it twice, get the same bytes". Everything here is closed-form arithmetic
over seeded inputs, using the *same* stockout and condemnation models M1 already
uses -- deliberately, so M2 cannot invent a more favourable world than M1
measured.

Where the twin's arithmetic mirrors A3's impact model, the shared definition is
imported (``corridor_remaining_fraction``) rather than re-derived, and a test
asserts the two agree.

    p_stockout(c, a) = 1 - exp(-max(0, outage(c, a) - coverage[S]) / recovery_days)
    outage(c, a)     = min(H, duration * a.outage_factor + delay(c, a))
    delay(c, a)      = transit_delay * remaining_fraction(c) * a.corridor_exposure_factor
                       + a.delay_delta_days
    condemn(c, a)    = base_condemn(c) * (1 - a.condemn_recovery_fraction)
    unrecovered(c, a)= value(c) * [ p_stockout * (1 - condemn) + condemn ]
    cost(c, a)       = a.fixed + a.per_pallet * pallets(c) + a.per_unit * units(c)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from backend.agents import telemetry
from backend.config import recovery_options, settings
from backend.graph.network import Network, Shipment, corridor_remaining_fraction
from backend.m2.inventory import HubInventory, hub_capacity_for, load_inventory

#: Which telemetry limit applies to a temperature class.
_TEMP_LIMIT_C = {"2_8C": 8.0, "ambient_15_25": 30.0}

#: Resilience weights. They sum to 1.0 and are asserted to do so by a test, so a
#: typo cannot silently rescale the score.
RESILIENCE_WEIGHTS = {
    "corridor_independence": 0.30,
    "temperature": 0.20,
    "speed": 0.15,
    "value_preservation": 0.15,
    "route_diversity": 0.20,
}


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


# ---------------------------------------------------------------------------
# The configured decision space
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RecoveryAction:
    """One option the optimizer may choose for one consignment."""

    id: str
    label: str
    mode: str
    description: str
    corridor_exposure_factor: float
    outage_factor: float
    delay_delta_days: float
    fixed_usd: float
    per_pallet_usd: float
    per_unit_usd: float
    temperature_multiplier: float
    temperature_floor: float
    condemn_recovery_fraction: float
    recovery_hours: float
    resilience_delta: float
    requires_alternate_source: bool
    #: True when the action draws on shared, finite downstream hub stock. Such an
    #: action is capacity-limited per consignment AND across consignments; see
    #: backend/m2/inventory.py and optimizer._resource_violations.
    consumes_hub_inventory: bool
    applies_to_temp_classes: tuple[str, ...]

    def cost_for(self, *, pallets: int, units: int) -> float:
        """Landed cost of choosing this action for one consignment."""
        return round(
            self.fixed_usd + self.per_pallet_usd * pallets + self.per_unit_usd * units,
            6,
        )

    def applies_to(self, ctx: ConsignmentContext) -> bool:
        """Feasibility of this action for this consignment.

        Two independent gates: the product's temperature class and whether a
        qualified alternate source exists, then -- for hub-sourced actions -- that
        the consignment's hub actually holds enough stock for it. The second gate
        is why the optimizer cannot "recover" a disruption out of inventory that
        was never there.
        """
        if ctx.temp_class not in self.applies_to_temp_classes:
            return False
        if self.requires_alternate_source and not ctx.alternate_source_available:
            return False
        if self.consumes_hub_inventory:
            if ctx.serving_hub is None:
                return False
            if ctx.hub_capacity_units < ctx.units:
                return False
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "mode": self.mode,
            "corridor_exposure_factor": self.corridor_exposure_factor,
            "outage_factor": self.outage_factor,
            "delay_delta_days": self.delay_delta_days,
            "recovery_hours": self.recovery_hours,
            "condemn_recovery_fraction": self.condemn_recovery_fraction,
            "consumes_hub_inventory": self.consumes_hub_inventory,
        }


@dataclass(frozen=True)
class Catalogue:
    """The parsed recovery-option catalogue plus objective weights."""

    actions: tuple[RecoveryAction, ...]
    strategy_names: dict[str, str]
    strategy_precedence: tuple[str, ...]
    value_of_risk_weight: float
    pareto_weight_grid: tuple[float, ...]
    max_budget_usd: float
    min_service_level: float
    max_temperature_risk: float
    max_recovery_hours: float
    catalogue_version: str

    def by_id(self, action_id: str) -> RecoveryAction:
        for action in self.actions:
            if action.id == action_id:
                return action
        raise KeyError(f"Unknown recovery action {action_id!r}")

    def strategy_for(self, dominant_action_id: str) -> str:
        return self.strategy_names.get(dominant_action_id, dominant_action_id)


def build_catalogue() -> Catalogue:
    """Parse config/recovery_options.yaml into typed objects.

    Not cached on purpose: a config edit must take effect on the next
    ``reload_config()`` without a second cache to remember to clear.
    """
    doc = recovery_options()
    actions: list[RecoveryAction] = []
    for raw in doc["actions"]:
        cost = raw.get("cost", {})
        temp = raw.get("temperature", {})
        actions.append(
            RecoveryAction(
                id=raw["id"],
                label=raw["label"],
                mode=raw["mode"],
                description=" ".join(str(raw.get("description", "")).split()),
                corridor_exposure_factor=float(raw["corridor_exposure_factor"]),
                outage_factor=float(raw["outage_factor"]),
                delay_delta_days=float(raw["delay_delta_days"]),
                fixed_usd=float(cost.get("fixed_usd", 0.0)),
                per_pallet_usd=float(cost.get("per_pallet_usd", 0.0)),
                per_unit_usd=float(cost.get("per_unit_usd", 0.0)),
                temperature_multiplier=float(temp.get("multiplier", 1.0)),
                temperature_floor=float(temp.get("floor", 0.0)),
                condemn_recovery_fraction=float(raw["condemn_recovery_fraction"]),
                recovery_hours=float(raw["recovery_hours"]),
                resilience_delta=float(raw.get("resilience_delta", 0.0)),
                requires_alternate_source=bool(raw.get("requires_alternate_source", False)),
                consumes_hub_inventory=bool(raw.get("consumes_hub_inventory", False)),
                applies_to_temp_classes=tuple(raw["applies_to_temp_classes"]),
            )
        )
    names = {str(k): str(v) for k, v in doc["strategy_names"].items()}
    objective = doc["objective"]
    constraints = doc["constraints"]
    return Catalogue(
        actions=tuple(actions),
        strategy_names=names,
        strategy_precedence=tuple(names.keys()),
        value_of_risk_weight=float(objective["value_of_risk_weight"]),
        pareto_weight_grid=tuple(float(w) for w in objective["pareto_weight_grid"]),
        max_budget_usd=float(constraints["max_budget_usd"]),
        min_service_level=float(constraints["min_service_level"]),
        max_temperature_risk=float(constraints["max_temperature_risk"]),
        max_recovery_hours=float(constraints["max_recovery_hours"]),
        catalogue_version=str(doc["meta"]["catalogue_version"]),
    )


# ---------------------------------------------------------------------------
# The world the twin simulates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioContext:
    scenario_id: str
    horizon_days: float
    duration_days: float
    transit_delay_days: float
    capacity_factor: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "horizon_days": self.horizon_days,
            "duration_days": self.duration_days,
            "transit_delay_days": self.transit_delay_days,
            "capacity_factor": self.capacity_factor,
        }


@dataclass(frozen=True)
class ConsignmentContext:
    """Everything the twin needs to know about one affected consignment."""

    shipment_id: str
    sku_id: str
    sku_name: str
    lane_id: str
    market: str
    current_node: str
    value_usd: float
    pallets: int
    units: int
    temp_class: str
    criticality: str
    coverage_days: float
    remaining_fraction: float
    condemn_ratio: float
    base_temperature_risk: float
    is_excursed: bool
    alternate_source_available: bool
    #: The downstream hub this consignment may draw stock from, and how much of
    #: its SKU that hub holds. Both come from the network and the inventory seed,
    #: never from configuration of the recovery action itself.
    serving_hub: str | None
    hub_capacity_units: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "shipment_id": self.shipment_id,
            "sku_id": self.sku_id,
            "sku_name": self.sku_name,
            "lane_id": self.lane_id,
            "market": self.market,
            "current_node": self.current_node,
            "value_usd": self.value_usd,
            "pallets": self.pallets,
            "units": self.units,
            "temp_class": self.temp_class,
            "criticality": self.criticality,
            "coverage_days": self.coverage_days,
            "remaining_fraction": round(self.remaining_fraction, 4),
            "condemn_ratio": round(self.condemn_ratio, 4),
            "base_temperature_risk": round(self.base_temperature_risk, 4),
            "is_excursed": self.is_excursed,
            "alternate_source_available": self.alternate_source_available,
            "serving_hub": self.serving_hub,
            "hub_capacity_units": self.hub_capacity_units,
            "expeditable_from_hub": bool(
                self.serving_hub and self.hub_capacity_units >= self.units
            ),
        }


@dataclass(frozen=True)
class ActionOutcome:
    """What one action does to one consignment."""

    shipment_id: str
    action_id: str
    cost_usd: float
    unrecovered_value_usd: float
    served_value_usd: float
    p_stockout: float
    condemn_ratio_after: float
    temperature_risk: float
    corridor_exposure: float
    outage_days: float
    delay_days: float
    recovery_hours: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "shipment_id": self.shipment_id,
            "action_id": self.action_id,
            "cost_usd": round(self.cost_usd, 2),
            "unrecovered_value_usd": round(self.unrecovered_value_usd, 2),
            "served_value_usd": round(self.served_value_usd, 2),
            "p_stockout": round(self.p_stockout, 4),
            "condemn_ratio_after": round(self.condemn_ratio_after, 4),
            "temperature_risk": round(self.temperature_risk, 4),
            "corridor_exposure": round(self.corridor_exposure, 4),
            "outage_days": round(self.outage_days, 4),
            "delay_days": round(self.delay_days, 4),
            "recovery_hours": self.recovery_hours,
        }


@dataclass(frozen=True)
class PlanOutcome:
    """Plan-level figures the optimizer scores and M3 consumes."""

    total_cost_usd: float
    expected_unrecovered_usd: float
    service_level: float
    temperature_risk: float
    resilience_score: float
    recovery_time_hours: float
    corridor_independence: float
    route_diversity: float
    by_consignment: tuple[ActionOutcome, ...]
    objective_value: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_cost_usd": round(self.total_cost_usd, 2),
            "expected_unrecovered_usd": round(self.expected_unrecovered_usd, 2),
            "service_level": round(self.service_level, 4),
            "temperature_risk": round(self.temperature_risk, 4),
            "resilience_score": round(self.resilience_score, 4),
            "recovery_time_hours": round(self.recovery_time_hours, 2),
            "corridor_independence": round(self.corridor_independence, 4),
            "route_diversity": round(self.route_diversity, 4),
            "objective_value": round(self.objective_value, 2),
            "by_consignment": [o.as_dict() for o in self.by_consignment],
        }


# ---------------------------------------------------------------------------
# Context construction
# ---------------------------------------------------------------------------


def _condemn_for(shipment_id: str, temp_class: str) -> float:
    """Telemetry-derived condemnation ratio for one consignment."""
    limit = _TEMP_LIMIT_C.get(temp_class, 30.0)
    analysis = telemetry.analyse(shipment_id, limit)
    return float(analysis.condemn_ratio) if analysis else 0.0


def _base_temperature_risk(lane_temp_class: str, red_sea_exposed: bool, condemn: float) -> float:
    """Residual temperature exposure BEFORE any recovery action.

    Two components, both from seeded facts rather than a judgement:
      * corridor exposure -- a 2-8C consignment on an exposed 2-8C corridor is
        the worst case; a 2-8C consignment on an unexposed corridor is mild; an
        ambient consignment has none.
      * damage already done -- the telemetry-derived condemnation ratio.
    """
    if lane_temp_class != "2_8C":
        return _clamp(0.4 * condemn)
    exposure = 1.0 if red_sea_exposed else 0.35
    return _clamp(0.6 * exposure + 0.4 * condemn)


def build_contexts(
    network: Network,
    *,
    disrupted_lanes: list[str],
    excursed_shipments: list[str],
    inventory: HubInventory | None = None,
) -> tuple[list[ConsignmentContext], list[str]]:
    """The consignments a recovery plan must cover, and why each is in scope.

    Scope rule (identical to A3's blast radius): a consignment is affected when
    it is on a lane the disruption touches, or when it is itself the subject of a
    cargo-damage event. Any other consignment is out of the plan's scope and is
    deliberately excluded, so the plan's service level is not inflated by
    consignments the disruption never threatened.

    Each context also carries its downstream hub and that hub's stock for its SKU,
    because a recovery action that draws on hub inventory has to know whether
    there is any. A consignment with no downstream hub gets ``None`` and zero,
    which makes such an action unavailable to it rather than free.
    """
    inv = inventory if inventory is not None else load_inventory()
    disrupted = set(disrupted_lanes)
    excursed = set(excursed_shipments)

    contexts: list[ConsignmentContext] = []
    scope_reasons: list[str] = []
    for shipment in sorted(network.shipments.values(), key=lambda s: s.id):
        on_lane = shipment.lane_id in disrupted
        is_excursed = shipment.id in excursed
        if not on_lane and not is_excursed:
            continue
        lane = network.lanes[shipment.lane_id]
        sku = network.skus[shipment.sku_id]
        condemn = _condemn_for(shipment.id, sku.temp_class)
        hub_id, hub_units = hub_capacity_for(network, shipment, inv)
        contexts.append(
            ConsignmentContext(
                shipment_id=shipment.id,
                sku_id=shipment.sku_id,
                sku_name=sku.name,
                lane_id=shipment.lane_id,
                market=shipment.market,
                current_node=shipment.current_node,
                value_usd=shipment.value_usd,
                pallets=shipment.pallets,
                units=shipment.units,
                temp_class=sku.temp_class,
                criticality=shipment.criticality,
                coverage_days=sku.coverage_days,
                remaining_fraction=corridor_remaining_fraction(shipment, lane),
                condemn_ratio=condemn,
                base_temperature_risk=_base_temperature_risk(
                    lane.temp_class, lane.red_sea_exposed, condemn
                ),
                is_excursed=is_excursed,
                alternate_source_available=_has_alternate_source(network, sku.temp_class, shipment),
                serving_hub=hub_id,
                hub_capacity_units=hub_units,
            )
        )
        reason = f"lane {shipment.lane_id} is disrupted"
        if is_excursed:
            reason = "consignment is the subject of a cargo-damage event"
        scope_reasons.append(f"{shipment.id}: {reason}")
    return contexts, scope_reasons


def _has_alternate_source(network: Network, temp_class: str, shipment: Shipment) -> bool:
    """Is there a qualified alternate supplier of the right temperature class?

    A supplier qualifies when its node temperature class matches the product's and
    it is not the consignment's own origin. This is a *network* fact, not a
    configured flag, so removing a supplier genuinely removes the option.
    """
    for node in network.nodes.values():
        if node.type != "supplier":
            continue
        if node.temp_class != temp_class:
            continue
        if node.id == shipment.origin:
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# The simulation
# ---------------------------------------------------------------------------


def evaluate_action(
    ctx: ConsignmentContext,
    action: RecoveryAction,
    scenario: ScenarioContext,
    *,
    recovery_days: float,
) -> ActionOutcome:
    """Simulate one action against one consignment. Pure arithmetic."""
    corridor_exposure = _clamp(
        ctx.remaining_fraction * action.corridor_exposure_factor
    )
    delay_days = max(
        0.0, scenario.transit_delay_days * corridor_exposure + action.delay_delta_days
    )
    outage_days = min(
        scenario.horizon_days, scenario.duration_days * action.outage_factor + delay_days
    )
    shortfall = max(0.0, outage_days - ctx.coverage_days)
    p_stockout = 1.0 - math.exp(-shortfall / recovery_days) if recovery_days > 0 else 1.0

    condemn_after = _clamp(
        ctx.condemn_ratio * (1.0 - action.condemn_recovery_fraction)
    )
    served_value = ctx.value_usd * (1.0 - p_stockout) * (1.0 - condemn_after)
    unrecovered = ctx.value_usd - served_value

    temp_risk = _residual_temperature_risk(ctx.base_temperature_risk, action)

    return ActionOutcome(
        shipment_id=ctx.shipment_id,
        action_id=action.id,
        cost_usd=action.cost_for(pallets=ctx.pallets, units=ctx.units),
        unrecovered_value_usd=unrecovered,
        served_value_usd=served_value,
        p_stockout=p_stockout,
        condemn_ratio_after=condemn_after,
        temperature_risk=temp_risk,
        corridor_exposure=corridor_exposure,
        outage_days=outage_days,
        delay_days=delay_days,
        recovery_hours=action.recovery_hours,
    )


def _residual_temperature_risk(base: float, action: RecoveryAction) -> float:
    """Temperature risk after the action. An ambient consignment has none."""
    if base <= 0.0:
        return 0.0
    return _clamp(max(action.temperature_floor, action.temperature_multiplier * base))


def evaluate_plan(
    contexts: list[ConsignmentContext],
    assignment: dict[str, str],
    catalogue: Catalogue,
    scenario: ScenarioContext,
    *,
    recovery_days: float | None = None,
) -> PlanOutcome:
    """Aggregate a full consignment -> action assignment into plan figures."""
    recovery = (
        float(settings()["impact"]["service_recovery_days"])
        if recovery_days is None
        else recovery_days
    )
    total_value = sum(c.value_usd for c in contexts)
    outcomes: list[ActionOutcome] = []
    for ctx in contexts:
        action = catalogue.by_id(assignment[ctx.shipment_id])
        outcomes.append(evaluate_action(ctx, action, scenario, recovery_days=recovery))

    total_cost = sum(o.cost_usd for o in outcomes)
    unrecovered = sum(o.unrecovered_value_usd for o in outcomes)
    served = sum(o.served_value_usd for o in outcomes)

    service_level = served / total_value if total_value else 0.0
    temperature_risk = (
        sum(c.value_usd * o.temperature_risk for c, o in zip(contexts, outcomes, strict=True))
        / total_value
        if total_value
        else 0.0
    )
    corridor_independence = (
        1.0
        - sum(c.value_usd * o.corridor_exposure for c, o in zip(contexts, outcomes, strict=True))
        / total_value
        if total_value
        else 1.0
    )
    recovery_hours = max((o.recovery_hours for o in outcomes), default=0.0)
    loss_ratio = unrecovered / total_value if total_value else 0.0
    modes = {catalogue.by_id(a).mode for a in assignment.values()}
    route_diversity = len(modes) / max(len({a.mode for a in catalogue.actions}), 1)

    resilience = _clamp(
        RESILIENCE_WEIGHTS["corridor_independence"] * corridor_independence
        + RESILIENCE_WEIGHTS["temperature"] * (1.0 - temperature_risk)
        + RESILIENCE_WEIGHTS["speed"] * (1.0 - recovery_hours / 720.0)
        + RESILIENCE_WEIGHTS["value_preservation"] * (1.0 - loss_ratio)
        + RESILIENCE_WEIGHTS["route_diversity"] * route_diversity
    )

    return PlanOutcome(
        total_cost_usd=total_cost,
        expected_unrecovered_usd=unrecovered,
        service_level=service_level,
        temperature_risk=temperature_risk,
        resilience_score=resilience,
        recovery_time_hours=recovery_hours,
        corridor_independence=corridor_independence,
        route_diversity=route_diversity,
        by_consignment=tuple(outcomes),
        objective_value=round(
            total_cost + catalogue.value_of_risk_weight * unrecovered, 6
        ),
    )


def feasible_actions(ctx: ConsignmentContext, catalogue: Catalogue) -> list[RecoveryAction]:
    """Every configured action this consignment may legitimately be given."""
    options = [a for a in catalogue.actions if a.applies_to(ctx)]
    if not options:
        raise ValueError(
            f"No feasible recovery action for {ctx.shipment_id} "
            f"(temp_class={ctx.temp_class}, alternate_source="
            f"{ctx.alternate_source_available}). The catalogue is missing a case."
        )
    return options
