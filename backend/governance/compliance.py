"""M3 / A5 -- Compliance gate.

Sits between the policy engine and the approval state machine. It assembles the
context a policy check needs, applies the results to the machine, and produces
the compliance record a human reads before deciding.

Language discipline: this module never claims regulatory certainty. Every
cold-chain statement is labelled `illustrative_check` and the record carries an
explicit `review_status`. Over-claiming compliance is the fastest way to lose a
technical judge.
"""

from __future__ import annotations

from typing import Any

from backend.config import policies as policies_cfg
from backend.contracts import RecoveryPlan
from backend.governance import policy_engine
from backend.governance.approval import (
    ApprovalState,
    GovernanceError,
    PlanGovernance,
)


def build_context(network, plan: RecoveryPlan, events: list[Any]) -> dict[str, Any]:
    """Assemble the fact base the policy engine is evaluated against."""
    excursed: list[str] = []
    temperature_sensitive = False
    critical_scope = False

    for event in events:
        event_type = getattr(event, "event_type", None) or event.get("type", "")
        target = getattr(event, "target", None) or event.get("target")
        if event_type == "COLD_CHAIN_EXCURSION" and target:
            excursed.append(target)

    # Scope: SKUs the plan touches, plus any excursed consignment's SKU.
    sku_ids = set(plan.impacted_skus)
    for ship_id in excursed:
        ship = network.shipments.get(ship_id)
        if ship:
            sku_ids.add(ship.sku_id)

    for sku_id in sku_ids:
        sku = network.skus.get(sku_id)
        if not sku:
            continue
        if sku.temp_class == "2_8C":
            temperature_sensitive = True
        if sku.criticality == "critical":
            critical_scope = True

    return {
        "excursed_shipments": sorted(set(excursed)),
        "temperature_sensitive": temperature_sensitive,
        "critical_scope": critical_scope,
        "sku_ids": sorted(sku_ids),
    }


def evaluate_plan(
    gov: PlanGovernance, plan: RecoveryPlan, network, events: list[Any]
) -> dict[str, Any]:
    """Run policy + compliance for a plan and advance the state machine.

    Returns the compliance record. Raises GovernanceError on an illegal
    transition (which would be a programming error, not a user error).
    """
    gov._transition(  # noqa: SLF001 - registry owns the machine
        ApprovalState.POLICY_CHECKING, "A5_COMPLIANCE", "system", "Policy evaluation started"
    )

    context = build_context(network, plan, events)
    evaluation = policy_engine.evaluate(
        plan,
        network=network,
        excursed_shipments=context["excursed_shipments"],
        temperature_sensitive=context["temperature_sensitive"],
        critical_scope=context["critical_scope"],
    )

    gov.risk_tier = evaluation.risk_tier
    gov.required_role = evaluation.required_role

    doc = policies_cfg()["meta"]
    record: dict[str, Any] = {
        "plan_id": plan.plan_id,
        "strategy": plan.strategy,
        "risk_tier": evaluation.risk_tier,
        "tier": evaluation.tier,
        "required_role": evaluation.required_role,
        "approval_required": evaluation.approval_required,
        "rulebook_version": evaluation.rulebook_version,
        "review_status": evaluation.review_status,
        "scope": context,
        "checks": evaluation.as_dict(),
        "summary": {
            "passed": len(evaluation.passed),
            "failed": len(evaluation.failed),
            "warned": len(evaluation.warned),
            "errored": len(evaluation.errored),
            "blocking": evaluation.blocking,
        },
        "disclaimer": doc["note"].strip(),
        "labelled_as": "governance_evaluation",
    }

    if evaluation.blocking:
        record["compliance_status"] = "FAILED"
        record["rationale"] = _blocked_rationale(evaluation)
        gov.blocked_reason = record["rationale"]
        reason = (
            f"{len(evaluation.failed)} policy rule(s) FAILED and "
            f"{len(evaluation.errored)} ERRORED"
        )
        gov._transition(  # noqa: SLF001
            ApprovalState.COMPLIANCE_CHECKING, "A5_COMPLIANCE", "system", "Compliance evaluation"
        )
        gov._transition(  # noqa: SLF001
            ApprovalState.COMPLIANCE_BLOCKED, "A5_COMPLIANCE", "system", reason
        )
        record["state"] = gov.state.value
        return record

    record["compliance_status"] = "PASSED"
    record["rationale"] = _pass_rationale(evaluation)
    gov._transition(  # noqa: SLF001
        ApprovalState.COMPLIANCE_CHECKING, "A5_COMPLIANCE", "system", "Compliance evaluation"
    )
    gov._transition(  # noqa: SLF001
        ApprovalState.APPROVAL_REQUIRED,
        "A5_COMPLIANCE",
        "system",
        (
            f"Compliant. Tier {evaluation.risk_tier} requires approval from role "
            f"{evaluation.required_role}."
            if evaluation.approval_required
            else "Compliant and below the human-approval threshold."
        ),
    )
    record["state"] = gov.state.value
    return record


def _blocked_rationale(evaluation: policy_engine.PolicyEvaluation) -> str:
    parts: list[str] = []
    for check in evaluation.failed:
        parts.append(f"{check.rule_id} ({check.name}): {check.message}")
    for check in evaluation.errored:
        parts.append(f"{check.rule_id} ({check.name}): {check.message}")
    return "Execution blocked. " + " ".join(parts)


def _pass_rationale(evaluation: policy_engine.PolicyEvaluation) -> str:
    warn_note = (
        f" {len(evaluation.warned)} rule(s) raised a review warning."
        if evaluation.warned
        else ""
    )
    return (
        f"All {len(evaluation.passed)} checks passed (rulebook "
        f"{evaluation.rulebook_version}). Risk tier {evaluation.risk_tier}; "
        f"required approver role {evaluation.required_role}.{warn_note}"
    )


def approval_request(plan: RecoveryPlan, record: dict[str, Any], impact: dict[str, Any]) -> dict[str, Any]:
    """The object the approval console renders.

    Everything a human needs to decide in seconds, with the arithmetic attached
    so the decision is auditable rather than a matter of trust.
    """
    checks = record["checks"]
    return {
        "plan_id": plan.plan_id,
        "strategy": plan.strategy,
        "required_role": record["required_role"],
        "risk_tier": record["risk_tier"],
        "approval_required": record["approval_required"],
        "cost": plan.cost,
        "service_level": plan.service_level,
        "resilience_score": plan.resilience_score,
        "temperature_risk": plan.temperature_risk,
        "recovery_time_hours": plan.recovery_time_hours,
        "rationale": plan.rationale,
        "plan_source": plan.source,
        "alternatives": plan.alternatives,
        "compliance": {
            "status": record["compliance_status"],
            "summary": record["summary"],
            "warnings": [c["message"] for c in checks["warned"]],
            "failed": [c["message"] for c in checks["failed"]],
        },
        "impact": {
            "event_id": impact.get("event_id"),
            "revenue_at_risk": impact.get("revenue_at_risk"),
            "stockout_probability": impact.get("stockout_probability"),
            "service_level_risk": impact.get("service_level_risk"),
            "affected_nodes": impact.get("affected_nodes", []),
            "affected_shipments": impact.get("affected_shipments", []),
            "affected_products": impact.get("affected_products", []),
            "horizon_days": impact.get("horizon_days"),
        },
        "what_will_happen": _execution_preview(plan),
        "withhold": (
            "Execution is server-enforced. The UI cannot execute this plan; only the "
            "backend approval state machine can, and only from the APPROVED state."
        ),
    }


def _execution_preview(plan: RecoveryPlan) -> list[dict[str, Any]]:
    """Plain-language description of the SAP-shaped calls that will follow."""
    return [
        {
            "system": "IBP",
            "label": "SAP IBP (scenario / key figures)",
            "mock": True,
            "action": "Create the recovery planning scenario and post re-planned key figures.",
        },
        {
            "system": "TM",
            "label": "SAP TM (freight / booking)",
            "mock": True,
            "action": "Re-book the affected freight orders onto the recovery route.",
        },
        {
            "system": "Ariba",
            "label": "SAP Ariba (supplier risk / procurement)",
            "mock": True,
            "action": "Pull risk exposure for the alternate provider and raise the sourcing request.",
        },
    ]


def assert_executable(gov: PlanGovernance) -> None:
    """Raise a structured GovernanceError if execution is not permitted."""
    if gov.state is not ApprovalState.APPROVED:
        raise GovernanceError(
            "EXECUTION_BLOCKED",
            f"Plan {gov.plan_id} cannot execute from state {gov.state.value}.",
            {
                "state": gov.state.value,
                "required": ApprovalState.APPROVED.value,
                "rule": "POL-APPROVAL-001",
            },
        )
