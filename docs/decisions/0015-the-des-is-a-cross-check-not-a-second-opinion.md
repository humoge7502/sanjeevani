# 0015 — The DES is a cross-check, never a second opinion

**Status:** Accepted
**Date:** 2026-10-26

## Context

ADR 0010 shipped the digital twin and the recovery optimizer, and said honestly
that the twin "is not a discrete-event simulation with queueing, berth allocation
or stochastic arrivals… That remains genuinely unbuilt, and `SimPy` is installed
but unused."

The gap mattered for three specific questions the closed-form twin cannot answer:

1. **Contention** — two consignments, two berths, a third waiting behind them.
2. **Position** — when does a consignment actually *reach* the chokepoint?
3. **Overlap** — two disruptions at one chokepoint last the *union* of two closed
   intervals, not the sum of two durations.

The tempting design is the obvious one: build the DES, average it with the twin,
or replace the twin's recovery number with the DES's. Both are wrong here. The
ledger's byte-reproducibility claim rests on every number coming from a model
whose whole arithmetic a reviewer can restate; and an average of two models is a
number neither model vouches for.

## Decision

**Build the DES as a deterministic cross-check whose verdict is reported, never
reconciled — advisory by default, a hard constraint only when switched on.**

* `backend/m2/des.py`, SimPy, **no randomness anywhere**: arrivals derive from
  seeded lane topology, service times are configured constants, and
  `test_the_module_imports_no_random_number_generator` reads the source to keep
  it that way. Two runs are identical to the bit.
* The DES **always runs** when the optimizer runs: its evidence is attached to
  the recommended plan *and* to every uniform-policy counterfactual in the trace
  (`GET /api/state` → `m2.des`), so the disagreement is always visible.
* `compare_with_twin()` returns one of four verdicts — `agrees`, `des_slower`,
  `des_faster`, `not_applicable` — with the delta and the tolerance. There is no
  averaging function, and no code path that blends the two numbers.
* `m2.des.enabled: false` (default): evidence only. `true`
  (`SANJEEVANI_M2_DES=true`): leftover corridor traffic that clears after the
  recovery ceiling, or any observed berth contention, becomes a constraint
  violation and the plan is infeasible.
* `wait_days` measures time **at anchor** (arrival to berth, service excluded),
  so the queue's own cost is visible in the summary instead of being restated
  `passed_at_day`.

The payoff is already in the seeded scenario: under a uniform `WAIT` policy the
twin prices recovery at 336 h; the DES says 406.8 h — a **70.8-hour blind spot**
the closed form cannot see, because the last consignment is 16.2 days out and
must queue behind the reopen.

## Alternatives considered

**Average the two recovery times.** Rejected: hides exactly the case worth
knowing about, and produces a number neither model vouches for.

**Make the DES the authoritative recovery number.** Rejected: the twin is what
the optimizer's objective and the M3 contract consume; swapping the number's
source would invalidate ADR 0010's reproducibility argument for a gain that a
reported disagreement delivers more honestly.

**Stochastic DES (sampled arrivals/service).** Rejected: would force a seed into
the audit chain and break "run it twice, get the same bytes". A DES does not have
to be stochastic, and determinism costs nothing here because every input is
seeded data.

**Keep not building it.** Rejected: "no uniform policy is admissible" and the
Cape result are both claims about leaving traffic on a corridor; a reviewer is
entitled to ask what the queue does, and before this ADR the answer was "nothing,
there is no queue in the model".

## Consequences

**Good.** Contention, position and overlap are modelled and reported; the twin's
known blind spot is quantified (and asserted by test) instead of described in
prose; the constraint switch lets a strict operator make leftover corridor
traffic infeasible; the recommended plan is provably unchanged with the DES off
(`test_the_des_did_not_change_the_recommended_plan`).

**Bad, and admitted.** The service model (`config/des.yaml`) is engineering
estimates, like the rest of the seed. The berth model is per-node with a
non-preemptive FIFO/priority queue — no tidal windows, no yard capacity, no
crane splits. Running the DES adds one simulation per plan plus one per
counterfactual to every optimizer solve. And a cross-check can only *report* a
contradiction in the default posture — a human still has to read it, which is
the same trade the forecaster makes in ADR 0012.

## Enforcement

- `tests/unit/test_m2_des.py` — 35 tests: determinism (source-level no-RNG,
  bit-identical reruns), interval-union semantics, contention and ordering,
  topology-derived arrivals, the empty-queue case, both disagreement directions,
  the tolerance band, the advisory/constraint split
  (`test_the_des_joins_the_hard_constraints_only_when_switched_on`), the trace
  always carrying the cross-check, and plan-identity.
- `scripts/health.py` exercises the optimizer path, which runs the DES.

Supersedes the "no DES" paragraph in
[ADR 0010](0010-m2-is-implemented-and-opt-in.md). Detail: [`m2-des.md`](../m2-des.md).
