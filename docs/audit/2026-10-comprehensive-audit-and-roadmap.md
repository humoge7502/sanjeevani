﻿# SANJEEVANI — Comprehensive Audit & Roadmap

**Date:** 2026-10-25
**Scope:** full repository (`humoge7502/sanjeevani`) — backend (M1+M3), frontend, data,
config, tests, CI, container.
**Baseline measured on this machine:** Python 3.13.6, Node 24.16.0.
All 159 backend tests pass, `scripts/health.py` is green, the hero loop closes.

This document is deliberately structured as **six requested categories plus two
extra** (Observability/Operability and Delivery/Engineering practice), because a
governance product that cannot be operated or shipped safely is not finished.

Every issue below cites the file it lives in, so it can be verified against the
code rather than taken on trust.

---

## 0. Executive summary

SANJEEVANI is an unusually disciplined MVP. Its strongest asset is not any single
feature — it is the **evidence discipline**: frozen contracts, provenance-labelled
compliance checks, an append-only hash-chained ledger, a single-edge governance
state machine, and docs that distinguish fact from inference. That is rare and it
is the thing judges reward.

Its three structural weaknesses are equally clear:

1. **The value chain has a hole in the middle.** M1 senses, M3 governs — but **M2
   (simulate → optimize) is a fixture**. The system cannot compare recovery
   strategies on evidence; it reads hand-set numbers. Every "we will do X because
   it is better" claim rests on a YAML file.
2. **There is no identity.** `resolve_actor()` trusts a client-supplied string.
   Posting `actor_id: "meera.iyer"` grants the COO role. In any non-demo
   deployment this is a complete authorization bypass — the single most severe
   defect in the repository.
3. **The system is a single process with in-memory authority.** Approvals vanish
   on restart and cannot be shared across workers, so the deployment scaling story
   is "one replica, forever", which is stated honestly but is still a ceiling.

The roadmap below closes all three, plus the operational gaps (observability,
rate limiting, security headers) that separate "impressive demo" from "runnable
system".

**Priority order (by value/effort):**

| Rank | Workstream | Closes | Impact |
|---|---|---|---|
| 1 | M2 optimizer + deterministic digital twin | the hole in the value chain | Very High |
| 2 | Authentication, RBAC, multi-tenancy | total authz bypass | Very High |
| 3 | Durable governance persistence | restart/scaling ceiling | High |
| 4 | Forecasting service (honest model provenance) | time-dimension blindness | High |
| 5 | Observability + metrics | blind operations | Medium-High |
| 6 | Security hardening (rate limit, headers) | abuse surface | Medium-High |
| 7 | Audit pagination + hot-path caching | latency/DoS at scale | Medium |
| 8 | Frontend depth (error boundary, live map state) | UX/correctness | Medium |

---

## 1. Project analysis & strategic opportunities

### 1.1 What the project actually is

A **governed closed-loop recovery system**: `sense → verify → scenario → impact →
[plan] → policy → compliance → human approval → execute → audit → learn`.

The differentiating claim in
[competitive-benchmark.md](../competitive-benchmark.md) is the *whitespace between
"the plan exists" and "the action is executed, receipted and auditable"*. That
positioning is correct and defensible. The gap in the claim is that the system
currently **consumes** a plan rather than **producing** one, so the middle of the
loop is a placeholder.

### 1.2 Market differentiation opportunities

| # | Opportunity | Why it differentiates | Where it lands |
|---|---|---|---|
| D1 | **A real multi-objective optimizer with a reproducible decision trail** | Competitors (Kinaxis, SAP IBP) optimize but do not expose *per-constraint* provenance to an auditor. An optimizer whose every chosen variable carries its bound, its shadow contribution and its Pareto rank is a governance-native optimizer. | `backend/m2/` |
| D2 | **Decision provenance as a first-class product surface** | The system already traces every number. Extending the trace to *optimization* (why this plan, why not the alternatives, what the binding constraint was) turns audit from an afterthought into the product. | `Impact.trace`, new `RecoveryPlan.trace` |
| D3 | **Deterministic-by-construction decisions with optional learned priors** | Determinism is the trust anchor. Learned models should *propose* priors, never *compute* the audited number. Very few AI supply-chain products can make that separation credibly. | `backend/forecasting/` |
| D4 | **Counterfactual / "what if we had waited" reporting** | Uses the digital twin to quantify the cost of inaction against the executed plan. Directly answers the persona question "was this the right call?" | `backend/m2/digital_twin.py` |
| D5 | **Compliance-as-code with a diffable rulebook and a dry-run mode** | `config/policies.yaml` already is the rulebook. Adding "what would change if we edit this rule" is a compliance-team feature no competitor ships at this level. | `backend/governance/` |
| D6 | **Cryptographic anchoring of the ledger head** | The ledger admits it is tamper-*evident*, not tamper-*proof*. Anchoring the head hash externally (e.g., a signed daily attestation) closes the last gap honestly. | `backend/audit/` |

### 1.3 Strategic value propositions

- **For a regulator/auditor:** "Every number has a formula, every formula has a
  config key, every decision has a hash, and the optimizer's chosen plan can be
  re-derived bit-for-bit."
- **For a supply-chain head:** "Compare three recovery strategies on simulated
  outcomes, not vendor claims, then execute the chosen one under a role-matched
  approval you can defend in an incident review."
- **For a security reviewer:** "The API is the security boundary; the UI cannot
  set state; identity is verified, not asserted."
- **For a platform owner:** "It runs offline, in one container, with the ledger on
  a volume — and now it also runs with auth, metrics, and durable approvals."

### 1.4 Competitive positioning risks

| Risk | Detail | Mitigation |
|---|---|---|
| Optimization credibility | A "governance-only" story invites "so you didn't solve the hard part". | Ship a real MILP + simulation (Workstream 1). |
| Data authenticity | Everything is synthetic. | Keep the honest labelling; add a real, sourced dataset for the forecasting prior. |
| AI hype mismatch | No LLM in the numbers. | Reframe as a *feature*: "no model can silently change an audited number", and add an explicitly narrative-only LLM seam. |
| Demo fragility | Frozen clock + fixtures. | Keep them; add a "live mode" toggle that does not change any audited arithmetic. |

---

## 2. Issues & required fixes

Severity: **Critical / High / Medium / Low**. Priority: P0 (now) → P3.
Effort is engineering effort, not calendar time.

### 2.1 CRITICAL

**C-1 — No authentication; actor identity is client-asserted (authorization bypass)**

- **Where:** [backend/api/app.py](../../backend/api/app.py#L334-L357) (`/api/plans/{id}/approve|reject|execute`), [backend/governance/approval.py](../../backend/governance/approval.py#L203-L214) (`resolve_actor`).
- **Description:** The approver is chosen by a request body field
  (`{"actor_id": "meera.iyer"}`). `resolve_actor()` maps that string to a role
  from `config/scenarios.yaml`. There is no credential. Any client that can reach
  the port can approve and execute a multi-million-dollar recovery plan.
- **Reproduction:**
  1. `POST /api/demo/run` → plan reaches `APPROVAL_REQUIRED` requiring `coo`.
  2. `POST /api/plans/PLAN-001/approve` with `{"actor_id":"meera.iyer"}` → **200 OK**, state `APPROVED`.
  3. `POST /api/plans/PLAN-001/execute` → **200 OK**, three SAP-shaped posts recorded.
  Expected: 401/403 without a verified credential. Actual: full success.
- **Severity:** Critical. **CVSS 3.1 ≈ 9.1** (`AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:N`) — unauthenticated, remote, high integrity impact on a consequential action.
- **Priority:** P0. **Effort:** 2–3 days. **Dependencies:** none.
- **Timeline:** must land before any deployment outside a laptop.
- **Fix:** JWT (HS256) issuance + verification, role claims, `require_role`
  dependency, tenant claim. **Implemented in this roadmap** — see Workstream 3.
  Kept **opt-in** (`SANJEEVANI_AUTH_ENABLED`) so the offline demo and the 159
  existing tests are untouched, which is the only way to add auth without
  invalidating the reproducibility claims.

**C-2 — Unbounded `GET /api/audit` returns the entire ledger**

- **Where:** [backend/api/app.py](../../backend/api/app.py#L254-L263) → `ledger.records()`.
- **Description:** The endpoint returns every record in full, with no pagination.
  The ledger grows monotonically (`STAGE_SEQUENCE` has 19 stages, plus signals).
  A ledger that has survived months of deployment will return tens of megabytes
  per call, and the JSON serialisation is done under the ledger lock.
- **Impact metrics:** linear growth. At ~2 KB/record, 100 k records ≈ **200 MB**
  response and a multi-second lock hold. That is an availability problem caused
  by an audit read.
- **Severity:** High. **CVSS ≈ 7.5** (unauthenticated DoS via resource exhaustion) once the endpoint is exposed.
- **Priority:** P0. **Effort:** 4–6 h. **Dependencies:** none.
- **Fix:** `?limit=&offset=&stage=&since=` with a hard cap and a `total` count.
  **Implemented** — see Workstream 6.

### 2.2 HIGH

**H-1 — Approval authority is process-local; a restart silently discards approvals**

- **Where:** [backend/governance/approval.py](../../backend/governance/approval.py#L245-L265) (`ApprovalRegistry`, in-memory dict).
- **Description:** The ledger persists; the approval *authority* does not. The
  docs call the direction "safe" (a restart cannot resurrect an approval) — true,
  but it also means **a legitimate approval cannot survive a rolling deploy**, and
  two workers would fork governance state entirely, so the system is pinned to one
  replica.
- **Severity:** High (functional + scalability). **Priority:** P1. **Effort:** 1–2 days.
- **Dependencies:** interacts with C-1 (tenant scoping).
- **Fix:** SQLite (WAL) governance store with an explicit
  `authority_state: LOST_ON_RESTART | RESTORED_REQUIRES_REAUTH` policy, so the
  safe direction is preserved *and* observable. **Implemented** — Workstream 4.

**H-2 — Hot read path recomputes the whole hash chain on every request**

- **Where:** [backend/orchestrator.py](../../backend/orchestrator.py#L658-L664) (`state_snapshot` calls `timeline()`, `stage_coverage()`, `verify_chain()`), surfaced by `GET /api/state`, which the UI calls after every action.
- **Description:** Three O(n) passes over the full ledger, including a SHA-256
  recomputation of every record, on every state read. Cost grows with ledger size
  while the useful payload does not.
- **Impact metrics:** ≈ 1 SHA-256 per record per request. At 100 k records that is
  ~100 k hashes (~0.3–1 s) and ~3 full list materialisations per refresh.
- **Severity:** High. **Priority:** P1. **Effort:** 4–8 h.
- **Fix:** memoise chain verification keyed by `(len, head_hash)`; paginate the
  timeline; compute coverage from a running stage set. **Implemented** — Workstream 7.

**H-3 — No rate limiting on state-changing endpoints**

- **Where:** all `POST /api/plans/*` and `POST /api/demo/*`.
- **Description:** No throttling. Combined with C-1 this allows unbounded
  approval/execution attempts (governance probes) and unbounded `demo/reset`
  (which truncates the ledger file — a destructive unauthenticated call).
- **Severity:** High. **Priority:** P1. **Effort:** 3–4 h.
- **Fix:** in-process token-bucket limiter keyed by identity (or client when
  unauthenticated), `429` with `Retry-After`. **Implemented** — Workstream 6.

**H-4 — No security response headers; no CSP**

- **Where:** [backend/api/app.py](../../backend/api/app.py#L50-L59) (only CORS is configured).
- **Description:** Missing `Content-Security-Policy`, `X-Content-Type-Options`,
  `X-Frame-Options`/`frame-ancestors`, `Referrer-Policy`,
  `Permissions-Policy`, `Strict-Transport-Security`. The SPA is served from the
  same origin, so a missing CSP leaves no defence-in-depth for injected content.
- **Severity:** High. **Priority:** P1. **Effort:** 2 h.
- **Fix:** security-headers middleware with a strict CSP that still allows Vite's
  built assets. **Implemented** — Workstream 6.

**H-5 — `demo/reset` is unauthenticated and destructive**

- **Where:** [backend/api/app.py](../../backend/api/app.py#L303-L305) → `orchestrator.reset()` → `ledger.reset()` truncates the ledger file ([ledger.py](../../backend/audit/ledger.py#L176-L195)).
- **Description:** An unauthenticated `POST` wipes the audit ledger. In a demo this
  is a feature; exposed, it is a data-destruction endpoint.
- **Severity:** High. **Priority:** P1. **Effort:** 1 h (gate behind auth + role).
- **Fix:** require an authenticated `admin`/`demo_operator` role when auth is on.

**H-6 — `/api/network` reports `closed: false` for every node, always**

- **Where:** [backend/graph/network.py](../../backend/graph/network.py#L297) — `"closed": False` is hard-coded in `snapshot()`.
- **Description:** The network map can never show a closed node from the network
  endpoint. Evidence of a disruption exists in M1 but is not reflected here, so a
  UI that reads this field is structurally unable to render the disruption it is
  portraying.
- **Severity:** Medium-High (correctness/UX). **Priority:** P2. **Effort:** 2 h.
- **Fix:** derive `closed` from the active run's disrupted nodes. **Implemented** —
  Workstream 8.

### 2.3 MEDIUM

**M-1 — Ledger durability is `open/append/close` per record, no fsync**

- **Where:** [backend/audit/ledger.py](../../backend/audit/ledger.py#L225-L231).
- **Description:** One file open per append; `OSError` is swallowed, so a full disk
  silently loses audit records. `health()` detects non-writability, but a partial
  write is not detected.
- **Impact:** O(n) syscalls; durability loss under disk pressure.
- **Severity:** Medium. **Priority:** P2. **Effort:** 4 h. **Dependencies:** H-1.
- **Fix:** keep a persistent file handle, optional `flush+fsync` policy, and count
  write failures in `health()` rather than swallowing them silently.

**M-2 — `DecisionLedger.chain_enabled` is read at import time**

- **Where:** [backend/audit/ledger.py](../../backend/audit/ledger.py#L103) — `ledger = DecisionLedger()` runs at import; `reload_config()` does not re-derive `chain_enabled`.
- **Description:** Toggling `audit.hash_chain` and calling `reload_config()` has no
  effect, which contradicts the "every knob is live in config" story.
- **Severity:** Medium (correctness of a documented behaviour). **Priority:** P2. **Effort:** 1 h.

**M-3 — `classify_tier` contains a no-op branch**

- **Where:** [backend/governance/policy_engine.py](../../backend/governance/policy_engine.py#L131-L132) — `elif ...: pass`.
- **Description:** Dead branch; a reader reasonably assumes it does something.
- **Severity:** Low-Medium (maintainability). **Priority:** P3. **Effort:** 15 min.

**M-4 — `run_id` is a hard-coded constant**

- **Where:** [backend/orchestrator.py](../../backend/orchestrator.py#L107) — `run_id="RUN-HERO-001"`.
- **Description:** Every run has the same id, so runs cannot be distinguished in
  the ledger or in logs.
- **Severity:** Medium. **Priority:** P2. **Effort:** 1 h. **Dependencies:** config seed.

**M-5 — No request-size limit**

- **Where:** FastAPI/uvicorn defaults; `DecisionRequest.rationale` is capped at
  1000 chars but `actor_id` only has `min_length=1`.
- **Description:** A large body is buffered before validation. `actor_id` accepts a
  multi-megabyte string that then flows into the ledger.
- **Severity:** Medium. **Priority:** P2. **Effort:** 2 h.
- **Fix:** `max_length` on `actor_id`, a body-size middleware cap.

**M-6 — No structured logging or metrics**

- **Where:** scattered `logging.getLogger` calls; no JSON formatter, no request id,
  no `/metrics`.
- **Description:** Nothing can answer "how many approvals were refused in the last
  hour" or "what is p95 latency of `/api/state`".
- **Severity:** Medium. **Priority:** P2. **Effort:** 1 day.
- **Fix:** `prometheus_client` + a JSON log formatter + request-id middleware.
  **Implemented** — Workstream 5.

**M-7 — `SapSurface.call_log()` re-sorts the whole log on every call**

- **Where:** [backend/sap/mocks.py](../../backend/sap/mocks.py#L334-L336), surfaced in `state_snapshot`.
- **Severity:** Low. **Priority:** P3. **Effort:** 30 min.

### 2.4 LOW

| ID | Issue | Where | Fix |
|---|---|---|---|
| L-1 | `impact.py` recomputes `_excursion_for` twice per shipment (memoised, so cheap) | [impact.py](../../backend/agents/impact.py#L192-L330) | hoist into a dict |
| L-2 | Frontend bundle has no code-splitting | `frontend/vite.config.ts` | `manualChunks` |
| L-3 | No npm `audit`/`pip-audit` gate in CI | `.github/workflows/ci.yml` | add a dependency-audit step |
| L-4 | No Dependabot for Python/Node in one place | `.github/dependabot.yml` | verify coverage |
| L-5 | No `pyproject.toml`; config spread across `mypy.ini`/`ruff.toml`/`pytest.ini` | repo root | optional consolidation |

### 2.5 Architectural problems

**A-1 — Single-process ceiling (admitted, unresolved).**
The orchestrator, approval registry and ledger are in-process singletons
([orchestrator.py](../../backend/orchestrator.py#L716)). Multi-worker would fork
approval state and duplicate ledger `seq`s. Documented, but it caps availability at
one replica. **Resolution:** Workstream 4 (durable governance + ledger index) moves
the *state* out of the process, enabling a future stateless API workers + a shared
store. **Effort:** 3–5 days for full horizontal scaling; 1–2 days for the durable
foundation shipped here.

**A-2 — No tenancy dimension anywhere.**
A second pharma company would collide in the ledger, the registry and the SAP mock
call log. **Resolution:** tenant id on JWT → ledger record → registry key →
`state_snapshot`. **Implemented (foundation)** — Workstream 3.

**A-3 — The value chain has no optimizer (see §1.2).** **Resolution:** Workstream 1.

### 2.6 Technical debt register

| ID | Debt | Effort | Business impact if unaddressed |
|---|---|---|---|
| T-1 | M2 fixture instead of a real optimizer | 3–5 d | The core claim ("we chose the best recovery") is unsupported. |
| T-2 | No auth | 2–3 d | Cannot be deployed; unpresentable to a security reviewer. |
| T-3 | In-memory authority | 1–2 d | Approvals lost on deploy; no scaling. |
| T-4 | No forecasting at all | 2–3 d | Blind to how disruption duration evolves; scenarios are static. |
| T-5 | No observability | 1 d | Incidents are undiagnosable in production. |
| T-6 | No frontend error boundary; a render error blanks the app | 0.5 d | A single bad payload loses the demo on stage. |
| T-7 | Ledger read/verify on hot path | 0.5 d | Latency and resource growth. |

---

## 3. Enhancements & modifications

### 3.1 `backend/m2/recovery_plan_provider.py` → replaceable by a real optimizer

- **Now:** `FixtureRecoveryPlanProvider` returns hand-set YAML.
- **Change:** add `OptimizerRecoveryPlanProvider` implementing the *same* Protocol,
  selected by `SANJEEVANI_PLAN_PROVIDER=optimizer`. **Nothing in M3 changes** — that
  is the test of the boundary.
- **Benefit:** the value chain closes; `RecoveryPlan.source` becomes `optimizer`
  and the UI badge changes truthfully.
- **Trade-off:** optimization is heavier than a dict lookup; mitigate with a
  deterministic seed and a cached, small problem size.
- **Approach:** MILP via PuLP/CBC over lane-selection + mode + inventory actions,
  with an exact enumeration fallback so the build never depends on a solver binary.

### 3.2 `backend/agents/impact.py` → expose per-consignment Pareto sensitivity

- **Change:** in addition to the point estimate, emit a small sensitivity band
  (stockout probability at ±the configured coverage assumption) so a human sees how
  brittle the number is.
- **Benefit:** an auditor can distinguish "63 % within ±4 pts" from "63 %, fragile".
- **Trade-off:** more compute; bound it to the affected consignments only.

### 3.3 `backend/governance/policy_engine.py` → dry-run diff mode

- **Change:** `evaluate(..., rulebook=override)` so a proposed rulebook edit can be
  scored against the last N plans without touching the live rulebook.
- **Benefit:** compliance-as-code; "what would this rule change have blocked?"
- **Trade-off:** needs a small plan archive to be meaningful.

### 3.4 `backend/audit/ledger.py` → verification memoisation + persistent handle

- **Change:** memoise `verify_chain()` by `(record_count, head_hash)`; hold a file
  handle; make write failures observable.
- **Benefit:** O(1) hot path; durability is measurable.
- **Trade-off:** a memoised "intact" is only valid until the next append — invalidate
  on every append (cheap).

### 3.5 `frontend/src/App.tsx` → error boundary + live disruption state

- **Change:** a React error boundary; drive the network map's `closed` flag from the
  active run's disrupted nodes; add a "live vs demo" indicator.
- **Benefit:** the UI can no longer blank on a bad payload, and it tells the truth
  about what is closed.
- **Trade-off:** none material.

### 3.6 `backend/api/app.py` → dependency-injected identity & pagination

- **Change:** `Depends(current_principal)` on every state-changing route; paginated
  audit; `/metrics`.
- **Benefit:** one enforcement point, testable in isolation.
- **Trade-off:** auth must remain optional to preserve the offline demo.

---

## 4. Advanced features

| # | Feature | Why it elevates the product | Technique |
|---|---|---|---|
| AF-1 | **Digital-twin counterfactuals** ("what if we had done nothing / waited / routed via air") | Turns the optimizer into a decision-support instrument, not a black box. Quantifies the cost of inaction. | Deterministic discrete-event simulation (SimPy) over the seeded network, no sampling. |
| AF-2 | **Pareto frontier with binding-constraint attribution** | Every recommended plan states *which constraint bound it*. That is the difference between "trust me" and "here is the proof". | Multi-objective search + MILP dual/marginal extraction. |
| AF-3 | **Learned disruption-duration priors with a hard provenance boundary** | Brings ML in without contaminating a single audited number. | Train offline on a sourced dataset; persist artefact + metadata; consume as an *advisory prior only*. |
| AF-4 | **Ledger head anchoring / attestation** | Closes the "tamper-evident, not tamper-proof" gap honestly. | Daily signed head digest + external anchor interface. |
| AF-5 | **Scenario stress-testing harness** | Run the whole loop against a matrix of synthetic disruptions and report robustness. | Parameter sweep over the scenario library with a fixed seed. |
| AF-6 | **Narrative-only LLM seam (strictly off the number path)** | Satisfies "AI" expectations without violating the trust model. | A `narrate()` function that receives *finished* numbers and returns prose; contract-tested to never emit a number. |

---

## 5. New additions

| # | Addition | Value | Integration |
|---|---|---|---|
| N-1 | `backend/m2/optimizer.py` — MILP + exact fallback | Closes T-1 | implements `RecoveryPlanProvider` |
| N-2 | `backend/m2/digital_twin.py` — deterministic simulation | Powers counterfactuals + optimizer evaluation | called by the optimizer |
| N-3 | `backend/forecasting/` — trained duration model + artefact | Time dimension | consumed as a prior by A2 |
| N-4 | `backend/api/auth.py` — JWT + RBAC + tenants | Closes C-1 / A-2 | FastAPI dependency |
| N-5 | `backend/persistence/store.py` — SQLite governance store | Closes H-1 / A-1 foundation | used by the registry |
| N-6 | `backend/observability/` — metrics + JSON logs + request IDs | Closes M-6 | ASGI middleware |
| N-7 | Rate limiting + security headers | Closes H-2/H-3/H-4 | ASGI middleware |
| N-8 | Audit pagination + chain memoisation | Closes C-2/H-2 | ledger + API |
| N-9 | OpenTelemetry tracing (optional) | Distributed diagnosis | ASGI instrumentor |
| N-10 | Real feed adapters (GDELT, Open-Meteo, Freightos) behind an offline-first interface | Makes "real signal ingestion" more than a seam | `backend/agents/sensing.load_feed` |

---

## 6. Verification & testing strategy

The suite is already strong (159 backend + 41 unit + 48 browser). The strategy is
to extend it in the same style — behaviour as the oracle, boundaries asserted
under attack — not to replace it.

| Layer | What to add | Why |
|---|---|---|
| Unit | optimizer optimality vs brute force; twin determinism; forecaster reproducibility; auth token round-trip; rate limiter; chain memo invalidation | new code must be provably correct |
| Contract | `RecoveryPlan` from the optimizer must satisfy the frozen schema; `source == "optimizer"` | boundary integrity |
| Integration | `SANJEEVANI_PLAN_PROVIDER=optimizer` runs the whole loop and still stops at the gate | the swap must not move M3 |
| Red team | forged JWT; role escalation via claim tampering; tenant crossover; audit pagination bypass of the cap; rate-limit evasion; unauthenticated reset | attacks must fail |
| Performance | `/api/state` p95 with 100 k ledger records before/after memoisation; `/api/audit` bounded payload | measurable budgets |
| Accessibility | unchanged browser suite must still pass; new UI controls need labels | no regression |
| Determinism | two optimizer runs produce identical plans; two full loops produce identical ledger hashes | the core claim |

**Gates:** `bash scripts/verify.sh` remains the single definition. New checks are
added *into it* (not into CI YAML) so local and CI stay identical.

---

## 7. Extra — Observability & operability

- **Metrics:** requests by route/status, latency histograms, governance decisions by
  outcome (`APPROVED`/`REJECTED`/`BLOCKED`/`DENIED`), ledger records, chain-intact
  gauge, optimizer solve time, model-inference time.
- **Logs:** JSON, one line per request with `request_id`, `tenant`, `principal`,
  `route`, `status`, `duration_ms`.
- **Health:** extend `/api/health` with `auth_enabled`, `ledger.chain_intact`,
  `optimizer.provider`, `persistence.backend`.
- **Runbooks:** how to rotate the JWT secret; how to recover from a read-only ledger;
  how to re-verify the chain offline.

## 8. Extra — Delivery & engineering practice

- Dependency vulnerability audit in the gate (`pip-audit`, `npm audit --omit=dev`).
- SBOM generation on release.
- `pyproject.toml` consolidation (optional).
- Conventional commits + a `CHANGELOG.md`.

---

## 9. Implementation log for this roadmap

The following were **implemented and verified** on this machine as part of this
work. Each has tests; `scripts/verify.sh` remains green.

| Workstream | Files | Verification |
|---|---|---|
| W1 M2 optimizer + twin | `backend/m2/{digital_twin,optimizer}.py`, `config/recovery_options.yaml` | 29 unit tests: MILP/exhaustive agreement, determinism, rulebook passage, contract conformance, the Cape and mixed-plan results |
| W2 Forecasting | `backend/forecasting/`, `scripts/train_forecasting_model.py`, `data/ml/`, `requirements-ml.txt` | 17 unit tests: sklearn-equivalence of the portable evaluator, clean-interpreter dependency check, advisory boundary in both directions |
| W3 Auth/RBAC/tenancy | `backend/security/`, `config/identity.yaml`, `scripts/make_identity.py` | 24 unit + 30 red-team tests, driving the real ASGI app |
| W4 Persistence | `backend/persistence/store.py`, ledger durability | 14 unit tests: WAL, idempotent mirroring, bounded queries, write failures counted not raised, consistency reported |
| W5 Observability | `backend/observability/`, `backend/api/middleware.py` | Metrics present on `/metrics`; request id echoed and hostile ids replaced |
| W6 Security | `backend/security/{headers,ratelimit}.py`, API gating | CSP nonce contract tests, rate-limit flood, oversized body, `413`/`429`/`401`/`403` mappings |
| W7 Performance | chain memoisation (primitive left uncached), audit pagination | Cap enforced; `verify_chain()` still recomputes |
| W8 Frontend depth | `frontend/src/components/ErrorBoundary.tsx`; `Network.snapshot(closed_nodes=…)` | `tsc -b` + production build clean; `closed` is now derived rather than a constant |
| W9 DES cross-check (AF-1) | `backend/m2/des.py`, `config/des.yaml`, `docs/m2-des.md`, ADR 0015, provider-aware M2 surfaces in the UI | 35 unit tests: determinism asserted against the source, interval-union semantics, berth contention, topology-derived arrivals, the 70.8 h WAIT blind spot, advisory-vs-constraint split; the recommended plan is provably unchanged |
| W10 Learning-shape fix | `backend/learning/reconcile.py` (PENDING scorecard carries the RECONCILED shape), `scripts/demo.py` (status-aware learning section), `frontend/src/components/AuditLearningPanels.tsx` (status badge), `tests/unit/test_learning.py` | 3 unit tests: a plan without a seeded observation reports PENDING with zeros and an explanatory method note; the two scorecard shapes expose identical keys; the honesty clause (no actual invented) |
| W11 netguard | `backend/security/netguard.py`, wiring in `backend/agents/feeds/base.py` + `backend/agents/narrative.py`, `config/settings.yaml`, `tests/unit/test_netguard.py` | 21 tests: every refused destination class, rebind-shaped resolution, the allow_private opt-out keeping the scheme rule, both real callers refusing through their own error types |
| W12 attestation (AF-4 foundation) | `backend/audit/attestation.py`, `scripts/attest_ledger.py`, `tests/unit/test_attestation.py` | 15 tests: round-trip, six tamper classes refused, append-compatible prefix rule, CLI sign/verify/offline, broken-chain and truncation refusal |
| W13 stress harness (AF-5) | `scripts/stress.py`, `tests/unit/test_stress.py` | 3 unit tests; the 6-cell default matrix holds per-cell determinism and admissibility |

### Corrections to this document's own predictions

Two things here were wrong when written, and the implementation found out:

1. **[§3.4] Memoising `verify_chain()` weakens the primitive.** The plan was to
   cache the chain verdict. Doing so makes a *tamper-evidence* function capable of
   saying "intact" without looking, and the existing red-team tamper test caught it
   immediately. The shipped design keeps `verify_chain()` always-recomputing and
   adds a separately named `verify_chain_memoised()` for the polling endpoint only.
2. **[§2.2 H-1] Persistence was scoped too broadly.** "Durable governance state"
   would have reversed a documented safety property (authority is *lost* on
   restart, deliberately). What shipped is narrower and better: the ledger's write
   path is now durable and observable, and a queryable SQLite **mirror** exists for
   operators — with the JSONL ledger still authoritative. See
   [ADR 0013](../decisions/0013-sqlite-is-a-ledger-mirror.md).


*See the repository docs updated alongside this file for the operator-facing detail.*
