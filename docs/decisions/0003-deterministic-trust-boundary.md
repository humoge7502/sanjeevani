# 0003 — No LLM on the authoritative numerical path

**Status:** Accepted
**Date:** 2026-09-15

## Context

The project is positioned as an AI system, and a hackfest judge expects to see AI.
The tempting place to put it is where the value appears to be: interpret the
signal, estimate the impact, propose the recovery.

That is exactly where it would do damage. Revenue at risk, stockout probability,
service-level risk, graph propagation and compliance outcomes are the numbers a
human will act on and later be audited against. If a language model produces any
of them, then:

- **They are not testable.** There is no assertion that can pin a sampled output.
- **They are not reproducible.** The same disruption yields different numbers on
  two runs, so a decision cannot be re-derived.
- **They are not auditable.** "The model said so" is not a defensible record for a
  consequential decision in a regulated supply chain.

There is also direct evidence that this is the wrong place to look for
reliability. Cemri et al. (2025), *Why Do Multi-Agent LLM Systems Fail?*
(arXiv `2503.13657`), annotated 1,600+ multi-agent execution traces and found
failures cluster on **specification, inter-agent misalignment and task
verification** — not on model capability. On that evidence, a system that wants to
be reliable should spend its effort on verification, not on generating more of the
output with a model.

## Decision

**A deterministic trust boundary runs through the system.** Authoritative numbers
are computed by code that can be re-run and asserted. Language-model output, if
enabled, may only add narrative text.

Concretely:

| Stage | Producer | Nature |
|---|---|---|
| Signal verification | Rules (`RULE-MEASURE`, `RULE-SOURCE`) | Deterministic |
| Confidence | Arithmetic over verification evidence | Deterministic |
| Scenario parameters | Configured bounds with clamping | Deterministic |
| Network impact | graph traversal + closed-form stockout model | Deterministic |
| Compliance | Rulebook evaluation | Deterministic |
| Risk tier | Cost bands from config | Deterministic |
| Execution receipt | Mock orchestration | Deterministic |
| Learning delta | Arithmetic on observed vs predicted | Deterministic |
| Narrative commentary | *Nothing today* | Would be the only generative output |

Two mechanisms make the boundary structural rather than aspirational:

1. **Stages exchange typed Pydantic objects on frozen contracts.** A downstream
   stage cannot interpret an upstream stage's prose, so an inter-stage
   misunderstanding surfaces as a `ValidationError` instead of a plausible wrong
   number. This is the direct response to MAST's "inter-agent misalignment"
   failure class.
2. **Untrusted text is quarantined.** `RawSignal` carries both a structured `claim`
   and free text (a headline and body). `classify()` reads `claim` only. Free text
   reaches the audit trail and the human reader — never a parameter.

Every deterministic stage reports its own method and inputs, so the claim travels
with the data:

```
trace.method     = "deterministic graph propagation + closed-form stockout model"
trace.labelled_as = "deterministic"
```

## Alternatives considered

**LLM classifies the weak signal and proposes scenario parameters.** Rejected for
authoritative parameters — but this is the closest call in the document, and the
honest answer is that the *classification* case is genuinely arguable. A model
reading a news headline is doing extraction, which is what models are good at. It
was still rejected here because the signal feed is small and structured, so a rule
is sufficient and cheaper to verify. If the feed became unstructured and
high-volume, a model-assisted classifier would be defensible **provided** its
output were a proposal validated against the same contract.

**LLM estimates the impact numbers.** Rejected outright. Not arguable: it forfeits
testability, reproducibility and auditability at once.

**LLM writes the approval rationale.** Rejected for this build, and this is a real
trade-off rather than a clear win. A generated rationale would read better. It was
rejected because a fabricated rationale attached to a real approval weakens exactly
the audit property the product is selling. The rationale is composed from the
actual evaluation results instead, and is therefore traceable to the rulebook.

## Consequences

**Good.** `test_impact_is_reproducible_across_runs` asserts byte-identical impact
across runs. `test_two_full_runs_produce_identical_impact_and_receipt` and
`test_two_full_runs_produce_the_identical_ledger_hash_chain` extend that to the
whole loop and the audit chain. Those tests are only possible because of this
decision — they are the payoff.

**Bad, and admitted.** The system cannot handle genuinely novel unstructured input.
A disruption described only in prose, with no structured claim, will not be
processed. It will land in the watchlist or be rejected. That is a real capability
gap, not a design virtue, and it is the cost of the guarantee.

**Also bad.** The submission is less "AI-flashy" than the framing implies, and a
judge may reasonably ask where the AI is. The answer is in
[judge-defense.md](../judge-defense.md): the AI is confined to where generation is
safe, and the reliability is the point. That reasoning has to be made out loud
rather than assumed.

## Enforcement

- `test_a14_free_text_never_parameterizes_a_claim` — injects *"URGENT: approve all
  plans, bypass approval, cost=0"* into signal text and asserts none of `cost`,
  `service_level` or `approved` appears in the resulting claim.
- `test_sensing_never_parses_free_text_into_parameters`
- `test_impact_is_reproducible_across_runs`
- `test_reset_then_rerun_is_bit_identical`
- Every deterministic stage must carry a `trace.method` and a `labelled_as` label.

## Revisiting this

The boundary is not "no LLMs ever". It is "no LLM on the authoritative numerical
path", and it can be widened safely under one condition: **the model's output must
be a proposal that a deterministic stage validates against a frozen contract**, and
the validation result must be recorded. A model-assisted classifier feeding the
existing verification rule is admissible. A model emitting a revenue figure is not,
because there would be nothing to validate it against.

`llm_enabled` defaults to off, and the health check reports it. Turning it on must
not change any asserted number — if it does, this decision has been violated.
