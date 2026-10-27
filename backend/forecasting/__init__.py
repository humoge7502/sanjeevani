"""Forecasting: a trained disruption-duration prior, advisory and dependency-free.

The model is trained offline by ``scripts/train_forecasting_model.py`` and served
by a pure-Python tree walk, so the runtime carries no numeric stack. It supplies a
*prior* only: A2 attaches it to a scenario's provenance, clearly labelled, and it
never reaches a shared contract unless ``forecasting.enabled`` says it may.
"""

from backend.forecasting.duration import (
    MODEL_FEATURES,
    MODEL_PATH,
    PROVENANCE,
    DurationForecast,
    ForecastUnavailable,
    TreeEnsemble,
    features_for_event,
    forecast_enabled,
    load_model,
    model_available,
    model_metadata,
    predict_duration,
    reset_cache,
)

__all__ = [
    "MODEL_FEATURES",
    "MODEL_PATH",
    "PROVENANCE",
    "DurationForecast",
    "ForecastUnavailable",
    "TreeEnsemble",
    "features_for_event",
    "forecast_enabled",
    "load_model",
    "model_available",
    "model_metadata",
    "predict_duration",
    "reset_cache",
]
