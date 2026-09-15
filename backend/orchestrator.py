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
    m2_fixture,
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
            from backend.graph.network import Network

            reset_cache()
            Network.load.cache_clear()
        return {
            "reset": True,
            "reset_at": isoformat(scenario_clock()),
            "note": (
                "Ledger, approval authority, SAP-shaped mocks and cached network reloaded. "
                "No approval survives a reset."
            ),
        }

    # ----------------------------------------------------------------------- run
    def run(self) -> dict[str, Any]:
        """Run M1 -> M2 boundary -> M3 governance. STOPS at the approval gate."""
        with self._lock:
            self._state = RunState(
                run_id="RUN-HERO-001", started_at=isoformat(scenario_clock())
            )
            settings_doc = settings()

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
    ) -> dict[str, Any]:
        """Record a human decision. The ONLY way out of APPROVAL_REQUIRED."""
        with self._lock:
            plan = self._state.plan
            if plan is None:
                raise GovernanceError(
                    "NO_ACTIVE_PLAN",
                    "No plan is loaded. Run the scenario before attempting a decision.",
                )
            gov = registry.get_or_create(plan.plan_id)
            actor = resolve_actor(actor_id)

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
                    event_ids=[e.event_id for e in (self._state.m1.events if self._state.m1 else [])],
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
        stage_for = {"ibp": "IBP_EXECUTED", "tm": "TM_EXECUTED", "ariba": "ARIBA_EXECUTED"}
        for action in outcome.actions:
            system = action.get("system")
            ledger.append(
                stage_for.get(system, "EXECUTION_STARTED"),
                "A5_EXECUTION",
                action.get("operation", system or "unknown"),
                result=action.get("status", "UNKNOWN"),
                ids={"plan_id": plan.plan_id, "correlation_id": outcome.receipt.correlation_id or ""},
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
            "approval": self._state.approval.model_dump(mode="json") if self._state.approval else None,
            "governance": gov.as_dict() if gov else None,
            "execution": self._state.execution,
            "learning": self._state.learning,
            "errors": self._state.errors,
            "ledger": {
                "length": len(ledger),
                "timeline": ledger.timeline(),
                "coverage": ledger.stage_coverage(),
                "chain": ledger.verify_chain(),
                "stages_expected": STAGE_SEQUENCE,
            },
            "sap": {
                "boundary": "SAP-shaped deterministic mocks",
                "real_integration": False,
                "calls": execution_orchestrator.sap.call_log(),
            },
            "mock_boundaries": mock_boundaries(),
            "approvers": scenarios_config()["approvers"],
            "markets": scenarios_config()["markets"],
        }


def mock_boundaries() -> dict[str, Any]:
    """The honest inventory of what is real, deterministic, simulated and mocked."""
    return {
        "real": [
            "Graph traversal and network impact arithmetic (networkx + closed-form model)",
            "Cold-chain telemetry analysis over the seeded device trace",
            "Policy rule evaluation and the approval state machine",
            "SHA-256 hash-chained append-only audit ledger",
            "FastAPI backend and React frontend",
        ],
        "deterministic": [
            "12-node synthetic pharma network, 12 SKUs, 6 lanes, 8 consignments",
            "Scripted signal feeds, device telemetry and post-execution observations",
            "Probability weights, stockout probabilities and revenue-at-risk",
            "Scenario clock is frozen so a run is reproducible",
        ],
        "simulated": [
            "IoT telemetry source (stands in for Tive/Sensitech-class devices)",
            "Post-execution outcome feedback (stands in for TM/IBP/QA actuals)",
            "Trade and sanctions screening lists (configured, not a live feed)",
        ],
        "mocked": [
            "SAP IBP planning-scenario surface",
            "SAP TM freight re-booking surface",
            "SAP Ariba supplier-risk surface",
            "RecoveryPlan (deterministic M2 fixture - no optimizer is implemented)",
        ],
        "not_implemented": [
            "M2 digital twin (SimPy) and Pyomo/HiGHS optimization",
            "Real SAP tenant connectors (BTP destination service + real OData)",
            "Any trained forecasting model",
            "Authentication, authorization and multi-tenancy",
        ],
        "ai_usage": (
            "No language model participates in any number. Settings expose an "
            "llm_enabled flag, disabled by default, which could only add narrative text."
        ),
    }


orchestrator = DemoOrchestrator()
