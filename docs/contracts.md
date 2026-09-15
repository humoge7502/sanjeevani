# Shared contracts

**Version 1.0.0 — frozen at architecture lock.**

These six objects are the entire integration surface between the three members.
They live in `backend/contracts/shared.py` and are enforced by
`tests/contract/test_shared_contracts.py`.

## Change protocol — read before touching a field

A shared contract is a promise to two other people. Breaking one silently is the
single most expensive mistake available in this repository.

**Additive changes** (new optional field with a default) — allowed, no
coordination needed. Existing consumers keep working because
`extra="forbid"` only rejects *unknown* keys on input, and new fields are always
optional.

**Breaking changes** (rename, retype, remove, make required) — require all five:

1. **Document it** in this file, in the contract's own section, with the reason.
2. **Version it** — bump `CONTRACT_VERSION` and, if the old shape must keep
   working, add a `vN` model rather than mutating the current one.
3. **Identify every consumer.** Grep for the field name plus the model name. In
   practice: `backend/agents/*`, `backend/governance/*`, `backend/sap/*`,
   `backend/orchestrator.py`, `frontend/src/lib/types.ts`, `docs/`.
4. **Update the contract tests** — `REQUIRED_KEYS` in
   `tests/contract/test_shared_contracts.py` is the machine-readable version of
   this document. If you changed a contract deliberately, that dict changes too.
5. **Run `python -m pytest -m contract && bash scripts/verify.sh`** and say in the
   commit message why the break was unavoidable.

### Why `extra="forbid"`

Every contract model uses `ConfigDict(extra="forbid")`. A misspelled or
unexpected key raises at the boundary instead of being silently dropped and
producing a plausible-but-wrong number three layers downstream. Schema drift
should be loud.

---

## 1. `Event` — A1/A2 → everyone

```json
{
  "event_id": "EVT-001",
  "type": "COLD_CHAIN_EXCURSION",
  "severity": "HIGH",
  "confidence": 0.96,
  "timestamp": "2026-09-30T06:00:00Z",
  "source": "IoT"
}
```

| Field | Type | Notes |
|---|---|---|
| `event_id` | string | Assigned deterministically: severity desc, then type, then target |
| `type` | string | Taxonomy value; open for future feeds |
| `severity` | enum | `LOW \| MEDIUM \| HIGH \| CRITICAL` |
| `confidence` | float `[0,1]` | **Deterministic** score, never an LLM output |
| `timestamp` | ISO-8601 string | Must parse |
| `source` | string | Primary source **class** |

Produced by: A1 (candidate) → A2 (verified). Consumed by: M2, M3, the UI.
Verified in the hero run as `EVT-001` (excursion, confidence 0.9603) and
`EVT-002` (port closure, confidence 0.8742).

## 2. `Impact` — A3 → M2

```json
{
  "event_id": "EVT-001",
  "affected_nodes": [],
  "affected_shipments": [],
  "revenue_at_risk": 0,
  "stockout_probability": 0,
  "service_level_risk": 0
}
```

| Field | Type | Notes |
|---|---|---|
| `event_id` | string | The primary (highest-severity) event |
| `affected_nodes` | string[] | Union of nodes on disrupted lanes and descendants of blocked nodes |
| `affected_shipments` | string[] | Consignments on disrupted lanes, or explicitly excursed |
| `revenue_at_risk` | float ≥ 0 | USD |
| `stockout_probability` | float `[0,1]` | Value-weighted across affected consignments |
| `service_level_risk` | float `[0,1]` | Expected service-level breach, in points |

**Additive fields (optional, safe for v1 consumers):** `affected_products`,
`scenario_id`, `horizon_days`, `related_event_ids`, `trace`, `contract_version`.

`trace` is the traceability layer: per-consignment arithmetic, market breakdown,
graph propagation record, the config keys that supplied each threshold, and the
label `"deterministic"`. Every number in the UI is reachable from this object.

## 3. `RecoveryPlan` — M2 → M3

```json
{
  "plan_id": "PLAN-001",
  "strategy": "REROUTE_MUMBAI_AIR",
  "cost": 0,
  "service_level": 0,
  "resilience_score": 0,
  "temperature_risk": 0,
  "recovery_time_hours": 0,
  "compliance_status": "PENDING"
}
```

| Field | Type | Notes |
|---|---|---|
| `plan_id` | string | Unique per plan |
| `strategy` | string | e.g. `REROUTE_MUMBAI_AIR`, `WAIT`, `EMERGENCY_SOURCE` |
| `cost` | float ≥ 0 | Validation rejects negatives at the boundary |
| `service_level` | float `[0,1]` | Post-recovery |
| `resilience_score` | float `[0,1]` | 0 fragile → 1 resilient |
| `temperature_risk` | float `[0,1]` | Ceiling checked by `POL-TEMP-001` |
| `recovery_time_hours` | float ≥ 0 | Bounded by `POL-PARAM-001` |
| `compliance_status` | enum | `PENDING \| PASSED \| FAILED \| REVIEW`; **M3 owns this value**, M2 supplies `PENDING`. The frozen schema in [`docs/evidence/contracts/`](evidence/contracts/) enumerates `PENDING/PASSED/FAILED`; `REVIEW` is an additive extension for a plan that passes every rule but carries a warning a human must read. |

**Additive fields:** `event_id`, `impacted_skus`, `impacted_lanes`, `rationale`,
`alternatives`, `source` (`optimizer \| fixture`), `contract_version`.

`source="fixture"` is a **disclosure obligation**, not decoration: the UI renders
it as a visible badge so a fixture can never be mistaken for an optimizer result.

## 4. `Approval` — the M3 gate

```json
{
  "plan_id": "PLAN-001",
  "approved": true,
  "approved_by": "Meera",
  "timestamp": "2026-09-30T06:00:00Z"
}
```

Only the approval state machine creates one of these, and only from
`APPROVAL_REQUIRED`, and only when the actor's role matches the plan's required
role. **Additive fields:** `decision`, `role`, `rationale`, `contract_version`.

## 5. `ExecutionReceipt` — M3 → SAP-shaped systems

```json
{
  "plan_id": "PLAN-001",
  "ibp": "SUCCESS",
  "tm": "SUCCESS",
  "ariba": "SUCCESS",
  "timestamp": "2026-09-30T06:00:00Z"
}
```

Statuses are `SUCCESS \| FAILED \| SKIPPED`. `SKIPPED` is a first-class outcome,
not an error: after a failure the remaining steps are marked skipped rather than
attempted, so a partial execution is described honestly.

**Additive fields:** `execution_id`, `correlation_id`, `actions`, `mock`,
`contract_version`. `mock` is always `true` in this build.

## 6. `Outcome` — A6 → learning

```json
{
  "plan_id": "PLAN-001",
  "predicted": {},
  "actual": {},
  "delta": {},
  "learning_note": ""
}
```

**Additive fields:** `event_id`, `calibration_signals`, `reconciliation_status`
(`RECONCILED \| PARTIAL \| PENDING`), `retraining_performed`,
`contract_version`.

`retraining_performed` defaults to and is hard-wired `false`. This is a
deliberate honesty constraint: the system emits calibration signals for human
review and does not pretend to have retrained anything.

---

## Verification

```bash
python -m pytest -m contract -v
```

The suite asserts, for each model:

* the required field set matches the specification exactly;
* unknown fields are rejected;
* the specification's literal JSON examples validate;
* invalid values (negative cost, out-of-range probabilities, unknown statuses)
  are rejected;
* **the live pipeline's output validates against the contract** — events, impact,
  plan, approval, receipt and outcome all round-trip from a real run.
