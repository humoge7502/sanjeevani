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
        # Rehydration bookkeeping. `_loaded` means "the in-memory list is the
        # authoritative baseline for this process" -- set by the first lazy read
        # or by an explicit reset(), whichever happens first.
        self._loaded = False
        self.rehydrated = False
        self.skipped_lines = 0
        # Durability is observable, not silent. A read-only filesystem used to lose
        # every record with no signal beyond "the directory is not writable".
        self.write_failures = 0
        self.last_write_error: str | None = None
        # Read-path memos. `verify_chain()` recomputes a SHA-256 per record and
        # `timeline()` rebuilds a dict per record, and `/api/state` asks for both
        # on every keystroke the UI makes. Each memo is invalidated by
        # `_invalidate_memos()`, which `append()` and `reset()` both call, so a
        # stale answer is impossible no matter which read comes first.
        self._chain_memo: dict[str, Any] | None = None
        self._coverage_memo: dict[str, Any] | None = None
        self._timeline_memo: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------------ paths
    @property
    def chain_enabled(self) -> bool:
        """Whether the hash chain is on.

        A property, not an attribute set at import time. It used to be read once
        in ``__init__``, which meant toggling ``audit.hash_chain`` and calling
        ``reload_config()`` had no effect -- contradicting the project's own claim
        that every threshold is a live config key.
        """
        return bool(settings()["audit"]["hash_chain"])

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
            self._invalidate_memos()
            if write_file:
                try:
                    ensure_runtime_dir()
                    self.path.write_text("", encoding="utf-8")
                except OSError:
                    # A read-only runtime directory must not break the demo; the
                    # in-memory ledger remains authoritative.
                    pass

    def _invalidate_memos(self) -> None:
        self._chain_memo = None
        self._coverage_memo = None
        self._timeline_memo = None

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
            self._invalidate_memos()
            self._write(record)
            # Mirror AFTER the file write, so the authoritative copy is never
            # behind the copy. A mirror failure is counted, not raised.
            if self._mirror_enabled():
                from backend.persistence import mirror

                mirror.append(record.as_dict())
            return record

    def _mirror_enabled(self) -> bool:
        return bool(settings().get("persistence", {}).get("sqlite_mirror", False))

    def _write(self, record: LedgerRecord) -> None:
        """Append one record to the JSONL file.

        Durability is configurable (``persistence.ledger_durability``) because the
        right trade-off differs between a demo and a deployment:

          * ``none``  -- write and return. A power loss can lose the tail.
          * ``flush`` -- flush the Python buffer to the OS. The default.
          * ``fsync`` -- flush and fsync. Survives a power loss; costs a syscall
                         per record, which is the correct price for an audit trail
                         that has to be provable.

        Failures are counted in ``write_failures`` rather than swallowed in
        silence: a read-only filesystem used to lose every record invisibly, and
        ``/api/health`` could only report the *directory* as unwritable.
        """
        try:
            ensure_runtime_dir()
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(_canonical(record.as_dict()) + "\n")
                self._apply_durability(handle)
        except OSError as exc:
            self.write_failures += 1
            self.last_write_error = f"{type(exc).__name__}: {exc}"

    def _apply_durability(self, handle) -> None:
        mode = str(settings().get("persistence", {}).get("ledger_durability", "flush"))
        if mode == "none":
            return
        handle.flush()
        if mode == "fsync":
            import os

            os.fsync(handle.fileno())

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

    def records_page(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        stage: str | None = None,
    ) -> dict[str, Any]:
        """A bounded page of records, newest first.

        WHY THIS EXISTS. ``records()`` returns the entire ledger. The ledger grows
        monotonically for the life of the deployment, so that endpoint's payload
        grew with it, and the JSON was serialised while holding the ledger lock --
        an audit read that could exhaust a worker. This method is the fix: the
        response is bounded, the expensive work is bounded with it, and the
        caller is told both the page and the true total so nothing is hidden.
        """
        cfg = settings()["audit"]
        page_default = int(cfg.get("page_size_default", 200))
        page_max = int(cfg.get("page_size_max", 1000))
        requested = page_default if limit is None else int(limit)
        effective = max(1, min(requested, page_max))
        clamped = requested > page_max
        start = max(0, int(offset))

        with self._lock:
            self._ensure_loaded()
            filtered = [
                r for r in self._records if stage is None or r.stage == stage
            ]
            total = len(filtered)
            # Newest first: an audit reader almost always wants the latest, and a
            # stable sort over seq keeps the order deterministic.
            ordered = sorted(filtered, key=lambda r: r.seq, reverse=True)
            window = ordered[start : start + effective]
            return {
                "records": [r.as_dict() for r in window],
                "total": total,
                "returned": len(window),
                "offset": start,
                "limit": effective,
                "limit_clamped_to_max": clamped,
                "page_size_max": page_max,
                "order": "seq descending (newest first)",
                "filter": {"stage": stage},
                "has_more": start + len(window) < total,
            }

    def timeline(self) -> list[dict[str, Any]]:
        """Compact chronological view for the audit UI. Memoised until the ledger changes."""
        with self._lock:
            self._ensure_loaded()
            if self._timeline_memo is not None:
                return self._timeline_memo
            self._timeline_memo = [
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
            return self._timeline_memo

    def stage_coverage(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            if self._coverage_memo is not None:
                return self._coverage_memo
            seen = {r.stage for r in self._records}
            self._coverage_memo = {
                "stages_expected": STAGE_SEQUENCE,
                "stages_present": [s for s in STAGE_SEQUENCE if s in seen],
                "stages_missing": [s for s in STAGE_SEQUENCE if s not in seen],
                "complete": all(s in seen for s in STAGE_SEQUENCE),
            }
            return self._coverage_memo

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
        payload = {
            "records": len(self._records),
            "hash_chain": self.chain_enabled,
            "path": str(self.path),
            "writable": writable,
            # Observable so an operator can tell "fresh ledger" from "ledger
            # restored from a mounted volume after a restart".
            "rehydrated_from_disk": self.rehydrated,
            "skipped_malformed_lines": self.skipped_lines,
            # A record that failed to persist is a silent audit hole unless it is
            # reported. The request path swallows the OSError on purpose; this is
            # how anybody finds out it happened.
            "write_failures": self.write_failures,
            "last_write_error": self.last_write_error,
            "durability": str(
                settings().get("persistence", {}).get("ledger_durability", "flush")
            ),
        }
        try:
            from backend.persistence import mirror

            payload["mirror"] = mirror.status().as_dict()
            payload["mirror_consistency"] = mirror.consistency(len(self._records))
        except Exception as exc:  # noqa: BLE001 - health must never fail on a mirror
            payload["mirror"] = {"enabled": False, "error": str(exc)}
        return payload

    def verify_chain(self) -> dict[str, Any]:
        """Recompute the chain, always. Detects edits to the ledger body.

        This is the tamper-evidence *primitive*, so it never returns a cached
        answer: a verification function that can say "intact" without looking is a
        weaker thing than the hash chain it is meant to attest, and the whole
        claim rests on it. The cost is one SHA-256 per record.

        The hot path avoids that cost a different way: ``/api/state`` calls
        ``verify_chain_memoised()``, which is explicitly named so nobody mistakes
        it for this method.
        """
        with self._lock:
            self._ensure_loaded()
            return self._verify_chain_uncached()

    def verify_chain_memoised(self) -> dict[str, Any]:
        """A cached chain verdict, for the polling state endpoint only.

        Safe because the verdict can only change when a record is appended, and
        every append clears the cache. An in-process mutation of
        ``_records`` would not invalidate it -- which is exactly why the audit
        endpoint and the tests use ``verify_chain()`` instead, and why the
        attacker this protects against (someone with filesystem access) is caught
        by rehydration rather than by polling.
        """
        with self._lock:
            self._ensure_loaded()
            if self._chain_memo is not None:
                return {**self._chain_memo, "memoised": True}
            verdict = self._verify_chain_uncached()
            self._chain_memo = verdict
            return {**verdict, "memoised": False}

    def _verify_chain_uncached(self) -> dict[str, Any]:
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
