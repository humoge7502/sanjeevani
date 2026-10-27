"""Signal-feed adapters (Member 1).

``load_signals`` is the only entry point. It answers one question for A1: "what
signals does the configured set of sources currently yield?" -- and it answers it
from disk, never from a live call, unless live fetching was explicitly turned on.

Exporting this as a single function is what keeps ``sensing.load_feed`` a
one-line branch and keeps every downstream contract untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.agents.feeds.base import (
    DEFAULT_TIMEOUT_SECONDS,
    FeedAdapter,
    FeedUnavailable,
    SignalRecord,
    canonical_json,
    collapse,
    short_digest,
)
from backend.agents.feeds.freightos import FreightosRateFeed
from backend.agents.feeds.gdelt import GdeltNewsFeed
from backend.agents.feeds.openmeteo import OpenMeteoFeed
from backend.agents.feeds.snapshot import (
    ORIGIN_FIXTURE,
    ORIGIN_LIVE,
    SNAPSHOT_VERSION,
    Snapshot,
    SnapshotStore,
)

#: The adapter registry. Insertion order is the load order and is therefore part
#: of the deterministic output ordering.
ADAPTERS: dict[str, type[FeedAdapter]] = {
    GdeltNewsFeed.feed_id: GdeltNewsFeed,
    OpenMeteoFeed.feed_id: OpenMeteoFeed,
    FreightosRateFeed.feed_id: FreightosRateFeed,
}

__all__ = [
    "ADAPTERS",
    "DEFAULT_TIMEOUT_SECONDS",
    "ORIGIN_FIXTURE",
    "ORIGIN_LIVE",
    "SNAPSHOT_VERSION",
    "FeedAdapter",
    "FeedLoad",
    "FeedUnavailable",
    "SignalRecord",
    "Snapshot",
    "SnapshotStore",
    "build_adapters",
    "canonical_json",
    "collapse",
    "load_signals",
    "short_digest",
]


def build_adapters(config: dict[str, Any]) -> list[FeedAdapter]:
    """Instantiate the adapters named in ``config/feeds.yaml``.

    A feed absent from the catalogue is not built at all, so removing a section
    removes the source cleanly rather than leaving a half-configured adapter.
    """
    catalogue = config.get("feeds") or {}
    return [
        adapter_cls(dict(catalogue[feed_id]))
        for feed_id, adapter_cls in ADAPTERS.items()
        if isinstance(catalogue.get(feed_id), dict)
    ]


@dataclass
class FeedLoad:
    """The outcome of one load pass, including what did *not* contribute."""

    mode: str
    records: list[SignalRecord] = field(default_factory=list)
    adapters: list[dict[str, Any]] = field(default_factory=list)
    retrieved_utc: str = ""

    @property
    def enabled(self) -> bool:
        return True

    def provenance(self) -> dict[str, Any]:
        contributed = [a for a in self.adapters if a.get("status") == "loaded"]
        return {
            "enabled": True,
            "mode": self.mode,
            "retrieved_utc": self.retrieved_utc,
            "signals": len(self.records),
            "sources_loaded": len(contributed),
            "sources_configured": len(self.adapters),
            "adapters": self.adapters,
        }


def _report(adapter: FeedAdapter, status: str, reason: str = "", **extra: Any) -> dict[str, Any]:
    return {
        "feed_id": adapter.feed_id,
        "name": adapter.display_name,
        "source_class": adapter.source_class,
        "endpoint": adapter.endpoint,
        "status": status,
        "reason": reason,
        **extra,
    }


def load_signals(
    *,
    config: dict[str, Any],
    snapshot_dir: Path,
    retrieved_utc: str,
    live: bool = False,
    offline: bool = True,
) -> FeedLoad:
    """Load every configured feed, from a snapshot or (if allowed) live.

    ``offline`` and ``live`` are checked separately and both must permit a fetch
    before one happens. ``runtime.offline: true`` is the project's stated
    guarantee that no external service is load-bearing, so it wins.
    """
    store = SnapshotStore(snapshot_dir)
    records: list[SignalRecord] = []
    reports: list[dict[str, Any]] = []

    for adapter in build_adapters(config):
        reason = adapter.preflight()
        if reason:
            reports.append(_report(adapter, "skipped", reason))
            continue
        if live and offline:
            reports.append(
                _report(adapter, "skipped", "runtime.offline is true: live fetch refused")
            )
            continue

        try:
            if live:
                payload = adapter.fetch()
                snapshot = store.write(
                    adapter.feed_id,
                    adapter.request_spec(),
                    retrieved_utc,
                    payload,
                    origin=ORIGIN_LIVE,
                )
            else:
                found = store.latest(adapter.feed_id)
                if found is None:
                    reports.append(
                        _report(
                            adapter,
                            "no_snapshot",
                            f"no snapshot for {adapter.feed_id} under {snapshot_dir}",
                        )
                    )
                    continue
                snapshot = found
                payload = snapshot.response
        except FeedUnavailable as exc:
            reports.append(_report(adapter, "unavailable", str(exc)))
            continue

        produced = adapter.normalise(payload, snapshot.retrieved_utc)
        records.extend(produced)
        reports.append(
            _report(
                adapter,
                "loaded",
                "",
                origin=snapshot.origin,
                signals=len(produced),
                snapshot=snapshot.as_dict(),
            )
        )

    records.sort(key=lambda r: (r.source_class, r.signal_id))
    return FeedLoad(
        mode="live" if live else "snapshot",
        records=records,
        adapters=reports,
        retrieved_utc=retrieved_utc,
    )
