"""A2 -- Event Verification & Scenario Agent (Member 1).

Two responsibilities, one agent body (per the MVP compression in the dossier):

  1. Verification: debounce false positives. Cluster candidates by (type,
     target), apply the dedup window, drop stale corroboration, then decide
     whether the cluster is *verified*.

  2. Scenario generation: turn a verified event into parameterized 3/10/30-day
     futures with probability weights. Parameters are CLAMPED against
     config/scenarios.yaml bounds -- A2 can shrink a claim, never inflate it.

Verification rules (both are implemented, both are testable):

  RULE-2SOURCE  A claim about the world (news / authority / carrier / policy)
                needs agreement across >= 2 distinct source CLASSES, and at
                least one corroborating signal must be fresh.

  RULE-MEASURE  A direct measurement (IoT) is self-corroborating, but ONLY when
                the device trace independently reproduces the claimed physical
                quantity. The claim says "peak 11.4C"; we re-derive the peak
                from the raw samples and require agreement. A device-only claim
                with no trace does NOT verify.

Nothing here is an LLM output, and A2 cannot trigger execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.agents import telemetry
from backend.agents.sensing import CandidateEvent
from backend.config import (
    Thresholds,
    isoformat,
    scenario_clock,
    scenarios_config,
    settings,
)
from backend.graph.network import Network

SEVERITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

# Agreement tolerance when re-deriving a claimed temperature from a trace.
TRACE_PEAK_TOLERANCE_C = 0.5


@dataclass
class VerifiedEvent:
    event_id: str
    event_type: str
    target: str
    severity: str
    confidence: float
    timestamp: str
    source: str
    verification: dict[str, Any]
    claim: dict[str, Any]
    contributing: list[str] = field(default_factory=list)

    def as_event_dict(self) -> dict[str, Any]:
        """The frozen `Event` contract payload."""
        return {
            "event_id": self.event_id,
            "type": self.event_type,
            "severity": self.severity,
            "confidence": round(self.confidence, 4),
            "timestamp": self.timestamp,
            "source": self.source,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.as_event_dict(),
            "target": self.target,
            "verification": self.verification,
            "contributing_signals": self.contributing,
        }


@dataclass
class Scenario:
    scenario_id: str
    event_id: str
    name: str
    horizon_days: int
    duration_days: float
    transit_delay_days: float
    capacity_factor: float
    probability_weight: float
    confidence: float
    affected_lanes: list[str]
    narrative: str
    parameters_source: dict[str, Any]
    contributed_by_events: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "event_id": self.event_id,
            "name": self.name,
            "horizon_days": self.horizon_days,
            "duration_days": self.duration_days,
            "transit_delay_days": self.transit_delay_days,
            "capacity_factor": self.capacity_factor,
            "probability_weight": round(self.probability_weight, 4),
            "confidence": round(self.confidence, 4),
            "affected_lanes": self.affected_lanes,
            "narrative": self.narrative,
            "parameters_source": self.parameters_source,
            "contributed_by_events": self.contributed_by_events,
        }


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _cluster_key(c: CandidateEvent) -> tuple[str, str]:
    return (c.event_type, c.target)


def _dedup(candidates: list[CandidateEvent], window_hours: float) -> tuple[list[CandidateEvent], list[dict[str, Any]]]:
    """Collapse *restatements of the same report* inside the dedup window.

    Critical subtlety: dedup is scoped to a single source CLASS. Two outlets in
    the news class saying the same thing is one report restated; a port authority
    and a carrier saying the same thing is independent corroboration. Collapsing
    across classes would destroy the very signal RULE-2SOURCE depends on -- so we
    keep the most reliable candidate per class and record the rest as evidence.
    """
    ordered = sorted(candidates, key=lambda c: (-c.source_reliability, c.candidate_id))
    kept: list[CandidateEvent] = []
    dropped: list[dict[str, Any]] = []
    for cand in ordered:
        published = datetime.fromisoformat(cand.published_utc.replace("Z", "+00:00"))
        duplicate_of = None
        for k in kept:
            if k.source_class != cand.source_class:
                continue
            kpub = datetime.fromisoformat(k.published_utc.replace("Z", "+00:00"))
            if abs((published - kpub).total_seconds()) / 3600.0 <= window_hours:
                duplicate_of = k
                break
        if duplicate_of is None:
            kept.append(cand)
        else:
            dropped.append(
                {
                    "candidate_id": cand.candidate_id,
                    "signal_id": cand.candidate_id.removeprefix("CAND-"),
                    "reason": "duplicate_within_source_class",
                    "source_class": cand.source_class,
                    "duplicate_of": duplicate_of.candidate_id,
                    "window_hours": window_hours,
                    "freshness": cand.freshness,
                    "note": "Corroboration across classes is preserved; only restatements collapse.",
                }
            )
    kept.sort(key=lambda c: c.candidate_id)
    return kept, dropped


def _verify_iot_measurement(
    cand: CandidateEvent, network: Network
) -> tuple[bool, dict[str, Any], float]:
    """RULE-MEASURE: re-derive the claimed physical quantity from the trace."""
    shipment_id = cand.target
    claimed_peak = cand.claim.get("peak_temp_c")
    limit = float(cand.claim.get("temp_limit_max_c", 8.0))
    shipment = network.shipments.get(shipment_id)

    detail: dict[str, Any] = {
        "rule": "RULE-MEASURE",
        "description": "Direct measurement must be reproduced from the device trace.",
        "claimed_peak_c": claimed_peak,
        "trace": None,
        "corroborated": False,
    }

    if shipment is None:
        detail["reason"] = "target is not a known consignment"
        return False, detail, cand.raw_confidence

    analysis = telemetry.analyse(shipment_id, limit)
    if analysis is None or not analysis.breached:
        detail["reason"] = "no trace, or trace shows no excursion"
        detail["trace"] = telemetry.as_dict(analysis) if analysis else None
        return False, detail, cand.raw_confidence

    detail["trace"] = telemetry.as_dict(analysis)
    if claimed_peak is None:
        detail["reason"] = "claim carries no peak temperature to corroborate"
        return False, detail, cand.raw_confidence

    delta = abs(analysis.peak_temp_c - float(claimed_peak))
    detail["peak_delta_c"] = round(delta, 4)
    detail["tolerance_c"] = TRACE_PEAK_TOLERANCE_C
    if delta > TRACE_PEAK_TOLERANCE_C:
        detail["reason"] = f"trace peak differs from claim by {delta:.2f}C"
        return False, detail, cand.raw_confidence

    detail["corroborated"] = True
    detail["reason"] = "device trace reproduces the claimed peak within tolerance"
    return True, detail, cand.raw_confidence


def _verify_cross_source(
    cluster: list[CandidateEvent], thresholds: Thresholds
) -> tuple[bool, dict[str, Any], float]:
    """RULE-2SOURCE: agreement across distinct source classes."""
    classes = sorted({c.source_class for c in cluster})
    fresh = [c for c in cluster if c.freshness != "STALE"]
    fresh_classes = sorted({c.source_class for c in fresh})
    reliable = [
        c for c in fresh if c.source_reliability >= thresholds.min_source_reliability
    ]

    detail: dict[str, Any] = {
        "rule": "RULE-2SOURCE",
        "description": "A claim about the world needs agreement across source classes.",
        "required_source_classes": thresholds.min_source_classes,
        "source_classes": classes,
        "fresh_source_classes": fresh_classes,
        "min_source_reliability": thresholds.min_source_reliability,
        "signals": [c.candidate_id for c in cluster],
        "fresh_signals": [c.candidate_id for c in fresh],
        "corroborated": False,
    }

    if len(fresh_classes) < thresholds.min_source_classes:
        detail["reason"] = (
            f"only {len(fresh_classes)} fresh source class(es); "
            f"{thresholds.min_source_classes} required"
        )
        return False, detail, _weighted_confidence(cluster)

    if not reliable:
        detail["reason"] = "no fresh signal from a sufficiently reliable source"
        return False, detail, _weighted_confidence(cluster)

    detail["corroborated"] = True
    detail["reason"] = (
        f"{len(fresh_classes)} fresh source classes agree "
        f"({', '.join(fresh_classes)})"
    )
    return True, detail, _weighted_confidence(cluster)


def _weighted_confidence(cluster: list[CandidateEvent]) -> float:
    """Reliability-weighted mean of contributing raw confidences.

    Stale signals are excluded: an old story cannot inflate today's confidence.
    """
    fresh = [c for c in cluster if c.freshness != "STALE"] or cluster
    denom = sum(c.source_reliability for c in fresh)
    if denom <= 0:
        return round(max(c.raw_confidence for c in fresh), 6)
    return round(sum(c.raw_confidence * c.source_reliability for c in fresh) / denom, 6)


def verify(
    candidates: list[CandidateEvent], network: Network | None = None
) -> dict[str, Any]:
    """Run verification over A1's escalated candidates."""
    net = network or Network.load()
    thresholds = Thresholds.load()
    now = scenario_clock()

    escalated = [c for c in candidates if c.status == "ESCALATED"]
    groups: dict[tuple[str, str], list[CandidateEvent]] = {}
    for cand in escalated:
        groups.setdefault(_cluster_key(cand), []).append(cand)

    verified: list[VerifiedEvent] = []
    rejected: list[dict[str, Any]] = []
    deduped: list[dict[str, Any]] = []

    for (event_type, target), cluster in sorted(groups.items()):
        kept, dropped = _dedup(cluster, thresholds.dedup_window_hours)
        deduped.extend(dropped)

        if event_type == "COLD_CHAIN_EXCURSION":
            ok, detail, base = _verify_iot_measurement(kept[0], net)
            primary = kept[0]
            bonus = 0.0
            # The measurement-corroboration factor. 0.97 is applied on success so
            # a device-attested excursion lands at ~0.96, matching the contract
            # example in the team handoff.
            confidence = round(min(0.99, base * 0.97), 6) if ok else base
            detail["confidence_math"] = (
                f"{base} x 0.97 (trace corroborated) = {confidence}"
                if ok
                else f"{base} (uncorroborated)"
            )
        else:
            ok, detail, base = _verify_cross_source(kept, thresholds)
            classes = detail["fresh_source_classes"]
            bonus = min(0.05 * max(0, len(classes) - 1), 0.10)
            confidence = round(min(0.99, base + bonus), 6)
            detail["confidence_math"] = (
                f"{base} (reliability-weighted mean) + {round(bonus, 4)} "
                f"(corroboration bonus for {len(classes)} fresh source classes) "
                f"= {confidence}"
            )
            primary = max(kept, key=lambda c: c.source_reliability)

        severity = max(
            (c.severity for c in kept), key=lambda s: SEVERITY_RANK.get(s, 0)
        )
        detail["ok"] = ok
        detail["deduplicated_signals"] = [d["candidate_id"] for d in dropped]
        detail["freshness"] = primary.freshness
        detail["signal_age_hours"] = primary.age_hours
        detail["provenance"] = {
            "url": primary.provenance_url,
            "published_utc": primary.published_utc,
            "retrieved_utc": primary.retrieved_utc,
        }

        if not ok:
            rejected.append(
                {
                    "event_type": event_type,
                    "target": target,
                    "verification": detail,
                    "signals": [c.candidate_id for c in kept],
                }
            )
            continue

        verified.append(
            VerifiedEvent(
                event_id="",  # assigned below, deterministically
                event_type=event_type,
                target=target,
                severity=severity,
                confidence=confidence,
                timestamp=isoformat(now),
                source=primary.source_class,
                verification=detail,
                claim=primary.claim,
                contributing=[c.candidate_id for c in kept],
            )
        )

    # Deterministic event ids: highest severity first, then type, then target.
    verified.sort(
        key=lambda v: (-SEVERITY_RANK.get(v.severity, 0), v.event_type, v.target)
    )
    for idx, event in enumerate(verified, start=1):
        event.event_id = f"EVT-{idx:03d}"

    return {
        "agent": "A2_VERIFICATION",
        "verified_at": isoformat(now),
        "events": verified,
        "rejected": rejected,
        "deduplicated": deduped,
        "rules": {
            "RULE-2SOURCE": "agreement across >= 2 fresh source classes",
            "RULE-MEASURE": "direct measurement reproduced from the device trace",
        },
    }


# ---------------------------------------------------------------------------
# Scenario generation
# ---------------------------------------------------------------------------


def _clamp(value: float, bounds: list[float]) -> tuple[float, bool]:
    lo, hi = float(bounds[0]), float(bounds[1])
    if value < lo:
        return lo, True
    if value > hi:
        return hi, True
    return value, False


def generate_scenarios(events: list[VerifiedEvent]) -> list[Scenario]:
    """Turn verified events into parameterized horizons with probability weights.

    Weight model (deterministic, configurable):

        alignment(H)    = exp(-|H - duration_days| / max(duration_days, 1))
        raw_weight(H)   = event_confidence x alignment(H)
        weight(H)       = raw_weight(H) / sum_H raw_weight(H)

    The horizon that matches the disruption's own duration dominates, which is
    why the 10-day window drives the hero demo.
    """
    import math

    clamp_cfg = settings()["scenario"]["clamp"]

    scenarios: list[Scenario] = []
    for event in events:
        if event.event_type != "PORT_CLOSURE":
            continue
        dur, dur_clamped = _clamp(
            float(event.claim.get("duration_days", 7)), clamp_cfg["duration_days"]
        )
        delay, delay_clamped = _clamp(
            float(event.claim.get("transit_delay_days", 7)),
            clamp_cfg["transit_delay_days"],
        )
        cap, cap_clamped = _clamp(
            float(event.claim.get("capacity_factor", 0.5)),
            clamp_cfg["capacity_factor"],
        )
        parameters_source = {
            "event_id": event.event_id,
            "origin": "signal_claim (seeded feed)",
            "clamped": {
                "duration_days": dur_clamped,
                "transit_delay_days": delay_clamped,
                "capacity_factor": cap_clamped,
            },
            "bounds": clamp_cfg,
        }

        horizons = Thresholds.load().horizons_days
        alignments = {
            h: math.exp(-abs(h - dur) / max(dur, 1.0)) for h in horizons
        }
        raws = {h: event.confidence * alignments[h] for h in horizons}
        total = sum(raws.values()) or 1.0

        for horizon in horizons:
            scenarios.append(
                Scenario(
                    scenario_id=f"SCN-{event.event_id}-{horizon}D",
                    event_id=event.event_id,
                    name=f"{event.claim.get('corridor', event.target)} "
                    f"{event.event_type.lower().replace('_', ' ')} - {horizon}-day horizon",
                    horizon_days=horizon,
                    duration_days=dur,
                    transit_delay_days=delay,
                    capacity_factor=cap,
                    probability_weight=raws[horizon] / total,
                    confidence=event.confidence,
                    affected_lanes=[],  # filled by A3 once the network is loaded
                    narrative=(
                        f"Corridor disrupted for {dur:g} days with {delay:g} days of added "
                        f"transit. Evaluated over a {horizon}-day planning window."
                    ),
                    parameters_source=parameters_source,
                    contributed_by_events=[event.event_id],
                )
            )
    return scenarios


def verify_and_scenario(network: Network | None = None) -> dict[str, Any]:
    """Full A2 pass: verify A1's candidates, then generate scenarios."""
    from backend.agents.sensing import sense

    net = network or Network.load()
    sensing_result = sense(network=net)
    result = verify(sensing_result["candidates"], net)
    scenarios = generate_scenarios(result["events"])
    net_cfg = scenarios_config().get("horizons", [])
    return {
        "agent": "A2_VERIFICATION_AND_SCENARIO",
        "verified_at": result["verified_at"],
        "events": result["events"],
        "rejected": result["rejected"],
        "deduplicated": result["deduplicated"],
        "scenarios": scenarios,
        "rules": result["rules"],
        "horizons_configured": [h["days"] for h in net_cfg],
    }
