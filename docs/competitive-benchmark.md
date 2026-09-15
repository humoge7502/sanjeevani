# Competitive benchmark

## The rule this document follows

**Every competitor claim below is the vendor's own published claim, dated and
linked. None is independently verified, and none is presented as fact.**

This constraint is load-bearing. A hackfest deck that says "we do what Kinaxis
does, but better" is a claim nobody in the room can check, and a judge who knows
the category will discard it. So the categories of statement are kept separate:

| Label | Meaning |
|---|---|
| **[VENDOR]** | The vendor states it about itself. Reported, dated, linked. Not verified. |
| **[INFERENCE]** | This project's reading of what the vendor material implies. Attributed. |
| **[GAP]** | Something the vendor material did not establish. Stated as a gap in *our knowledge*, not as a gap in their product. |
| **[OURS]** | A claim about this build, backed by a file or a test in this repository. |

Where the honest answer is "we are not in this category", that is what it says.

---

## Everstream Analytics

**Category:** supply-chain risk intelligence — the sensing and pre-emptive layer.

**[VENDOR]** Everstream states it was named a Leader in the 2025 Gartner® Magic
Quadrant™ for Supplier Risk Management Solutions, and that it ranked #1 in three
of four use cases in the 2026 Gartner® Critical Capabilities for Supplier Risk
Management Solutions.
Sources: `everstream.ai` (homepage), Businesswire release 2025-04-24
(`businesswire.com/news/home/20250424944630/en/`), accessed 2026-09-15.

**[VENDOR]** Everstream states it monitors and maps n-tier suppliers to give
customers visibility into sub-tier risk, and provides tailored alerts across risks
including extreme weather and labour unrest. Third-party review material on
Everstream's own site (Spend Matters) describes the same capability.
Source: `everstream.ai/articles/the-benefits-of-supply-ecosystem-risk-management/`,
accessed 2026-09-15.

**[INFERENCE]** The strength is breadth of external signal and sub-tier discovery —
knowing a supplier's supplier is exposed before the disruption lands. That requires
ingested global data at a scale this project does not attempt.

**[GAP]** The vendor material reviewed does not address the post-decision path:
what happens after a human agrees to act, how that action is authorised, executed
across systems, receipted and audited. That is not a claim that the capability is
absent — only that it was not established from the material reviewed.

**What to learn from it:** the verification story. Everstream's value rests on
sourcing that is trusted. This project's A2 verification (two-source rule, direct
measurement reproduced from the device trace, explicit freshness, an explicit
`rejected` and `deduplicated` outcome for anything that fails) is modelled on the
same instinct applied to a much smaller feed.

**What to avoid copying:** breadth without depth. Ingesting every possible signal
is a different product. 12 nodes with fully traceable arithmetic is the opposite
trade and it is the right one at this scope.

**[OURS]** Not a competitor. Different layer.

---

## Kinaxis

**Category:** concurrent planning and supply-chain orchestration.

**[VENDOR]** Kinaxis describes Maestro as an "AI-powered platform for supply chain
planning and decisioning", built on concurrency, that "continuously synchronizes
signals, plans, and decisions." The 2024 launch release calls it the first
AI-infused end-to-end supply chain orchestration platform.
Sources: `kinaxis.com/en/solutions/platform`, `kinaxis.com/en/what-concurrent-planning`,
press release 2024 (`kinaxis.com/en/news/press-releases/2024/...`), accessed 2026-09-15.

**[VENDOR]** Kinaxis announced a result with NVIDIA AI claiming up to 12× faster
end-to-end planning performance, reducing large-scale planning cycles from more
than three hours. Source: Businesswire release 2026-03-17, accessed 2026-09-15.
**This is a vendor-reported benchmark.** It is cited here as the vendor's claim and
no comparison is drawn against it.

**[INFERENCE]** The real capability is *concurrency*: making plan changes visible
across a whole network without sequential replanning. That is a genuinely hard
engineering problem and this project does not solve it.

**What to learn from it:** the plan-exploration surface. Kinaxis's value is that
planners compare scenarios rather than accept one. This project shows the same
instinct at fixture scale — the recommended plan alongside `EMERGENCY_SOURCE`
($248K, SL 0.978, temperature risk 0.31, 30h) and the policy-blocked `WAIT`
(cost $0, SL 0.712, temperature risk 0.86) — so a human sees the trade, not just
the verdict.

**What to avoid copying:** winning on optimisation performance. That is M2. This
build consumes a plan through a frozen contract and says so out loud, rather than
entering a benchmark contest it would lose.

**[OURS]** The differentiation is not the plan; it is the **governed transition
from an approved plan to executed, auditable actions**.
`test_one_approval_triggers_three_sap_shaped_actions`. A planning platform produces
a better plan. This produces a decision a regulated organisation can defend.

---

## SAP IBP and the SAP execution stack

**Category:** the incumbent planning and execution backbone — and the system this
submission is designed *with*, not against.

**[VENDOR]** SAP describes IBP as combining S&OP, forecasting and demand, response
and supply, demand-driven replenishment and inventory planning, using real-time
data and predictive analytics to balance demand and supply across a network.
Sources: `sap.com/products/scm/integrated-business-planning.html`,
`help.sap.com/docs/SAP_INTEGRATED_BUSINESS_PLANNING`, accessed 2026-09-15.

**[VENDOR]** SAP advertises response and supply planning as a capability to
"anticipate risk and focus on customers".
Source: `sap.com/products/scm/integrated-business-planning/features/response-and-supply-planning.html`,
accessed 2026-09-15.

**[INFERENCE]** IBP is the planning system of record in the target environment,
which is exactly why this submission consumes a `RecoveryPlan` and does not
generate one. Competing with IBP on planning would be strategically wrong.

**What to learn from it:** the *action* vocabulary. The mocks are shaped as
planning scenarios (IBP), freight rebinding (TM) and supplier risk and procurement
(Ariba) because those are the real systems a recovery decision would have to touch.
The fan-out is the point.

**[GAP] Important and stated plainly:** no specific SAP API contract was verified
in this work. A targeted search for the relevant OData services returned no usable
result, so the integrations are deliberately named **SAP-shaped**, never
API-faithful. `test_all_sap_responses_disclose_the_mock_boundary` asserts every
response is labelled as a mock. Claiming API fidelity without verifying a contract
would be exactly the kind of overclaim this document exists to prevent.

**[OURS]** M3 is the governed layer *around* SAP, and it is the thing SAP does not
provide across three systems: was this action authorised, by whom, against which
rulebook version, and what was the outcome?
`test_governance_history_is_complete_and_ordered`,
`test_compliance_record_is_traceable_to_the_rulebook`.

---

## Differentiation matrix

Cells describe the **layer**, not a feature-by-feature score. A cell marked
"out of scope" is a deliberate boundary, not a deficiency.

| Capability | Everstream | Kinaxis | SAP IBP | SANJEEVANI (M1+M3) |
|---|---|---|---|---|
| External signal ingestion at scale | Core — **[VENDOR]** n-tier mapping, tailored alerts | Adjacent | Adjacent | **Deliberately small** — file-based feeds, offline by default |
| Signal verification & provenance | Implied by reputation | n/a | n/a | **Explicit** — two-source rule, trace reproduction, `rejected`/`deduplicated` states |
| Scenario generation | n/a | Core — **[VENDOR]** concurrency | Core — **[VENDOR]** scenario planning | 3 horizons (3/10/30d), parameterized, clamped to configured bounds |
| Network impact on a graph | Risk mapping | Planning network | Planning network | **Deterministic traversal, per-consignment arithmetic** |
| Recovery plan | n/a | Core | Core | **Not implemented — M2, consumed as a labelled fixture** |
| Policy & compliance evaluation | n/a | n/a | Not established | **Core — rulebook-driven, fails closed, provenance-labelled** |
| Role-matched human approval | n/a | n/a | Not established | **Core — exact role match, recorded, refusable** |
| Governed execution across systems | n/a | Execution adjacent | Core execution | **Core — one approval → 3 SAP-shaped actions, receipted** |
| Cross-system audit & correlation | n/a | n/a | System-local | **Core — append-only hash-chained ledger, 19 stages** |
| Predicted vs actual, calibration | n/a | Planning variance | Not established | **Core — honest deltas, no retraining claim** |

Read it as: **columns 2–4 are strong where this build is deliberately thin, and
this build is specific where they are not the subject.** Nothing here claims
superiority.

---

## The whitespace, stated narrowly

The defensible positioning is not "better sensing" and not "better optimisation".
It is:

> **Governed closed-loop recovery** — the segment between *the plan exists* and
> *the action is executed, receipted and auditable*.

Concretely, that segment contains five things, all implemented and tested here:

1. **Compliance evaluation that can block** — `test_policy_failure_blocks_execution_end_to_end`
2. **Role-matched mandatory human authority** — `test_a03_insufficient_role_cannot_approve`
3. **One approval fanning out to multiple governed system actions** — `test_one_approval_triggers_three_sap_shaped_actions`
4. **A correlated, append-only, tamper-evident decision record** — `test_ledger_hash_chain_survives_a_full_run`
5. **Predicted-vs-actual reconciliation that does not lie about learning** — `test_outcome_never_claims_retraining_by_default`

## Claims this document will not make

- That SANJEEVANI is better than Everstream, Kinaxis or SAP IBP at anything.
- That any competitor lacks a capability — only that the material reviewed did not
  establish it.
- That the vendor-reported 12× figure is wrong, or that this build approaches it.
  It is quoted as the vendor's claim and nothing more.
- Any market size, customer count, revenue figure or adoption statistic. None was
  verified, so none appears.

## Sources

| Vendor | Source | Accessed |
|---|---|---|
| Everstream | `everstream.ai` homepage, Gartner MQ 2025 release (Businesswire 2025-04-24), Gartner Critical Capabilities 2026 page, Spend Matters review hosted by Everstream | 2026-09-15 |
| Kinaxis | `kinaxis.com/en/solutions/platform`, `kinaxis.com/en/what-concurrent-planning`, 2024 Maestro launch release, NVIDIA-collaboration release (Businesswire 2026-03-17) | 2026-09-15 |
| SAP | `sap.com/products/scm/integrated-business-planning.html`, `help.sap.com/docs/SAP_INTEGRATED_BUSINESS_PLANNING`, response-and-supply planning feature page | 2026-09-15 |
