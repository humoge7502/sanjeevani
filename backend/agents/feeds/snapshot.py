"""Fetch-to-snapshot replay: the reason a live feed can be deterministic.

A live HTTP response is not reproducible -- the news changes, the forecast
changes, the rate moves. The system's determinism guarantee is that two runs
over the same *inputs* produce the same outputs, so the fix is not to make the
network deterministic. It is to make the network a one-time act that writes down
what it saw.

::

    live fetch  ->  snapshot file on disk  ->  signal normalisation
                            ^
    replay     ---------------------------

The snapshot is a plain JSON document holding the request, the retrieval
timestamp and the response. Replaying it cannot touch the network, and because
``retrieved_utc`` is *inside* the document, the replay produces byte-identical
signals. The digest is derived from the document rather than stored in it, so a
hand-edited snapshot cannot claim a digest it does not deserve.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.agents.feeds.base import FeedUnavailable, canonical_json, short_digest

SNAPSHOT_VERSION = "1.0.0"

#: Where a snapshot came from. Recorded so a reviewer can tell a live capture
#: from a shipped fixture without reading the payload.
ORIGIN_LIVE = "live"
ORIGIN_FIXTURE = "fixture"


@dataclass(frozen=True)
class Snapshot:
    path: Path
    feed_id: str
    version: str
    request: dict[str, Any]
    retrieved_utc: str
    response: Any
    origin: str
    digest: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "feed_id": self.feed_id,
            "path": str(self.path),
            "digest": self.digest,
            "retrieved_utc": self.retrieved_utc,
            "origin": self.origin,
            "version": self.version,
        }


def _document(
    feed_id: str,
    request: dict[str, Any],
    retrieved_utc: str,
    response: Any,
    origin: str,
) -> dict[str, Any]:
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "feed_id": feed_id,
        "origin": origin,
        "retrieved_utc": retrieved_utc,
        "request": request,
        "response": response,
    }


def _digest_of(document: dict[str, Any]) -> str:
    """Content digest over the document's meaningful fields.

    Derived, never stored: a hand-edited snapshot cannot carry a forged digest.
    """
    return short_digest(
        document.get("snapshot_version"),
        document.get("feed_id"),
        document.get("origin"),
        document.get("retrieved_utc"),
        document.get("request"),
        document.get("response"),
    )


class SnapshotStore:
    """A directory of feed snapshots, one file per (feed, content) pair."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    # -- writing --------------------------------------------------------

    def write(
        self,
        feed_id: str,
        request: dict[str, Any],
        retrieved_utc: str,
        response: Any,
        origin: str = ORIGIN_LIVE,
    ) -> Snapshot:
        document = _document(feed_id, request, retrieved_utc, response, origin)
        digest = _digest_of(document)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{feed_id}-{digest}.json"
        if not path.exists():
            with path.open("w", encoding="utf-8", newline="\n") as fh:
                json.dump(document, fh, indent=2, sort_keys=False, ensure_ascii=True)
                fh.write("\n")
        return self.load(path)

    # -- reading --------------------------------------------------------

    def load(self, path: Path) -> Snapshot:
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise FeedUnavailable(f"snapshot {path.name} is unreadable: {exc}") from exc
        if not isinstance(raw, dict) or "response" not in raw:
            raise FeedUnavailable(f"snapshot {path.name} is not a snapshot document")
        return Snapshot(
            path=path,
            feed_id=str(raw.get("feed_id") or ""),
            version=str(raw.get("snapshot_version") or ""),
            request=dict(raw.get("request") or {}),
            retrieved_utc=str(raw.get("retrieved_utc") or ""),
            response=raw["response"],
            origin=str(raw.get("origin") or ORIGIN_LIVE),
            digest=_digest_of(raw),
        )

    def list(self, feed_id: str) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(self.directory.glob(f"{feed_id}-*.json"))

    def latest(self, feed_id: str) -> Snapshot | None:
        """Newest snapshot for a feed, ordered by recorded retrieval time."""
        candidates: list[tuple[str, str, Path]] = []
        for path in self.list(feed_id):
            try:
                snapshot = self.load(path)
            except FeedUnavailable:
                continue
            if snapshot.feed_id != feed_id:
                continue
            candidates.append((snapshot.retrieved_utc, path.name, snapshot.path))
        if not candidates:
            return None
        return self.load(max(candidates)[2])

    def status(self) -> dict[str, Any]:
        """An inventory for /api/state: what is on disk, and what it covers."""
        files: list[dict[str, Any]] = []
        if self.directory.is_dir():
            for path in sorted(self.directory.glob("*.json")):
                try:
                    files.append(self.load(path).as_dict())
                except FeedUnavailable:
                    files.append({"path": str(path), "error": "unreadable"})
        return {"directory": str(self.directory), "count": len(files), "snapshots": files}


def canonical(document: Any) -> str:
    """Exposed for tests that assert snapshot bytes are stable."""
    return canonical_json(document)
