# Security Policy

## Scope: what this is, and what it is not

SANJEEVANI is a **deterministic offline prototype** built for SAP Hackfest
2026. It contains no real SAP integration, no tenant data, no credentials, and
no network egress. Every "SAP" surface is a local mock
(`backend/sap/mocks.py`). That context frames everything below.

## Supported version

Only the latest commit on `master` is supported. There are no release
branches.

## Reporting a vulnerability

Open a [GitHub Security Advisory](https://github.com/humoge7502/sanjeevani/security/advisories/new)
(private disclosure) rather than a public issue. Include a description, the
affected file(s) or endpoint(s), and a reproduction if you have one.

You can expect an acknowledgement within **7 days** and a status update
within **30 days**. There is no bug bounty and no SLA beyond those two
commitments — this is a single-maintainer prototype, and saying otherwise
would be a fabrication.

## What is in scope

- The FastAPI surface (`backend/api/app.py`) — the declared security boundary.
- The governance state machine (`backend/governance/`) — bypass or
  state-transition flaws.
- The audit ledger (`backend/audit/ledger.py`) — hash-chain integrity claims.
- The static file serving path (`mount_frontend` in `app.py`) — path traversal.
- Dependency vulnerabilities in `requirements.txt` / `frontend/package.json`.

## What is explicitly out of scope

- The mocked SAP surfaces: they are test doubles by design, not security
  boundaries. Their refusals are labelled as such in the API payload.
- Attacks requiring local filesystem write access to `runtime/`. The ledger's
  own docs state the honest scope: it detects edits to the record body; it
  does not protect against an actor who can rewrite and re-hash the file.
  See [ADR 0005](docs/decisions/0005-tamper-evident-not-tamper-proof-ledger.md)
  and `docs/security-threat-model.md`.
- Denial of service of a single-process demo control plane. Concurrency is a
  documented, accepted limitation (one process, deliberately — see
  `docs/deployment.md`), not a vulnerability to report.

## Known limitations (stated up front)

These are accepted, documented design limitations, not secrets:

1. **No authentication.** The API trusts the network it runs on. The demo
   "approvers" are config-declared roles, resolved server-side for
   authorization logic — they are not user accounts, and there are no
   sessions, tokens or cookies.
2. **Single process.** The orchestrator, approval registry and ledger are
   in-process singletons. Horizontal scaling would fork approval state and
   duplicate ledger sequence numbers.
3. **Best-effort ledger durability.** Appends swallow `OSError` so a read-only
   volume cannot break a demo. `/api/health` exposes `ledger.writable` so an
   operator can detect the silent-failure condition.
4. **Unbounded audit growth.** `/api/audit` grows with the ledger until a
   reset; a long-lived deployment needs pagination (threat model, finding D5).

Full inventory: [`docs/security-threat-model.md`](docs/security-threat-model.md)
(STRIDE review with accepted risks) and
[`docs/mock-boundaries.md`](docs/mock-boundaries.md) (what is real,
deterministic, simulated and mocked).
