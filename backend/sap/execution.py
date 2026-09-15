"""M3 -- Execution orchestrator.

Sequences the SAP-shaped posts for an approved plan and produces one
`ExecutionReceipt`.

    approve -> [IBP, TM, Ariba] -> receipt

Why the sequencing is what it is (and why it is not a single atomic call):
  * IBP first. The planning scenario is the key-figure record of the decision;
    if the planner of record is not updated, downstream freight changes are
    orphaned.
  * TM second. Freight is re-booked against the scenario that now exists.
  * Ariba last. Sourcing risk is pulled for the alternate provider, and a
    procurement failure is a *reviewable* failure rather than a physical one.

Partial failure: if a step fails, we stop, record `FAILED` for that system and
`SKIPPED` for the rest, and emit the receipt anyway. A half-executed plan with an
honest receipt and defined compensating actions is far safer than a receipt that
claims success. The plan is never silently retried.

Idempotency: the key is derived from (plan_id, attempt scope), so a replayed
request returns the ORIGINAL receipt rather than posting twice.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from backend.config import isoformat, scenario_clock
from backend.contracts import ExecutionReceipt, ExecutionStatus, RecoveryPlan
from backend.governance.approval import ApprovalState, GovernanceError, PlanGovernance, can_execute
from backend.sap.mocks import SapMockError, SapSurface, deterministic_id, surface as default_surface


@dataclass
class ExecutionOutcome:
    receipt: ExecutionReceipt
    actions: list[dict[str, Any]]
    compensating_actions: list[dict[str, Any]]
    failure: dict[str, Any] | None = None
    replayed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "receipt": self.receipt.model_dump(mode="json"),
            "actions": self.actions,
            "compensating_actions": self.compensating_actions,
            "failure": self.failure,
            "replayed": self.replayed,
        }


class ExecutionOrchestrator:
    def __init__(self, sap: SapSurface | None = None) -> None:
        self.sap = sap or default_surface
        self._receipts: dict[str, ExecutionOutcome] = {}
        self._lock = threading.RLock()

    def reset(self) -> None:
        with self._lock:
            self._receipts.clear()
        self.sap.reset()

    def receipt_for(self, plan_id: str) -> ExecutionOutcome | None:
        with self._lock:
            return self._receipts.get(plan_id)

    # ---------------------------------------------------------------- execution
    def execute(self, plan: RecoveryPlan, gov: PlanGovernance, requested_mode: str = "air") -> ExecutionOutcome:
        """Execute an approved plan. Refuses on any non-approved state."""
        allowed, reason = can_execute(gov)
        if not allowed:
            # Fail closed with a structured, user-legible error. Never a 500.
            raise GovernanceError(
                "EXECUTION_BLOCKED",
                f"Execution refused: plan {plan.plan_id} is {reason} (state {gov.state.value}). "
                "Governance requires an APPROVED state.",
                {
                    "state": gov.state.value,
                    "reason": reason,
                    "rule": "POL-APPROVAL-001",
                    "required_state": ApprovalState.APPROVED.value,
                },
            )

        # A receipt already exists for this plan: replay it. This is the
        # page-refresh guard at the orchestrator level.
        with self._lock:
            existing = self._receipts.get(plan.plan_id)
            if existing is not None:
                replayed = ExecutionOutcome(
                    receipt=existing.receipt,
                    actions=existing.actions,
                    compensating_actions=existing.compensating_actions,
                    failure=existing.failure,
                    replayed=True,
                )
                return replayed

        gov._transition(  # noqa: SLF001 - the orchestrator drives the machine
            ApprovalState.EXECUTING, "A5_EXECUTION", "system", "Execution started"
        )

        correlation_id = deterministic_id("CORR", plan.plan_id, gov.approval["timestamp"] if gov.approval else "")
        idem = f"{plan.plan_id}:{gov.approval['approved_by'] if gov.approval else 'unknown'}"

        actions: list[dict[str, Any]] = []
        failure: dict[str, Any] | None = None
        statuses: dict[str, ExecutionStatus] = {
            "ibp": ExecutionStatus.SKIPPED,
            "tm": ExecutionStatus.SKIPPED,
            "ariba": ExecutionStatus.SKIPPED,
        }

        steps = [
            ("ibp", self._step_ibp),
            ("tm", self._step_tm),
            ("ariba", self._step_ariba),
        ]
        for system, step in steps:
            if failure is not None:
                actions.append(
                    {
                        "system": system,
                        "status": ExecutionStatus.SKIPPED.value,
                        "reason": f"not attempted after {failure['system']} failure",
                    }
                )
                continue
            try:
                action = step(plan, correlation_id, idem, requested_mode)
                statuses[system] = ExecutionStatus.SUCCESS
                actions.append(action)
            except SapMockError as exc:
                statuses[system] = ExecutionStatus.FAILED
                failure = exc.as_dict()
                actions.append(
                    {
                        "system": system,
                        "status": ExecutionStatus.FAILED.value,
                        "error": exc.as_dict(),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - any unexpected failure fails this step
                statuses[system] = ExecutionStatus.FAILED
                failure = {
                    "system": system,
                    "error": "UNEXPECTED_ERROR",
                    "message": str(exc),
                }
                actions.append(
                    {"system": system, "status": ExecutionStatus.FAILED.value, "error": failure}
                )

        receipt = ExecutionReceipt(
            plan_id=plan.plan_id,
            ibp=statuses["ibp"],
            tm=statuses["tm"],
            ariba=statuses["ariba"],
            timestamp=isoformat(scenario_clock()),
            execution_id=deterministic_id("EXEC", plan.plan_id, correlation_id),
            correlation_id=correlation_id,
            actions=actions,
            mock=True,
        )

        outcome = ExecutionOutcome(
            receipt=receipt,
            actions=actions,
            compensating_actions=self._compensating_actions(plan, actions),
            failure=failure,
        )

        with self._lock:
            self._receipts[plan.plan_id] = outcome

        gov._transition(  # noqa: SLF001
            ApprovalState.EXECUTED if failure is None else ApprovalState.FAILED,
            "A5_EXECUTION",
            "system",
            "All SAP-shaped actions succeeded"
            if failure is None
            else f"{failure['system']} failed; remaining steps skipped",
        )
        return outcome

    # -------------------------------------------------------------- step bodies
    def _step_ibp(self, plan: RecoveryPlan, cid: str, idem: str, mode: str) -> dict[str, Any]:
        record = self.sap.ibp.create_scenario(
            {
                "plan_id": plan.plan_id,
                "strategy": plan.strategy,
                "cost": plan.cost,
                "service_level": plan.service_level,
                "resilience_score": plan.resilience_score,
                "recovery_time_hours": plan.recovery_time_hours,
            },
            cid,
            f"{idem}:ibp",
        )
        return {
            "system": "ibp",
            "label": "SAP IBP",
            "status": record.status,
            "operation": record.operation,
            "mock": True,
            "summary": (
                f"Planning scenario {record.response['d']['PlanningScenarioID']} created and "
                f"key figures posted."
            ),
            "response": record.response,
        }

    def _step_tm(self, plan: RecoveryPlan, cid: str, idem: str, mode: str) -> dict[str, Any]:
        temperature_controlled = plan.temperature_risk > 0.0 or bool(plan.impacted_skus)
        record = self.sap.tm.rebook_freight_order(
            {
                "plan_id": plan.plan_id,
                "mode": mode,
                "temperature_controlled": temperature_controlled,
                "setpoint_c": 5.0,
            },
            cid,
            f"{idem}:tm",
        )
        return {
            "system": "tm",
            "label": "SAP TM",
            "status": record.status,
            "operation": record.operation,
            "mock": True,
            "summary": (
                f"Freight order {record.response['d']['FreightOrderID']} re-booked to "
                f"{record.response['d']['TransportationMode']}."
            ),
            "response": record.response,
        }

    def _step_ariba(self, plan: RecoveryPlan, cid: str, idem: str, mode: str) -> dict[str, Any]:
        suppliers = self._suppliers_for(plan)
        record = self.sap.ariba.request_supplier_risk_scores(
            {"plan_id": plan.plan_id, "supplier_ids": suppliers},
            cid,
            f"{idem}:ariba",
        )
        bands = {r["SupplierID"]: r["Band"] for r in record.response["d"]["Results"]}
        return {
            "system": "ariba",
            "label": "SAP Ariba",
            "status": record.status,
            "operation": record.operation,
            "mock": True,
            "summary": (
                f"Risk exposure pulled for {len(suppliers)} supplier(s): "
                + ", ".join(f"{k} {v}" for k, v in bands.items())
            ),
            "response": record.response,
        }

    def _suppliers_for(self, plan: RecoveryPlan) -> list[str]:
        # Emergency re-sourcing is exactly the case where supplier risk matters,
        # so the mock is asked for the wider candidate set.
        if plan.strategy == "EMERGENCY_SOURCE":
            return ["SUP-EU-A", "SUP-CHN-A", "SUP-CHN-B", "SUP-IND-A"]
        return ["SUP-EU-A", "SUP-IND-A"]

    def _compensating_actions(
        self, plan: RecoveryPlan, actions: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Defined up-front, per the dossier's rollback requirement."""
        out: list[dict[str, Any]] = []
        for action in actions:
            if action.get("status") != ExecutionStatus.SUCCESS.value:
                continue
            if action["system"] == "tm":
                out.append(
                    {
                        "system": "tm",
                        "action": "rebook_freight_order_back",
                        "description": "Re-book the freight order to its original routing.",
                        "available": True,
                    }
                )
            elif action["system"] == "ibp":
                out.append(
                    {
                        "system": "ibp",
                        "action": "deactivate_scenario",
                        "description": "Deactivate the recovery planning scenario.",
                        "available": True,
                    }
                )
            elif action["system"] == "ariba":
                out.append(
                    {
                        "system": "ariba",
                        "action": "withdraw_sourcing_request",
                        "description": "Withdraw the sourcing request raised for the alternate provider.",
                        "available": True,
                    }
                )
        if not out:
            out.append(
                {
                    "system": "none",
                    "action": "advisory_mode",
                    "description": (
                        "No SAP-shaped action was posted, so no compensation is required. "
                        "The loop degrades to advisory mode."
                    ),
                    "available": True,
                }
            )
        return out


orchestrator = ExecutionOrchestrator()
