"""SANJEEVANI API (M3).

The API is the security boundary, not the React app. Every state-changing call
goes through the orchestrator, which goes through the approval state machine.
A client cannot set a state, cannot approve without a role, and cannot execute
without an approval.

Error contract: governance refusals return a structured 4xx/409 with a
user-legible message AND machine-readable detail. They never surface as 500s,
because "500 Internal Server Error" is not an acceptable answer to "why did this
not execute?".

AUTHENTICATION. Off by default, so the offline demo and the container smoke test
are unchanged. With ``SANJEEVANI_AUTH_ENABLED=true`` every route below requires a
signed token, the approver is taken **from the token** rather than from the
request body, and the token's role -- not the seeded roster's -- is what the
approval machine checks. Guarding is done through ``Depends(...)``, so the
enforcement point is visible in the route signature rather than buried in the
handler body.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from backend.api.middleware import (
    BodySizeLimitMiddleware,
    ObservationMiddleware,
    RateLimitMiddleware,
    limiter_state,
)
from backend.audit.ledger import ledger
from backend.config import FRONTEND_DIST, cors_origins, settings
from backend.contracts import CONTRACT_VERSION, ApprovalDecision
from backend.governance import policy_engine
from backend.governance.approval import (
    ApprovalState,
    GovernanceError,
    registry,
    resolve_actor,
)
from backend.observability import record_auth_failure, record_governance, render
from backend.orchestrator import mock_boundaries, orchestrator
from backend.sap.mocks import SapMockError
from backend.security import (
    AuthError,
    IdentityError,
    Principal,
    SecurityHeadersMiddleware,
    current_principal,
    require_admin,
    require_authenticated,
    token_service,
)
from backend.security.headers import nonce_for

logger = logging.getLogger("sanjeevani.api")

app = FastAPI(
    title="SANJEEVANI API",
    version=CONTRACT_VERSION,
    description=(
        "Governed closed-loop pharma supply-chain recovery. M1 upstream "
        "intelligence + M3 governance/execution/audit. M2 is a fixture boundary "
        "or the real optimizer, selected at runtime."
    ),
)

_security = settings()["security"]
_rate_limit = _security["rate_limit"]

# Middleware is applied INSIDE-OUT: the last one added is the outermost. The
# order below is chosen deliberately (see backend/api/middleware.py):
#   SecurityHeaders  <- outermost, so even a 429/413 carries them
#   Observation      <- times and logs everything, including refusals
#   RateLimit        <- cheap, and must sit outside the route handler
#   BodySizeLimit    <- runs before anything parses a body
#   CORS             <- innermost, wrapping the routes
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    # No cookie or session auth exists, and `allow_credentials=True` combined with
    # a wildcard origin is the classic CORS misconfiguration. This product has no
    # credentials to send, so the safer value is also the correct one.
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=int(_security["max_request_bytes"]))
app.add_middleware(
    RateLimitMiddleware,
    enabled=bool(_rate_limit["enabled"]),
    read_rate_per_second=float(_rate_limit["read_per_minute"]) / 60.0,
    read_burst=int(_rate_limit["read_burst"]),
    write_rate_per_second=float(_rate_limit["state_changing_per_minute"]) / 60.0,
    write_burst=int(_rate_limit["state_changing_burst"]),
)
app.add_middleware(ObservationMiddleware)
app.add_middleware(
    SecurityHeadersMiddleware,
    enabled=bool(_security["headers_enabled"]),
    extra_connect_src=tuple(_security.get("csp_extra_connect_src") or ()),
)


def _inject_nonce(html: str, nonce: str) -> str:
    """Add the per-request CSP nonce to the SPA's script tags.

    Vite's build emits one inline module-preload shim. Without a nonce the policy
    would need ``'unsafe-inline'``, which would make the CSP decorative: an
    injected ``<script>`` would run. Rewriting the tag is the whole reason the
    middleware bothers to mint a nonce.
    """
    return html.replace("<script", f'<script nonce="{nonce}"')


def mount_frontend(application: FastAPI, dist: Path = FRONTEND_DIST) -> bool:
    """Serve the built command center from the API, if it has been built.

    This is what makes the project one deployable unit. Vite's dev server is
    excellent for development and wrong for production: it is a development
    server with no process manager, and the browser would need a second origin.

    Mounted only when the build exists, so the development workflow (API here,
    Vite there, proxy between) is untouched. Returns whether it mounted.

    Routing note: the static mount is registered at "/" and therefore must come
    AFTER every /api route. FastAPI matches in registration order, so mounting
    first would swallow the API. Hence this is called once, at the bottom.
    """
    index = dist / "index.html"
    if not index.is_file():
        logger.info(
            "frontend build not found at %s; API-only mode (run the Vite dev server)",
            dist,
        )
        return False

    assets = dist / "assets"
    if assets.is_dir():
        application.mount("/assets", StaticFiles(directory=assets), name="assets")

    @application.get("/", include_in_schema=False)
    def _index(request: Request) -> Response:
        html = _inject_nonce(index.read_text(encoding="utf-8"), nonce_for(request.scope))
        return Response(content=html, media_type="text/html")

    # Anything that is not an API path and not a real file is a client-side
    # route. The command center is a single scrolling page today, but a 404 on a
    # deep link is the kind of thing that only shows up on stage.
    @application.get("/{path:path}", include_in_schema=False)
    def _spa(path: str, request: Request) -> Response:
        candidate = dist / path
        if path and candidate.is_file() and dist in candidate.resolve().parents:
            return FileResponse(candidate)
        html = _inject_nonce(index.read_text(encoding="utf-8"), nonce_for(request.scope))
        return Response(content=html, media_type="text/html")

    logger.info("serving the command center from %s", dist)
    return True


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
        "UNKNOWN_PLAN": 404,
        "NO_ACTIVE_RUN": 404,
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


#: Codes that mean "you are not who you say you are" -> 401.
_UNAUTHENTICATED_CODES = frozenset(
    {
        "AUTHENTICATION_REQUIRED",
        "AUTH_DISABLED",
        "INVALID_CREDENTIALS",
        "TOKEN_INVALID",
        "TOKEN_EXPIRED",
        "TOKEN_INVALID_AUDIENCE",
        "TOKEN_INVALID_ISSUER",
        "JWT_SECRET_REQUIRED",
        "JWT_SECRET_TOO_WEAK",
    }
)

#: Codes that mean "you are who you say you are, but not allowed" -> 403.
_FORBIDDEN_CODES = frozenset(
    {"INSUFFICIENT_ROLE", "ACCOUNT_DISABLED", "ACCOUNT_UNKNOWN", "ACTOR_MISMATCH"}
)


def _auth_status(code: str) -> int:
    if code in _UNAUTHENTICATED_CODES:
        return 401
    if code in _FORBIDDEN_CODES:
        return 403
    return 400


@app.exception_handler(AuthError)
async def auth_error_handler(_: Request, exc: AuthError) -> JSONResponse:
    status = _auth_status(exc.code)
    record_auth_failure(exc.code)
    return JSONResponse(
        status_code=status,
        content={
            "error": exc.code,
            "message": exc.message,
            "detail": {},
            "security": {
                "enforced_by": "backend token verification and role check",
                "note": "The approver is taken from the verified token, not the request body.",
            },
        },
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
    )


@app.exception_handler(IdentityError)
async def identity_error_handler(_: Request, exc: IdentityError) -> JSONResponse:
    """A token failure raised below the auth layer must still be a 401.

    ``TokenService.verify`` raises ``IdentityError``, and it is called from the
    dependency rather than from a route body. Without this mapping a forged token
    surfaced as a generic 400, which both misleads a client and hides the failure
    from an auth-failure alert.
    """
    status = _auth_status(exc.code)
    record_auth_failure(exc.code)
    return JSONResponse(
        status_code=status,
        content={"error": exc.code, "message": exc.message, "detail": {}},
        headers={"WWW-Authenticate": "Bearer"} if status == 401 else None,
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

    actor_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._@-]+$",
        description="Approver id or name. Ignored (and must match) when authentication is on.",
    )
    rationale: str | None = Field(default=None, max_length=1000)


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=512)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


@app.post("/api/auth/token")
def issue_token(body: TokenRequest) -> dict[str, Any]:
    """Exchange credentials for a signed access token."""
    from backend.security.identity import store

    service = token_service()
    if not service.enabled:
        raise AuthError(
            "AUTH_DISABLED",
            "Authentication is disabled in this deployment, so no token is issued. "
            "Requests are unauthenticated and every audit record says so.",
        )
    account = store.authenticate(body.username, body.password)
    issued = service.issue(account)
    logger.info("token issued for %s", account.id, extra={"fields": {"principal": account.id}})
    return issued


@app.get("/api/auth/me")
def whoami(principal: Annotated[Principal, Depends(require_authenticated)]) -> dict[str, Any]:
    return {
        "principal": principal.as_dict(),
        "auth_enabled": token_service().enabled,
        "ephemeral_secret": token_service().settings.ephemeral_secret,
    }


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, Any]:
    """Liveness + readiness in one payload.

    Deliberately returns 200 whenever the process can serve a request. It reports
    *state* (`offline`, `clock_frozen`, `auth_enabled`) rather than judging it,
    because a frozen clock and a disabled-auth demo are both correct here and must
    not fail a container health probe.
    """
    service = token_service()
    return {
        "status": "ok",
        "contract_version": CONTRACT_VERSION,
        "offline": bool(settings()["runtime"]["offline"]),
        "deterministic_seed": settings()["runtime"]["deterministic_seed"],
        "clock_frozen": bool(settings()["runtime"]["freeze_clock"]),
        "llm_enabled": bool(settings()["runtime"]["llm_enabled"]),
        # Readiness detail for an orchestrator: is the ledger writable, and is
        # there a UI to serve? Both are silent failure modes otherwise.
        "frontend_built": (FRONTEND_DIST / "index.html").is_file(),
        "ledger": ledger.health(),
        "security": {
            "auth_enabled": service.enabled,
            "ephemeral_jwt_secret": service.settings.ephemeral_secret,
            "security_headers": bool(settings()["security"]["headers_enabled"]),
            "rate_limiting": bool(settings()["security"]["rate_limit"]["enabled"]),
            "max_request_bytes": int(settings()["security"]["max_request_bytes"]),
            "rate_limiter": limiter_state(),
        },
        "m2_provider": orchestrator.provider.describe(),
    }


@app.get("/api/state")
def state(principal: Annotated[Principal, Depends(require_authenticated)]) -> dict[str, Any]:
    payload = orchestrator.state_snapshot()
    payload["caller"] = principal.as_dict()
    return payload


@app.get("/api/network")
def network(principal: Annotated[Principal, Depends(require_authenticated)]) -> dict[str, Any]:
    del principal
    from backend.graph.network import Network
    from backend.orchestrator import disrupted_node_ids

    return Network.load().snapshot(closed_nodes=disrupted_node_ids())


@app.get("/api/contracts")
def contracts(principal: Annotated[Principal, Depends(require_authenticated)]) -> dict[str, Any]:
    """The frozen contract surface, rendered for inspection."""
    del principal
    from backend.contracts import (
        Approval,
        Event,
        ExecutionReceipt,
        Impact,
        Outcome,
        RecoveryPlan,
    )

    # Annotated explicitly: without it the dict literal is inferred as a union of
    # the concrete model metaclasses, and the type checker cannot see
    # `model_json_schema` on the union even though every member has it.
    contract_models: dict[str, type[BaseModel]] = {
        "Event": Event,
        "Impact": Impact,
        "RecoveryPlan": RecoveryPlan,
        "Approval": Approval,
        "ExecutionReceipt": ExecutionReceipt,
        "Outcome": Outcome,
    }

    return {
        "contract_version": CONTRACT_VERSION,
        "change_protocol": (
            "Breaking changes require documentation, versioning, consumer updates and "
            "contract tests. See docs/contracts.md."
        ),
        "contracts": {
            name: model.model_json_schema() for name, model in contract_models.items()
        },
    }


@app.get("/api/policies")
def policies(principal: Annotated[Principal, Depends(require_authenticated)]) -> dict[str, Any]:
    del principal
    from backend.config import policies as policies_cfg

    return {
        "meta": policies_cfg()["meta"],
        "rules": policy_engine.rule_catalogue(),
        "provenance_labels": policy_engine.PROVENANCE_LABELS,
    }


@app.get("/api/audit")
def audit(
    principal: Annotated[Principal, Depends(require_authenticated)],
    verify: bool = Query(default=True, description="Recompute the hash chain"),
    limit: int | None = Query(default=None, ge=1, description="Page size (capped)"),
    offset: int = Query(default=0, ge=0),
    stage: str | None = Query(default=None, description="Filter to one ledger stage"),
) -> dict[str, Any]:
    """A **bounded page** of the audit trail, newest first.

    This endpoint used to return the entire ledger, unbounded, on every call. The
    ledger grows for the life of the deployment, so that was a response that grew
    without limit -- an availability problem caused by an audit read. The page is
    capped by ``audit.page_size_max`` and the true total is always reported, so
    nothing is hidden by the bound.
    """
    del principal
    page = ledger.records_page(limit=limit, offset=offset, stage=stage)
    return {
        "timeline": ledger.timeline(),
        "coverage": ledger.stage_coverage(),
        "chain": ledger.verify_chain() if verify else None,
        "page": page,
    }


@app.get("/api/impact")
def impact(
    principal: Annotated[Principal, Depends(require_authenticated)],
    horizon_days: int | None = None,
) -> dict[str, Any]:
    del principal
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
def boundaries(principal: Annotated[Principal, Depends(require_authenticated)]) -> dict[str, Any]:
    del principal
    return mock_boundaries()


@app.get("/api/forecasting")
def forecasting(principal: Annotated[Principal, Depends(require_authenticated)]) -> dict[str, Any]:
    """The trained duration model's provenance, and whether it may influence a number.

    Exposed so the evidence for a forecast is inspectable rather than asserted:
    the features, the held-out metrics, the dataset hash and the disclosure that
    the training rows are synthetic while the pipeline is real.
    """
    del principal
    from backend.forecasting import model_metadata

    return {"model": model_metadata(), "config": settings().get("forecasting", {})}


@app.get("/api/approvers")
def approvers(principal: Annotated[Principal, Depends(require_authenticated)]) -> dict[str, Any]:
    """The seeded roster the demo dropdown renders.

    With authentication on, this is *presentation only*: the actor that matters is
    the one in the token, and the role checked is the token's role.
    """
    del principal
    from backend.config import scenarios_config

    return {
        "approvers": scenarios_config()["approvers"],
        "authoritative_source": (
            "verified token (the roster below is a demo affordance only)"
            if token_service().enabled
            else "seeded roster (authentication is disabled)"
        ),
    }


@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    """Prometheus exposition. Unauthenticated on purpose: it exposes counts, not
    records, and a scrape should not need a credential. It is also exempt from the
    rate limiter so a scraper cannot be throttled into blindness."""
    from backend.observability import refresh_ledger_gauges

    refresh_ledger_gauges(ledger)
    body, content_type = render()
    return Response(content=body, media_type=content_type)


@app.get("/api/audit/index")
def audit_index(
    principal: Annotated[Principal, Depends(require_authenticated)],
    stage: str | None = Query(default=None),
    result: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Query the SQLite mirror, when one is configured.

    This is the capability the JSONL file does not have: filter and page without
    reading the whole ledger. It reads the MIRROR, and the response says so -- the
    JSONL ledger remains the source of truth, and a disagreement between them is
    reported by ``/api/health`` rather than reconciled here.
    """
    del principal
    from backend.persistence import mirror

    if not mirror.enabled:
        raise HTTPException(
            status_code=404,
            detail=(
                "No audit mirror is configured. Enable it with "
                "SANJEEVANI_AUDIT_MIRROR=true, or use GET /api/audit for the "
                "authoritative JSONL ledger."
            ),
        )
    return mirror.query(stage=stage, result=result, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# Demo control
# ---------------------------------------------------------------------------


@app.post("/api/demo/reset")
def demo_reset(principal: Annotated[Principal, Depends(require_admin)]) -> dict[str, Any]:
    """Clear state. Destructive, so it requires ``admin`` once auth is on.

    It truncates the audit ledger file. Leaving an unauthenticated, destructive
    endpoint reachable would have been the easiest way to lose the audit trail
    this product exists to protect.
    """
    del principal
    result = orchestrator.reset()
    result["auth_required"] = token_service().enabled
    return result


@app.post("/api/demo/run")
def demo_run(principal: Annotated[Principal, Depends(require_authenticated)]) -> dict[str, Any]:
    """Run M1 -> M2 -> M3 governance. Stops at the approval gate."""
    del principal
    return orchestrator.run()


@app.get("/api/demo/scenario")
def demo_scenario(principal: Annotated[Principal, Depends(require_authenticated)]) -> dict[str, Any]:
    """The seeded hero scenario definition, for the presenter panel."""
    del principal
    from backend.config import m2_fixture, recovery_options, scenarios_config

    cfg = scenarios_config()
    return {
        "hero_scenario": cfg["hero_scenario"],
        "disruption_library": cfg["disruption_library"],
        "horizons": cfg["horizons"],
        "markets": cfg["markets"],
        "m2_fixture": m2_fixture()["meta"],
        "m2_catalogue": recovery_options()["meta"],
    }


# ---------------------------------------------------------------------------
# Decisions (state-changing)
# ---------------------------------------------------------------------------


def _acting_identity(principal: Principal, body_actor_id: str) -> dict[str, Any]:
    """Decide *who* is acting, from the credential when there is one.

    With auth on, the token is authoritative and the body's ``actor_id`` must
    agree with it -- otherwise a token for a low-privilege account could be used
    to post an approval *as* a high-privilege one, which is the same bypass in a
    new coat. The role comes from the token, not from the seeded roster, so a
    roster edit cannot grant authority.
    """
    if not principal.authenticated:
        return resolve_actor(body_actor_id)
    if body_actor_id and body_actor_id != principal.id:
        raise AuthError(
            "ACTOR_MISMATCH",
            f"The token identifies {principal.id!r}, but the request names {body_actor_id!r}. "
            "An authenticated caller may only act as themselves.",
        )
    return {
        "id": principal.id,
        "name": principal.name,
        "full_name": principal.full_name,
        "role": principal.role,
        "title": None,
    }


def _decide(
    plan_id: str,
    body: DecisionRequest,
    principal: Principal,
    decision: ApprovalDecision,
) -> dict[str, Any]:
    _assert_active_plan(plan_id)
    identity = _acting_identity(principal, body.actor_id)
    try:
        return orchestrator.decide(
            decision, identity["id"], body.rationale, actor_identity=identity
        )
    except GovernanceError as exc:
        record_governance(decision.value.lower(), exc.code.lower())
        raise


@app.post("/api/plans/{plan_id}/approve")
def approve(
    plan_id: str,
    body: DecisionRequest,
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, Any]:
    result = _decide(plan_id, body, principal, ApprovalDecision.APPROVE)
    record_governance("approve", "granted")
    return result


@app.post("/api/plans/{plan_id}/reject")
def reject(
    plan_id: str,
    body: DecisionRequest,
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, Any]:
    result = _decide(plan_id, body, principal, ApprovalDecision.REJECT)
    record_governance("reject", "granted")
    return result


@app.post("/api/plans/{plan_id}/request-review")
def request_review(
    plan_id: str,
    body: DecisionRequest,
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, Any]:
    result = _decide(plan_id, body, principal, ApprovalDecision.REQUEST_REVIEW)
    record_governance("request_review", "granted")
    return result


@app.post("/api/plans/{plan_id}/execute")
def execute(
    plan_id: str,
    principal: Annotated[Principal, Depends(current_principal)],
) -> dict[str, Any]:
    """Execute an approved plan. Requires APPROVED state; there is no override."""
    del principal
    _assert_active_plan(plan_id)
    return orchestrator.execute()


@app.get("/api/plans/{plan_id}/governance")
def plan_governance(
    plan_id: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
) -> dict[str, Any]:
    del principal
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
def actor(
    actor_id: str,
    principal: Annotated[Principal, Depends(require_authenticated)],
) -> dict[str, Any]:
    """Resolve an approver and their role, so the UI can show who may approve."""
    del principal
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


# ---------------------------------------------------------------------------
# Static frontend (must be LAST: it registers a catch-all route)
# ---------------------------------------------------------------------------

serve_frontend = mount_frontend(app)
