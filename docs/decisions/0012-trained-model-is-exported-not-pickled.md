# 0012 — The trained model is exported to portable JSON, not pickled

**Status:** Accepted
**Date:** 2026-10-25

## Context

The build previously shipped **no trained model at all**, which was defensible but
left a real hole: M1's scenario duration was a number read out of a signal claim
and clamped. The concrete request was to add a trained model. The concrete risk is
that a trained model quietly contaminates the property this whole product rests on:
**every audited number is deterministic and traceable.**

There is a second, harder question: how does a model *artifact* get to the runtime?
The obvious answer — `joblib.dump` / `pickle` — is also the dangerous one. A pickle
is not data. Deserialising one executes code, so an artifact that can be swapped is
a remote-code-execution primitive, and every artifact is tied to the exact library
version that wrote it. `numpy`, `scipy`, `pandas` and `scikit-learn` would all have
to enter the runtime image to load it.

## Decision

**Train with scikit-learn; export to a plain JSON tree ensemble; serve it with pure
Python. And make the model advisory by default.**

`scripts/train_forecasting_model.py` is the only place in the repository that
imports a numeric-stack library. It flattens a fitted
`GradientBoostingRegressor` into:

```json
{
  "kind": "gradient_boosting_regressor",
  "features": ["capacity_loss_pct", "corridor_exposed", "..."],
  "learning_rate": 0.05,
  "base_value": 0.0,
  "trees": [ { "nodes": [ {"feature": "...", "threshold": 0.42, "left": 1, "right": 2}, {"leaf": -0.013} ] } ],
  "meta": { "metrics": {...}, "dataset": {"sha256": "..."}, "evidence_anchors": {...} }
}
```

`init="zero"` is set specifically so the evaluator is exactly
`base_value + learning_rate * sum(leaf)`, with no initial-estimator replay to get
wrong.

`backend/forecasting/duration.py` then walks the trees. Its result:

| Property | Benefit |
|---|---|
| No runtime ML dependency | The container carries no numpy, scipy, pandas, scikit-learn or joblib — so a CVE in any of them is off the request path. |
| No deserialisation gadget | The artifact is JSON. There is nothing to execute. |
| Readable model | Every split threshold and leaf value is inspectable by a reviewer. |
| No version drift | Training and serving cannot disagree about a library ABI. |

**The export is verified, not assumed.**
`test_portable_evaluator_reproduces_scikit_learn` trains a real model, exports it,
and asserts the pure-Python evaluator matches `model.predict` to `1e-9`.
`test_serving_requires_no_numeric_stack` imports the runtime module in a clean
subprocess and fails if any of the banned modules appear in `sys.modules`.

## The advisory boundary, in code

The model **always** runs and its prediction is attached to
`Scenario.parameters_source.duration_prior` with `provenance: "trained_model"`, so
a reviewer can see what it would have said. It influences the scenario duration
**only** when `forecasting.enabled` is true, and even then A2 clamps the blend
against `scenario.clamp.duration_days` — so a trained model can never widen a claim
past what an operator authorised.

`_duration_prior()` catches every failure and returns `None`. No artifact means *no
forecast*, never a prediction of zero. `ForecastUnavailable` exists as a distinct
type so a caller cannot accidentally treat absence as a number.

## Honest disclosure of the dataset

The training rows are **synthetic**, generated from the cited ranges in this
repository's own evidence package — the Cape re-route's 10–14 days
(`SJEV-CASE-001`), the 90% transit drop (`SJEV-CASE-002`), four-year-high port
congestion (`SJEV-WEB-PRT-001`) — and each anchor is recorded in the artifact's
`evidence_anchors`. The **pipeline** is real; only the rows are constructed.

Measured on the held-out test split:

| Metric | Value |
|---|---|
| Test MAE | **3.66 days** |
| Mean baseline MAE | 14.18 days |
| Test R² | 0.905 |

A model that did not beat the mean baseline would fail its own training script
(non-zero exit) rather than ship.

## Alternatives considered

**`joblib.dump` the sklearn model.** Rejected: puts numpy/scipy/sklearn/joblib in
the runtime image and turns the artifact into a code-execution surface.

**ONNX.** Better than pickle and genuinely portable, but it adds a runtime
dependency and an opaque binary; a JSON tree walk is ~40 lines and fully readable.

**A time-series foundation model (Chronos / TimesFM / Moirai), as the dossier's
research shelf suggests.** Rejected for this build: hundreds of megabytes, no
provenance for this problem, and it would put an inference stack on the critical
path for a number that is advisory anyway. The extension point is a *dataset* swap,
not a model swap.

**Use the model to compute a contract number.** Rejected outright. It would make
the ledger unreproducible and break the trust model the rest of the product is
built on.

## Consequences

**Good.** A real trained model with real held-out metrics, a dependency-free
runtime, a verifiable export, and a hard boundary preventing it from touching an
audited number.

**Bad, and admitted.** The dataset is synthetic, and the split is a shuffled
i.i.d. one. A real historical dataset would need a **time-based** split to prove
temporal generalisation; the metadata says so rather than implying otherwise.
3.66 days of MAE is a prior's accuracy, not a forecast anyone should plan a
shipment on.

## Enforcement

- `tests/unit/test_forecasting.py` — 17 tests, including the sklearn-equivalence
  proof, the clean-interpreter dependency check, refusal on a missing/partial
  input, and the advisory-boundary assertions in both directions.
- `scripts/health.py` is unaffected: the model is optional, so a checkout without
  `data/ml/` still passes every gate.
