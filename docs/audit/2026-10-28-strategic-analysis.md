# SANJEEVANI — Post-Completion Strategic Analysis & Next-Frontier Roadmap

**Date:** 2026-10-28
**Baseline:** the state after the 2026-10 comprehensive audit's workstreams W1–W10 all
landed and verified (`e2da557`), plus this document's own W11–W17 (netguard,
ledger attestation, stress harness, tenant-scoped registry, optimizer browser
suite, forecaster time split, attestation pre-flight) — **445 backend + 46
frontend + 52 browser tests, `bash scripts/verify.sh` 10/10 green.**
Predecessor: [`2026-10-comprehensive-audit-and-roadmap.md`](2026-10-comprehensive-audit-and-roadmap.md)
(2026-10-25). That audit's issues C-1, C-2, H-1…H-6, M-1…M-7 and workstreams W1–W8
are implemented; W9 (DES) and W10 (learning shape) landed after it. This document
re-scores the project from the *new* baseline and is deliberately structured as the
six requested categories plus two extras.

---

## 0. Executive summary

The loop the product promises — sense → verify → understand → scenario → impact →
**simulate → optimize** → policy → compliance → human approval → execute → audit →
learn — is now real end to end, with no fixture load-bearing anywhere:

* M2 produces plans from a multi-objective binary program (PuLP/CBC, cross-checked
  by exhaustive enumeration, evaluated on a deterministic digital twin, shadowed by
  a discrete-event queue that reports disagreements with the twin — never averages
  them).
* M3's boundary survived the swap: the optimizer's plan faces the same approval
  gate, rulebook and ledger as the fixture's.
* The audit chain now has the beginnings of an external anchor (HMAC head
  attestation, ADR 0005's admitted gap).
* Every dial-out passes a single outbound-host guard.

The three structural weaknesses that remain are unchanged in kind and are the whole
of the next frontier:

1. **One process** — the orchestrator, approval registry, rate limiter and ledger
   are in-process singletons. This is stated honestly in three docs and is the
   ceiling on availability.
2. **One tenant-shaped hole** — a tenant claim rides on tokens and ledger records,
   but the approval registry is still process-global, so multi-tenancy is a claim
   the system cannot yet enforce under shared operation.
3. **A synthetic world** — the network, the action catalogue's coefficients and the
   forecaster's training rows are engineering estimates and constructed data,
   labelled as such everywhere. The *engines* are real; the *data* is a demo.

Everything in §1–§5 is sequenced against those three.

---

## 1. Project analysis & strategic opportunities

### 1.1 What is now defensibly true in a sales conversation

| Claim | Evidence a skeptic can check |
|---|---|
| "The plan is computed, not quoted" | `GET /api/state` → `m2`: decision space, objective, MILP-vs-exhaustive agreement, Pareto frontier |
| "We know what inaction cost" | uniform-policy counterfactuals per solve, each with its own DES verdict |
| "The twin has a blind spot and we publish it" | the 70.8 h WAIT discrepancy, asserted by `tests/unit/test_m2_des.py` |
| "Nothing executes without a role-matched human" | 26+ adversarial red-team tests drive the real ASGI app; the UI cannot set state |
| "Every number re-derives bit-for-bit" | frozen contracts + scenario clock + `scripts/stress.py` matrix determinism |
| "The audit trail is signed against later rewriting" | `scripts/attest_ledger.py` attest/verify, prefix-compatible, key-off-volume |

Few governance products can put a file path and a test name behind each claim. That
is the differentiation: **decision provenance as the product**, not a feature.

### 1.2 Where the next differentiation lives (ranked)

1. **Rolling-horizon re-optimisation** — one solve per event is the honest MVP;
   competitors (Kinaxis IBP-class) re-plan on a cadence. A `replan()` that consumes
   post-execution observations and re-solves with updated state turns the learning
   loop from reporting into control — with the same governance gate per replan.
   *This is the single biggest closing-the-gap-with-incumbents item.*
2. **Multi-tenant registry isolation** — tenant-scoped approval registries unlock
   the "shared operator, isolated decisions" deployment story (and the SaaS model in
   the evidence package). Effort is bounded: the tenant claim already exists end to
   end; what remains is keying the registry and mocking boundaries by tenant.
3. **Attestation as a product surface** — the HMAC anchor's `kind` seam is built
   for RFC-3161 (a real TSA is a POST-and-parse). "Bring your own timestamp
   authority" is a compliance-team feature competitors do not ship at this tier.
4. **The DES as an operator tool** — berth/service models are config; a "what does
   the queue look like if the port drops to one berth" slider is a demo moment and
   a real planning question, at near-zero code cost (the engine is already there).

### 1.3 Value propositions per audience

* **Regulator/auditor:** every number has a formula, every decision a hash, every
  head an attestation; re-derivable bit-for-bit (`scripts/stress.py` proves it at
  matrix scale).
* **Supply-chain head:** compare recovery strategies on simulated outcomes, execute
  the chosen one under a role-matched approval you can defend in review, and see
  what inaction would have cost — with a queueing model saying what the closed form
  cannot.
* **Security reviewer:** single security boundary (the API), backend-enforced state
  machine, verified identity, CSP with nonces, rate limits, and now an
  outbound-host guard with one documented opt-out.

### 1.4 Honest risks

| Risk | Standing | Mitigation status |
|---|---|---|
| "Your data is fake" | true, labelled everywhere | keep labelling; the *pipeline* is the deliverable; a real dataset is a config-swap |
| "Single replica" | true, documented | acceptable for the demo tier; the durable foundation (ledger rehydration, SQLite mirror) exists |
| "No live research" | this repo's competitive evidence is a frozen 58-search package | a live refresh needs search/LLM API keys the operator supplies; do not fabricate |

---

## 2. Issues & required fixes (current baseline)

Severity · priority · effort · dependencies. The 2026-10-25 catalog's items are all
**resolved** (C-1 auth bypass, C-2 unbounded audit, H-1…H-6, M-1…M-7, T-1…T-7); what
follows is what a fresh pass finds *today*.

### 2.1 High

**N-1 — Approval registry is process-global; no tenant isolation**
- Where: `backend/governance/approval.py` (registry keyed by plan id only).
- Impact: in a shared deployment, one tenant's operator can act on another tenant's
  plan; the token's tenant claim is recorded but not enforced here.
- Severity **High** (deployability, not demo). Priority **P1**. Effort **1–2 d**.
- Dependencies: none (token tenancy exists). Timeline: before any multi-tenant pilot.

**N-2 — Single-process authority caps availability**
- Where: `backend/orchestrator.py` singletons (stated in deployment docs).
- Impact: no horizontal scaling; a crash drops in-flight approvals by design.
- Severity **High** (at deployment tier). Priority **P2**. Effort **3–5 d** for a
  shared-store foundation. Dependencies: N-1 (registry first).

**N-3 — Browser suite never exercises optimizer mode**
- Where: `frontend/playwright.config.ts` boots the backend once, fixture-provider.
- Impact: the flagship UI addition (solver-trace panel) is untested in a real
  renderer; a rendering bug would reach a live demo unseen.
- Severity **Medium-High** (demo risk). Priority **P1**. Effort **2–3 h**:
  a second Playwright project (`optimizer` webServer with
  `SANJEEVANI_PLAN_PROVIDER=optimizer`) + one spec asserting the evidence panel.
- Dependencies: none. Timeline: before the next live demo.

### 2.2 Medium

**N-4 — Forecaster split is i.i.d. shuffled, not temporal** (ADR 0012 admits it)
- Where: `scripts/train_forecasting_model.py`.
- Effort **0.5–1 d**: add `--time-split` (train on earlier rows, test on later) and
  report both; the metadata already says which split ran. Priority P2.

**N-5 — `verify_chain()` is O(n) per audit read**
- Where: `backend/audit/ledger.py:442` — deliberate (the tamper primitive must
  recompute); the hot path already uses the memoised twin.
- Residual exposure: `GET /api/audit` paginates but re-verifies. Effort **2–4 h**
  to cap verification frequency server-side (e.g. verify-on-append + N-minute
  re-verify), with the red-team suite re-run as the gate. Priority P3.

**N-6 — DES waits are recorded but not alerted**
- The DES reports disagreements in the trace; nothing pushes them at an operator.
- Effort **0.5 d**: surface `des.contradicts` as a metric and a KPI badge.
  Priority P2. Dependencies: none.

### 2.3 Low / debt register

| ID | Item | Effort | Note |
|---|---|---|---|
| L-1 | `pyproject.toml` consolidation (mypy.ini/ruff.toml/pytest.ini) | 2 h | cosmetic, tooling parity |
| L-2 | `pip-audit` + `npm audit` in verify.sh | 2 h | supply-chain gate; roadmap §8 |
| L-3 | SBOM on release (syft or `pip freeze`-based) | 2 h | pairs with L-2 |
| L-4 | `scripts/attest_ledger.py` not yet in verify.sh | 1 h | a sign+verify round-trip gate would catch drift early (its library tests already run in-suite) |
| L-5 | Frontend bundle code-splitting | 2 h | roadmap L-2, unchanged |

### 2.4 Security posture (post-remediation)

The five Mimosa findings of this session are closed: alg:none tests forge tokens by
hand, PRAGMA is static text with a drift guard, and both dial-outs speak explicit
`http.client` behind `backend/security/netguard.py` (http/https only; hosts must
resolve public; one documented `allow_private_endpoints` opt-out; rebind-shaped
resolution covered by tests). Residual accepted risks are unchanged from
[`security-threat-model.md`](../security-threat-model.md): the attestation key is
only as safe as its storage; DNS is resolved at validation time, so a resolver that
lies between validation and connection is out of scope at this tier; no HSM.

---

## 3. Enhancements & modifications

| Component | Enhancement | Approach | Benefit / trade-off |
|---|---|---|---|
| `backend/governance/approval.py` | tenant-scoped registry | key registry entries by `(tenant, plan_id)`; derive tenant from the verified principal, refuse mismatches with 403 | closes N-1; migration-free (single-tenant default) |
| `backend/m2/optimizer.py` | rolling re-solve | `replan(event_id, observations)` merging observed actuals into twin state; every replan is a new plan through the same M3 gate | turns learning into control; cost: one more governed plan per cycle |
| `backend/audit/attestation.py` | RFC-3161 anchor | new `kind: "rfc3161-v1"`, POST the head digest to a TSA, store the token | real external anchoring; needs an outbound call (netguard) and a TSA endpoint |
| `backend/forecasting/` (training) | temporal split + walk-forward | `--time-split`, report train/test by period; keep the portable-JSON export | honest temporal generalisation; the metric will look worse and that is the point |
| `scripts/health.py` | attestation + netguard pre-flight | a check that signs+verifies a round-trip and rejects a private host | the gate catches anchor drift before a demo does |
| `frontend/` | DES disagreement badge + optimizer-mode browser project | KPI row reads `m2.des.cross_check.contradicts`; second Playwright project | the cross-check becomes visible without opening the trace |

---

## 4. Advanced features

| # | Feature | Why it elevates | Technique |
|---|---|---|---|
| AF-7 | **Rolling-horizon replan loop** | incumbents' core behaviour; ours would be governed | re-solve on a cadence, each plan gated |
| AF-8 | **Pareto explorer in the UI** | "show me the frontier, not the answer" — drag the risk weight, watch the plan change, every alternative still provenance-stamped | the frontier is already computed; this is rendering + an objective-weight re-solve |
| AF-9 | **Attestation chain-of-custody view** | auditors see "who anchored, when, verified against what" | attestations are files; a listing + verifier endpoint is small |
| AF-10 | **Chaos day** | scheduled failure injection (SAP mock 500s, ledger read-only, solver missing) proving the loop degrades the way the docs claim | the failure paths exist and are individually tested; a composed drill is the differentiator |
| AF-11 | **Counterfactual replay in the learning panel** | "what if we had executed the runner-up" quantified from the twin | twin evaluation of the runner-up plan against the observed world |

---

## 5. New additions

| # | Addition | Value | Integration |
|---|---|---|---|
| N-7 | `scripts/stress.py` (**shipped this session**) | matrix-scale determinism + admissibility evidence | exit code can gate CI optionally |
| N-8 | `backend/audit/attestation.py` + CLI (**shipped this session**) | the signed-head anchor; cron-ready | `deployment.md` gets the cron line |
| N-9 | `backend/security/netguard.py` (**shipped this session**) | one outbound-host policy for every dial-out | wired into feeds + narrative |
| N-10 | RFC-3161 TSA integration | closes AF-4 fully | the `kind` seam is ready |
| N-11 | OpenTelemetry tracing (optional) | distributed diagnosis when the store goes shared | ASGI instrumentor; keep optional |
| N-12 | `pip-audit`/`npm audit`/SBOM in the gate | supply-chain posture | roadmap §8, L-2/L-3 |

---

## 6. Verification & testing strategy

**What exists and holds (this baseline):** 439 backend (unit/contract/integration/
e2e/redteam, adversarial suites asserting attacks FAIL), 46 frontend unit + static
a11y guards, 48 real-Chromium browser tests across two viewports with axe-core,
the served-SPA CSP contract, and a ten-gate `verify.sh` as the single definition
(CI runs the same script). The stress harness adds matrix-scale determinism.

**Gaps to close, in order:**

| Layer | Add | Why |
|---|---|---|
| Browser | optimizer-mode Playwright project + spec (N-3) | the flagship panel is renderer-untested |
| Performance | `/api/state` p95 with a 100k-record ledger; `/api/audit` bound at cap | the roadmap's perf budget was measured pre-attestation era |
| Chaos | composed failure drill (AF-10) | individual failure paths are tested; composition is not |
| Property-based | ledger append/verify invariants (hypothesis) | the chain is the trust anchor; property tests beat examples |
| Security | DAST pass (zap baseline) against the API | complements the red-team suite's known-attack coverage |
| UAT | a scripted operator runbook: run demo in both provider modes, attest, verify, read the report | the "acceptance" a non-engineer can execute |

**Gates:** everything new lands *inside* `verify.sh` (one definition, two
environments) — that rule is why this baseline is trustworthy, and it should not be
bent for speed.

---

## 7. Extra — business model (grounded in the in-repo evidence)

The evidence package (`docs/evidence/`, 477 sourced records) already sketches
Layer 2 (research platform: RecoveryBench-12, ablations, GNN module) and Layer 3
(startup: BTP/OData connectors, multi-tenant SaaS, $15k–50k ACV mid-market). This
analysis sharpens the wedge: sell the **governed-execution boundary** —
"the plan a human signed, executed, receipted and provable" — as the entry SKU
(proof-of-value: run their scenario through the loop in a sandbox), with
optimization depth and the attestation/TSA story as the expansion path. The demo's
honest-labelling culture is itself the pitch to regulated buyers: nothing here
overclaims, and the docs show their working.

## 8. Extra — implementation log for this document

| Workstream | Files | Verification |
|---|---|---|
| W11 netguard | `backend/security/netguard.py`, wiring in feeds/narrative, `config/settings.yaml`, tests/unit/test_netguard.py | 21 tests incl. rebind-shaped resolution and both real callers; suite stays offline-pure |
| W12 attestation | `backend/audit/attestation.py`, `scripts/attest_ledger.py`, tests/unit/test_attestation.py | 15 tests: round-trip, six tamper classes, prefix rule, CLI incl. broken-chain and truncation refusal |
| W13 stress harness | `scripts/stress.py`, tests/unit/test_stress.py | 3 tests; the 6-cell default matrix holds determinism and admissibility |
| W14 tenant-scoped registry (closes N-1) | `backend/governance/approval.py`, `backend/orchestrator.py`, `backend/api/app.py`, ADR 0016, tests/redteam/test_tenant_isolation.py | 5 red-team tests driving the real ASGI app: a same-role, different-tenant principal is refused (403 TENANT_MISMATCH) on approve/governance-read/execute; the owning tenant completes the whole gate; the demo path with auth off is byte-identical |
| W15 optimizer-mode browser suite (closes N-3) | `frontend/playwright.optimizer.config.ts`, `frontend/e2e/optimizer.spec.ts`, npm script, verify.sh step 8 | 4 real-Chromium tests: provider badge, both M2 stages done, the Why-this-plan panel (solve, DES cross-check side by side, counterfactuals), reset semantics |
| W16 forecaster time split (closes N-4) | `scripts/train_forecasting_model.py --split time`, tests/unit/test_forecasting.py | positional 60/20/20 (earliest train, latest test); metadata says "proxy for time" rather than claiming temporal proof; the shipped default remains the documented shuffled split |
| W17 attestation pre-flight (closes L-4) | `scripts/health.py` | health signs + verifies the head and asserts a forged mac is refused, in a self-seeded two-record chain that leaves the runtime clean |

*Shipped alongside: this document, then W14–W17 (N-1, N-3, N-4 and L-4 closed).
Remaining from §2: N-2 (shared-store scaling foundation, 3–5 d), N-5 (audit
re-verify cadence), N-6 (DES contradiction KPI) and the §2.3 debt register.*
