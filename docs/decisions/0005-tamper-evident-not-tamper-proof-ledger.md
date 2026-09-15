# 0005 — Hash-chained audit ledger: tamper-evident, not tamper-proof

**Status:** Accepted, **with a named production requirement**
**Date:** 2026-09-15

## Context

The product claims that every consequential decision is traceable. That claim is
worth nothing if the record can be quietly edited after the fact.

Three levels of assurance were available, at very different costs:

1. **A log file.** Append-only by convention. Anyone with disk access changes it
   and nothing notices.
2. **A hash-chained append-only log.** Edits break the chain and are detected on
   verification.
3. **Immutable storage or external anchoring.** WORM media, append-only database
   with enforced permissions, or publishing a periodic root hash to an external
   witness.

Level 3 is what a regulated production deployment actually needs. It is also
infrastructure this build does not have.

## Decision

**Level 2: an append-only ledger where each record is SHA-256 chained to its
predecessor, with no mutation API.**

Record shape:

```
seq · timestamp · stage · actor · action · result · metadata · correlation_id · prev_hash · hash
```

Guarantees:

- **The chain detects an edit.** Changing any record's body invalidates its hash
  and every subsequent link.
- **No mutation surface exists.** The ledger object exposes no `update`, `delete`,
  `pop`, `clear_record` or `rewrite`.
- **Verification is a first-class operation.** `verify_chain()` is the same
  function the UI calls, so what the judge sees is what the code computes.
- **The chain is itself reproducible.** Two identical runs produce an identical
  chain of hashes.
- **41 records across 19 declared stages** for a completed hero run, with
  `stage_coverage()` reporting any stage that did not occur.

## The limitation, stated in the product rather than the footnotes

**A writer with filesystem access can recompute the entire chain.** Changing a
record and re-hashing every subsequent link produces a ledger that verifies
cleanly. Level 2 detects *accidental or casual* tampering; it does not defend
against a determined insider with disk access.

This is not buried. It travels with the verification result itself:

```python
ledger.verify_chain()["scope"]
# "Detects edits to the ledger body. Does NOT protect against an actor with
#  filesystem write access who rewrites and re-hashes the file."
```

That string is returned by the same call the UI makes, asserted by
`test_ledger_declares_its_own_limitations`, exposed at `GET /api/state` under
`ledger.chain`, and rendered in the audit view. A reader of the interface sees
both the verdict and its limits.

A security property that only holds conditionally must state its condition where
the user can see it. Otherwise the interface is making a promise the code does not
keep.

## Alternatives considered

**Cryptographic signing with a key held by the application.** Rejected as security
theatre at this scope. A key the application itself holds does not raise the bar
against the insider threat that matters — the attacker has the application. It
would add complexity and imply a guarantee it does not provide. This is the
"do not over-engineer cryptography merely for appearance" case.

**A full blockchain / Merkle anchoring to an external service.** Rejected for the
MVP specifically because of
[ADR 0006](0006-offline-first-seeded-data.md): an external anchoring service is a
network dependency in the demo path. It is the right *production* answer and the
wrong MVP one.

**A database with an append-only trigger.** Rejected. It would add a runtime
dependency (a database server) to a demo that currently has none, for a guarantee
that is still defeatable by someone with database superuser access.

**No ledger; derive history from application logs.** Rejected. Logs are not a
contract, get rotated, and cannot be verified.

## Consequences

**Good.** The chain is verifiable in-process, in the demo, with no infrastructure.
`test_ledger_is_append_only_and_chained`, `test_ledger_detects_tampering_with_a_record_body`,
`test_a12_ledger_tampering_is_detected`, `test_a12b_ledger_has_no_mutation_surface`,
`test_ledger_hash_chain_survives_a_full_run`,
`test_ledger_is_chronological_with_contiguous_sequence`,
`test_two_full_runs_produce_the_identical_ledger_hash_chain`.

**Bad, and admitted.** The insider-with-disk-access threat is not mitigated. It is
detected only if the attacker does not also recompute the chain. This is the
single most important caveat in this document.

**Also bad.** The ledger file grows monotonically. A completed run writes 41
records; repeated runs without a reset accumulated to 329 KB of API response
during measurement. Bounded today by the reset control, and recorded as a known
limit in [performance-budget.md](../performance-budget.md) and as D5 in the
[threat model](../security-threat-model.md).

**Also bad.** Records carry the rulebook version but not the application commit.
Two decisions made by different builds are therefore indistinguishable from the
ledger alone.

## Enforcement

```bash
python -m pytest -k "ledger" -v
```

## Revisiting this

Level 3 is the production requirement, and the migration is:

1. **Append-only storage** — WORM object storage, or a database with an enforced
   append-only role, so the application cannot rewrite history even if compromised.
2. **External anchoring** — publish the head hash (or a Merkle root) to a separate
   trust domain on a schedule, so recomputation is detectable.
3. **Build stamping** — record the application commit in every record.
4. **Retention policy** — rotation with chain continuity preserved across segments.

Items 1–3 are recorded as production requirements in
[mock-boundaries.md](../mock-boundaries.md). Until then, the honest description of
this ledger is **tamper-evident**, and that word is used everywhere rather than
"tamper-proof".
