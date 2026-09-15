# 0009 — The ledger rehydrates from disk on start, and `reset()` wins over it

**Status:** Accepted
**Date:** 2026-09-15
**Supersedes nothing.** Extends [0005](0005-tamper-evident-not-tamper-proof-ledger.md).

## Context

The ledger was in-memory with an append-only JSONL mirror on disk. Nothing ever
read that file back. The in-memory list was the only thing `records()`,
`timeline()` and `verify_chain()` looked at.

That is invisible in local development, because the process only starts once. It
is a real defect the moment the system is deployed, which is when this was found:
running the image with the ledger on a mounted volume and restarting it showed
`/api/health` reporting **`records: 0`** against a file that contained 32 records.

Two distinct problems, and the second is the serious one:

1. **History became invisible.** The audit trail existed on disk but every
   read endpoint reported an empty ledger, so the command center showed no
   decisions after a restart.

2. **The chain broke.** `append()` sets
   `prev_hash = self._records[-1].hash if self._records else "GENESIS"`. With an
   empty in-memory list, the first record after a restart chained from `GENESIS`
   while the file already contained a chain ending somewhere else, and the next
   record's `seq` restarted at 1. The resulting file held two disconnected chains
   with duplicate sequence numbers — in a file whose entire claim to value is
   **continuity and order**.

Item 2 is why this could not be left as a documented limitation. A hash chain
that silently restarts is not a weaker guarantee than a hash chain; it is a
*false* one, and it would be discovered by a judge asking to see the ledger after
a redeploy.

## Decision

`DecisionLedger` rehydrates from its file lazily, on first read or append.
Loaded records keep their `seq`, so the sequence continues, and `append()` chains
from the last persisted hash, so the chain stays continuous.

Three supporting decisions:

- **Lazy, not `__init__`.** The module-level singleton is constructed at import
  time, which happens in `--help` paths, tests and CLI tools that never touch the
  ledger. Loading eagerly would make every one of those do file I/O.

- **`reset()` establishes the baseline and blocks rehydration.** `reset()` sets an
  internal `_loaded` flag. Without it, `reset()` followed by `append()` would read
  the just-cleared file straight back in — or, worse, a `reset(write_file=False)`
  used by the test fixtures would resurrect records from a previous run. A
  consumer that has explicitly reset has stated what its baseline is, and the
  ledger does not second-guess that.

- **Malformed lines are counted and skipped, never fatal.** A process killed
  mid-append leaves a truncated final line. Refusing to start on that would
  convert a benign crash into an outage. The count surfaces as
  `skipped_malformed_lines` on `/api/health`, so the truncation is visible rather
  than hidden — silence is the actual failure mode to avoid.

`/api/health` now reports `rehydrated_from_disk` and `skipped_malformed_lines`,
so an operator can distinguish "fresh ledger" from "ledger restored from a
mounted volume" without reading the file.

## Consequences

**Good.** The audit trail survives a restart, and `seq` and `prev_hash` remain
continuous across it. The tamper-evidence claim in 0005 is now true in a deployed
container and not only in a single long-lived process.

**Good.** A truncated tail is tolerated and reported.

**Bad — this is still not durability.** Rehydration restores the *application's*
view. It does not protect the file: an actor with filesystem access can rewrite
the whole file and recompute every hash, and rehydration will faithfully load the
forged chain. This is the same limitation 0005 already names, and it is unchanged
by this decision. Real tamper resistance needs append-only storage plus external
anchoring — still a production requirement, still not claimed.

**Bad — one process only.** Rehydration assumes a single writer. Two processes
sharing the ledger file would interleave appends and produce two records with the
same `seq`, because sequence assignment is in-memory. This is why
[the image runs a single worker](../deployment.md#why-one-process-only): the
constraint is real and is documented rather than discovered.

**Bad — a long-lived deployment grows the file without bound.** Every read
rehydrates the whole thing into memory. Fine at demo scale (tens to thousands of
records); a production ledger needs rotation or a database, since the current
design reads the entire history to answer any question about it.

## Alternatives considered

**Load at import time in `__init__`.** Rejected: forces file I/O on every process
that imports the module, including `--help` and the test suite.

**Do not rehydrate; treat each process as a fresh ledger and rotate the filename
per start.** Rejected: makes the audit surface a per-process artifact rather than
a system record, and destroys the ability to ask "what has this system decided?"
across restarts — which is the point of an audit ledger.

**Rehydrate and verify the chain at startup, refusing to start if it is broken.**
Rejected as the *default*: a demo must not be unable to start because of a
pre-existing file, and `reset` is the tool for that. Verification remains
available on demand via `verify_chain()`, which the audit view reports.

## Revisit if

The ledger moves to a database, to append-only object storage, or to more than one
process. Any of those changes removes the constraints above — and if a second
process ever appears, in-memory sequence assignment must be replaced with
something atomic first.
