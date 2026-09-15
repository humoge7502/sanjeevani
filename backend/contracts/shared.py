"""The six frozen SANJEEVANI contracts.

Contract version: v1.0.0 (frozen at architecture lock).

Each model mirrors the field names in the team handoff specification *exactly*.
Additional fields are permitted only where they are additive and optional, so a
consumer that reads only the documented keys keeps working.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CONTRACT_VERSION = "1.0.0"

# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class EventSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class EventType(str, Enum):
    """Event taxonomy. Kept open (`str`) on the model for future feeds."""

    COLD_CHAIN_EXCURSION = "COLD_CHAIN_EXCURSION"
    PORT_CLOSURE = "PORT_CLOSURE"
    LANE_DISRUPTION = "LANE_DISRUPTION"
    SUPPLIER_DISRUPTION = "SUPPLIER_DISRUPTION"
    TARIFF_CHANGE = "TARIFF_CHANGE"
    CAPACITY_SHORTAGE = "CAPACITY_SHORTAGE"


class EventSourceClass(str, Enum):
    """Source *class* matters: A2's cross-source verification rule counts these."""

    IOT = "IOT"
    NEWS = "NEWS"
    PORT_AUTHORITY = "PORT_AUTHORITY"
    TRADE_POLICY = "TRADE_POLICY"
    CARRIER = "CARRIER"
    MANUAL = "MANUAL"


class ExecutionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


SapSystem = Literal["ibp", "tm", "ariba"]


class ApprovalDecision(str, Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    REQUEST_REVIEW = "REQUEST_REVIEW"


# --------------------------------------------------------------------------
# Contract base
# --------------------------------------------------------------------------


class ContractModel(BaseModel):
    """Strict base: unknown keys are rejected so schema drift fails loudly."""

    model_config = ConfigDict(extra="forbid", frozen=False, validate_assignment=True)


# --------------------------------------------------------------------------
# 1. Event  (A1/A2 -> everyone)
# --------------------------------------------------------------------------


class Event(ContractModel):
    """A verified disruption event.

    ``confidence`` is a calibrated confidence in ``[0, 1]`` produced by A2's
    deterministic scoring function, not by an LLM.
    """

    event_id: str = Field(min_length=1)
    type: str = Field(min_length=1, description="Event taxonomy value, e.g. COLD_CHAIN_EXCURSION")
    severity: EventSeverity
    confidence: float = Field(ge=0.0, le=1.0)
    timestamp: str = Field(min_length=1, description="ISO-8601 event timestamp (UTC)")
    source: str = Field(min_length=1, description="Primary source id, e.g. 'IoT'")

    @field_validator("timestamp")
    @classmethod
    def _iso8601(cls, v: str) -> str:
        from datetime import datetime

        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError as exc:  # pragma: no cover - defensive
            raise ValueError(f"timestamp must be ISO-8601, got {v!r}") from exc
        return v


# --------------------------------------------------------------------------
# 2. Impact  (A3 -> M2)
# --------------------------------------------------------------------------


class Impact(ContractModel):
    """Deterministic network consequence of a scenario.

    Every number here is reproducible from seeded data. See ``trace`` for the
    per-value provenance that the UI exposes on hover.
    """

    event_id: str = Field(min_length=1)
    affected_nodes: list[str] = Field(default_factory=list)
    affected_shipments: list[str] = Field(default_factory=list)
    revenue_at_risk: float = Field(ge=0.0, description="USD")
    stockout_probability: float = Field(ge=0.0, le=1.0)
    service_level_risk: float = Field(ge=0.0, le=1.0)

    # --- additive, optional (traceability layer; safe for v1 consumers) ---
    affected_products: list[str] = Field(default_factory=list)
    scenario_id: str | None = None
    horizon_days: int = Field(default=10, ge=1, le=365)
    trace: dict[str, Any] = Field(default_factory=dict)
    contract_version: str = CONTRACT_VERSION


# --------------------------------------------------------------------------
# 3. RecoveryPlan  (M2 -> M3) -- produced by M2, consumed by M3
# --------------------------------------------------------------------------


class RecoveryPlan(ContractModel):
    """The M2 optimizer's recommended recovery action.

    M3 NEVER computes these numbers. If M2 is not implemented, the
    deterministic fixture in ``backend/m2`` supplies this object and the
    ``source`` field is set to ``fixture``.
    """

    plan_id: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    cost: float = Field(ge=0.0, description="USD, >= 0")
    service_level: float = Field(ge=0.0, le=1.0)
    resilience_score: float = Field(ge=0.0, le=1.0)
    temperature_risk: float = Field(ge=0.0, le=1.0)
    recovery_time_hours: float = Field(ge=0.0)
    # Values follow the frozen schema in docs/evidence/contracts/:
    # "PENDING/PASSED/FAILED". REVIEW is an additive extension this
    # implementation needs, because a plan can pass every rule while still
    # carrying a documented warning that a human must read.
    compliance_status: Literal["PENDING", "PASSED", "FAILED", "REVIEW"] = "PENDING"

    # --- additive, optional ---
    event_id: str | None = None
    impacted_skus: list[str] = Field(default_factory=list)
    impacted_lanes: list[str] = Field(default_factory=list)
    rationale: str | None = None
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    source: Literal["optimizer", "fixture"] = "fixture"
    contract_version: str = CONTRACT_VERSION


# --------------------------------------------------------------------------
# 4. Approval  (M3 gate)
# --------------------------------------------------------------------------


class Approval(ContractModel):
    """Human authority record. Only M3's approval state machine may create one."""

    plan_id: str = Field(min_length=1)
    approved: bool
    approved_by: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)

    # --- additive, optional ---
    decision: ApprovalDecision = ApprovalDecision.APPROVE
    role: str | None = None
    rationale: str | None = None
    contract_version: str = CONTRACT_VERSION


# --------------------------------------------------------------------------
# 5. ExecutionReceipt  (M3 -> SAP-shaped systems)
# --------------------------------------------------------------------------


class ExecutionReceipt(ContractModel):
    """One governed execution across the SAP-shaped surfaces."""

    plan_id: str = Field(min_length=1)
    ibp: ExecutionStatus
    tm: ExecutionStatus
    ariba: ExecutionStatus
    timestamp: str = Field(min_length=1)

    # --- additive, optional ---
    execution_id: str | None = None
    correlation_id: str | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    mock: bool = True
    contract_version: str = CONTRACT_VERSION

    @property
    def all_succeeded(self) -> bool:
        return all(
            s is ExecutionStatus.SUCCESS for s in (self.ibp, self.tm, self.ariba)
        )


# --------------------------------------------------------------------------
# 6. Outcome  (A6 -> learning)
# --------------------------------------------------------------------------


class Outcome(ContractModel):
    """Predicted vs actual reconciliation. Honest about what learning means."""

    plan_id: str = Field(min_length=1)
    predicted: dict[str, Any] = Field(default_factory=dict)
    actual: dict[str, Any] = Field(default_factory=dict)
    delta: dict[str, Any] = Field(default_factory=dict)
    learning_note: str = ""

    # --- additive, optional ---
    event_id: str | None = None
    calibration_signals: list[dict[str, Any]] = Field(default_factory=list)
    reconciliation_status: Literal["RECONCILED", "PARTIAL", "PENDING"] = "RECONCILED"
    retraining_performed: bool = Field(
        default=False,
        description="Always False in the MVP. No model is retrained; a calibration signal is emitted.",
    )
    contract_version: str = CONTRACT_VERSION
