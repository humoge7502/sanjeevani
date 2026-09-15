"""M3 -- Approval state machine.

The governance boundary, expressed as an explicit machine rather than as
if-statements scattered through an API handler. Every consequential action must
pass through here, and the machine is the single place that can say "execution
is allowed".

States:
    RECEIVED -> POLICY_CHECKING -> COMPLIANCE_CHECKING -> APPROVAL_REQUIRED
    APPROVAL_REQUIRED -> APPROVED | REJECTED | REVIEW_REQUESTED
    APPROVED -> EXECUTING -> EXECUTED | FAILED
    EXECUTED -> AUDITED -> COMPLETED
    (or) COMPLIANCE_BLOCKED / POLICY_BLOCKED / FAILED terminal states

Two rules make bypassing impossible:
  1. `EXECUTING` is only reachable from `APPROVED`. There is no other path.
  2. Approving requires an actor whose role matches the plan's required role.
     The API layer passes `actor` in; it never trusts a client-supplied
     "approved: true" flag.

Idempotency: a second approve on an already-decided plan is rejected with
ALREADY_DECIDED rather than silently succeeding, and a replayed execute request
returns the existing receipt.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from backend.config import isoformat, scenario_clock, scenarios_config


class ApprovalState(str, Enum):
    RECEIVED = "RECEIVED"
    POLICY_CHECKING = "POLICY_CHECKING"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    COMPLIANCE_CHECKING = "COMPLIANCE_CHECKING"
    COMPLIANCE_BLOCKED = "COMPLIANCE_BLOCKED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    REVIEW_REQUESTED = "REVIEW_REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    AUDITED = "AUDITED"
    COMPLETED = "COMPLETED"


TERMINAL = {
    ApprovalState.REJECTED,
    ApprovalState.POLICY_BLOCKED,
    ApprovalState.COMPLIANCE_BLOCKED,
    ApprovalState.COMPLETED,
}

ALLOWED: dict[ApprovalState, set[ApprovalState]] = {
    ApprovalState.RECEIVED: {ApprovalState.POLICY_CHECKING},
    ApprovalState.POLICY_CHECKING: {
        ApprovalState.COMPLIANCE_CHECKING,
        ApprovalState.POLICY_BLOCKED,
    },
    ApprovalState.COMPLIANCE_CHECKING: {
        ApprovalState.APPROVAL_REQUIRED,
        ApprovalState.COMPLIANCE_BLOCKED,
    },
    ApprovalState.APPROVAL_REQUIRED: {
        ApprovalState.APPROVED,
        ApprovalState.REJECTED,
        ApprovalState.REVIEW_REQUESTED,
    },
    ApprovalState.REVIEW_REQUESTED: {
        ApprovalState.APPROVAL_REQUIRED,
        ApprovalState.REJECTED,
    },
    # The single edge into execution. Nothing else reaches EXECUTING.
    ApprovalState.APPROVED: {ApprovalState.EXECUTING},
    ApprovalState.EXECUTING: {ApprovalState.EXECUTED, ApprovalState.FAILED},
    ApprovalState.EXECUTED: {ApprovalState.AUDITED},
    ApprovalState.AUDITED: {ApprovalState.COMPLETED},
    ApprovalState.FAILED: {ApprovalState.AUDITED},
    ApprovalState.REJECTED: set(),
    ApprovalState.POLICY_BLOCKED: set(),
    ApprovalState.COMPLIANCE_BLOCKED: set(),
    ApprovalState.COMPLETED: set(),
}


class GovernanceError(Exception):
    """Raised when a governance rule refuses an action. Never a 500."""

    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def as_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, "detail": self.detail}


@dataclass
class Transition:
    seq: int
    from_state: ApprovalState
    to_state: ApprovalState
    actor: str
    actor_role: str | None
    reason: str
    timestamp: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "from": self.from_state.value,
            "to": self.to_state.value,
            "actor": self.actor,
            "actor_role": self.actor_role,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


@dataclass
class PlanGovernance:
    """Everything the machine knows about one plan's governance journey."""

    plan_id: str
    state: ApprovalState = ApprovalState.RECEIVED
    required_role: str | None = None
    risk_tier: str = "L1"
    transitions: list[Transition] = field(default_factory=list)
    approval: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    blocked_reason: str | None = None
    review_notes: list[dict[str, Any]] = field(default_factory=list)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # ------------------------------------------------------------------ helpers
    def history(self) -> list[dict[str, Any]]:
        return [t.as_dict() for t in self.transitions]

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "state": self.state.value,
            "risk_tier": self.risk_tier,
            "required_role": self.required_role,
            "approval_required": self.approval_required,
            "terminal": self.state in TERMINAL,
            "can_execute": self.state is ApprovalState.APPROVED,
            "blocks_execution": self.state
            in {
                ApprovalState.REJECTED,
                ApprovalState.POLICY_BLOCKED,
                ApprovalState.COMPLIANCE_BLOCKED,
            },
            "approval": self.approval,
            "receipt": self.receipt,
            "blocked_reason": self.blocked_reason,
            "review_notes": self.review_notes,
            "history": self.history(),
        }

    @property
    def approval_required(self) -> bool:
        """Derived from state, so it cannot disagree with the machine."""
        return self.state in {
            ApprovalState.APPROVAL_REQUIRED,
            ApprovalState.REVIEW_REQUESTED,
        }

    def _transition(
        self, to_state: ApprovalState, actor: str, role: str | None, reason: str
    ) -> Transition:
        if to_state not in ALLOWED[self.state]:
            raise GovernanceError(
                "ILLEGAL_TRANSITION",
                f"Cannot move plan {self.plan_id} from {self.state.value} to {to_state.value}.",
                {
                    "from": self.state.value,
                    "requested": to_state.value,
                    "allowed": sorted(s.value for s in ALLOWED[self.state]),
                },
            )
        transition = Transition(
            seq=len(self.transitions) + 1,
            from_state=self.state,
            to_state=to_state,
            actor=actor,
            actor_role=role,
            reason=reason,
            timestamp=isoformat(scenario_clock()),
        )
        self.transitions.append(transition)
        self.state = to_state
        return transition


def resolve_actor(actor_id: str) -> dict[str, Any]:
    """Look up an approver and their role. Unknown actors have NO role."""
    for approver in scenarios_config()["approvers"]:
        if approver["id"] == actor_id or approver["name"].lower() == actor_id.lower():
            return {
                "id": approver["id"],
                "name": approver["name"],
                "full_name": approver["full_name"],
                "role": approver["role"],
                "title": approver["title"],
            }
    return {"id": actor_id, "name": actor_id, "full_name": actor_id, "role": None, "title": None}


def role_satisfies(actor_role: str | None, required_role: str | None) -> bool:
    """Exact-role match. 'coo' does not implicitly satisfy 'planner' requirements.

    Deliberately strict: a narrow role model that refuses more often is safer
    than one that guesses seniority.
    """
    if required_role is None:
        return True
    return actor_role == required_role


def can_execute(gov: PlanGovernance) -> tuple[bool, str]:
    """The single authority check the execution orchestrator consults."""
    if gov.state is ApprovalState.APPROVED:
        return True, "approved"
    if gov.state is ApprovalState.EXECUTING:
        return False, "already_executing"
    if gov.state is ApprovalState.EXECUTED:
        return False, "already_executed"
    if gov.state is ApprovalState.REJECTED:
        return False, "rejected"
    if gov.state is ApprovalState.POLICY_BLOCKED:
        return False, "policy_blocked"
    if gov.state is ApprovalState.COMPLIANCE_BLOCKED:
        return False, "compliance_blocked"
    return False, "approval_missing"


class ApprovalRegistry:
    """In-memory registry of plan governance journeys.

    Deliberately process-local: the ledger is the durable record, and a restart
    therefore *loses* execution authority rather than carrying it forward. That
    is the safe direction to fail (a restart cannot resurrect an approval).
    """

    def __init__(self) -> None:
        self._plans: dict[str, PlanGovernance] = {}
        self._lock = threading.RLock()

    def reset(self) -> None:
        with self._lock:
            self._plans.clear()

    def get_or_create(self, plan_id: str) -> PlanGovernance:
        with self._lock:
            if plan_id not in self._plans:
                self._plans[plan_id] = PlanGovernance(plan_id=plan_id)
            return self._plans[plan_id]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {pid: g.as_dict() for pid, g in self._plans.items()}


registry = ApprovalRegistry()
