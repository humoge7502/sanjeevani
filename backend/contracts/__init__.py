"""SANJEEVANI shared integration contracts.

These six objects are the *frozen* integration surface between Member 1
(upstream intelligence), Member 2 (twin + optimization) and Member 3
(governance + execution + audit).

Ownership: SHARED. No member may rename a field silently. See
``docs/contracts.md`` for the change protocol and ``tests/contract`` for the
enforcement tests.
"""

from __future__ import annotations

from backend.contracts.shared import (
    CONTRACT_VERSION,
    Approval,
    ApprovalDecision,
    Event,
    EventSeverity,
    EventSourceClass,
    ExecutionReceipt,
    ExecutionStatus,
    Impact,
    Outcome,
    RecoveryPlan,
    SapSystem,
)

__all__ = [
    "CONTRACT_VERSION",
    "Approval",
    "ApprovalDecision",
    "Event",
    "EventSeverity",
    "EventSourceClass",
    "ExecutionReceipt",
    "ExecutionStatus",
    "Impact",
    "Outcome",
    "RecoveryPlan",
    "SapSystem",
]
