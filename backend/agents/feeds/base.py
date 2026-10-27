"""Signal-feed adapters -- A1's front door for sources that are not a file.

Every adapter in this package obeys three rules, and they are the reason the
adapters can exist at all without weakening anything the project already claims:

1. **The output shape is not new.** An adapter produces the same fields as one
   record in ``data/events/hero_feeds.yaml``. Downstream (A1 classify, A2 verify,
   A3 impact, M2, M3) cannot tell which produced it, so no contract changes.

2. **An adapter never invents a number.** It may copy a value the payload
   actually carried, and it may attach a *configured constant* from
   ``config/feeds.yaml``. It may not parse free text into a parameter, and it may
   not synthesise a plausible value when the payload is silent.

3. **A feed that cannot be read is reported unavailable.** Not an error, not an
   empty signal, and never a guess. ``FeedUnavailable`` travels up to the loader,
   which records it in the feed provenance so ``/api/state`` and
   ``/api/mock-boundaries`` can show that a source contributed nothing.
"""

from __future__ import annotations

import abc
import hashlib
import http.client
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

#: A feed fetch is bounded. A hanging external service must not hang the loop.
DEFAULT_TIMEOUT_SECONDS = 10.0

USER_AGENT = "sanjeevani-signal-feed/1.0 (+https://github.com/humoge7502/sanjeevani)"


class FeedUnavailable(RuntimeError):
    """The feed could not be read. A reason to contribute nothing, never to guess."""


def canonical_json(payload: Any) -> str:
    """Stable serialisation used for every digest in this package."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )


def short_digest(*parts: Any) -> str:
    """A 12-hex-character content digest. Used for ids and snapshot filenames."""
    return hashlib.sha256(canonical_json(list(parts)).encode("utf-8")).hexdigest()[:12]


def collapse(text: str) -> str:
    """Whitespace-normalised free text, the same treatment the YAML loader gives it."""
    return " ".join(str(text).split())


@dataclass(frozen=True)
class SignalRecord:
    """One normalised signal: exactly the shape ``sensing.RawSignal`` consumes."""

    signal_id: str
    source_class: str
    source_id: str
    source_reliability: float
    source_url: str
    published_utc: str
    retrieved_utc: str
    headline: str
    body: str
    entities: tuple[str, ...] = ()
    claim: dict[str, Any] = field(default_factory=dict)

    def as_signal_kwargs(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "source_class": self.source_class,
            "source_id": self.source_id,
            "source_reliability": self.source_reliability,
            "source_url": self.source_url,
            "published_utc": self.published_utc,
            "retrieved_utc": self.retrieved_utc,
            "headline": self.headline,
            "body": self.body,
            "entities": tuple(self.entities),
            "claim": dict(self.claim),
        }


class FeedAdapter(abc.ABC):
    """Base class for a source adapter.

    Subclasses implement :meth:`query_params` (the request they issue) and
    :meth:`normalise` (the payload -> signals mapping). The HTTP call itself is
    implemented once, here, so there is one place where a network call can be
    made and one place where it can be refused.
    """

    #: Stable identifier; also the key in ``config/feeds.yaml``.
    feed_id: str = ""
    #: The A1 source class this feed's signals claim to be.
    source_class: str = "MANUAL"

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    # -- descriptive ----------------------------------------------------

    @property
    def display_name(self) -> str:
        return str(self.config.get("display_name") or self.feed_id)

    @property
    def endpoint(self) -> str:
        return str(self.config.get("endpoint") or "")

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    @property
    def api_key_env(self) -> str | None:
        name = self.config.get("api_key_env")
        return str(name) if name else None

    def api_key(self) -> str | None:
        """The credential, read from the environment. Never from a file in the repo."""
        name = self.api_key_env
        if not name:
            return None
        return os.environ.get(name) or None

    def request_spec(self) -> dict[str, Any]:
        """The exact request this adapter issues, recorded in every snapshot."""
        return {
            "feed_id": self.feed_id,
            "endpoint": self.endpoint,
            "params": {
                k: v for k, v in self.query_params().items() if v is not None
            },
        }

    # -- contract -------------------------------------------------------

    @abc.abstractmethod
    def query_params(self) -> dict[str, Any]:
        """Query parameters for this request. Never contains the credential."""

    @abc.abstractmethod
    def normalise(self, payload: Any, retrieved_utc: str) -> list[SignalRecord]:
        """Map a payload onto signals. Pure: same payload in, same signals out."""

    # -- transport ------------------------------------------------------

    def fetch(self, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
        """Perform the one HTTP GET this package is allowed to make."""
        return self._get_json(self.query_params(), timeout=timeout)

    def _get_json(self, params: dict[str, Any], timeout: float = DEFAULT_TIMEOUT_SECONDS) -> Any:
        """The single place in this package a network call happens.

        Raises :class:`FeedUnavailable` on anything that is not a valid JSON
        response. The caller decides what "unavailable" means; this method never
        substitutes a default payload.
        """
        if not self.endpoint:
            raise FeedUnavailable(f"{self.feed_id}: no endpoint configured")
        # The endpoint is configuration, not request input, so this is not an
        # SSRF path: no caller-supplied string reaches the URL. Scheme validation
        # is still enforced: urllib would happily serve file:// or ftp:// from a
        # tampered config, and a feed adapter is an HTTP client by definition.
        if urllib.parse.urlparse(self.endpoint).scheme not in ("http", "https"):
            raise FeedUnavailable(
                f"{self.feed_id}: endpoint scheme must be http or https "
                f"(got {self.endpoint.split(':', 1)[0] + ':' if ':' in self.endpoint else 'none'})"
            )
        params = {k: v for k, v in params.items() if v is not None}
        url = f"{self.endpoint}?{urllib.parse.urlencode(params)}" if params else self.endpoint

        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        header_name = self.config.get("api_key_header")
        key = self.api_key()
        if header_name and key:
            headers[str(header_name)] = key

        # The endpoint is configuration, not request input, so this is not an
        # SSRF path: no caller-supplied string reaches the URL. The transport is
        # http.client rather than urlopen: the connection target is the already
        # scheme-validated host, only GET is spoken, and there is no implicit
        # redirect/proxy behaviour to audit.
        parsed = urllib.parse.urlparse(url)
        connection: http.client.HTTPConnection
        if parsed.scheme == "https":
            connection = http.client.HTTPSConnection(parsed.netloc, timeout=timeout)
        else:
            connection = http.client.HTTPConnection(parsed.netloc, timeout=timeout)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        try:
            connection.request("GET", path, headers=headers)
            with connection.getresponse() as response:
                raw = response.read()
                if response.status >= 400:
                    raise FeedUnavailable(
                        f"{self.feed_id}: HTTP {response.status} {response.reason}"
                    )
                body = raw.decode("utf-8")
        except (http.client.HTTPException, TimeoutError, OSError) as exc:
            raise FeedUnavailable(f"{self.feed_id}: {type(exc).__name__}: {exc}") from exc
        finally:
            connection.close()
        try:
            return json.loads(body)
        except ValueError as exc:
            raise FeedUnavailable(
                f"{self.feed_id}: response was not JSON ({type(exc).__name__})"
            ) from exc

    def preflight(self) -> str | None:
        """Return a reason this adapter cannot run, or None if it can."""
        if not self.enabled:
            return "disabled by configuration"
        if self.api_key_env and not self.api_key():
            return f"missing credential: set {self.api_key_env}"
        return None
