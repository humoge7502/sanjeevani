"""Metrics: the numbers an operator needs, exposed in Prometheus format.

A governance product that cannot answer "how many approvals were refused in the
last hour, and what is the p95 latency of a state read?" is not operable. These
are the counters that make the difference between "it works on my machine" and
"it is running".

A dedicated ``CollectorRegistry`` is used rather than the library default. The
default registry is process-global and raises on duplicate registration, which
makes it a landmine in a test suite that imports modules more than once.
"""

from __future__ import annotations

from typing import Any, Final

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

REGISTRY: Final[CollectorRegistry] = CollectorRegistry()

#: Requests, by route template (not raw path -- a raw path would explode into one
#: series per plan id and blow up cardinality).
HTTP_REQUESTS: Final[Counter] = Counter(
    "sanjeevani_http_requests_total",
    "HTTP requests handled, by method, route template and status code.",
    labelnames=("method", "route", "status"),
    registry=REGISTRY,
)

HTTP_DURATION: Final[Histogram] = Histogram(
    "sanjeevani_http_request_duration_seconds",
    "HTTP request latency in seconds.",
    labelnames=("method", "route"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=REGISTRY,
)

#: Governance outcomes. This is the product's heartbeat: how often authority was
#: granted, refused, or never requested because compliance blocked.
GOVERNANCE_DECISIONS: Final[Counter] = Counter(
    "sanjeevani_governance_decisions_total",
    "Governance outcomes, by decision and result.",
    labelnames=("decision", "result"),
    registry=REGISTRY,
)

AUTH_FAILURES: Final[Counter] = Counter(
    "sanjeevani_auth_failures_total",
    "Authentication and authorization refusals, by reason code.",
    labelnames=("reason",),
    registry=REGISTRY,
)

RATE_LIMIT_REJECTIONS: Final[Counter] = Counter(
    "sanjeevani_rate_limit_rejections_total",
    "Requests refused by the rate limiter, by scope.",
    labelnames=("scope",),
    registry=REGISTRY,
)

LEDGER_RECORDS: Final[Gauge] = Gauge(
    "sanjeevani_ledger_records",
    "Records currently in the audit ledger.",
    registry=REGISTRY,
)

LEDGER_CHAIN_INTACT: Final[Gauge] = Gauge(
    "sanjeevani_ledger_chain_intact",
    "1 when the audit hash chain verifies intact, 0 otherwise.",
    registry=REGISTRY,
)

M2_SOLVE_SECONDS: Final[Histogram] = Histogram(
    "sanjeevani_m2_solve_seconds",
    "Time to produce a recovery plan from M2.",
    labelnames=("provider",),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)

EXECUTION_ACTIONS: Final[Counter] = Counter(
    "sanjeevani_execution_actions_total",
    "SAP-shaped execution outcomes, by system and status.",
    labelnames=("system", "status"),
    registry=REGISTRY,
)


def record_governance(decision: str, result: str) -> None:
    GOVERNANCE_DECISIONS.labels(decision=decision, result=result).inc()


def record_auth_failure(reason: str) -> None:
    AUTH_FAILURES.labels(reason=reason).inc()


def record_rate_limit(scope: str) -> None:
    RATE_LIMIT_REJECTIONS.labels(scope=scope).inc()


def record_execution(system: str, status: str) -> None:
    EXECUTION_ACTIONS.labels(system=system, status=status).inc()


def refresh_ledger_gauges(ledger: Any) -> None:
    """Publish the two ledger gauges. Cheap, and refreshed on demand."""
    LEDGER_RECORDS.set(len(ledger))
    LEDGER_CHAIN_INTACT.set(1 if ledger.verify_chain()["intact"] else 0)


def render() -> tuple[bytes, str]:
    """The exposition payload and its content type."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST


def reset_runtime_metrics() -> None:
    """Clear counters between tests, so assertions are about this test only.

    Gauges are intentionally left alone: they describe current state, not history.
    """
    for metric in (
        HTTP_REQUESTS,
        HTTP_DURATION,
        GOVERNANCE_DECISIONS,
        AUTH_FAILURES,
        RATE_LIMIT_REJECTIONS,
        M2_SOLVE_SECONDS,
        EXECUTION_ACTIONS,
    ):
        metric.clear()
