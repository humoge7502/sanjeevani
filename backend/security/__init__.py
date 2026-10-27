"""Security: identity, credentials, authorization and request hardening.

    identity.py   accounts, PBKDF2 password hashing, tenants
    tokens.py     signed access tokens (issue + verify)
    principal.py  the request principal and the FastAPI dependencies
    headers.py    security response headers (CSP and friends)
    ratelimit.py  in-process token-bucket throttling

Authentication is OFF by default so the offline demo and the container smoke test
are unchanged. Turn it on with ``SANJEEVANI_AUTH_ENABLED=true`` and a
``SANJEEVANI_JWT_SECRET`` of at least 32 bytes.
"""

from backend.security.headers import (
    BASE_HEADERS,
    NONCE_SCOPE_KEY,
    SecurityHeadersMiddleware,
    build_csp,
    nonce_for,
)
from backend.security.identity import (
    Account,
    IdentityError,
    IdentityStore,
    hash_password,
    store,
    verify_password,
)
from backend.security.principal import (
    AuthError,
    Principal,
    current_principal,
    demo_principal,
    require_admin,
    require_authenticated,
    require_roles,
    reset_security,
    token_service,
)
from backend.security.ratelimit import RateLimitDecision, TokenBucketLimiter
from backend.security.tokens import TokenService, TokenSettings, resolve_settings

__all__ = [
    "BASE_HEADERS",
    "NONCE_SCOPE_KEY",
    "Account",
    "AuthError",
    "IdentityError",
    "IdentityStore",
    "Principal",
    "RateLimitDecision",
    "SecurityHeadersMiddleware",
    "TokenBucketLimiter",
    "TokenService",
    "TokenSettings",
    "build_csp",
    "current_principal",
    "demo_principal",
    "hash_password",
    "nonce_for",
    "require_admin",
    "require_authenticated",
    "require_roles",
    "reset_security",
    "resolve_settings",
    "store",
    "token_service",
    "verify_password",
]
