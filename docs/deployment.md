# Deployment

SANJEEVANI ships as **one deployable unit**: a single image containing the API
*and* the built command center. The API serves `frontend/dist`, so there is no
second web server to run, no CORS to configure, and no cross-origin failure mode
to debug at a venue.

```bash
docker compose up --build      # -> http://localhost:8787
```

Or without compose:

```bash
docker build -t sanjeevani .
docker run --rm -p 8787:8787 -v sanjeevani-ledger:/app/runtime sanjeevani
```

Both are verified below. Everything in this document was executed, not inferred.

---

## What is in the image

| | |
|---|---|
| Base | `python:3.13-slim` (runtime), `node:20-alpine` (build stage only) |
| Python in image | 3.13.15 |
| Node in image | **absent** — the dependency build stage does not cross into runtime |
| Size | ~235 MB |
| Listens on | `8787` (override with `SANJEEVANI_API_PORT`) |
| Runs as | `sanjeevani`, UID 10001, non-root |
| Mutable state | `/app/runtime` only — declared as a `VOLUME` |
| Build context | ~55 kB (`docs/`, `tests/`, `node_modules/` excluded) |

The image is built in two stages. Stage one runs `npm ci && npm run build`, which
is `tsc -b && vite build` — so **a TypeScript error fails the image build** rather
than shipping a broken bundle. Only `frontend/dist` crosses into the runtime
stage. Confirmed inside the running container: `node --version` → `not found`.

`docs/`, `tests/` and `data/evidence/` are excluded from the image by
`.dockerignore`. Nothing reads them at runtime, and `docs/` alone carries several
megabytes of source PDFs.

---

## Why one process only

The orchestrator, the approval state machine, the SAP-shaped mocks and the ledger
are **in-process singletons**. They are the governance boundary, and they live in
memory.

Running more than one worker would give each worker its own copy of that state
and break the product in ways that are silent rather than loud:

- One worker would hold the approval and another would refuse the execution, so
  an approved plan would fail with `EXECUTION_BLOCKED` depending on which worker
  served the request.
- The ledger would fork. Each worker would assign `seq` from its own in-memory
  counter, and two records would claim the same sequence number.
- `POST /api/demo/reset` would clear one worker's state and leave the others
  serving the previous run.

`scripts/serve.py` therefore never passes `workers=` to uvicorn, and this is
stated rather than left as a trap. **Horizontal scaling is a production
requirement**, listed with the other gaps at the end of this document. It needs
the state to move out of process — a shared store for approvals and a database or
append-only log for the ledger — before a second replica is safe.

Because there is exactly one process, the demo is also single-tenant: one
scenario at a time, one active plan. That matches the brief's presenter-driven
flow, and it is a real limitation rather than a design claim.

---

## Configuration

Every knob below has a working default. **The demo runs with no environment file
at all**, which is the offline-first guarantee. `config/*.yaml` remains the source
of truth for *thresholds* — the impact, policy and verification numbers the
interface claims are auditable — deliberately so that a deployment cannot silently
change an impact calculation through an environment variable.

| Variable | Default | Purpose |
|---|---|---|
| `SANJEEVANI_API_HOST` | `127.0.0.1` | Interface to bind. **Set `0.0.0.0` in a container** or nothing outside it can connect. |
| `SANJEEVANI_API_PORT` | `8787` | Listening port. |
| `SANJEEVANI_CORS_ORIGINS` | localhost dev origins | Comma-separated. Only needed if the UI is served from a *different* origin; same-origin (the default layout) needs no CORS. |
| `SANJEEVANI_RUNTIME_DIR` | `<repo>/runtime` | Directory for the ledger. Point at a volume. |
| `SANJEEVANI_LEDGER_PATH` | `runtime/audit_ledger.jsonl` | Ledger filename (resolved inside `RUNTIME_DIR`). |
| `SANJEEVANI_FRONTEND_DIST` | `<repo>/frontend/dist` | Where the built UI is served from. |
| `SANJEEVANI_REPO_ROOT` | parent of `backend/` | Override if `config/` and `data/` are not siblings of the package. |
| `SANJEEVANI_OFFLINE` | `true` | `false`/`0`/`no` disables the explicit offline marking. |
| `SANJEEVANI_PLAN_PROVIDER` | `fixture` | `fixture` (M2 stand-in), `unavailable` (fails closed, used by the red-team suite), `optimizer` (not implemented — raises). |

A malformed value raises at startup rather than falling back to the default. A
typo'd port silently running on the wrong port is worse than a clear failure.

`.env.example` documents the same set with the same defaults.

---

## The ledger volume

`/app/runtime` holds the append-only hash-chained audit ledger. It is the **only**
mutable state, and it is why the volume matters: without it, the chain is lost on
every redeploy, which would make the tamper-evidence claim depend on never
deploying again.

Verified behaviour, with the ledger on a named volume:

```
before restart : 41 records · chain intact · head cb0b1a01d9ce329e
docker restart
after restart  : 41 records · rehydrated_from_disk: true · chain intact
                 head cb0b1a01d9ce329e   (identical — nothing was lost)
new run        : 73 records · chain intact · issues: none
                 sequence 1..73 strictly increasing
```

The ledger rehydrates from its file on first access so that `seq` and
`prev_hash` stay continuous across a restart. Before that fix, a restart silently
restarted the chain from `GENESIS` while the file held a different head — see
[ADR 0009](decisions/0009-ledger-rehydrates-from-disk-on-start.md).

`/api/health` exposes the state so an operator can tell a fresh ledger from a
restored one:

```json
{
  "ledger": {
    "records": 41,
    "hash_chain": true,
    "path": "/app/runtime/audit_ledger.jsonl",
    "writable": true,
    "rehydrated_from_disk": true,
    "skipped_malformed_lines": 0
  }
}
```

`writable: false` means every append is being dropped on the floor. The request
path deliberately swallows write errors so a read-only filesystem cannot break a
demo — which is exactly why nothing else would notice, and why this field exists.

---

## Health checks

The image declares a `HEALTHCHECK` that calls the **real** `/api/health`
endpoint, not a static file, so it fails if the app is up but its config or
ledger is broken. Compose declares the same check. Startup to `healthy` was
**~6 s** in testing; `start_period` is 10 s to cover a cold interpreter.

```bash
docker inspect --format='{{.State.Health.Status}}' sanjeevani
curl -s localhost:8787/api/health | python3 -m json.tool
```

---

## Behind a reverse proxy

The app honors `X-Forwarded-For` / `X-Forwarded-Proto`
(`proxy_headers=True`), so it logs real client addresses and generates correct
URLs behind a load balancer or ingress. It does not terminate TLS itself —
terminate at the proxy and forward plain HTTP to `8787`.

No CORS entry is needed when the UI is served by this same image, because browser
requests are same-origin. Set `SANJEEVANI_CORS_ORIGINS` only if you deliberately
split the UI onto a separate host.

---

## Running without Docker

For a production-style run on a host (the demo itself should use
`scripts/dev.sh`, which adds the Vite HMR server):

```bash
pip install -r requirements.txt
npm --prefix frontend ci && npm --prefix frontend run build

SANJEEVANI_API_HOST=0.0.0.0 SANJEEVANI_API_PORT=8787 python3 scripts/serve.py
```

The API serves the built UI on the same port. `FRONTEND_DIST` must contain an
`index.html`; if it does not, the server logs a warning and serves the API only
rather than failing to start.

---

## Verification

Before deploying, or before any commit:

```bash
pip install -r requirements-dev.txt
bash scripts/verify.sh
```

Nine gates: environment health, backend compile, ruff, mypy, the backend suite
(unit + contract + integration + e2e + red team), frontend typecheck, frontend
unit and static a11y guards, browser tests in real Chromium, and the production
build. A missing dev tool is reported as a failure, not skipped — "lint passes"
is only meaningful if lint actually ran.

Deployment-specific checks:

```bash
docker build -t sanjeevani .
docker run --rm -d -p 8787:8787 --name sj sanjeevani
docker inspect --format='{{.State.Health.Status}}' sj      # healthy
docker exec sj whoami                                      # sanjeevani (non-root)
docker exec sj sh -c 'node --version'                      # not found (multi-stage worked)
curl -s localhost:8787/api/health | python3 -m json.tool
curl -s -o /dev/null -w '%{http_code}\n' localhost:8787/   # 200, SPA served
```

---

## What is NOT production-ready

Stated plainly, because a deployment guide that only lists strengths is a
liability.

- **Single process, single tenant.** No horizontal scaling. See above.
- **No authentication or authorization on the API.** Approval *authority* is
  enforced — an actor without the required role is refused with `403` and the
  check runs in the backend — but the API has no session, no token and no
  identity provider. `actor_id` is client-supplied and trusted as an identity
  claim. Real deployment needs an authenticated principal behind it, and the
  actor should come from the token rather than the request body.
- **The ledger is tamper-evident, not tamper-proof.** A writer with filesystem
  access can rewrite the file and recompute every hash. Needs append-only storage
  and external anchoring. See
  [ADR 0005](decisions/0005-tamper-evident-not-tamper-proof-ledger.md).
- **The ledger grows without bound.** Every read rehydrates the whole file into
  memory. Fine at demo scale; production needs rotation or a database.
- **SAP is mocked, deliberately.** IBP/TM/Ariba are deterministic in-process
  mocks shaped like the real calls, so business logic would survive a swap to
  real connectors without redesign. They are not connectors. See
  [mock-boundaries.md](mock-boundaries.md).
- **M2 is a labelled fixture.** No optimizer, by design and by scope. See
  [ADR 0001](decisions/0001-m2-is-a-fixture-not-an-optimizer.md).
- **In-memory rate and abuse controls are absent.** Nothing stops a client from
  hammering `/api/demo/run`. Acceptable behind a trusted network; not on the open
  internet.
