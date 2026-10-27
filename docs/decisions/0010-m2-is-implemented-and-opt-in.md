# 0010 — M2 is implemented: a real optimizer, with the fixture kept as the default

**Status:** Accepted
**Date:** 2026-10-25
**Supersedes:** [0001](0001-m2-is-a-fixture-not-an-optimizer.md)

## Context

[ADR 0001](0001-m2-is-a-fixture-not-an-optimizer.md) declared that M2 — the
digital twin and the recovery optimizer — is *consumed* through a labelled fixture
rather than implemented, and recorded the acceptance criteria for revisiting that
decision:

1. `describe()` must report `optimizer_implemented: True` and a real `reality`.
2. Every returned `RecoveryPlan` must validate against the frozen 1.0.0 contract.
3. The 3-horizon structure must be preserved, or the impact layer's coupling must
   change with it.
4. The contract and integration suites must pass **unchanged**.

The gap that decision left was real and structural: the product's differentiating
claim is the governed closed-loop transition *from an approved plan*, but the plan
itself was hand-set YAML. Every "we chose the better recovery" claim rested on a
file nobody could check.

## Decision

**Implement M2 for real — a deterministic digital twin and a multi-objective
optimizer — and keep the fixture as the default provider.**

New modules:

| Module | Responsibility |
|---|---|
| `backend/m2/digital_twin.py` | Closed-form simulation of what a chosen recovery action does to this network. No sampling, no Monte Carlo, no model inference. |
| `backend/m2/optimizer.py` | The binary program, the Pareto frontier, the counterfactuals, and the solver trace. |
| `config/recovery_options.yaml` | The **configured decision space** (actions, coefficients, ceilings). Explicitly the *space*, never the answer. |
| `OptimizerRecoveryPlanProvider` | The same `RecoveryPlanProvider` protocol. Selected by `SANJEEVANI_PLAN_PROVIDER=optimizer`. |

**The fixture stays the default.** The demo's headline numbers, the frozen clock
and the byte-reproducible ledger hashes are documented guarantees, and switching
the default would change all three. `fixture` is the demo; `optimizer` is the
capability. `GET /api/mock-boundaries` reports which one is live, so neither can
be mistaken for the other.

## Why the twin matters more than the solver

The optimizer is a binary program, and a binary program is not clever. The value
is in the **twin**, because it is what makes the objective defensible: it re-uses
M1's own physics (the shared `corridor_remaining_fraction`, the same stockout and
condemnation models) so M2 cannot invent a more favourable world than M1 measured.
`test_wait_reproduces_a3_impact_for_the_hero_horizon` asserts exactly that: with a
"do nothing" assignment the twin lands on A3's published revenue-at-risk.

That has a consequence worth stating: **the results are counter-intuitive, and
that is the point.** The Cape re-route is *worse than doing nothing* inside a
10-day window, because the closure duration already equals the horizon, so the
extra three days of transit buy no coverage and cost money. And no single uniform
policy is admissible at all — waiting and quarantine lose the service floor, air
uplift and emergency sourcing breach the budget cap — so the only admissible answer
is a per-consignment mix. A planner cannot compute that by hand, and a vendor
claim cannot show it.

## Alternatives considered

**Ship the optimizer as the default provider.** Rejected. It changes the demo's
byte-reproducible numbers, the frozen-clock hash chain and two browser specs — all
of which are existing, tested guarantees. Making the new capability opt-in costs
nothing and keeps every guarantee intact.

**Use OR-Tools CP-SAT instead of PuLP/CBC.** Reasonable, and CP-SAT is faster. CBC
was chosen because it is bundled with PuLP and needs no separate binary, so
`pip install -r requirements.txt` is still sufficient. The formulation is small
enough that solver speed is irrelevant here.

**Solve everything with the MILP and skip enumeration.** Rejected, and this is the
most important choice in the module. Exhaustive enumeration over five consignments
is *exact* and *independent of the formulation*, so running it alongside the MILP
gives a free correctness check on the MILP itself. `solve()` records
`milp_agrees_with_exhaustive` in its trace, and a test fails if they ever disagree.
An optimizer that cannot show its own arithmetic being checked is just an opinion.

**Make the MILP solver mandatory.** Rejected. PuLP is optional at runtime: if the
import or the solve fails, the exact enumeration stands alone and the trace says
why. A missing solver degrades the *explanation*, never the answer.

## Consequences

**Good.** Every one of ADR 0001's four acceptance criteria is met, with the suites
passing unchanged. The plan is now derived, its every chosen variable carries its
coefficient, the binding constraints are named, and the Pareto frontier plus the
uniform-policy counterfactuals expose the trade rather than asserting a verdict.
`GET /api/state` → `m2` returns the whole solver trace.

**Bad, and admitted.** The action coefficients in `config/recovery_options.yaml`
are this project's own engineering estimates, not vendor quotes or measured
history. The catalogue is labelled `configured_catalogue` everywhere it appears,
and replacing it with measured cost and transit data is a config change, not a code
change — but it is not measured data today.

**Also bad.** The twin is deterministic by construction, which is what makes the
audit chain meaningful, but it is not a discrete-event simulation with queueing,
berth allocation or stochastic arrivals. It cannot answer "what happens if two
disruptions overlap". *(When this ADR was written, that remained genuinely unbuilt
and `SimPy` was installed but unused. It has since been built — as a deterministic
cross-check that is reported, never averaged: see
[ADR 0015](0015-the-des-is-a-cross-check-not-a-second-opinion.md) and
[`m2-des.md`](../m2-des.md).)*

## Enforcement

- `tests/unit/test_m2_optimizer.py` — 29 tests, including MILP/exhaustive agreement,
  determinism, contract conformance, rulebook passage, and the counter-intuitive
  Cape result.
- `test_a18_optimizer_mode_produces_a_governed_plan` — the optimizer's plan must
  still stop at the approval gate. A better plan is not a shorter path.
- `scripts/health.py` — `M2 · simulation and optimization` asserts the MILP solved,
  agreed with the exhaustive search, and broke no ceiling.
