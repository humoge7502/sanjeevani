#!/usr/bin/env python3
"""SANJEEVANI presenter demo — one command, no network, no server required.

    python scripts/demo.py              # full run: reset -> run -> approve -> execute
    python scripts/demo.py --to-gate    # stop at the human approval gate
    python scripts/demo.py --json       # machine-readable output for tooling

Runs the loop IN-PROCESS against the same modules the API uses, so the demo
cannot fail because a server is not up, a port is taken or the venue Wi-Fi died.
That is the offline-first guarantee, exercised rather than stated.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.contracts import ApprovalDecision  # noqa: E402
from backend.governance.approval import GovernanceError  # noqa: E402
from backend.orchestrator import orchestrator  # noqa: E402

RULE = "─" * 78


def heading(text: str) -> None:
    print(f"\n{RULE}\n{text}\n{RULE}")


def kpi(label: str, value: str) -> None:
    print(f"  {label:<32} {value}")


def money(value: float) -> str:
    return f"${value:,.2f}"


def percentage(value: float) -> str:
    return f"{value * 100:.2f}%"


def run_demo(actor: str, rationale: str, to_gate: bool, as_json: bool) -> int:
    transcript: dict[str, object] = {}

    # ------------------------------------------------------------- reset
    orchestrator.reset()
    if not as_json:
        heading("SANJEEVANI — governed closed-loop recovery")
        print("  Reset: ledger, approval authority and SAP-shaped mocks cleared.")

    # --------------------------------------------------------------- run
    state = orchestrator.run()
    m1 = state["m1"]

    if not as_json:
        heading("M1 · SENSE → VERIFY → SCENARIO → IMPACT")
        print(f"  Signals sensed:      {len(m1['watchlist']) + len(m1['rejected']) + len(m1['events'])}")
        print(f"  Verified events:     {len(m1['events'])}")
        for event in m1["events"]:
            print(
                f"    · {event['event_id']}  {event['type']:<22} {event['severity']:<7} "
                f"confidence {event['confidence']:.4f}  target {event['target']}"
            )
            print(f"      rule {event['verification']['rule']}: {event['verification']['reason']}")
        if m1["rejected"]:
            print(f"  Rejected (not verified): {len(m1['rejected'])}")
            for rejected in m1["rejected"]:
                print(
                    f"    · {rejected['event_type']} on {rejected['target']}: "
                    f"{rejected['verification'].get('reason')}"
                )
        if m1["deduplicated"]:
            print(f"  Deduplicated restatements: {len(m1['deduplicated'])}")
        print(f"  On watchlist (below the gate): {len(m1['watchlist'])}")

        print("\n  Scenario horizons:")
        for scenario in m1["scenarios"]:
            print(
                f"    · {scenario['horizon_days']:>2}-day  weight {percentage(scenario['probability_weight']):<8} "
                f"duration {scenario['duration_days']:g}d  delay {scenario['transit_delay_days']:g}d"
            )

        print("\n  Network impact per horizon:")
        for impact in m1["impacts"]:
            print(
                f"    · {impact['horizon_days']:>2}-day  revenue at risk {money(impact['revenue_at_risk']):>16}  "
                f"stockout {percentage(impact['stockout_probability']):>7}  "
                f"service risk {percentage(impact['service_level_risk']):>7}"
            )
        primary = m1["primary_impact"]
        print("\n  Primary (10-day) detail:")
        print(f"    affected nodes:       {', '.join(primary['affected_nodes'])}")
        print(f"    affected consignments: {', '.join(primary['affected_shipments'])}")
        print(f"    affected products:    {', '.join(primary['affected_products'])}")
        hero = next(
            (c for c in primary["trace"]["consignments"] if c["shipment_id"] == "SHIP-001"), None
        )
        if hero:
            print(
                f"    hero consignment {hero['shipment_id']} ({hero['sku_id']}): "
                f"P(stockout) {percentage(hero['p_stockout'])}, "
                f"condemned {percentage(hero['condemn_ratio'])}, "
                f"value at risk {money(hero['value_at_risk_usd'])}"
            )

    transcript["m1"] = {
        "events": [
            {
                "event_id": e["event_id"],
                "type": e["type"],
                "confidence": e["confidence"],
                "target": e["target"],
            }
            for e in m1["events"]
        ],
        "rejected": len(m1["rejected"]),
        "watchlist": len(m1["watchlist"]),
        "impacts": [
            {
                "horizon_days": i["horizon_days"],
                "revenue_at_risk": i["revenue_at_risk"],
                "stockout_probability": i["stockout_probability"],
                "service_level_risk": i["service_level_risk"],
            }
            for i in m1["impacts"]
        ],
    }

    # ------------------------------------------------------------ M2 / M3
    plan = state["plan"]
    if plan is None:
        print("\n  LOOP STOPPED: no RecoveryPlan was available. Nothing was invented.")
        return 2

    if not as_json:
        heading("M2 BOUNDARY · RECOVERY PLAN  (deterministic fixture — no optimizer ran)")
        kpi("Plan", f"{plan['plan_id']} · {plan['strategy']}")
        kpi("Cost", money(plan["cost"]))
        kpi("Service level", percentage(plan["service_level"]))
        kpi("Resilience score", f"{plan['resilience_score']:.3f}")
        kpi("Temperature risk", f"{plan['temperature_risk']:.3f}")
        kpi("Recovery time", f"{plan['recovery_time_hours']:.1f} h")
        print(f"  Provider: {state['plan_provider']['reality']}")

        heading("M3 · POLICY → COMPLIANCE")
        compliance = state["compliance"]
        kpi("Compliance status", compliance["compliance_status"])
        kpi("Risk tier", f"{compliance['risk_tier']} ({compliance['tier']})")
        kpi("Required approver role", str(compliance["required_role"]))
        summary = compliance["summary"]
        kpi(
            "Checks",
            f"{summary['passed']} passed · {summary['failed']} failed · "
            f"{summary['warned']} warned · {summary['errored']} errored",
        )
        print(f"  {compliance['rationale']}")
        print(f"  Review status: {compliance['review_status']}")

    transcript["plan"] = {
        "plan_id": plan["plan_id"],
        "strategy": plan["strategy"],
        "cost": plan["cost"],
        "source": plan["source"],
    }
    transcript["compliance"] = state["compliance"]["summary"] | {
        "status": state["compliance"]["compliance_status"],
        "risk_tier": state["compliance"]["risk_tier"],
    }

    if state["compliance"]["compliance_status"] != "PASSED":
        if not as_json:
            print("\n  COMPLIANCE BLOCKED EXECUTION. The loop stops before any human is asked.")
        return 3

    if to_gate:
        if not as_json:
            heading("STOPPED AT THE HUMAN APPROVAL GATE")
            print("  Nothing executes without a recorded approval from the required role.")
            print("  Next: open the UI, or re-run without --to-gate.")
        print(json.dumps(transcript, indent=2) if as_json else "")
        return 0

    # ------------------------------------------------------ adversarial check
    if not as_json:
        print("\n  Governance check — attempting execution BEFORE approval:")
    try:
        orchestrator.execute()
        print("    UNEXPECTED: execution was permitted without approval.")
        return 4
    except GovernanceError as exc:
        if not as_json:
            print(f"    blocked ✓  {exc.code}: {exc.message}")

    # ------------------------------------------------------------- approve
    if not as_json:
        heading("HUMAN-IN-THE-LOOP · APPROVAL")
    state = orchestrator.decide(ApprovalDecision.APPROVE, actor, rationale)
    approval = state["approval"]
    if not as_json:
        kpi("Approved by", f"{approval['approved_by']} ({approval['role']})")
        kpi("Plan", approval["plan_id"])
        kpi("State", state["governance"]["state"])
        print(f"  Rationale recorded: {rationale}")

    transcript["approval"] = {
        "approved_by": approval["approved_by"],
        "role": approval["role"],
        "state": state["governance"]["state"],
    }

    # ------------------------------------------------------------- execute
    state = orchestrator.execute()
    receipt = state["execution"]["receipt"]

    if not as_json:
        heading("GOVERNED EXECUTION · SAP-SHAPED MOCKS")
        for action in state["execution"]["actions"]:
            print(
                f"  {action.get('label', action['system']):<12} {action['status']:<9} "
                f"{action.get('summary', '')}"
            )
        print()
        kpi("IBP", receipt["ibp"])
        kpi("TM", receipt["tm"])
        kpi("Ariba", receipt["ariba"])
        kpi("Correlation ID", str(receipt["correlation_id"]))
        kpi("Execution ID", str(receipt["execution_id"]))
        kpi("Mock boundary", "yes — no SAP tenant was contacted")

    transcript["execution"] = {
        "ibp": receipt["ibp"],
        "tm": receipt["tm"],
        "ariba": receipt["ariba"],
        "correlation_id": receipt["correlation_id"],
    }

    # -------------------------------------------------------------- learning
    learning = state["learning"]
    if not as_json:
        heading("AUDIT → LEARN")
        ledger = state["ledger"]
        kpi("Ledger records", str(ledger["length"]))
        kpi(
            "Loop stages covered",
            f"{len(ledger['coverage']['stages_present'])}/{len(ledger['coverage']['stages_expected'])}"
            + (" (complete)" if ledger["coverage"]["complete"] else " (INCOMPLETE)"),
        )
        kpi("Hash chain intact", str(ledger["chain"]["intact"]))
        kpi("Chain head", str(ledger["chain"]["head"])[:24])
        print()
        scorecard = learning["scorecard"]
        kpi("Metrics compared", str(scorecard["summary"]["metrics_compared"]))
        kpi("Better than predicted", str(scorecard["summary"]["better_than_predicted"]))
        kpi("Worse than predicted", str(scorecard["summary"]["worse_than_predicted"]))
        kpi("Calibration signals", str(scorecard["summary"]["calibration_signals"]))
        kpi("Model retrained", "no — calibration signals only")
        print(f"\n  {learning['outcome']['learning_note']}")
        heading("LOOP CLOSED")
        print("  Sense → Verify → Understand → Scenario → Simulate → Optimize →")
        print("  Approve → Execute → Audit → Learn")
        print(f"\n  Final state: {state['governance']['state']}")

    transcript["learning"] = learning["scorecard"]["summary"]
    transcript["ledger"] = {
        "records": state["ledger"]["length"],
        "chain_intact": state["ledger"]["chain"]["intact"],
        "stages_complete": state["ledger"]["coverage"]["complete"],
        "head": state["ledger"]["chain"]["head"],
    }
    transcript["final_state"] = state["governance"]["state"]

    if as_json:
        print(json.dumps(transcript, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SANJEEVANI presenter demo")
    parser.add_argument("--actor", default="meera.iyer", help="approver id (default: meera.iyer)")
    parser.add_argument(
        "--rationale",
        default="Protect the biologic supply; cost is inside the tier cap.",
        help="approval rationale recorded in the ledger",
    )
    parser.add_argument("--to-gate", action="store_true", help="stop at the human approval gate")
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    args = parser.parse_args()
    return run_demo(args.actor, args.rationale, args.to_gate, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
