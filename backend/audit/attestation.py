"""Ledger-head attestation — closing the gap ADR 0005 admits in writing.

The hash chain makes the ledger **tamper-evident**: edit one record and the
chain breaks. What it cannot do alone is prove *when* a head was valid — an
attacker with filesystem access can rewrite the ledger AND recompute every
hash, producing a chain that verifies intact against a fabricated history. The
honest name for that limit is in ADR 0005, and the classic answer is external
anchoring: hand the head hash to something the attacker does not control.

WHAT THIS IS. The local form of that answer. A small, dependency-free HMAC
attestation: ``mac = HMAC-SHA256(key, canonical(head_hash, length, timestamp,
key_id))`` over the ledger's verified head. Signed by a key that is NOT on the
ledger volume, an attestation file is evidence the head existed, with this
content, at that time — a rewrite cannot forge it without stealing the key.

WHAT THIS IS NOT. Not a timestamp authority, and not a substitute for the real
external anchor. The ``kind`` field is a deliberate seam: swapping the local
HMAC for an RFC-3161 timestamp or a public blockchain anchor is a new ``kind``
value, not a redesign. Until one is wired in, the attestation is as strong as
the key's storage — which is a statement the threat model makes explicitly.

HOW IT IS USED. ``scripts/attest_ledger.py attest`` after a demo or on a cron
schedule; ``verify`` against the live ledger head, or against an explicit
``--head``/``--length`` pair when the ledger is not available. The signature
covers the head hash and the record count, so an attestation is only valid for
an *unchanged* chain prefix — appending is allowed (a longer chain still starts
with the attested head), truncating or editing is not.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

ATTESTATION_VERSION = "1.0.0"
KIND_HMAC_SHA256 = "hmac-sha256-head-v1"


class AttestationError(RuntimeError):
    """The attestation does not match the ledger it claims to describe."""


def _canonical(payload: dict[str, Any]) -> str:
    """Byte-stable serialisation: the mac is defined over exactly these bytes."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def attest_head(
    *,
    head_hash: str,
    length: int,
    key: str,
    key_id: str = "primary",
    created_at: str,
) -> dict[str, Any]:
    """Sign one ledger head. Pure: same inputs, byte-identical attestation."""
    if not key:
        raise AttestationError(
            "an attestation key is required; set SANJEEVANI_ATTESTATION_KEY "
            "(or pass --key-env) — an unkeyed attestation attests nothing"
        )
    if not head_hash:
        raise AttestationError("the ledger has no head; nothing to attest")
    if length < 0:
        raise AttestationError(f"length must be >= 0, got {length}")
    payload = {
        "attestation_version": ATTESTATION_VERSION,
        "kind": KIND_HMAC_SHA256,
        "head_hash": head_hash,
        "length": int(length),
        "key_id": key_id,
        "created_at": created_at,
    }
    mac = hmac.new(key.encode("utf-8"), _canonical(payload).encode("utf-8"), hashlib.sha256)
    return {**payload, "mac": mac.hexdigest()}


def verify_attestation(
    attestation: dict[str, Any],
    *,
    key: str,
    head_hash: str,
    length: int,
) -> None:
    """Confirm the attestation describes exactly this ledger state, or raise.

    Appending is compatible: an attestation covers a *prefix*, so verify
    ``head_hash`` against the chain's record at position ``length - 1`` when the
    ledger has grown. ``scripts/attest_ledger.py verify`` does that unwinding;
    this function stays the pure pairwise check.
    """
    if not key:
        raise AttestationError("a verification key is required")
    expected = {
        "attestation_version": ATTESTATION_VERSION,
        "kind": KIND_HMAC_SHA256,
        "head_hash": head_hash,
        "length": int(length),
        "key_id": str(attestation.get("key_id") or "primary"),
        "created_at": str(attestation.get("created_at") or ""),
    }
    for field in ("head_hash", "length"):
        if str(attestation.get(field)) != str(expected[field]):
            raise AttestationError(
                f"attestation {field} ({attestation.get(field)!r}) does not match "
                f"the ledger ({expected[field]!r})"
            )
    if attestation.get("kind") != KIND_HMAC_SHA256:
        raise AttestationError(f"unknown attestation kind: {attestation.get('kind')!r}")
    mac = hmac.new(
        key.encode("utf-8"), _canonical(expected).encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(mac, str(attestation.get("mac") or "")):
        raise AttestationError(
            "attestation mac does not verify: the file was edited, or it was "
            "signed with a different key"
        )
