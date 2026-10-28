"""Chaos drill — the failure paths, composed and asserted in one run.

WHAT THIS IS. Roadmap AF-10. Every failure path in this system is individually
unit-tested, but a demo leans on their *composition*: when the solver is gone,
the ledger must still be intact and the errors legible; when the queueing-model
veto switch flips, the recommended plan must not move; when a foreign tenant
knocks, the registry refuses before any state moves. This drill runs those
scenarios in sequence against the real orchestrator and reports, per scenario,
whether the system degraded the way the docs claim.

WHAT THIS IS NOT. Not fuzzing and not randomised: every scenario is a fixed,
deterministic injection, because a chaos drill whose outcome depends on a dice
roll cannot gate a release.

    python scripts/chaos.py          # run the drill, print the report
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend.config import reload_config
from backend.governance.approval import ApprovalRegistry, GovernanceError


def _solver_unavailable() -> tuple[str, bool, str]:
    """The M2 provider is switched off: the loop must stop, legibly, safely."""
    import os

    from backend.orchestrator import orchestrator

    os.environ["SANJEEVANI_PLAN_PROVIDER"] = "unavailable"
    try:
        reload_config()
        orchestrator.reset()
        state = orchestrator.run()
    finally:
        os.environ.pop("SANJEEVANI_PLAN_PROVIDER", None)
        reload_config()
        orchestrator.reset()
    errors = state["errors"]
    codes = {e["error"] for e in errors}
    held = (
        "RECOVERY_PLAN_UNAVAILABLE" in codes
        and state["plan"] is None
        and state["ledger"]["chain"]["intact"]
        and state["governance"] is None
    )
    return (
        "solver-unavailable",
        held,
        (
            f"errors={sorted(codes)} plan_present={state['plan'] is not None} "
            f"ledger_intact={state['ledger']['chain']['intact']}"
        ),
    )


def _ledger_tamper_is_caught() -> tuple[str, bool, str]:
    """An out-of-band edit to a stored record must break the chain verdict."""
    from backend.audit.ledger import ledger
    from backend.orchestrator import orchestrator

    orchestrator.reset()
    ledger.append("SIGNAL_RECEIVED", "CHAOS", "seed")
    ledger.append("EVENT_VERIFIED", "CHAOS", "seed")
    before = ledger.verify_chain()["intact"]
    ledger._records[0].detail["injected"] = "chaos drill"  # noqa: SLF001
    after = ledger.verify_chain()
    orchestrator.reset()
    held = before is True and after["intact"] is False and bool(after["issues"])
    return (
        "ledger-tamper-caught",
        held,
        (
            f"intact_before={before} intact_after={after['intact']} "
            f"issues={len(after['issues'])}"
        ),
    )


def _des_veto_switch_changes_only_the_constraints() -> tuple[str, bool, str]:
    """Flipping m2.des.enabled must add constraint power without moving the plan."""
    import os

    from backend.m2.optimizer import optimize_for_event

    os.environ["SANJEEVANI_M2_DES"] = "true"
    try:
        reload_config()
        strict = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    finally:
        os.environ.pop("SANJEEVANI_M2_DES", None)
        reload_config()
    advisory = optimize_for_event(event_id="EVT-001", scenario_id="SCN-EVT-001-10D")
    same_plan = strict.recommended.model_dump() == advisory.recommended.model_dump()
    held = (
        strict.trace["des"]["enabled_as_constraint"] is True
        and advisory.trace["des"]["enabled_as_constraint"] is False
        and same_plan
    )
    return (
        "des-veto-switch",
        held,
        (
            f"same_plan={same_plan} "
            f"strict_des={strict.trace['des']['enabled_as_constraint']} "
            f"advisory_des={advisory.trace['des']['enabled_as_constraint']}"
        ),
    )


def _foreign_tenant_is_refused() -> tuple[str, bool, str]:
    """A second tenant cannot obtain a parallel journey for the same plan."""
    registry = ApprovalRegistry()
    registry.get_or_create("PLAN-CHAOS", tenant="acme-pharma")
    try:
        registry.get_or_create("PLAN-CHAOS", tenant="helix-biologics")
    except GovernanceError as exc:
        held = exc.code == "TENANT_MISMATCH"
        detail = f"refused code={exc.code}"
    else:  # pragma: no cover - only reachable if the guard is removed
        held, detail = False, "cross-tenant access was ALLOWED"
    return ("foreign-tenant-refused", held, detail)


def run_drill() -> dict[str, Any]:
    """Every scenario, in order. A scenario raising is a failed scenario."""
    scenarios = [
        _solver_unavailable,
        _ledger_tamper_is_caught,
        _des_veto_switch_changes_only_the_constraints,
        _foreign_tenant_is_refused,
    ]
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        try:
            name, held, detail = scenario()
        except Exception as exc:  # noqa: BLE001 - a crash IS a failed scenario
            rows.append(
                {"scenario": scenario.__name__, "held": False, "detail": f"crashed: {exc!r}"}
            )
            continue
        rows.append({"scenario": name, "held": held, "detail": detail})
    return {
        "rows": rows,
        "scenarios": len(rows),
        "all_held": all(row["held"] for row in rows),
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        "SANJEEVANI chaos drill",
        "",
        f"{report['scenarios']} scenarios · "
        + ("ALL DEGRADATIONS HELD" if report["all_held"] else "DEGRADATION BROKEN"),
        "",
    ]
    for row in report["rows"]:
        marker = "HELD " if row["held"] else "BROKEN"
        lines.append(f"  [{marker}] {row['scenario']}: {row['detail']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args()
    report = run_drill()
    print(render(report))
    return 0 if report["all_held"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
