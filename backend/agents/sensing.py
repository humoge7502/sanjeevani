"""A1 -- Disruption Sensing Agent (Member 1).

Signals -> normalize -> classify -> candidate events.

Honesty boundary: A1 always produces structure and provenance, never
authoritative numbers. The numbers a downstream agent consumes come from the
signal's structured `claim` payload, which is seeded data -- free text is
carried for the audit trail and for a human to read, and is never parsed into a
parameter. That is the prompt-injection containment decision from the dossier
(14.2), implemented rather than described.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.config import DATA_DIR, Thresholds, isoformat, scenario_clock, settings
from backend.graph.network import Network


@dataclass(frozen=True)
class RawSignal:
    """One untrusted record as it arrived from a feed."""

    signal_id: str
    source_class: str
    source_id: str
    source_reliability: float
    source_url: str
    published_utc: str
    retrieved_utc: str
    headline: str
    body: str
    entities: tuple[str, ...]
    claim: dict[str, Any]
    expectation: str | None = None

    def age_hours(self, now: datetime) -> float:
        published = datetime.fromisoformat(self.published_utc.replace("Z", "+00:00"))
        return round((now - published).total_seconds() / 3600.0, 4)


@dataclass
class CandidateEvent:
    """A1's output: a classified, scored, provenance-carrying candidate."""

    candidate_id: str
    event_type: str
    target: str
    severity: str
    source_class: str
    source_id: str
    source_reliability: float
    raw_confidence: float
    provenance_url: str
    published_utc: str
    retrieved_utc: str
    age_hours: float
    freshness: str  # FRESH | AGING | STALE
    claim: dict[str, Any]
    entities: tuple[str, ...]
    headline: str
    status: str  # ESCALATED | WATCHLIST
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "event_type": self.event_type,
            "target": self.target,
            "severity": self.severity,
            "source_class": self.source_class,
            "source_id": self.source_id,
            "source_reliability": self.source_reliability,
            "raw_confidence": round(self.raw_confidence, 4),
            "provenance": {
                "url": self.provenance_url,
                "published_utc": self.published_utc,
                "retrieved_utc": self.retrieved_utc,
            },
            "age_hours": self.age_hours,
            "freshness": self.freshness,
            "claim": self.claim,
            "entities": list(self.entities),
            "headline": self.headline,
            "status": self.status,
            "reasons": self.reasons,
        }


def _freshness(age_hours: float, max_age: float) -> str:
    if age_hours <= max_age * 0.5:
        return "FRESH"
    if age_hours <= max_age:
        return "AGING"
    return "STALE"


def load_feed(path: str | None = None) -> list[RawSignal]:
    """Load the signal feed. Files by default -- no network on the demo path.

    Two sources, one shape:

    * **scripted** (default) -- ``data/events/hero_feeds.yaml``. This is the
      documented demo path and its numbers are frozen.
    * **adapters** -- GDELT / Open-Meteo / Freightos, normalised into exactly the
      same fields. Enabled with ``settings.sensing.feeds.enabled`` /
      ``SANJEEVANI_FEEDS_ENABLED``. A replay reads a snapshot from disk, so
      nothing on this path needs the network either unless live fetching is
      explicitly requested.

    Passing ``path`` always selects the scripted loader, which is what the test
    suite and ``scripts/reset.py`` rely on.
    """
    if path is None and _feeds_enabled():
        return _load_adapter_feed()
    return _load_scripted_feed(path)


def _feeds_enabled() -> bool:
    return bool(settings().get("sensing", {}).get("feeds", {}).get("enabled"))


def _load_scripted_feed(path: str | None = None) -> list[RawSignal]:
    import yaml

    rel = path or "events/hero_feeds.yaml"
    feed_path = Path(rel)
    if not feed_path.is_absolute():
        feed_path = DATA_DIR / rel
    with feed_path.open("r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)

    signals: list[RawSignal] = []
    for s in doc["signals"]:
        signals.append(
            RawSignal(
                signal_id=s["signal_id"],
                source_class=s["source_class"],
                source_id=s["source_id"],
                source_reliability=float(s["source_reliability"]),
                source_url=s.get("source_url", ""),
                published_utc=s["published_utc"],
                retrieved_utc=s["retrieved_utc"],
                headline=s["headline"].strip(),
                body=" ".join((s.get("body") or "").split()),
                entities=tuple(s.get("entities") or ()),
                claim=dict(s.get("claim") or {}),
                expectation=s.get("expect"),
            )
        )
    _FEED_PROVENANCE.clear()
    _FEED_PROVENANCE.update(
        {
            "enabled": False,
            "mode": "scripted",
            "source": _display_path(feed_path),
            "signals": len(signals),
        }
    )
    return signals


def _display_path(path: Path) -> str:
    """Repo-relative when possible, absolute otherwise. Never raises."""
    try:
        return str(path.relative_to(DATA_DIR.parent))
    except ValueError:
        return str(path)


def _load_adapter_feed() -> list[RawSignal]:
    """Normalise adapter output into ``RawSignal``s.

    The retrieval timestamp is the *scenario* clock, not the wall clock: the
    system's notion of "now" is the scenario's, and mixing real time into a frozen
    clock would make every replayed signal stale by construction. It is recorded
    inside the snapshot, so a replay reproduces it exactly.
    """
    from backend.agents import feeds as feeds_pkg
    from backend.config import feeds_config

    cfg = settings()
    feeds_cfg = cfg["sensing"]["feeds"]
    snapshot_dir = Path(str(feeds_cfg.get("snapshot_dir") or "events/snapshots"))
    if not snapshot_dir.is_absolute():
        snapshot_dir = DATA_DIR / snapshot_dir

    load = feeds_pkg.load_signals(
        config=feeds_config(),
        snapshot_dir=snapshot_dir,
        retrieved_utc=isoformat(scenario_clock()),
        live=bool(feeds_cfg.get("live")),
        offline=bool(cfg.get("runtime", {}).get("offline", True)),
    )
    _FEED_PROVENANCE.clear()
    _FEED_PROVENANCE.update(load.provenance())
    return [RawSignal(**record.as_signal_kwargs()) for record in load.records]


def feed_provenance() -> dict[str, Any]:
    """What the last feed load actually did -- for ``/api/state`` and health."""
    return dict(_FEED_PROVENANCE)


def reset_feed_state() -> None:
    """Drop the recorded provenance. Used by the demo reset endpoint."""
    _FEED_PROVENANCE.clear()


#: The last load pass's provenance. Populated by both loaders so a caller can
#: always ask "which sources produced these signals, and which contributed
#: nothing?" without threading a return value through every call site.
_FEED_PROVENANCE: dict[str, Any] = {}


def classify(signal: RawSignal, network: Network, now: datetime) -> CandidateEvent:
    """Normalize + classify one signal into a scored candidate event.

    The score is a transparent product of two configured inputs:

        raw_confidence = source_reliability x source_class_weight

    No language model participates. If an LLM is ever enabled (settings
    runtime.llm_enabled) it may only add narrative text.
    """
    cfg = settings()
    weights = cfg["sensing"]["source_class_weights"]
    class_weight = float(weights.get(signal.source_class, 0.5))
    raw_confidence = round(signal.source_reliability * class_weight, 6)

    claim = signal.claim
    event_type = claim.get("event_type", "UNCLASSIFIED")
    target = claim.get("target", "UNKNOWN")
    severity = claim.get("severity") or _infer_severity(event_type, claim)

    age = signal.age_hours(now)
    freshness = _freshness(age, float(cfg["verification"]["max_signal_age_hours"]))

    reasons: list[str] = [
        f"source_class={signal.source_class} (weight {class_weight})",
        f"source_reliability={signal.source_reliability}",
        f"raw_confidence={raw_confidence}",
        f"signal_age={age}h -> {freshness}",
    ]

    # Target must exist in the network, otherwise we cannot act on it.
    known_targets = set(network.nodes) | set(network.shipments) | set(network.lanes)
    if target not in known_targets:
        status = "WATCHLIST"
        reasons.append(f"target {target!r} is not a known network entity")
    elif freshness == "STALE":
        status = "WATCHLIST"
        reasons.append("stale signal cannot escalate on its own")
    elif raw_confidence < Thresholds.load().escalate_confidence:
        status = "WATCHLIST"
        reasons.append(
            f"confidence {raw_confidence} below escalation gate "
            f"{Thresholds.load().escalate_confidence}"
        )
    else:
        status = "ESCALATED"
        reasons.append("confidence at or above escalation gate -> A2 verification")

    return CandidateEvent(
        candidate_id=f"CAND-{signal.signal_id}",
        event_type=event_type,
        target=target,
        severity=severity,
        source_class=signal.source_class,
        source_id=signal.source_id,
        source_reliability=signal.source_reliability,
        raw_confidence=raw_confidence,
        provenance_url=signal.source_url,
        published_utc=signal.published_utc,
        retrieved_utc=signal.retrieved_utc,
        age_hours=age,
        freshness=freshness,
        claim=claim,
        entities=signal.entities,
        headline=signal.headline,
        status=status,
        reasons=reasons,
    )


def _infer_severity(event_type: str, claim: dict[str, Any]) -> str:
    """Severity is a configured lookup, not a judgement call by a model."""
    if event_type == "COLD_CHAIN_EXCURSION":
        return "HIGH"
    if event_type == "PORT_CLOSURE":
        capacity = float(claim.get("capacity_factor", 1.0))
        return "HIGH" if capacity <= 0.25 else "MEDIUM"
    if event_type == "FREIGHT_RATE_SPIKE":
        # The band is a threshold on a number the payload carried. A rate that
        # has moved 40% is not a cost story any more, it is a sourcing story.
        move = abs(float(claim.get("rate_change_pct", 0.0)))
        return "HIGH" if move >= 40.0 else "MEDIUM"
    if event_type in {"TARIFF_CHANGE", "CAPACITY_SHORTAGE"}:
        return "MEDIUM"
    return "LOW"


def sense(feed_path: str | None = None, network: Network | None = None) -> dict[str, Any]:
    """Run A1 over the feed. Returns candidates split by disposition."""
    net = network or Network.load()
    now = scenario_clock()
    signals = load_feed(feed_path)
    candidates = [classify(s, net, now) for s in signals]

    return {
        "agent": "A1_SENSING",
        "sensed_at": isoformat(now),
        "signals_seen": len(signals),
        "feed": feed_provenance(),
        "escalated": [c.as_dict() for c in candidates if c.status == "ESCALATED"],
        "watchlist": [c.as_dict() for c in candidates if c.status == "WATCHLIST"],
        "candidates": candidates,
    }
