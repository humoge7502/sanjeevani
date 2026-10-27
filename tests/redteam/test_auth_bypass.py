"""RED TEAM: authentication and authorization bypass attempts.

The original defect this file exists for: ``POST /api/plans/{id}/approve`` with
``{"actor_id": "meera.iyer"}`` returned 200 and executed a multi-million-dollar
recovery plan, because the approver was a string in the request body. Every test
here is named after an attack, and a passing test means **the attack failed**.

These drive the real ASGI app through ``TestClient``, so the middleware chain,
the dependency guards and the error handlers are all in the path -- not just the
handler functions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient

from backend.security import reset_security

pytestmark = pytest.mark.redteam

SECRET = "r" * 48
ACME = "acme-pharma"


@pytest.fixture
def client(monkeypatch):
    """The real app with authentication ENABLED."""
    monkeypatch.setenv("SANJEEVANI_AUTH_ENABLED", "true")
    monkeypatch.setenv("SANJEEVANI_JWT_SECRET", SECRET)
    reset_security()
    from backend.api.app import app

    with TestClient(app) as test_client:
        yield test_client
    reset_security()


@pytest.fixture
def open_client():
    """The real app with authentication DISABLED, i.e. the demo path."""
    from backend.api.app import app

    with TestClient(app) as test_client:
        yield test_client


def _token(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/token", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _forged(**overrides) -> str:
    claims = {
        "sub": "svc.admin",
        "role": "admin",
        "tenant": ACME,
        "iss": "sanjeevani-api",
        "aud": "sanjeevani-command-center",
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    claims.update(overrides)
    return jwt.encode(claims, "wrong-secret-wrong-secret-wrong-secr", algorithm="HS256")


# ---------------------------------------------------------------------------
# A19 -- unauthenticated access
# ---------------------------------------------------------------------------


def test_a19_unauthenticated_reads_are_refused(client):
    for path in ("/api/state", "/api/audit", "/api/network", "/api/policies", "/api/auth/me"):
        assert client.get(path).status_code == 401, path


def test_a19b_unauthenticated_execution_is_refused(client):
    """The original bypass, attempted with no credential at all."""
    response = client.post("/api/plans/PLAN-001/execute")
    assert response.status_code == 401
    assert response.json()["error"] == "AUTHENTICATION_REQUIRED"


def test_a19c_unauthenticated_approval_is_refused(client):
    response = client.post(
        "/api/plans/PLAN-001/approve",
        json={"actor_id": "meera.iyer", "rationale": "no credential at all"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "AUTHENTICATION_REQUIRED"


def test_a19d_health_remains_reachable_and_discloses_that_auth_is_on(client):
    """A probe must work, and must tell the truth about the security posture."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["security"]["auth_enabled"] is True


# ---------------------------------------------------------------------------
# A20 -- forged and tampered tokens
# ---------------------------------------------------------------------------


def test_a20_token_signed_with_the_wrong_secret_is_refused(client):
    response = client.get("/api/state", headers=_auth(_forged()))
    assert response.status_code == 401
    assert response.json()["error"] == "TOKEN_INVALID"


def test_a20b_alg_none_token_is_refused(client):
    """An unsigned token, forged the way an attacker sends it: base64url
    segments and an empty signature. The header constant decodes to
    {"alg": "none", "typ": "JWT"}."""
    import base64
    import json

    def _b64(part: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode()

    token = (
        "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"  # {"alg":"none","typ":"JWT"}
         "."
        + _b64(
            {
                "sub": "svc.admin",
                "role": "admin",
                "tenant": ACME,
                "iss": "sanjeevani-api",
                "aud": "sanjeevani-command-center",
                "iat": int(datetime.now(UTC).timestamp()),
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            }
        )
        + "."
    )
    assert client.get("/api/state", headers=_auth(token)).status_code == 401


def test_a20c_expired_token_is_refused(client):
    expired = _forge_real_token(client, exp_delta=timedelta(hours=-2))
    response = client.get("/api/state", headers=_auth(expired))
    assert response.status_code == 401
    assert response.json()["error"] == "TOKEN_EXPIRED"


def test_a20d_role_claim_is_taken_from_the_verified_token_not_the_body(client):
    """A token cannot be used to approve as somebody with a different role.

    ``meera.iyer`` holds ``supply_planning_head``. If the request body could name
    a different actor, the whole point of the token would be void.
    """
    client.post("/api/demo/run", headers=_auth(_token(client, "svc.admin", "demo-admin-2026")))
    viewer = _token(client, "user.viewer", "demo-viewer-2026")
    response = client.post(
        "/api/plans/PLAN-001/approve",
        json={"actor_id": "meera.iyer", "rationale": "acting as someone else"},
        headers=_auth(viewer),
    )
    assert response.status_code == 403
    assert response.json()["error"] == "ACTOR_MISMATCH"


def test_a20e_insufficient_role_cannot_approve(client):
    admin = _token(client, "svc.admin", "demo-admin-2026")
    client.post("/api/demo/run", headers=_auth(admin))
    viewer = _token(client, "user.viewer", "demo-viewer-2026")
    response = client.post(
        "/api/plans/PLAN-001/approve",
        json={"actor_id": "user.viewer", "rationale": "I am only a viewer"},
        headers=_auth(viewer),
    )
    assert response.status_code == 403
    assert response.json()["error"] in {"UNAUTHORIZED_APPROVER", "INSUFFICIENT_ROLE"}


def test_a20f_the_authorised_role_still_works(client):
    """The control must not be so tight that the legitimate path is blocked."""
    admin = _token(client, "svc.admin", "demo-admin-2026")
    run = client.post("/api/demo/run", headers=_auth(admin))
    assert run.status_code == 200
    plan_id = run.json()["plan"]["plan_id"]
    required_role = run.json()["compliance"]["required_role"]

    # Pick an account holding exactly the required role.
    credentials = {
        "supply_planning_head": ("meera.iyer", "demo-meera-2026"),
        "coo": ("rajan.kulkarni", "demo-rajan-2026"),
        "planner": ("anil.deshpande", "demo-anil-2026"),
    }
    username, password = credentials[required_role]
    token = _token(client, username, password)
    approved = client.post(
        f"/api/plans/{plan_id}/approve",
        json={"actor_id": username, "rationale": "authorised"},
        headers=_auth(token),
    )
    assert approved.status_code == 200, approved.text
    executed = client.post(f"/api/plans/{plan_id}/execute", headers=_auth(token))
    assert executed.status_code == 200, executed.text


# ---------------------------------------------------------------------------
# A21 -- destructive and administrative operations
# ---------------------------------------------------------------------------


def test_a21_reset_requires_admin(client):
    viewer = _token(client, "user.viewer", "demo-viewer-2026")
    response = client.post("/api/demo/reset", headers=_auth(viewer))
    assert response.status_code == 403
    assert response.json()["error"] == "INSUFFICIENT_ROLE"


def test_a21b_reset_is_refused_without_a_token(client):
    assert client.post("/api/demo/reset").status_code == 401


def test_a21c_an_auditor_may_read_but_not_act(client):
    auditor = _token(client, "user.auditor", "demo-auditor-2026")
    assert client.get("/api/audit", headers=_auth(auditor)).status_code == 200
    assert client.post("/api/demo/reset", headers=_auth(auditor)).status_code == 403


# ---------------------------------------------------------------------------
# A22 -- request-shape and volume attacks
# ---------------------------------------------------------------------------


def test_a22_oversized_body_is_refused_before_it_is_parsed(client):
    token = _token(client, "svc.admin", "demo-admin-2026")
    response = client.post(
        "/api/plans/PLAN-001/approve",
        content=b"x" * 200_000,
        headers={**_auth(token), "Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"] == "REQUEST_TOO_LARGE"


def test_a22b_a_flood_of_approval_attempts_is_throttled(client):
    """Governance probing must stop being free."""
    token = _token(client, "user.viewer", "demo-viewer-2026")
    statuses = [
        client.post(
            "/api/plans/PLAN-001/approve",
            json={"actor_id": "user.viewer", "rationale": "probe"},
            headers=_auth(token),
        ).status_code
        for _ in range(60)
    ]
    assert 429 in statuses, "the write limiter never engaged"
    first_429 = statuses.index(429)
    assert first_429 > 0, "the limiter blocked the legitimate first request too"


def test_a22c_extra_fields_in_the_decision_body_are_refused(client):
    """A client posting `approved: true` must be rejected, not silently ignored."""
    token = _token(client, "svc.admin", "demo-admin-2026")
    response = client.post(
        "/api/plans/PLAN-001/approve",
        json={"actor_id": "svc.admin", "approved": True, "risk_tier": "L1"},
        headers=_auth(token),
    )
    assert response.status_code == 422


def test_a22d_wildcard_characters_in_actor_id_are_refused(client):
    token = _token(client, "svc.admin", "demo-admin-2026")
    response = client.post(
        "/api/plans/PLAN-001/approve",
        json={"actor_id": "*", "rationale": "wildcard"},
        headers=_auth(token),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# A23 -- security headers on every response, including refusals
# ---------------------------------------------------------------------------


def test_a23_security_headers_are_present_on_a_refusal(client):
    response = client.get("/api/state")
    assert response.status_code == 401
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "content-security-policy" in response.headers
    assert response.headers["referrer-policy"] == "no-referrer"


def test_a23b_request_id_is_echoed_for_correlation(client):
    response = client.get("/api/health", headers={"X-Request-ID": "trace-me-1234"})
    assert response.headers["x-request-id"] == "trace-me-1234"


def test_a23c_a_hostile_request_id_is_replaced_not_trusted(client):
    """The id lands in log lines, so a caller must not be able to inject one."""
    response = client.get("/api/health", headers={"X-Request-ID": "bad id with spaces"})
    assert response.headers["x-request-id"] != "bad id with spaces"
    assert len(response.headers["x-request-id"]) == 16


# ---------------------------------------------------------------------------
# A24 -- the demo path must remain open when auth is off, but honest about it
# ---------------------------------------------------------------------------


def test_a24_demo_path_is_unchanged_when_auth_is_disabled(open_client):
    assert open_client.get("/api/state").status_code == 200
    run = open_client.post("/api/demo/run")
    assert run.status_code == 200
    plan_id = run.json()["plan"]["plan_id"]
    approved = open_client.post(
        f"/api/plans/{plan_id}/approve",
        json={"actor_id": "meera.iyer", "rationale": "demo, unauthenticated"},
    )
    assert approved.status_code == 200
    # `/api/state` reports who the server believes is calling, so the demo path
    # is visibly unauthenticated rather than merely permitted.
    assert open_client.get("/api/state").json()["caller"]["authenticated"] is False


def test_a24b_token_issuance_is_refused_when_auth_is_disabled(open_client):
    response = open_client.post(
        "/api/auth/token", json={"username": "meera.iyer", "password": "demo-meera-2026"}
    )
    assert response.status_code == 401
    assert response.json()["error"] == "AUTH_DISABLED"


def test_a24c_health_says_authentication_is_off(open_client):
    payload = open_client.get("/api/health").json()
    assert payload["security"]["auth_enabled"] is False


def _forge_real_token(client: TestClient, *, exp_delta: timedelta) -> str:
    """Mint a token with the real secret but an arbitrary expiry, bypassing login.

    Needed because the login endpoint always issues a valid, unexpired token.
    """
    claims = {
        "sub": "svc.admin",
        "role": "admin",
        "tenant": ACME,
        "iss": "sanjeevani-api",
        "aud": "sanjeevani-command-center",
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + exp_delta).timestamp()),
    }
    return jwt.encode(claims, SECRET, algorithm="HS256")
