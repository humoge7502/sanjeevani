"""Open-Meteo forecast adapter -- the independent physical source.

Two things make this adapter worth having rather than decorative:

1. It is a **different kind of evidence**. GDELT reports what someone wrote; a
   forecast reports what the atmosphere is doing. RULE-2SOURCE exists precisely
   so that those two can corroborate each other, and this adapter is what gives
   the rule something real to work with.

2. Its numbers come out of the payload. Peak temperature and peak wind speed are
   read from the hourly series the service returned; only the *thresholds* and
   the resulting claim come from ``config/feeds.yaml``.

Aggregation is deterministic: max and min over the returned series, no sampling,
no interpolation, no smoothing. The same payload produces the same signals, so a
replayed snapshot is byte-identical to the fetch that produced it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.agents.feeds.base import (
    DEFAULT_TIMEOUT_SECONDS,
    FeedAdapter,
    SignalRecord,
    collapse,
    short_digest,
)


def canonical_hour(value: Any) -> str:
    """Open-Meteo returns ``YYYY-MM-DDTHH:MM``. Every timestamp here carries Z.

    A naive timestamp reaching ``RawSignal.age_hours`` is a crash, and more
    importantly it is a lie: the series is UTC and the contract says so.
    """
    text = collapse(str(value))
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    return parsed.replace(tzinfo=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def series_stats(response: Any) -> dict[str, Any] | None:
    """Max and min over a Open-Meteo ``hourly`` block. None if there is no block."""
    if not isinstance(response, dict):
        return None
    hourly = response.get("hourly")
    if not isinstance(hourly, dict):
        return None
    times = hourly.get("time")
    if not isinstance(times, list) or not times:
        return None

    def numeric(key: str) -> list[float]:
        values = hourly.get(key)
        if not isinstance(values, list):
            return []
        return [float(v) for v in values if isinstance(v, (int, float))]

    temps = numeric("temperature_2m")
    winds = numeric("wind_speed_10m")
    return {
        "hours": len(times),
        "first_hour_utc": canonical_hour(times[0]),
        "last_hour_utc": canonical_hour(times[-1]),
        "peak_temp_c": max(temps) if temps else None,
        "min_temp_c": min(temps) if temps else None,
        "peak_wind_kmh": max(winds) if winds else None,
        "samples": len(temps),
    }


class OpenMeteoFeed(FeedAdapter):
    feed_id = "open_meteo"
    source_class = "WEATHER"

    # -- request --------------------------------------------------------

    def sites(self) -> list[dict[str, Any]]:
        return [s for s in (self.config.get("sites") or []) if isinstance(s, dict)]

    def query_params(self) -> dict[str, Any]:
        """The parameters shared by every site; latitude/longitude are added per site."""
        return dict(self.config.get("query") or {})

    def _site_params(self, site: dict[str, Any]) -> dict[str, Any]:
        return {
            **self.query_params(),
            "latitude": site.get("latitude"),
            "longitude": site.get("longitude"),
        }

    def request_spec(self) -> dict[str, Any]:
        return {
            "feed_id": self.feed_id,
            "endpoint": self.endpoint,
            "requests": [
                {"node_id": s.get("node_id"), "params": self._site_params(s)}
                for s in self.sites()
            ],
        }

    def fetch(self, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
        """One call per monitored site, assembled under the node id.

        The assembled shape is what gets snapshotted, so a replay performs the
        same normalisation over the same per-site payloads.
        """
        sites: dict[str, Any] = {}
        for site in self.sites():
            node_id = str(site.get("node_id") or "")
            if not node_id:
                continue
            sites[node_id] = self._get_json(self._site_params(site), timeout=timeout)
        return {"sites": sites}

    # -- mapping --------------------------------------------------------

    def _closure_claim(self, node_id: str, stats: dict[str, Any]) -> dict[str, Any]:
        claim = dict((self.config.get("closure") or {}).get("claim") or {})
        claim["target"] = node_id
        claim["peak_wind_kmh"] = stats["peak_wind_kmh"]
        claim["observed_at_utc"] = stats["last_hour_utc"]
        return claim

    def normalise(self, payload: Any, retrieved_utc: str) -> list[SignalRecord]:
        sites = (payload or {}).get("sites") if isinstance(payload, dict) else None
        if not isinstance(sites, dict):
            return []

        closure_cfg = self.config.get("closure") or {}
        anomaly_cfg = self.config.get("anomaly") or {}
        wind_ceiling = float(closure_cfg.get("wind_speed_kmh", 60.0))
        cold_floor = float(anomaly_cfg.get("peak_temp_c_min", 2.0))
        heat_ceiling = float(anomaly_cfg.get("peak_temp_c_max", 30.0))
        source_reliability = float(self.config.get("source_reliability", 0.90))
        source_id = str(self.config.get("source_id") or "open-meteo:forecast")

        records: list[SignalRecord] = []
        for node_id in sorted(sites):
            stats = series_stats(sites[node_id])
            if stats is None:
                continue

            wind = stats["peak_wind_kmh"]
            if wind is not None and wind >= wind_ceiling:
                records.append(
                    self._record(
                        node_id=node_id,
                        kind="CLOSURE",
                        stats=stats,
                        claim=self._closure_claim(node_id, stats),
                        headline=(
                            f"Forecast wind {wind:.0f} km/h at {node_id} exceeds the "
                            f"{wind_ceiling:.0f} km/h operability limit"
                        ),
                        source_id=source_id,
                        source_reliability=source_reliability,
                        retrieved_utc=retrieved_utc,
                    )
                )
                continue

            peak = stats["peak_temp_c"]
            low = stats["min_temp_c"]
            out_of_band = peak is not None and (peak > heat_ceiling or (low or 0.0) < cold_floor)
            if out_of_band:
                claim = dict(anomaly_cfg.get("claim") or {})
                claim.update(
                    {
                        "target": node_id,
                        "peak_temp_c": peak,
                        "min_temp_c": low,
                        "band_c": [cold_floor, heat_ceiling],
                        "observed_at_utc": stats["last_hour_utc"],
                    }
                )
                records.append(
                    self._record(
                        node_id=node_id,
                        kind="ANOMALY",
                        stats=stats,
                        claim=claim,
                        headline=(
                            f"Forecast at {node_id} leaves the "
                            f"{cold_floor:g}-{heat_ceiling:g}C band (peak {peak:g}C)"
                        ),
                        source_id=source_id,
                        source_reliability=source_reliability,
                        retrieved_utc=retrieved_utc,
                    )
                )
        return records

    def _record(
        self,
        *,
        node_id: str,
        kind: str,
        stats: dict[str, Any],
        claim: dict[str, Any],
        headline: str,
        source_id: str,
        source_reliability: float,
        retrieved_utc: str,
    ) -> SignalRecord:
        observed = str(stats.get("last_hour_utc") or retrieved_utc)
        return SignalRecord(
            signal_id=f"METEO-{kind}-{node_id}-{short_digest(node_id, kind, observed).upper()}",
            source_class=self.source_class,
            source_id=source_id,
            source_reliability=source_reliability,
            # A forecast has no article; the provenance is the site it is about.
            source_url=f"https://api.open-meteo.com/v1/forecast?latitude&longitude#{node_id}",
            published_utc=observed,
            retrieved_utc=retrieved_utc,
            headline=collapse(headline),
            body=collapse(
                f"Open-Meteo hourly series for {node_id}: {stats['samples']} samples over "
                f"{stats['hours']} hours from {stats['first_hour_utc']} to "
                f"{stats['last_hour_utc']}."
            ),
            entities=(node_id,),
            claim=claim,
        )
