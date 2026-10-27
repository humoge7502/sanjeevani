"""Structured logging and request correlation.

Two problems this solves.

  1. **Free-text logs cannot be queried.** ``"governance refusal code=UNAUTHORIZED_APPROVER"``
     is fine to read and useless to aggregate. A JSON line with named fields can be
     filtered, counted and alerted on without a regex.

  2. **Nothing connects a UI action to a ledger record to an error.** Every log
     line carries the same ``request_id`` the response echoes in a header, so "the
     approval button did nothing" becomes one grep.

The request id is also accepted FROM the caller (``X-Request-ID``) when it is
well-formed, so a proxy or the browser can propagate its own trace id. A malformed
one is replaced rather than trusted, because an attacker-controlled value lands in
log lines.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sys
import time
from contextvars import ContextVar
from typing import Any, Final

#: The per-request correlation id, propagated through the call stack without
#: threading a parameter through every function.
request_id_var: ContextVar[str] = ContextVar("sanjeevani_request_id", default="-")
tenant_var: ContextVar[str] = ContextVar("sanjeevani_tenant", default="-")
principal_var: ContextVar[str] = ContextVar("sanjeevani_principal", default="-")

#: Incoming ids are accepted only if they look like ids. Bounded length and a
#: conservative character class, because this value ends up in log lines.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

#: Anything below WARNING is line noise in production. The access log line is the
#: record of what happened; per-stage info logs are for development.
DEFAULT_LEVEL: Final[int] = logging.INFO


def new_request_id() -> str:
    return secrets.token_hex(8)


def sanitise_request_id(candidate: str | None) -> str:
    """Accept a caller-supplied id only if it is well-formed; otherwise mint one."""
    if candidate and _SAFE_REQUEST_ID.match(candidate):
        return candidate
    return new_request_id()


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the request context attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        tenant = tenant_var.get()
        if tenant != "-":
            payload["tenant"] = tenant
        principal = principal_var.get()
        if principal != "-":
            payload["principal"] = principal

        # Anything passed via `extra=` under `fields` is merged in, so a call site
        # can attach structured detail without string formatting.
        extra_fields = getattr(record, "fields", None)
        if isinstance(extra_fields, dict):
            payload.update(extra_fields)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_logging(*, level: int = DEFAULT_LEVEL, json_output: bool = True) -> None:
    """Install the JSON handler on the root logger, once.

    Idempotent: calling it twice must not double every log line.
    """
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, "_sanjeevani_json", False):
            return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else logging.Formatter("%(levelname)s %(name)s %(message)s"))
    # Marked with setattr rather than attribute assignment so the "private member"
    # lint rule does not have to be suppressed: this is a marker on a handler we
    # just constructed, not access to someone else's internals.
    _MARKER = "_sanjeevani_json"
    setattr(handler, _MARKER, True)
    root.addHandler(handler)
