"""GDELT DOC 2.0 API adapter -- global news, the widest-net source class.

GDELT's article list gives a URL, a title, a domain and a timestamp. That is
all. It does **not** give a structured claim, so the claim attached here comes
from the keyword rulebook in ``config/feeds.yaml``: a configured constant per
matched rule, never a number derived from the headline.

An article that matches no rule still becomes a signal. It is corroboration for
whatever else the other source classes are saying, and because its claim is
``UNCLASSIFIED``/``UNKNOWN`` A1 watchlists it rather than escalating it. That is
the honest outcome: the feed told us an article exists, not that a port closed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.agents.feeds.base import FeedAdapter, SignalRecord, collapse, short_digest


def parse_seendate(value: str) -> str | None:
    """GDELT's ``YYYYMMDDTHHMMSSZ`` -> canonical ``YYYY-MM-DDTHH:MM:SSZ``."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


class GdeltNewsFeed(FeedAdapter):
    feed_id = "gdelt"
    source_class = "NEWS"

    def query_params(self) -> dict[str, Any]:
        return dict(self.config.get("query") or {})

    # -- rulebook -------------------------------------------------------

    def _reliability(self, domain: str) -> float:
        table = self.config.get("domain_reliability") or {}
        default = float(self.config.get("default_reliability", 0.60))
        return float(table.get(domain, default))

    def _match_rule(self, title: str) -> dict[str, Any] | None:
        lowered = title.lower()
        for rule in self.config.get("rules") or []:
            keywords = [str(k).lower() for k in (rule.get("any_keywords") or [])]
            if keywords and any(k in lowered for k in keywords):
                return rule
        return None

    # -- mapping --------------------------------------------------------

    def normalise(self, payload: Any, retrieved_utc: str) -> list[SignalRecord]:
        articles = (payload or {}).get("articles") if isinstance(payload, dict) else None
        if not isinstance(articles, list):
            return []

        records: list[SignalRecord] = []
        for article in articles:
            if not isinstance(article, dict):
                continue
            url = str(article.get("url") or "")
            title = collapse(article.get("title") or "")
            if not url or not title:
                continue
            domain = str(article.get("domain") or "").lower()
            published = parse_seendate(str(article.get("seendate") or "")) or retrieved_utc
            rule = self._match_rule(title)
            if rule is None:
                claim: dict[str, Any] = {"event_type": "UNCLASSIFIED", "target": "UNKNOWN"}
                entities: tuple[str, ...] = ()
                rule_id = "UNMATCHED"
            else:
                claim = dict(rule.get("claim") or {})
                entities = tuple(rule.get("entities") or ())
                rule_id = str(rule.get("id") or "RULE")

            records.append(
                SignalRecord(
                    # Content-addressed: the same article always gets the same id,
                    # in this run and in any replay.
                    signal_id=f"GDELT-{rule_id}-{short_digest(url).upper()}",
                    source_class=self.source_class,
                    source_id=domain or "gdelt-unknown-domain",
                    source_reliability=self._reliability(domain),
                    source_url=url,
                    published_utc=published,
                    retrieved_utc=retrieved_utc,
                    headline=title,
                    # The article-list mode carries no body. Stated rather than
                    # faked: an empty body is the truth about what was retrieved.
                    body=title,
                    entities=entities,
                    claim=claim,
                )
            )
        records.sort(key=lambda r: (r.published_utc, r.signal_id), reverse=True)
        return records
