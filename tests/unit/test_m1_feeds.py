"""M1 signal-feed adapter tests.

What this module is actually defending:

* **Shape identity.** An adapter's output must be indistinguishable, downstream,
  from a record in ``data/events/hero_feeds.yaml``. If that ever stops being
  true the whole "swap the source without touching a contract" claim is dead.
* **No invention.** A feed that cannot be read contributes nothing. An article
  that matches no rule is ``UNCLASSIFIED``, not a guess. A missing credential is
  ``unavailable``, not a seeded rate.
* **Replay determinism.** A snapshot replays offline and byte-identically.
* **The default is untouched.** With the adapters off, the scripted hero feed is
  exactly what it was.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agents import feeds as feeds_pkg
from backend.agents.feeds.base import FeedUnavailable
from backend.agents.feeds.freightos import FreightosRateFeed
from backend.agents.feeds.gdelt import GdeltNewsFeed, parse_seendate
from backend.agents.feeds.openmeteo import OpenMeteoFeed, canonical_hour, series_stats
from backend.agents.feeds.snapshot import (
    ORIGIN_FIXTURE,
    ORIGIN_LIVE,
    SnapshotStore,
)
from backend.agents.sensing import load_feed, sense
from backend.config import DATA_DIR, REPO_ROOT, feeds_config, reload_config, settings

FIXTURES = DATA_DIR / "events" / "feeds"
SNAPSHOTS = DATA_DIR / "events" / "snapshots"
RETRIEVED = "2026-09-30T05:50:00Z"

RAW_SIGNAL_FIELDS = {
    "signal_id",
    "source_class",
    "source_id",
    "source_reliability",
    "source_url",
    "published_utc",
    "retrieved_utc",
    "headline",
    "body",
    "entities",
    "claim",
}


def _catalogue() -> dict:
    return feeds_config()["feeds"]


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _snapshot_dir() -> Path:
    return DATA_DIR / str(settings()["sensing"]["feeds"]["snapshot_dir"])


def _gdelt() -> GdeltNewsFeed:
    return GdeltNewsFeed(dict(_catalogue()["gdelt"]))


def _meteo() -> OpenMeteoFeed:
    return OpenMeteoFeed(dict(_catalogue()["open_meteo"]))


def _freightos() -> FreightosRateFeed:
    return FreightosRateFeed(dict(_catalogue()["freightos"]))


@pytest.fixture
def adapters_on(monkeypatch):
    """Enable the adapter feed path for one test, then restore the default."""
    from backend.agents import sensing

    monkeypatch.setenv("SANJEEVANI_FEEDS_ENABLED", "true")
    reload_config()
    sensing.reset_feed_state()
    yield
    monkeypatch.delenv("SANJEEVANI_FEEDS_ENABLED", raising=False)
    reload_config()
    sensing.reset_feed_state()


# ---------------------------------------------------------------------------
# The shape contract
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_adapters_emit_exactly_the_scripted_feed_field_set():
    """Every adapter's output is consumable as a RawSignal, with no extra keys."""
    produced = [
        *_gdelt().normalise(_fixture("gdelt_sample_response.json"), RETRIEVED),
        *_meteo().normalise(_fixture("open_meteo_sample_response.json"), RETRIEVED),
        *_freightos().normalise(_fixture("freightos_sample_response.json"), RETRIEVED),
    ]
    assert produced, "the shipped fixtures must produce signals"
    for record in produced:
        assert set(record.as_signal_kwargs()) == RAW_SIGNAL_FIELDS


@pytest.mark.unit
def test_scripted_feed_is_the_default_and_unchanged(monkeypatch):
    """With the adapters off, A1 loads the scripted hero feed and says so."""
    monkeypatch.delenv("SANJEEVANI_FEEDS_ENABLED", raising=False)
    reload_config()
    signals = load_feed()
    provenance = sense()["feed"]
    assert len(signals) == 8
    assert provenance["enabled"] is False
    assert provenance["mode"] == "scripted"
    assert provenance["signals"] == 8
    assert provenance["source"].endswith("hero_feeds.yaml")


@pytest.mark.unit
def test_enabling_adapters_replaces_the_scripted_feed(adapters_on):
    """The switch is real: the two paths do not produce the same scenario."""
    adapter_events = {c.event_type for c in sense()["candidates"]}
    assert load_feed()[0].source_class in {"NEWS", "WEATHER", "CARRIER"}
    assert sense()["feed"]["mode"] == "snapshot"
    # The scripted feed's device-measured excursion has no adapter analogue,
    # which is exactly why the adapters are opt-in rather than the default.
    assert "COLD_CHAIN_EXCURSION" not in adapter_events


# ---------------------------------------------------------------------------
# GDELT
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_gdelt_seendate_is_parsed_into_a_zulu_timestamp():
    assert parse_seendate("20260930T051200Z") == "2026-09-30T05:12:00Z"
    assert parse_seendate("not-a-date") is None
    assert parse_seendate("") is None


@pytest.mark.unit
def test_gdelt_reads_its_own_payload_and_uses_the_domain_prior():
    records = _gdelt().normalise(_fixture("gdelt_sample_response.json"), RETRIEVED)
    by_domain = {r.source_id: r for r in records}
    assert by_domain["reuters.com"].source_reliability == 0.92
    # An unknown domain gets the configured fallback, not the highest prior.
    assert by_domain["port-news-daily.example"].source_reliability == 0.60


@pytest.mark.unit
def test_gdelt_claim_parameters_come_from_the_rulebook_not_the_headline():
    records = _gdelt().normalise(_fixture("gdelt_sample_response.json"), RETRIEVED)
    reuters = next(r for r in records if r.source_id == "reuters.com")
    assert reuters.claim == {
        "event_type": "PORT_CLOSURE",
        "target": "PORT-SUEZ",
        "corridor": "RED_SEA",
        "duration_days": 10,
        "transit_delay_days": 12,
        "capacity_factor": 0.15,
    }
    assert reuters.entities == ("PORT-SUEZ", "RED-SEA", "LANE-MUM-BIO-EU")


@pytest.mark.unit
def test_gdelt_unmatched_article_is_unclassified_rather_than_guessed():
    records = _gdelt().normalise(_fixture("gdelt_sample_response.json"), RETRIEVED)
    unmatched = next(r for r in records if r.source_id == "port-news-daily.example")
    assert unmatched.claim == {"event_type": "UNCLASSIFIED", "target": "UNKNOWN"}
    assert unmatched.entities == ()
    # It is still a signal: it is corroboration, nothing more.
    assert unmatched.headline


@pytest.mark.unit
def test_gdelt_signal_ids_are_content_addressed():
    payload = _fixture("gdelt_sample_response.json")
    first = [r.signal_id for r in _gdelt().normalise(payload, RETRIEVED)]
    second = [r.signal_id for r in _gdelt().normalise(payload, "2030-01-01T00:00:00Z")]
    assert sorted(first) == sorted(second), "ids must not depend on retrieval time"


@pytest.mark.unit
def test_gdelt_returns_nothing_for_a_malformed_payload():
    for payload in (None, {}, {"articles": "nope"}, {"articles": [None, {}]}):
        assert _gdelt().normalise(payload, RETRIEVED) == []


# ---------------------------------------------------------------------------
# Open-Meteo
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_open_meteo_hour_strings_are_made_timezone_aware():
    """A naive timestamp reaching A1 is a crash. Asserted, not assumed."""
    assert canonical_hour("2026-09-30T05:00") == "2026-09-30T05:00:00Z"
    assert canonical_hour("2026-09-30T05:00:00Z") == "2026-09-30T05:00:00Z"


@pytest.mark.unit
def test_open_meteo_aggregation_is_max_and_min_over_the_series():
    stats = series_stats(_fixture("open_meteo_sample_response.json")["sites"]["PORT-SUEZ"])
    assert stats is not None
    assert stats["peak_wind_kmh"] == 84.2
    assert stats["peak_temp_c"] == 39.2
    assert stats["hours"] == 8
    assert stats["last_hour_utc"] == "2026-09-30T05:00:00Z"


@pytest.mark.unit
def test_wind_above_the_operability_ceiling_becomes_a_port_closure_claim():
    records = _meteo().normalise(_fixture("open_meteo_sample_response.json"), RETRIEVED)
    closure = next(r for r in records if r.claim["event_type"] == "PORT_CLOSURE")
    assert closure.claim["target"] == "PORT-SUEZ"
    assert closure.claim["peak_wind_kmh"] == 84.2
    assert closure.claim["cause"] == "WEATHER"
    assert closure.source_class == "WEATHER"


@pytest.mark.unit
def test_a_site_within_band_and_below_the_wind_ceiling_produces_no_signal():
    """Silence is a result. A quiet forecast must not become a fabricated alert."""
    records = _meteo().normalise(_fixture("open_meteo_sample_response.json"), RETRIEVED)
    targets = {r.claim["target"] for r in records}
    assert "WH-BOM" not in targets
    assert "WH-FRA" not in targets


@pytest.mark.unit
def test_temperature_outside_the_band_is_an_anomaly_not_a_closure():
    records = _meteo().normalise(_fixture("open_meteo_sample_response.json"), RETRIEVED)
    anomaly = next(r for r in records if r.claim["event_type"] == "WEATHER_ANOMALY")
    assert anomaly.claim["target"] == "PORT-JNPT"
    assert anomaly.claim["peak_temp_c"] == 33.6
    assert anomaly.claim["band_c"] == [2.0, 30.0]


@pytest.mark.unit
def test_open_meteo_returns_nothing_without_an_hourly_block():
    assert _meteo().normalise({"sites": {"X": {"hourly": {}}}}, RETRIEVED) == []
    assert _meteo().normalise(None, RETRIEVED) == []


# ---------------------------------------------------------------------------
# Freightos
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_freightos_refuses_to_run_without_a_credential(monkeypatch):
    monkeypatch.delenv("FREIGHTOS_API_KEY", raising=False)
    assert _freightos().preflight() == "missing credential: set FREIGHTOS_API_KEY"


@pytest.mark.unit
def test_freightos_does_not_attach_its_credential_to_the_recorded_request(monkeypatch):
    monkeypatch.setenv("FREIGHTOS_API_KEY", "super-secret-value")
    spec = json.dumps(_freightos().request_spec())
    assert "super-secret-value" not in spec


@pytest.mark.unit
def test_freightos_reports_only_moves_above_the_configured_threshold():
    records = _freightos().normalise(_fixture("freightos_sample_response.json"), RETRIEVED)
    lanes = {r.claim["target"] for r in records}
    # +2.7% and -12.9% are both inside the 15% noise band and produce nothing.
    assert "LANE-CN-A-API" not in lanes
    assert "LANE-CN-B-API" not in lanes
    assert lanes == {"LANE-MUM-BIO-EU", "LANE-UNLISTED-CORRIDOR"}


@pytest.mark.unit
def test_freightos_change_percent_is_arithmetic_over_the_payload():
    records = _freightos().normalise(_fixture("freightos_sample_response.json"), RETRIEVED)
    bio = next(r for r in records if r.claim["target"] == "LANE-MUM-BIO-EU")
    expected = round((4310.0 - 3150.0) / 3150.0 * 100.0, 4)
    assert bio.claim["rate_change_pct"] == expected
    assert bio.claim["previous_rate_usd_per_feu"] == 3150.0
    assert bio.claim["event_type"] == "FREIGHT_RATE_SPIKE"


@pytest.mark.unit
def test_freightos_ignores_rows_missing_its_required_fields():
    payload = {"data": [{"lane": "LANE-X"}, {"lane": "LANE-Y", "rate_usd_per_feu": 10.0}]}
    assert _freightos().normalise(payload, RETRIEVED) == []


@pytest.mark.unit
def test_freightos_ignores_a_zero_baseline_instead_of_dividing_by_it():
    payload = {
        "data": [
            {
                "lane": "LANE-MUM-BIO-EU",
                "rate_usd_per_feu": 100.0,
                "previous_rate_usd_per_feu": 0.0,
            }
        ]
    }
    assert _freightos().normalise(payload, RETRIEVED) == []


# ---------------------------------------------------------------------------
# Snapshot / replay determinism
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_snapshot_round_trip_replays_byte_identically(tmp_path):
    store = SnapshotStore(tmp_path)
    payload = _fixture("gdelt_sample_response.json")
    written = store.write("gdelt", _gdelt().request_spec(), RETRIEVED, payload, ORIGIN_LIVE)
    replayed = store.write(
        "gdelt", _gdelt().request_spec(), RETRIEVED, payload, ORIGIN_LIVE
    )
    assert written.digest == replayed.digest
    assert len(list(tmp_path.glob("gdelt-*.json"))) == 1, "identical content must not pile up"
    first = _gdelt().normalise(written.response, written.retrieved_utc)
    second = _gdelt().normalise(replayed.response, replayed.retrieved_utc)
    assert [r.as_signal_kwargs() for r in first] == [r.as_signal_kwargs() for r in second]


@pytest.mark.unit
def test_snapshot_digest_is_derived_from_content_not_stored(tmp_path):
    """A hand-edited snapshot cannot keep claiming the digest it was written with."""
    store = SnapshotStore(tmp_path)
    payload = _fixture("freightos_sample_response.json")
    snapshot = store.write(
        "freightos", _freightos().request_spec(), RETRIEVED, payload, ORIGIN_FIXTURE
    )
    document = json.loads(snapshot.path.read_text(encoding="utf-8"))
    assert "digest" not in document
    document["response"]["data"][0]["rate_usd_per_feu"] = 1.0
    snapshot.path.write_text(json.dumps(document), encoding="utf-8")
    assert store.load(snapshot.path).digest != snapshot.digest


@pytest.mark.unit
def test_shipped_snapshots_parse_and_match_their_filename_digest():
    store = SnapshotStore(SNAPSHOTS)
    files = sorted(SNAPSHOTS.glob("*.json"))
    assert files, "the repository must ship a replayable snapshot per adapter"
    for path in files:
        snapshot = store.load(path)
        expected = path.stem.rsplit("-", 1)[-1]
        assert snapshot.digest == expected
        assert snapshot.origin == ORIGIN_FIXTURE
        assert snapshot.feed_id in feeds_pkg.ADAPTERS
    for feed_id in feeds_pkg.ADAPTERS:
        assert store.latest(feed_id) is not None, f"{feed_id} has no replayable snapshot"


@pytest.mark.unit
def test_replay_of_the_committed_snapshots_never_touches_the_network(monkeypatch):
    def explode(*_args, **_kwargs):
        raise AssertionError("a replay must not perform a network call")

    # The rate feed is credentialled; supply a value so all three feed adapters
    # are exercised. It is never sent anywhere on a replay.
    monkeypatch.setenv("FREIGHTOS_API_KEY", "replay-only-never-transmitted")
    monkeypatch.setattr(feeds_pkg.FeedAdapter, "_get_json", explode, raising=False)
    load = feeds_pkg.load_signals(
        config=feeds_config(),
        snapshot_dir=SNAPSHOTS,
        retrieved_utc=RETRIEVED,
        live=False,
        offline=True,
    )
    assert load.mode == "snapshot"
    assert len(load.records) == 10
    assert all(a["status"] == "loaded" for a in load.adapters)


@pytest.mark.unit
def test_live_fetch_is_refused_while_offline(monkeypatch):
    calls: list[str] = []

    def record(self, *_args, **_kwargs):
        calls.append(self.feed_id)
        return {}

    monkeypatch.setenv("FREIGHTOS_API_KEY", "present-but-unused")
    monkeypatch.setattr(feeds_pkg.FeedAdapter, "_get_json", record, raising=False)
    load = feeds_pkg.load_signals(
        config=feeds_config(),
        snapshot_dir=SNAPSHOTS,
        retrieved_utc=RETRIEVED,
        live=True,
        offline=True,
    )
    assert calls == [], "runtime.offline must win over feeds.live"
    assert load.records == []
    assert all(a["status"] == "skipped" for a in load.adapters)
    assert all("offline" in a["reason"] for a in load.adapters)


@pytest.mark.unit
def test_live_fetch_writes_a_snapshot_that_a_later_replay_can_use(tmp_path, monkeypatch):
    monkeypatch.setenv("FREIGHTOS_API_KEY", "present")
    payload = _fixture("freightos_sample_response.json")

    def fake_transport(self, params, timeout=10.0):
        # GDELT and Open-Meteo return nothing usable; Freightos returns a rate
        # sheet. That is enough to prove the write path, the normalisation and
        # the replay all agree.
        return {} if self.feed_id == "gdelt" else payload

    def fake_meteo_transport(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(feeds_pkg.FeedAdapter, "_get_json", fake_transport, raising=False)
    monkeypatch.setattr(OpenMeteoFeed, "_get_json", fake_meteo_transport, raising=False)
    live = feeds_pkg.load_signals(
        config=feeds_config(),
        snapshot_dir=tmp_path,
        retrieved_utc=RETRIEVED,
        live=True,
        offline=False,
    )
    assert live.mode == "live"
    freightos = next(a for a in live.adapters if a["feed_id"] == "freightos")
    assert freightos["status"] == "loaded"
    assert freightos["origin"] == ORIGIN_LIVE

    replay = feeds_pkg.load_signals(
        config=feeds_config(),
        snapshot_dir=tmp_path,
        retrieved_utc="2099-01-01T00:00:00Z",
        live=False,
        offline=True,
    )
    assert [r.as_signal_kwargs() for r in replay.records] == [
        r.as_signal_kwargs() for r in live.records
    ], "the replay must reproduce the fetch, including its recorded retrieval time"


@pytest.mark.unit
def test_a_missing_snapshot_is_reported_rather_than_invented(tmp_path, monkeypatch):
    monkeypatch.setenv("FREIGHTOS_API_KEY", "present")
    load = feeds_pkg.load_signals(
        config=feeds_config(),
        snapshot_dir=tmp_path,
        retrieved_utc=RETRIEVED,
        live=False,
        offline=True,
    )
    assert load.records == []
    assert {a["status"] for a in load.adapters} == {"no_snapshot"}
    assert all("no snapshot" in a["reason"] for a in load.adapters)


@pytest.mark.unit
def test_one_unavailable_source_does_not_stop_the_others(tmp_path, monkeypatch):
    def explode(self, params, timeout=10.0):
        if self.feed_id == "gdelt":
            raise FeedUnavailable("gdelt: URLError: name resolution failed")
        return {}

    monkeypatch.setattr(feeds_pkg.FeedAdapter, "_get_json", explode, raising=False)
    load = feeds_pkg.load_signals(
        config=feeds_config(),
        snapshot_dir=tmp_path,
        retrieved_utc=RETRIEVED,
        live=True,
        offline=False,
    )
    by_id = {a["feed_id"]: a for a in load.adapters}
    assert by_id["gdelt"]["status"] == "unavailable"
    assert "name resolution failed" in by_id["gdelt"]["reason"]
    # The others still ran; an empty payload is a valid payload, not a failure.
    assert by_id["open_meteo"]["status"] == "loaded"
    assert by_id["freightos"]["status"] == "skipped"


@pytest.mark.unit
def test_an_unreadable_snapshot_is_reported_not_silently_skipped(tmp_path):
    (tmp_path / "gdelt-deadbeef0000.json").write_text("{not json", encoding="utf-8")
    store = SnapshotStore(tmp_path)
    assert store.latest("gdelt") is None
    status = store.status()
    assert status["count"] == 1
    assert status["snapshots"][0]["error"] == "unreadable"


# ---------------------------------------------------------------------------
# End to end: the adapters satisfy RULE-2SOURCE with independent evidence
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_news_and_weather_independently_corroborate_the_same_closure(adapters_on):
    """The whole point of the adapters, in one assertion.

    GDELT reports what someone wrote; Open-Meteo reports what the atmosphere is
    doing. Neither is sufficient alone. Together they satisfy the two-source
    rule, and the resulting event is the same PORT_CLOSURE the scripted hero
    feed describes.
    """
    from backend.agents.pipeline import run_m1

    result = run_m1()
    closure = [e for e in result.events if e.event_type == "PORT_CLOSURE"]
    assert len(closure) == 1
    assert closure[0].target == "PORT-SUEZ"
    classes = closure[0].verification["fresh_source_classes"]
    assert classes == ["NEWS", "WEATHER"]
    assert closure[0].verification["corroborated"] is True


@pytest.mark.unit
def test_a_weather_anomaly_alone_is_rejected_by_verification(adapters_on):
    """One source class is not corroboration, however confident it is."""
    from backend.agents.pipeline import run_m1

    result = run_m1()
    rejected = [(r["event_type"], r["target"]) for r in result.rejected]
    assert ("WEATHER_ANOMALY", "PORT-JNPT") in rejected


@pytest.mark.unit
def test_an_unmatched_article_lands_on_the_watchlist_with_its_reason(adapters_on):
    watchlist = sense()["watchlist"]
    unmatched = next(w for w in watchlist if w["event_type"] == "UNCLASSIFIED")
    joined = " ".join(unmatched["reasons"])
    assert "not a known network entity" in joined


@pytest.mark.unit
def test_feed_provenance_names_every_configured_source(adapters_on):
    provenance = sense()["feed"]
    assert provenance["sources_configured"] == 3
    statuses = {a["feed_id"]: a["status"] for a in provenance["adapters"]}
    assert statuses["gdelt"] == "loaded"
    assert statuses["open_meteo"] == "loaded"
    # No credential in the test environment, so the rate feed is honestly absent.
    assert statuses["freightos"] == "skipped"


@pytest.mark.unit
def test_repo_root_is_importable_so_these_fixtures_are_where_we_think():
    assert (REPO_ROOT / "config" / "feeds.yaml").is_file()
