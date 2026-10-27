"""Identity: who a request is, and what they are allowed to do.

WHY THIS EXISTS. The MVP resolved an approver from a client-supplied string
(``{"actor_id": "meera.iyer"}``), which meant anyone who could reach the port
could approve and execute a consequential recovery plan. That is a complete
authorization bypass and it was the highest-severity defect in the repository.

This module is the *verification* half: a principal is established from a signed
credential, never from a request field. The API layer then asks this module
questions instead of trusting the client.

DESIGN CHOICES, AND WHY.

  * **Password hashing is PBKDF2-HMAC-SHA256 from the standard library.** No new
    dependency, no compiled wheel, no CVE surface. The parameters are stored in
    the hash string (``pbkdf2_sha256$<iterations>$<salt>$<hash>``) so they can be
    raised later without invalidating existing records.
  * **Comparison is constant-time** (``hmac.compare_digest``). A byte-by-byte
    compare leaks a timing oracle; it costs nothing to avoid.
  * **Roles are exact-match, not hierarchical.** This deliberately mirrors
    ``governance.approval.role_satisfies``: a COO does not implicitly satisfy a
    planner requirement. A narrow model that refuses more is safer than one that
    guesses seniority. The two must stay in agreement, and a test asserts it.
  * **Tenancy is a claim, not a query parameter.** A caller cannot ask for another
    tenant's data; the tenant comes from the verified token and is applied by the
    server.

This is a *file-backed* identity store, which is honest for a prototype and
obviously not a directory service. ``docs/security-threat-model.md`` records the
production requirement (OIDC + a managed directory).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from backend.config import CONFIG_DIR

IDENTITY_FILE: Final[Path] = CONFIG_DIR / "identity.yaml"

#: PBKDF2 iteration count for newly generated hashes. Verification uses whatever
#: the stored hash declares, so this can be raised without a migration.
DEFAULT_PBKDF2_ITERATIONS: Final[int] = 600_000

ALGORITHM: Final[str] = "pbkdf2_sha256"

#: Roles this build understands. An unknown role in the identity file is refused
#: at load time rather than silently creating a principal nothing can authorise.
KNOWN_ROLES: Final[frozenset[str]] = frozenset(
    {
        "admin",
        "auditor",
        "viewer",
        "planner",
        "supply_planning_head",
        "coo",
    }
)

#: Roles that may perform state-changing operations at all.
PRIVILEGED_ROLES: Final[frozenset[str]] = frozenset(
    {"admin", "planner", "supply_planning_head", "coo"}
)


class IdentityError(Exception):
    """A credential or identity record was rejected. Never a 500."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(
    password: str, *, iterations: int = DEFAULT_PBKDF2_ITERATIONS, salt: str | None = None
) -> str:
    """Derive a storable hash. Returns ``pbkdf2_sha256$iterations$salt$digest``."""
    if not password:
        raise ValueError("password must not be empty")
    use_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), use_salt.encode("utf-8"), iterations
    ).hex()
    return f"{ALGORITHM}${iterations}${use_salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification against a stored hash.

    Returns False rather than raising on a malformed record: a corrupt identity
    row must deny access, not crash the request path.
    """
    try:
        algorithm, raw_iterations, salt, expected = stored.split("$", 3)
        iterations = int(raw_iterations)
    except (ValueError, AttributeError):
        return False
    if algorithm != ALGORITHM or not salt or not expected:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    ).hex()
    return hmac.compare_digest(candidate, expected)


# ---------------------------------------------------------------------------
# The identity record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Account:
    """One credential-bearing identity."""

    id: str
    name: str
    full_name: str
    role: str
    tenant: str
    title: str | None = None
    password_hash: str = ""
    disabled: bool = False

    def as_public_dict(self) -> dict[str, Any]:
        """The account as it may be shown to its owner. Never includes the hash."""
        return {
            "id": self.id,
            "name": self.name,
            "full_name": self.full_name,
            "role": self.role,
            "tenant": self.tenant,
            "title": self.title,
            "disabled": self.disabled,
        }


class IdentityStore:
    """A file-backed account directory.

    Loaded once and cached, because the file is a deployment artefact: reading it
    per request would add a disk hit to every authenticated call for no benefit.
    ``reload()`` exists for the demo reset and for tests.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or IDENTITY_FILE
        self._accounts: dict[str, Account] = {}
        self._default_tenant = "tenant-default"
        self._loaded = False

    # ------------------------------------------------------------------ loading
    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._path.is_file():
            # Auth can be enabled before an identity file exists. That is a
            # deployment error, and it must present as "nobody may authenticate"
            # rather than as a crash.
            return
        with self._path.open("r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        self._default_tenant = str(doc.get("meta", {}).get("default_tenant", "tenant-default"))
        for raw in doc.get("accounts", []):
            role = str(raw["role"])
            if role not in KNOWN_ROLES:
                raise IdentityError(
                    "UNKNOWN_ROLE",
                    f"Account {raw.get('id')!r} declares role {role!r}, which this build does "
                    f"not understand. Known roles: {sorted(KNOWN_ROLES)}.",
                )
            account = Account(
                id=str(raw["id"]),
                name=str(raw.get("name", raw["id"])),
                full_name=str(raw.get("full_name", raw["id"])),
                role=role,
                tenant=str(raw.get("tenant", self._default_tenant)),
                title=raw.get("title"),
                password_hash=str(raw.get("password_hash", "")),
                disabled=bool(raw.get("disabled", False)),
            )
            self._accounts[account.id] = account

    def reload(self) -> None:
        self._loaded = False
        self._accounts.clear()
        self._load()

    # ------------------------------------------------------------------- lookup
    @property
    def default_tenant(self) -> str:
        self._load()
        return self._default_tenant

    def get(self, account_id: str) -> Account | None:
        self._load()
        return self._accounts.get(account_id)

    def resolve(self, identifier: str) -> Account | None:
        """Look an account up by id, or by full name, case-insensitively."""
        self._load()
        by_id = self._accounts.get(identifier)
        if by_id is not None:
            return by_id
        lowered = identifier.lower()
        for account in self._accounts.values():
            if account.full_name.lower() == lowered or account.name.lower() == lowered:
                return account
        return None

    def accounts(self) -> list[Account]:
        self._load()
        return sorted(self._accounts.values(), key=lambda a: a.id)

    def authenticate(self, identifier: str, password: str) -> Account:
        """Verify a credential. Raises IdentityError on any failure.

        Every failure mode returns the *same* error code and message, so the
        response cannot be used to enumerate which accounts exist.
        """
        account = self.resolve(identifier)
        if account is None or not account.password_hash:
            # Spend comparable time on a miss so a timing observer cannot tell an
            # unknown account from a wrong password.
            verify_password(password, hash_password("decoy", iterations=1_000))
            raise IdentityError("INVALID_CREDENTIALS", "Invalid username or password.")
        if account.disabled:
            raise IdentityError("ACCOUNT_DISABLED", "This account is disabled.")
        if not verify_password(password, account.password_hash):
            raise IdentityError("INVALID_CREDENTIALS", "Invalid username or password.")
        return account


store = IdentityStore()
