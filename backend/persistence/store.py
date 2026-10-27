"""Durable persistence: a SQLite mirror of the append-only audit ledger.

WHY THIS EXISTS. The architecture names one persistence seam: ``DecisionLedger._write``.
The JSONL file behind it is honest but limited -- reading the whole ledger to answer
"every refusal in the last hour" is O(n), a crash mid-append leaves a truncated
line, and there is no way to query across records without loading them.

This module is a **mirror**, not a replacement. The JSONL ledger remains the
append-only source of truth and the thing the hash chain covers; SQLite is a
queryable, crash-safe copy that an operator or a SIEM can read. Two properties
follow from that choice, and they are the reason it is a mirror:

  * **The hash chain is untouched.** SQLite cannot become a second place where
    history is defined, because it is never read to answer "what happened" -- only
    to answer "find me the rows that match". If the two ever disagree, the JSONL
    ledger wins and ``consistency()`` says so.
  * **Failure is not fatal.** A mirror write that fails is counted and surfaced in
    ``/api/health``; it never breaks the request path and never blocks an approval.

Standard library only: ``sqlite3`` ships with Python, needs no server, and is the
right shape for the single-process deployment this build deliberately has (see
docs/deployment.md). Moving to Postgres is a driver swap behind the same methods.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config import RUNTIME_DIR, ensure_runtime_dir, settings

logger = logging.getLogger("sanjeevani.persistence")

MIRROR_FILENAME = "audit_mirror.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_records (
    seq         INTEGER PRIMARY KEY,
    stage       TEXT    NOT NULL,
    actor       TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    result      TEXT    NOT NULL,
    tenant      TEXT,
    correlation TEXT,
    timestamp   TEXT    NOT NULL,
    prev_hash   TEXT,
    hash        TEXT,
    body        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_stage  ON ledger_records(stage);
CREATE INDEX IF NOT EXISTS idx_records_ts     ON ledger_records(timestamp);
CREATE INDEX IF NOT EXISTS idx_records_tenant ON ledger_records(tenant);
CREATE INDEX IF NOT EXISTS idx_records_result ON ledger_records(result);
"""


@dataclass
class MirrorStatus:
    enabled: bool
    backend: str
    path: str
    records: int
    write_failures: int
    last_error: str | None
    journal_mode: str | None
    schema_version: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "backend": self.backend,
            "path": self.path,
            "records": self.records,
            "write_failures": self.write_failures,
            "last_error": self.last_error,
            "journal_mode": self.journal_mode,
            "schema_version": self.schema_version,
            "note": (
                "A queryable MIRROR of the append-only JSONL ledger. The JSONL file and "
                "its hash chain remain the source of truth; this is never read to decide "
                "what happened."
            ),
        }


class SqliteAuditMirror:
    """A crash-safe, queryable copy of the decision ledger."""

    SCHEMA_VERSION = 1

    def __init__(self, path: Path | None = None, *, enabled: bool | None = None) -> None:
        cfg = settings().get("persistence", {})
        # An explicit `enabled` wins (tests, and callers that want a deliberate
        # choice). Otherwise it is resolved live from config on every access, so
        # toggling `persistence.sqlite_mirror` and calling `reload_config()` takes
        # effect -- the same rule every other knob in this project follows.
        self._forced_enabled = enabled
        configured = cfg.get("mirror_path") or MIRROR_FILENAME
        self._path = path or (RUNTIME_DIR / Path(str(configured)).name)
        self._lock = threading.RLock()
        self.write_failures = 0
        self.last_error: str | None = None
        self._journal_mode: str | None = None
        self._connection: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ plumbing
    @property
    def enabled(self) -> bool:
        if self._forced_enabled is not None:
            return self._forced_enabled
        return bool(settings().get("persistence", {}).get("sqlite_mirror", False))

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        ensure_runtime_dir()
        connection = sqlite3.connect(self._path, check_same_thread=False)
        # WAL lets a reader (an operator, a SIEM exporter) query while the process
        # keeps appending, which is the whole point of having a mirror.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        # PRAGMA accepts no bound parameters, so the statement is static text --
        # and the guard below fails loudly if the constant and the text drift.
        connection.execute("PRAGMA user_version=1")
        stored_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if stored_version != int(self.SCHEMA_VERSION):
            raise RuntimeError(
                "mirror schema drift: PRAGMA user_version text says "
                f"{stored_version}, SCHEMA_VERSION says {self.SCHEMA_VERSION}"
            )
        connection.executescript(SCHEMA)
        connection.commit()
        self._journal_mode = str(
            connection.execute("PRAGMA journal_mode").fetchone()[0]
        )
        self._connection = connection
        return connection

    def reset(self) -> None:
        """Drop mirror contents (demo reset). Never touches the JSONL ledger."""
        with self._lock:
            if not self.enabled:
                return
            try:
                connection = self._connect()
                connection.execute("DELETE FROM ledger_records")
                connection.commit()
            except sqlite3.Error as exc:  # pragma: no cover - defensive
                self._record_failure(exc)

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _record_failure(self, exc: Exception) -> None:
        self.write_failures += 1
        self.last_error = f"{type(exc).__name__}: {exc}"
        # Logged, not raised: a mirror is not on the critical path.
        logger.warning("audit mirror write failed: %s", self.last_error)

    # ------------------------------------------------------------------ writing
    def append(self, record: dict[str, Any]) -> None:
        """Mirror one ledger record. Best-effort by design."""
        if not self.enabled:
            return
        with self._lock:
            try:
                connection = self._connect()
                ids = record.get("ids") or {}
                connection.execute(
                    """
                    INSERT OR REPLACE INTO ledger_records
                        (seq, stage, actor, action, result, tenant, correlation,
                         timestamp, prev_hash, hash, body)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(record["seq"]),
                        str(record.get("stage", "")),
                        str(record.get("actor", "")),
                        str(record.get("action", "")),
                        str(record.get("result", "OK")),
                        ids.get("tenant"),
                        ids.get("correlation_id") or ids.get("plan_id"),
                        str(record.get("timestamp", "")),
                        str(record.get("prev_hash", "")),
                        str(record.get("hash", "")),
                        json.dumps(record.get("detail") or {}, sort_keys=True),
                    ),
                )
                connection.commit()
            except (sqlite3.Error, KeyError, TypeError, ValueError) as exc:
                self._record_failure(exc)

    # ------------------------------------------------------------------ reading
    def count(self) -> int:
        if not self.enabled:
            return 0
        with self._lock:
            try:
                row = self._connect().execute(
                    "SELECT COUNT(*) FROM ledger_records"
                ).fetchone()
                return int(row[0]) if row else 0
            except sqlite3.Error as exc:
                self._record_failure(exc)
                return 0

    def query(
        self,
        *,
        stage: str | None = None,
        result: str | None = None,
        tenant: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Indexed, parameterised query. This is what the JSONL file cannot do."""
        if not self.enabled:
            return {"records": [], "total": 0, "enabled": False}
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("stage", stage), ("result", result), ("tenant", tenant)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            try:
                connection = self._connect()
                total = int(
                    connection.execute(
                        f"SELECT COUNT(*) FROM ledger_records {where}", params  # noqa: S608 - column names are a fixed allow-list, values are parameterised
                    ).fetchone()[0]
                )
                rows = connection.execute(
                    f"SELECT seq, stage, actor, action, result, timestamp, hash, body "  # noqa: S608
                    f"FROM ledger_records {where} ORDER BY seq DESC LIMIT ? OFFSET ?",
                    [*params, max(1, min(limit, 1000)), max(0, offset)],
                ).fetchall()
            except sqlite3.Error as exc:
                self._record_failure(exc)
                return {"records": [], "total": 0, "enabled": True, "error": self.last_error}
        return {
            "enabled": True,
            "total": total,
            "returned": len(rows),
            "records": [
                {
                    "seq": row[0],
                    "stage": row[1],
                    "actor": row[2],
                    "action": row[3],
                    "result": row[4],
                    "timestamp": row[5],
                    "hash": row[6],
                    "detail": json.loads(row[7]) if row[7] else {},
                }
                for row in rows
            ],
            "note": "Read from the SQLite MIRROR. The JSONL ledger remains the source of truth.",
        }

    def status(self) -> MirrorStatus:
        return MirrorStatus(
            enabled=self.enabled,
            backend="sqlite" if self.enabled else "none",
            path=str(self._path),
            records=self.count(),
            write_failures=self.write_failures,
            last_error=self.last_error,
            journal_mode=self._journal_mode,
            schema_version=self.SCHEMA_VERSION,
        )

    def consistency(self, ledger_record_count: int) -> dict[str, Any]:
        """Compare the mirror against the JSONL ledger, which always wins.

        A disagreement is reported rather than reconciled silently: the whole
        point of naming one source of truth is that the other one can be wrong
        out loud.
        """
        if not self.enabled:
            return {"enabled": False, "consistent": None}
        mirrored = self.count()
        return {
            "enabled": True,
            "ledger_records": ledger_record_count,
            "mirror_records": mirrored,
            "consistent": mirrored == ledger_record_count,
            "resolution": (
                "The JSONL ledger is authoritative. Rebuild the mirror by replaying it."
            ),
        }


mirror = SqliteAuditMirror()


def reset_mirror() -> None:
    """Used by the demo reset and by tests."""
    mirror.reset()


def mirror_status() -> dict[str, Any]:
    return mirror.status().as_dict()
