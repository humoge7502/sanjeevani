# Research evidence registry

Every source below was retrieved and its title, venue and URL confirmed during
this work. **No citation in this document was written from memory.**

Confidence labels are mandatory throughout:

- **FACT** — the source states it, and it is verifiable at the URL given.
- **INFERENCE** — a conclusion drawn from the source, attributed to this project.
- **HYPOTHESIS** — untested, may be wrong, stated so it can be falsified.

Where a design decision rests on an inference rather than a fact, it says so. No
claim about regulator approval, benchmark superiority or production readiness is
made anywhere in this repository, because no source supports one.

---

## 0. The authoritative evidence base

This document is the project's *reading* of the literature. The *dataset* behind
it is [`docs/evidence/`](evidence/) — the SANJEEVANI real-data package (v2.1.0),
supplied separately and included here verbatim.

| What | Where | Size |
|---|---|---|
| Merged master dataset, 477 records across 44 sections | [`evidence/data/sanjeevani_real_data_master.json`](evidence/data/sanjeevani_real_data_master.json) | 475 KB |
| Every quantitative record, flat | [`evidence/data/sanjeevani_real_data_quantitative.csv`](evidence/data/sanjeevani_real_data_quantitative.csv) | 157 rows |
| The six frozen contracts | [`evidence/contracts/`](evidence/contracts/) | 6 schemas |
| Raw JSON of all 58 web searches | [`evidence/web_evidence/`](evidence/web_evidence/) | 58 files |
| Every record ID, searchable | [`evidence/INDEX.md`](evidence/INDEX.md) | — |
| File-to-record map for the evidence | [`evidence/web_evidence/EVIDENCE_INDEX.md`](evidence/web_evidence/EVIDENCE_INDEX.md) | — |

The load-bearing property of that package is its provenance discipline. Every
record carries `source_org`, `source_date`, `document`, `page` and `chapter`, and
is tagged with a class:

| Class | Count | Meaning |
|---|---|---|
| `FACT` | 388 | Stated by the cited source |
| `SPEC` | 64 | A design decision, not an empirical claim |
| `INFERENCE` | 9 | Drawn from sources, attributed |
| `HYPOTHESIS` | 6 | Untested, falsifiable |
| `CONSTRUCTED` | 5 | Invented for the demo (the Meera Iyer persona) |
| `ASSUMPTION` | 5 | A modelling input, labelled |

**Why this matters to this repository.** That taxonomy is the same distinction the
project already enforces elsewhere — labelling the M2 plan a *fixture*, the SAP
surfaces *mocks*, and the compliance rules *configured policy*. The dataset makes
the same separation at the level of evidence, so a design choice is never
mistaken for a measured fact.

### One correction it produced

The dossier's own text renders the supply-chain-risk-management software market as
`$5-23bn growing at 9-14% CAGR`. The curated dataset records it as **`$5-8bn`
growing at 9-14% CAGR** (`SJEV-STAT-007`, sourced to Mordor Intelligence,
Fortune Business Insights and Kearney). The larger figure does not appear in this
repository's documents; the smaller, better-attributed one is the one used.

### The contracts, checked rather than trusted

Including the frozen schemas made them testable. `tests/contract/test_frozen_schema_conformance.py`
reads them directly and asserts that every declared field still exists on the
Pydantic model, that each documented example validates, and that anything beyond
the frozen set is optional. That test found a genuine divergence on first run:
the schema enumerates `compliance_status` as `PENDING/PASSED/FAILED` while the
implementation emitted `PASS`/`FAIL`. The implementation was aligned to the
schema.

---

## 1. Cold-chain integrity and GDP

### WHO Technical Report Series 961, Annex 9 — *Model guidance for the storage and transport of time- and temperature-sensitive pharmaceutical products*

- **Published by:** World Health Organization
- **Year:** 2011
- **URL:** `https://www.who.int/publications/m/item/trs961-annex9-modelguidanceforstoragetransport`
- **Confidence:** FACT

**Relevant finding.** The guidance establishes that temperature-controlled
distribution requires documented, monitored handling of excursions — an excursion
is an event to be detected, investigated and resolved, not merely a sensor
reading.

**Implementation implication.** This is the regulatory shape the hero scenario is
modelled on. `POL-GDP-TEMP-001` in `config/policies.yaml` is a GDP-*style*
temperature-risk check: a plan that leaves a biologic consignment above its
excursion allowance is blocked. The telemetry trace
(`data/telemetry/cold_chain.csv`) is analysed for an actual breach, so the
temperature risk is derived from data rather than asserted.

**What is NOT claimed.** SANJEEVANI does not assert GDP certification or
regulatory compliance. The rule is a *configured policy* that mirrors a GDP
principle. Every check in the compliance panel carries a provenance label
(`configured policy`, `simulated rule`, `illustrative check`) precisely so this
distinction survives into the UI — enforced by
`test_every_check_carries_a_provenance_label_and_config_key`.

### Temperature excursion management: a novel approach of quality system

- **Venue:** *Journal of Pharmaceutical Analysis* (PMC5355558)
- **URL:** `https://pmc.ncbi.nlm.nih.gov/articles/PMC5355558/`
- **Confidence:** FACT (exists); INFERENCE (applicability)

**Relevant finding.** Excursions during transport require a defined quality-system
response, not a binary pass/fail reading.

**Implementation implication — INFERENCE.** This suggested modelling excursion as
a *degree* with a configured allowance rather than a threshold, so a short breach
inside the allowance is reported as `WITHIN_ALLOWANCE` and does not block. This is
a modelling choice made here, not a standard, and it is labelled as configured
policy rather than as regulation.

---

## 2. Agentic AI reliability

### *Why Do Multi-Agent LLM Systems Fail?*

- **Authors:** Mert Cemri, Melissa Z. Pan, Shuyi Yang, Lakshya A. Agrawal, Bhavya Chopra, Rishabh Tiwari, Kurt Keutzer, Aditya Parameswaran, Dan Klein, et al.
- **Venue:** arXiv (also at NeurIPS 2025 per OpenReview listing)
- **Year:** 2025
- **arXiv:** `2503.13657` — `https://arxiv.org/abs/2503.13657`
- **Confidence:** FACT

**Relevant finding.** The paper introduces **MAST**, an empirically grounded
taxonomy of failure modes in multi-agent LLM systems, built by annotating 1,600+
execution traces across several multi-agent frameworks. Failures cluster around
specification, inter-agent misalignment and task verification rather than raw
model capability.

**Implementation implication.** This is the single most load-bearing source for
the architecture. It is direct evidence that **coordination and verification, not
model capability, are where multi-agent systems break** — which is exactly why
this project keeps authoritative numbers deterministic and confines LLM use to
narrative text.

The concrete design consequences:

- **Task verification is explicit, not implicit.** A2 requires a two-source rule
  before a signal is trusted and rejects single-source claims; nothing downstream
  re-derives trust.
- **No agent interprets another agent's prose.** Stages exchange typed Pydantic
  objects on frozen contracts, so an inter-agent misunderstanding is a
  `ValidationError`, not a plausible-looking wrong number.
- **Non-authoritative generation is quarantined.** Signal free text reaches only
  the audit trail and the human reader; it never parameterizes a claim. Enforced
  by `test_a14_free_text_never_parameterizes_a_claim`.
- **The optimiser boundary is refused rather than improvised.** M2 is a fixture
  that says it is a fixture (`test_a18_optimizer_mode_is_explicitly_not_implemented`).

**What is NOT claimed.** SANJEEVANI is not benchmarked against MAST and this
document does not assert that it is free of those failure modes. **HYPOTHESIS:**
the deterministic-trust-boundary pattern reduces the specification and
verification failure classes. Testing that would require the kind of trace
annotation MAST performs, which this work did not do.

---

## 3. Human oversight and the governance boundary

### *Exploring automation bias in human–AI collaboration: a review and implications for explainable AI*

- **Author:** G. Romeo et al.
- **Venue:** *AI & Society*
- **Year:** 2026 (publication; indexed 2025)
- **DOI:** `10.1007/s00146-025-02422-7` — `https://link.springer.com/article/10.1007/s00146-025-02422-7`
- **Confidence:** FACT

**Relevant finding.** Automation bias — over-reliance on automated recommendations —
is a well-established failure mode in human–AI collaboration, and explanation
quality mediates it.

### *Automation Bias in AI-Decision Support: Results from an Empirical Study*

- **Author:** F. Kücking et al.
- **Year:** 2024
- **PMID:** `39234734` — `https://pubmed.ncbi.nlm.nih.gov/39234734/`
- **Confidence:** FACT

**Relevant finding.** Automation bias was measured directly as the **agreement
rate with incorrect AI recommendations**, producing measurable degradation in
decision quality.

### TechDispatch — *Human oversight of automated decision-making*

- **Published by:** European Data Protection Supervisor
- **Date:** 2025-09-15
- **URL:** `https://www.edps.europa.eu/system/files/2025-09/25-09-15_techdispatch-human-oversight_en.pdf`
- **Confidence:** FACT

**Relevant finding.** Human oversight is only meaningful if the human can
genuinely disagree; well-designed explanation can *reduce* automation bias by
encouraging critical engagement.

**Implementation implication.** Two things follow, and they shaped the approval
console directly:

1. **The human must be able to see the counter-argument, not only the pitch.** So
   the approval console shows the failed policy checks, the best rejected
   alternative (including the deliberately blocked `WAIT_AND_HOLD` strategy), cost,
   temperature risk and the affected consignments — not just the recommendation.
   `test_approval_request_gives_the_human_everything_needed` asserts the payload
   is sufficient to disagree with.
2. **Approval must be a real, recorded act.** Rejection and review-request are
   first-class transitions, and every outcome is written to the ledger, including
   denials.

**Honest limitation.** The interface can make disagreement *possible*; it cannot
make it *happen*. SANJEEVANI does not measure whether a human actually engages
critically, and no claim is made that it reduces automation bias. That would need
a user study. **HYPOTHESIS:** requiring a role-matched approval with visible
counter-evidence produces more considered decisions than a bare Approve button.
Untested here.

---

## 4. Supply-chain resilience and digital twins

### *A digital supply chain twin for managing the disruption risks and resilience in the era of Industry 4.0*

- **Authors:** Dmitry Ivanov, Alexandre Dolgui
- **Venue:** *Production Planning & Control*, 32(9)
- **Year:** 2021
- **DOI:** `10.1080/09537287.2020.1768450` — `https://www.tandfonline.com/doi/full/10.1080/09537287.2020.1768450`
- **Confidence:** FACT

### *Intelligent digital twin (iDT) for supply chain stress-testing, resilience and viability*

- **Author:** Dmitry Ivanov
- **Venue:** *International Journal of Production Economics*
- **Year:** 2023
- **URL:** `https://www.sciencedirect.com/science/article/abs/pii/S0925527323001706`
- **Confidence:** FACT

**Relevant finding.** The ripple effect — a disruption propagating beyond its
origin — is the central object of study in supply-chain disruption analysis, and
digital twins are the proposed vehicle for stress-testing resilience against it.

**Implementation implication.** The ripple effect is the reason A3 is a *graph
traversal* and not a per-node lookup. The hero scenario's Red Sea closure
propagates from the closure node through the biologics corridor to dependent
warehouses and forward consignments — the same "disruption outruns its origin"
structure the literature describes. The impact engine walks this graph and records
its arithmetic per consignment (`test_every_consignment_row_carries_its_arithmetic`).

**Scope boundary — deliberately not taken.** This literature is overwhelmingly
about *optimisation and simulation* — the digital twin as a solver. That is M2,
and this project does not implement it. M3 consumes a `RecoveryPlan` from a
documented fixture. This is the clearest example in the repository of the M1/M3
scope boundary being protected over feature accumulation, and the boundary is
disclosed in the UI rather than hidden.

**What is NOT claimed.** The 12-node network is a *synthetic teaching network*
whose shape follows the dossier. It is not a validated model of any real supply
chain, and its output is not compared against any published result.

---

## 5. Determinism as an engineering property

**INFERENCE, and the conviction this project actually holds most strongly.**

The three threads above converge on a single engineering position that is stated
as this project's own reasoning, not as a finding from any one source:

> If an agentic system's output cannot be reproduced byte-for-byte, then its
> correctness cannot be tested, its failures cannot be attributed, and its
> decisions cannot be audited. Every one of those three properties is a hard
> requirement for a system that approves and executes consequential actions in a
> regulated supply chain.

The sources supply the *motivation* (MAST: verification is where multi-agent
systems fail; automation bias: oversight must be real; WHO: excursions must be
documented) and this project supplies the *mechanism*. The mechanisms are all
tested rather than asserted:

| Property | Mechanism | Test |
|---|---|---|
| Reproducible impact numbers | Closed-form arithmetic over frozen seed data | `test_impact_is_reproducible_across_runs` |
| Reproducible full loop | Deterministic SAP mocks and scenario clock | `test_two_full_runs_produce_identical_impact_and_receipt`, `test_reset_then_rerun_is_bit_identical` |
| Reproducible audit chain | SHA-256 chained append-only ledger | `test_two_full_runs_produce_the_identical_ledger_hash_chain` |
| No LLM in authoritative numbers | LLM disabled; claims are structured | `test_a14_free_text_never_parameterizes_a_claim` |

**HYPOTHESIS:** byte-level reproducibility will matter more, not less, as agentic
systems move into regulated decision-making, because it is the precondition for
any independent verification of a decision. This is a prediction and is offered as
one.

---

## 6. Competitive and vendor sources

Vendor capability claims are recorded in
[`competitive-benchmark.md`](competitive-benchmark.md), with an explicit rule:
**vendor material is reported as the vendor's claim, dated, and never presented as
independently verified.**

## 7. Sources that could not be verified

Recorded because an honest registry includes its failures.

| Attempted | Result | Consequence |
|---|---|---|
| Specific SAP OData contract paths for IBP planning scenarios, TM freight orders and Ariba procurement actions | A targeted search returned no usable results, and no SAP API reference was retrieved | The SAP integrations are described and named as **SAP-shaped** throughout, never as API-faithful to a specific published contract. This is why `docs/mock-boundaries.md` says "shaped" rather than "verified". Verifying the real contracts is a named next step. |
| Per-competitor feature-level sourcing for Kinaxis and SAP IBP | Only vendor-authored material was reachable | No feature-parity claim is made for these products. The benchmark document restricts itself to what the vendor states about itself. |

---

## Summary of what actually influenced the build

Ranked by how much the implementation would have differed without it:

1. **MAST** — the deterministic-trust-boundary architecture, and the refusal to let
   generated text carry a number.
2. **Automation bias literature** — the approval console showing counter-evidence
   and accepting rejection as a first-class outcome.
3. **WHO TRS 961 Annex 9** — the shape of the hero scenario and the provenance
   labelling on every compliance check.
4. **Ivanov / Dolgui** — graph traversal as the ripple-effect model, and the
   confidence to leave the optimiser to M2 rather than fake it.

## Access dates

All URLs accessed **2026-09-15**. Metadata (venue, year, DOI) as reported by the
publisher or index at that time.
