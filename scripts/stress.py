"""Scenario stress harness — the whole M2 decision engine against a matrix.

WHAT THIS IS. Roadmap AF-5: run the optimizer + digital twin + DES cross-check
across a sweep of scenario ids (every event label x every horizon) and report,
per cell, the plan that would be recommended, its admissibility, its DES
verdict, and how many uniform policies remain executable. Then state the two
properties that make the sweep worth running:

* **determinism** — every cell is solved twice and both answers must be
  byte-identical (the audit chain's promise, exercised at matrix scale);
* **admissibility** — every recommended plan must break no hard ceiling.

A cell that fails either is a finding, not a crash: the report names it and the
exit code is non-zero.

WHAT THIS IS NOT. A performance benchmark and not new arithmetic — it reuses the
exact solve path the API uses, so the report is evidence about the system, not
about a harness.

    python scripts/stress.py                          # default matrix, stdout
    python scripts/stress.py --out runtime/stress.md  # also write a report
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend.m2.optimizer import optimize_for_event

DEFAULT_EVENT_IDS = ("EVT-001", "EVT-002")
DEFAULT_HORIZONS = (3, 10, 30)


def run_cell(event_id: str, scenario_id: str) -> dict[str, Any]:
    """One matrix cell, solved twice. Determinism is the cell's first result."""
    first = optimize_for_event(event_id=event_id, scenario_id=scenario_id)
    second = optimize_for_event(event_id=event_id, scenario_id=scenario_id)
    deterministic = first.recommended.model_dump() == second.recommended.model_dump()

    plan = first.recommended
    violations = list(first.trace["constraints"]["violations"])
    cross = first.trace["des"]["cross_check"]
    counterfactuals = first.trace["counterfactuals"]
    return {
        "event_id": event_id,
        "scenario_id": scenario_id,
        "plan_id": plan.plan_id,
        "strategy": plan.strategy,
        "cost_usd": round(plan.cost, 2),
        "service_level": round(plan.service_level, 4),
        "resilience_score": round(plan.resilience_score, 4),
        "recovery_time_hours": round(plan.recovery_time_hours, 2),
        "temperature_risk": round(plan.temperature_risk, 4),
        "violations": violations,
        "admissible": not violations,
        "deterministic": deterministic,
        "des_verdict": cross["verdict"],
        "des_recovery_hours": cross["des_recovery_hours"],
        "twin_recovery_hours": cross["twin_recovery_hours"],
        "executable_uniform_policies": sum(1 for r in counterfactuals if r["executable"]),
        "uniform_policies_evaluated": len(counterfactuals),
    }


def run_matrix(
    event_ids: list[str] | tuple[str, ...] = DEFAULT_EVENT_IDS,
    horizons: list[int] | tuple[int, ...] = DEFAULT_HORIZONS,
) -> dict[str, Any]:
    """The sweep. Raises nothing per cell; findings travel in the report."""
    rows: list[dict[str, Any]] = []
    for event_id in event_ids:
        for horizon in horizons:
            scenario_id = f"SCN-{event_id}-{horizon}D"
            rows.append(run_cell(event_id, scenario_id))
    problems = [
        f"{r['scenario_id']}: " + ("non-deterministic " if not r["deterministic"] else "")
        + (f"violations: {r['violations']}" if r["violations"] else "")
        for r in rows
        if not r["deterministic"] or r["violations"]
    ]
    return {
        "rows": rows,
        "cells": len(rows),
        "all_deterministic": all(r["deterministic"] for r in rows),
        "all_admissible": all(r["admissible"] for r in rows),
        "problems": problems,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# M2 scenario stress report",
        "",
        (
            f"{report['cells']} cells · "
            f"determinism {'HELD' if report['all_deterministic'] else 'BROKEN'} · "
            f"admissibility {'HELD' if report['all_admissible'] else 'BROKEN'}"
        ),
        "",
    ]
    if report["problems"]:
        lines += ["## Findings", ""] + [f"- {p}" for p in report["problems"]] + [""]
    lines += [
        "| Scenario | Plan | Strategy | Cost | Service | Recovery (twin) | Recovery (DES) | DES verdict | Executable policies |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in report["rows"]:
        lines.append(
            f"| {r['scenario_id']} | {r['plan_id']} | {r['strategy']} | ${r['cost_usd']:,.0f} "
            f"| {r['service_level']:.1%} | {r['twin_recovery_hours']:.1f}h "
            f"| {r['des_recovery_hours']:.1f}h | {r['des_verdict']} "
            f"| {r['executable_uniform_policies']}/{r['uniform_policies_evaluated']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--events", default=",".join(DEFAULT_EVENT_IDS), help="comma-separated event ids")
    parser.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_HORIZONS), help="comma-separated horizon days")
    parser.add_argument("--out", default="", help="also write the markdown report here")
    args = parser.parse_args()

    event_ids = [e.strip() for e in args.events.split(",") if e.strip()]
    horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]
    report = run_matrix(event_ids, horizons)

    print(render_markdown(report))
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_markdown(report), encoding="utf-8")
        print(f"report written to {out_path}")

    if report["problems"]:
        print(f"FAILED: {len(report['problems'])} finding(s)", file=sys.stderr)
        return 1
    print(f"OK: {report['cells']} cells, determinism and admissibility hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
