"""Signed credentials: issuing and verifying access tokens.

A token is the *only* thing that establishes identity. Nothing in a request body
can create a principal, which is the property the previous build lacked.

Properties this module guarantees, each with a test:

  * **Algorithm is pinned.** Verification passes ``algorithms=["HS256"]``
    explicitly, so a token that declares ``alg: none`` -- or a different
    algorithm an attacker controls -- is refused rather than honoured.
  * **Lifetime is enforced.** ``exp`` is required and validated by the library; a
    token with no expiry is not accepted as "never expires", it is rejected.
  * **Audience and issuer are enforced.** A token minted for a different service
    is not a token for this one.
  * **The secret must be strong.** With auth enabled and no secret configured,
    the process refuses to mint or verify rather than defaulting to a guessable
    value. An ephemeral secret is available for local development, and it is
    opt-in and loudly labelled.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import jwt

from backend.security.identity import Account, IdentityError

ISSUER: Final[str] = "sanjeevani-api"
AUDIENCE: Final[str] = "sanjeevani-command-center"

#: Below this the HMAC key is not worth the name. Refused when auth is enabled and
#: an explicit secret is supplied.
MIN_SECRET_BYTES: Final[int] = 32

#: Lifetime of an issued access token.
DEFAULT_TTL_SECONDS: Final[int] = 3600


@dataclass(frozen=True)
class TokenSettings:
    """Resolved authentication settings."""

    enabled: bool
    secret: str
    ttl_seconds: int
    ephemeral_secret: bool


def resolve_settings(cfg: dict[str, Any], env: dict[str, str]) -> TokenSettings:
    """Resolve auth settings from YAML + environment.

    Environment wins, and a weak or missing secret is a hard failure when auth is
    on -- a deployment that thinks it is authenticated but is not is worse than
    one that refuses to start.
    """
    auth = cfg.get("auth", {})
    enabled = str(env.get("SANJEEVANI_AUTH_ENABLED", auth.get("enabled", False))).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    ttl = int(env.get("SANJEEVANI_JWT_TTL_SECONDS", auth.get("token_ttl_seconds", DEFAULT_TTL_SECONDS)))
    secret = env.get("SANJEEVANI_JWT_SECRET", "") or str(auth.get("jwt_secret", "") or "")
    allow_ephemeral = str(
        env.get("SANJEEVANI_ALLOW_EPHEMERAL_SECRET", auth.get("allow_ephemeral_secret", False))
    ).strip().lower() in {"1", "true", "yes", "on"}

    ephemeral = False
    if enabled and not secret:
        if not allow_ephemeral:
            raise IdentityError(
                "JWT_SECRET_REQUIRED",
                "SANJEEVANI_AUTH_ENABLED is on but no SANJEEVANI_JWT_SECRET is set. "
                "Refusing to run with an implicit secret: a deployment that believes it "
                "is authenticated but is not is worse than one that refuses to start. "
                "Set a >=32-byte secret, or set SANJEEVANI_ALLOW_EPHEMERAL_SECRET=1 for "
                "local development only.",
            )
        secret = secrets.token_urlsafe(48)
        ephemeral = True
    if enabled and len(secret.encode("utf-8")) < MIN_SECRET_BYTES and not ephemeral:
        raise IdentityError(
            "JWT_SECRET_TOO_WEAK",
            f"The configured JWT secret is {len(secret.encode('utf-8'))} bytes; "
            f"at least {MIN_SECRET_BYTES} are required.",
        )
    return TokenSettings(enabled=enabled, secret=secret, ttl_seconds=ttl, ephemeral_secret=ephemeral)


class TokenService:
    """Mint and verify access tokens for one secret."""

    def __init__(self, settings: TokenSettings) -> None:
        self._settings = settings

    @property
    def settings(self) -> TokenSettings:
        return self._settings

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    def issue(self, account: Account, *, now: datetime | None = None) -> dict[str, Any]:
        """Mint an access token for an account."""
        if not self._settings.enabled:
            raise IdentityError(
                "AUTH_DISABLED",
                "Authentication is disabled. Enable it with SANJEEVANI_AUTH_ENABLED=true "
                "to obtain a token.",
            )
        issued_at = now or datetime.now(UTC)
        expires = issued_at + timedelta(seconds=self._settings.ttl_seconds)
        claims: dict[str, Any] = {
            "sub": account.id,
            "name": account.name,
            "full_name": account.full_name,
            "role": account.role,
            "tenant": account.tenant,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": int(issued_at.timestamp()),
            "exp": int(expires.timestamp()),
            "jti": secrets.token_urlsafe(12),
        }
        token = jwt.encode(claims, self._settings.secret, algorithm="HS256")
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": self._settings.ttl_seconds,
            "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "principal": {
                "id": account.id,
                "name": account.name,
                "full_name": account.full_name,
                "role": account.role,
                "tenant": account.tenant,
            },
        }

    def verify(self, token: str) -> dict[str, Any]:
        """Verify a token and return its claims. Raises IdentityError on failure."""
        if not self._settings.enabled:
            raise IdentityError("AUTH_DISABLED", "Authentication is disabled.")
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._settings.secret,
                # Pinned explicitly. An unpinned decode is the classic JWT
                # vulnerability: a token that says `alg: none` would be honoured.
                algorithms=["HS256"],
                audience=AUDIENCE,
                issuer=ISSUER,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise IdentityError("TOKEN_EXPIRED", "The access token has expired.") from exc
        except jwt.InvalidAudienceError as exc:
            raise IdentityError(
                "TOKEN_INVALID_AUDIENCE", "The access token was issued for another audience."
            ) from exc
        except jwt.InvalidIssuerError as exc:
            raise IdentityError(
                "TOKEN_INVALID_ISSUER", "The access token was issued by another issuer."
            ) from exc
        except jwt.InvalidTokenError as exc:
            # Covers bad signatures, unsupported algorithms (including `none`),
            # malformed tokens and missing required claims.
            raise IdentityError("TOKEN_INVALID", f"The access token is not valid: {exc}.") from exc
        if not claims.get("sub"):
            raise IdentityError("TOKEN_INVALID", "The access token carries no subject.")
        return claims
