"""M2 BOUNDARY -- the RecoveryPlan provider.

M3 consumes `RecoveryPlan`; it never computes one. This module is the *only*
place in M3 that knows plans come from a fixture, so replacing it with Member 2's
real optimizer is a one-class change.

    M1 -> Impact -> [ this module ] -> RecoveryPlan -> M3

Swap procedure (documented in docs/mock-boundaries.md):
  1. Implement `RecoveryPlanProvider` against the real solver.
  2. Point `get_provider()` at it (env var `SANJEEVANI_PLAN_PROVIDER=optimizer`).
  3. Run `pytest tests/contract` -- the contract is identical, so nothing in M3
     changes.

M3 does NOT: solve a MILP, replay a digital twin, or optimize a cost. If a number
looks wrong here, the bug is M2's, and this module will say so.
"""

from __future__ import annotations

import os
from typing import Protocol

from backend.config import m2_fixture
from backend.contracts import RecoveryPlan


class RecoveryPlanError(LookupError):
    """Raised when no plan exists for a request. M3 fails closed on this."""


class RecoveryPlanProvider(Protocol):
    """The interface M2 must satisfy."""

    def plan_for_event(self, event_id: str, scenario_id: str | None = None) -> RecoveryPlan:
        """Return the RECOMMENDED plan for an event."""
        ...

    def ranked_plans(self, event_id: str, scenario_id: str | None = None) -> list[RecoveryPlan]:
        """Return every candidate plan, best first."""
        ...

    def plan_by_id(self, plan_id: str) -> RecoveryPlan:
        ...

    def describe(self) -> dict[str, object]:
        ...


class FixtureRecoveryPlanProvider:
    """Deterministic, hand-set stand-in for M2. NOT an optimizer."""

    SOURCE = "fixture"

    def __init__(self) -> None:
        self._doc = m2_fixture()
        self._by_id: dict[str, RecoveryPlan] = {}
        for raw in self._doc["plans"]:
            self._by_id[raw["plan_id"]] = RecoveryPlan(
                plan_id=raw["plan_id"],
                strategy=raw["strategy"],
                cost=float(raw["cost"]),
                service_level=float(raw["service_level"]),
                resilience_score=float(raw["resilience_score"]),
                temperature_risk=float(raw["temperature_risk"]),
                recovery_time_hours=float(raw["recovery_time_hours"]),
                compliance_status="PENDING",
                impacted_skus=list(raw.get("impacted_skus", [])),
                impacted_lanes=list(raw.get("impacted_lanes", [])),
                rationale=raw.get("rationale", "").strip() or None,
                alternatives=[],
                source=self.SOURCE,
            )

    # ------------------------------------------------------------------ helpers
    def _binding(self, event_id: str, scenario_id: str | None) -> dict:
        bindings = self._doc.get("scenario_bindings", [])
        for b in bindings:
            if b["event_id"] != event_id:
                continue
            if scenario_id is None or b.get("scenario_id") in (None, scenario_id):
                return b
        # Fall back to the first binding: the demo runs one hero scenario and
        # failing closed here would be unhelpful, so we degrade loudly instead.
        if bindings:
            return bindings[0]
        raise RecoveryPlanError(f"No M2 fixture binding for event {event_id}")

    def _with_alternatives(self, plan: RecoveryPlan, ranked: list[RecoveryPlan]) -> RecoveryPlan:
        """Attach the ranked table so the UI can render the strategy comparison."""
        return plan.model_copy(
            update={
                "alternatives": [
                    {
                        "plan_id": p.plan_id,
                        "strategy": p.strategy,
                        "cost": p.cost,
                        "service_level": p.service_level,
                        "resilience_score": p.resilience_score,
                        "temperature_risk": p.temperature_risk,
                        "recovery_time_hours": p.recovery_time_hours,
                        "recommended": p.plan_id == plan.plan_id,
                    }
                    for p in ranked
                ]
            }
        )

    # ------------------------------------------------------------------ Protocol
    def plan_for_event(self, event_id: str, scenario_id: str | None = None) -> RecoveryPlan:
        binding = self._binding(event_id, scenario_id)
        ranked = [self._by_id[pid] for pid in binding["ranked_plan_ids"]]
        recommended = self._by_id[binding["recommended_plan_id"]]
        return self._with_alternatives(recommended, ranked)

    def ranked_plans(self, event_id: str, scenario_id: str | None = None) -> list[RecoveryPlan]:
        binding = self._binding(event_id, scenario_id)
        return [self._by_id[pid] for pid in binding["ranked_plan_ids"]]

    def plan_by_id(self, plan_id: str) -> RecoveryPlan:
        try:
            return self._by_id[plan_id]
        except KeyError as exc:
            raise RecoveryPlanError(f"Unknown plan_id {plan_id!r}") from exc

    def describe(self) -> dict[str, object]:
        return {
            "provider": type(self).__name__,
            "source": self.SOURCE,
            "reality": "deterministic fixture",
            "optimizer_implemented": False,
            "owner": "M2",
            "consumed_by": "M3",
            "fixture_version": self._doc.get("meta", {}).get("fixture_version"),
            "disclosure": (
                "RecoveryPlan values in this build are hand-set fixture data. No MILP "
                "is solved and no digital twin is replayed. Values are labelled "
                "source='fixture' on every contract object."
            ),
        }


class UnavailableRecoveryPlanProvider:
    """Fails closed when the provider is deliberately disabled (red-team case)."""

    def plan_for_event(self, event_id: str, scenario_id: str | None = None) -> RecoveryPlan:
        raise RecoveryPlanError("M2 provider unavailable: no RecoveryPlan can be produced")

    def ranked_plans(self, event_id: str, scenario_id: str | None = None) -> list[RecoveryPlan]:
        raise RecoveryPlanError("M2 provider unavailable")

    def plan_by_id(self, plan_id: str) -> RecoveryPlan:
        raise RecoveryPlanError("M2 provider unavailable")

    def describe(self) -> dict[str, object]:
        return {"provider": "UnavailableRecoveryPlanProvider", "available": False}


def get_provider() -> RecoveryPlanProvider:
    """Resolve the configured provider.

    `SANJEEVANI_PLAN_PROVIDER=optimizer` is the intended production switch and is
    explicitly NOT implemented in the MVP.
    """
    mode = os.environ.get("SANJEEVANI_PLAN_PROVIDER", "fixture").lower()
    if mode == "unavailable":
        return UnavailableRecoveryPlanProvider()
    if mode == "optimizer":
        raise NotImplementedError(
            "The real M2 optimizer is not part of the MVP (see docs/mock-boundaries.md). "
            "Implement RecoveryPlanProvider in backend/m2 and register it here."
        )
    return FixtureRecoveryPlanProvider()
