"""The synthetic 12-node pharma network and its derived directed graph.

Design decision (ADR-002): lanes are *commercial routes* that carry an ordered
list of waypoints. The graph A3 traverses is DERIVED from those routes, so
"which commercial corridor is this shipment on" and "what does the disruption
reach" can never disagree.

Everything here is loaded from seeded YAML. Loading twice yields identical
objects, which is what makes the Impact numbers reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import networkx as nx
import yaml

from backend.config import DATA_DIR


@dataclass(frozen=True)
class Node:
    id: str
    type: str  # supplier | plant | port | warehouse | customer
    name: str
    country: str
    city: str
    temp_class: str
    risk_score: float
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class Lane:
    id: str
    name: str
    origin: str
    destination: str
    waypoints: tuple[str, ...]
    mode: str
    transit_days: float
    cost_usd_per_pallet: float
    capacity_pallets: int
    temp_class: str
    red_sea_exposed: bool
    direction: str

    @property
    def path(self) -> tuple[str, ...]:
        """Full node sequence: origin -> waypoints -> destination."""
        return (self.origin, *self.waypoints, self.destination)

    @property
    def edges(self) -> tuple[tuple[str, str], ...]:
        """Consecutive node pairs. This is the graph contribution of the lane."""
        p = self.path
        return tuple((p[i], p[i + 1]) for i in range(len(p) - 1))

    def hop_transit_days(self) -> dict[tuple[str, str], float]:
        """Transit time apportioned evenly across the lane's hops."""
        pairs = self.edges
        per_hop = self.transit_days / max(len(pairs), 1)
        return {pair: round(per_hop, 6) for pair in pairs}


@dataclass(frozen=True)
class Sku:
    id: str
    name: str
    form: str
    temp_class: str
    unit_price_usd: float
    coverage_days: float
    margin_band: str
    criticality: str
    gdp_sensitive: bool


@dataclass(frozen=True)
class Shipment:
    id: str
    sku_id: str
    lane_id: str
    market: str
    units: int
    pallets: int
    origin: str
    current_node: str
    destination: str
    temp_class: str
    telemetry_profile: str
    depart_day: float
    eta_day: float
    criticality: str
    unit_price_usd: float

    @property
    def value_usd(self) -> float:
        """Consignment value. Derived from seeded units x SKU price, never stored."""
        return round(self.units * self.unit_price_usd, 2)


class Network:
    """The seeded supply network plus its derived graph."""

    def __init__(
        self,
        nodes: dict[str, Node],
        lanes: dict[str, Lane],
        skus: dict[str, Sku],
        shipments: dict[str, Shipment],
        meta: dict[str, Any],
    ) -> None:
        self.nodes = nodes
        self.lanes = lanes
        self.skus = skus
        self.shipments = shipments
        self.meta = meta
        self.graph = self._build_graph()

    # ------------------------------------------------------------------ build
    @staticmethod
    def _load_yaml(name: str) -> dict[str, Any]:
        with (DATA_DIR / name).open("r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    @classmethod
    @lru_cache(maxsize=1)
    def load(cls) -> "Network":
        """Load and cache the seeded network."""
        nodes_doc = cls._load_yaml("network/nodes.yaml")
        lanes_doc = cls._load_yaml("network/lanes.yaml")
        skus_doc = cls._load_yaml("products/skus.yaml")
        ships_doc = cls._load_yaml("shipments/shipments.yaml")

        nodes = {
            n["id"]: Node(
                id=n["id"],
                type=n["type"],
                name=n["name"],
                country=n["country"],
                city=n["city"],
                temp_class=n["temp_class"],
                risk_score=float(n.get("risk_score", 0.0)),
                raw=n,
            )
            for n in nodes_doc["nodes"]
        }
        lanes = {
            l["id"]: Lane(
                id=l["id"],
                name=l["name"],
                origin=l["origin"],
                destination=l["destination"],
                waypoints=tuple(l.get("waypoints") or ()),
                mode=l["mode"],
                transit_days=float(l["transit_days"]),
                cost_usd_per_pallet=float(l["cost_usd_per_pallet"]),
                capacity_pallets=int(l["capacity_pallets"]),
                temp_class=l["temp_class"],
                red_sea_exposed=bool(l.get("red_sea_exposed", False)),
                direction=l.get("direction", "outbound"),
            )
            for l in lanes_doc["lanes"]
        }
        skus = {
            s["id"]: Sku(
                id=s["id"],
                name=s["name"],
                form=s["form"],
                temp_class=s["temp_class"],
                unit_price_usd=float(s["unit_price_usd"]),
                coverage_days=float(s["coverage_days"]),
                margin_band=s["margin_band"],
                criticality=s["criticality"],
                gdp_sensitive=bool(s["gdp_sensitive"]),
            )
            for s in skus_doc["skus"]
        }
        shipments = {
            s["id"]: Shipment(
                id=s["id"],
                sku_id=s["sku_id"],
                lane_id=s["lane_id"],
                market=s["market"],
                units=int(s["units"]),
                pallets=int(s["pallets"]),
                origin=s["origin"],
                current_node=s["current_node"],
                destination=s["destination"],
                temp_class=s["temp_class"],
                telemetry_profile=s["telemetry_profile"],
                depart_day=float(s["depart_day"]),
                eta_day=float(s["eta_day"]),
                criticality=s["criticality"],
                unit_price_usd=skus[s["sku_id"]].unit_price_usd,
            )
            for s in ships_doc["shipments"]
        }
        # Referential integrity is asserted at load time: a typo in seed data
        # must fail fast, not produce a silently wrong impact number.
        for lane in lanes.values():
            for node_id in lane.path:
                if node_id not in nodes:
                    raise ValueError(f"Lane {lane.id} references unknown node {node_id}")
        for ship in shipments.values():
            if ship.lane_id not in lanes:
                raise ValueError(f"Shipment {ship.id} references unknown lane {ship.lane_id}")
            if ship.sku_id not in skus:
                raise ValueError(f"Shipment {ship.id} references unknown SKU {ship.sku_id}")

        meta = {
            "network": nodes_doc.get("meta", {}),
            "lanes": lanes_doc.get("meta", {}),
            "skus": skus_doc.get("meta", {}),
            "shipments": ships_doc.get("meta", {}),
            "node_count": len(nodes),
            "lane_count": len(lanes),
        }
        return cls(nodes, lanes, skus, shipments, meta)

    def _build_graph(self) -> nx.DiGraph:
        g = nx.DiGraph()
        for node in self.nodes.values():
            g.add_node(
                node.id,
                type=node.type,
                name=node.name,
                temp_class=node.temp_class,
                risk_score=node.risk_score,
                country=node.country,
            )
        for lane in self.lanes.values():
            for a, b in lane.edges:
                if g.has_edge(a, b):
                    g[a][b]["lane_ids"].append(lane.id)
                else:
                    g.add_edge(
                        a,
                        b,
                        lane_ids=[lane.id],
                        mode=lane.mode,
                        temp_class=lane.temp_class,
                        red_sea_exposed=lane.red_sea_exposed,
                        transit_days=lane.hop_transit_days()[(a, b)],
                    )
        return g

    # ------------------------------------------------------------------ query
    def lanes_touching_node(self, node_id: str) -> list[Lane]:
        return [l for l in self.lanes.values() if node_id in l.path]

    def lanes_touching_edge(self, a: str, b: str) -> list[Lane]:
        return [l for l in self.lanes.values() if (a, b) in l.edges]

    def shipments_on_lane(self, lane_id: str) -> list[Shipment]:
        return [s for s in self.shipments.values() if s.lane_id == lane_id]

    def shipments_carrying_sku(self, sku_id: str) -> list[Shipment]:
        return [s for s in self.shipments.values() if s.sku_id == sku_id]

    def downstream_of(self, node_ids: list[str]) -> set[str]:
        """All nodes reachable FROM the given nodes (disruption propagation)."""
        reachable: set[str] = set()
        for nid in node_ids:
            if nid in self.graph:
                reachable |= nx.descendants(self.graph, nid)
        return reachable

    def upstream_of(self, node_ids: list[str]) -> set[str]:
        reachable: set[str] = set()
        for nid in node_ids:
            if nid in self.graph:
                reachable |= nx.ancestors(self.graph, nid)
        return reachable

    def red_sea_lanes(self) -> list[Lane]:
        return [l for l in self.lanes.values() if l.red_sea_exposed]

    def snapshot(self) -> dict[str, Any]:
        """Serialisable view for the frontend network map."""
        return {
            "meta": self.meta,
            "nodes": [
                {
                    "id": n.id,
                    "type": n.type,
                    "name": n.name,
                    "country": n.country,
                    "city": n.city,
                    "temp_class": n.temp_class,
                    "risk_score": n.risk_score,
                    "closed": False,
                    "detail": n.raw,
                }
                for n in self.nodes.values()
            ],
            "lanes": [
                {
                    "id": l.id,
                    "name": l.name,
                    "origin": l.origin,
                    "destination": l.destination,
                    "path": list(l.path),
                    "mode": l.mode,
                    "transit_days": l.transit_days,
                    "cost_usd_per_pallet": l.cost_usd_per_pallet,
                    "temp_class": l.temp_class,
                    "red_sea_exposed": l.red_sea_exposed,
                    "direction": l.direction,
                }
                for l in self.lanes.values()
            ],
            "shipments": [
                {
                    "id": s.id,
                    "sku_id": s.sku_id,
                    "lane_id": s.lane_id,
                    "market": s.market,
                    "units": s.units,
                    "pallets": s.pallets,
                    "current_node": s.current_node,
                    "destination": s.destination,
                    "temp_class": s.temp_class,
                    "value_usd": s.value_usd,
                    "criticality": s.criticality,
                }
                for s in self.shipments.values()
            ],
            "skus": [
                {
                    "id": s.id,
                    "name": s.name,
                    "form": s.form,
                    "temp_class": s.temp_class,
                    "unit_price_usd": s.unit_price_usd,
                    "coverage_days": s.coverage_days,
                    "criticality": s.criticality,
                }
                for s in self.skus.values()
            ],
        }
