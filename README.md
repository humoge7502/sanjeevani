# SANJEEVANI

**A governed closed-loop recovery system for pharmaceutical supply networks.**

> AI detects and understands a disruption, deterministic systems evaluate the
> consequences, a human authorizes the consequential action, SAP-shaped systems
> execute it, and the outcome becomes auditable learning.

This repository implements the **M1 + M3 vertical slice**: upstream intelligence
(sense → verify → scenario → network impact) and governed execution (policy →
compliance → human approval → SAP-shaped execution → audit → learn).

The question this system answers is not "what happened?" — sensing is a solved
problem. It is: **"Can we safely execute the proposed recovery, and can we prove
afterwards why we did?"**

---

## Quick start

```bash
# 1. Dependencies
python -m pip install -r requirements-dev.txt
npm --prefix frontend install
(cd frontend && npx playwright install chromium)   # only needed for the browser tests

# 2. Pre-flight: proves the environment, the seed data and the
#    governance invariant are all intact (no server required)
python scripts/health.py

# 3. The whole loop, one command, offline, no server
python scripts/demo.py
```

For the full command center UI:

```bash
bash scripts/dev.sh          # backend on :8787 + frontend on :5173
```

Then open <http://127.0.0.1:5173> and follow **Reset → Run hero scenario →
Approve → Execute → Audit → Learn**.

### The hero scenario

A maritime corridor closure at Bab-el-Mandeb plus a 2–8 °C biologics excursion
on the consignment waiting at the chokepoint, against a deterministic 12-node
synthetic pharmaceutical network.

| | 3-day horizon | 10-day horizon | 30-day horizon |
|---|---|---|---|
| Revenue at risk | $852,208 | **$3,908,933** | $5,111,325 |
| Stockout probability | 1.56 % | **65.59 %** | 90.46 % |
| Service-level risk | 0.19 % | **63.14 %** | 88.02 % |

The hero consignment — `SHIP-001`, **Trastuzumab 440mg** (BIO-002), $2.856M, bound
for the EU market on `LANE-MUM-BIO-EU` and stranded at `PORT-SUEZ` — carries a
**63.21 %** stockout probability at the 10-day horizon, with **26.67 %** of the
consignment condemned by the modelled temperature excursion. Every one of those
figures is traceable in the UI to its arithmetic:

```
remaining corridor fraction 0.400 of 12d delay -> 4.80d
outage gap = min(H=10d, duration 10d + delay 4.80d) = 10.00d
cover 5d -> shortfall 5.00d
p_stockout = 1 - exp(-5.00/5) = 0.6321
consigned fraction 0.2667 condemned from device trace TVE-4417
```

---

## The loop

```
M1  Sense ──► Verify ──► Understand ──► Scenario ──► Impact
                                                       │
M2                         Simulate ──► Optimize ◄─────┘   (NOT IMPLEMENTED — fixture boundary)
                                             │
M3  Policy ──► Compliance ──► HUMAN APPROVAL ──► Execute ──► Audit ──► Learn
```

### What each agent does

| Agent | Owns | Implementation |
|---|---|---|
| **A1 Sensing** | Normalize, classify, score, provenance, freshness | `backend/agents/sensing.py` |
| **A2 Verification + Scenario** | Dedup, 2-source rule, measurement corroboration, 3/10/30-day scenarios | `backend/agents/verification.py` |
| **A3 Network Impact** | Graph propagation, stockout, revenue at risk, service level | `backend/agents/impact.py` |
| **A4 Logistics/Inventory/Optimization** | *(Member 2 — not in this repository)* | `RecoveryPlan` contract boundary |
| **A5 Compliance + HITL** | Policy engine, risk tiering, approval state machine, execution | `backend/governance/` |
| **A6 Audit + Learning** | Append-only hash-chained ledger, predicted-vs-actual, calibration signals | `backend/audit/`, `backend/learning/` |

---

## Commands

Every command below exists in this repository. Nothing is aspirational.

| Command | Purpose |
|---|---|
| `python scripts/health.py` | Pre-flight check. Run before every rehearsal. |
| `python scripts/demo.py` | Full loop: reset → run → approve → execute → audit → learn |
| `python scripts/demo.py --to-gate` | Stop at the human approval gate |
| `python scripts/demo.py --json` | Machine-readable transcript |
| `python scripts/reset.py` | Clear ledger, approvals and SAP mock logs |
| `bash scripts/dev.sh` | Backend + frontend together (dev servers, HMR) |
| `python scripts/serve.py` | **Production entrypoint**: serves the API *and* the built UI on one port |
| `docker compose up --build` | Same thing, containerised: UI + API on :8787 |
| `bash scripts/verify.sh` | Everything: nine gates — health, compile, lint, types, tests, browser, build |
| `python -m ruff check .` | Python lint (also a gate in `verify.sh`) |
| `python -m mypy backend scripts` | Python typecheck (also a gate in `verify.sh`) |
| `python -m pytest` | Backend suite (155 tests) |
| `python -m pytest -m redteam` | Adversarial governance-bypass suite |
| `python -m pytest -m contract` | Frozen-contract suite |
| `npm --prefix frontend run test` | Frontend unit tests (41) |
| `npm --prefix frontend run test:browser` | **Real Chromium**: hero journey, governance, accessibility (48) |
| `npm --prefix frontend run test:browser:headed` | Same, with a visible browser — useful in a rehearsal |
| `npm --prefix frontend run build` | Production build |
| `npm run api` / `npm run web` | Start each half (ports 8787 / 5173) |

---

## Architecture

```
sanjeevani/
├── config/                     settings · policy rulebook · scenario library
├── data/                       deterministic seed: network · SKUs · consignments · feeds · telemetry
├── backend/
│   ├── contracts/              THE SIX FROZEN INTEGRATION OBJECTS
│   ├── graph/                  12-node network + derived directed graph
│   ├── agents/                 M1: sensing · verification · impact · telemetry · pipeline
│   ├── m2/                     ⚠️  M2 BOUNDARY — RecoveryPlan fixture (no optimizer)
│   ├── governance/             M3: policy engine · compliance · approval state machine
│   ├── sap/                    M3: SAP-shaped IBP/TM/Ariba mocks + execution orchestrator
│   ├── audit/                  M3: append-only hash-chained decision ledger
│   ├── learning/               M3: predicted-vs-actual reconciliation
│   ├── api/                    FastAPI surface (the security boundary)
│   └── orchestrator.py         the loop coordinator
├── frontend/                   React + TypeScript command center
│   └── e2e/                    Playwright: hero journey · governance · axe-core a11y
├── tests/                      155 tests: unit 69 · contract 36 · integration 11 · e2e 24 · redteam 26
├── scripts/                    health · demo · reset · dev · verify · serve
├── Dockerfile                  multi-stage build -> one image (API + built UI)
├── docker-compose.yml          one-command deployment with a persistent ledger volume
└── docs/                       architecture · contracts · boundaries · deployment …
    └── evidence/               authoritative real-data package: 477 sourced records + frozen schemas
```

### Documentation map

| Document | Answers |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | How the loop is structured and where the boundaries are |
| [`docs/contracts.md`](docs/contracts.md) | The six frozen integration objects |
| [`docs/mock-boundaries.md`](docs/mock-boundaries.md) | **What is real, deterministic, simulated and mocked** |
| [`docs/demo-runbook.md`](docs/demo-runbook.md) | The 10-minute presentation, minute by minute |
| [`docs/security-threat-model.md`](docs/security-threat-model.md) | STRIDE review with mitigations and accepted risks |
| [`docs/design-system.md`](docs/design-system.md) | Tokens, hierarchy, motion, accessibility |
| [`docs/performance-budget.md`](docs/performance-budget.md) | Measured budgets and how to reproduce them |
| [`docs/deployment.md`](docs/deployment.md) | **How to deploy it**: the single-image layout, config, ledger volume, scale limits |
| [`docs/skills-registry.md`](docs/skills-registry.md) | Which external skills were evaluated and why none were installed |
| [`docs/research-evidence.md`](docs/research-evidence.md) | Sources that changed the implementation, fact vs inference |
| [`docs/competitive-benchmark.md`](docs/competitive-benchmark.md) | Everstream · Kinaxis · SAP IBP, vendor claims labelled |
| [`docs/judge-defense.md`](docs/judge-defense.md) | The hard questions and the honest answers |
| [`docs/evidence/`](docs/evidence/) | **The real-data package**: 477 records with page-level provenance, the six frozen schemas, and the raw JSON of all 58 web searches |
| [`docs/decisions/`](docs/decisions/) | Nine ADRs, each with its downsides listed |

---

## The governance invariant

Execution is only reachable from an approved state, and only one module in the
codebase can set that state.

```
RECEIVED → POLICY_CHECKING → COMPLIANCE_CHECKING → APPROVAL_REQUIRED
                                                          │
                            ┌─────────────────────────────┼──────────────┐
                            ▼                             ▼              ▼
                         APPROVED                     REJECTED      REVIEW_REQUESTED
                            │
                            ▼                    ← THE ONLY EDGE INTO EXECUTION
                        EXECUTING → EXECUTED → AUDITED → COMPLETED
```

Enforced in the backend, not the UI. `POST /api/plans/{id}/execute` from any
other state returns `409 EXECUTION_BLOCKED` — proven by 26 adversarial tests in
`tests/redteam/`, including an architectural guard that fails if a second module
ever gains the ability to transition a plan into `APPROVED`.

---

## Honest boundaries

This is an MVP. Read [`docs/mock-boundaries.md`](docs/mock-boundaries.md) for the
full inventory. In short:

| | |
|---|---|
| **Real** | Graph traversal and impact arithmetic · telemetry analysis · policy engine · approval state machine · hash-chained ledger · the whole API and UI |
| **Deterministic** | The 12-node network, 12 SKUs, 6 lanes, 8 consignments · scripted signal feeds · device telemetry · post-execution observations · a frozen scenario clock so every run is reproducible |
| **Simulated** | IoT telemetry source · post-execution outcome feedback · trade/sanctions screening lists |
| **Mocked** | SAP IBP / TM / Ariba surfaces · the `RecoveryPlan` (an M2 fixture — **no MILP is solved and no digital twin is replayed**) |
| **Not built** | M2 digital twin and optimizer · real SAP connectors · any trained forecasting model · authentication and multi-tenancy · horizontal scaling |
| **AI usage** | **No language model participates in any number.** An `llm_enabled` flag exists, is off, and could only ever add narrative text. |

Compliance checks are labelled `configured_policy`, `simulated_rule` or
`illustrative_check` in the UI and in the API payload. Nothing here is legal or
regulatory advice and nothing claims certification.

---

## Deployment

It ships as **one deployable unit**: a multi-stage image where the API also
serves the built command center, so there is no second web server and no CORS.

```bash
docker compose up --build      # -> http://localhost:8787
```

Full guide: [`docs/deployment.md`](docs/deployment.md). The parts worth knowing
up front:

- **Multi-stage build.** `npm run build` (which is `tsc -b && vite build`) runs in
the build stage, so a type error **fails the image build** instead of shipping.
Only `frontend/dist` crosses into the runtime stage — `node` is absent from the
final image.
- **~235 MB, non-root (UID 10001), `/app/runtime` declared a `VOLUME`.** The
  ledger is the only mutable state; without the volume the hash chain would be
  lost on every redeploy, making the tamper-evidence claim depend on never
  deploying.
- **The ledger survives a restart.** Verified with the volume mounted: after
  `docker restart`, 41 records rehydrate with the chain intact and the **same
  head hash**, and a new run continues the sequence `1..73` without a break. See
  [ADR 0009](docs/decisions/0009-ledger-rehydrates-from-disk-on-start.md).
- **One process, deliberately.** The orchestrator, approval machine and ledger are
  in-process singletons. More than one worker would fork approval state and
  duplicate ledger `seq` numbers. Horizontal scaling is a stated production
  requirement, not a hidden one.
- **`/api/health` probes the real app**, not a static file, and reports whether
  the ledger is `writable`, `rehydrated_from_disk`, and whether any truncated
  lines were `skipped_malformed_lines`.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the *same*
`bash scripts/verify.sh` gate as a developer does — one definition, two
environments — then builds the image and drives the governance boundary inside
the running container.

---

## Testing

```
155 passed  (backend: unit 69 · contract 36 · integration 11 · e2e 24 · red team 26)
 41 passed  (frontend: 25 pure logic · 16 static accessibility guards)
 48 passed  (browser: 24 in real Chromium × 2 viewports)
```

The backend markers **overlap** — the 11 integration cases are marked inside the
e2e module, so they are counted by both. 155 is the suite total; summing the
markers would double-count. `verify.sh` also runs `ruff` and `mypy` as gates, and
a missing dev tool fails the run rather than being skipped: "lint passes" is only
meaningful if lint actually ran.

The static accessibility guards earned their place immediately: on first run they
found that `.skip-link` was styled in CSS and `<main id="main">` existed, but
**the anchor was never rendered** — a dead skip link that no review had caught.

But a static guard can only check that CSS is *declared*. It cannot tell you the
rendered page is usable. So the browser suite runs the whole product in Chromium
and puts it through **axe-core** across the WCAG 2.1 A/AA rule set, plus keyboard,
reduced-motion and responsive checks at 1600 px and 1120 px.

On its first real run that suite found **five defects no prior review had caught**:

| Found | Detail |
|---|---|
| **Contrast, pervasively** | `--text-muted` measured 4.43:1 and `--text-faint` 2.62:1 against the panel surface — both below the 4.5:1 floor, across KPI labels, table headers and the audit timeline |
| **Nested interactive controls** | The network map was `role="img"` containing twelve `role="button"` nodes: an atomic container holding focusable children, so assistive tech would flatten controls that were still in the tab order |
| **Unscrollable-by-keyboard region** | The learning scorecard table overflows horizontally with no way to scroll it without a pointer |
| **Scrollable HUD at narrow widths** | Twelve loop stages at a 92 px minimum overflow 1120 px, so stages 11 and 12 were unreachable by keyboard |
| **Duplicate React key** | The side rail reused `key="LEARN"` for both Learning and Boundaries, logging a duplicate-key warning on every render |

All five are fixed, and the suite fails the build if any returns. Full list in
[`docs/design-system.md`](docs/design-system.md).

The adversarial suite is the one that matters most. It attempts execution before
approval, execution after rejection, approval with an insufficient role, approval
as an unknown actor, double approval, double execution, compliance-bypass,
state-machine bypass, forged approval fields, malformed plans, ledger tampering,
prompt injection through feed text, and an unavailable M2 provider. Every test
asserts that the attack FAILED.

```bash
python -m pytest -m redteam -v
```

---

## Source material

The specification this build implements lives in
[`docs/source/`](docs/source/) — the SANJEEVANI unified dossier and the
three-member workflow handoff. [`docs/research-evidence.md`](docs/research-evidence.md)
records which external sources materially changed the implementation, with the
distinction between **fact**, **inference** and **hypothesis** made explicit.

---

## License / attribution

Prototype built for SAP Hackfest 2026. SAP, IBP, TM, Ariba, HANA and Joule are
trademarks of SAP SE. No SAP tenant, service or credential is used or contacted
anywhere in this repository.
