"""In-process token-bucket rate limiting.

WHAT IT IS FOR. Two concrete risks in this product:

  1. **Governance probing.** ``POST /api/plans/{id}/approve`` is where authority
     is asserted. Without throttling, an attacker (or a buggy retry loop) can
     hammer it; with ``demo/reset`` in the same unthrottled namespace they can
     also wipe the ledger repeatedly.
  2. **Demo-destroying accidents.** A presenter leaning on a key, or a client
     retrying a 409 in a tight loop, is a realistic way to lose a rehearsal.

WHAT IT IS NOT. It is **not** a distributed limiter. State is per-process, so two
replicas each get their own budget. That is stated rather than hidden: a shared
limiter needs Redis or the database, and this build is deliberately a single
process (see docs/deployment.md). The limiter is still the right control *here*,
because the process is the deployment.

ALGORITHM. A token bucket, refilled continuously at ``rate_per_second`` up to
``burst``. It is O(1) per request, needs one float and one timestamp per key, and
does not have the fixed-window boundary problem where a caller gets 2x the budget
by straddling the window edge.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Final

#: Buckets are evicted after this long without use, so a long-running process
#: cannot accumulate unbounded state from one-off clients.
IDLE_EVICTION_SECONDS: Final[float] = 900.0


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_seconds: float
    limit: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "remaining": self.remaining,
            "retry_after_seconds": round(self.retry_after_seconds, 3),
            "limit": self.limit,
        }


class TokenBucketLimiter:
    """Per-key token buckets with continuous refill."""

    def __init__(
        self,
        *,
        rate_per_second: float,
        burst: int,
        clock: Any = time.monotonic,
    ) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be > 0")
        if burst < 1:
            raise ValueError("burst must be >= 1")
        self.rate = float(rate_per_second)
        self.burst = int(burst)
        self._clock = clock
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def check(self, key: str, *, cost: float = 1.0) -> RateLimitDecision:
        """Consume ``cost`` tokens for ``key`` and report the decision."""
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(self.burst), updated_at=now)
                self._buckets[key] = bucket
            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(float(self.burst), bucket.tokens + elapsed * self.rate)
            bucket.updated_at = now

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                allowed = True
                retry_after = 0.0
            else:
                allowed = False
                deficit = cost - bucket.tokens
                retry_after = deficit / self.rate
            remaining = int(bucket.tokens)
            self._evict(now)
            return RateLimitDecision(
                allowed=allowed,
                remaining=max(0, remaining),
                retry_after_seconds=retry_after,
                limit=self.burst,
            )

    def _evict(self, now: float) -> None:
        if len(self._buckets) < 128:
            return
        stale = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.updated_at > IDLE_EVICTION_SECONDS
        ]
        for key in stale:
            del self._buckets[key]

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "keys": len(self._buckets),
                "rate_per_second": self.rate,
                "burst": self.burst,
                "scope": "per-process (single replica by design)",
            }
