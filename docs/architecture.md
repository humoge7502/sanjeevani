# Architecture

## 1. The loop, and who owns each stage

```
        M1  (this repo)                          M2  (not in this repo)         M3  (this repo)
 ┌──────────────────────────────┐   ┌──────────────────────────────┐   ┌──────────────────────────────┐
 │  A1        A2        A3      │   │  A4                          │   │  A5                    A6    │
 │ Sense → Verify → Impact      │   │ Simulate → Optimize          │   │ Policy → Execute → Audit     │
 │  │       │         │         │   │  │            │              │   │  │         │          │      │
 │  signals events   impact      │   │ impact    RecoveryPlan       │   │  RecoveryPlan        ledger  │
 └──┼───────┼─────────┼─────────┘   └──┼────────────┼─────────────┘   └──┼─────────┼──────────┼──────┘
    │       │         │                │            │                    │         │          │
    └───────┴─────────┴─── Impact ─────┘            │                    │         │          │
                                       RecoveryPlan ┘────────────────────┘         │          │
                                                                    ExecutionReceipt ──────► Outcome
```

The frozen integration objects are the *only* things that cross a boundary. See
[`contracts.md`](contracts.md).

## 2. Layer responsibilities

| Layer | Path | Responsibility | Must NOT do |
|---|---|---|---|
| Contracts | `backend/contracts/` | Six frozen Pydantic models. Field names are the integration surface. | Compute anything |
| Config | `backend/config.py` | Resolve paths, thresholds, rulebook, frozen clock. | Contain business logic |
| Network | `backend/graph/` | Seed data → node/lane/SKU/shipment model → derived directed graph. | Interpret events |
| M1 agents | `backend/agents/` | Sense, verify, generate scenarios, compute impact. | Execute, approve, own M2 math |
| M2 boundary | `backend/m2/` | Return a `RecoveryPlan` from a provider. | Optimize, simulate, sell a fixture as an optimizer |
| Governance | `backend/governance/` | Policy evaluation, compliance gate, approval state machine. | Be bypassable from the API or UI |
| SAP mocks | `backend/sap/` | Idempotent, deterministic, API-shaped posts + receipt. | Contact a real tenant |
| Audit | `backend/audit/` | Append-only hash-chained ledger. | Expose update/delete |
| Learning | `backend/learning/` | Predicted vs actual, calibration signals. | Claim a model was retrained |
| API | `backend/api/` | HTTP surface + error contract. **The security boundary.** | Trust the client |
| Orchestrator | `backend/orchestrator.py` | Sequence the loop, write the ledger. | Compute a number it does not own |
| Frontend | `frontend/` | Render server truth. | Decide what is permitted |

## 3. M1 — upstream intelligence

```
data/events/hero_feeds.yaml      data/telemetry/cold_chain.csv
        │                                    │
        ▼                                    ▼
   A1 SENSING                          (device trace)
   raw signal → classify → score             │
        │  candidate events + provenance     │
        ▼                                    │
   A2 VERIFICATION ◄──────────────────────────┘
   RULE-2SOURCE   claims need ≥ 2 fresh source CLASSES
   RULE-MEASURE   a device claim must be reproduced from the trace
   dedup          collapses restatements WITHIN a source class only
        │  verified Event (confidence, severity, provenance)
        ▼
   A2 SCENARIO
   parameters clamped against config/scenarios.yaml
   weights(H) ∝ confidence × exp(−|H − duration| / max(duration,1))
        │  3-day / 10-day / 30-day
        ▼
   A3 NETWORK IMPACT
   blocked nodes → lanes touching them → reachability closure
   per consignment: remaining corridor fraction → delay → outage gap → P(stockout)
        │
        ▼
   Impact contract  +  trace{}   (every number carries its inputs and formula)
```

### Why two different verification rules

A news claim and a port-authority advisory agreeing is **independent
corroboration**. Two news outlets saying the same thing is **one report
restated**. Collapsing them together would destroy the very signal the
credibility check depends on — so dedup is scoped to a single source class and
the two-source rule counts distinct classes.

A direct device measurement is a different kind of evidence, so it gets a
different rule: the claim ("peak 11.4 °C") is re-derived from the raw samples and
must agree within tolerance. A device-only claim with no trace does **not**
verify.

### The stockout model

Per consignment, per horizon `H`:

```
remaining_fraction = corridor hops remaining / corridor hops total
delay_days         = transit_delay_days × remaining_fraction
gap_days           = min(H, duration_days + delay_days)
shortfall_days     = max(0, gap_days − coverage_days[sku])
P(stockout)        = 1 − exp(−shortfall_days / service_recovery_days)
condemn_ratio      = see backend/agents/telemetry.condemn_ratio
value_at_risk      = value × [ P(stockout) × (1 − condemn) + condemn ]
```

The horizon **cap** in `gap_days` is the point, not an artefact: it is why a
3-day view says the existing cover absorbs the shock and the 10-day view says it
does not. That divergence is what the product exists to surface.

### Why the numbers are reproducible

* No sampling. No Monte Carlo. No model inference. Pure arithmetic over seed data.
* `networkx.descendants` for propagation — deterministic for a fixed graph.
* The scenario clock is **frozen** in demo mode (`config/settings.yaml`), so
  timestamps are stable and therefore so are the audit hashes.
* Verified by `tests/e2e` — two full runs produce an identical ledger hash chain.

## 4. M2 — the boundary

M2 owns the digital twin and the optimizer. **Neither is implemented here.** M3
consumes a `RecoveryPlan` through a two-method interface:

```python
class RecoveryPlanProvider(Protocol):
    def plan_for_event(self, event_id, scenario_id=None) -> RecoveryPlan: ...
    def ranked_plans(self, event_id, scenario_id=None) -> list[RecoveryPlan]: ...
```

`FixtureRecoveryPlanProvider` implements it with hand-set data labelled
`source="fixture"` on every contract object. Swapping in the real optimizer is a
one-class change; nothing in M3 moves. See [`mock-boundaries.md`](mock-boundaries.md).

## 5. M3 — governance and execution

```
RecoveryPlan
    │
    ▼
POLICY ENGINE (config/policies.yaml — 10 rules)
    │  each rule: threshold, actual, arithmetic, provenance label
    ▼
COMPLIANCE GATE
    │  any FAIL or ERROR  → COMPLIANCE_BLOCKED  → loop stops, no human is asked
    │  PASS               → APPROVAL_REQUIRED
    ▼
HUMAN APPROVAL  (role-checked against the plan's required role)
    │  approve → APPROVED  |  reject → REJECTED  |  review → REVIEW_REQUESTED
    ▼
EXECUTION ORCHESTRATOR
    │  IBP → TM → Ariba, one correlation id, idempotency key per action
    │  failure → stop, mark remainder SKIPPED, issue an honest partial receipt
    ▼
EXECUTION RECEIPT  →  LEDGER  →  OUTCOME  →  CALIBRATION SIGNAL
```

### The state machine

States: `RECEIVED · POLICY_CHECKING · POLICY_BLOCKED · COMPLIANCE_CHECKING ·
COMPLIANCE_BLOCKED · APPROVAL_REQUIRED · REVIEW_REQUESTED · APPROVED · REJECTED ·
EXECUTING · EXECUTED · FAILED · AUDITED · COMPLETED`

The transition table lives in `backend/governance/approval.py` and is the single
source of truth. Two properties are asserted by tests:

1. `EXECUTING` is reachable **only** from `APPROVED`.
2. Exactly one module (`orchestrator.py`) transitions a plan into `APPROVED`.

### Failure semantics

| Failure | Behaviour |
|---|---|
| Policy rule FAILs | `COMPLIANCE_BLOCKED`. No approval request is created. |
| Policy rule ERRORS | Treated as FAIL. A broken engine is never a permission. |
| Rule has no evaluator | Treated as FAIL. An unimplemented rule cannot pass. |
| Actor lacks the required role | `403 UNAUTHORIZED_APPROVER`, transition logged as DENIED. |
| Execution attempted early | `409 EXECUTION_BLOCKED` with the current state in the detail. |
| Second approve | `409 ALREADY_DECIDED`. |
| Repeated execute | Original receipt replayed; **no second post**. |
| SAP step fails | Stop, mark remainder `SKIPPED`, issue a partial receipt, never retry silently. |
| M2 unavailable | Loop stops with a legible message. **No plan is invented.** |
| Restart | Approvals are process-local, so authority is *lost*, not carried forward. |

## 6. Audit ledger

Append-only JSONL, one record per loop stage, SHA-256 chained over the canonical
record body.

```
{ seq, stage, actor, action, result, ids, detail, timestamp, prev_hash, hash }
```

Immutability is an API property first (there is no update or delete method), and
detectability second (`verify_chain()` recomputes the chain, so an out-of-band
edit is caught). It is explicitly **not** a blockchain and does **not** resist an
actor with filesystem write access — that limitation is stated in the code, the
API response and the threat model rather than implied by a hash column.

## 7. Frontend

React 18 + TypeScript + Vite. Dependency count is deliberately minimal: React and
that is it at runtime. The network map is hand-drawn SVG because the graph layout
is fixed and deterministic — a force-directed library would add jitter, weight
and non-determinism in exchange for nothing.

The UI derives *presentation* state (which loop stage is active, how to colour a
probability) and never *permission* state. Every governance decision round-trips
to the server.

See [`design-system.md`](design-system.md).

## 8. Extension points

Designed for, not built:

| Future | Seam |
|---|---|
| Real SAP connectors | `backend/sap/mocks.py` — same request/response shapes, swap the transport |
| Real optimizer | `backend/m2/recovery_plan_provider.py` — implement the Protocol |
| Real signal feeds | `backend/agents/sensing.load_feed` — same `RawSignal` shape |
| Real outcome feedback | `backend/learning/reconcile.load_observations` — same observation shape |
| Authentication | `resolve_actor()` is the single identity seam |
| Append-only storage | `DecisionLedger._write` is the single persistence seam |
| Richer learning | `backend/learning/reconcile` — the calibration layer is already separate from the decision layer |
