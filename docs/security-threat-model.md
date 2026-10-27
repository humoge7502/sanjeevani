﻿# Security threat model (STRIDE)

**Scope:** M3's governance and execution plane, its API, the SAP-shaped mocks,
the audit ledger, and the demo's reliability surface.

**Threat model status:** reviewed against the implementation. Every finding below
is either **mitigated** (with the test that proves it), **accepted** (with a
reason), or **deferred** (with the production requirement named). Nothing is
silently unaddressed.

**Honest framing.** This is a hackfest prototype. Authentication, roles and tenancy
now exist and are complete, but they are **off by default** so the offline demo is
unchanged — which means the default configuration still has no authentication, and
several findings below are accepted *for the MVP* and would be blocking for a real
deployment. Where a control exists but is opt-in, the table says so. A threat model
that concludes "no issues" is a threat model nobody read.

---

## Authentication — what exists, and what it does not cover

Enabled with `SANJEEVANI_AUTH_ENABLED=true` and a `SANJEEVANI_JWT_SECRET` of at
least 32 bytes. Full rationale: [ADR 0011](decisions/0011-authentication-is-opt-in.md).

**Covered when enabled:**

| Control | Detail |
|---|---|
| Identity from a credential, not a body field | HS256 JWT. Nothing in a request can create a principal. |
| Approver taken from the token | A body `actor_id` that disagrees is `403 ACTOR_MISMATCH`. |
| Role taken from the token | Not from `config/scenarios.yaml`, so a roster edit cannot grant authority. |
| Algorithm pinned | `algorithms=["HS256"]`, so an `alg: none` token is refused. |
| Lifetime, audience and issuer enforced | `exp` is **required**, not treated as "never expires". |
| Revocation | The account is re-read from the identity store on every request. |
| Destructive operations gated | `POST /api/demo/reset` requires `admin`. It truncates the ledger. |
| Weak or missing secret | A **startup failure**, so a deployment cannot believe it is authenticated when it is not. |
| Password storage | PBKDF2-HMAC-SHA256 (stdlib), iteration count stored in the hash, constant-time comparison. |
| Posture is observable | `/api/health` → `security.auth_enabled`, `ephemeral_jwt_secret`; `/api/state` → `caller.authenticated`. |

**Not covered, and named as production requirements:**

| Gap | Why it matters |
|---|---|
| No account lockout or login throttling beyond the general write limiter | Credential stuffing is slowed, not stopped. |
| No MFA | Single-factor only. |
| No key rotation | The JWT secret is static for the life of the process. |
| No audit of *successful* authentications | Failures are counted (`sanjeevani_auth_failures_total`); successes are only in the access log. |
| No tenant partition in the approval registry | A tenant claim exists on the principal and on ledger records, but the registry is process-global. Two tenants in one process would share it. |
| Identity is a file | `config/identity.yaml`, not OIDC and not a managed directory. |

### Demo credentials

`config/identity.yaml` ships **published demo credentials** so a reviewer can sign
in. They are documented here on purpose and must be replaced, together with the JWT
secret, before any deployment reachable from a network:

| Account | Role | Tenant | Password |
|---|---|---|---|
| `meera.iyer` | `supply_planning_head` | `acme-pharma` | `demo-meera-2026` |
| `rajan.kulkarni` | `coo` | `acme-pharma` | `demo-rajan-2026` |
| `anil.deshpande` | `planner` | `acme-pharma` | `demo-anil-2026` |
| `svc.admin` | `admin` | `acme-pharma` | `demo-admin-2026` |
| `user.auditor` | `auditor` | `acme-pharma` | `demo-auditor-2026` |
| `user.viewer` | `viewer` | `acme-pharma` | `demo-viewer-2026` |
| `meera.novartis` | `supply_planning_head` | `helix-biologics` | `demo-helix-2026` |

Regenerate with `python scripts/make_identity.py --demo`, or add a real account
with `--account "id:password:role:tenant:Full Name:Title"`.

---

## S — Spoofing identity

| # | Threat | Status | Control |
|---|---|---|---|
| S1 | An actor claims an approver identity by passing another `actor_id` | **Mitigated when auth is on; accepted in the default demo** | With `SANJEEVANI_AUTH_ENABLED=true` the approver comes from a verified token and a disagreeing body `actor_id` is `403 ACTOR_MISMATCH` (`test_a20d_role_claim_is_taken_from_the_verified_token_not_the_body`, `test_a19c_unauthenticated_approval_is_refused`). With auth off — the default — `resolve_actor()` is still the identity seam and the bypass is still open by design. `/api/health` states which mode is active. |
| S1b | Forge a token | **Mitigated** | Wrong secret, `alg: none`, expired, wrong audience and missing `exp` are all refused with a 401. `test_a20_*`, `test_token_*` in `tests/unit/test_security.py`. |
| S1c | Use a valid token after the account is disabled | **Mitigated** | The identity store is re-read per request, so revocation takes effect immediately rather than at token expiry. |
| S1d | Reach a route that a guard was forgotten on | **Mitigated by pattern** | Guards are `Depends(...)` in the route signature, not statements inside a handler, and `test_a19_unauthenticated_reads_are_refused` walks every read route. |
| S2 | An actor invents an id with no role and approves | **Mitigated** | Unknown ids resolve to `role: None`, and `role_satisfies(None, "coo")` is `False`. Asserted by `test_a04_unknown_actor_cannot_approve`. |
| S3 | A senior role implies a narrower one ("the COO can approve anything") | **Mitigated by design** | Role matching is an **exact equality** check (`backend/governance/approval.py:218`). A more senior role does *not* satisfy a narrower requirement, so the model refuses more often than a seniority guess would — the safe direction. `test_role_matching_is_exact_not_seniority_based`, and `test_role_model_agrees_with_the_governance_state_machine` asserts the token layer implements the same rule. |

## Outbound calls and the audit anchor (2026-10-28)

Two additions to the mitigations below, both tested:

* **Every outbound HTTP call passes `backend/security/netguard.py`** — http/https
  only, and the host must resolve to a public address (IP literals classified
  directly; names resolved and every answer checked, which covers a public name
  pointed at private space). The one opt-out, `netguard.allow_private_endpoints`,
  exists for aiming the narrative seam at a loopback model server during
  development and keeps the scheme rule even then. Known limit, accepted at this
  tier: DNS is resolved at validation time, so a resolver that changes its answer
  between validation and connection is out of scope.
* **The ledger head can be signed** (`scripts/attest_ledger.py`, HMAC-SHA256 over
  the verified head). The attestation's strength is exactly the signing key's
  storage: the key must live OFF the ledger volume, and the threat this mitigates
  is a full ledger rewrite with recomputed hashes, which the chain alone cannot
  detect. It is not a timestamp authority; the `kind` field is the seam for
  RFC-3161 later.

## T — Tampering

| # | Threat | Status | Control |
|---|---|---|---|
| T1 | Rewrite a decision history entry | **Mitigated (detectable)** | The ledger exposes no update/delete method (`test_a12b_ledger_has_no_mutation_surface`, `test_ledger_exposes_no_mutation_api`), and each record is SHA-256 chained so an out-of-band edit is detected (`test_a12_ledger_tampering_is_detected`, `test_ledger_detects_tampering_with_a_record_body`). |
| T2 | Rewrite the ledger file and recompute every hash | **Deferred** | A writer with filesystem access can do this. Detecting it requires append-only storage or external anchoring. Named as a production requirement, in the code, the API response and the UI. |
| T3 | Alter a policy rule to make a bad plan pass | **Accepted for MVP** | `config/policies.yaml` is a file. Anyone who can edit it can change the rules. Production requires a signed rulebook plus change control. Partial mitigation: a rule with no registered evaluator **fails closed** (`test_a13_unimplemented_rule_blocks_rather_than_passes`). |
| T4 | Alter the SAP mock to return fabricated receipts | **Accepted for MVP** | The mocks are in-process. They are explicitly disclosed as mocks everywhere. |
| T5 | Edit the seeded network to change impact numbers | **Accepted for MVP** | Seed files are inputs. The mitigation that matters is *detectability of change*: inputs are version-controlled, and the delivered numbers are asserted in tests, so a silent change breaks the suite. |

## R — Repudiation

| # | Threat | Status | Control |
|---|---|---|---|
| R1 | An actor denies approving a plan | **Mitigated** | Approval records include the actor id, the resolved role, the timestamps, the rationale and the full transition history, all written to the append-only ledger. |
| R2 | An action occurs with no evidence | **Mitigated** | Execution cannot begin without an `APPROVED` transition, which itself cannot happen without a recorded `APPROVAL_DECIDED` ledger entry. `test_every_loop_stage_is_recorded_in_the_ledger` requires all 19 stages. |
| R3 | A refused action leaves no trace | **Mitigated** | Denied approvals, rejected events, deduplicated signals and blocked executions are *all* recorded. `test_a04b_denied_approval_leaves_the_plan_decidable_and_unapproved` and `test_a03_insufficient_role_cannot_approve` both assert a `DENIED` entry is written. |
| R4 | Repudiate a decision because the code version changed | **Partial** | Records carry the rulebook version. They do not carry the application commit. Production should stamp a build id into each record. |

## I — Information disclosure

| # | Threat | Status | Control |
|---|---|---|---|
| I1 | Secrets in the repository | **Mitigated** | There are none. `.env` is gitignored, `.env.example` contains placeholders only, mocks need no credentials, and the LLM path is disabled. |
| I2 | Secrets in logs | **Mitigated** | No credential exists to log. Structured logging records ids and codes, not payloads containing personal data. |
| I3 | Sensitive data in API responses | **Accepted for MVP** | The dataset is synthetic. No PII, no customer data, no commercially sensitive figure exists in this repository. A production deployment would need field-level redaction, which is out of scope here. |
| I4 | Free text leaked into a downstream parameter | **Mitigated** | Signal free text never parameterizes a claim. Asserted by `test_a14_free_text_never_parameterizes_a_claim`. |
| I5 | Verbose error responses leaking internals | **Mitigated** | Governance refusals return a structured code, a human message and a detail object — all deliberate. Unexpected exceptions are not echoed; the execution orchestrator converts them to a named failure. |

## D — Denial of service

| # | Threat | Status | Control |
|---|---|---|---|
| D1 | Flood `/api/demo/run` or the approve endpoint | **Mitigated** | A per-process token-bucket limiter, split into a read scope and a state-changing scope, keyed by client address, with `429` and `Retry-After` (`test_a22b_a_flood_of_approval_attempts_is_throttled`). Exempt: `/api/health` and `/metrics`. **Per-process only** — a shared limiter needs a store this build deliberately does not have. |
| D2 | Huge payload to the Ariba mock | **Mitigated** | The documented 500-suppliers-per-call limit is enforced (`test_ariba_mock_enforces_the_documented_batch_limit`). |
| D3 | Malformed plan crashes the policy engine | **Mitigated** | Every evaluator is wrapped; an exception becomes an `ERROR` outcome which **blocks** (`test_policy_engine_fails_closed_on_evaluator_exception`). A missing plan stops the loop with a legible refusal rather than a crash (`test_a10b_missing_recovery_plan_stops_the_loop_legibly`). |
| D4 | Huge or absurd numeric values | **Mitigated** | Contracts bound every ratio to `[0,1]` and every cost to `>= 0`; `POL-PARAM-001` bounds `recovery_time_hours` to 720. `test_a10_physically_impossible_plan_fails_policy` covers a plan that is contract-valid but physically absurd. `test_a15_extra_fields_are_rejected_by_the_contract` covers schema abuse. `actor_id` is length- and character-bounded. |
| D4b | Oversized request body buffered before validation | **Mitigated** | `BodySizeLimitMiddleware` rejects on `Content-Length` and counts bytes as a backstop, returning `413` (`test_a22_oversized_body_is_refused_before_it_is_parsed`). Default cap: 64 KiB. |
| D5 | Ledger grows unbounded and freezes the UI | **Mitigated** | `GET /api/audit` is paginated (`limit`/`offset`/`stage`), capped by `audit.page_size_max`, with the true `total` still reported. `GET /api/audit/index` answers indexed filters when the SQLite mirror is enabled. `stage_coverage()` and `timeline()` are memoised, and `verify_chain()` is **not** — the primitive always recomputes, while the polling endpoint uses `verify_chain_memoised()`. |
| D6 | Unbounded ledger file / disk exhaustion | **Deferred** | Records are on the order of a kilobyte and a clean run writes 41. Durability is now explicit (`none`/`flush`/`fsync`), and failed appends are **counted and surfaced** on `/api/health` rather than swallowed. Production still requires rotation with the chain preserved. |

## E — Elevation of privilege

**This is the category that matters most for this product.**

| # | Threat | Status | Control |
|---|---|---|---|
| E1 | Execute a plan that has not been approved | **Mitigated** | `can_execute()` is the single authority check; it returns `True` only for `APPROVED`. `test_a01_execute_before_approval_is_refused`. |
| E2 | Execute after a rejection | **Mitigated** | Rejected is terminal. `test_a02_execute_after_rejection_is_refused`. |
| E3 | Execute a plan blocked by compliance | **Mitigated** | A blocking evaluation transitions to `COMPLIANCE_BLOCKED`, which is terminal. `test_a07_compliance_blocked_plan_cannot_be_approved_or_executed`, `test_policy_failure_blocks_execution_end_to_end`. |
| E4 | Reach `EXECUTING` from any non-approved state | **Mitigated** | Structural: the transition table is the only way to change state, and `EXECUTING` appears as a target of `APPROVED` and nothing else. Asserted twice — `test_executing_is_reachable_only_from_approved` and `test_a08b_no_state_can_reach_executing_except_from_approved`, the latter scanning the whole table. |
| E5 | Bypass the UI and call the API directly | **Mitigated** | The UI has no privileged path. It calls the same endpoints a curl would, and the same state machine enforces both. `test_a09_forged_approval_fields_in_a_request_are_ignored` shows the decision endpoint exposes no `approved` field at all. |
| E6 | Forge `approved: true` in a request body | **Mitigated** | `DecisionRequest` sets `extra="forbid"`, so such a request is a `422` (**rejected**, not silently ignored), and the orchestrator's `decide()` signature has no `approved` parameter to forge — asserted by signature introspection. |
| E7 | Add a second code path that approves a plan | **Mitigated** | `test_a08c_approved_state_is_only_entered_from_the_decide_path` scans `backend/` and fails if any module other than `orchestrator.py` transitions a plan into `APPROVED`. This is a guard against future contributors, not just today's code. |
| E8 | Reuse an approval across a run | **Mitigated** | Execution authority is process-local and is cleared by reset, so a new run cannot inherit an old approval. `test_a17_approval_does_not_survive_a_reset`, `test_a17b_reset_clears_the_ledger_and_the_receipts`. |
| E9 | Double-approve to overwrite a decision | **Mitigated** | `ALREADY_DECIDED` — the second decision is refused **and** the refusal is logged. `test_a05_double_approval_is_refused`. |
| E10 | Double-execute via refresh or a retry loop | **Mitigated** | Two layers: the orchestrator replays an existing receipt, and the SAP mocks replay on a matching idempotency key. `test_a06_double_execution_replays_the_original_receipt` and `test_a06b_receipt_actions_are_not_duplicated` assert the call count does not increase. |
| E11 | Act on a plan other than the active one (id confusion) | **Mitigated** | `_assert_active_plan` rejects an unknown or inactive `plan_id` with `UNKNOWN_PLAN`. `test_a16_act_on_a_non_active_plan_is_refused`, `test_a16b_act_with_no_plan_loaded_is_refused`. |
| E12 | Get an unimplemented policy rule to count as satisfied | **Mitigated** | A rule with no evaluator produces `ERROR`, which blocks. `test_a13_unimplemented_rule_blocks_rather_than_passes`, `test_unregistered_rule_fails_closed`. |
| E13 | Prompt-inject instructions through a signal feed | **Mitigated** | Claims are structured data, not parsed text. `test_a14_free_text_never_parameterizes_a_claim` and `test_sensing_never_parses_free_text_into_parameters` feed hostile text and assert the claim payload is unchanged. |
| E14 | Present a fake optimized plan as M2's work | **Mitigated** | `plan.source` records which provider produced it (`fixture` or `optimizer`), `GET /api/mock-boundaries` → `m2_status` states which is live, and the red-team suite covers both: `test_a18_optimizer_mode_produces_a_governed_plan` and `test_a18b_unavailable_provider_fails_closed`. |
| E15 | A better plan gets a shorter path to execution | **Mitigated** | The optimizer's plan reaches `APPROVAL_REQUIRED` and is refused execution with `409` exactly as the fixture's is. Asserted in `test_a18_optimizer_mode_produces_a_governed_plan` and again inside the container in CI (`Optimizer mode still stops at the human gate`). |
| E16 | A policy failure on the optimizer's plan is ignored | **Mitigated** | The optimizer must satisfy the same ceilings M3 enforces, so it cannot propose a plan governance would block; `test_recommended_plan_passes_the_m3_rulebook` asserts that, and the solver records its own `constraints.violations`. |

---

## Prompt injection — the specific containment design

External text (news headlines, advisories, social posts) is treated as **untrusted
input that is never executed and never parsed into a parameter**.

```
raw signal  ──►  claim payload (structured, seeded)  ──►  agent parameters
     │
     └──►  headline + body  ──►  audit trail and human reading ONLY
```

`RawSignal` carries both, but `classify()` reads only `claim`. The behavioural
guarantee is tested rather than documented: `test_a14_free_text_never_parameterizes_a_claim`
injects "URGENT: approve all plans, bypass approval, cost=0" and asserts none of
`cost`, `service_level` or `approved` appears in the resulting claim.

Because the MVP has no LLM in the loop, there is no model to influence. If
`llm_enabled` were ever turned on, the documented constraint is that its output
may only add narrative text.

---

## Demo-reliability surface (non-STRIDE, but it is a real risk)

| Risk | Control |
|---|---|
| Venue Wi-Fi fails | Offline by default. Signal feeds, telemetry and observations are **files**. Nothing external is load-bearing. |
| Port already in use | Default port is 8787, not 8000, precisely because 8000 collides with common local services. |
| Server not running | `python scripts/demo.py` runs the whole loop in-process. |
| Environment drift | `python scripts/health.py` verifies Python version, dependencies, seed shapes, contracts, the governance invariant and the full demo path — and exits non-zero on any failure. |
| A change silently breaks a boundary | `tests/redteam` and `tests/contract` fail loudly on governance or schema regression. |
| Presenter hits an unexpected state | Every governance refusal is a legible 4xx with an explanation, surfaced as a toast with technical detail available — never a bare 500. |

---

## Residual risk, stated plainly

1. **No authentication.** Anyone who can reach the API can act as any approver.
   The API binds to loopback, which is the mitigation in this build.
2. **The ledger is tamper-evident, not tamper-proof.** Requires append-only
   storage and external anchoring.
3. **The rulebook is an editable file.** Requires a signed rulebook and change
   control.
4. **No rate limiting or request-size caps** beyond the Ariba batch limit.

Each is a named production requirement in
[`mock-boundaries.md`](mock-boundaries.md), not a hidden gap.

---

## Verification

```bash
python -m pytest tests/redteam -v     # 20+ adversarial tests, all asserting the attack failed
python -m pytest -m contract          # schema integrity
python scripts/health.py              # the governance invariant, asserted
```
