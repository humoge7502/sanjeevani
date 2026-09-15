"""M3 governance: policy engine, compliance gate, approval state machine."""

from backend.governance import compliance, policy_engine
from backend.governance.approval import (
    ALLOWED,
    ApprovalRegistry,
    ApprovalState,
    GovernanceError,
    PlanGovernance,
    registry,
    resolve_actor,
    role_satisfies,
)

__all__ = [
    "ALLOWED",
    "ApprovalRegistry",
    "ApprovalState",
    "GovernanceError",
    "PlanGovernance",
    "compliance",
    "policy_engine",
    "registry",
    "resolve_actor",
    "role_satisfies",
]
