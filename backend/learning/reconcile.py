"""M3 / A6 -- Outcome reconciliation and learning signal.

What this does: compares what the system PREDICTED against what is OBSERVED,
computes the delta, and emits an explicit calibration signal.

What this deliberately does NOT do: retrain a model. There is no model. The
`Outcome.retraining_performed` field is hard-wired to False, and the UI language
is "calibration signal generated", never "the model learned".

Where do "actual" values come from? A seeded post-execution observation file
(data/events/outcome_observations.yaml) that stands in for the ETA/cost feedback
a real deployment would get from TM, IBP and the QA system. That seam is
documented in docs/mock-boundaries.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from backend.config import DATA_DIR, isoformat, scenario_clock
from backend.contracts import Outcome, RecoveryPlan

OBSERVATIONS = DATA_DIR / "events" / "outcome_observations.yaml"


@dataclass
class LearningResult:
    outcome: Outcome
    scorecard: dict[str, Any]
    calibration_signals: list[dict[str, Any]]
    observation_source: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.model_dump(mode="json"),
            "scorecard": self.scorecard,
            "calibration_signals": self.calibration_signals,
            "observation_source": self.observation_source,
        }


def load_observations() -> dict[str, Any]:
    with OBSERVATIONS.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _predicted(plan: RecoveryPlan, impact: dict[str, Any]) -> dict[str, Any]:
    """What the system claimed BEFORE executing."""
    return {
        "recovery_cost_usd": round(plan.cost, 2),
        "service_level": round(plan.service_level, 4),
        "temperature_risk": round(plan.temperature_risk, 4),
        "resilience_score": round(plan.resilience_score, 4),
        "recovery_time_hours": round(plan.recovery_time_hours, 2),
        "revenue_at_risk_usd": impact.get("revenue_at_risk"),
        "stockout_probability": impact.get("stockout_probability"),
        "service_level_risk": impact.get("service_level_risk"),
    }


def _delta(predicted: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, p_value in predicted.items():
        a_value = actual.get(key)
        if a_value is None or not isinstance(p_value, (int, float)):
            continue
        diff = round(float(a_value) - float(p_value), 6)
        denom = abs(float(p_value))
        pct = round(diff / denom * 100.0, 4) if denom > 1e-9 else None
        out[key] = {
            "predicted": p_value,
            "actual": a_value,
            "absolute_delta": diff,
            "percent_delta": pct,
            "direction": "better" if _better(key, diff) else ("worse" if diff else "exact"),
        }
    return out


# For cost/temperature/revenue, lower is better. For service/resilience, higher.
_LOWER_IS_BETTER = {
    "recovery_cost_usd",
    "temperature_risk",
    "revenue_at_risk_usd",
    "stockout_probability",
    "service_level_risk",
    "recovery_time_hours",
}


def _better(key: str, diff: float) -> bool:
    if diff == 0:
        return False
    return diff < 0 if key in _LOWER_IS_BETTER else diff > 0


def _calibration_signals(delta: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn deltas into explicit, human-reviewable calibration proposals."""
    signals: list[dict[str, Any]] = []
    for key, d in sorted(delta.items()):
        pct = d.get("percent_delta")
        if pct is None:
            continue
        magnitude = abs(pct)
        if magnitude < 5.0:
            continue
        if key in _LOWER_IS_BETTER:
            if d["absolute_delta"] < 0:
                signals.append(
                    {
                        "key": key,
                        "observation": f"{key} came in {magnitude:.1f}% better than predicted",
                        "proposal": (
                            f"Relax the planning assumption for {key} slightly, or leave "
                            "unchanged if the sample is a single event."
                        ),
                        "requires_human_approval": True,
                        "auto_applied": False,
                    }
                )
            else:
                signals.append(
                    {
                        "key": key,
                        "observation": f"{key} came in {magnitude:.1f}% worse than predicted",
                        "proposal": (
                            f"Review the assumption driving {key} before the next event. "
                            "Threshold review recommended."
                        ),
                        "requires_human_approval": True,
                        "auto_applied": False,
                    }
                )
        else:
            signals.append(
                {
                    "key": key,
                    "observation": f"{key} came in {magnitude:.1f}% "
                    f"{'better' if d['absolute_delta'] > 0 else 'worse'} than predicted",
                    "proposal": f"Review the {key} estimate used for recovery selection.",
                    "requires_human_approval": True,
                    "auto_applied": False,
                }
            )
    return signals


def reconcile(
    plan: RecoveryPlan,
    impact: dict[str, Any],
    event_ids: list[str] | None = None,
) -> LearningResult:
    """Reconcile predicted vs actual for one plan."""
    doc = load_observations()
    ledger_note = doc.get("meta", {})
    observed = (doc.get("observations") or {}).get(plan.plan_id)
    predicted = _predicted(plan, impact)

    if observed is None:
        # No feedback yet. Report PENDING rather than inventing an "actual".
        outcome = Outcome(
            plan_id=plan.plan_id,
            predicted=predicted,
            actual={},
            delta={},
            learning_note=(
                "No outcome feedback has been received for this plan yet. "
                "Reconciliation is pending; nothing is assumed."
            ),
            event_id=event_ids[0] if event_ids else None,
            calibration_signals=[],
            reconciliation_status="PENDING",
            retraining_performed=False,
        )
        return LearningResult(
            outcome=outcome,
            scorecard={"status": "PENDING", "predicted": predicted, "actual": None, "rows": []},
            calibration_signals=[],
            observation_source={
                "kind": "seeded observation file",
                "path": "data/events/outcome_observations.yaml",
                "labelled_as": "simulated",
                "status": "no observation for this plan",
                **ledger_note,
            },
        )

    actual = {k: v for k, v in observed.items() if not k.startswith("_")}
    delta = _delta(predicted, actual)
    signals = _calibration_signals(delta)

    rows = [
        {
            "metric": key,
            "predicted": d["predicted"],
            "actual": d["actual"],
            "absolute_delta": d["absolute_delta"],
            "percent_delta": d["percent_delta"],
            "direction": d["direction"],
        }
        for key, d in sorted(delta.items())
    ]
    improved = sum(1 for r in rows if r["direction"] == "better")
    worsened = sum(1 for r in rows if r["direction"] == "worse")

    note = (
        f"Outcome observed for {plan.plan_id}. {len(rows)} metrics compared: "
        f"{improved} better than predicted, {worsened} worse. "
        f"{len(signals)} calibration signal(s) generated for human review. "
        "No model was retrained: this prototype emits calibration signals only."
    )

    outcome = Outcome(
        plan_id=plan.plan_id,
        predicted=predicted,
        actual=actual,
        delta={k: v["absolute_delta"] for k, v in delta.items()},
        learning_note=note,
        event_id=event_ids[0] if event_ids else None,
        calibration_signals=signals,
        reconciliation_status="RECONCILED",
        retraining_performed=False,
    )

    scorecard = {
        "status": "RECONCILED",
        "plan_id": plan.plan_id,
        "strategy": plan.strategy,
        "rows": rows,
        "summary": {
            "metrics_compared": len(rows),
            "better_than_predicted": improved,
            "worse_than_predicted": worsened,
            "calibration_signals": len(signals),
            "retraining_performed": False,
        },
        "method": (
            "delta = actual - predicted; percent_delta = delta / |predicted| x 100. "
            "Direction conventions differ by metric (lower is better for cost/risk, "
            "higher is better for service/resilience)."
        ),
    }

    return LearningResult(
        outcome=outcome,
        scorecard=scorecard,
        calibration_signals=signals,
        observation_source={
            "kind": "seeded observation file",
            "path": "data/events/outcome_observations.yaml",
            "labelled_as": "simulated",
            "generated_at": isoformat(scenario_clock()),
            **ledger_note,
        },
    )
