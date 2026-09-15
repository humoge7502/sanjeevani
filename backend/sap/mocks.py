"""M3 -- SAP-shaped mocks: IBP, TM, Ariba.

⚠️  MOCK BOUNDARY. These are NOT SAP integrations. No tenant is contacted, no
OData service is deployed, and no credential exists. They are deterministic
in-process services whose request/response shapes are modelled on SAP's public
API families so the business workflow around them is realistic.

Response envelopes mirror SAP's OData v4 JSON conventions
(`{"d": {...}}` with a `@odata.context`) so that swapping in a real connector is
a transport change, not a business-logic change. See docs/mock-boundaries.md.

Three properties every mock here must have, because the demo depends on them:

  DETERMINISM  Same request -> identical response body, including ids.
  IDEMPOTENCY  Same idempotency key -> the ORIGINAL response is replayed and no
               second action is recorded. This is what stops a page refresh
               double-posting a freight re-booking.
  OBSERVABILITY A call log of every request and response for the audit ledger.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from typing import Any

from backend.config import isoformat, scenario_clock


class SapMockError(Exception):
    """A mock surface refused a request. Deterministic and inspectable."""

    def __init__(self, system: str, code: str, message: str, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.system = system
        self.code = code
        self.message = message
        self.detail = detail or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "error": self.code,
            "message": self.message,
            "detail": self.detail,
        }


def deterministic_id(prefix: str, *parts: str, width: int = 10) -> str:
    """A stable, content-derived id.

    Uses SHA-256 over the canonical request parts, so the same logical action
    always produces the same document number. This is *not* used for security.
    """
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{int(digest[:width], 16) % (10 ** width):0{width}d}"


@dataclass
class CallRecord:
    system: str
    operation: str
    idempotency_key: str
    correlation_id: str
    request: dict[str, Any]
    response: dict[str, Any]
    status: str  # SUCCESS | FAILED | REPLAYED
    timestamp: str
    duration_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "operation": self.operation,
            "idempotency_key": self.idempotency_key,
            "correlation_id": self.correlation_id,
            "request": self.request,
            "response": self.response,
            "status": self.status,
            "timestamp": self.timestamp,
        }


class BaseMock:
    """Shared idempotency + call-log behaviour for the three surfaces."""

    SYSTEM = "SAP"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_key: dict[str, CallRecord] = {}
        self.calls: list[CallRecord] = []

    def reset(self) -> None:
        with self._lock:
            self._by_key.clear()
            self.calls.clear()

    def _execute(
        self,
        operation: str,
        payload: dict[str, Any],
        correlation_id: str,
        idempotency_key: str,
        builder,
    ) -> CallRecord:
        with self._lock:
            existing = self._by_key.get(idempotency_key)
            if existing is not None:
                replay = CallRecord(
                    system=self.SYSTEM,
                    operation=existing.operation,
                    idempotency_key=idempotency_key,
                    correlation_id=existing.correlation_id,
                    request=existing.request,
                    response=existing.response,
                    status="REPLAYED",
                    timestamp=isoformat(scenario_clock()),
                )
                self.calls.append(replay)
                return replay

            response = builder(payload, correlation_id)
            record = CallRecord(
                system=self.SYSTEM,
                operation=operation,
                idempotency_key=idempotency_key,
                correlation_id=correlation_id,
                request=payload,
                response=response,
                status="SUCCESS",
                timestamp=isoformat(scenario_clock()),
            )
            self._by_key[idempotency_key] = record
            self.calls.append(record)
            return record

    def log(self) -> list[dict[str, Any]]:
        with self._lock:
            return [c.as_dict() for c in self.calls]

    def describe(self) -> dict[str, Any]:
        return {
            "system": self.SYSTEM,
            "mock": True,
            "real_integration": False,
            "calls_made": len(self.calls),
        }


def _odata(context: str, payload: dict[str, Any]) -> dict[str, Any]:
    """OData-v4-shaped envelope, as SAP services return."""
    return {"@odata.context": context, "d": payload}


class IbpMock(BaseMock):
    """SAP IBP -- planning scenario + key figures. SHAPE FAITHFUL, NOT REAL."""

    SYSTEM = "IBP"
    CONTEXT = "$metadata#PlanningScenario"

    def create_scenario(self, payload: dict[str, Any], correlation_id: str, idempotency_key: str) -> CallRecord:
        required = {"plan_id", "strategy", "cost"}
        missing = required - set(payload)
        if missing:
            raise SapMockError(
                self.SYSTEM, "MISSING_FIELDS", f"Missing required key figure fields: {sorted(missing)}"
            )
        if float(payload["cost"]) < 0:
            raise SapMockError(self.SYSTEM, "INVALID_KEY_FIGURE", "cost must be >= 0")

        def build(p: dict[str, Any], cid: str) -> dict[str, Any]:
            scenario_id = deterministic_id("SCN", p["plan_id"], cid)
            return _odata(
                self.CONTEXT,
                {
                    "PlanningScenarioID": scenario_id,
                    "SourcePlanID": p["plan_id"],
                    "Strategy": p["strategy"],
                    "KeyFigures": {
                        "PlannedRecoveryCost": round(float(p["cost"]), 2),
                        "TargetServiceLevel": round(float(p.get("service_level", 0.0)), 4),
                        "ResilienceScore": round(float(p.get("resilience_score", 0.0)), 4),
                        "RecoveryTimeHours": round(float(p.get("recovery_time_hours", 0.0)), 2),
                    },
                    "Status": "SCENARIO_ACTIVE",
                    "CorrelationID": cid,
                    "CreatedAt": isoformat(scenario_clock()),
                },
            )

        return self._execute("createPlanScenario", payload, correlation_id, idempotency_key, build)


class TmMock(BaseMock):
    """SAP TM -- freight order / booking. SHAPE FAITHFUL, NOT REAL."""

    SYSTEM = "TM"
    CONTEXT = "$metadata#FreightOrder"

    def rebook_freight_order(
        self, payload: dict[str, Any], correlation_id: str, idempotency_key: str
    ) -> CallRecord:
        if not payload.get("plan_id"):
            raise SapMockError(self.SYSTEM, "MISSING_FIELDS", "plan_id is required")
        mode = str(payload.get("mode", "AIR")).upper()
        if mode not in {"AIR", "OCEAN", "OCEAN_REEFER", "ROAD", "RAIL"}:
            raise SapMockError(self.SYSTEM, "INVALID_MODE", f"Unsupported transport mode {mode!r}")

        def build(p: dict[str, Any], cid: str) -> dict[str, Any]:
            freight_order = deterministic_id("FO", p["plan_id"], mode, cid)
            return _odata(
                self.CONTEXT,
                {
                    "FreightOrderID": freight_order,
                    "SourcePlanID": p["plan_id"],
                    "PreviousFreightOrderID": p.get("previous_freight_order_id")
                    or deterministic_id("FO", p["plan_id"], "ORIGINAL"),
                    "TransportationMode": mode,
                    "TemperatureControlled": bool(p.get("temperature_controlled", True)),
                    "SetpointC": p.get("setpoint_c", 5.0),
                    "BookingStatus": "CONFIRMED",
                    "CorrelationID": cid,
                    "ChangedAt": isoformat(scenario_clock()),
                },
            )

        return self._execute("rebookFreightOrder", payload, correlation_id, idempotency_key, build)


class AribaMock(BaseMock):
    """SAP Ariba -- supplier risk exposure + sourcing request. SHAPE FAITHFUL, NOT REAL."""

    SYSTEM = "ARIBA"
    CONTEXT = "$metadata#SupplierRiskExposure"

    MAX_SUPPLIERS_PER_CALL = 500  # documented API limit; enforced so we stay shape-faithful

    def request_supplier_risk_scores(
        self, payload: dict[str, Any], correlation_id: str, idempotency_key: str
    ) -> CallRecord:
        suppliers = payload.get("supplier_ids") or []
        if not suppliers:
            raise SapMockError(self.SYSTEM, "MISSING_FIELDS", "supplier_ids is required")
        if len(suppliers) > self.MAX_SUPPLIERS_PER_CALL:
            raise SapMockError(
                self.SYSTEM,
                "BATCH_TOO_LARGE",
                f"At most {self.MAX_SUPPLIERS_PER_CALL} suppliers per call (received {len(suppliers)}).",
            )

        def build(p: dict[str, Any], cid: str) -> dict[str, Any]:
            # CONSISTENCY RULE: if the supplier exists in the seeded network, the
            # mock returns the network's risk score. A mock that contradicted the
            # graph would be indefensible in Q&A ("your risk service says HIGH,
            # your network says 0.18"). Only unknown supplier ids fall back to a
            # stable derived score, and the response says which happened.
            entries = []
            for sid in p["supplier_ids"]:
                score, origin = self._risk_for(sid)
                entries.append(
                    {
                        "SupplierID": sid,
                        "RiskExposureScore": score,
                        "Band": "HIGH" if score > 0.66 else ("MEDIUM" if score > 0.33 else "LOW"),
                        "ScoreOrigin": origin,
                        "AssessmentDate": isoformat(scenario_clock()),
                    }
                )
            return _odata(
                self.CONTEXT,
                {
                    "RequestID": deterministic_id("RISK", cid, *p["supplier_ids"]),
                    "SourcePlanID": p.get("plan_id"),
                    "SupplierCount": len(entries),
                    "Results": entries,
                    "CorrelationID": cid,
                },
            )

        return self._execute(
            "requestSupplierRiskScores", payload, correlation_id, idempotency_key, build
        )

    @staticmethod
    def _risk_for(supplier_id: str) -> tuple[float, str]:
        """Prefer the seeded network's risk score so the mock cannot contradict it."""
        try:
            from backend.graph.network import Network

            node = Network.load().nodes.get(supplier_id)
            if node is not None:
                return round(float(node.risk_score), 4), "network_model"
        except Exception:  # noqa: BLE001 - the mock must never hard-fail on a lookup
            pass
        digest = hashlib.sha256(supplier_id.encode("utf-8")).hexdigest()
        return round(int(digest[:4], 16) / 0xFFFF, 4), "derived_fallback"


@dataclass
class SapSurface:
    """The three mocks, wired as one execution surface."""

    ibp: IbpMock = field(default_factory=IbpMock)
    tm: TmMock = field(default_factory=TmMock)
    ariba: AribaMock = field(default_factory=AribaMock)

    def reset(self) -> None:
        self.ibp.reset()
        self.tm.reset()
        self.ariba.reset()

    def describe(self) -> dict[str, Any]:
        return {
            "boundary": "SAP-shaped deterministic mocks",
            "real_integration": False,
            "note": (
                "No SAP tenant is contacted. Shapes follow SAP's public OData API "
                "families; every response carries its own mock disclosure."
            ),
            "surfaces": [self.ibp.describe(), self.tm.describe(), self.ariba.describe()],
        }

    def call_log(self) -> list[dict[str, Any]]:
        records = self.ibp.log() + self.tm.log() + self.ariba.log()
        return sorted(records, key=lambda r: (r["timestamp"], r["system"]))


surface = SapSurface()
