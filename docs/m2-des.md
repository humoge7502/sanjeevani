# The discrete-event cross-check (M2)

`backend/m2/des.py` · config: `config/des.yaml` · tests: `tests/unit/test_m2_des.py`

> The closed-form twin answers "what does this action do to this consignment".
> The DES answers "what does everyone doing it at once do to the chokepoint".

## Why it exists

The digital twin ([`m2-optimizer.md`](m2-optimizer.md)) evaluates each consignment
independently. That is the right model for what it is, and it cannot express three
things:

1. **Contention.** Two consignments, two berths, and a third waiting behind them.
   A per-consignment delay has nowhere to put the third's wait.
2. **Position.** When does a consignment actually *reach* the chokepoint? The twin
   uses position only as a corridor-exposure multiplier, so a consignment three
   days out and one already alongside are treated as equally exposed.
3. **Overlap.** Two disruptions at the same chokepoint do not last the *sum* of
   their durations, they last the **union of two closed intervals**. Adding the
   durations is the intuitive answer and the wrong one — `[0,10) + [7,17)` closes
   the port for 17 days, not 20 and not 10.

A discrete-event simulation answers all three.

## Deterministic, and provably so

A DES does not have to be stochastic. This one is not:

* every arrival time is derived from seeded lane topology
  (hops-to-chokepoint × lane transit time, prorated);
* every service time is a configured constant (`config/des.yaml`);
* SimPy schedules a deterministic event heap — **no random number generator is
  imported anywhere in the module**, and a test reads the source to assert it
  (`test_the_module_imports_no_random_number_generator`). Two runs are identical
  to the bit (`test_two_runs_are_identical_to_the_bit`).

That is what makes it compatible with an audit chain that claims "run it twice,
get the same bytes".

## The model

```
arrivals -> [ closed window ] -> berths -> departure
```

* A closure window closes `[start_day, end_day)` per chokepoint; overlapping and
  adjacent windows merge into their union (`merge_windows`).
* A consignment participates only when the plan's action keeps it
  corridor-exposed (`corridor_exposure_factor > 0`) **and** the chokepoint is at
  or downstream of its current position. Traffic that already cleared the
  chokepoint is not re-queued; plans that lift everything off the corridor
  generate an empty queue, and that is a valid, informative result.
* Berths are finite and non-preemptive; ties break by arrival, then priority
  band (critical before standard), then shipment id.
* `wait_days` is **time at anchor** — arrival to berth acquisition, service time
  excluded. The moment of clearing is `passed_at_day`; the deepest queue is
  `queue_peak`, recomputable by hand from the timeline rather than sampled from
  library internals.

## The cross-check: agreement is evidence, disagreement is a finding

The DES is **not** a second opinion to be averaged with the twin, and it is not
the source of the audited numbers. Both recovery times are reported, and when
they disagree past a configured tolerance (`m2.des.contradiction_tolerance_hours`,
default 24 h) the disagreement is surfaced with one of four verdicts:
`agrees` · `des_slower` · `des_faster` · `not_applicable` (the plan left no
corridor traffic to queue).

Averages hide exactly the case worth knowing about, and the seeded scenario
contains it:

> **The 70.8-hour blind spot.** Under a uniform `WAIT` policy the twin prices
> recovery at 336 h (the action's own duration). The DES says the corridor is not
> clear for **406.8 h**, because the last consignment is 16.2 days from the
> chokepoint and has to queue behind the reopen. A 70.8-hour gap, asserted by
> `test_the_des_contradicts_the_twin_when_a_policy_leaves_traffic_queued`.

The optimizer's trace (`GET /api/state` → `m2.des`) carries the DES evidence for
the recommended plan *and* for every uniform-policy counterfactual, and the
command center renders the verdict beside the twin's numbers.

## Advisory by default, a constraint when you ask

* `m2.des.enabled: false` (default) — the DES always **runs** and its evidence is
  always in the trace, but it cannot veto a plan.
* `m2.des.enabled: true` (or `SANJEEVANI_M2_DES=true`) — a plan whose leftover
  corridor traffic clears after the recovery ceiling, or that exhibits berth
  contention at all, gains a constraint violation and is infeasible.

The split is the same one the duration forecaster uses: evidence a reviewer can
see, versus evidence the system acts on.

## Configuration

`config/des.yaml` — the service model, a knob a domain expert can audit:

| Key | Meaning |
|---|---|
| `ports.PORT-SUEZ.berths` | parallel passages the corridor can process |
| `ports.PORT-SUEZ.service_days_per_consignment` | deterministic passage time |
| `defaults` | applied to any blocked node without an entry, so a new chokepoint never silently becomes infinitely fast |

The values are engineering estimates for a small synthetic network, like the rest
of the seeded data, and are labelled as such.

## Tests

```bash
python -m pytest tests/unit/test_m2_des.py -v    # 35 tests
```

Covering: no-RNG source assertion; bit-identical reruns; interval-union semantics
(overlap, adjacency, containment, disjoint, cross-chokepoint); berth contention
and ordering; topology-derived arrivals; the already-past exclusion; the empty
queue as a valid answer; the 70.8 h contradiction; both disagreement directions;
the tolerance band; advisory vs constraint posture; config-driven service model;
and the guarantee that the DES did not change the recommended plan.

See also [ADR 0015](decisions/0015-the-des-is-a-cross-check-not-a-second-opinion.md)
and [ADR 0010](decisions/0010-m2-is-implemented-and-opt-in.md).
