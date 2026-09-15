# 0006 — Offline-first with file-based seed data

**Status:** Accepted
**Date:** 2026-09-15

## Context

A hackfest demo runs on venue Wi-Fi. Venue Wi-Fi is congested, filtered and
sometimes absent. A demo that streams a network map, fetches a live disruption
feed or calls an external service is a demo that can fail in front of an audience
for reasons unrelated to the engineering — and the failure would be blamed on the
engineering.

There is also a reproducibility argument that outlives the demo. If a run depends
on a live feed, the run is not reproducible.

## Decision

**Every input is a file in the repository. Nothing external is load-bearing.**

| Input | Source | Format |
|---|---|---|
| Network | `data/network/nodes.yaml`, `lanes.yaml` | YAML |
| Products | `data/products/skus.yaml` | YAML |
| Shipments | `data/shipments/shipments.yaml` | YAML |
| Cold-chain telemetry | `data/telemetry/cold_chain.csv` | CSV |
| Disruption signals | `data/events/hero_feeds.yaml` | YAML |
| Outcome observations | `data/events/outcome_observations.yaml` | YAML |
| Policy rulebook | `config/policies.yaml` | YAML |
| Scenarios, tiers, approvers | `config/scenarios.yaml` | YAML |
| Recovery plans | `backend/m2/fixtures/recovery_plans.yaml` | YAML |
| SAP systems | in-process mocks | Python |

The only process the demo needs is the API itself, and even that is optional —
`python scripts/demo.py` runs the entire loop in-process with no server.

Supporting decisions taken because of this one:

- **The default API port is 8787, not 8000.** Port 8000 collides with a striking
  number of local development services; this was discovered during verification
  when the port was already occupied on the build machine. A demo that fails to
  start because of someone else's leftover process is a demo that fails.
- **The frontend proxies to the API through the Vite dev server**, so no CORS
  configuration or absolute URL has to be right for the demo to work.
- **`scripts/health.py` verifies the whole chain** — Python version, dependencies,
  seed file shapes, contract validity, the governance invariant and the full demo
  path — and exits non-zero on any failure. It is the pre-demo check.

## Alternatives considered

**Live signal feeds (news APIs, weather services, port advisories).** Rejected for
the MVP. It would make the demo non-reproducible, introduce an API key, and add a
failure mode with no upside: the seed feed is already shaped like real input,
including the cases that must be *rejected*.

**A database (Postgres, SQLite) for the network and ledger.** Rejected. It adds a
service to start, a schema to migrate and a failure mode to the demo path, in
exchange for capability this dataset does not need. 12 nodes and 41 ledger records
fit in memory without strain. The ledger is appended to a JSONL file.

**A hosted backend or serverless deployment.** Rejected. It converts a local
reliability problem into a network reliability problem — the exact trade this
decision refuses.

**A service worker with an API cache for genuine offline frontend operation.**
Rejected *for now*, though it is the interesting omission. It is real work, and
the seed data already removes the need: the frontend never needs the network
because the API is local. A PWA would protect against the API being down, which is
a different and less likely failure than the venue's Wi-Fi being congested.

## Consequences

**Good.** The demo works with the network cable unplugged.
`test_offline_flag_is_on_and_no_network_is_required` asserts the flag and the
absence of a network requirement. Every number in a run is reproducible from
committed files — `test_reset_then_rerun_is_bit_identical` and
`test_two_full_runs_produce_identical_impact_and_receipt` are only meaningful
because of this. Nothing has to be configured, downloaded or seeded at demo time;
`git clone` and run.

**Bad, and admitted.** The system has no capability to react to something genuinely
new. It demonstrates a *pattern*, not a live service. A judge asking "what happens
when a real disruption happens that isn't in your file?" has a correct answer: the
system does not ingest it. That is a capability gap and the reason a real
deployment needs the ingestion layer this build scopes out.

**Also bad.** YAML is a permissive format and a malformed seed file fails late.
Mitigated by shape validation at load and by `scripts/health.py`, but a
schema-validated format would fail better.

**Also bad.** Data and code are coupled through file paths and field names with no
migration mechanism. A seed-format change is a breaking change made by editing
files.

## Enforcement

```bash
python scripts/health.py                                   # pre-demo verification
python -m pytest -k "offline or reproducible" -v
```

Two standing constraints follow from this decision and apply to any future work:

1. **No dependency may be added that is load-bearing for the demo path.**
2. **No skill, library or integration may require network access at run time.**

Both are recorded in [skills-registry.md](../skills-registry.md), because a
community skill is the most likely way this constraint would be violated by
accident.

## Revisiting this

The right time to add a live ingestion layer is when the product is being
evaluated against real data — and even then, it should sit *behind* the same
`RawSignal` contract the file loader implements, with the file loader retained as
the test fixture and the offline fallback. Live ingestion must be an addition, not
a replacement, or this decision's reproducibility guarantee is lost.
