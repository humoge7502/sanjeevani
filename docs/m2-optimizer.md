# M2 — the digital twin and the recovery optimizer

> **Status:** implemented and opt-in. The demo runs the labelled fixture by default.
> `SANJEEVANI_PLAN_PROVIDER=optimizer` turns on the real solve.
> Rationale and rejected alternatives: [ADR 0010](decisions/0010-m2-is-implemented-and-opt-in.md).
> The queueing cross-check that shadows this solve has its own page:
> [`m2-des.md`](m2-des.md) · [ADR 0015](decisions/0015-the-des-is-a-cross-check-not-a-second-opinion.md).

The architecture's headline seam is `RecoveryPlan`. This document is about the two
modules that now produce one, and about what is and is not claimed for them.

```
Impact ──► digital_twin (simulate) ──► optimizer (choose) ──► RecoveryPlan ──► M3
```

---

## 1. What is real, what is configured

| Component | Status | Where |
|---|---|---|
| Network, SKUs, consignments, device traces | **Seeded, deterministic** | `data/` |
| Corridor exposure (the shared hop fraction) | **Real arithmetic, shared with M1** | `backend/graph/network.py` |
| Stockout and condemnation models | **Real, identical to A3's** | `backend/agents/impact.py`, `backend/agents/telemetry.py` |
| Simulation of a chosen action's consequence | **Computed** | `backend/m2/digital_twin.py` |
| The binary program and the Pareto frontier | **Solved** | `backend/m2/optimizer.py` |
| Queueing cross-check (berths, closure unions, waits) | **Simulated, deterministic — reported, never averaged** | `backend/m2/des.py` |
| Hub stock an `EXPEDITE_FROM_HUB` action may draw | **Seeded ceiling, contended** | `backend/m2/inventory.py` |
| The recovery **action catalogue** (costs, outage factors) | **Configured** | `config/recovery_options.yaml` |
| Budget / service / cold-chain / time ceilings | **Configured** | `config/recovery_options.yaml` |

The action catalogue is the only configured part, it is labelled
`configured_catalogue` in every trace it produces, and its coefficients are this
project's own engineering estimates rather than measured history. Replacing them
with measured cost and transit data is a **config change, not a code change** —
that is the point of putting the decision *space* in YAML and the decision in code.

---

## 2. The digital twin

Deterministic, closed-form, no sampling. Given a candidate assignment of one action
per affected consignment:

```
corridor_exposure(c, a) = remaining_fraction(c) x a.corridor_exposure_factor
delay(c, a)             = transit_delay x corridor_exposure + a.delay_delta_days
outage(c, a)            = min(H, duration x a.outage_factor + max(0, delay))
shortfall(c, a)         = max(0, outage - coverage[sku])
p_stockout(c, a)        = 1 - exp(-shortfall / service_recovery_days)
condemn(c, a)           = base_condemn(c) x (1 - a.condemn_recovery_fraction)
unrecovered(c, a)       = value(c) x [ p_stockout x (1 - condemn) + condemn ]
cost(c, a)              = a.fixed + a.per_pallet x pallets(c) + a.per_unit x units(c)
```

Every term except the action's own coefficients comes from M1. `remaining_fraction`
is imported from the **same function** A3 uses, and
`test_twin_agrees_with_a3_on_remaining_corridor_fraction` asserts they agree for
every consignment in the network.

**The anchor test.** With every consignment assigned `WAIT`, the twin's
`expected_unrecovered_usd` must land on A3's published `revenue_at_risk`
(`test_wait_reproduces_a3_impact_for_the_hero_horizon`). If the twin's baseline ever
drifts from M1's impact model, every optimization above it is measured against a
different world.

**No randomness anywhere.** No sampling, no Monte Carlo, no `numpy.random`. Run it
twice and you get identical bytes — which is what makes the audit ledger downstream
meaningful rather than merely present.

### Plan-level aggregation

| Figure | Definition |
|---|---|
| `service_level` | served value / total value, over the plan's scope |
| `temperature_risk` | value-weighted mean of residual per-consignment risk |
| `resilience_score` | weighted sum of corridor independence, temperature, speed, value preservation, route diversity |
| `recovery_time_hours` | max across consignments |

---

## 3. The optimizer

### The program

```
minimise   sum_a,c ( cost + w x unrecovered ) x x[a,c]
subject to sum_a x[a,c] = 1                    for every consignment c
           sum cost                       <= budget ceiling
           sum served_value               >= service floor x total value
           sum value x temp_risk          <= cold-chain ceiling x total value
           sum recovery_hours             <= time ceiling
           x[a,c] binary
```

`w` is `objective.value_of_risk_weight` — a plain economic exchange rate between
"money spent now" and "value expected to be lost". Both terms are USD.

### Two solvers, deliberately

| Path | When | Why |
|---|---|---|
| `milp` — PuLP + bundled CBC | Always attempted | The production path; a real solver on a real formulation. |
| `exact` — exhaustive enumeration | Whenever the Cartesian product is ≤ 100 000 | Exact, fast at this size, and **independent of the formulation**. |

They run together, and the trace records `milp_agrees_with_exhaustive`. If they
ever disagree, the trace says so and the exact answer is used —
`test_milp_agrees_with_exhaustive_enumeration` fails on a disagreement. An optimizer
that cannot show its own arithmetic being checked is just an opinion.

If PuLP is missing or CBC fails, the exact path stands alone and the trace records
why. **A missing solver degrades the explanation, never the answer.**

### The Pareto frontier

Computed exactly as the non-dominated set on `(cost, expected_unrecovered)`. 38
points for the hero scenario.

### Counterfactuals

`uniform_policy` rows answer "what if we had applied one policy to everything?" —
the question a post-incident review actually asks. Each row carries an
`executable` flag, because a uniform policy can look cheaper on total economic
impact and still be **un-admissible**: the cheapest policy (air-uplift everything)
breaches the budget cap. Each row also carries its own `des` verdict, because a
policy that leaves traffic on the corridor is exactly what queueing penalises.

### The discrete-event cross-check

Every solve — recommended plan and every counterfactual — is shadowed by a
deterministic discrete-event simulation of the disrupted chokepoints
(`backend/m2/des.py`, SimPy, no randomness). It exists because the closed-form
twin cannot express **contention** (a third consignment waiting behind two
berths), **position** (when a consignment actually reaches the chokepoint) or
**overlap** (two closures last the *union* of their intervals, not the sum of
their durations).

The two models are **never averaged**: `m2.des.cross_check` states one of four
verdicts (`agrees` · `des_slower` · `des_faster` · `not_applicable`) with the
delta and the tolerance. The seeded scenario contains the case the module was
built for — uniform `WAIT` prices at 336 h in the twin and **406.8 h** in the DES,
a 70.8-hour blind spot that is reported rather than reconciled away. Full detail:
[`m2-des.md`](m2-des.md).

---

## 4. Reading the output

```
python scripts/demo.py                 # fixture (default)
SANJEEVANI_PLAN_PROVIDER=optimizer python scripts/demo.py
```

`GET /api/state` → `m2` returns the whole solver trace:

```jsonc
{
  "solver": "exact_enumeration",
  "disruption": { "blocked_nodes": ["PORT-SUEZ"], "duration_days": 10.0, ... },
  "decision_space": { "combinations": 7776, "actions_considered": { ... } },
  "objective": { "value_of_risk_weight": 1.0, "selected_objective_value": ... },
  "constraints": { "violations": [], "feasibility": "optimal among all feasible assignments" },
  "verification": {
    "milp_available": true, "milp_status": "Optimal", "milp_agrees_with_exhaustive": true
  },
  "selected_plan": { "choices": { "SHIP-001": "REROUTE_AIR_UPLIFT", ... } },
  "pareto_frontier": [ ... ],
  "hub_inventory": { "source": "data/inventory/hub_inventory.yaml", "contended": [ ... ] },
  "counterfactuals": [ { "uniform_policy": "REROUTE_AIR_UPLIFT", "executable": false, "des": { ... } } ],
  "des": {
    "recommended_plan": { "recovery_hours": 0.0, "participants": [], ... },
    "cross_check": { "verdict": "not_applicable", "contradicts": false, ... }
  }
}
```

---

## 5. Two results worth knowing before anyone asks

### 5.1 Doing nothing is not the worst option, and the Cape re-route is

`REROUTE_CAPE` has a **higher** total economic impact than `WAIT`. It adds three
days of transit; because the closure duration (10 d) already equals the planning
horizon (10 d), the extra delay buys no additional coverage, and it costs money on
top. Re-routing around the Cape inside a 10-day window is strictly dominated by
waiting.

This is asserted, not incidental:
`test_cape_reroute_is_worse_than_doing_nothing_in_a_10_day_window`.

### 5.2 No uniform policy is admissible, so the optimum is a mix

Every single-action policy breaches at least one hard ceiling:

| Policy | Breaks |
|---|---|
| `WAIT` | service floor (and the cold-chain ceiling) |
| `QUARANTINE_QA` | service floor — it rescues cargo, not supply |
| `REROUTE_AIR_UPLIFT` | budget ceiling |
| `EMERGENCY_SOURCE` | budget ceiling |
| `EXPEDITE_FROM_HUB` | service floor |
| `REROUTE_CAPE` | service floor, and worse than waiting |

The only admissible answer is a **per-consignment mix**, and the optimizer finds it.
That is exactly the thing a planner cannot compute by hand and a vendor claim cannot
show. Asserted by `test_no_uniform_policy_is_admissible_and_the_optimum_is_a_mix`,
which also fails if a uniform policy ever *becomes* admissible — because then the
optimizer would have to return it and the mix would be decorative.

---

## 6. What is still not built

- **No routing or scheduling.** `OR-Tools` is not used; lanes are selected from
  the seeded network, not synthesised.
- **No inventory *optimisation*.** The hub ceiling is modelled — several
  consignments contend for a finite per-(hub, SKU) pool
  (`backend/m2/inventory.py`) — but there is no reorder policy, no safety-stock
  computation and no echelon holdback.
- **The coefficients are not measured.** See §1. The DES's berth and service
  times (`config/des.yaml`) are engineering estimates too.
- **No rolling-horizon re-optimisation.** One solve per event, not a receding
  loop.

(The queueing cross-check that used to be on this list is built — see
[`m2-des.md`](m2-des.md).)

---

## 7. Tests

```bash
python -m pytest tests/unit/test_m2_optimizer.py -v    # 29 tests
python -m pytest tests/unit/test_m2_inventory.py -v    # 23 tests
python -m pytest tests/unit/test_m2_des.py -v          # 35 tests
```

Covering: twin determinism; twin/A3 agreement on corridor fraction; twin/A3
agreement on the WAIT baseline; MILP/exhaustive agreement; determinism across two
solves; every hard ceiling satisfied; the plan passing the **M3 rulebook**; the
strategy landing in POL-TEMP-002's accepted set; contract conformance; plan-id
stability; distinct ranked strategies; non-dominated frontier; beating every
admissible uniform policy; the two results in §5; and a complete trace.
