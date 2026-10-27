"""Unit tests: the trained disruption-duration forecaster.

The three properties that matter, in order:

  1. **The portable evaluator is correct.** It re-implements what scikit-learn's
     ``predict`` does, by hand. An export nobody verifies is a guess, so a test
     trains a real model and asserts the two agree to floating-point tolerance.
  2. **Absence is handled honestly.** A missing artifact must raise, never return
     a number.
  3. **The model is advisory.** It must not change a scenario's duration unless
     it is explicitly enabled, and even then it must stay inside the clamps A2
     imposes. A trained model that can widen a claim is not a prior, it is an
     unreviewed decision.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from backend.agents.pipeline import run_m1
from backend.config import settings
from backend.forecasting import (
    MODEL_PATH,
    ForecastUnavailable,
    TreeEnsemble,
    features_for_event,
    forecast_enabled,
    model_available,
    model_metadata,
    predict_duration,
    reset_cache,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_training_script():
    """Import scripts/train_forecasting_model.py without it being a package.

    The module is registered in ``sys.modules`` before execution because
    ``@dataclass`` resolves annotations through the module registry, and a module
    that exists only as a local variable cannot be found.
    """
    path = REPO_ROOT / "scripts" / "train_forecasting_model.py"
    spec = importlib.util.spec_from_file_location("train_forecasting_model", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["train_forecasting_model"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The artifact
# ---------------------------------------------------------------------------


def test_the_model_artifact_is_present():
    """If this fails, run: python scripts/train_forecasting_model.py"""
    assert MODEL_PATH.is_file(), f"no model artifact at {MODEL_PATH}"
    assert model_available() is True


def test_metadata_declares_its_metrics_dataset_and_disclosure():
    metadata = model_metadata()
    assert metadata["available"] is True
    assert metadata["provenance"] == "trained_model"
    assert metadata["model_version"]
    assert metadata["features"]
    assert metadata["artifact_sha256"]
    metrics = metadata["metrics"]
    assert metrics["test_mae_days"] > 0
    # The model must actually beat predicting the mean, or it is not worth shipping.
    assert metrics["test_mae_days"] < metrics["baseline_mae_days"]
    assert metrics["test_r2"] > 0.5
    dataset = metadata["dataset"]
    assert dataset["sha256"]
    assert "synthetic" in dataset["kind"]
    assert "ADVISORY ONLY" in metadata["disclosure"]


def test_feature_order_is_frozen_in_the_artifact():
    """A reordered feature list read by old code would silently mis-map columns."""
    from backend.forecasting.duration import MODEL_FEATURES

    metadata = model_metadata()
    assert list(metadata["features"]) == list(MODEL_FEATURES)


# ---------------------------------------------------------------------------
# The portable evaluator must agree with scikit-learn
# ---------------------------------------------------------------------------


def test_portable_evaluator_reproduces_scikit_learn():
    sklearn = pytest.importorskip("sklearn")
    del sklearn
    import numpy as np
    from sklearn.ensemble import GradientBoostingRegressor

    module = _load_training_script()
    rng = np.random.default_rng(7)
    features = module.FEATURES
    x = rng.normal(size=(400, len(features)))
    y = (
        3.0
        + 2.0 * x[:, 0]
        - 1.5 * x[:, 1]
        + 0.8 * np.abs(x[:, 2])
        + rng.normal(scale=0.3, size=400)
    )
    # A mix of continuous and "categorical" columns, mirroring the real feature set.
    x[:, 1] = np.round(np.abs(x[:, 1]))
    x[:, 4] = np.round(np.abs(x[:, 4]))

    model = GradientBoostingRegressor(
        n_estimators=40, learning_rate=0.1, max_depth=3, random_state=7, init="zero"
    )
    model.fit(x, y)

    payload = module.export_portable(
        model,
        feature_names=features,
        learning_rate=model.learning_rate,
        base_value=0.0,
    )
    ensemble = TreeEnsemble(payload)

    probe = rng.normal(size=(25, len(features)))
    probe[:, 1] = np.round(np.abs(probe[:, 1]))
    probe[:, 4] = np.round(np.abs(probe[:, 4]))
    expected = model.predict(probe)
    for index in range(probe.shape[0]):
        row = {name: float(probe[index][i]) for i, name in enumerate(features)}
        assert ensemble.predict(row) == pytest.approx(float(expected[index]), abs=1e-9)


def test_the_export_is_readable_json_not_a_pickle():
    """A pickle deserialisation is a code-execution primitive; the export is data."""
    import json

    payload = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    assert payload["kind"] == "gradient_boosting_regressor"
    assert isinstance(payload["trees"], list)
    assert payload["trees"][0]["nodes"]
    first = payload["trees"][0]["nodes"][0]
    assert "leaf" in first or {"feature", "threshold", "left", "right"} <= set(first)


def test_serving_requires_no_numeric_stack():
    """Import the runtime module in a clean interpreter and check what it pulled in.

    This is the point of exporting to JSON: the container carries no numpy, no
    scipy, no scikit-learn and no joblib, so a vulnerability in any of them cannot
    reach the running service.
    """
    code = (
        "import sys;"
        "import backend.forecasting as f;"
        "f.model_available();"
        "banned=[m for m in ('sklearn','numpy','scipy','joblib','pandas','torch') "
        "if m in sys.modules];"
        "print(','.join(banned))"
    )
    result = subprocess.run(  # noqa: S603 - a fixed argv, no shell
        [sys.executable, "-c", code],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "", f"runtime imported {result.stdout.strip()}"


# ---------------------------------------------------------------------------
# Refusing to invent a number
# ---------------------------------------------------------------------------


def test_a_missing_artifact_raises_rather_than_guessing(tmp_path):
    with pytest.raises(ForecastUnavailable):
        predict_duration({}, path=tmp_path / "nope.json")


def test_an_unreadable_artifact_raises(tmp_path):
    broken = tmp_path / "model.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ForecastUnavailable):
        predict_duration({}, path=broken)


def test_a_partial_feature_vector_is_refused():
    """A prediction from missing inputs would be silently wrong, so it is refused."""
    with pytest.raises(ValueError, match="Missing model feature"):
        predict_duration({"capacity_loss_pct": 50.0})


def test_an_empty_tree_ensemble_is_refused():
    with pytest.raises(ForecastUnavailable):
        TreeEnsemble({"features": [], "learning_rate": 0.1, "base_value": 0.0, "trees": []})


# ---------------------------------------------------------------------------
# Prediction behaviour
# ---------------------------------------------------------------------------


def test_prediction_is_deterministic():
    features = {
        "capacity_loss_pct": 85.0,
        "corridor_exposed": 1.0,
        "congestion_index": 0.5,
        "node_risk_score": 0.8,
        "chokepoint": 1.0,
        "prior_incidents_90d": 2.0,
        "carrier_diversion_pct": 35.0,
        "vessel_wait_days": 6.0,
        "season_month": 9.0,
        "lane_transit_days": 27.0,
        "alt_route_available": 0.0,
        "is_tariff_shock": 0.0,
    }
    first = predict_duration(features)
    second = predict_duration(features)
    assert first.as_dict() == second.as_dict()


def test_a_worse_disruption_is_predicted_to_last_longer():
    """Monotonicity in the main driver, which is the sanity check that matters."""
    base = {
        "capacity_loss_pct": 20.0,
        "corridor_exposed": 0.0,
        "congestion_index": 0.2,
        "node_risk_score": 0.2,
        "chokepoint": 0.0,
        "prior_incidents_90d": 0.0,
        "carrier_diversion_pct": 35.0,
        "vessel_wait_days": 1.0,
        "season_month": 6.0,
        "lane_transit_days": 20.0,
        "alt_route_available": 1.0,
        "is_tariff_shock": 0.0,
    }
    mild = predict_duration(base).duration_days
    severe = predict_duration({**base, "capacity_loss_pct": 95.0, "corridor_exposed": 1.0}).duration_days
    assert severe > mild


def test_the_band_covers_the_point_estimate_and_is_labelled_as_typical_error():
    features = {
        "capacity_loss_pct": 60.0,
        "corridor_exposed": 1.0,
        "congestion_index": 0.4,
        "node_risk_score": 0.5,
        "chokepoint": 1.0,
        "prior_incidents_90d": 1.0,
        "carrier_diversion_pct": 35.0,
        "vessel_wait_days": 4.0,
        "season_month": 3.0,
        "lane_transit_days": 24.0,
        "alt_route_available": 0.0,
        "is_tariff_shock": 0.0,
    }
    forecast = predict_duration(features)
    assert forecast.interval_low_days <= forecast.duration_days <= forecast.interval_high_days
    assert forecast.duration_days >= 1.0
    assert forecast.provenance == "trained_model"
    assert forecast.method["llm_involved"] is False
    assert "NOT a calibrated prediction interval" in forecast.method["band"]


def test_features_come_from_seed_data_and_never_from_free_text():
    m1 = run_m1()
    net = m1.network
    closure = next(e for e in m1.events if e.event_type == "PORT_CLOSURE")
    features = features_for_event(closure, net)
    from backend.forecasting.duration import MODEL_FEATURES

    assert set(features) == set(MODEL_FEATURES)
    # Suez is the seeded chokepoint and the seeded lane is the only Red Sea lane.
    assert features["chokepoint"] == 1.0
    assert features["corridor_exposed"] == 1.0
    assert features["capacity_loss_pct"] == pytest.approx(85.0)
    # The hostile free text on the signal must not appear anywhere.
    serialised = str(features)
    assert "approve" not in serialised.lower()
    assert "cost=0" not in serialised


# ---------------------------------------------------------------------------
# The advisory boundary
# ---------------------------------------------------------------------------


def test_forecasting_is_disabled_by_default():
    assert forecast_enabled() is False
    assert settings()["forecasting"]["enabled"] is False


def test_a2_records_the_prior_without_letting_it_change_the_duration():
    """With the model off, the number is the claim; the prior is pure evidence."""
    m1 = run_m1()
    scenario = next(s for s in m1.scenarios if s.horizon_days == 10)
    prior = scenario.parameters_source["duration_prior"]
    assert prior is not None, "the prior should be attached as evidence"
    assert prior["provenance"] == "trained_model"
    assert prior["applied"] is False
    # The duration is exactly the (clamped) claim, untouched.
    assert scenario.duration_days == pytest.approx(10.0)
    assert scenario.parameters_source["claimed_duration_days"] == pytest.approx(10.0)


def test_enabling_the_model_blends_the_prior_but_stays_inside_the_clamps(monkeypatch):
    """The trained model may influence the duration -- never beyond the bounds."""
    import backend.forecasting as forecasting_pkg
    from backend.config import reload_config

    monkeypatch.setattr(
        "backend.agents.verification.settings",
        lambda: {
            **settings(),
            "forecasting": {**settings()["forecasting"], "enabled": True},
        },
    )
    # `_duration_prior` imports this from the package at call time, so patching the
    # package attribute is what the code actually consults.
    monkeypatch.setattr(forecasting_pkg, "forecast_enabled", lambda: True)
    try:
        m1 = run_m1()
        scenario = next(s for s in m1.scenarios if s.horizon_days == 10)
        prior = scenario.parameters_source["duration_prior"]
        assert prior["applied"] is True
        bounds = settings()["scenario"]["clamp"]["duration_days"]
        assert bounds[0] <= scenario.duration_days <= bounds[1]
        blended = (
            0.5 * prior["duration_days"]
            + 0.5 * scenario.parameters_source["claimed_duration_days"]
        )
        assert scenario.duration_days == pytest.approx(
            min(max(blended, bounds[0]), bounds[1]), abs=1e-6
        )
    finally:
        reload_config()
        reset_cache()
