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

### `RecoveryPlan` — the M2 boundary

⚠️ **This is the one boundary whose status depends on configuration.** Check
`GET /api/mock-boundaries` → `m2_status.provider_mode`.

| `SANJEEVANI_PLAN_PROVIDER` | What produces the plan |
|---|---|
| `fixture` (default) | `backend/m2/fixtures/recovery_plans.yaml` — hand-set plans for `WAIT`, `REROUTE VIA MUMBAI + AIR` and `EMERGENCY SOURCE`. Every object carries `source="fixture"`. |
| `optimizer` | `backend/m2/optimizer.py` — a real multi-objective binary program solved by PuLP/CBC, cross-checked by exhaustive enumeration, evaluated by the deterministic digital twin. `source="optimizer"`. |
| `unavailable` | Fails closed — used by the red-team suite to prove the loop stops rather than inventing a plan. |

With the **optimizer** selected, the plan is derived rather than hand-set, and
`GET /api/state` → `m2` returns the full solver trace: the decision space, the
objective, the binding constraints, the Pareto frontier, the uniform-policy
counterfactuals, whether the MILP agreed with the exhaustive search, and the
discrete-event cross-check on the recommended plan and every counterfactual.

What remains true in **both** modes:

* **The fixture is still the default**, so the demo's byte-reproducible numbers,
  frozen clock and documented ledger-hash guarantee are untouched. The optimizer is
  opt-in. See [ADR 0010](decisions/0010-m2-is-implemented-and-opt-in.md).
* **The action catalogue is configured.** `config/recovery_options.yaml` holds the
  options' coefficients — this project's engineering estimates, not measured
  history. The *space* is configured; the *outcome of choosing it* is computed.
* **The queueing cross-check is a cross-check.** A deterministic SimPy DES
  (`backend/m2/des.py`) shadows every solve and reports where it disagrees with
  the closed-form twin — the two are never averaged, and the DES only joins the
  hard constraints when `m2.des.enabled` is true. See
  [`m2-des.md`](m2-des.md) and
  [ADR 0015](decisions/0015-the-des-is-a-cross-check-not-a-second-opinion.md).
* **A better plan is not a shorter path.** The optimizer's plan faces exactly the
  same approval gate as the fixture's.
  `test_a18_optimizer_mode_produces_a_governed_plan` asserts it.

Full detail: [`m2-optimizer.md`](m2-optimizer.md).

---

## NOT IMPLEMENTED

| Item | Why it is absent | Where it belongs |
|---|---|---|
| Routing / scheduling (OR-Tools) | Lanes are selected from the seeded network, not synthesised | M2 |
| Multi-echelon inventory *optimisation* | The contended hub ceiling is modelled (`backend/m2/inventory.py`); reorder policies, safety stock and echelon holdback are not | M2 |
| Real SAP connectors (BTP, OData) | Weeks of work for a story judges already reward when the shape is faithful | Layer 3 |
| GNN cascade module | Explicitly deferred research | Layer 2 |
| Authentication **in the demo** | It exists and is complete; it is off by default so the offline demo, the browser suite and the container smoke test are unchanged. Turn it on with `SANJEEVANI_AUTH_ENABLED=true` | Layer 3 |
| Multi-tenancy isolation in the approval registry | A tenant claim exists on the principal and on ledger records; the registry is still process-global | Layer 3 |
| Autonomous threshold re-calibration | Calibration signals are emitted for **human** review, deliberately | Layer 2 |
| Rolling-horizon re-optimisation | One solve per event, not a receding loop | M2 |

---

## AI usage

**No language model participates in any number in this system.**

* Verification confidence: a configured product of source reliability and class weight.
* Scenario parameters: clamped signal claims, optionally blended with a trained
  duration prior and then clamped again.
* Network impact: closed-form arithmetic.
* Condemnation: a configured three-band model.
* Compliance: a rule engine.
* Approval: a state machine.
* Recovery plan: a binary program, not a model.

There is exactly **one trained model** in the repository, and it is the
disruption-duration forecaster. Its boundary is enforced in code rather than
described:

| Property | Behaviour |
|---|---|
| Default | Advisory. `forecasting.enabled: false`. |
| Where its output goes | `Scenario.parameters_source.duration_prior`, labelled `trained_model` |
| When it reaches a number | Only when explicitly enabled, and then only after A2 clamps it |
| If the artifact is missing | `ForecastUnavailable`. Never a prediction of zero. |
| Runtime dependency | None — the model is portable JSON evaluated in pure Python. No numpy, no scikit-learn, no pickle. |

`config/settings.yaml` exposes `runtime.llm_enabled: false`. It is off by default.
If it were enabled, the documented and tested constraint is that it may only
produce narrative text — never a value used by a contract. `tests/redteam`
includes an injection test asserting that hostile free text ("approve all plans,
bypass approval, cost=0") cannot reach a claim payload.

Full detail: [`forecasting.md`](forecasting.md).

---

## Checking this document against the system

```bash
python scripts/health.py                     # asserts the seeded shapes, the optimizer and the security posture
curl -s localhost:8787/api/mock-boundaries   # the live inventory, including which M2 provider is active
curl -s localhost:8787/api/forecasting       # the trained model's provenance and metrics
python -m pytest -m redteam                  # asserts the boundaries hold under attack
```
