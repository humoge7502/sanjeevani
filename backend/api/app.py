"""SANJEEVANI API (M3).

The API is the security boundary, not the React app. Every state-changing call
goes through the orchestrator, which goes through the approval state machine.
A client cannot set a state, cannot approve without a role, and cannot execute
without an approval.

Error contract: governance refusals return a structured 4xx/409 with a
user-legible message AND machine-readable detail. They never surface as 500s,
because "500 Internal Server Error" is not an acceptable answer to "why did this
not execute?" (prompt S32).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.audit.ledger import ledger
from backend.config import settings
from backend.contracts import CONTRACT_VERSION, ApprovalDecision
from backend.governance import policy_engine
from backend.governance.approval import (
    ApprovalState,
    GovernanceError,
    registry,
    resolve_actor,
)
from backend.orchestrator import mock_boundaries, orchestrator
from backend.sap.mocks import SapMockError

logger = logging.getLogger("sanjeevani.api")

app = FastAPI(
    title="SANJEEVANI API",
    version=CONTRACT_VERSION,
    description=(
        "Governed closed-loop pharma supply-chain recovery. M1 upstream "
        "intelligence + M3 governance/execution/audit. M2 is a fixture boundary."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings()["api"]["cors_origins"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Error handling: structured, legible, never a bare 500
# ---------------------------------------------------------------------------


@app.exception_handler(GovernanceError)
async def governance_error_handler(_: Request, exc: GovernanceError) -> JSONResponse:
    status = {
        "EXECUTION_BLOCKED": 409,
        "ALREADY_DECIDED": 409,
        "ILLEGAL_TRANSITION": 409,
        "UNAUTHORIZED_APPROVER": 403,
        "NO_ACTIVE_PLAN": 404,
    }.get(exc.code, 400)
    logger.info("governance refusal code=%s", exc.code)
    return JSONResponse(
        status_code=status,
        content={
            "error": exc.code,
            "message": exc.message,
            "detail": exc.detail,
            "governance": {
                "enforced_by": "backend approval state machine",
                "note": "Client-side state cannot override this.",
            },
        },
    )


@app.exception_handler(SapMockError)
async def sap_error_handler(_: Request, exc: SapMockError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={
            "error": "SAP_MOCK_ERROR",
            "message": exc.message,
            "detail": exc.as_dict(),
            "note": "SAP-shaped mock refusal, not a real SAP error.",
        },
    )


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class DecisionRequest(BaseModel):
    """A human decision.

    `extra="forbid"` is deliberate: a client that posts `approved: true` or
    `risk_tier: L1` alongside the actor must be rejected outright rather than
    having the extra field quietly ignored. Silently ignoring it would leave an
    attacker (and a future maintainer) believing the flag did something.
    """

    model_config = ConfigDict(extra="forbid")

    actor_id: str = Field(min_length=1, description="Approver id or name, resolved server-side")
    rationale: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "contract_version": CONTRACT_VERSION,
        "offline": bool(settings()["runtime"]["offline"]),
        "deterministic_seed": settings()["runtime"]["deterministic_seed"],
        "clock_frozen": bool(settings()["runtime"]["freeze_clock"]),
        "llm_enabled": bool(settings()["runtime"]["llm_enabled"]),
    }


@app.get("/api/state")
def state() -> dict[str, Any]:
    return orchestrator.state_snapshot()


@app.get("/api/network")
def network() -> dict[str, Any]:
    from backend.graph.network import Network

    return Network.load().snapshot()


@app.get("/api/contracts")
def contracts() -> dict[str, Any]:
    """The frozen contract surface, rendered for inspection."""
    from backend.contracts import (
        Approval,
        Event,
        ExecutionReceipt,
        Impact,
        Outcome,
        RecoveryPlan,
    )

    return {
        "contract_version": CONTRACT_VERSION,
        "change_protocol": (
            "Breaking changes require documentation, versioning, consumer updates and "
            "contract tests. See docs/contracts.md."
        ),
        "contracts": {
            name: model.model_json_schema()
            for name, model in {
                "Event": Event,
                "Impact": Impact,
                "RecoveryPlan": RecoveryPlan,
                "Approval": Approval,
                "ExecutionReceipt": ExecutionReceipt,
                "Outcome": Outcome,
            }.items()
        },
    }


@app.get("/api/policies")
def policies() -> dict[str, Any]:
    from backend.config import policies as policies_cfg

    return {
        "meta": policies_cfg()["meta"],
        "rules": policy_engine.rule_catalogue(),
        "provenance_labels": policy_engine.PROVENANCE_LABELS,
    }


@app.get("/api/audit")
def audit(
    verify: bool = Query(default=True, description="Recompute the hash chain"),
) -> dict[str, Any]:
    return {
        "timeline": ledger.timeline(),
        "coverage": ledger.stage_coverage(),
        "chain": ledger.verify_chain() if verify else None,
        "records": ledger.records(),
    }


@app.get("/api/impact")
def impact(horizon_days: int | None = None) -> dict[str, Any]:
    run_state = orchestrator.run_state()
    if run_state.m1 is None:
        raise GovernanceError(
            "NO_ACTIVE_RUN", "No scenario has been run yet. POST /api/demo/run first."
        )
    m1 = run_state.m1
    if horizon_days is None:
        return {"primary": m1.primary_impact, "all": m1.impacts}
    match = next((i for i in m1.impacts if i["horizon_days"] == horizon_days), None)
    if match is None:
        raise HTTPException(
            status_code=404,
            detail=f"No scenario for horizon {horizon_days}. "
            f"Configured horizons: {[i['horizon_days'] for i in m1.impacts]}",
        )
    return {"horizon_days": horizon_days, "impact": match}


@app.get("/api/mock-boundaries")
def boundaries() -> dict[str, Any]:
    return mock_boundaries()


@app.get("/api/approvers")
def approvers() -> dict[str, Any]:
    from backend.config import scenarios_config

    return {"approvers": scenarios_config()["approvers"]}


# ---------------------------------------------------------------------------
# Demo control
# ---------------------------------------------------------------------------


@app.post("/api/demo/reset")
def demo_reset() -> dict[str, Any]:
    return orchestrator.reset()


@app.post("/api/demo/run")
def demo_run() -> dict[str, Any]:
    """Run M1 -> M2 fixture -> M3 governance. Stops at the approval gate."""
    return orchestrator.run()


@app.get("/api/demo/scenario")
def demo_scenario() -> dict[str, Any]:
    """The seeded hero scenario definition, for the presenter panel."""
    from backend.config import m2_fixture, scenarios_config

    cfg = scenarios_config()
    return {
        "hero_scenario": cfg["hero_scenario"],
        "disruption_library": cfg["disruption_library"],
        "horizons": cfg["horizons"],
        "markets": cfg["markets"],
        "m2_fixture": m2_fixture()["meta"],
    }


# ---------------------------------------------------------------------------
# Decisions (state-changing)
# ---------------------------------------------------------------------------


@app.post("/api/plans/{plan_id}/approve")
def approve(plan_id: str, body: DecisionRequest) -> dict[str, Any]:
    _assert_active_plan(plan_id)
    return orchestrator.decide(ApprovalDecision.APPROVE, body.actor_id, body.rationale)


@app.post("/api/plans/{plan_id}/reject")
def reject(plan_id: str, body: DecisionRequest) -> dict[str, Any]:
    _assert_active_plan(plan_id)
    return orchestrator.decide(ApprovalDecision.REJECT, body.actor_id, body.rationale)


@app.post("/api/plans/{plan_id}/request-review")
def request_review(plan_id: str, body: DecisionRequest) -> dict[str, Any]:
    _assert_active_plan(plan_id)
    return orchestrator.decide(ApprovalDecision.REQUEST_REVIEW, body.actor_id, body.rationale)


@app.post("/api/plans/{plan_id}/execute")
def execute(plan_id: str) -> dict[str, Any]:
    """Execute an approved plan. Requires APPROVED state; there is no override."""
    _assert_active_plan(plan_id)
    return orchestrator.execute()


@app.get("/api/plans/{plan_id}/governance")
def plan_governance(plan_id: str) -> dict[str, Any]:
    gov = registry.get_or_create(plan_id)
    from backend.governance.approval import ALLOWED

    return {
        "governance": gov.as_dict(),
        "state_machine": {
            "states": [s.value for s in ApprovalState],
            "transitions": {s.value: sorted(t.value for t in targets) for s, targets in ALLOWED.items()},
            "invariant": "EXECUTING is reachable only from APPROVED.",
        },
    }


@app.get("/api/actors/{actor_id}")
def actor(actor_id: str) -> dict[str, Any]:
    """Resolve an approver and their role, so the UI can show who may approve."""
    resolved = resolve_actor(actor_id)
    return {"actor": resolved}


def _assert_active_plan(plan_id: str) -> None:
    run_state = orchestrator.run_state()
    active = run_state.plan.plan_id if run_state.plan else None
    if active is None:
        raise GovernanceError(
            "NO_ACTIVE_PLAN",
            "No plan is loaded. POST /api/demo/run before acting on a plan.",
        )
    if plan_id != active:
        raise GovernanceError(
            "UNKNOWN_PLAN",
            f"Plan {plan_id} is not the active plan ({active}).",
            {"active_plan_id": active},
        )
