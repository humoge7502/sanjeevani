# 0013 — SQLite is a mirror of the ledger, never a second source of truth

**Status:** Accepted
**Date:** 2026-10-25

## Context

Three concrete problems with the append-only JSONL ledger, all of which get worse
the longer the process runs:

1. **Unbounded reads.** `GET /api/audit` returned **every** record, on every call.
   At ~2 KB per record, a ledger that has survived months of deployment returns
   hundreds of megabytes, and the JSON was serialised while holding the ledger
   lock. An audit read that can exhaust a worker is an availability problem caused
   by the audit trail itself.
2. **No query surface.** "Every refusal in the last hour" means loading the whole
   file and filtering in Python.
3. **Silent durability gaps.** The append swallowed every `OSError`, so a
   read-only filesystem lost audit records with no signal beyond
   "the directory is not writable".

The architecture document already named the right seam: *`DecisionLedger._write` is
the single persistence seam.* This ADR is about what to put behind it without
weakening what is already there.

## Decision

**Add a crash-safe, queryable SQLite mirror behind the seam, with three rules that
keep the JSONL ledger authoritative — and make durability observable.**

Rule 1 — **The mirror is never read to decide what happened.** `/api/audit` serves
the JSONL ledger. `/api/audit/index` serves the mirror and says so in its response.
The hash chain covers the JSONL records and nothing else.

Rule 2 — **A disagreement is reported, not reconciled.** `mirror.consistency(n)`
compares counts and `/api/health` surfaces `mirror_consistency`. The JSONL ledger
wins, and the response states the resolution. Silently preferring one is how a
mirror becomes a second truth.

Rule 3 — **The mirror is never on the critical path.** A failed mirror write is
counted in `write_failures`, logged, and surfaced in health. It cannot fail an
approval. `test_a_mirror_write_failure_is_counted_not_raised` asserts that by
destroying the table mid-test.

Alongside it, three smaller changes to the same seam:

| Change | Why |
|---|---|
| `GET /api/audit` is **paginated** (`limit`/`offset`/`stage`, capped by `audit.page_size_max`) | Bounded response, bounded lock hold, and `total` still reported so the bound hides nothing. |
| `SQLite` runs in **WAL** mode | A reader (operator, SIEM exporter) can query while the process keeps appending — the entire point of a mirror. |
| Durability is **configurable**: `none` \| `flush` \| `fsync` | A demo and an audit trail want different trade-offs. A typo (`flsuh`) is a **startup failure**, because a silently weaker audit trail is worse than not starting. |
| `ledger.write_failures` and `last_write_error` are on `/api/health` | A record that failed to persist is a silent audit hole. Swallowing the `OSError` was the original bug. |

## Why a mirror and not a migration

**A database would be a better store.** It would also be a second place where
history is defined. The existing guarantee is that the audit trail is a flat,
append-only, hash-chained file that a reviewer can `cat` — and the ledger's own
docstring is explicit that it is *not* a blockchain and does not resist a writer
with filesystem access. Moving the source of truth into SQLite would silently
upgrade a claim about a file into a claim about a database, which nobody has
verified.

Keeping SQLite as a derivative preserves every existing property and adds the
query surface. If the two ever diverge, the file is right and the mirror is
rebuildable by replaying it.

## Alternatives considered

**Return to the unbounded `/api/audit` and tell people to page client-side.**
Rejected: the cost is server-side, and a client cannot bound it.

**Replace JSONL with SQLite entirely.** Rejected for the reason above. Also: the
hash chain's value depends on it being obvious what is covered.

**Postgres instead of SQLite.** Better for a multi-replica deployment, and the
wrong shape here — this build is deliberately one process (see
[deployment.md](../deployment.md)), and a mirror that needs a server is a mirror
that will be off in the demo. `sqlite3` ships with Python and needs nothing.

**Make the mirror mandatory.** Rejected: it is off by default so the documented
file-only story stays exactly as documented.

## Consequences

**Good.** `/api/audit` is bounded. `/api/audit/index` answers indexed, filtered,
paginated questions. Durability is a stated trade-off instead of an accident. And
the ledger's write failures are finally visible: `test_ledger_records_write_failures_instead_of_swallowing_them`
produces a real `OSError` and asserts it is counted.

**Bad, and admitted.** The mirror is per-process and local. Two replicas would each
have their own, which is a property of the single-process decision rather than of
this module. There is no retention policy, no compaction and no archival tier; a
production deployment needs all three, and the JSONL file grows without bound.

**Also bad.** `INSERT OR REPLACE` makes a re-mirrored `seq` idempotent, which is
what a replay needs — but it also means a mirror row *can* be overwritten, which is
the one property the ledger deliberately does not have. That asymmetry is exactly
why the mirror is not the source of truth.

## Enforcement

- `tests/unit/test_persistence.py` — 14 tests: WAL mode, idempotent re-mirroring,
  filtered/paginated queries, `reset` never touching the JSONL file, write failures
  counted not raised, consistency reporting a disagreement, and the durability typo
  failing startup.
- `tests/unit/test_runtime_isolation.py` — unchanged, and still fails the build if
  the test-suite runtime redirect is removed.
