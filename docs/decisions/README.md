# Architecture decision records

One file per decision that was genuinely contested — where a reasonable engineer
could have chosen differently.

They are worth writing down because the *reasons* are the part that gets lost. Six
months from now, "why is there no charting library?" is answerable by reading
[0002](0002-no-charting-or-ui-component-library.md) instead of by guessing.

| # | Decision | Status |
|---|---|---|
| [0001](0001-m2-is-a-fixture-not-an-optimizer.md) | M2 is consumed as a labelled fixture, not implemented | Accepted |
| [0002](0002-no-charting-or-ui-component-library.md) | No charting or UI component library; the network map is hand-written SVG | Accepted |
| [0003](0003-deterministic-trust-boundary.md) | No LLM on the authoritative numerical path | Accepted |
| [0004](0004-backend-enforced-governance.md) | Approval is enforced by the backend state machine, and nowhere else | Accepted |
| [0005](0005-tamper-evident-not-tamper-proof-ledger.md) | Hash-chained audit ledger — tamper-evident, not tamper-proof | Accepted, with a named production requirement |
| [0006](0006-offline-first-seeded-data.md) | Offline-first with file-based seed data; no external service load-bearing | Accepted |
| [0007](0007-browser-tests-are-the-behaviour-oracle.md) | Real Chromium is the behaviour and accessibility oracle | Accepted |
| [0008](0008-text-ramp-anchored-to-measured-contrast.md) | The text ramp is anchored to a measured contrast floor, not to taste | Accepted, with a compressed ramp |
| [0009](0009-ledger-rehydrates-from-disk-on-start.md) | The ledger rehydrates from disk on start, and `reset()` wins over it | Accepted, single-writer only |

## Format

Each record states the context, the decision, the alternatives that were actually
considered, the consequences — **including the bad ones** — and what would have to
change for the decision to be revisited. A record with no downsides listed is not
a decision, it is marketing.
