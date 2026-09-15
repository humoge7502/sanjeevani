"""Cold-chain telemetry analysis (Member 1).

The device trace is a *seeded CSV*, not a live feed. Analysis is pure arithmetic
over those samples, so the same CSV always yields the same excursion metrics --
which is what lets the audit ledger be reproducible.

Integration note: the emitted JSON shape mirrors what a Tive/Sensitech-class
device would report, so swapping in a real feed changes the loader, not the
model.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from backend.config import DATA_DIR, settings

TELEMETRY_CSV = DATA_DIR / "telemetry" / "cold_chain.csv"


@dataclass(frozen=True)
class TelemetrySample:
    shipment_id: str
    t_offset_minutes: float
    recorded_utc: str
    temp_c: float
    humidity_pct: float
    device_id: str


@dataclass(frozen=True)
class ExcursionAnalysis:
    """Deterministic verdict on a device trace."""

    shipment_id: str
    device_id: str
    sample_count: int
    peak_temp_c: float
    min_temp_c: float
    first_sample_utc: str
    last_sample_utc: str
    limit_max_c: float
    peak_excess_c: float
    minutes_above_limit: float
    degree_minutes: float
    samples_above_limit: int
    breached: bool
    condemn_ratio: float
    method: dict[str, Any] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _load_samples() -> dict[str, list[TelemetrySample]]:
    by_shipment: dict[str, list[TelemetrySample]] = {}
    with TELEMETRY_CSV.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sample = TelemetrySample(
                shipment_id=row["shipment_id"],
                t_offset_minutes=float(row["t_offset_minutes"]),
                recorded_utc=row["recorded_utc"],
                temp_c=float(row["temp_c"]),
                humidity_pct=float(row["humidity_pct"]),
                device_id=row["device_id"],
            )
            by_shipment.setdefault(sample.shipment_id, []).append(sample)
    for samples in by_shipment.values():
        samples.sort(key=lambda s: s.t_offset_minutes)
    return by_shipment


def samples_for(shipment_id: str) -> list[TelemetrySample]:
    return list(_load_samples().get(shipment_id, []))


def _minutes_above_limit(samples: list[TelemetrySample], limit: float) -> float:
    """Linear-interpolated duration above the limit.

    Interpolates crossing times between samples rather than counting samples, so
    the figure does not depend on the sampling cadence.
    """
    total = 0.0
    for a, b in zip(samples, samples[1:]):
        ea, eb = a.temp_c - limit, b.temp_c - limit
        dt = b.t_offset_minutes - a.t_offset_minutes
        if ea <= 0 and eb <= 0:
            continue
        if ea > 0 and eb > 0:
            total += dt
            continue
        # One crossing inside the interval: the above-limit span is the tail or head.
        frac = abs(ea) / (abs(ea) + abs(eb)) if (abs(ea) + abs(eb)) else 0.0
        total += dt * (frac if ea <= 0 else 1 - frac)
    return round(total, 2)


def _degree_minutes(samples: list[TelemetrySample], limit: float) -> float:
    """Trapezoidal integral of the positive excess over the limit."""
    total = 0.0
    for a, b in zip(samples, samples[1:]):
        ea = max(a.temp_c - limit, 0.0)
        eb = max(b.temp_c - limit, 0.0)
        dt = b.t_offset_minutes - a.t_offset_minutes
        total += (ea + eb) / 2.0 * dt
    return round(total, 2)


def condemn_ratio(
    peak_excess_c: float, degree_minutes: float, minutes_above: float
) -> tuple[float, dict[str, Any]]:
    """Illustrative GDP-style condemnation model.

    This is a CONFIGURED MODEL, not a regulatory rule. It is deliberately simple
    and monotone so a domain expert can audit and replace it:

      * inside the tolerance band (small excess AND brief excursion) -> 0
      * beyond the full-condemn band (large excess OR long cumulative load) -> 1
      * otherwise -> the mean of the two normalised severities

    Returns (ratio, method) so the UI can show exactly how the number arose.
    """
    cc = settings()["impact"]["cold_chain"]
    tol_peak = cc["tolerable_peak_excess_c"]
    tol_min = cc["tolerable_minutes"]
    full_peak = cc["full_condemn_peak_excess_c"]
    full_deg_hours = cc["full_condemn_degree_hours"]
    degree_hours = degree_minutes / 60.0

    method: dict[str, Any] = {
        "model": "configured_cold_chain_condemnation",
        "labelled_as": "illustrative_check",
        "inputs": {
            "peak_excess_c": peak_excess_c,
            "degree_hours": round(degree_hours, 4),
            "minutes_above_limit": minutes_above,
        },
        "thresholds": {
            "tolerable_peak_excess_c": tol_peak,
            "tolerable_minutes": tol_min,
            "full_condemn_peak_excess_c": full_peak,
            "full_condemn_degree_hours": full_deg_hours,
        },
    }

    if peak_excess_c <= tol_peak and minutes_above <= tol_min:
        method["band"] = "tolerable"
        method["ratio_formula"] = "0 (within tolerance)"
        return 0.0, method

    if peak_excess_c >= full_peak or degree_hours >= full_deg_hours:
        method["band"] = "full_condemn"
        method["ratio_formula"] = "1 (beyond full-condemn threshold)"
        return 1.0, method

    peak_sev = peak_excess_c / full_peak
    load_sev = degree_hours / full_deg_hours
    ratio = round((peak_sev + load_sev) / 2.0, 6)
    method["band"] = "proportional"
    method["ratio_formula"] = (
        f"mean(peak_excess/{full_peak}, degree_hours/{full_deg_hours}) "
        f"= mean({round(peak_sev, 4)}, {round(load_sev, 4)})"
    )
    return ratio, method


def analyse(shipment_id: str, limit_max_c: float) -> ExcursionAnalysis | None:
    """Analyse the seeded trace for one consignment. None if no trace exists."""
    samples = _load_samples().get(shipment_id)
    if not samples:
        return None
    temps = [s.temp_c for s in samples]
    peak = max(temps)
    peak_excess = round(max(0.0, peak - limit_max_c), 4)
    minutes_above = _minutes_above_limit(samples, limit_max_c)
    degree_min = _degree_minutes(samples, limit_max_c)
    above = sum(1 for s in samples if s.temp_c > limit_max_c)
    ratio, method = condemn_ratio(peak_excess, degree_min, minutes_above)

    return ExcursionAnalysis(
        shipment_id=shipment_id,
        device_id=samples[0].device_id,
        sample_count=len(samples),
        peak_temp_c=round(peak, 2),
        min_temp_c=round(min(temps), 2),
        first_sample_utc=samples[0].recorded_utc,
        last_sample_utc=samples[-1].recorded_utc,
        limit_max_c=limit_max_c,
        peak_excess_c=peak_excess,
        minutes_above_limit=minutes_above,
        degree_minutes=degree_min,
        samples_above_limit=above,
        breached=above > 0,
        condemn_ratio=ratio,
        method=method,
    )


def as_dict(a: ExcursionAnalysis) -> dict[str, Any]:
    return {
        "shipment_id": a.shipment_id,
        "device_id": a.device_id,
        "sample_count": a.sample_count,
        "peak_temp_c": a.peak_temp_c,
        "min_temp_c": a.min_temp_c,
        "first_sample_utc": a.first_sample_utc,
        "last_sample_utc": a.last_sample_utc,
        "limit_max_c": a.limit_max_c,
        "peak_excess_c": a.peak_excess_c,
        "minutes_above_limit": a.minutes_above_limit,
        "degree_minutes": a.degree_minutes,
        "samples_above_limit": a.samples_above_limit,
        "breached": a.breached,
        "condemn_ratio": a.condemn_ratio,
        "method": a.method,
    }


def reset_cache() -> None:
    """Clear the telemetry cache (demo reset / tests)."""
    _load_samples.cache_clear()
