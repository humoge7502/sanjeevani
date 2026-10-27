"""M2 BOUNDARY -- the RecoveryPlan provider.

M3 consumes `RecoveryPlan`; it never computes one. This module is the *only*
place in M3 that knows where plans come from, so swapping the fixture for the
real optimizer is a one-class change.

    M1 -> Impact -> [ this module ] -> RecoveryPlan -> M3

Two implementations satisfy the same Protocol:

  ``fixture``    -- hand-set YAML (the default; see docs/mock-boundaries.md).
  ``optimizer``  -- ``OptimizerRecoveryPlanProvider``, which solves a real
                    multi-objective binary program over the configured recovery
                    catalogue and evaluates it with a deterministic digital twin
                    (``backend/m2/optimizer.py``). Select it with
                    ``SANJEEVANI_PLAN_PROVIDER=optimizer``.

M3 does not need to know which one is active: both return the same frozen
contract, and ``plan.source`` records which produced it.
"""

from __future__ import annotations

import os
import threading
from typing import ClassVar, Literal, Protocol

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

    # Annotated as a Literal rather than left as `str`, so the value that lands
    # in the RecoveryPlan contract is checked against the contract's own union
    # instead of being coerced at runtime.
    SOURCE: ClassVar[Literal["optimizer", "fixture"]] = "fixture"

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


class OptimizerRecoveryPlanProvider:
    """M2's real optimizer behind the fixture's interface.

    Solves the multi-objective recovery program in ``backend/m2/optimizer.py``
    using the deterministic digital twin in ``backend/m2/digital_twin.py``. Every
    plan it returns carries ``source="optimizer"`` and can be re-derived
    bit-for-bit from the seeded network and the configured catalogue.

    Results are memoised per ``(event_id, scenario_id)`` because the API reads the
    recommended plan and the ranked list in the same request; solving the same
    instance twice would be wasted work, not extra safety.
    """

    SOURCE: ClassVar[Literal["optimizer", "fixture"]] = "optimizer"

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str | None], tuple[RecoveryPlan, list[RecoveryPlan], dict]] = {}
        self._lock = threading.RLock()

    def _solve(
        self, event_id: str, scenario_id: str | None
    ) -> tuple[RecoveryPlan, list[RecoveryPlan], dict]:
        from backend.m2.optimizer import optimize_for_event

        key = (event_id, scenario_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
        try:
            result = optimize_for_event(event_id=event_id, scenario_id=scenario_id)
        except Exception as exc:
            raise RecoveryPlanError(
                f"Optimizer could not produce a plan for event {event_id!r} "
                f"(scenario {scenario_id!r}): {type(exc).__name__}: {exc}"
            ) from exc
        solved = (result.recommended, result.ranked, result.trace)
        with self._lock:
            self._cache[key] = solved
        return solved

    def plan_for_event(self, event_id: str, scenario_id: str | None = None) -> RecoveryPlan:
        recommended, _, _ = self._solve(event_id, scenario_id)
        return recommended

    def ranked_plans(self, event_id: str, scenario_id: str | None = None) -> list[RecoveryPlan]:
        _, ranked, _ = self._solve(event_id, scenario_id)
        return ranked

    def plan_by_id(self, plan_id: str) -> RecoveryPlan:
        with self._lock:
            for (recommended, ranked, _trace) in self._cache.values():
                if recommended.plan_id == plan_id:
                    return recommended
                for candidate in ranked:
                    if candidate.plan_id == plan_id:
                        return candidate
        raise RecoveryPlanError(
            f"Unknown plan_id {plan_id!r}. The optimizer only knows plans it has solved "
            "in this process; call plan_for_event first."
        )

    def last_trace(self) -> dict | None:
        """The most recent optimization trace, for the API's evidence panel."""
        with self._lock:
            if not self._cache:
                return None
            return next(reversed(self._cache.values()))[2]

    def reset(self) -> None:
        with self._lock:
            self._cache.clear()

    def describe(self) -> dict[str, object]:
        from backend.m2.digital_twin import build_catalogue

        catalogue = build_catalogue()
        return {
            "provider": type(self).__name__,
            "source": self.SOURCE,
            "reality": "solved optimization over a deterministic digital twin",
            "optimizer_implemented": True,
            "owner": "M2",
            "consumed_by": "M3",
            "catalogue_version": catalogue.catalogue_version,
            "actions_considered": [a.id for a in catalogue.actions],
            "objective": (
                "minimise total_recovery_cost + w * expected_unrecovered_value, subject to "
                "budget, service-level, cold-chain and recovery-time ceilings"
            ),
            "disclosure": (
                "The recovery ACTION CATALOGUE (config/recovery_options.yaml) is configured; "
                "the OUTCOME of choosing it is computed by deterministic simulation over the "
                "seeded network and device traces. No sampling, no LLM, no SAP tenant. Every "
                "plan carries source='optimizer' and a full solver trace."
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


def provider_mode() -> str:
    """The configured provider mode, lower-cased."""
    return os.environ.get("SANJEEVANI_PLAN_PROVIDER", "fixture").lower()


#: One provider instance per mode. The orchestrator asks for the recommended plan
#: and the ranked list in the same run; handing it two fresh optimizer instances
#: would solve the same binary program twice for no benefit. Instance state is
#: only a memo cache, so a stale entry can never change an answer -- but tests
#: and the demo reset still clear it explicitly.
_PROVIDERS: dict[str, RecoveryPlanProvider] = {}
_PROVIDERS_LOCK = threading.RLock()


def get_provider() -> RecoveryPlanProvider:
    """Resolve the configured provider.

    ``fixture`` (default) is the offline, hand-set stand-in that keeps the demo
    byte-reproducible. ``optimizer`` turns on the real solve. ``unavailable`` is
    the red-team switch that proves the loop stops rather than inventing a plan.
    """
    mode = provider_mode()
    with _PROVIDERS_LOCK:
        existing = _PROVIDERS.get(mode)
        if existing is not None:
            return existing
        if mode == "unavailable":
            provider: RecoveryPlanProvider = UnavailableRecoveryPlanProvider()
        elif mode == "optimizer":
            provider = OptimizerRecoveryPlanProvider()
        elif mode == "fixture":
            provider = FixtureRecoveryPlanProvider()
        else:
            raise NotImplementedError(
                f"Unknown SANJEEVANI_PLAN_PROVIDER={mode!r}. Known modes: "
                "fixture, optimizer, unavailable."
            )
        _PROVIDERS[mode] = provider
        return provider


def reset_provider() -> None:
    """Drop every cached provider. Used by the demo reset and by tests."""
    with _PROVIDERS_LOCK:
        _PROVIDERS.clear()
