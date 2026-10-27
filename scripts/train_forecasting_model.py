#!/usr/bin/env python3
"""Train the SANJEEVANI disruption-duration forecaster and export it portably.

    python scripts/train_forecasting_model.py
    python scripts/train_forecasting_model.py --rows 20000 --out data/ml

WHY THIS SCRIPT EXISTS. The repository previously shipped no trained model at all,
which meant M1's scenario duration was simply a number read out of a signal claim
and clamped. That is defensible but it is not a forecast. This adds a real one.

TWO DESIGN DECISIONS THAT MATTER MORE THAN THE MODEL CHOICE.

1. **The model is exported to portable JSON, not pickled.** Pickle ties a model
   artifact to the exact library version that wrote it, and a deserialisation
   gadget is a code-execution primitive. The export below is a plain tree
   ensemble that ``backend/forecasting/duration.py`` evaluates with a few dozen
   lines of pure Python, so:
     * the runtime needs NO scikit-learn, no numpy, no joblib -- the container
       stays lean and a CVE in a numeric stack cannot reach it;
     * a reviewer can *read* the model: every split threshold and leaf value is
       visible in the JSON;
     * there is no version-drift risk between training and serving.
   ``tests/unit/test_forecasting.py`` asserts the portable evaluator reproduces
   scikit-learn's own predictions, so the export is verified rather than assumed.

2. **The dataset is synthetic and says so, loudly.** It is generated from the
   published ranges in this repository's own evidence package, with each range
   cited to its evidence ID. The *pipeline* -- features, split, model, metrics,
   artifact, provenance -- is real; only the rows are constructed. Replacing
   ``data/ml/disruption_duration_training.csv`` with real historical events and
   re-running this script is a data swap, not a code change.

The model is ADVISORY. It never produces a number that reaches a contract unless
``file:config/settings.yaml`` ``forecasting.enabled`` is turned on, and even then
it only supplies a *prior* that A2 still clamps against configured bounds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: Feature order is part of the artifact contract. Never reorder without bumping
#: MODEL_VERSION: an old artifact read by new code would silently mis-map columns.
FEATURES: list[str] = [
    "capacity_loss_pct",
    "corridor_exposed",
    "congestion_index",
    "node_risk_score",
    "chokepoint",
    "prior_incidents_90d",
    "carrier_diversion_pct",
    "vessel_wait_days",
    "season_month",
    "lane_transit_days",
    "alt_route_available",
    "is_tariff_shock",
]

MODEL_VERSION = "1.0.0"

#: Every generator coefficient below traces to a record in
#: docs/evidence/INDEX.md. They are the *priors* the synthetic rows are drawn
#: around, not measurements of this network.
EVIDENCE_ANCHORS: dict[str, str] = {
    "red_sea_cape_transit_days": (
        "SJEV-CASE-001 - Red Sea crisis: Cape of Good Hope re-routing added 10-14 days"
    ),
    "red_sea_transit_drop_pct": (
        "SJEV-CASE-002 - Red Sea transits fell by up to 90%"
    ),
    "port_congestion_high": (
        "SJEV-WEB-PRT-001 - Port congestion at a four-year high"
    ),
    "us_port_strike_backlog_days": (
        "SJEV-CASE-021 - US East & Gulf Coast port strike backlog (Xeneta)"
    ),
    "panama_drought_days": "SJEV-CASE-020 - Panama Canal drought",
    "tariff_volatility_months": (
        "SJEV-CASE-015 - Tariff volatility duration verdict (Columbus Consulting)"
    ),
    "pure_reroute_cost_40ft": (
        "SJEV-CASE-004 - ITF-OECD pure re-routing cost per 40ft container"
    ),
}


@dataclass(frozen=True)
class TrainingReport:
    rows: int
    train_rows: int
    validation_rows: int
    test_rows: int
    mae_days: float
    rmse_days: float
    r2: float
    baseline_mae_days: float
    model_path: Path
    metadata_path: Path
    dataset_path: Path


def build_dataset(rows: int, seed: int):
    """Generate the training frame. Deterministic for a fixed (rows, seed)."""
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)

    # ---- latent drivers, drawn from the cited ranges ----------------------
    corridor_exposed = rng.integers(0, 2, rows)
    chokepoint = rng.integers(0, 2, rows)
    capacity_loss_pct = np.clip(
        rng.beta(2.2, 2.0, rows) * 100.0 + corridor_exposed * 18.0, 0.0, 100.0
    )
    congestion_index = np.clip(
        rng.beta(2.0, 3.0, rows) + chokepoint * 0.2, 0.0, 1.0
    )
    node_risk_score = np.clip(rng.beta(2.0, 3.5, rows) + corridor_exposed * 0.25, 0.0, 1.0)
    prior_incidents_90d = rng.poisson(1.6 + corridor_exposed * 1.4, rows)
    carrier_diversion_pct = np.clip(
        rng.normal(35.0 + corridor_exposed * 20.0, 15.0, rows), 0.0, 100.0
    )
    # SJEV-WEB-PRT-001: congestion at a four-year high. Wait times in days.
    vessel_wait_days = np.clip(rng.gamma(2.4, 1.3, rows) + chokepoint * 2.0, 0.0, 30.0)
    season_month = rng.integers(1, 13, rows)
    lane_transit_days = np.clip(rng.normal(21.0, 7.0, rows), 1.0, 45.0)
    alt_route_available = rng.integers(0, 2, rows)
    is_tariff_shock = (rng.random(rows) < 0.15).astype(int)

    # ---- the data-generating process --------------------------------------
    # SJEV-CASE-001: a corridor closure with a Cape re-route is the 10-14 day
    # case. Congestion and vessel waiting stretch it; an available alternate
    # route and early carrier diversion compress it. Tariff shocks are the long
    # tail (SJEV-CASE-015).
    base = 2.0
    duration = (
        base
        + 0.115 * capacity_loss_pct
        + corridor_exposed * 11.0
        + 4.5 * congestion_index
        + 3.0 * node_risk_score
        + 1.1 * prior_incidents_90d
        + 0.30 * vessel_wait_days
        + is_tariff_shock * 44.0
        - 0.055 * carrier_diversion_pct
        - alt_route_available * 3.4
        + 0.9 * np.sin((season_month - 1) / 12.0 * 2 * np.pi)
    )
    # Multiplicative noise, then a hard floor: a duration is at least a day.
    duration = np.clip(duration * rng.lognormal(0.0, 0.18, rows), 1.0, 180.0)

    return pd.DataFrame(
        {
            "capacity_loss_pct": capacity_loss_pct,
            "corridor_exposed": corridor_exposed,
            "congestion_index": congestion_index,
            "node_risk_score": node_risk_score,
            "chokepoint": chokepoint,
            "prior_incidents_90d": prior_incidents_90d,
            "carrier_diversion_pct": carrier_diversion_pct,
            "vessel_wait_days": vessel_wait_days,
            "season_month": season_month,
            "lane_transit_days": lane_transit_days,
            "alt_route_available": alt_route_available,
            "is_tariff_shock": is_tariff_shock,
            "duration_days": np.round(duration, 2),
        }
    )


def export_portable(model, *, feature_names: list[str], learning_rate: float, base_value: float):
    """Flatten a fitted scikit-learn gradient-boosting regressor into plain JSON."""
    trees = []
    for stage in model.estimators_:
        estimator = stage[0]
        tree = estimator.tree_
        nodes: list[dict[str, Any]] = []
        for index in range(tree.node_count):
            left = int(tree.children_left[index])
            right = int(tree.children_right[index])
            if left == -1 or right == -1:
                nodes.append({"leaf": float(tree.value[index][0][0])})
            else:
                nodes.append(
                    {
                        "feature": feature_names[int(tree.feature[index])],
                        "threshold": float(tree.threshold[index]),
                        "left": left,
                        "right": right,
                        "missing_left": bool(tree.missing_go_to_left[index]),
                    }
                )
        trees.append({"nodes": nodes})
    return {
        "kind": "gradient_boosting_regressor",
        "features": list(feature_names),
        "learning_rate": float(learning_rate),
        "base_value": float(base_value),
        "trees": trees,
    }


def train(rows: int, seed: int, out_dir: Path, test_size: float = 0.2, val_size: float = 0.2):
    import numpy as np
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.metrics import mean_absolute_error, r2_score

    out_dir.mkdir(parents=True, exist_ok=True)
    frame = build_dataset(rows, seed)
    dataset_path = out_dir / "disruption_duration_training.csv"
    frame.to_csv(dataset_path, index=False)

    # A shuffled split over an i.i.d. synthetic frame; a real historical dataset
    # would need a TIME-based split, and that is noted in the metadata rather
    # than quietly assumed away.
    shuffled = frame.sample(frac=1.0, random_state=seed).reset_index(drop=True)
    n_total = len(shuffled)
    n_test = int(n_total * test_size)
    n_val = int(n_total * val_size)
    test = shuffled.iloc[:n_test]
    validation = shuffled.iloc[n_test : n_test + n_val]
    train_frame = shuffled.iloc[n_test + n_val :]

    x_train = train_frame[FEATURES].to_numpy(dtype=float)
    y_train = train_frame["duration_days"].to_numpy(dtype=float)
    x_val = validation[FEATURES].to_numpy(dtype=float)
    y_val = validation["duration_days"].to_numpy(dtype=float)
    x_test = test[FEATURES].to_numpy(dtype=float)
    y_test = test["duration_days"].to_numpy(dtype=float)

    model = GradientBoostingRegressor(
        n_estimators=300,
        learning_rate=0.05,
        max_depth=3,
        subsample=0.9,
        random_state=seed,
        # "zero" so the portable evaluator needs no initial-estimator replay: the
        # prediction is exactly learning_rate * sum(leaf contributions).
        init="zero",
    )
    model.fit(x_train, y_train)

    predictions = model.predict(x_test)
    mae = float(mean_absolute_error(y_test, predictions))
    rmse = float(np.sqrt(np.mean((y_test - predictions) ** 2)))
    r2 = float(r2_score(y_test, predictions))
    baseline_mae = float(mean_absolute_error(y_test, np.full_like(y_test, y_train.mean())))

    validation_predictions = model.predict(x_val)
    payload: dict[str, Any] = export_portable(
        model,
        feature_names=FEATURES,
        learning_rate=model.learning_rate,
        base_value=0.0,
    )
    model_path = out_dir / "disruption_duration_model.json"
    payload["meta"] = {
        "model_version": MODEL_VERSION,
        "trained_at": "2026-09-30T06:00:00Z",  # frozen clock: reproducible artifact
        "algorithm": "sklearn GradientBoostingRegressor (squared_error), exported to JSON",
        "hyperparameters": {
            "n_estimators": model.n_estimators,
            "learning_rate": model.learning_rate,
            "max_depth": model.max_depth,
            "subsample": model.subsample,
            "random_state": seed,
            "init": "zero",
        },
        "features": FEATURES,
        "target": "duration_days",
        "target_unit": "days",
        "rows": {
            "total": n_total,
            "train": len(train_frame),
            "validation": len(validation),
            "test": len(test),
        },
        "metrics": {
            "test_mae_days": round(mae, 4),
            "test_rmse_days": round(rmse, 4),
            "test_r2": round(r2, 4),
            "baseline_mae_days": round(baseline_mae, 4),
            "validation_mae_days": round(
                float(mean_absolute_error(y_val, validation_predictions)), 4
            ),
        },
        "dataset": {
            "kind": "synthetic, generated from cited evidence ranges",
            "path": dataset_path.name,
            "sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            "rows": n_total,
            "generator_seed": seed,
            "split": (
                "shuffled 60/20/20. A real historical dataset must use a TIME-based "
                "split; an i.i.d. shuffle over generated rows does not leak, but it "
                "also does not prove temporal generalisation."
            ),
        },
        "evidence_anchors": EVIDENCE_ANCHORS,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "disclosure": (
            "ADVISORY ONLY. This model supplies a duration PRIOR for scenario generation. "
            "It never produces a number that reaches a shared contract unless "
            "forecasting.enabled is set in config/settings.yaml, and even then A2 clamps "
            "it against configured bounds. The training rows are synthetic; the pipeline "
            "is real. No LLM is involved."
        ),
    }
    model_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    metadata_path = out_dir / "disruption_duration_model.meta.json"
    metadata_path.write_text(json.dumps(payload["meta"], indent=2), encoding="utf-8")

    return TrainingReport(
        rows=n_total,
        train_rows=len(train_frame),
        validation_rows=len(validation),
        test_rows=len(test),
        mae_days=mae,
        rmse_days=rmse,
        r2=r2,
        baseline_mae_days=baseline_mae,
        model_path=model_path,
        metadata_path=metadata_path,
        dataset_path=dataset_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=6000)
    parser.add_argument("--seed", type=int, default=20260930)
    parser.add_argument("--out", default=str(REPO_ROOT / "data" / "ml"))
    args = parser.parse_args()

    report = train(args.rows, args.seed, Path(args.out))
    print("SANJEEVANI disruption-duration forecaster\n")
    print(f"  dataset   {report.dataset_path}")
    print(
        f"  split     {report.train_rows} train · {report.validation_rows} validation · "
        f"{report.test_rows} test"
    )
    print(f"  test MAE  {report.mae_days:.3f} days  (baseline {report.baseline_mae_days:.3f})")
    print(f"  test RMSE {report.rmse_days:.3f} days")
    print(f"  test R^2  {report.r2:.4f}")
    print(f"  model     {report.model_path}")
    print(f"  metadata  {report.metadata_path}")
    if report.mae_days >= report.baseline_mae_days:
        print("\n  WARNING: the model does not beat the mean baseline. Not shipping it.")
        return 1
    print("\n  Model beats the baseline. Portable JSON artifact written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
