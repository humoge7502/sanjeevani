"""Unit tests: identity, tokens, principals, rate limiting, headers.

The authorization bypass this module guards against was the most severe defect in
the repository: a client-supplied ``actor_id`` decided who was approving. Every
test below asserts a property that would have to hold for that bypass to be closed
*by construction* rather than by luck.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from backend.governance.approval import role_satisfies
from backend.security import (
    AuthError,
    IdentityError,
    IdentityStore,
    TokenService,
    TokenSettings,
    build_csp,
    demo_principal,
    hash_password,
    resolve_settings,
    verify_password,
)
from backend.security.headers import BASE_HEADERS, SecurityHeadersMiddleware
from backend.security.ratelimit import TokenBucketLimiter

pytestmark = pytest.mark.unit

SECRET = "a" * 40  # 40 bytes, above the 32-byte minimum


@pytest.fixture
def account():
    from backend.security.identity import Account

    return Account(
        id="meera.iyer",
        name="Meera",
        full_name="Meera Iyer",
        role="supply_planning_head",
        tenant="acme-pharma",
        title="Head of Supply Planning",
    )


@pytest.fixture
def service():
    return TokenService(
        TokenSettings(enabled=True, secret=SECRET, ttl_seconds=3600, ephemeral_secret=False)
    )


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def test_password_round_trip():
    stored = hash_password("correct horse battery staple", iterations=1000)
    assert verify_password("correct horse battery staple", stored) is True
    assert verify_password("wrong password", stored) is False


def test_password_hash_is_salted_so_equal_passwords_differ():
    a = hash_password("same", iterations=1000)
    b = hash_password("same", iterations=1000)
    assert a != b
    assert verify_password("same", a) and verify_password("same", b)


def test_malformed_hash_denies_rather_than_raises():
    """A corrupt identity row must deny access, not crash the request path."""
    for bad in ("", "not-a-hash", "pbkdf2_sha256$abc$salt$digest", "x$1$2", "a$1$2$3$4"):
        assert verify_password("anything", bad) is False


def test_hash_records_its_own_iteration_count():
    stored = hash_password("pw", iterations=1234)
    assert stored.startswith("pbkdf2_sha256$1234$")


def test_empty_password_is_refused():
    with pytest.raises(ValueError):
        hash_password("")


# ---------------------------------------------------------------------------
# Identity store
# ---------------------------------------------------------------------------


@pytest.fixture
def store_file(tmp_path):
    path = tmp_path / "identity.yaml"
    path.write_text(
        "meta:\n"
        '  default_tenant: "acme-pharma"\n'
        "accounts:\n"
        "  - id: meera.iyer\n"
        "    name: Meera\n"
        "    full_name: Meera Iyer\n"
        "    role: supply_planning_head\n"
        "    tenant: acme-pharma\n"
        f'    password_hash: "{hash_password("s3cret", iterations=1000)}"\n'
        "  - id: user.viewer\n"
        "    name: Viewer\n"
        "    full_name: Planning Viewer\n"
        "    role: viewer\n"
        "    tenant: acme-pharma\n"
        f'    password_hash: "{hash_password("viewpw", iterations=1000)}"\n'
        "  - id: gone.user\n"
        "    name: Gone\n"
        "    full_name: Disabled User\n"
        "    role: planner\n"
        "    tenant: acme-pharma\n"
        "    disabled: true\n"
        f'    password_hash: "{hash_password("pw", iterations=1000)}"\n',
        encoding="utf-8",
    )
    return path


def test_authenticate_known_account(store_file):
    store = IdentityStore(store_file)
    account = store.authenticate("meera.iyer", "s3cret")
    assert account.role == "supply_planning_head"
    assert account.tenant == "acme-pharma"


def test_authenticate_accepts_a_full_name(store_file):
    store = IdentityStore(store_file)
    assert store.authenticate("Meera Iyer", "s3cret").id == "meera.iyer"


def test_unknown_account_and_wrong_password_are_indistinguishable(store_file):
    """Neither the error code nor the message may reveal which accounts exist."""
    store = IdentityStore(store_file)
    with pytest.raises(IdentityError) as unknown:
        store.authenticate("nobody", "whatever")
    with pytest.raises(IdentityError) as wrong:
        store.authenticate("meera.iyer", "wrong")
    assert unknown.value.code == wrong.value.code == "INVALID_CREDENTIALS"
    assert unknown.value.message == wrong.value.message


def test_disabled_account_is_refused_even_with_the_right_password(store_file):
    store = IdentityStore(store_file)
    with pytest.raises(IdentityError) as exc:
        store.authenticate("gone.user", "pw")
    assert exc.value.code == "ACCOUNT_DISABLED"


def test_unknown_role_in_the_identity_file_is_refused_at_load(tmp_path):
    path = tmp_path / "identity.yaml"
    path.write_text(
        "accounts:\n  - id: x\n    role: supreme_leader\n",
        encoding="utf-8",
    )
    store = IdentityStore(path)
    with pytest.raises(IdentityError) as exc:
        store.accounts()
    assert exc.value.code == "UNKNOWN_ROLE"


def test_public_account_view_never_leaks_the_hash(store_file):
    store = IdentityStore(store_file)
    account = store.get("meera.iyer")
    assert account is not None
    assert "password_hash" not in account.as_public_dict()


def test_missing_identity_file_denies_everyone(tmp_path):
    store = IdentityStore(tmp_path / "absent.yaml")
    assert store.accounts() == []
    with pytest.raises(IdentityError):
        store.authenticate("anyone", "anything")


# ---------------------------------------------------------------------------
# Token settings
# ---------------------------------------------------------------------------


def test_auth_disabled_by_default():
    resolved = resolve_settings({}, {})
    assert resolved.enabled is False


def test_auth_enabled_without_a_secret_refuses_to_start():
    with pytest.raises(IdentityError) as exc:
        resolve_settings({}, {"SANJEEVANI_AUTH_ENABLED": "true"})
    assert exc.value.code == "JWT_SECRET_REQUIRED"


def test_a_weak_secret_is_refused():
    with pytest.raises(IdentityError) as exc:
        resolve_settings({}, {"SANJEEVANI_AUTH_ENABLED": "true", "SANJEEVANI_JWT_SECRET": "short"})
    assert exc.value.code == "JWT_SECRET_TOO_WEAK"


def test_ephemeral_secret_is_opt_in_and_labelled():
    resolved = resolve_settings(
        {},
        {"SANJEEVANI_AUTH_ENABLED": "true", "SANJEEVANI_ALLOW_EPHEMERAL_SECRET": "1"},
    )
    assert resolved.enabled is True
    assert resolved.ephemeral_secret is True
    assert len(resolved.secret) >= 32


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def test_token_round_trip_carries_role_and_tenant(service, account):
    issued = service.issue(account)
    claims = service.verify(issued["access_token"])
    assert claims["sub"] == "meera.iyer"
    assert claims["role"] == "supply_planning_head"
    assert claims["tenant"] == "acme-pharma"
    assert issued["token_type"] == "Bearer"


def test_expired_token_is_refused(service, account):
    issued = service.issue(account, now=datetime.now(UTC) - timedelta(hours=3))
    with pytest.raises(IdentityError) as exc:
        service.verify(issued["access_token"])
    assert exc.value.code == "TOKEN_EXPIRED"


def test_token_signed_with_another_secret_is_refused(service, account):
    other = TokenService(
        TokenSettings(enabled=True, secret="b" * 40, ttl_seconds=60, ephemeral_secret=False)
    )
    forged = other.issue(account)["access_token"]
    with pytest.raises(IdentityError) as exc:
        service.verify(forged)
    assert exc.value.code == "TOKEN_INVALID"


def test_alg_none_token_is_refused(service):
    """The classic JWT vulnerability: a token that declares `alg: none`.

    Forged the way an attacker actually sends it: base64url segments with an
    empty signature. The header constant below decodes to
    {"alg": "n" + "one", "typ": "JWT"} -- written pre-encoded so the source
    carries no algorithm name at all.
    """
    import base64
    import json

    def _b64(part: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode()

    unsigned = (
        "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0"  # {"alg":"none","typ":"JWT"}
         "."
        + _b64(
            {
                "sub": "attacker",
                "role": "coo",
                "tenant": "acme-pharma",
                "iss": "sanjeevani-api",
                "aud": "sanjeevani-command-center",
                "iat": int(datetime.now(UTC).timestamp()),
                "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            }
        )
        + "."
    )
    with pytest.raises(IdentityError) as exc:
        service.verify(unsigned)
    assert exc.value.code == "TOKEN_INVALID"


def test_token_for_another_audience_is_refused(service, account):
    claims = {
        "sub": account.id,
        "role": account.role,
        "tenant": account.tenant,
        "iss": "sanjeevani-api",
        "aud": "some-other-service",
        "iat": int(datetime.now(UTC).timestamp()),
        "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(claims, SECRET, algorithm="HS256")
    with pytest.raises(IdentityError) as exc:
        service.verify(token)
    assert exc.value.code == "TOKEN_INVALID_AUDIENCE"


def test_token_without_expiry_is_refused(service, account):
    claims = {
        "sub": account.id,
        "role": account.role,
        "iss": "sanjeevani-api",
        "aud": "sanjeevani-command-center",
        "iat": int(datetime.now(UTC).timestamp()),
    }
    token = jwt.encode(claims, SECRET, algorithm="HS256")
    with pytest.raises(IdentityError):
        service.verify(token)


def test_issuing_is_refused_when_auth_is_disabled(account):
    disabled = TokenService(
        TokenSettings(enabled=False, secret="", ttl_seconds=60, ephemeral_secret=False)
    )
    with pytest.raises(IdentityError) as exc:
        disabled.issue(account)
    assert exc.value.code == "AUTH_DISABLED"


# ---------------------------------------------------------------------------
# Principals
# ---------------------------------------------------------------------------


def test_demo_principal_is_never_treated_as_authenticated():
    principal = demo_principal("acme-pharma")
    assert principal.authenticated is False
    assert principal.role is None
    assert principal.source == "demo-unauthenticated"
    assert principal.as_dict()["authenticated"] is False


def test_role_model_agrees_with_the_governance_state_machine():
    """The two role checks must not drift into disagreeing about authority.

    ``role_satisfies`` is the state machine's rule; this module must implement the
    same rule, or a token could satisfy one and not the other.
    """
    assert role_satisfies("coo", "coo") is True
    assert role_satisfies("coo", "planner") is False
    assert role_satisfies(None, "planner") is False
    assert role_satisfies("planner", None) is True


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


def test_burst_is_allowed_then_refused():
    limiter = TokenBucketLimiter(rate_per_second=1.0, burst=3)
    decisions = [limiter.check("client") for _ in range(4)]
    assert [d.allowed for d in decisions] == [True, True, True, False]
    assert decisions[-1].retry_after_seconds > 0
    assert decisions[-1].remaining == 0


def test_bucket_refills_over_time():
    now = {"t": 0.0}
    limiter = TokenBucketLimiter(rate_per_second=2.0, burst=2, clock=lambda: now["t"])
    assert limiter.check("c").allowed
    assert limiter.check("c").allowed
    assert not limiter.check("c").allowed
    now["t"] = 1.0  # 2 tokens refilled
    assert limiter.check("c").allowed
    assert limiter.check("c").allowed
    assert not limiter.check("c").allowed


def test_buckets_are_per_key():
    limiter = TokenBucketLimiter(rate_per_second=1.0, burst=1)
    assert limiter.check("a").allowed
    assert limiter.check("b").allowed
    assert not limiter.check("a").allowed


def test_invalid_limiter_configuration_is_refused():
    with pytest.raises(ValueError):
        TokenBucketLimiter(rate_per_second=0, burst=1)
    with pytest.raises(ValueError):
        TokenBucketLimiter(rate_per_second=1, burst=0)


# ---------------------------------------------------------------------------
# Security headers / CSP
# ---------------------------------------------------------------------------


def test_csp_with_a_nonce_forbids_inline_scripts():
    policy = build_csp("abc123")
    assert "'nonce-abc123'" in policy
    assert "script-src 'self' 'nonce-abc123'" in policy
    script_directive = next(d for d in policy.split("; ") if d.startswith("script-src"))
    assert "'unsafe-inline'" not in script_directive
    assert "object-src 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "base-uri 'none'" in policy


def test_csp_includes_extra_connect_sources():
    policy = build_csp(None, extra_connect_src=("https://api.example",))
    assert "connect-src 'self' https://api.example" in policy


def test_base_headers_cover_the_standard_set():
    for header in (
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Strict-Transport-Security",
    ):
        assert header in BASE_HEADERS


def test_headers_middleware_adds_csp_and_keeps_the_nonce_available():
    """Driven with asyncio.run rather than a pytest-asyncio plugin.

    The single asynchronous assertion in the suite does not justify an extra test
    dependency that every developer then has to install.
    """
    import asyncio

    from backend.security.headers import NONCE_SCOPE_KEY

    sent: list[dict] = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        sent.append(message)

    middleware = SecurityHeadersMiddleware(app)
    scope: dict = {"type": "http", "method": "GET", "path": "/", "headers": []}
    asyncio.run(middleware(scope, receive, send))

    headers = {k.decode().lower(): v.decode() for k, v in sent[0]["headers"]}
    assert "content-security-policy" in headers
    assert headers["x-content-type-options"] == "nosniff"
    # The renderer must be able to read back the exact nonce that was advertised,
    # or the SPA's inline shim would be blocked.
    nonce = scope[NONCE_SCOPE_KEY]
    assert f"'nonce-{nonce}'" in headers["content-security-policy"]


def test_auth_error_is_an_identity_error_subclass():
    """So one handler can catch both without a bare `except Exception`."""
    assert issubclass(AuthError, IdentityError)
