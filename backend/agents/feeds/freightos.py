"""Freightos rate adapter -- the cost-of-recovery source.

Why a rate feed belongs in A1 rather than in finance: the cost of a recovery
action is an input to the M2 objective, and a lane whose cost has moved is a lane
whose recovery economics have moved. Making that visible as a signal means the
planner sees "this corridor got 37% more expensive this week" before choosing it.

Two deliberate limits:

* **Credentialled.** With no ``FREIGHTOS_API_KEY`` the adapter reports
  ``unavailable`` and contributes nothing. It does not fall back to a seeded rate,
  because a plausible-looking invented freight rate is exactly the kind of number
  this project refuses to produce.
* **Thresholded.** A move below ``rate_change_threshold_pct`` is noise and
  produces no signal at all. The threshold and the severity bands are configured.
"""

from __future__ import annotations

from typing import Any

from backend.agents.feeds.base import FeedAdapter, SignalRecord, collapse, short_digest

REQUIRED_FIELDS = ("lane", "rate_usd_per_feu", "previous_rate_usd_per_feu")


class FreightosRateFeed(FeedAdapter):
    feed_id = "freightos"
    source_class = "CARRIER"

    def query_params(self) -> dict[str, Any]:
        return dict(self.config.get("query") or {})

    def normalise(self, payload: Any, retrieved_utc: str) -> list[SignalRecord]:
        rows = (payload or {}).get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []

        threshold = float(self.config.get("rate_change_threshold_pct", 15.0))
        source_id = str(self.config.get("source_id") or "freightos:fbx")
        source_reliability = float(self.config.get("source_reliability", 0.85))
        claim_template = dict(self.config.get("claim") or {})
        base_url = self.endpoint

        records: list[SignalRecord] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            if any(row.get(f) is None for f in REQUIRED_FIELDS):
                continue
            lane = str(row["lane"])
            rate = float(row["rate_usd_per_feu"])
            previous = float(row["previous_rate_usd_per_feu"])
            if previous <= 0:
                continue
            change_pct = round((rate - previous) / previous * 100.0, 4)
            if abs(change_pct) < threshold:
                continue

            direction = "up" if change_pct > 0 else "down"
            currency = str(row.get("currency") or "USD")
            claim = {
                **claim_template,
                "target": lane,
                "rate_usd_per_feu": rate,
                "previous_rate_usd_per_feu": previous,
                "rate_change_pct": change_pct,
                "currency": currency,
            }
            records.append(
                SignalRecord(
                    signal_id=f"FBX-{lane}-{short_digest(lane, rate, previous).upper()}",
                    source_class=self.source_class,
                    source_id=source_id,
                    source_reliability=source_reliability,
                    source_url=f"{base_url}#{lane}" if base_url else f"freightos://{lane}",
                    published_utc=retrieved_utc,
                    retrieved_utc=retrieved_utc,
                    headline=collapse(
                        f"Freight rate {direction} {abs(change_pct):.1f}% on {lane}: "
                        f"{currency} {previous:,.0f} -> {currency} {rate:,.0f} per FEU"
                    ),
                    body=collapse(
                        f"Freightos-reported 40HC reference rate for {lane} moved from "
                        f"{previous:,.2f} to {rate:,.2f} {currency} per FEU, a change of "
                        f"{change_pct:+.2f}%. Threshold for reporting is "
                        f"{threshold:g}%."
                    ),
                    entities=(lane,),
                    claim=claim,
                )
            )
        records.sort(key=lambda r: (-abs(float(r.claim["rate_change_pct"])), r.signal_id))
        return records
