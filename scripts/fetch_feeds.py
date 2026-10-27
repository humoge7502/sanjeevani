#!/usr/bin/env python
"""Fetch signal feeds and write snapshots, or inspect the ones already on disk.

    python scripts/fetch_feeds.py --status          # what snapshots exist
    python scripts/fetch_feeds.py --live            # fetch every adapter, write a snapshot
    python scripts/fetch_feeds.py --from-fixtures   # (re)wrap data/events/feeds/*.json
    python scripts/fetch_feeds.py --verify          # check every snapshot parses and digests

Why a snapshot and not a cache. A cache is an optimisation; a snapshot is a
*record of evidence*. The difference matters here because the project's
determinism claim is about inputs: replaying a snapshot must produce the same
signals the fetch produced, and the snapshot is what makes that reproducible.

`--live` performs real network calls and writes real captures. The committed
snapshots are produced by `--from-fixtures` from the synthetic payloads under
`data/events/feeds/`, which conform to each service's documented response shape
but were not captured from the live services. The `origin` field in every
snapshot says which it is.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.agents.feeds import ADAPTERS, build_adapters
from backend.agents.feeds.base import FeedUnavailable
from backend.agents.feeds.snapshot import ORIGIN_FIXTURE, ORIGIN_LIVE, SnapshotStore
from backend.config import DATA_DIR, feeds_config

#: The scenario clock, so a shipped fixture is consistent with the rest of the
#: seeded data rather than being "now" and therefore stale on every replay.
FIXTURE_RETRIEVED_UTC = "2026-09-30T05:50:00Z"

FIXTURE_FILES = {
    "gdelt": "gdelt_sample_response.json",
    "open_meteo": "open_meteo_sample_response.json",
    "freightos": "freightos_sample_response.json",
}


def snapshot_dir() -> Path:
    configured = feeds_config().get("snapshot_dir")
    if configured:
        return Path(str(configured))
    return DATA_DIR / "events" / "snapshots"


def cmd_status(store: SnapshotStore) -> int:
    status = store.status()
    print(f"snapshot directory: {status['directory']}")
    print(f"snapshots on disk:  {status['count']}")
    for entry in status["snapshots"]:
        print(
            f"  {entry.get('feed_id', '?'):<12} {entry.get('digest', '?'):<14} "
            f"{entry.get('origin', '?'):<8} {entry.get('retrieved_utc', '?')}  "
            f"{Path(str(entry.get('path', ''))).name}"
        )
    for feed_id in ADAPTERS:
        present = store.latest(feed_id)
        state = "replayable" if present else "NO SNAPSHOT (would contribute nothing)"
        print(f"  {feed_id:<12} {state}")
    return 0


def cmd_from_fixtures(store: SnapshotStore) -> int:
    adapters = {a.feed_id: a for a in build_adapters(feeds_config())}
    fixtures_dir = DATA_DIR / "events" / "feeds"
    written = 0
    for feed_id, filename in FIXTURE_FILES.items():
        adapter = adapters.get(feed_id)
        if adapter is None:
            print(f"  {feed_id}: not in the catalogue, skipped")
            continue
        path = fixtures_dir / filename
        if not path.exists():
            print(f"  {feed_id}: fixture {path} missing, skipped")
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        snapshot = store.write(
            feed_id,
            adapter.request_spec(),
            FIXTURE_RETRIEVED_UTC,
            payload,
            origin=ORIGIN_FIXTURE,
        )
        signals = adapter.normalise(snapshot.response, snapshot.retrieved_utc)
        print(f"  {feed_id}: wrote {snapshot.path.name} -> {len(signals)} signal(s)")
        written += 1
    print(f"wrote {written} snapshot(s) under {store.directory}")
    return 0


def cmd_live(store: SnapshotStore, timeout: float) -> int:
    retrieved = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    failures = 0
    for adapter in build_adapters(feeds_config()):
        reason = adapter.preflight()
        if reason:
            print(f"  {adapter.feed_id}: skipped ({reason})")
            continue
        try:
            payload = adapter.fetch(timeout=timeout)
        except FeedUnavailable as exc:
            print(f"  {adapter.feed_id}: UNAVAILABLE ({exc})")
            failures += 1
            continue
        snapshot = store.write(
            adapter.feed_id, adapter.request_spec(), retrieved, payload, origin=ORIGIN_LIVE
        )
        signals = adapter.normalise(snapshot.response, snapshot.retrieved_utc)
        print(f"  {adapter.feed_id}: {snapshot.path.name} -> {len(signals)} signal(s)")
    print(f"live capture complete ({failures} unavailable)")
    return 1 if failures else 0


def cmd_verify(store: SnapshotStore) -> int:
    status = store.status()
    problems = 0
    for entry in status["snapshots"]:
        if entry.get("error"):
            print(f"  FAIL {entry['path']}: {entry['error']}")
            problems += 1
            continue
        name = Path(str(entry["path"])).name
        expected = name.rsplit("-", 1)[-1].removesuffix(".json")
        ok = expected == entry["digest"]
        print(
            f"  {'OK  ' if ok else 'FAIL'} {name}: digest {entry['digest']}"
            f"{'' if ok else ' does not match its filename'}"
        )
        problems += 0 if ok else 1
    print(f"{status['count']} snapshot(s) checked, {problems} problem(s)")
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="list snapshots on disk")
    group.add_argument("--live", action="store_true", help="fetch live and write snapshots")
    group.add_argument(
        "--from-fixtures",
        action="store_true",
        help="wrap data/events/feeds/*.json into snapshots",
    )
    group.add_argument("--verify", action="store_true", help="check every snapshot parses")
    parser.add_argument("--timeout", type=float, default=10.0, help="per-request timeout")
    args = parser.parse_args()

    store = SnapshotStore(snapshot_dir())
    if args.live:
        return cmd_live(store, args.timeout)
    if args.from_fixtures:
        return cmd_from_fixtures(store)
    if args.verify:
        return cmd_verify(store)
    return cmd_status(store)


if __name__ == "__main__":
    raise SystemExit(main())
