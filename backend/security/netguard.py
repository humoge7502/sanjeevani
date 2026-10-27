"""Outbound-URL guard — the one place that decides where this system may dial.

WHY THIS EXISTS. Two modules in this codebase make outbound HTTP calls by
design (the signal-feed adapters and the narrative seam), and in both cases the
URL comes from *operator configuration*, not from request data. That makes them
safe from the classic request-forgery attack — and still not safe from a
tampered or mistaken config: ``http.client`` will happily dial
``http://169.254.169.254/`` (cloud metadata) or ``http://127.0.0.1:8787/api/…``
(the system's own surface). One guard, used by every dial-out, turns that class
of mistake into a loud, worded failure before any request is made.

WHAT IT ENFORCES. http/https only; the host must be a real, *public*
destination. Hosts are classified in two steps: IP literals are classified
directly; names are **resolved** and every answer must be public, because a
public-looking name that resolves into private space is exactly the
rebind-shaped case a textual check would miss.

WHAT IT COSTS. One DNS resolution per validated URL (cached by the OS). The two
callers validate once per fetch, and a fetch whose host cannot resolve is a
fetch that would have failed anyway — the guard only changes the wording.

THE ESCAPE HATCH. Local development legitimately points the narrative seam at a
loopback LLM (Ollama, LM Studio). That is a deliberate operator decision, so it
is an explicit configuration flag (``allow_private_endpoints``, read by the
callers), off by default and documented in the threat model — never an accident
of omission.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

#: Names that are local by spelling, resolved or not.
_LOCAL_NAMES = ("localhost",)
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal")

NETGUARD_VERSION = "1.0.0"


class DisallowedHostError(ValueError):
    """The URL's destination is one this deployment must not call."""


def _addr_is_public(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """False for every range a dial-out must never target."""
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_reserved
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
    )


def _name_is_local(host: str) -> bool:
    lowered = host.lower().rstrip(".")
    return lowered in _LOCAL_NAMES or lowered.endswith(_LOCAL_SUFFIXES)


def _host_is_public(host: str) -> bool:
    """Classify the host: IP literals directly, names after resolving them."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return _addr_is_public(addr)

    if _name_is_local(host):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise DisallowedHostError(f"host {host!r} does not resolve: {exc}") from exc
    answers = [ipaddress.ip_address(info[4][0]) for info in infos]
    if not answers:  # pragma: no cover - getaddrinfo raises rather than returning []
        raise DisallowedHostError(f"host {host!r} does not resolve")
    return all(_addr_is_public(addr) for addr in answers)


def validate_public_http_url(url: str, *, allow_private: bool = False) -> None:
    """Reject any destination that is not a public http(s) endpoint.

    ``allow_private=True`` keeps the scheme rule but drops the host rule — the
    explicit, documented opt-out that lets a developer aim the narrative seam at
    a loopback model server. Nothing else bypasses it.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise DisallowedHostError(f"scheme must be http or https, got {parsed.scheme!r}")
    host = parsed.hostname or ""
    if not host:
        raise DisallowedHostError(f"URL has no host: {url!r}")
    if allow_private:
        return
    if _name_is_local(host):
        raise DisallowedHostError(f"host {host!r} is a local name")
    if not _host_is_public(host):
        raise DisallowedHostError(
            f"host {host!r} resolves into a private, loopback, link-local or "
            "reserved range; dial-outs must target public hosts "
            "(allow_private_endpoints=true overrides this for local development)"
        )
