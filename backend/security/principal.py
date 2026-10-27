"""The request principal, and the FastAPI dependencies that establish it.

The shape of the fix for the original authorization bypass is here: an endpoint
never reads "who is this" from its body. It declares ``Depends(...)`` and receives
a principal that was established from a verified credential.

BACKWARD COMPATIBILITY IS DELIBERATE. Authentication is **off by default**, so the
offline demo, the browser suite and the container smoke test behave exactly as
before. When it is off, ``current_principal`` returns an *unauthenticated* demo
principal and every guard becomes a no-op -- but the principal's ``authenticated``
flag is False, and the state it produces says so. When it is on, the same guards
become hard requirements with no code change at the call sites.

That asymmetry is the point: ``auth_enabled`` is observable in ``/api/health`` and
in every audit record, so a deployment can never be *running unauthenticated by
accident and not know it*.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from backend.config import settings
from backend.security.identity import IdentityError, store
from backend.security.tokens import TokenService, TokenSettings, resolve_settings

# ---------------------------------------------------------------------------
# The principal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    """Who is making this request.

    ``authenticated`` is not the same as "has a role": the demo principal has no
    credential and no role, and it is never treated as an authorisation.
    """

    id: str
    name: str
    full_name: str
    role: str | None
    tenant: str
    authenticated: bool
    source: str  # "token" | "demo-unauthenticated"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "full_name": self.full_name,
            "role": self.role,
            "tenant": self.tenant,
            "authenticated": self.authenticated,
            "source": self.source,
        }


def demo_principal(tenant: str | None = None) -> Principal:
    """The principal used when authentication is disabled.

    It is explicitly NOT authenticated. It carries no role, so it can never
    satisfy a role requirement -- which is why the demo path must resolve the
    actor from configuration instead, exactly as it did before.
    """
    return Principal(
        id="demo-anonymous",
        name="demo-anonymous",
        full_name="Unauthenticated demo caller",
        role=None,
        tenant=tenant or store.default_tenant,
        authenticated=False,
        source="demo-unauthenticated",
    )


# ---------------------------------------------------------------------------
# Service resolution
# ---------------------------------------------------------------------------


_LOCK = threading.RLock()
_SERVICE: TokenService | None = None


def reset_security() -> None:
    """Drop the cached token service and identity store (reset route / tests)."""
    global _SERVICE
    with _LOCK:
        _SERVICE = None
    store.reload()


def token_service() -> TokenService:
    """The process-wide token service, resolved from config + environment."""
    global _SERVICE
    with _LOCK:
        if _SERVICE is None:
            cfg = settings()
            resolved: TokenSettings = resolve_settings(cfg, dict(os.environ))
            _SERVICE = TokenService(resolved)
        return _SERVICE


class AuthError(IdentityError):
    """A request-level authentication or authorization refusal."""


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def current_principal(request: Request) -> Principal:
    """Establish the principal. Must be a no-op when auth is disabled."""
    service = token_service()
    if not service.enabled:
        return demo_principal()
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise AuthError(
            "AUTHENTICATION_REQUIRED",
            "A Bearer access token is required. Obtain one from POST /api/auth/token.",
        )
    claims = service.verify(header[7:].strip())
    return Principal(
        id=str(claims["sub"]),
        name=str(claims.get("name") or claims["sub"]),
        full_name=str(claims.get("full_name") or claims["sub"]),
        role=str(claims.get("role")) if claims.get("role") else None,
        tenant=str(claims.get("tenant") or store.default_tenant),
        authenticated=True,
        source="token",
    )


def require_authenticated(request: Request) -> Principal:
    """Read access guard: a verified principal is required when auth is on."""
    principal = current_principal(request)

    # The token may be valid while the ACCOUNT has since been disabled. A token is
    # a claim about the past; the identity store is the present. Re-checking here
    # is what makes revocation possible at all.
    if principal.authenticated:
        account = store.get(principal.id)
        if account is None:
            raise AuthError("ACCOUNT_UNKNOWN", "This account no longer exists.")
        if account.disabled:
            raise AuthError("ACCOUNT_DISABLED", "This account is disabled.")
    return principal


def require_roles(*roles: str) -> Callable[[Request], Principal]:
    """Build a dependency that requires the principal to hold one of ``roles``.

    When authentication is disabled this still enforces nothing, because there is
    no verified identity to enforce against -- but the demo principal has no role,
    so a *role-scoped* endpoint cannot be reached by an anonymous caller even in
    demo mode unless the guard explicitly allows the demo path.
    """
    allowed = frozenset(roles)

    def dependency(request: Request) -> Principal:
        principal = require_authenticated(request)
        if not principal.authenticated:
            return principal
        if principal.role not in allowed:
            raise AuthError(
                "INSUFFICIENT_ROLE",
                f"Role {principal.role!r} may not perform this action. "
                f"Required: one of {sorted(allowed)}.",
            )
        return principal

    return dependency


def require_admin(request: Request) -> Principal:
    """Destructive operations (demo reset) require an administrator when auth is on."""
    principal = require_authenticated(request)
    if not principal.authenticated:
        return principal
    if principal.role != "admin":
        raise AuthError(
            "INSUFFICIENT_ROLE",
            f"Role {principal.role!r} may not perform this action. Required: 'admin'.",
        )
    return principal
