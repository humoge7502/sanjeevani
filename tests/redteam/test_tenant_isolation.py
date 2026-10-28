"""RED TEAM: cross-tenant governance access.

N-1 from the post-completion analysis: the token's tenant claim existed end to
end, but the approval registry was process-global -- a caller from tenant B could
act on tenant A's plan. Now a governance journey belongs to the tenant that
created it, and a foreign tenant is refused (403 TENANT_MISMATCH) before any
state can be read or moved.

Both tests below drive the real ASGI app with authentication enabled, exactly
like the rest of this suite: a passing test means the attack failed.

    python -m pytest tests/redteam/test_tenant_isolation.py -q
"""

from __future__ import annotations

from datetime import UTC, datetime

import jwt
import pytest
from fastapi.testclient import TestClient

from backend.governance.approval import ApprovalRegistry, GovernanceError
from backend.security import reset_security

pytestmark = pytest.mark.redteam

SECRET = "t" * 48
ACME = "acme-pharma"
HELIX = "helix-biologics"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SANJEEVANI_AUTH_ENABLED", "true")
    monkeypatch.setenv("SANJEEVANI_JWT_SECRET", SECRET)
    reset_security()
    from backend.api.app import app

    with TestClient(app) as test_client:
        yield test_client
    reset_security()


def _token(sub: str, role: str, tenant: str) -> str:
    now = int(datetime.now(UTC).timestamp())
    return jwt.encode(
        {
            "sub": sub,
            "role": role,
            "tenant": tenant,
            "iss": "sanjeevani-api",
            "aud": "sanjeevani-command-center",
            "iat": now,
            "exp": now + 3600,
        },
        SECRET,
        algorithm="HS256",
    )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


ACME_PLANNER = _token("meera.iyer", "supply_planning_head", ACME)
FOREIGN_PLANNER = _token("meera.novartis", "supply_planning_head", HELIX)


def _run_and_plan_id(client: TestClient) -> str:
    run = client.post("/api/demo/run", headers=_auth(ACME_PLANNER))
    assert run.status_code == 200, run.text
    state = client.get("/api/state", headers=_auth(ACME_PLANNER)).json()
    plan_id = state["plan"]["plan_id"]
    assert plan_id, "the run produced no plan"
    return plan_id


# ---------------------------------------------------------------------------
# The API boundary
# ---------------------------------------------------------------------------


def test_a_foreign_tenant_cannot_decide_on_anothers_plan(client):
    """Same role, different tenant: the role check would pass, tenancy must not."""
    plan_id = _run_and_plan_id(client)
    response = client.post(
        f"/api/plans/{plan_id}/approve",
        headers=_auth(FOREIGN_PLANNER),
        json={"actor_id": "meera.novartis", "rationale": "I hold the right role in my own tenant."},
    )
    assert response.status_code == 403
    assert response.json()["error"] == "TENANT_MISMATCH"


def test_a_foreign_tenant_cannot_read_or_execute_anothers_plan(client):
    plan_id = _run_and_plan_id(client)
    gov = client.get(f"/api/plans/{plan_id}/governance", headers=_auth(FOREIGN_PLANNER))
    assert gov.status_code == 403
    execute = client.post(f"/api/plans/{plan_id}/execute", headers=_auth(FOREIGN_PLANNER))
    assert execute.status_code == 403
    assert execute.json()["error"] == "TENANT_MISMATCH"


def test_the_owning_tenant_still_flows_through_the_whole_gate(client):
    """Tenancy must not break the legitimate path: acme approves and executes."""
    plan_id = _run_and_plan_id(client)
    approve = client.post(
        f"/api/plans/{plan_id}/approve",
        headers=_auth(ACME_PLANNER),
        json={"actor_id": "meera.iyer", "rationale": "Own tenant, right role."},
    )
    assert approve.status_code == 200, approve.text
    execute = client.post(f"/api/plans/{plan_id}/execute", headers=_auth(ACME_PLANNER))
    assert execute.status_code == 200, execute.text


# ---------------------------------------------------------------------------
# The registry primitive
# ---------------------------------------------------------------------------


def test_the_registry_refuses_a_parallel_journey_for_the_same_plan():
    registry = ApprovalRegistry()
    first = registry.get_or_create("PLAN-1", tenant=ACME)
    assert registry.get_or_create("PLAN-1", tenant=ACME) is first
    with pytest.raises(GovernanceError) as excinfo:
        registry.get_or_create("PLAN-1", tenant=HELIX)
    assert excinfo.value.code == "TENANT_MISMATCH"


def test_reset_clears_tenancy_so_the_demo_can_start_over():
    registry = ApprovalRegistry()
    registry.get_or_create("PLAN-1", tenant=ACME)
    registry.reset()
    other = registry.get_or_create("PLAN-1", tenant=HELIX)
    assert other.tenant == HELIX
