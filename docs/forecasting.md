# Forecasting — the one trained model, and why it cannot touch a number

> **Status:** implemented and advisory. `forecasting.enabled: false` by default.
> Rationale and rejected alternatives: [ADR 0012](decisions/0012-trained-model-is-exported-not-pickled.md).

The product's trust model is that **every audited number is deterministic and
traceable**. A trained model is by nature neither. This document is about how the
two coexist without weakening either.

---

## 1. The rule

**The model always runs. Its output reaches a number only when an operator says so.**

| | |
|---|---|
| Where the prediction goes | `Scenario.parameters_source.duration_prior`, with `provenance: "trained_model"` |
| When it changes the duration | Never by default. Only when `forecasting.enabled` is true. |
| And even then | A2 blends it with the signal claim and **clamps** the result against `scenario.clamp.duration_days` |
| What it can never do | Widen a claim past what an operator authorised, or reach a shared contract directly |
| LLM involvement | None, anywhere in this path |

Attaching the prediction *always* is deliberate: a reviewer can see what the model
would have said next to the number that was actually used. Suppressing it when
disabled would hide the evidence; applying it unconditionally would compromise the
ledger.

```jsonc
// Scenario.parameters_source
{
  "origin": "signal_claim (seeded feed)",
  "claimed_duration_days": 10.0,
  "clamped": { "duration_days": false, "...": "..." },
  "duration_prior": {
    "duration_days": 11.482,
    "interval_low_days": 7.822,
    "interval_high_days": 15.142,
    "provenance": "trained_model",
    "applied": false,
    "blend_weight": 0.5
  }
}
```

---

## 2. Training

```bash
python -m pip install -r requirements-dev.txt -r requirements-ml.txt
python scripts/train_forecasting_model.py
```

The script generates the dataset, splits it 60/20/20, fits a
`GradientBoostingRegressor`, evaluates on the held-out test split, and refuses to
write an artifact if the model does not beat the mean baseline (non-zero exit).

### Features — all read from seed data or a signal claim

`capacity_loss_pct`, `corridor_exposed`, `congestion_index`, `node_risk_score`,
`chokepoint`, `prior_incidents_90d`, `carrier_diversion_pct`, `vessel_wait_days`,
`season_month`, `lane_transit_days`, `alt_route_available`, `is_tariff_shock`.

Two of these — `prior_incidents_90d` and `carrier_diversion_pct` — cannot be derived
from this repository at all. They are declared in `config/settings.yaml` with
documented defaults rather than invented at the call site.

### Measured, on the held-out test split

| Metric | Value |
|---|---|
| Test MAE | **3.66 days** |
| Mean-baseline MAE | 14.18 days |
| Test RMSE | 5.98 days |
| Test R² | **0.905** |

---

## 3. The dataset is synthetic; the pipeline is not

Every row is generated, and the generator's coefficients are drawn from the cited
ranges in this repository's own evidence package. Each anchor is recorded in the
artifact's `evidence_anchors`:

| Anchor | Evidence record |
|---|---|
| Cape re-route adds 10–14 days | `SJEV-CASE-001` |
| Red Sea transits fell by up to 90% | `SJEV-CASE-002` |
| Port congestion at a four-year high | `SJEV-WEB-PRT-001` |
| US East/Gulf port strike backlog | `SJEV-CASE-021` |
| Panama Canal drought | `SJEV-CASE-020` |
| Tariff volatility duration | `SJEV-CASE-015` |

**Replacing `data/ml/disruption_duration_training.csv` with real historical events
and re-running the script is a data swap, not a code change.** That is the whole
reason the pipeline is real while the rows are not.

The split is a shuffled i.i.d. one. A real historical dataset would need a
**time-based** split to prove temporal generalisation, and the artifact's metadata
says so rather than implying otherwise.

---

## 4. Serving: no numeric stack, no pickle

The artifact is **portable JSON**, and `backend/forecasting/duration.py` walks the
trees in pure Python:

```
prediction = base_value + learning_rate x sum(leaf(node))
```

`init="zero"` is set at training time specifically so there is no initial-estimator
replay to get wrong.

| Consequence | Why it matters |
|---|---|
| No numpy / scipy / pandas / scikit-learn / joblib at runtime | A CVE in any of them is off the request path |
| No pickle deserialisation | An artifact is data, not an execution primitive |
| Readable model | Every split threshold and leaf value is inspectable |
| No version drift | Training and serving cannot disagree about an ABI |

Both are verified, not asserted:

- `test_portable_evaluator_reproduces_scikit_learn` — trains a real model in the
  test, exports it with the production exporter, and asserts the pure-Python
  evaluator matches `model.predict` to `1e-9`.
- `test_serving_requires_no_numeric_stack` — imports the runtime module in a clean
  subprocess and fails if any banned module appears in `sys.modules`.

---

## 5. Failure is "no forecast", never a number

```python
predict_duration(features)   # raises ForecastUnavailable if there is no artifact
```

`ForecastUnavailable` is a distinct type so a caller cannot treat absence as a
prediction of zero. `_duration_prior()` in A2 catches every failure and returns
`None`, so `forecasting` is never load-bearing: a checkout without `data/ml/`
passes every gate, and `scripts/health.py` reports the model's provenance as
available or absent rather than assuming either.

A partial feature vector is refused outright —
`test_a_partial_feature_vector_is_refused` — because a prediction from missing
inputs would be silently wrong.

---

## 6. What is not claimed

- **This is not a demand forecast.** It estimates a *disruption duration*.
- **3.66 days of MAE is a prior's accuracy.** Nobody should plan a shipment on it.
- **The uncertainty band is `point ± test MAE`**, which is a typical-error band and
  not a calibrated prediction interval. A calibrated interval needs quantile
  regression, and the response says so (`method.band`) rather than over-claiming.
- **No foundation model.** Chronos, TimesFM, Moirai and TiRex appear in the research
  shelf and none is used: hundreds of megabytes, no provenance for this problem, and
  an inference stack on the critical path for an advisory number.

---

## 7. Inspecting it

```bash
python -m pytest tests/unit/test_forecasting.py -v      # 17 tests
curl -s localhost:8787/api/forecasting | python -m json.tool
python scripts/health.py                                # no forecasting gate on purpose
```
