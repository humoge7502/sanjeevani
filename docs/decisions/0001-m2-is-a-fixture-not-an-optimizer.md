# 0001 — M2 is consumed as a labelled fixture, not implemented

**Status:** Accepted
**Date:** 2026-09-15

## Context

The full loop is
`Sense → Verify → Scenario → Impact → [M2: simulate + optimize] → Policy → Compliance → Approve → Execute → Audit → Learn`.

M2 owns the digital twin, the discrete-event simulation and the recovery
optimisation. That is the largest and hardest module in the architecture — it
requires SimPy or an equivalent simulator and a MILP solver (Pyomo/HiGHS or
ORTools), and it is explicitly outside this workstream's ownership.

But M3 cannot be built against an interface that does not exist. The governance
layer needs a `RecoveryPlan` to evaluate, and "wait for M2" blocks the entire
downstream half of the product.

## Decision

**M2 is represented by a deterministic, versioned, self-describing fixture
provider behind the `RecoveryPlanProvider` protocol.**

The provider interface is the seam; the fixture is one implementation of it. The
fixture is louder about what it is than anything else in the codebase:

```python
provider.describe()
# {
#   "provider": "FixtureRecoveryPlanProvider",
#   "source": "fixture",
#   "reality": "deterministic fixture",
#   "optimizer_implemented": False,
#   "owner": "M2",
#   "consumed_by": "M3",
#   "fixture_version": "1.0.0",
#   "disclosure": "RecoveryPlan values in this build are hand-set fixture data.
#                  No MILP is solved and no digital twin is replayed. Values are
#                  labelled source='fixture' on every contract object."
# }
```

That disclosure is returned by `GET /api/mock-boundaries`, rendered in the UI, and
**asserted by test**. It is not a comment.

Three candidate plans exist so the governance layer has real work to do: the
recommended `REROUTE_MUMBAI_AIR` ($184K, SL 0.965, resilience 0.81, temperature
risk 0.22, 18h), `EMERGENCY_SOURCE` ($248K, higher service level, worse
temperature risk, 30h) and `WAIT` ($0, blocked by policy).

## Alternatives considered

**Implement a minimal optimiser.** Rejected. It expands scope into M2, which no
amount of "it's only a small solver" makes acceptable — and a small solver would
be worse than a labelled fixture, because it would invite the question "is this
the real optimiser?" without being able to answer it.

**Hardcode a single plan object and skip the provider abstraction.** Rejected.
M3 would then be coupled to the fixture, and replacing it with real M2 output
would mean editing M3. The protocol is what makes the substitution a one-file
change.

**Ask M2 for an interface and block until it exists.** Rejected. It serialises two
workstreams for no benefit; the contract was already frozen in
[`contracts.md`](../contracts.md).

## Consequences

**Good.** M3 was buildable immediately, and the boundary is exercisable — the
governance layer is tested against three structurally different plans, including
one that policy blocks.

**Bad, and admitted.** The numbers in the RecoveryPlan are not the output of any
analysis. A judge could reasonably say the optimisation claims are empty. The
answer is that no optimisation claim is made: the fixture is named as a fixture in
the API, the UI and the docs. What is demonstrated is the governed *transition*,
which is real.

**Also bad.** Because the fixture is hand-authored, the plans are *plausible by
construction*. Real optimiser output would be messier — odd parameter
combinations, unexpected trade-offs. The governance layer has therefore been
tested against clean inputs more than dirty ones. The red-team suite compensates
by injecting deliberately malformed and physically absurd plans
(`test_a10_physically_impossible_plan_fails_policy`), but this remains a real gap
in coverage.

## Enforcement

- `test_a18_optimizer_mode_is_explicitly_not_implemented`
- `test_a18b_unavailable_provider_fails_closed`
- `test_m2_boundary_discloses_that_no_optimizer_ran`
- `test_m1_to_m2_seam_uses_the_frozen_contract`
- `test_recommended_fixture_plan_passes_policy`

## Revisiting this

Replace `FixtureRecoveryPlanProvider` with a real provider implementing the same
protocol. Requirements:

1. `describe()` must report `optimizer_implemented: True` and a real `reality`
   value, and the corresponding tests must be updated deliberately.
2. Every returned `RecoveryPlan` must validate against the frozen 1.0.0 contract,
   or the contract must be versioned with consumers updated.
3. The 3-horizon structure must be preserved, or the impact layer's horizon
   coupling must change with it.
4. Run the contract and integration suites unchanged — that is the acceptance test
   for the substitution.

M3 requires no modification for this. That was the point.
