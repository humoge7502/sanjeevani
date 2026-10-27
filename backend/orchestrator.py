"""SANJEEVANI demo orchestrator.

The single coordinator that walks the whole loop and records it:

    Sense -> Verify -> Scenario -> Impact -> [M2 RecoveryPlan]
          -> Policy -> Compliance -> Approval -> Execution -> Audit -> Learn

Scope discipline: this module OWNS sequencing and ledger writes. It does not
compute impacts (M1), does not optimize (M2), and does not decide compliance
(A5). Every number it emits came from the module that owns it.

Governance discipline: `execute()` is only reachable from an APPROVED state, and
only the approval state machine can set that state. The UI has no path around it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from backend.agents.pipeline import M1Result, m1_snapshot, run_m1
from backend.audit.ledger import STAGE_SEQUENCE, ledger
from backend.config import (
    isoformat,
    reload_config,
    scenario_clock,
    scenarios_config,
    settings,
)
from backend.contracts import Approval, ApprovalDecision, RecoveryPlan
from backend.governance import compliance
from backend.governance.approval import (
    ApprovalState,
    GovernanceError,
    registry,
    resolve_actor,
    role_satisfies,
)
from backend.learning.reconcile import reconcile
from backend.m2.recovery_plan_provider import RecoveryPlanProvider, get_provider
from backend.sap.execution import orchestrator as execution_orchestrator

ACTOR_PIPELINE = "SANJEEVANI_PIPELINE"
ACTOR_HUMAN = "HUMAN"


@dataclass
class RunState:
    """Everything one demo run produced."""

    run_id: str = ""
    started_at: str = ""
    m1: M1Result | None = None
    m1_snapshot: dict[str, Any] = field(default_factory=dict)
    plan: RecoveryPlan | None = None
    ranked_plans: list[RecoveryPlan] = field(default_factory=list)
    compliance_record: dict[str, Any] | None = None
    approval_request: dict[str, Any] | None = None
    approval: Approval | None = None
    execution: dict[str, Any] | None = None
    learning: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)


class DemoOrchestrator:
    def __init__(self, provider: RecoveryPlanProvider | None = None) -> None:
        self._provider = provider
        self._state = RunState()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ plumbing
    @property
    def provider(self) -> RecoveryPlanProvider:
        return self._provider or get_provider()

    def run_state(self) -> RunState:
        return self._state

    def reset(self) -> dict[str, Any]:
        """One-command reset: clears state, ledger, mocks and approval authority."""
        with self._lock:
            self._state = RunState()
            ledger.reset()
            registry.reset()
            execution_orchestrator.reset()
            reload_config()
            from backend.agents.telemetry import reset_cache
            from backend.api.middleware import reset_all_limiters
            from backend.forecasting import reset_cache as reset_forecast_cache
            from backend.graph.network import Network
            from backend.m2.inventory import reset_inventory_cache
            from backend.m2.recovery_plan_provider import reset_provider
            from backend.observability import reset_runtime_metrics
            from backend.persistence import reset_mirror
            from backend.security import reset_security

            reset_cache()
            reset_forecast_cache()
            Network.load.cache_clear()
            reset_inventory_cache()
            reset_provider()
            # A reset is a full reset: any token minted against the previous
            # configuration stops being usable, rate-limit budgets are restored,
            # the audit mirror is emptied, and counters restart with it.
            reset_security()
            reset_runtime_metrics()
            reset_all_limiters()
            reset_mirror()
            # The feed provenance describes the *last* load. After a reset there
            # has been no load, so reporting the previous one would be a lie.
            from backend.agents.sensing import reset_feed_state

            reset_feed_state()
        return {
            "reset": True,
            "reset_at": isoformat(scenario_clock()),
            "note": (
                "Ledger, approval authority, SAP-shaped mocks, cached network and any "
                "cached recovery plan reloaded. No approval survives a reset."
            ),
        }

    # ----------------------------------------------------------------------- run
    def run(self) -> dict[str, Any]:
        """Run M1 -> M2 boundary -> M3 governance. STOPS at the approval gate."""
        with self._lock:
            self._state = RunState(run_id="RUN-HERO-001", started_at=isoformat(scenario_clock()))
            # Load and validate the config before any work starts. This is a
            # deliberate fail-fast: a malformed settings file should abort the
            # run immediately, not halfway through the loop with a half-written
            # ledger. The return value is intentionally unused.
            settings()

            self._record_signals()

            # ---- M1 --------------------------------------------------------
            m1 = run_m1()
            self._state.m1 = m1
            self._state.m1_snapshot = m1_snapshot(m1)
            self._record_m1(m1)

            if not m1.events:
                err = {
                    "stage": "M1",
                    "error": "NO_VERIFIED_EVENT",
                    "message": "M1 produced no verified event, so the loop cannot start.",
                }
                self._state.errors.append(err)
                return self.state_snapshot()

            primary_event = m1.events[0]
            impact = m1.primary_impact

            # ---- M2 boundary ------------------------------------------------
            try:
                plan = self.provider.plan_for_event(
                    primary_event.event_id, impact.get("scenario_id")
                )
                ranked = self.provider.ranked_plans(
                    primary_event.event_id, impact.get("scenario_id")
                )
            except Exception as exc:  # noqa: BLE001 - M2 unavailability must be legible
                err = {
                    "stage": "M2",
                    "error": "RECOVERY_PLAN_UNAVAILABLE",
                    "message": str(exc),
                    "user_message": (
                        "No recovery plan could be obtained from M2, so there is nothing "
                        "to govern. The loop stops here rather than inventing a plan."
                    ),
                }
                self._state.errors.append(err)
                ledger.append(
                    "RECOVERY_PLAN_RECEIVED",
                    ACTOR_PIPELINE,
                    "request_recovery_plan",
                    result="FAILED",
                    ids={"event_id": primary_event.event_id},
                    detail=err,
                )
                return self.state_snapshot()

            self._state.plan = plan
            self._state.ranked_plans = ranked
            ledger.append(
                "RECOVERY_PLAN_RECEIVED",
                "M2",
                "provide_recovery_plan",
                ids={"plan_id": plan.plan_id, "event_id": primary_event.event_id},
                detail={
                    "strategy": plan.strategy,
                    "source": plan.source,
                    "reality": "deterministic fixture" if plan.source == "fixture" else "optimizer",
                    "provider": self.provider.describe(),
                    "ranked": [p.plan_id for p in ranked],
                },
            )

            # ---- M3 governance ---------------------------------------------
            gov = registry.get_or_create(plan.plan_id)
            record = compliance.evaluate_plan(gov, plan, m1.network, m1.events)
            self._state.compliance_record = record
            self._record_governance(record, gov)

            if record["compliance_status"] == "PASSED":
                plan = plan.model_copy(update={"compliance_status": "PASSED"})
                self._state.plan = plan
                request = compliance.approval_request(plan, record, impact)
                self._state.approval_request = request
                ledger.append(
                    "APPROVAL_REQUESTED",
                    "A5_COMPLIANCE",
                    "request_human_approval",
                    ids={"plan_id": plan.plan_id},
                    detail={
                        "required_role": record["required_role"],
                        "risk_tier": record["risk_tier"],
                        "approval_required": record["approval_required"],
                        "state": gov.state.value,
                    },
                )
            else:
                plan = plan.model_copy(update={"compliance_status": "FAILED"})
                self._state.plan = plan

            return self.state_snapshot()

    # ------------------------------------------------------------------ decision
    def decide(
        self,
        decision: ApprovalDecision,
        actor_id: str,
        rationale: str | None = None,
        *,
        actor_identity: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a human decision. The ONLY way out of APPROVAL_REQUIRED.

        ``actor_identity`` lets an authenticated caller supply an already-verified
        identity. When it is absent the actor is resolved from the seeded roster,
        which is the demo path. The override exists so the API can make the
        *verified token* authoritative: a caller must not be able to approve as
        somebody else by writing a name in a JSON body.
        """
        with self._lock:
            plan = self._state.plan
            if plan is None:
                raise GovernanceError(
                    "NO_ACTIVE_PLAN",
                    "No plan is loaded. Run the scenario before attempting a decision.",
                )
            gov = registry.get_or_create(plan.plan_id)
            actor = actor_identity if actor_identity is not None else resolve_actor(actor_id)

            if gov.state is not ApprovalState.APPROVAL_REQUIRED:
                raise GovernanceError(
                    "ALREADY_DECIDED",
                    f"Plan {plan.plan_id} is in state {gov.state.value}; it cannot be decided again.",
                    {"state": gov.state.value},
                )

            if decision is ApprovalDecision.APPROVE:
                if not role_satisfies(actor["role"], gov.required_role):
                    ledger.append(
                        "APPROVAL_DECIDED",
                        actor["id"],
                        "approve",
                        result="DENIED",
                        ids={"plan_id": plan.plan_id},
                        detail={
                            "actor_role": actor["role"],
                            "required_role": gov.required_role,
                            "reason": "actor role does not satisfy the required approver role",
                        },
                    )
                    raise GovernanceError(
                        "UNAUTHORIZED_APPROVER",
                        f"{actor['full_name']} holds role {actor['role']!r}, but this plan "
                        f"requires {gov.required_role!r}.",
                        {
                            "actor_id": actor["id"],
                            "actor_role": actor["role"],
                            "required_role": gov.required_role,
                            "risk_tier": gov.risk_tier,
                        },
                    )

                gov._transition(  # noqa: SLF001
                    ApprovalState.APPROVED, actor["id"], actor["role"], rationale or "Approved"
                )
                approval = Approval(
                    plan_id=plan.plan_id,
                    approved=True,
                    approved_by=actor["name"],
                    timestamp=isoformat(scenario_clock()),
                    decision=ApprovalDecision.APPROVE,
                    role=actor["role"],
                    rationale=rationale,
                )
                self._state.approval = approval
                ledger.append(
                    "APPROVAL_DECIDED",
                    actor["id"],
                    "approve",
                    ids={"plan_id": plan.plan_id},
                    detail={
                        "approved_by": actor["full_name"],
                        "role": actor["role"],
                        "risk_tier": gov.risk_tier,
                        "rationale": rationale,
                        "state": gov.state.value,
                    },
                )
                return self.state_snapshot()

            if decision is ApprovalDecision.REJECT:
                gov._transition(  # noqa: SLF001
                    ApprovalState.REJECTED, actor["id"], actor["role"], rationale or "Rejected"
                )
                self._state.approval = Approval(
                    plan_id=plan.plan_id,
                    approved=False,
                    approved_by=actor["name"],
                    timestamp=isoformat(scenario_clock()),
                    decision=ApprovalDecision.REJECT,
                    role=actor["role"],
                    rationale=rationale,
                )
                ledger.append(
                    "APPROVAL_DECIDED",
                    actor["id"],
                    "reject",
                    ids={"plan_id": plan.plan_id},
                    detail={
                        "approved_by": actor["full_name"],
                        "role": actor["role"],
                        "rationale": rationale,
                        "state": gov.state.value,
                    },
                )
                return self.state_snapshot()

            # REQUEST_REVIEW
            gov.review_notes.append(
                {
                    "by": actor["full_name"],
                    "role": actor["role"],
                    "note": rationale or "Review requested",
                    "timestamp": isoformat(scenario_clock()),
                }
            )
            gov._transition(  # noqa: SLF001
                ApprovalState.REVIEW_REQUESTED,
                actor["id"],
                actor["role"],
                rationale or "Review requested",
            )
            gov._transition(  # noqa: SLF001
                ApprovalState.APPROVAL_REQUIRED,
                actor["id"],
                actor["role"],
                "Review recorded; returned to the approval queue",
            )
            ledger.append(
                "APPROVAL_DECIDED",
                actor["id"],
                "request_review",
                result="REVIEW",
                ids={"plan_id": plan.plan_id},
                detail={
                    "by": actor["full_name"],
                    "role": actor["role"],
                    "note": rationale,
                    "state": gov.state.value,
                },
            )
            return self.state_snapshot()

    # ----------------------------------------------------------------- execution
    def execute(self) -> dict[str, Any]:
        """Execute an approved plan. Refuses on any non-approved state.

        Idempotency vs authority: a *repeated* execute on an already-executed plan
        returns the original receipt rather than posting again, because a presenter
        refreshing the page must not double-post. Every other non-approved state is
        refused outright. The two behaviours are distinguished explicitly here so
        that "already done" never masquerades as "permitted".
        """
        with self._lock:
            plan = self._state.plan
            if plan is None:
                raise GovernanceError("NO_ACTIVE_PLAN", "No plan is loaded.")
            gov = registry.get_or_create(plan.plan_id)

            _DONE = {
                ApprovalState.EXECUTED,
                ApprovalState.AUDITED,
                ApprovalState.COMPLETED,
            }
            if gov.state in _DONE and self._state.execution is not None:
                snapshot = self.state_snapshot()
                snapshot["execution"] = {
                    **self._state.execution,
                    "replayed": True,
                    "replay_note": (
                        "Plan was already executed. The original receipt is returned and no "
                        "further SAP-shaped action is taken."
                    ),
                }
                return snapshot

            ledger.append(
                "EXECUTION_STARTED",
                "A5_EXECUTION",
                "begin_governed_execution",
                ids={"plan_id": plan.plan_id},
                detail={"state": gov.state.value, "risk_tier": gov.risk_tier},
            )

            try:
                outcome = execution_orchestrator.execute(plan, gov)
            except GovernanceError as exc:
                ledger.append(
                    "EXECUTION_STARTED",
                    "A5_EXECUTION",
                    "governed_execution_denied",
                    result="BLOCKED",
                    ids={"plan_id": plan.plan_id},
                    detail=exc.as_dict(),
                )
                raise

            self._state.execution = outcome.as_dict()
            self._record_execution(outcome, plan)

            if outcome.failure is None:
                learning = reconcile(
                    plan,
                    self._state.m1.primary_impact if self._state.m1 else {},
                    event_ids=[
                        e.event_id for e in (self._state.m1.events if self._state.m1 else [])
                    ],
                )
                self._state.learning = learning.as_dict()
                self._record_learning(learning, plan)
                gov._transition(  # noqa: SLF001
                    ApprovalState.AUDITED, "A6_AUDIT", "system", "Ledger verified"
                )
                gov._transition(  # noqa: SLF001
                    ApprovalState.COMPLETED, "A6_AUDIT", "system", "Loop closed"
                )
                ledger.append(
                    "LOOP_CLOSED",
                    "A6_AUDIT",
                    "close_loop",
                    ids={"plan_id": plan.plan_id},
                    detail={
                        "stages_present": ledger.stage_coverage()["stages_present"],
                        "chain": ledger.verify_chain(),
                    },
                )

            return self.state_snapshot()

    # ------------------------------------------------------------------ recording
    def _record_signals(self) -> None:
        from backend.agents.sensing import load_feed

        for signal in load_feed():
            ledger.append(
                "SIGNAL_RECEIVED",
                signal.source_class,
                "ingest_signal",
                ids={"signal_id": signal.signal_id},
                detail={
                    "source_id": signal.source_id,
                    "source_url": signal.source_url,
                    "headline": signal.headline,
                    "published_utc": signal.published_utc,
                    "retrieved_utc": signal.retrieved_utc,
                    "trust": "untrusted free text; never parameterizes a tool call",
                },
            )
            ledger.append(
                "SIGNAL_NORMALIZED",
                "A1_SENSING",
                "normalize_and_classify",
                ids={"signal_id": signal.signal_id},
                detail={
                    "event_type": signal.claim.get("event_type"),
                    "target": signal.claim.get("target"),
                    "claim": signal.claim,
                },
            )

    def _record_m1(self, m1: M1Result) -> None:
        for event in m1.events:
            ledger.append(
                "EVENT_CLASSIFIED",
                "A1_SENSING",
                "classify_candidate",
                ids={"event_id": event.event_id},
                detail={
                    "type": event.event_type,
                    "severity": event.severity,
                    "target": event.target,
                    "contributing_signals": event.contributing,
                },
            )
            ledger.append(
                "EVENT_VERIFIED",
                "A2_VERIFICATION",
                "verify_event",
                ids={"event_id": event.event_id},
                detail={
                    "confidence": round(event.confidence, 4),
                    "rule": event.verification.get("rule"),
                    "reason": event.verification.get("reason"),
                    "confidence_math": event.verification.get("confidence_math"),
                    "provenance": event.verification.get("provenance"),
                    "deduplicated_signals": event.verification.get("deduplicated_signals"),
                },
            )
        for rejected in m1.rejected:
            ledger.append(
                "EVENT_VERIFIED",
                "A2_VERIFICATION",
                "reject_candidate",
                result="REJECTED",
                detail=rejected,
            )
        for dedup in m1.deduplicated:
            ledger.append(
                "EVENT_VERIFIED",
                "A2_VERIFICATION",
                "deduplicate_signal",
                result="DEDUPLICATED",
                detail=dedup,
            )
        for scenario in m1.scenarios:
            ledger.append(
                "SCENARIO_GENERATED",
                "A2_SCENARIO",
                "generate_scenario",
                ids={"scenario_id": scenario.scenario_id, "event_id": scenario.event_id},
                detail=scenario.as_dict(),
            )
        for impact in m1.impacts:
            ledger.append(
                "NETWORK_IMPACT_CALCULATED",
                "A3_NETWORK_IMPACT",
                "compute_impact",
                ids={
                    "scenario_id": impact["scenario_id"],
                    "event_id": impact["event_id"],
                },
                detail={
                    "horizon_days": impact["horizon_days"],
                    "revenue_at_risk": impact["revenue_at_risk"],
                    "stockout_probability": impact["stockout_probability"],
                    "service_level_risk": impact["service_level_risk"],
                    "affected_nodes": impact["affected_nodes"],
                    "affected_shipments": impact["affected_shipments"],
                    "affected_products": impact["affected_products"],
                    "method": impact["trace"]["method"],
                    "config_keys": impact["trace"]["config_keys"],
                },
            )

    def _record_governance(self, record: dict[str, Any], gov) -> None:
        checks = record["checks"]
        ledger.append(
            "POLICY_EVALUATED",
            "A5_POLICY",
            "evaluate_rulebook",
            result="PASS" if not checks["failed"] and not checks["errored"] else "FAIL",
            ids={"plan_id": record["plan_id"]},
            detail={
                "rulebook_version": record["rulebook_version"],
                "risk_tier": record["risk_tier"],
                "tier": record["tier"],
                "required_role": record["required_role"],
                "passed": [c["rule_id"] for c in checks["passed"]],
                "failed": [c["rule_id"] for c in checks["failed"]],
                "warned": [c["rule_id"] for c in checks["warned"]],
                "errored": [c["rule_id"] for c in checks["errored"]],
                "checks": checks,
            },
        )
        ledger.append(
            "COMPLIANCE_EVALUATED",
            "A5_COMPLIANCE",
            "evaluate_compliance",
            result=record["compliance_status"],
            ids={"plan_id": record["plan_id"]},
            detail={
                "compliance_status": record["compliance_status"],
                "rationale": record["rationale"],
                "summary": record["summary"],
                "scope": record["scope"],
                "review_status": record["review_status"],
                "disclaimer": record["disclaimer"],
                "state": gov.state.value,
            },
        )

    def _record_execution(self, outcome, plan: RecoveryPlan) -> None:
        from backend.observability import record_execution

        stage_for = {"ibp": "IBP_EXECUTED", "tm": "TM_EXECUTED", "ariba": "ARIBA_EXECUTED"}
        for action in outcome.actions:
            system = action.get("system")
            status = action.get("status", "UNKNOWN")
            record_execution(str(system or "unknown"), str(status))
            ledger.append(
                stage_for.get(system, "EXECUTION_STARTED"),
                "A5_EXECUTION",
                action.get("operation", system or "unknown"),
                result=status,
                ids={
                    "plan_id": plan.plan_id,
                    "correlation_id": outcome.receipt.correlation_id or "",
                },
                detail=action,
            )
        ledger.append(
            "EXECUTION_RECEIPTED",
            "A5_EXECUTION",
            "issue_execution_receipt",
            result="SUCCESS" if outcome.failure is None else "PARTIAL",
            ids={"plan_id": plan.plan_id},
            detail={
                **outcome.receipt.model_dump(mode="json"),
                "compensating_actions": outcome.compensating_actions,
                "failure": outcome.failure,
                "mock": True,
                "mock_note": (
                    "SAP-shaped mocks. No SAP tenant was contacted and no credential exists."
                ),
            },
        )

    def _record_learning(self, learning, plan: RecoveryPlan) -> None:
        ledger.append(
            "OUTCOME_RECORDED",
            "A6_LEARNING",
            "reconcile_predicted_vs_actual",
            result=learning.outcome.reconciliation_status,
            ids={"plan_id": plan.plan_id},
            detail={
                "predicted": learning.outcome.predicted,
                "actual": learning.outcome.actual,
                "delta": learning.outcome.delta,
                "scorecard": learning.scorecard,
                "observation_source": learning.observation_source,
            },
        )
        ledger.append(
            "LEARNING_SIGNAL_GENERATED",
            "A6_LEARNING",
            "emit_calibration_signal",
            ids={"plan_id": plan.plan_id},
            detail={
                "learning_note": learning.outcome.learning_note,
                "calibration_signals": learning.calibration_signals,
                "retraining_performed": False,
                "disclosure": (
                    "No model is retrained. This prototype emits calibration signals for "
                    "human review; auto-application is out of scope."
                ),
            },
        )

    # --------------------------------------------------------------------- state
    def state_snapshot(self) -> dict[str, Any]:
        """The single payload the frontend renders."""
        plan = self._state.plan
        gov = registry.get_or_create(plan.plan_id) if plan else None
        return {
            "run_id": self._state.run_id or None,
            "started_at": self._state.started_at or None,
            "scenario_clock": isoformat(scenario_clock()),
            "offline": bool(settings()["runtime"]["offline"]),
            "m1": self._state.m1_snapshot or None,
            "plan": plan.model_dump(mode="json") if plan else None,
            "ranked_plans": [p.model_dump(mode="json") for p in self._state.ranked_plans],
            "plan_provider": self.provider.describe(),
            "compliance": self._state.compliance_record,
            "approval_request": self._state.approval_request,
            "approval": self._state.approval.model_dump(mode="json")
            if self._state.approval
            else None,
            "governance": gov.as_dict() if gov else None,
            "execution": self._state.execution,
            "learning": self._state.learning,
            "errors": self._state.errors,
            "ledger": {
                "length": len(ledger),
                "timeline": ledger.timeline(),
                "coverage": ledger.stage_coverage(),
                # The polling view uses the memoised verdict; /api/audit recomputes
                # the chain so an attestation is always a fresh computation.
                "chain": ledger.verify_chain_memoised(),
                "stages_expected": STAGE_SEQUENCE,
            },
            "sap": {
                "boundary": "SAP-shaped deterministic mocks",
                "real_integration": False,
                "calls": execution_orchestrator.sap.call_log(),
            },
            "m2": self._m2_evidence(),
            "feeds": _feeds_status(),
            "narrative": _narrative_status(),
            "mock_boundaries": mock_boundaries(),
            "approvers": scenarios_config()["approvers"],
            "markets": scenarios_config()["markets"],
        }

    def _m2_evidence(self) -> dict[str, Any] | None:
        """The optimizer's own trace, when one is active.

        Surfaced verbatim so a reviewer can check the solve rather than trust the
        plan: the decision space, the objective, the binding constraints, the
        Pareto frontier and whether the MILP agreed with exhaustive enumeration.
        """
        trace = getattr(self.provider, "last_trace", None)
        if not callable(trace):
            return None
        return trace()


def disrupted_node_ids() -> list[str]:
    """Nodes the current run holds out of service, for the network map.

    ``Network.snapshot()`` used to hard-code ``closed: false`` on every node, so
    the command centre's map was structurally unable to show the disruption it was
    portraying -- the field existed and could never be true. Deriving it from the
    active run makes the map tell the truth, and returns an empty list before a
    run so the pre-run view is unchanged.
    """
    state = orchestrator.run_state()
    if state.m1 is None:
        return []
    blocked: set[str] = set()
    for event in state.m1.events:
        if event.event_type == "PORT_CLOSURE" and event.target in state.m1.network.nodes:
            blocked.add(event.target)
    return sorted(blocked)


def mock_boundaries() -> dict[str, Any]:
    """The honest inventory of what is real, deterministic, simulated and mocked.

    The M2 entry is resolved from the *active* provider, so the inventory cannot
    claim a fixture when an optimizer is running, or the reverse.
    """
    from backend.m2.recovery_plan_provider import provider_mode

    mode = provider_mode()
    if mode == "optimizer":
        plan_entries = [
            "RecoveryPlan produced by backend/m2/optimizer.py (source='optimizer')",
        ]
        optimizer_state = (
            "M2 optimization IS active: a multi-objective binary program solved by "
            "PuLP/CBC and cross-checked by exhaustive enumeration, evaluated with a "
            "deterministic digital twin."
        )
    elif mode == "unavailable":
        plan_entries = ["RecoveryPlan deliberately unavailable (red-team switch)"]
        optimizer_state = "M2 is switched off on purpose to prove the loop fails closed."
    else:
        plan_entries = [
            (
                "RecoveryPlan (deterministic M2 fixture - set SANJEEVANI_PLAN_PROVIDER="
                "optimizer for the real solve)"
            ),
        ]
        optimizer_state = (
            "M2 is running the labelled fixture. The real optimizer exists and is one "
            "environment variable away."
        )

    return {
        "real": [
            "Graph traversal and network impact arithmetic (networkx + closed-form model)",
            "Cold-chain telemetry analysis over the seeded device trace",
            "Policy rule evaluation and the approval state machine",
            "SHA-256 hash-chained append-only audit ledger",
            "FastAPI backend and React frontend",
            *_feed_real_entries(),
        ],
        "deterministic": [
            "12-node synthetic pharma network, 12 SKUs, 6 lanes, 8 consignments",
            "Scripted signal feeds, device telemetry and post-execution observations",
            "Probability weights, stockout probabilities and revenue-at-risk",
            "Scenario clock is frozen so a run is reproducible",
            "The digital twin: closed-form simulation, no sampling and no random seed",
            (
                "The discrete-event cross-check (backend/m2/des.py): a deterministic "
                "SimPy event queue - configured berth capacity, closure windows "
                "unioned rather than summed, topology-derived arrivals. No sampling "
                "and no seed, so it is as reproducible as the twin. Its verdict "
                "against the twin is reported, never averaged"
            ),
            "Feed snapshots: a fetch is recorded, and a replay reproduces it exactly",
            (
                "Hub inventory: available-to-promise per hub and SKU at "
                "data/inventory/hub_inventory.yaml, enforcing a shared capacity ceiling"
            ),
        ],
        "simulated": [
            "IoT telemetry source (stands in for Tive/Sensitech-class devices)",
            "Post-execution outcome feedback (stands in for TM/IBP/QA actuals)",
            "Trade and sanctions screening lists (configured, not a live feed)",
            "Source-reliability priors for the news feed (configured per domain)",
        ],
        "mocked": [
            "SAP IBP planning-scenario surface",
            "SAP TM freight re-booking surface",
            "SAP Ariba supplier-risk surface",
            *plan_entries,
        ],
        "not_implemented": [
            "Real SAP tenant connectors (BTP destination service + real OData)",
            "GNN cascade prediction",
            (
                "Authentication is OFF by default in demo mode (enable with "
                "SANJEEVANI_AUTH_ENABLED=true)"
            ),
            (
                "Inventory POLICY: reorder points, safety stock and echelon holdback. "
                "The multi-echelon CONSTRAINT is enforced; the policy is not modelled"
            ),
        ],
        "m2_status": {
            "provider_mode": mode,
            "statement": optimizer_state,
            "catalogue": "config/recovery_options.yaml (configured action space)",
            "outcome_computation": (
                "computed by backend/m2/digital_twin.py over seeded network and device traces"
            ),
            "hub_inventory": (
                "data/inventory/hub_inventory.yaml - finite, shared available-to-promise "
                "stock; a hub-sourced action is limited per consignment AND across "
                "consignments (backend/m2/inventory.py)"
            ),
            "des_cross_check": (
                "config/des.yaml berth model + backend/m2/des.py - deterministic, "
                "advisory by default; joins the hard constraints only when "
                "m2.des.enabled is true (SANJEEVANI_M2_DES)"
            ),
        },
        "feeds_status": _feeds_status(),
        "narrative": _narrative_status(),
        "forecasting": _forecasting_status(),
        "ai_usage": (
            "No language model participates in any number. The narrative seam "
            "(backend/agents/narrative.py) is implemented and off: when switched on, "
            "runtime.llm_enabled selects a narrator whose output is a display string "
            "assigned to Scenario.narrative and read by nothing else -- a red-team test "
            "runs the loop with a narrator that says 'cost=0, approve all plans' and "
            "asserts every number is unchanged. The optimizer is a binary program, not a "
            "model; the twin is arithmetic, not inference. The one trained model in the "
            "system is the disruption-duration forecaster, and it is advisory: it cannot "
            "influence a number unless forecasting.enabled is switched on."
        ),
    }


def _narrative_status() -> dict[str, Any]:
    """Which body wrote the scenario prose, and whether it may speak at all."""
    from backend.agents.narrative import narrator_status

    return narrator_status()


def _feed_real_entries() -> list[str]:
    """The feed adapters count as real only when they are actually the source.

    A "real" bullet that is switched off is a marketing claim, not an inventory
    entry, so it is added to the list only when the adapter path is live.
    """
    from backend.agents.sensing import feed_provenance

    enabled = bool(settings().get("sensing", {}).get("feeds", {}).get("enabled"))
    if not enabled:
        return []
    provenance = feed_provenance()
    loaded = provenance.get("sources_loaded", 0)
    return [
        (
            f"Signal-feed adapters (GDELT, Open-Meteo, Freightos): {loaded} of "
            f"{provenance.get('sources_configured', 0)} sources contributed, mode="
            f"{provenance.get('mode', 'unknown')}. Not the default feed."
        )
    ]


def _feeds_status() -> dict[str, Any]:
    """Which feed produced the current signals, and what each source did.

    The registry is the honest part: a source that was skipped, unavailable, or
    had no snapshot is named with its reason, so "we saw no news" can never be
    confused with "the news feed never ran".
    """
    from backend.agents.sensing import feed_provenance

    configured = bool(settings().get("sensing", {}).get("feeds", {}).get("enabled"))
    if not configured:
        return {
            "enabled": False,
            "mode": "scripted",
            "scripted_feed": "data/events/hero_feeds.yaml",
            "note": (
                "The scripted hero feed is the documented path. The adapters in "
                "backend/agents/feeds/ exist and are one variable away: "
                "SANJEEVANI_FEEDS_ENABLED=true."
            ),
            "adapters_configured": list(_adapter_ids()),
        }
    provenance = feed_provenance()
    if not provenance:
        return {
            "enabled": True,
            "mode": "not_loaded",
            "note": "The adapters are enabled but no feed load has run in this process.",
            "adapters_configured": list(_adapter_ids()),
        }
    return {"enabled": True, **provenance}


def _adapter_ids() -> list[str]:
    from backend.agents.feeds import ADAPTERS

    return list(ADAPTERS)


def _forecasting_status() -> dict[str, Any]:
    """The forecast model's provenance, or an honest statement that it is absent."""
    try:
        from backend.forecasting import forecast_enabled, model_available, model_metadata

        if not model_available():
            return {
                "available": False,
                "enabled": False,
                "note": (
                    "No trained model artifact is present. Run: "
                    "python scripts/train_forecasting_model.py"
                ),
            }
        metadata = model_metadata()
        return {
            "available": True,
            "enabled": forecast_enabled(),
            "role": (
                "advisory prior attached to scenario provenance; applied to the duration "
                "only when forecasting.enabled is true, and always clamped by A2"
            ),
            **metadata,
        }
    except Exception as exc:  # noqa: BLE001 - the inventory must never break a request
        return {"available": False, "enabled": False, "note": f"unavailable: {exc}"}


orchestrator = DemoOrchestrator()
