# ADR 0014 — Signal-feed adapters are opt-in, and a fetch is recorded as a snapshot

**Status:** Accepted · 2026-10

## Context

`data/events/hero_feeds.yaml` documents itself as standing in for live news,
port-authority, carrier and IoT feeds. That is an honest label, but it leaves the
obvious question unanswered: *would this work against a real source, or only
against eight lines of YAML someone wrote to make the demo work?*

Answering it naively would break two properties the project already guarantees:

1. **Byte-reproducibility.** A live response is not reproducible. If A1 read the
   network on every request, the scenario clock, the impact numbers and therefore
   the ledger hashes would all start moving.
2. **The offline guarantee.** `runtime.offline: true` is a stated promise that no
   external service is load-bearing. A feed that must be reachable contradicts it.

## Decision

Add three real adapters (`backend/agents/feeds/`) behind two independent switches,
and make a live fetch write a **snapshot** that a later run replays.

* `sensing.feeds.enabled` decides **which** source produces signals. Off (the
  default) means the scripted feed; on means the adapters. These are different
  scenarios by construction, so the switch is a *substitution*, not an addition —
  enabling the adapters changes the hero numbers, and that is why they are off.
* `sensing.feeds.live` decides **whether a fetch may happen**. Off replays the
  newest snapshot on disk. On fetches and writes one. `runtime.offline: true`
  overrides it: an air-gapped deployment can enable the adapters and still never
  open a socket.

The snapshot is a plain JSON document holding the request, the retrieval
timestamp and the response. Its digest is **derived** from the document, not
stored in it, so a hand-edited snapshot cannot keep claiming a digest it no
longer deserves.

Every adapter may only copy a value the payload actually carried, or attach a
configured constant. An article that matches no rule is `UNCLASSIFIED` and A1
watchlists it. A feed that cannot be read is `unavailable` and contributes
nothing. The loader reports all three cases by name.

## Consequences

* **Good.** The claim "swappable for a real source" is now testable rather than
  asserted, and the adapters make RULE-2SOURCE do real work: a news report and a
  weather forecast are genuinely independent evidence, and the replay proves it.
* **Good.** Determinism is preserved *and demonstrated*: `--from-fixtures` writes
  snapshots, a test replays them with the network transport stubbed to raise, and
  the resulting signals are compared field by field against the fetch.
* **Cost.** Two more switches, a snapshot directory to keep tidy, and an `origin`
  field on every snapshot that a reviewer has to read. `scripts/fetch_feeds.py
  --status` prints exactly which snapshots exist and which feeds have none.
* **Accepted.** The shipped snapshots are built from synthetic payloads that
  conform to each service's documented response shape (`origin: "fixture"`).
  They are not captures from the live services, and every file says so. A live
  capture is one `--live` away and is labelled `origin: "live"`.
* **Accepted.** Credentials come from the environment (`FREIGHTOS_API_KEY`) and
  are never written into a snapshot or a request spec. A test asserts it.

See [feeds.md](../feeds.md).
