# Architecture decision records

One file per decision that was genuinely contested — where a reasonable engineer
could have chosen differently.

They are worth writing down because the *reasons* are the part that gets lost. Six
months from now, "why is there no charting library?" is answerable by reading
[0002](0002-no-charting-or-ui-component-library.md) instead of by guessing.

| # | Decision | Status |
|---|---|---|
| [0001](0001-m2-is-a-fixture-not-an-optimizer.md) | M2 is consumed as a labelled fixture, not implemented | **Superseded by 0010** |
| [0002](0002-no-charting-or-ui-component-library.md) | No charting or UI component library; the network map is hand-written SVG | Accepted |
| [0003](0003-deterministic-trust-boundary.md) | No LLM on the authoritative numerical path | Accepted |
| [0004](0004-backend-enforced-governance.md) | Approval is enforced by the backend state machine, and nowhere else | Accepted |
| [0005](0005-tamper-evident-not-tamper-proof-ledger.md) | Hash-chained audit ledger — tamper-evident, not tamper-proof | Accepted, with a named production requirement |
| [0006](0006-offline-first-seeded-data.md) | Offline-first with file-based seed data; no external service load-bearing | Accepted |
| [0007](0007-browser-tests-are-the-behaviour-oracle.md) | Real Chromium is the behaviour and accessibility oracle | Accepted |
| [0008](0008-text-ramp-anchored-to-measured-contrast.md) | The text ramp is anchored to a measured contrast floor, not to taste | Accepted, with a compressed ramp |
| [0009](0009-ledger-rehydrates-from-disk-on-start.md) | The ledger rehydrates from disk on start, and `reset()` wins over it | Accepted, single-writer only |
| [0010](0010-m2-is-implemented-and-opt-in.md) | M2 is implemented — a real optimizer and digital twin — with the fixture kept as the default | Accepted |
| [0011](0011-authentication-is-opt-in.md) | Authentication is off by default; the token is authoritative when it is on | Accepted, with named production requirements |
| [0012](0012-trained-model-is-exported-not-pickled.md) | The trained model is exported to portable JSON, not pickled, and is advisory | Accepted, with a synthetic dataset |
| [0013](0013-sqlite-is-a-ledger-mirror.md) | SQLite is a mirror of the ledger, never a second source of truth | Accepted |
| [0014](0014-feeds-are-opt-in-and-fetches-are-snapshotted.md) | Live signal feeds are opt-in, and every fetch is snapshotted before it touches the pipeline | Accepted |
| [0015](0015-the-des-is-a-cross-check-not-a-second-opinion.md) | The discrete-event simulation is a cross-check that is reported, never averaged | Accepted |

## Format

Each record states the context, the decision, the alternatives that were actually
considered, the consequences — **including the bad ones** — and what would have to
change for the decision to be revisited. A record with no downsides listed is not
a decision, it is marketing.
