# Security threat model (STRIDE)

**Scope:** M3's governance and execution plane, its API, the SAP-shaped mocks,
the audit ledger, and the demo's reliability surface.

**Threat model status:** reviewed against the implementation. Every finding below
is either **mitigated** (with the test that proves it), **accepted** (with a
reason), or **deferred** (with the production requirement named). Nothing is
silently unaddressed.

**Honest framing.** This is a hackfest prototype with no authentication. Several
findings are therefore accepted *for the MVP* and would be blocking for a real
deployment. That is stated here rather than elided, because a threat model that
concludes "no issues" is a threat model nobody read.

---

## S — Spoofing identity

| # | Threat | Status | Control |
|---|---|---|---|
| S1 | An actor claims an approver identity by passing another `actor_id` | **Accepted for MVP** | There is no authentication. `resolve_actor()` (`backend/governance/approval.py:204`) maps an id to a role and is the single identity seam. Production requires OIDC + a directory lookup; the change is confined to that one function. |
| S2 | An actor invents an id with no role and approves | **Mitigated** | Unknown ids resolve to `role: None`, and `role_satisfies(None, "coo")` is `False`. Asserted by `test_a04_unknown_actor_cannot_approve`. |
| S3 | A senior role implies a narrower one ("the COO can approve anything") | **Mitigated by design** | Role matching is an **exact equality** check (`backend/governance/approval.py:218`). A more senior role does *not* satisfy a narrower requirement, so the model refuses more often than a seniority guess would — the safe direction. `test_role_matching_is_exact_not_seniority_based`. |

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
| D1 | Flood `/api/demo/run` | **Deferred** | No rate limiting. The API is bound to `127.0.0.1` only, which is the actual mitigation in this build. Production needs rate limiting and auth. |
| D2 | Huge payload to the Ariba mock | **Mitigated** | The documented 500-suppliers-per-call limit is enforced (`test_ariba_mock_enforces_the_documented_batch_limit`). |
| D3 | Malformed plan crashes the policy engine | **Mitigated** | Every evaluator is wrapped; an exception becomes an `ERROR` outcome which **blocks** (`test_policy_engine_fails_closed_on_evaluator_exception`). A missing plan stops the loop with a legible refusal rather than a crash (`test_a10b_missing_recovery_plan_stops_the_loop_legibly`). |
| D4 | Huge or absurd numeric values | **Mitigated** | Contracts bound every ratio to `[0,1]` and every cost to `>= 0`; `POL-PARAM-001` bounds `recovery_time_hours` to 720. `test_a10_physically_impossible_plan_fails_policy` covers a plan that is contract-valid but physically absurd. `test_a15_extra_fields_are_rejected_by_the_contract` covers schema abuse. |
| D5 | Ledger grows unbounded and freezes the UI | **Deferred** | A clean hero run writes **41** records, which renders and scrolls fine. The `scripts/health.py` path writes 43 because it also attempts an execution *before* approval and records the refusal — a useful illustration of R3. A long-lived deployment needs pagination or windowing. |
| D6 | Unbounded ledger file / disk exhaustion | **Accepted for MVP** | Records are on the order of a kilobyte and a clean run writes 41. Production requires rotation with the chain preserved. |

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
| E14 | Present a fake optimized plan as M2's work | **Mitigated** | The provider is a fixture and says so everywhere. `test_a18_optimizer_mode_is_explicitly_not_implemented`, `test_a18b_unavailable_provider_fails_closed`. |

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
