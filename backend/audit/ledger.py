"""M3 / A6 -- Append-only decision ledger.

The ledger IS the memory of the system: it records *decisions*, not just data.

Immutability in practice, not in marketing:
  * Application code exposes `append()` and read methods. There is no update or
    delete method, and no code path rewrites a record. That is the primary
    control: you cannot rewrite history you have no API to rewrite.
  * Each record carries `prev_hash` and `hash`, forming a SHA-256 chain over the
    canonical record body. `verify_chain()` recomputes it, so an out-of-band edit
    to the JSONL file is *detectable*.
  * What this is NOT: it is not a blockchain, not a WORM store, and not evidence
    of authorship. A writer with filesystem access can rewrite the whole file and
    recompute every hash. Real tamper resistance needs append-only storage and
    external anchoring -- that is a production requirement, stated honestly in
    docs/security-threat-model.md rather than implied by a hash column.

Determinism: the scenario clock is frozen in demo mode, so a full demo run
produces an identical hash chain every time. That is what makes
"reset and replay" verifiable rather than merely asserted.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from backend.config import RUNTIME_DIR, ensure_runtime_dir, isoformat, scenario_clock, settings

LEDGER_FILENAME = "audit_ledger.jsonl"

# The canonical loop stages, in order. The UI renders exactly this sequence.
STAGE_SEQUENCE = [
    "SIGNAL_RECEIVED",
    "SIGNAL_NORMALIZED",
    "EVENT_CLASSIFIED",
    "EVENT_VERIFIED",
    "SCENARIO_GENERATED",
    "NETWORK_IMPACT_CALCULATED",
    "RECOVERY_PLAN_RECEIVED",
    "POLICY_EVALUATED",
    "COMPLIANCE_EVALUATED",
    "APPROVAL_REQUESTED",
    "APPROVAL_DECIDED",
    "EXECUTION_STARTED",
    "IBP_EXECUTED",
    "TM_EXECUTED",
    "ARIBA_EXECUTED",
    "EXECUTION_RECEIPTED",
    "OUTCOME_RECORDED",
    "LEARNING_SIGNAL_GENERATED",
    "LOOP_CLOSED",
]


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass
class LedgerRecord:
    seq: int
    stage: str
    actor: str
    action: str
    result: str
    ids: dict[str, str]
    detail: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    prev_hash: str = ""
    hash: str = ""

    def body(self) -> dict[str, Any]:
        """Everything the hash covers, excluding the hash fields themselves."""
        return {
            "seq": self.seq,
            "stage": self.stage,
            "actor": self.actor,
            "action": self.action,
            "result": self.result,
            "ids": self.ids,
            "detail": self.detail,
            "timestamp": self.timestamp,
            "prev_hash": self.prev_hash,
        }

    def compute_hash(self) -> str:
        return hashlib.sha256(_canonical(self.body()).encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {**self.body(), "hash": self.hash}


class DecisionLedger:
    def __init__(self, path=None) -> None:
        self._records: list[LedgerRecord] = []
        self._lock = threading.RLock()
        self._path = path
        self.chain_enabled = bool(settings()["audit"]["hash_chain"])
        # Rehydration bookkeeping. `_loaded` means "the in-memory list is the
        # authoritative baseline for this process" -- set by the first lazy read
        # or by an explicit reset(), whichever happens first.
        self._loaded = False
        self.rehydrated = False
        self.skipped_lines = 0

    # ------------------------------------------------------------------ paths
    @property
    def path(self):
        if self._path is not None:
            return self._path
        configured = settings()["audit"]["ledger_path"]
        return RUNTIME_DIR / configured.split("/")[-1]

    # -------------------------------------------------------------- rehydrate
    def _ensure_loaded(self) -> None:
        """Read an existing ledger file back into memory, once.

        WHY THIS EXISTS. The ledger is the audit memory of the system, but the
        process is not. Without this, a container restart left the file holding
        a chain while the in-memory list was empty, which broke the tamper-evident
        claim in two ways: `/api/audit` reported zero records even though the file
        had them, and the next `append()` chained its `prev_hash` from `GENESIS`
        while the file already contained a different chain head. The result was a
        discontinuous chain in a file whose whole purpose is continuity.

        Correct sequencing: loaded records keep their `seq`, so the next append
        continues the sequence, and `prev_hash` links to the last persisted hash.

        A half-written trailing line (the process died mid-append) is normal and
        MUST NOT stop startup: unparseable lines are counted and skipped rather
        than raised, because refusing to boot on a truncated last line would turn
        a benign crash into an outage -- and the truncation is itself visible in
        `skipped_lines` on `/api/health`.
        """
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            path = self.path
            try:
                if not path.is_file():
                    return
                raw = path.read_text(encoding="utf-8")
            except OSError:
                # Unreadable is not fatal: fall back to an empty in-memory ledger,
                # exactly as the process behaved before rehydration existed.
                return
            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                    self._records.append(
                        LedgerRecord(
                            seq=int(payload["seq"]),
                            stage=str(payload["stage"]),
                            actor=str(payload["actor"]),
                            action=str(payload["action"]),
                            result=str(payload.get("result", "OK")),
                            ids=dict(payload.get("ids") or {}),
                            detail=dict(payload.get("detail") or {}),
                            timestamp=str(payload.get("timestamp", "")),
                            prev_hash=str(payload.get("prev_hash", "")),
                            hash=str(payload.get("hash", "")),
                        )
                    )
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    self.skipped_lines += 1
            self.rehydrated = bool(self._records)

    def reset(self, write_file: bool = True) -> None:
        """Clear the ledger. Used by the demo reset command.

        An explicit reset establishes the baseline, so it also closes the door on
        lazy rehydration: without this flag, `reset()` followed by `append()`
        would read the old file straight back in and defeat the reset.
        """
        with self._lock:
            self._records.clear()
            self._loaded = True
            self.rehydrated = False
            self.skipped_lines = 0
            if write_file:
                try:
                    ensure_runtime_dir()
                    self.path.write_text("", encoding="utf-8")
                except OSError:
                    # A read-only runtime directory must not break the demo; the
                    # in-memory ledger remains authoritative.
                    pass

    # ------------------------------------------------------------------ append
    def append(
        self,
        stage: str,
        actor: str,
        action: str,
        result: str = "OK",
        ids: dict[str, str] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> LedgerRecord:
        with self._lock:
            self._ensure_loaded()
            record = LedgerRecord(
                seq=len(self._records) + 1,
                stage=stage,
                actor=actor,
                action=action,
                result=result,
                ids=ids or {},
                detail=detail or {},
                timestamp=isoformat(scenario_clock()),
            )
            record.prev_hash = self._records[-1].hash if self._records else "GENESIS"
            record.hash = record.compute_hash() if self.chain_enabled else ""
            self._records.append(record)
            self._write(record)
            return record

    def _write(self, record: LedgerRecord) -> None:
        try:
            ensure_runtime_dir()
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(_canonical(record.as_dict()) + "\n")
        except OSError:
            pass  # durability is best-effort; never break the request path

    # -------------------------------------------------------------------- read
    def __len__(self) -> int:
        self._ensure_loaded()
        return len(self._records)

    def __iter__(self) -> Iterator[LedgerRecord]:
        self._ensure_loaded()
        return iter(list(self._records))

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_loaded()
            return [r.as_dict() for r in self._records]

    def timeline(self) -> list[dict[str, Any]]:
        """Compact chronological view for the audit UI."""
        with self._lock:
            self._ensure_loaded()
            return [
                {
                    "seq": r.seq,
                    "stage": r.stage,
                    "actor": r.actor,
                    "action": r.action,
                    "result": r.result,
                    "timestamp": r.timestamp,
                    "ids": r.ids,
                    "hash": r.hash[:12] if r.hash else "",
                    "prev_hash": r.prev_hash[:12] if r.prev_hash else "",
                }
                for r in self._records
            ]

    def stage_coverage(self) -> dict[str, Any]:
        self._ensure_loaded()
        seen = {r.stage for r in self._records}
        return {
            "stages_expected": STAGE_SEQUENCE,
            "stages_present": [s for s in STAGE_SEQUENCE if s in seen],
            "stages_missing": [s for s in STAGE_SEQUENCE if s not in seen],
            "complete": all(s in seen for s in STAGE_SEQUENCE),
        }

    def health(self) -> dict[str, Any]:
        """Readiness detail for `/api/health`.

        The ledger is the one piece of state that must SURVIVE a container. If
        the configured path is not writable, every append is silently dropped:
        the request path deliberately swallows the OSError so a read-only
        filesystem cannot break a demo, which means nothing else would notice.
        This is how an orchestrator notices.
        """
        writable = False
        try:
            ensure_runtime_dir()
            # Touch the real file rather than testing the directory: a mounted
            # volume can be writable at the mount point and read-only below it.
            with self.path.open("a", encoding="utf-8"):
                pass
            writable = True
        except OSError:
            writable = False
        self._ensure_loaded()
        return {
            "records": len(self._records),
            "hash_chain": self.chain_enabled,
            "path": str(self.path),
            "writable": writable,
            # Observable so an operator can tell "fresh ledger" from "ledger
            # restored from a mounted volume after a restart".
            "rehydrated_from_disk": self.rehydrated,
            "skipped_malformed_lines": self.skipped_lines,
        }

    def verify_chain(self) -> dict[str, Any]:
        """Recompute the chain. Detects out-of-band edits to the ledger body."""
        with self._lock:
            self._ensure_loaded()
            broken: list[dict[str, Any]] = []
            prev = "GENESIS"
            for record in self._records:
                if record.prev_hash != prev:
                    broken.append(
                        {"seq": record.seq, "issue": "prev_hash_mismatch", "expected": prev}
                    )
                recomputed = record.compute_hash()
                if record.hash and recomputed != record.hash:
                    broken.append(
                        {
                            "seq": record.seq,
                            "issue": "hash_mismatch",
                            "expected": record.hash,
                            "recomputed": recomputed,
                        }
                    )
                prev = record.hash or recomputed
            return {
                "records": len(self._records),
                "chain_enabled": self.chain_enabled,
                "intact": not broken,
                "head": self._records[-1].hash if self._records else None,
                "issues": broken,
                "rehydrated_from_disk": self.rehydrated,
                "scope": (
                    "Detects edits to the ledger body. Does NOT protect against an actor "
                    "with filesystem write access who rewrites and re-hashes the file."
                ),
            }


ledger = DecisionLedger()
