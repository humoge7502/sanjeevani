# Signal feeds

A1 reads one shape. Two loaders produce it.

```
data/events/hero_feeds.yaml  ─┐
                              ├─→ RawSignal[] → classify → CandidateEvent[]
backend/agents/feeds/        ─┘
```

The default is the scripted feed. The adapters are the answer to "would this work
against a real source?", and they are **off** so the hero scenario keeps its
byte-reproducible numbers. ADR
[0014](decisions/0014-feeds-are-opt-in-and-fetches-are-snapshotted.md) has the
reasoning; this document has the operation.

---

## Turning them on

```bash
# Replay the snapshots shipped in the repository. No network.
SANJEEVANI_FEEDS_ENABLED=true python scripts/demo.py

# Capture fresh snapshots first, then replay them.
python scripts/fetch_feeds.py --live            # writes data/events/snapshots/
SANJEEVANI_FEEDS_ENABLED=true python scripts/demo.py
```

| Variable | Default | Meaning |
|---|---|---|
| `SANJEEVANI_FEEDS_ENABLED` | `false` | Use the adapters instead of the scripted feed |
| `SANJEEVANI_FEEDS_LIVE` | `false` | Allow a network fetch (writes a snapshot). Refused while `SANJEEVANI_OFFLINE=true` |
| `SANJEEVANI_FEEDS_SNAPSHOT_DIR` | `events/snapshots` | Where snapshots are read and written, relative to `data/` |
| `FREIGHTOS_API_KEY` | unset | Required by the rate adapter. With no key it reports `unavailable` |

Enabling the adapters **replaces** the scripted feed. It is not additive, and the
resulting scenario is not the hero scenario: the adapters have no analogue of the
reefer device that produces the cold-chain excursion. That is intentional — a
switch that left the demo looking identical would prove nothing.

---

## The three adapters

| Adapter | Source | Source class | What it can claim |
|---|---|---|---|
| `gdelt` | GDELT DOC 2.0 API (global news) | `NEWS` | A keyword rulebook maps an article onto `PORT_CLOSURE` / `CAPACITY_SHORTAGE`; an unmatched article is `UNCLASSIFIED` |
| `open_meteo` | Open-Meteo forecast API | `WEATHER` | Wind at or above the operability ceiling becomes a `PORT_CLOSURE` claim; a temperature outside the band becomes `WEATHER_ANOMALY` |
| `freightos` | Freightos ocean rate feed | `CARRIER` | A rate move at or above the threshold becomes `FREIGHT_RATE_SPIKE`, carrying the payload's own rates |

Every definition — endpoint, query, reliability table, keyword rulebook,
thresholds — is in [config/feeds.yaml](../config/feeds.yaml). The switch and the
snapshot directory are in [config/settings.yaml](../config/settings.yaml).

### What an adapter may and may not do

**May:** copy a number the payload carried (`peak_wind_kmh`, `rate_usd_per_feu`,
`temperature_2m`), and attach a configured constant (`duration_days`,
`capacity_factor`, a reliability prior).

**May not:** parse free text into a parameter; invent a value when the payload is
silent; treat a missing credential or an unreachable endpoint as a reason to
produce a plausible-looking default.

Three behaviours follow, and each has a test:

* An article matching no rule is `UNCLASSIFIED`/`UNKNOWN`, so A1 watchlists it.
  It is corroboration, nothing more.
* A site whose forecast is inside every band produces **no signal**. Silence is a
  result.
* A feed with no credential or no snapshot contributes **nothing**, and the
  loader names it, its status and its reason.

---

## Why a snapshot, and not a cache

A cache is an optimisation you may lose. A snapshot is a record of evidence.

```bash
python scripts/fetch_feeds.py --status          # what is on disk, what is missing
python scripts/fetch_feeds.py --verify          # recompute every digest
python scripts/fetch_feeds.py --from-fixtures   # rebuild from data/events/feeds/*.json
python scripts/fetch_feeds.py --live            # capture from the real services
```

A snapshot is a plain JSON document:

```json
{
  "snapshot_version": "1.0.0",
  "feed_id": "gdelt",
  "origin": "fixture",
  "retrieved_utc": "2026-09-30T05:50:00Z",
  "request": { "feed_id": "gdelt", "endpoint": "https://...", "params": { "...": "..." } },
  "response": { "articles": ["..."] }
}
```

Three properties make it usable as evidence rather than as a convenience:

* **The retrieval time is inside the document.** So a replay produces the same
  `age_hours`, the same freshness verdict and the same signals — not "the same
  signals modulo the clock".
* **The digest is derived, not stored.** It is the first 12 hex characters of a
  SHA-256 over the document's meaningful fields, and it is also the filename. Edit
  the file and the digest changes, so the file can no longer masquerade as the
  capture it was written from.
* **`origin` says what it is.** `live` is a real capture; `fixture` was built
  from a synthetic payload conforming to the documented response shape. The
  committed snapshots are all `fixture`, and the test suite asserts it. Nothing
  in this repository pretends to be a capture from a service it never called.

`retrieved_utc` is the **scenario** clock, not the wall clock. In a frozen-clock
scenario, mixing real time in would make every live signal stale by construction
— a signal "published" at 05:12 against a clock reading ten days later cannot
pass a 24-hour freshness gate. Using the scenario's own notion of "now" keeps the
feed consistent with everything else, and the value is recorded so a replay
reproduces it exactly.

---

## What you get when you enable it

`scripts/health.py` on the default configuration:

```
M1 · upstream intelligence
  PASS  Signal-feed adapters (GDELT / Open-Meteo / Freightos) — 3 adapters
        configured · 8 signals replayed offline · 3 snapshot(s) on disk ·
        adapters off by default
```

With `SANJEEVANI_FEEDS_ENABLED=true`, the shipped snapshots produce a scenario
that is genuinely different and, in one respect, better:

* GDELT's `NEWS` signals (four articles) and Open-Meteo's `WEATHER` signal both
  report `PORT_CLOSURE` at `PORT-SUEZ`. A2 dedups the restatements inside the news
  class, then verifies the cluster on **two independent source classes** —
  `fresh_source_classes: ["NEWS", "WEATHER"]` — and the rule is doing what it was
  written for rather than what a fixture arranged.
* The unlisted-domain article lands on the watchlist as `UNCLASSIFIED`.
* A rate rise on `LANE-MUM-BIO-EU` would arrive as `FREIGHT_RATE_SPIKE` unless
  `FREIGHTOS_API_KEY` is unset, in which case the source is reported `skipped`
  with the reason.
* A `WEATHER_ANOMALY` at JNPT escalates and is then **rejected** by verification,
  because one source class is not corroboration no matter how confident it is.

`GET /api/state` → `feeds` and `GET /api/mock-boundaries` → `feeds_status` report
which source produced the current signals and what every configured source did.
`mock_boundaries.real` only lists the adapters when they are actually the source
in use.
