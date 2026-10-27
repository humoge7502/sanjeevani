"""ASGI middleware: observability, throttling and request-shape limits.

Three concerns, one module, because all three wrap the same request/response cycle
and each needs the same two facts -- the resolved route template and the time the
request took. Splitting them into three middlewares would mean reading both three
times.

Order matters and is stated here rather than left to whoever writes the
``add_middleware`` calls: body limit (cheapest, and must run before anything
parses a body) -> rate limit -> observation (must be outermost of the three so it
still records a 429).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from backend.observability.logging import (
    configure_logging,
    request_id_var,
    sanitise_request_id,
)
from backend.observability.metrics import (
    HTTP_DURATION,
    HTTP_REQUESTS,
    record_rate_limit,
)
from backend.security.ratelimit import TokenBucketLimiter

logger = logging.getLogger("sanjeevani.access")

#: Every constructed limiter middleware, in one place.
#:
#: This is a deliberate testing and operations seam. `add_middleware` gives no
#: handle back, so without it a test that exercises the limiter would leave the
#: budget spent for every later test, and an operator could not clear a bucket
#: without restarting. It is a list of weak references rather than a registry of
#: state: the limiters remain the only owners of their buckets.
_LIMITER_MIDDLEWARES: list[RateLimitMiddleware] = []


def reset_all_limiters() -> None:
    """Clear every limiter's buckets. Used by the demo reset and by tests."""
    for middleware in _LIMITER_MIDDLEWARES:
        middleware.read_limiter.reset()
        middleware.write_limiter.reset()


def limiter_state() -> list[dict[str, Any]]:
    """A read-only view of the limiter state, for diagnostics."""
    return [middleware.snapshot() for middleware in _LIMITER_MIDDLEWARES]


def _route_template(scope: dict[str, Any]) -> str:
    """The route *template*, never the raw path.

    ``/api/plans/{plan_id}/approve`` is one metric series. Using the raw path
    would create one series per plan id, and a metric label an attacker controls
    is a memory leak with extra steps.
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return scope.get("path", "unknown")


def _client_key(scope: dict[str, Any]) -> str:
    client = scope.get("client")
    if isinstance(client, (tuple, list)) and client:
        return str(client[0])
    return "unknown"


class ObservationMiddleware:
    """Correlate, time, count and log every HTTP request."""

    def __init__(self, app: Any) -> None:
        self.app = app
        configure_logging()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        request_id = sanitise_request_id(headers.get("x-request-id"))
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_holder = {"status": 500}

        async def send_observed(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message["status"])
                raw = list(message.get("headers", []))
                raw.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": raw}
            await send(message)

        try:
            await self.app(scope, receive, send_observed)
        finally:
            duration = time.perf_counter() - started
            route = _route_template(scope)
            method = scope.get("method", "GET")
            status = status_holder["status"]
            HTTP_REQUESTS.labels(method=method, route=route, status=str(status)).inc()
            HTTP_DURATION.labels(method=method, route=route).observe(duration)
            # One line per request. 4xx/5xx are logged at warning so a filter can
            # find refusals without a regex over successful traffic.
            log = logger.warning if status >= 400 else logger.info
            log(
                "request",
                extra={
                    "fields": {
                        "method": method,
                        "route": route,
                        "status": status,
                        "duration_ms": round(duration * 1000.0, 3),
                    }
                },
            )
            request_id_var.reset(token)


class BodySizeLimitMiddleware:
    """Refuse oversized request bodies before anything parses them.

    Checks ``Content-Length`` first, then counts actual bytes as a backstop for a
    chunked body that declares nothing. Without this, a local deterministic API
    with a 64-byte legitimate payload will happily buffer a multi-megabyte one.
    """

    def __init__(self, app: Any, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = int(max_bytes)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = 0
        for key, value in scope.get("headers", []):
            if key.decode("latin-1").lower() == "content-length":
                try:
                    declared = int(value.decode("latin-1"))
                except ValueError:
                    declared = 0
                break
        if declared > self.max_bytes:
            await self._reject(send, declared)
            return

        received = 0
        too_large = False

        async def counted_receive() -> dict[str, Any]:
            nonlocal received, too_large
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    too_large = True
            return message

        async def guarded_send(message: dict[str, Any]) -> None:
            if too_large and message["type"] == "http.response.start":
                message = {**message, "status": 413}
            await send(message)

        await self.app(scope, counted_receive, guarded_send)

    async def _reject(self, send: Any, declared: int) -> None:
        body = json.dumps(
            {
                "error": "REQUEST_TOO_LARGE",
                "message": (
                    f"Request body of {declared} bytes exceeds the {self.max_bytes}-byte limit."
                ),
                "detail": {"max_bytes": self.max_bytes, "declared_bytes": declared},
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class RateLimitMiddleware:
    """Token-bucket throttling, split into a read scope and a state-changing scope.

    Two buckets rather than one, because the two have genuinely different budgets:
    the UI polls read endpoints constantly, and a state-changing call is a
    governance act that should be rare. One shared bucket would either throttle
    the UI's own polling or fail to throttle an approval flood.

    Keyed by client address, which is the honest limit of an in-process limiter
    without a directory lookup. Behind a proxy every client shares an address;
    that is documented rather than papered over with a trust-the-header shortcut.
    """

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool,
        read_rate_per_second: float,
        read_burst: int,
        write_rate_per_second: float,
        write_burst: int,
        exempt_paths: tuple[str, ...] = ("/api/health", "/metrics"),
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.read_limiter = TokenBucketLimiter(rate_per_second=read_rate_per_second, burst=read_burst)
        self.write_limiter = TokenBucketLimiter(
            rate_per_second=write_rate_per_second, burst=write_burst
        )
        self.exempt = tuple(exempt_paths)
        _LIMITER_MIDDLEWARES.append(self)

    def snapshot(self) -> dict[str, Any]:
        """Diagnostic view: the limits and how many clients are tracked."""
        return {
            "enabled": self.enabled,
            "exempt_paths": list(self.exempt),
            "read": self.read_limiter.snapshot(),
            "write": self.write_limiter.snapshot(),
        }

    def _scope_for(self, scope: dict[str, Any]) -> str | None:
        path = scope.get("path", "")
        if path in self.exempt:
            return None
        method = scope.get("method", "GET")
        return "write" if method in {"POST", "PUT", "PATCH", "DELETE"} else "read"

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        kind = self._scope_for(scope)
        if kind is None:
            await self.app(scope, receive, send)
            return

        limiter = self.write_limiter if kind == "write" else self.read_limiter
        decision = limiter.check(_client_key(scope))
        if decision.allowed:
            await self.app(scope, receive, send)
            return

        record_rate_limit(kind)
        body = json.dumps(
            {
                "error": "RATE_LIMITED",
                "message": (
                    f"Too many {kind} requests. Retry in "
                    f"{decision.retry_after_seconds:.2f}s."
                ),
                "detail": decision.as_dict(),
                "governance": {
                    "enforced_by": "per-process token bucket",
                    "note": "The limiter is per-process; see docs/deployment.md.",
                },
            }
        ).encode("utf-8")
        retry_after = max(1, int(decision.retry_after_seconds + 0.999))
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                    (b"retry-after", str(retry_after).encode("latin-1")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
