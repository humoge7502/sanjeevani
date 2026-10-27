"""M2 -- multi-echelon hub inventory.

WHAT THIS IS. The finite, *shared* stock a recovery action may draw on. Before
this module the `EXPEDITE_FROM_HUB` action was unconstrained: every affected
consignment could be "covered" from a hub, at full benefit, with no limit on how
much was taken or by how many plans. A recovery model whose cheapest option
draws on unlimited inventory is not a model, it is an assumption wearing a cost.

WHAT THIS IS NOT. It is not inventory optimisation in the research sense: there is
no reorder policy, no safety-stock computation, no echelon-holdback and no
service-level optimisation. What exists is the *constraint* -- a per-(hub, SKU)
ceiling that several consignments contend for -- which is the part that changes
the optimizer's answer, and the part a reviewer can check by hand.

HOW A CONSIGNMENT FINDS ITS HUB. Deterministically, from the lane it is on: the
first warehouse-type node at or after the consignment's current position. That is
"the next hub you would reach", it is derived from seeded network data, and it
cannot be talked into an answer -- a consignment on a lane with no warehouse
downstream simply has no hub to draw from, and the action is unavailable to it.

    data/inventory/hub_inventory.yaml   ->   HubInventory
    HUB avail(h, s)                     ->   the ceiling
    sum(units of c : c takes EXPEDITE from h for sku s)  <=  avail(h, s)
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any

from backend.config import DATA_DIR
from backend.graph.network import Network, Shipment

INVENTORY_VERSION = "1.0.0"


@dataclass(frozen=True)
class HubInventory:
    """Available-to-promise units per hub and SKU, at the scenario's t0."""

    as_of_utc: str
    source: str
    positions: dict[str, dict[str, int]]

    def available(self, hub_id: str, sku_id: str) -> int:
        """Units on hand for this hub/SKU. Absent means zero, and that is stated."""
        return int(self.positions.get(hub_id, {}).get(sku_id, 0))

    def hub_ids(self) -> list[str]:
        return sorted(self.positions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": INVENTORY_VERSION,
            "as_of_utc": self.as_of_utc,
            "source": self.source,
            "positions": {h: dict(sorted(p.items())) for h, p in sorted(self.positions.items())},
        }


def _read_inventory() -> dict[str, Any]:
    import yaml

    path = DATA_DIR / "inventory" / "hub_inventory.yaml"
    if not path.exists():  # pragma: no cover - configuration error path
        raise FileNotFoundError(
            f"Hub inventory seed missing: {path}. "
            "Without it the multi-echelon constraint has no ceiling to enforce."
        )
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@functools.cache
def load_inventory() -> HubInventory:
    """Parse the hub inventory seed. Cached; clear with ``reset_inventory_cache``."""
    doc = _read_inventory()
    positions: dict[str, dict[str, int]] = {}
    for hub_id, entry in (doc.get("hubs") or {}).items():
        raw = (entry or {}).get("positions") or {}
        positions[str(hub_id)] = {str(sku): int(units) for sku, units in raw.items()}
    meta = doc.get("meta") or {}
    return HubInventory(
        as_of_utc=str(meta.get("as_of_utc") or ""),
        source=str(meta.get("source") or "unknown"),
        positions=positions,
    )


def reset_inventory_cache() -> None:
    load_inventory.cache_clear()


# ---------------------------------------------------------------------------
# Which hub a consignment can draw from
# ---------------------------------------------------------------------------


def serving_hub(network: Network, shipment: Shipment) -> str | None:
    """The first warehouse at or downstream of the consignment, on its lane.

    "At or downstream": a consignment sitting in a hub's yard draws on that hub,
    not on the one after it. Returns None when the lane has no warehouse from the
    consignment's position onward -- and then there is no hub to expedite from,
    which is a fact about the network rather than a default to fall back on.
    """
    lane = network.lanes.get(shipment.lane_id)
    if lane is None:
        return None
    path = lane.path
    start = path.index(shipment.current_node) if shipment.current_node in path else 0
    for node_id in path[start:]:
        node = network.nodes.get(node_id)
        if node is not None and node.type == "warehouse":
            return node_id
    return None


def hub_capacity_for(
    network: Network,
    shipment: Shipment,
    inventory: HubInventory | None = None,
) -> tuple[str | None, int]:
    """The consignment's hub and the units available there for its SKU."""
    inv = inventory or load_inventory()
    hub_id = serving_hub(network, shipment)
    if hub_id is None:
        return None, 0
    return hub_id, inv.available(hub_id, shipment.sku_id)
