"""The outbound-URL guard: a dial-out is a decision, and decisions are tested.

Every class of destination the guard must refuse gets its own case, because a
guard that half-works is worse than none: it reads as protection while the
metadata endpoint stays reachable.

    python -m pytest tests/unit/test_netguard.py -q
"""

from __future__ import annotations

import ipaddress
import socket

import pytest

from backend.agents import narrative as narrative_mod
from backend.agents.feeds import base as feeds_base
from backend.security.netguard import DisallowedHostError, validate_public_http_url

MODULE = "tests.unit.test_netguard"


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",  # loopback, IPv4
        "http://[::1]/x",  # loopback, IPv6
        "http://10.1.2.3/x",  # private
        "http://172.16.0.9/x",  # private
        "http://192.168.1.1/x",  # private
        "http://169.254.169.254/latest/meta-data/",  # link-local / cloud metadata
        "http://0.0.0.0/x",  # unspecified
        "http://224.0.0.1/x",  # multicast
        "http://[fe80::1]/x",  # link-local, IPv6
        "http://[fc00::1]/x",  # unique-local, IPv6
        "http://localhost/x",  # local by name
        "http://api.localhost/x",  # local by suffix
        "http://ollama.local:11434/v1/chat/completions",  # local by suffix
        "http://metadata.internal/x",  # local by suffix
        "file:///etc/passwd",  # not http at all
        "ftp://example.com/x",
        "http:///no-host",  # no host
    ],
)
def test_a_private_or_non_http_destination_is_refused(url):
    with pytest.raises(DisallowedHostError):
        validate_public_http_url(url)


@pytest.mark.unit
@pytest.mark.parametrize(
    "url",
    [
        "http://93.184.216.34/x",  # public IP literal, no DNS needed
        "https://[2606:2800:220:1:248:1893:25c8:1946]/x",  # public v6 literal
    ],
)
def test_a_public_http_destination_is_allowed(url):
    # IP literals only: a *name* allowed here would make the test depend on live
    # DNS, which differs per machine (a filtering resolver answers private
    # addresses for public names -- the guard is then CORRECT to refuse it, and
    # that behaviour is asserted by the mocked-resolution tests below).
    validate_public_http_url(url)


@pytest.mark.unit
def test_a_public_name_that_resolves_private_is_refused(monkeypatch):
    """The rebind-shaped case a textual check would miss."""

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, None, None, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(DisallowedHostError):
        validate_public_http_url("https://looks-public.example.com/x")


@pytest.mark.unit
def test_a_name_that_resolves_public_is_allowed(monkeypatch):
    public = str(ipaddress.ip_address("93.184.216.34"))

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, None, None, "", (public, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    validate_public_http_url("https://example.com/x")


@pytest.mark.unit
def test_a_name_that_does_not_resolve_is_refused_with_a_worded_error(monkeypatch):
    def boom(host, port, *args, **kwargs):
        raise socket.gaierror("nameserver unreachable")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(DisallowedHostError, match="does not resolve"):
        validate_public_http_url("https://unreachable.example.com/x")


@pytest.mark.unit
def test_allow_private_keeps_the_scheme_rule_and_drops_the_host_rule():
    # Scheme still enforced even for local development.
    with pytest.raises(DisallowedHostError, match="scheme"):
        validate_public_http_url("file:///etc/passwd", allow_private=True)
    # ...and loopback becomes callable, which is the point of the opt-out.
    validate_public_http_url("http://127.0.0.1:11434/v1/chat/completions", allow_private=True)


# ---------------------------------------------------------------------------
# The two real callers refuse through their own error types
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_a_feed_aimed_at_a_private_host_reports_unavailable():
    class ProbeFeed(feeds_base.FeedAdapter):
        """The smallest concrete adapter: no parsing, no parameters."""

        def query_params(self) -> dict:
            return {}

        def normalise(self, payload: object, retrieved_utc: str) -> list:
            return []

    adapter = ProbeFeed(
        {"id": "probe", "display_name": "probe", "endpoint": "http://169.254.169.254/x"}
    )
    with pytest.raises(feeds_base.FeedUnavailable, match="private"):
        adapter._get_json({})


@pytest.mark.unit
def test_the_narrator_aimed_at_a_private_host_raises_before_any_connection():
    with pytest.raises(DisallowedHostError):
        narrative_mod._post_json(
            "http://127.0.0.1:9/v1/chat/completions",
            {"messages": []},
            headers={},
            timeout=1.0,
        )
