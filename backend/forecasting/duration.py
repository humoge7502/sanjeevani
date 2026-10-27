"""Forecasting: a trained disruption-duration prior, with no runtime ML stack.

    duration.py  load the exported tree ensemble and evaluate it in pure Python

WHY THERE IS NO SCIKIT-LEARN IMPORT HERE. The model is trained by
``scripts/train_forecasting_model.py`` (a development-time dependency) and
exported to portable JSON of split thresholds and leaf values. Serving it is a
tree walk, so the runtime image needs no numpy, no scipy, no joblib and no pickle
deserialisation -- the last of which is a well-known remote-code-execution
primitive when an artifact can be swapped.

The evaluator is verified against scikit-learn's own ``predict`` output in
``tests/unit/test_forecasting.py``. An export that nobody checks is a guess.

THE HONESTY BOUNDARY. This model is **advisory**. It produces a duration *prior*
that A2 may attach to a scenario's provenance, clearly labelled. It never
produces a number that reaches a shared contract unless
``forecasting.enabled`` is switched on, and even then A2 clamps it against the
configured bounds. No language model is involved anywhere in this path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from backend.config import DATA_DIR, settings

#: The exported artifact and its sibling metadata.
MODEL_PATH: Final[Path] = DATA_DIR / "ml" / "disruption_duration_model.json"

PROVENANCE: Final[str] = "trained_model"


class ForecastUnavailable(RuntimeError):
    """Raised when no model artifact is present or it is unusable.

    Callers must treat this as "no forecast", never as "prediction of zero". A
    missing model that silently returns a number is worse than no model at all.
    """


@dataclass(frozen=True)
class DurationForecast:
    """One prediction, with everything needed to justify or discount it."""

    duration_days: float
    interval_low_days: float
    interval_high_days: float
    model_version: str
    provenance: str
    features_used: dict[str, float]
    method: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "duration_days": round(self.duration_days, 3),
            "interval_low_days": round(self.interval_low_days, 3),
            "interval_high_days": round(self.interval_high_days, 3),
            "model_version": self.model_version,
            "provenance": self.provenance,
            "features_used": {k: round(v, 4) for k, v in self.features_used.items()},
            "method": self.method,
        }


class TreeEnsemble:
    """A portable gradient-boosted tree ensemble.

    The evaluation is deliberately the boring, literal one:

        prediction = base_value + learning_rate * sum(tree_leaf(node))

    so it can be read against the export by eye.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self.features: list[str] = list(payload["features"])
        self.learning_rate: float = float(payload["learning_rate"])
        self.base_value: float = float(payload["base_value"])
        self.trees: list[list[dict[str, Any]]] = [t["nodes"] for t in payload["trees"]]
        self.meta: dict[str, Any] = dict(payload.get("meta") or {})
        if not self.trees or not self.features:
            raise ForecastUnavailable("The model artifact contains no trees or no features.")

    def _leaf_for(self, nodes: list[dict[str, Any]], row: list[float]) -> float:
        index = 0
        # Bounded loop: a malformed artifact must fail rather than spin.
        for _ in range(64):
            node = nodes[index]
            if "leaf" in node:
                return float(node["leaf"])
            value = row[self.features.index(node["feature"])]
            index = node["left"] if value <= node["threshold"] else node["right"]
        raise ForecastUnavailable("The model artifact contains a cyclic tree.")

    def predict(self, features: dict[str, float]) -> float:
        row = [float(features[name]) for name in self.features]
        total = self.base_value
        for nodes in self.trees:
            total += self.learning_rate * self._leaf_for(nodes, row)
        return total

    def metadata(self) -> dict[str, Any]:
        return dict(self.meta)


@lru_cache(maxsize=1)
def _load_model(path: str) -> TreeEnsemble:
    artifact = Path(path)
    if not artifact.is_file():
        raise ForecastUnavailable(
            f"No forecasting model at {artifact}. Train one with: "
            "python scripts/train_forecasting_model.py"
        )
    try:
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ForecastUnavailable(f"The forecasting model at {artifact} is unreadable: {exc}") from exc
    return TreeEnsemble(payload)


def load_model(path: Path | None = None) -> TreeEnsemble:
    return _load_model(str(path or MODEL_PATH))


def reset_cache() -> None:
    """Drop the cached artifact (demo reset / tests)."""
    _load_model.cache_clear()


def model_available() -> bool:
    try:
        load_model()
    except ForecastUnavailable:
        return False
    return True


def model_metadata() -> dict[str, Any]:
    """Metadata for the evidence panel. Never raises: absence is reported."""
    try:
        model = load_model()
    except ForecastUnavailable as exc:
        return {"available": False, "reason": str(exc), "provenance": PROVENANCE}
    meta = model.metadata()
    artifact = MODEL_PATH
    return {
        "available": True,
        "provenance": PROVENANCE,
        "artifact": artifact.name,
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "features": model.features,
        "learning_rate": model.learning_rate,
        "trees": len(model.trees),
        **meta,
    }


def predict_duration(features: dict[str, float], *, path: Path | None = None) -> DurationForecast:
    """Predict a disruption duration in days, with an honest uncertainty band."""
    model = load_model(path)
    missing = [name for name in model.features if name not in features]
    if missing:
        raise ValueError(
            f"Missing model feature(s) {missing}. A prediction from a partial feature "
            "vector would be silently wrong."
        )
    prediction = model.predict(features)
    duration = max(1.0, prediction)
    mae = float(model.metadata().get("metrics", {}).get("test_mae_days", 0.0) or 0.0)
    return DurationForecast(
        duration_days=duration,
        # An MAE-based band, stated as what it is: a typical-error band, not a
        # calibrated prediction interval. Claiming the latter would need
        # quantile regression, and saying so is cheaper than over-claiming.
        interval_low_days=max(1.0, duration - mae),
        interval_high_days=duration + mae,
        model_version=str(model.metadata().get("model_version", "unknown")),
        provenance=PROVENANCE,
        features_used={name: float(features[name]) for name in model.features},
        method={
            "model": "gradient-boosted regression trees, exported to portable JSON",
            "evaluator": "pure-Python tree walk (no numpy, no scikit-learn at runtime)",
            "band": (
                "point estimate +/- the model's held-out test MAE. This is a typical-error "
                "band, NOT a calibrated prediction interval."
            ),
            "advisory": (
                "Advisory only. This value never reaches a shared contract unless "
                "forecasting.enabled is on, and A2 clamps it against configured bounds."
            ),
            "llm_involved": False,
        },
    )


def forecast_enabled() -> bool:
    """Whether the prior may influence a scenario at all."""
    return bool(settings().get("forecasting", {}).get("enabled", False))


# ---------------------------------------------------------------------------
# Feature construction
# ---------------------------------------------------------------------------

#: Feature names the model expects, in the artifact's order.
MODEL_FEATURES: Final[tuple[str, ...]] = (
    "capacity_loss_pct",
    "corridor_exposed",
    "congestion_index",
    "node_risk_score",
    "chokepoint",
    "prior_incidents_90d",
    "carrier_diversion_pct",
    "vessel_wait_days",
    "season_month",
    "lane_transit_days",
    "alt_route_available",
    "is_tariff_shock",
)


def features_for_event(
    event: Any,
    network: Any,
    *,
    observed_incidents_90d: int = 0,
    carrier_diversion_pct: float = 35.0,
) -> dict[str, float]:
    """Build the model's input vector from a verified event and the network.

    Every value is either read from the seed data or derived from the verifying
    signal's structured claim -- never from free text. The two values that cannot
    be derived from this repository at all (a 90-day incident count and a carrier
    diversion share) are parameters with documented defaults, so a caller must
    supply them rather than have a number invented.
    """
    from backend.agents.verification import VerifiedEvent

    if not isinstance(event, VerifiedEvent):
        raise TypeError("features_for_event expects a VerifiedEvent")

    claim = event.claim or {}
    target = event.target
    node = network.nodes.get(target)
    lane = next((la for la in network.lanes.values() if target in la.path), None)

    capacity_factor = float(claim.get("capacity_factor", 1.0))
    capacity_loss_pct = max(0.0, min(100.0, (1.0 - capacity_factor) * 100.0))
    if event.event_type == "COLD_CHAIN_EXCURSION":
        # Not a corridor capacity event at all; the shock is cargo damage.
        capacity_loss_pct = 0.0

    red_sea = bool(getattr(lane, "red_sea_exposed", False)) if lane else False
    congestion = float(getattr(node, "raw", {}).get("congestion_index", 0.25) or 0.25)
    chokepoint = 1.0 if bool(getattr(node, "raw", {}).get("chokepoint", False)) else 0.0
    risk = float(getattr(node, "risk_score", 0.3) or 0.3)
    transit = float(getattr(lane, "transit_days", 21.0) or 21.0)

    from backend.config import scenario_clock

    month = float(scenario_clock().month)

    return {
        "capacity_loss_pct": capacity_loss_pct,
        "corridor_exposed": 1.0 if red_sea else 0.0,
        "congestion_index": max(0.0, min(1.0, congestion)),
        "node_risk_score": max(0.0, min(1.0, risk)),
        "chokepoint": chokepoint,
        "prior_incidents_90d": float(observed_incidents_90d),
        "carrier_diversion_pct": max(0.0, min(100.0, carrier_diversion_pct)),
        "vessel_wait_days": max(0.0, min(30.0, congestion * 12.0)),
        "season_month": month,
        "lane_transit_days": max(1.0, min(45.0, transit)),
        "alt_route_available": _alternate_route_available(network, lane),
        "is_tariff_shock": 1.0 if event.event_type == "TARIFF_CHANGE" else 0.0,
    }


def _alternate_route_available(network: Any, lane: Any) -> float:
    """Is there another lane from the same origin to the same destination?"""
    if lane is None:
        return 0.0
    for other in network.lanes.values():
        if other.id == lane.id:
            continue
        if other.origin == lane.origin and other.destination == lane.destination:
            return 1.0
    return 0.0
