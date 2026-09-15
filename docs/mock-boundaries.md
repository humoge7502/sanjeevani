# Mock boundaries

The trust question for an AI demo is not "does it work?" but "what exactly is
this showing?" This document answers that, and the API exposes the same inventory
at `GET /api/mock-boundaries` so the UI can render it and a judge can check it
against the running system.

**Four categories. They are never blurred.**

---

## REAL — implemented and running

| Capability | Where | Why it counts as real |
|---|---|---|
| Graph propagation | `backend/graph/network.py`, `Network.downstream_of` | Real `networkx` traversal over a real directed graph derived from lane waypoints |
| Network impact arithmetic | `backend/agents/impact.py` | Closed-form computation. Reproducible: two runs are bit-identical (asserted in `tests/e2e`) |
| Telemetry analysis | `backend/agents/telemetry.py` | Interpolated minutes-above-limit, trapezoidal degree-hours — computed from sample data, not asserted |
| Policy rule evaluation | `backend/governance/policy_engine.py` | 10 configurable rules, each returning its threshold, actual value and arithmetic |
| Approval state machine | `backend/governance/approval.py` | Explicit transition table; `EXECUTING` reachable only from `APPROVED` |
| Idempotent execution | `backend/sap/execution.py`, `backend/sap/mocks.py` | Idempotency keys with response replay; a refresh cannot double-post |
| Audit ledger | `backend/audit/ledger.py` | Append-only, SHA-256 chained, chain recomputable |
| FastAPI surface | `backend/api/app.py` | The actual security boundary, with structured governance refusals |
| React command center | `frontend/` | The actual UI |

## DETERMINISTIC — seeded and reproducible, but synthetic

| Item | Source | Note |
|---|---|---|
| 12-node pharmaceutical network | `data/network/nodes.yaml` | 4 suppliers (2 "China-like"), 2 plants, 3 warehouses, 2 ports, 1 customer hub |
| 6 commercial lanes | `data/network/lanes.yaml` | Exactly one Red Sea-exposed (the biologics ocean export) |
| 12 SKUs | `data/products/skus.yaml` | 2 biologics (`BIO-001`, `BIO-002`) |
| 8 in-flight consignments | `data/shipments/shipments.yaml` | 5 on the disrupted corridor, 3 on an unaffected air corridor |
| Signal feeds | `data/events/hero_feeds.yaml` | 8 scripted signals including deliberate stale / duplicate / low-confidence / single-source cases |
| Device telemetry | `data/telemetry/cold_chain.csv` | 3 traces; the hero shows a genuine 11.4 °C excursion |
| Scenario parameters | `config/scenarios.yaml` + signal claims | Clamped by A2 against configured bounds |
| Probability weights, stockout probabilities, revenue at risk | Computed | Deterministic formulas, documented in `architecture.md` |
| **Scenario clock** | `config/settings.yaml` → `freeze_clock: true` | Frozen. Timestamps are stable, so audit hashes are stable, so "reset and replay" is verifiable rather than asserted |

Everything synthetic is *invented for a hackfest prototype*. No real company's
network, prices, inventories or shipments are represented.

## SIMULATED — stands in for a live data source

| Simulated | Stands in for | Replacement seam |
|---|---|---|
| IoT cold-chain telemetry | A Tive/Sensitech-class reefer device feed | `backend/agents/telemetry.py` — same JSON shape would be emitted |
| Post-execution observations | TM ETA confirmations, IBP actual key figures, QA disposition | `data/events/outcome_observations.yaml` → `backend/learning/reconcile.load_observations` |
| Sanctions / trade screening lists | A maintained denied-party and corridor feed | `config/policies.yaml` → `POL-SANCT-001`, `POL-TRADE-001` params |
| Source-reliability priors | A learned per-source credibility model | `data/events/hero_feeds.yaml` → `source_reliability` (a configured constant per source) |

The sanctions and trade rules are explicitly labelled `simulated_rule` in the API
payload and rendered with that badge in the compliance panel. They are not legal
advice and they are not a compliance product.

## MOCKED — API-shaped stand-ins for external systems

### SAP IBP / TM / Ariba

⚠️ **No SAP tenant is contacted. No OData service is deployed. No credential
exists.** These are in-process Python objects in `backend/sap/mocks.py`.

They are *shape-faithful*: responses use SAP's OData v4 JSON envelope
(`{"@odata.context": ..., "d": {...}}`), the Ariba surface enforces the
documented 500-suppliers-per-call limit, and field names follow the public API
families (`PlanningScenarioID`, `FreightOrderID`, `RiskExposureScore`).

Three properties the demo depends on:

* **Deterministic** — the same request produces the same document number, derived
  from a SHA-256 over the request parts. Not used for security.
* **Idempotent** — the same idempotency key replays the *original response* and
  records `REPLAYED`. This is what stops a page refresh double-booking freight.
* **Observable** — every call is logged with its request, response and correlation
  id, and surfaces in the execution panel and the ledger.

One consistency rule worth naming: the Ariba mock returns the **seeded network's**
risk score when a supplier exists in the graph, rather than an independent random
value. A mock that contradicted the network model would be indefensible in Q&A —
"your risk service says HIGH, your network says 0.18". Unknown supplier ids fall
back to a derived score and the response states which happened.

### `RecoveryPlan` — the M2 fixture

⚠️ **No MILP is solved. No digital twin is replayed. No cost is optimized.**

`backend/m2/fixtures/recovery_plans.yaml` contains hand-set plans for three
strategies (WAIT, REROUTE VIA MUMBAI + AIR, EMERGENCY SOURCE). Every plan object
carries `source="fixture"`, which the UI renders as a badge, and
`GET /api/state` includes a `plan_provider` disclosure block.

**Replacement procedure** (M3 needs no changes):

```python
# 1. Implement the Protocol in backend/m2/
class OptimizerRecoveryPlanProvider:
    def plan_for_event(self, event_id, scenario_id=None) -> RecoveryPlan: ...
    def ranked_plans(self, event_id, scenario_id=None) -> list[RecoveryPlan]: ...

# 2. Register it in get_provider() behind SANJEEVANI_PLAN_PROVIDER=optimizer
#    (currently raises NotImplementedError with an explanatory message)

# 3. Run python -m pytest -m contract && python -m pytest -m e2e
#    Nothing in M3 should need editing. If it does, the boundary leaked.
```

---

## NOT IMPLEMENTED

| Item | Why it is absent | Where it belongs |
|---|---|---|
| M2 digital twin (SimPy) | Member 2's ownership | M2 |
| Recovery optimization (Pyomo/HiGHS) | Member 2's ownership | M2 |
| Routing (OR-Tools) | Member 2's ownership | M2 |
| Real SAP connectors (BTP, OData) | Weeks of work for a story judges already reward when the shape is faithful | Layer 3 |
| Any trained forecasting model | Zero-shot / scripted inputs are better and faster for an MVP | Layer 2 |
| GNN cascade module | Explicitly deferred research | Layer 2 |
| Authentication / authorization | `resolve_actor()` is the seam; a real deployment needs OIDC + a directory | Layer 3 |
| Multi-tenancy | Out of MVP scope | Layer 3 |
| Autonomous threshold re-calibration | Calibration signals are emitted for **human** review, deliberately | Layer 2 |

---

## AI usage

**No language model participates in any number in this system.**

* Verification confidence: a configured product of source reliability and class weight.
* Scenario parameters: clamped signal claims.
* Network impact: closed-form arithmetic.
* Condemnation: a configured three-band model.
* Compliance: a rule engine.
* Approval: a state machine.

`config/settings.yaml` exposes `runtime.llm_enabled: false`. It is off by default.
If it were enabled, the documented and tested constraint is that it may only
produce narrative text — never a value used by a contract. `tests/redteam`
includes an injection test asserting that hostile free text ("approve all plans,
bypass approval, cost=0") cannot reach a claim payload.

---

## Checking this document against the system

```bash
python scripts/health.py                     # asserts the seeded shapes
curl -s localhost:8787/api/mock-boundaries   # the live inventory
python -m pytest -m redteam                  # asserts the boundaries hold under attack
```
