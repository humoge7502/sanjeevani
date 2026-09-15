"""A3 -- Network Impact Agent (Member 1).

Quantifies what a scenario does to THIS network. Every number is arithmetic over
seeded data plus graph reachability. There is no model, no sampling and no LLM
anywhere in this file -- call it twice and you get byte-identical output, which
is what makes the audit ledger meaningful.

Pipeline:
  disrupted nodes
    -> lane blast radius (lanes whose path touches a disrupted node)
    -> reachability closure (networkx descendants)
    -> affected consignments (with per-shipment remaining-corridor exposure)
    -> per-horizon stockout + condemnation
    -> revenue_at_risk / stockout_probability / service_level_risk

Every intermediate value lands in `trace` so the UI can show its origin.

Formulas (all documented, all tested):

  remaining_fraction(s) = corridor_hops_remaining(s) / corridor_hops_total
  delay_days(s)         = transit_delay_days x remaining_fraction(s)

  gap_days(H, s)        = min(H, duration_days + delay_days(s))
  shortfall_days(s, H)  = max(0, gap_days(H, s) - coverage_days(sku))
  p_stockout(s, H)      = 1 - exp(-shortfall_days(s, H) / service_recovery_days)

The horizon CAP in `gap_days` matters: a 3-day view genuinely cannot see damage
from a 10-day closure that existing cover absorbs, while the 30-day view exposes
the accumulated gap. That divergence is the product's point, not an artefact.

  condemn_ratio(s)      = see backend.agents.telemetry.condemn_ratio (horizon-independent)
  value_at_risk(s, H)   = value(s) x [ p_stockout(s,H) x (1 - condemn_ratio(s))
                                        + condemn_ratio(s) ]

  revenue_at_risk(H)        = sum_s value_at_risk(s, H)
  stockout_probability(H)   = sum_s value(s) x p_stockout(s,H) / sum_s value(s)
  service_level_risk(H)     = sum_market share_m x max(0, target_m - (1 - p_market))
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from backend.agents import telemetry
from backend.agents.verification import Scenario, VerifiedEvent
from backend.config import Thresholds, isoformat, scenario_clock, scenarios_config, settings
from backend.graph.network import Network, Shipment


@dataclass
class ShipmentImpact:
    shipment_id: str
    sku_id: str
    sku_name: str
    lane_id: str
    market: str
    current_node: str
    value_usd: float
    coverage_days: float
    remaining_fraction: float
    delay_days: float
    disrupted_days: float
    shortfall_days: float
    p_stockout: float
    condemn_ratio: float
    value_at_risk_usd: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "shipment_id": self.shipment_id,
            "sku_id": self.sku_id,
            "sku_name": self.sku_name,
            "lane_id": self.lane_id,
            "market": self.market,
            "current_node": self.current_node,
            "value_usd": round(self.value_usd, 2),
            "coverage_days": self.coverage_days,
            "remaining_fraction": round(self.remaining_fraction, 4),
            "delay_days": round(self.delay_days, 4),
            "disrupted_days": round(self.disrupted_days, 4),
            "shortfall_days": round(self.shortfall_days, 4),
            "p_stockout": round(self.p_stockout, 4),
            "condemn_ratio": round(self.condemn_ratio, 4),
            "value_at_risk_usd": round(self.value_at_risk_usd, 2),
            "reasons": self.reasons,
        }


@dataclass
class ImpactResult:
    impact: dict[str, Any]
    detail: dict[str, Any]


def _disrupted_nodes(
    events: list[VerifiedEvent], network: Network
) -> tuple[list[str], list[str], dict[str, Any]]:
    """Nodes the events take out of service, and the lanes they touch."""
    blocked: set[str] = set()
    evidence: dict[str, Any] = {}
    exercised_shipments: list[str] = []

    for event in events:
        if event.event_type == "PORT_CLOSURE":
            if event.target in network.nodes:
                blocked.add(event.target)
                evidence[event.event_id] = {
                    "kind": "node_out_of_service",
                    "node": event.target,
                    "why": f"{event.event_type} on {event.target}",
                }
        elif event.event_type == "COLD_CHAIN_EXCURSION":
            # A temperature excursion does not close a node; it damages cargo.
            exercised_shipments.append(event.target)
            evidence[event.event_id] = {
                "kind": "cargo_damage",
                "shipment": event.target,
                "why": f"{event.event_type} breaches the consignment's temperature limit",
            }

    lanes: list[str] = []
    for node_id in sorted(blocked):
        for lane in network.lanes_touching_node(node_id):
            if lane.id not in lanes:
                lanes.append(lane.id)
    lanes.sort()
    return sorted(blocked), lanes, {**evidence, "excursed_shipments": exercised_shipments}


def _remaining_fraction(shipment: Shipment, network: Network) -> float:
    """How much of the disrupted corridor this consignment has left to travel."""
    lane = network.lanes[shipment.lane_id]
    path = lane.path
    if shipment.current_node not in path:
        return 1.0
    idx = path.index(shipment.current_node)
    total_hops = max(len(path) - 1, 1)
    remaining = max(len(path) - 1 - idx, 0)
    return remaining / total_hops


def _excursion_for(shipment: Shipment) -> dict[str, Any] | None:
    limit = 8.0 if shipment.temp_class == "2_8C" else 30.0
    analysis = telemetry.analyse(shipment.id, limit)
    return telemetry.as_dict(analysis) if analysis else None


def compute_impact(
    events: list[VerifiedEvent],
    scenario: Scenario,
    network: Network | None = None,
) -> ImpactResult:
    """Compute the Impact contract for one scenario horizon."""
    net = network or Network.load()
    thresholds = Thresholds.load()
    imp_cfg = settings()["impact"]
    recovery_days = float(imp_cfg["service_recovery_days"])
    markets = {m["id"]: float(m["service_target"]) for m in scenarios_config()["markets"]}

    blocked_nodes, disrupted_lanes, disruption_evidence = _disrupted_nodes(events, net)

    # ---- node blast radius -------------------------------------------------
    lane_nodes: set[str] = set()
    for lane_id in disrupted_lanes:
        lane_nodes.update(net.lanes[lane_id].path)
    downstream = net.downstream_of(blocked_nodes)
    upstream = net.upstream_of(blocked_nodes)
    affected_nodes = sorted(lane_nodes | downstream)

    # ---- affected consignments --------------------------------------------
    excursed = set(disruption_evidence.get("excursed_shipments", []))
    candidates = {
        s.id: s
        for s in net.shipments.values()
        if s.lane_id in set(disrupted_lanes) or s.id in excursed
    }

    horizon = float(scenario.horizon_days)
    rows: list[ShipmentImpact] = []
    for ship in sorted(candidates.values(), key=lambda s: s.id):
        sku = net.skus[ship.sku_id]
        frac = _remaining_fraction(ship, net)
        delay = round(scenario.transit_delay_days * frac, 6)
        # Total replenishment outage, capped by the horizon we are examining.
        disrupted_days = min(horizon, scenario.duration_days + delay)
        shortfall = max(0.0, disrupted_days - sku.coverage_days)
        p_stockout = 1.0 - math.exp(-shortfall / recovery_days) if recovery_days else 1.0

        excursion = _excursion_for(ship)
        condemn = float(excursion["condemn_ratio"]) if excursion else 0.0
        if not sku.gdp_sensitive:
            condemn = 0.0  # temperature damage only applies to cold-chain product

        var = ship.value_usd * (p_stockout * (1 - condemn) + condemn)

        # The multi-line strings are parenthesised deliberately. Adjacent string
        # literals concatenate implicitly, which means a missing comma between two
        # entries would silently merge them instead of raising. The parentheses
        # make the intended continuation explicit.
        reasons = [
            f"on disrupted lane {ship.lane_id}",
            (
                f"remaining corridor fraction {frac:.3f} of "
                f"{scenario.transit_delay_days:g}d delay -> {delay:.2f}d"
            ),
            (
                f"outage gap = min(H={horizon:g}d, duration "
                f"{scenario.duration_days:g}d + delay {delay:.2f}d) = {disrupted_days:.2f}d"
            ),
            f"cover {sku.coverage_days:g}d -> shortfall {shortfall:.2f}d",
            f"p_stockout = 1 - exp(-{shortfall:.2f}/{recovery_days:g}) = {p_stockout:.4f}",
        ]
        if ship.id in excursed:
            reasons.append(f"consignment is on the excursion event(s): {sorted(excursed)}")
        if condemn > 0:
            reasons.append(f"condemned fraction {condemn:.4f} from device trace")

        rows.append(
            ShipmentImpact(
                shipment_id=ship.id,
                sku_id=ship.sku_id,
                sku_name=sku.name,
                lane_id=ship.lane_id,
                market=ship.market,
                current_node=ship.current_node,
                value_usd=ship.value_usd,
                coverage_days=sku.coverage_days,
                remaining_fraction=frac,
                delay_days=delay,
                disrupted_days=disrupted_days,
                shortfall_days=shortfall,
                p_stockout=p_stockout,
                condemn_ratio=condemn,
                value_at_risk_usd=var,
                reasons=reasons,
            )
        )

    total_value = sum(r.value_usd for r in rows)
    revenue_at_risk = sum(r.value_at_risk_usd for r in rows)
    stockout_probability = (
        sum(r.value_usd * r.p_stockout for r in rows) / total_value if total_value else 0.0
    )

    # ---- service-level risk, per market ------------------------------------
    by_market: dict[str, list[ShipmentImpact]] = {}
    for row in rows:
        by_market.setdefault(row.market, []).append(row)

    sl_rows: list[dict[str, Any]] = []
    weighted_breach = 0.0
    for market, mrows in sorted(by_market.items()):
        mvalue = sum(r.value_usd for r in mrows)
        p_market = (
            sum(r.value_usd * r.p_stockout for r in mrows) / mvalue if mvalue else 0.0
        )
        target = markets.get(market, 0.95)
        achieved = 1.0 - p_market
        breach = max(0.0, target - achieved)
        weighted_breach += mvalue * breach
        sl_rows.append(
            {
                "market": market,
                "service_target": target,
                "achieved_service_level": round(achieved, 4),
                "breach_points": round(breach, 4),
                "value_weight": round(mvalue, 2),
                "formula": f"max(0, {target} - (1 - {p_market:.4f})) = {breach:.4f}",
            }
        )
    service_level_risk = weighted_breach / total_value if total_value else 0.0

    affected_products = sorted({r.sku_id for r in rows})
    primary_event = events[0] if events else None

    rounding = imp_cfg["rounding"]
    impact = {
        "event_id": primary_event.event_id if primary_event else scenario.event_id,
        "affected_nodes": affected_nodes,
        "affected_shipments": [r.shipment_id for r in rows],
        "revenue_at_risk": round(revenue_at_risk, rounding["money_decimals"]),
        "stockout_probability": round(stockout_probability, rounding["ratio_decimals"]),
        "service_level_risk": round(service_level_risk, rounding["ratio_decimals"]),
        # additive + optional
        "affected_products": affected_products,
        "scenario_id": scenario.scenario_id,
        "horizon_days": scenario.horizon_days,
        "related_event_ids": [e.event_id for e in events],
        "trace": {},
    }

    trace = {
        "method": "deterministic graph propagation + closed-form stockout model",
        "labelled_as": "deterministic",
        "inputs": {
            "scenario_id": scenario.scenario_id,
            "horizon_days": scenario.horizon_days,
            "duration_days": scenario.duration_days,
            "transit_delay_days": scenario.transit_delay_days,
            "capacity_factor": scenario.capacity_factor,
        },
        "disruption": disruption_evidence,
        "graph": {
            "blocked_nodes": blocked_nodes,
            "disrupted_lanes": disrupted_lanes,
            "nodes_on_disrupted_lanes": sorted(lane_nodes),
            "downstream_of_blocked": sorted(downstream),
            "upstream_of_blocked": sorted(upstream),
            "affected_nodes": affected_nodes,
            # The union symbol is deliberate: this string is rendered as the
            # provenance of the affected-node set, and set union is what it is.
            "formula": "affected = nodes(lanes touching blocked) ∪ descendants(blocked)",  # noqa: RUF001
        },
        "aggregation": {
            "total_consignment_value_usd": round(total_value, 2),
            "revenue_at_risk_usd": round(revenue_at_risk, 2),
            "revenue_at_risk_formula": "sum_s value_s x [p_s x (1 - condemn_s) + condemn_s]",
            "stockout_probability_formula": "sum_s value_s x p_s / sum_s value_s",
            "service_level_risk_formula": (
                "sum_market value_market x max(0, target - (1 - p_market)) / sum value"
            ),
        },
        "consignments": [r.as_dict() for r in rows],
        "service_level_by_market": sl_rows,
        "excursion_analyses": {
            r.shipment_id: _excursion_for(net.shipments[r.shipment_id]) for r in rows
        },
        "thresholds": {
            "service_recovery_days": recovery_days,
            "markets": markets,
            "escalate_confidence": thresholds.escalate_confidence,
        },
        "config_keys": [
            "impact.service_recovery_days",
            "impact.cold_chain",
            "impact.rounding",
            "config/scenarios.yaml:markets",
        ],
    }
    impact["trace"] = trace

    detail = {
        "agent": "A3_NETWORK_IMPACT",
        "computed_at": isoformat(scenario_clock()),
        "scenario": scenario.as_dict(),
        "impact": impact,
        "consignments": [r.as_dict() for r in rows],
        "service_level_by_market": sl_rows,
        "graph": trace["graph"],
    }
    return ImpactResult(impact=impact, detail=detail)


def impact_for_all_horizons(
    events: list[VerifiedEvent], scenarios: list[Scenario], network: Network | None = None
) -> list[dict[str, Any]]:
    """Impact for every scenario horizon.

    All verified events contribute to every horizon. That is deliberate: the hero
    is a *fused* disruption (corridor closure + the excursion it caused), and the
    excursion's cargo damage is horizon-independent. The corridor parameters
    still come from the scenario, which is what makes the three horizons differ.
    """
    net = network or Network.load()
    out: list[dict[str, Any]] = []
    for scenario in scenarios:
        result = compute_impact(events, scenario, net)
        out.append(
            {
                "scenario_id": scenario.scenario_id,
                "horizon_days": scenario.horizon_days,
                "probability_weight": round(scenario.probability_weight, 4),
                **result.impact,
            }
        )
    return out
