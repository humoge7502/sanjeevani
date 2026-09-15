"""M3 / A5 -- Policy engine.

Validates a RecoveryPlan against the rulebook *before* any human is asked to
approve, and before any SAP-shaped call is made. The rulebook is configuration
(config/policies.yaml), not code, so an operator can inspect and change it.

Design decisions worth defending:
  * Each check returns a structured result with the threshold used, the actual
    value, and the arithmetic. Nothing is a boolean with no evidence.
  * Every rule carries a `provenance` label (configured_policy / simulated_rule /
    illustrative_check / production_requirement) so the UI cannot over-claim.
  * FAIL blocks. WARN requires acknowledgement. Unexpected engine errors FAIL
    closed -- a broken policy engine must never be a permission to execute.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from backend.config import policies as policies_cfg
from backend.config import settings
from backend.contracts import RecoveryPlan


@dataclass
class CheckResult:
    rule_id: str
    name: str
    dimension: str
    provenance: str
    severity: str  # FAIL | WARN
    outcome: str  # PASS | FAIL | WARN | ERROR
    threshold: Any
    actual: Any
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "dimension": self.dimension,
            "provenance": self.provenance,
            "provenance_label": PROVENANCE_LABELS.get(self.provenance, self.provenance),
            "severity": self.severity,
            "outcome": self.outcome,
            "threshold": self.threshold,
            "actual": self.actual,
            "message": self.message,
            "evidence": self.evidence,
        }


PROVENANCE_LABELS = {
    "configured_policy": "Configured policy (editable in config/policies.yaml)",
    "simulated_rule": "Simulated rule (stand-in for a real data/party feed)",
    "illustrative_check": "Illustrative check (NOT a regulatory certification)",
    "production_requirement": "Production requirement (not certified by this MVP)",
}


@dataclass
class PolicyEvaluation:
    plan_id: str
    tier: str
    risk_tier: str
    required_role: str | None
    approval_required: bool
    passed: list[CheckResult]
    failed: list[CheckResult]
    warned: list[CheckResult]
    errored: list[CheckResult]
    rulebook_version: str
    review_status: str

    @property
    def blocking(self) -> bool:
        return bool(self.failed or self.errored)

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "tier": self.tier,
            "risk_tier": self.risk_tier,
            "required_role": self.required_role,
            "approval_required": self.approval_required,
            "blocking": self.blocking,
            "rulebook_version": self.rulebook_version,
            "review_status": self.review_status,
            "passed": [c.as_dict() for c in self.passed],
            "failed": [c.as_dict() for c in self.failed],
            "warned": [c.as_dict() for c in self.warned],
            "errored": [c.as_dict() for c in self.errored],
        }


def _tier_for_cost(cost: float) -> dict[str, Any]:
    tiers = settings()["governance"]["tiers"]
    for tier in tiers:
        cap = tier["max_cost"]
        if cap is None or cost <= float(cap):
            return tier
    return tiers[-1]


def classify_tier(plan: RecoveryPlan, temperature_sensitive: bool) -> dict[str, Any]:
    """Cost-banded risk tier, escalated when the plan is compliance-sensitive.

    The dossier's escalation idea (cost bands + compliance sensitivity) is kept;
    the band boundaries are ours and are documented in config/settings.yaml.
    """
    gov = settings()["governance"]
    tier = _tier_for_cost(plan.cost)
    risk_tier = tier["risk_tier"]
    required_role = tier["required_role"]
    escalated = False

    order = ["L1", "L2", "L3", "L4"]
    if temperature_sensitive:
        floor = gov["compliance_sensitive_min_tier"]
        if order.index(risk_tier) < order.index(floor):
            risk_tier = floor
            for t in gov["tiers"]:
                if t["risk_tier"] == floor:
                    required_role = t["required_role"]
                    tier = t
                    break
            escalated = True
    elif order.index(risk_tier) < order.index(gov["human_required_from_tier"]):
        pass

    approval_required = order.index(risk_tier) >= order.index(
        gov["human_required_from_tier"]
    )
    return {
        "tier": tier["id"],
        "risk_tier": risk_tier,
        "required_role": required_role,
        "approval_required": approval_required,
        "cost": plan.cost,
        "escalated_for_compliance_sensitivity": escalated,
    }


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------


def _rule_param_bounds(plan: RecoveryPlan, p: dict, ctx: dict) -> CheckResult:
    errs: list[str] = []
    if plan.cost < float(p["cost_min"]):
        errs.append(f"cost {plan.cost} < {p['cost_min']}")
    for field_name, key in (
        ("service_level", "service_level_range"),
        ("resilience_score", "resilience_score_range"),
        ("temperature_risk", "temperature_risk_range"),
    ):
        lo, hi = p[key]
        value = getattr(plan, field_name)
        if not (float(lo) <= value <= float(hi)):
            errs.append(f"{field_name} {value} outside [{lo}, {hi}]")
    if plan.recovery_time_hours > float(p["recovery_time_hours_max"]):
        errs.append(
            f"recovery_time_hours {plan.recovery_time_hours} > {p['recovery_time_hours_max']}"
        )
    return _mk(
        ctx,
        outcome="FAIL" if errs else "PASS",
        actual={k: getattr(plan, k) for k in
                ("cost", "service_level", "resilience_score", "temperature_risk",
                 "recovery_time_hours")},
        threshold=p,
        message=("; ".join(errs) if errs else
                 "All plan parameters are inside configured physical bounds."),
    )


def _rule_evidence(plan: RecoveryPlan, p: dict, ctx: dict) -> CheckResult:
    n_skus, n_lanes = len(plan.impacted_skus), len(plan.impacted_lanes)
    ok = n_skus >= int(p["min_impacted_skus"]) and n_lanes >= int(p["min_impacted_lanes"])
    return _mk(
        ctx,
        outcome="PASS" if ok else "FAIL",
        actual={"impacted_skus": plan.impacted_skus, "impacted_lanes": plan.impacted_lanes},
        threshold=p,
        message=(
            f"Plan references {n_skus} SKU(s) and {n_lanes} lane(s)."
            if ok
            else "Plan does not name the SKUs/lanes it affects, so it cannot be audited."
        ),
    )


def _rule_budget(plan: RecoveryPlan, p: dict, ctx: dict) -> CheckResult:
    risk_tier = ctx["risk_tier"]
    cap = p["caps"].get(risk_tier)
    if cap is None:
        return _mk(
            ctx,
            outcome="PASS",
            actual={"cost": plan.cost, "risk_tier": risk_tier},
            threshold={"cap": None},
            message=f"Tier {risk_tier} has no cost cap; execution still requires human authority.",
        )
    ok = plan.cost <= float(cap)
    return _mk(
        ctx,
        outcome="PASS" if ok else "FAIL",
        actual={"cost": plan.cost, "risk_tier": risk_tier},
        threshold={"cap": cap},
        message=(
            f"Cost {plan.cost:,.2f} is inside the {risk_tier} cap {float(cap):,.2f}."
            if ok
            else f"Cost {plan.cost:,.2f} exceeds the {risk_tier} cap {float(cap):,.2f}."
        ),
    )


def _rule_temperature(plan: RecoveryPlan, p: dict, ctx: dict) -> CheckResult:
    applies = ctx["temperature_sensitive"]
    if not applies:
        return _mk(
            ctx,
            outcome="PASS",
            actual={"temperature_risk": plan.temperature_risk},
            threshold=p,
            message="No 2-8C consignment on the affected scope; cold-chain ceiling not engaged.",
            skip=True,
        )
    ceiling = float(p["max_temperature_risk"])
    ok = plan.temperature_risk <= ceiling
    return _mk(
        ctx,
        outcome="PASS" if ok else "FAIL",
        actual={"temperature_risk": plan.temperature_risk},
        threshold={"max_temperature_risk": ceiling, "temp_classes": p["applies_to_temp_classes"]},
        message=(
            f"Post-recovery temperature risk {plan.temperature_risk:.4f} is inside the "
            f"ceiling {ceiling}."
            if ok
            else f"ILLUSTRATIVE GDP-style check failed: temperature risk "
            f"{plan.temperature_risk:.4f} exceeds ceiling {ceiling}."
        ),
    )


def _rule_excursion_corrective(plan: RecoveryPlan, p: dict, ctx: dict) -> CheckResult:
    excursed = [s for s in p["excursed_shipments"] if s in ctx["excursed_shipments"]]
    if not excursed:
        return _mk(
            ctx,
            outcome="PASS",
            actual={"excursed_shipments": []},
            threshold=p,
            message="No consignment in the scope breached its excursion limit.",
            skip=True,
        )
    ok = plan.strategy in p["acceptable_strategies"]
    return _mk(
        ctx,
        outcome="PASS" if ok else "FAIL",
        actual={"excursed_shipments": excursed, "strategy": plan.strategy},
        threshold={"acceptable_strategies": p["acceptable_strategies"]},
        message=(
            f"Strategy {plan.strategy} is an accepted corrective action for "
            f"{', '.join(excursed)}."
            if ok
            else f"Strategy {plan.strategy} leaves excursed consignment(s) "
            f"{', '.join(excursed)} without a corrective step."
        ),
    )


def _rule_service_level(plan: RecoveryPlan, p: dict, ctx: dict) -> CheckResult:
    if not ctx["critical_scope"]:
        return _mk(
            ctx,
            outcome="PASS",
            actual={"service_level": plan.service_level},
            threshold=p,
            message="No critical SKU in scope; service floor not engaged.",
            skip=True,
        )
    floor = float(p["min_service_level"])
    ok = plan.service_level >= floor
    return _mk(
        ctx,
        outcome="PASS" if ok else "FAIL",
        actual={"service_level": plan.service_level},
        threshold={"min_service_level": floor},
        message=(
            f"Service level {plan.service_level:.4f} meets the critical-SKU floor {floor}."
            if ok
            else f"Service level {plan.service_level:.4f} is below the critical-SKU floor {floor}."
        ),
    )


def _rule_sanctions(plan: RecoveryPlan, p: dict, ctx: dict) -> CheckResult:
    blocked_nodes = set(p.get("blocked_nodes") or [])
    blocked_countries = set(p.get("blocked_countries") or [])
    touched_nodes = set()
    for lane_id in plan.impacted_lanes:
        lane = ctx["network"].lanes.get(lane_id)
        if lane:
            touched_nodes.update(lane.path)
    hit_nodes = sorted(touched_nodes & blocked_nodes)
    hit_countries = sorted(
        {
            ctx["network"].nodes[n].country
            for n in touched_nodes
            if n in ctx["network"].nodes
            and ctx["network"].nodes[n].country in blocked_countries
        }
    )
    ok = not hit_nodes and not hit_countries
    return _mk(
        ctx,
        outcome="PASS" if ok else "FAIL",
        actual={"nodes": sorted(touched_nodes), "hits": hit_nodes, "countries": hit_countries},
        threshold={"blocked_nodes": sorted(blocked_nodes), "blocked_countries": sorted(blocked_countries)},
        message=(
            "SIMULATED sanctions screen: no configured match on the plan's routed nodes."
            if ok
            else f"SIMULATED sanctions screen matched {hit_nodes or hit_countries}."
        ),
        evidence_note=(
            "Simulated: the MVP ships a small configured list, not a maintained "
            "sanctions or denied-party feed. A production deployment must replace it."
        ),
    )


def _rule_trade_corridor(plan: RecoveryPlan, p: dict, ctx: dict) -> CheckResult:
    restricted = set(p.get("restricted_corridors") or [])
    red_sea = bool(p.get("warn_on_red_sea"))
    touched: list[str] = []
    for lane_id in plan.impacted_lanes:
        lane = ctx["network"].lanes.get(lane_id)
        if not lane:
            continue
        if lane.id in restricted:
            touched.append(lane.id)
        elif red_sea and lane.red_sea_exposed:
            touched.append(lane.id)
    ok = not touched
    return _mk(
        ctx,
        outcome="PASS" if ok else "WARN",
        actual={"corridors": sorted(touched)},
        threshold={"restricted_corridors": sorted(restricted), "warn_on_red_sea": red_sea},
        message=(
            "No restricted corridor on the plan's route."
            if ok
            else f"SIMULATED trade screen: plan touches restricted corridor(s) "
            f"{sorted(touched)}; documented review required."
        ),
        evidence_note="Simulated: corridor restrictions are configured, not sourced from a live feed.",
    )


def _rule_supplier_risk(plan: RecoveryPlan, p: dict, ctx: dict) -> CheckResult:
    applies = plan.strategy in (p.get("applies_to_strategies") or [])
    if not applies:
        return _mk(
            ctx,
            outcome="PASS",
            actual={"strategy": plan.strategy},
            threshold=p,
            message="Rule only applies to emergency re-sourcing strategies.",
            skip=True,
        )
    ceiling = float(p["max_supplier_risk"])
    worst = max(
        (ctx["network"].nodes[n].risk_score
         for lane_id in plan.impacted_lanes
         if lane_id in ctx["network"].lanes
         for n in ctx["network"].lanes[lane_id].path
         if n in ctx["network"].nodes),
        default=0.0,
    )
    ok = worst <= ceiling
    return _mk(
        ctx,
        outcome="PASS" if ok else "WARN",
        actual={"max_supplier_risk_on_route": worst},
        threshold={"max_supplier_risk": ceiling},
        message=(
            f"Highest supplier risk on the re-source route is {worst:.2f}, inside {ceiling}."
            if ok
            else f"Re-source route reaches supplier risk {worst:.2f} above ceiling {ceiling}; "
            "executable but flagged for review."
        ),
    )


def _rule_approval_invariant(plan: RecoveryPlan, p: dict, ctx: dict) -> CheckResult:
    """The invariant rule. It restates what the API layer already enforces."""
    order = ["L1", "L2", "L3", "L4"]
    consequential = order.index(ctx["risk_tier"]) >= order.index(p["consequential_from_tier"])
    needs_role = bool(p["require_approval_role"])
    ok = (not consequential) or (needs_role and ctx["required_role"] is not None)
    return _mk(
        ctx,
        outcome="PASS" if ok else "FAIL",
        actual={
            "risk_tier": ctx["risk_tier"],
            "consequential": consequential,
            "required_role": ctx["required_role"],
        },
        threshold=p,
        message=(
            f"Tier {ctx['risk_tier']} consequential={consequential}; execution requires a "
            f"recorded approval from role {ctx['required_role']}."
            if ok
            else "A consequential plan has no approver role configured; refusing to proceed."
        ),
    )


def _mk(
    ctx: dict,
    *,
    outcome: str,
    actual: Any,
    threshold: Any,
    message: str,
    skip: bool = False,
    evidence_note: str | None = None,
) -> CheckResult:
    rule = ctx["rule"]
    evidence = {
        "config_key": f"config/policies.yaml:rules[{rule['id']}]",
        "plan_id": ctx["plan"].plan_id,
        "strategy": ctx["plan"].strategy,
        "skipped": skip,
    }
    if evidence_note:
        evidence["note"] = evidence_note
    return CheckResult(
        rule_id=rule["id"],
        name=rule["name"],
        dimension=rule["dimension"],
        provenance=rule["provenance"],
        severity=rule["severity"],
        outcome=outcome,
        threshold=threshold,
        actual=actual,
        message=message,
        evidence=evidence,
    )


EVALUATORS: dict[str, Callable[[RecoveryPlan, dict, dict], CheckResult]] = {
    "POL-PARAM-001": _rule_param_bounds,
    "POL-EVIDENCE-001": _rule_evidence,
    "POL-BUDGET-001": _rule_budget,
    "POL-TEMP-001": _rule_temperature,
    "POL-TEMP-002": _rule_excursion_corrective,
    "POL-SERVICE-001": _rule_service_level,
    "POL-SANCT-001": _rule_sanctions,
    "POL-TRADE-001": _rule_trade_corridor,
    "POL-SUPPLIER-001": _rule_supplier_risk,
    "POL-APPROVAL-001": _rule_approval_invariant,
}


def evaluate(
    plan: RecoveryPlan,
    *,
    network,
    excursed_shipments: list[str],
    temperature_sensitive: bool,
    critical_scope: bool,
) -> PolicyEvaluation:
    """Run the whole rulebook against a plan."""
    doc = policies_cfg()
    tier_info = classify_tier(plan, temperature_sensitive and critical_scope)

    ctx_base = {
        "plan": plan,
        "network": network,
        "risk_tier": tier_info["risk_tier"],
        "required_role": tier_info["required_role"],
        "excursed_shipments": excursed_shipments,
        "temperature_sensitive": temperature_sensitive,
        "critical_scope": critical_scope,
    }

    passed: list[CheckResult] = []
    failed: list[CheckResult] = []
    warned: list[CheckResult] = []
    errored: list[CheckResult] = []

    for rule in doc["rules"]:
        evaluator = EVALUATORS.get(rule["id"])
        if evaluator is None:
            # An executable rule with no evaluator must FAIL CLOSED, never pass.
            errored.append(
                CheckResult(
                    rule_id=rule["id"],
                    name=rule.get("name", rule["id"]),
                    dimension=rule.get("dimension", "unknown"),
                    provenance=rule.get("provenance", "configured_policy"),
                    severity=rule.get("severity", "FAIL"),
                    outcome="ERROR",
                    threshold=None,
                    actual=None,
                    message=(
                        f"No evaluator is registered for rule {rule['id']}. "
                        "Failing closed: an unimplemented rule cannot be treated as satisfied."
                    ),
                    evidence={"config_key": f"config/policies.yaml:rules[{rule['id']}]"},
                )
            )
            continue

        ctx = {**ctx_base, "rule": rule}
        try:
            result = evaluator(plan, rule.get("params", {}), ctx)
        except Exception as exc:  # noqa: BLE001 - fail closed on any engine error
            result = CheckResult(
                rule_id=rule["id"],
                name=rule.get("name", rule["id"]),
                dimension=rule.get("dimension", "unknown"),
                provenance=rule.get("provenance", "configured_policy"),
                severity=rule.get("severity", "FAIL"),
                outcome="ERROR",
                threshold=rule.get("params"),
                actual={"exception": type(exc).__name__},
                message=f"Policy engine error while evaluating {rule['id']}: {exc}. Failing closed.",
                evidence={"config_key": f"config/policies.yaml:rules[{rule['id']}]"},
            )

        if result.outcome == "PASS":
            passed.append(result)
        elif result.outcome == "FAIL":
            failed.append(result)
        elif result.outcome == "WARN":
            warned.append(result)
        else:
            errored.append(result)

    return PolicyEvaluation(
        plan_id=plan.plan_id,
        tier=tier_info["tier"],
        risk_tier=tier_info["risk_tier"],
        required_role=tier_info["required_role"],
        approval_required=tier_info["approval_required"],
        passed=passed,
        failed=failed,
        warned=warned,
        errored=errored,
        rulebook_version=doc["meta"]["rulebook_version"],
        review_status=doc["meta"]["review_status"],
    )


def rule_catalogue() -> list[dict[str, Any]]:
    """The rulebook as displayed in the compliance panel."""
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "dimension": r["dimension"],
            "provenance": r["provenance"],
            "provenance_label": PROVENANCE_LABELS.get(r["provenance"], r["provenance"]),
            "severity": r["severity"],
            "description": " ".join(r["description"].split()),
            "implemented": r["id"] in EVALUATORS,
        }
        for r in policies_cfg()["rules"]
    ]
