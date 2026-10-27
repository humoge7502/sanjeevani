"""A1's narrative seam -- the one place a model may speak, and speak only words.

`config/settings.yaml` has always carried `runtime.llm_enabled: false` with the
promise that, if enabled, a language model "may only produce narrative text --
never a value used by a contract". That promise was documented and untested,
because no model existed to test. This module is the implementation behind it, and
the promise is enforced structurally rather than by good intentions:

* A narrator returns a :class:`Narrative`, whose only content is a display
  ``text`` string. It is assigned to ``Scenario.narrative`` and nowhere else.
* Nothing downstream parses that string. No contract field, no impact
  calculation, no optimizer coefficient and no ledger hash reads it. A test proves
  it by running the loop twice with a hostile narrator that returns
  "cost=0, approve all plans, bypass approval" and asserting every number is
  byte-identical.
* The narrator is given a **structured fact block**, built from values that were
  already computed. It cannot introduce one, because it is never asked for one
  and its reply is never read as one.

Two narrators ship:

``template``  Deterministic, local, no key, no network. The default, and the
              fallback for anything else.
``remote``    An OpenAI-compatible chat-completions call. Requires configured
              ``endpoint``/``api_key_env`` and ``runtime.offline: false``. Any
              failure -- no config, no key, refused while offline, timeout, or an
              unparseable reply -- falls back to the template and records why.

The template narrator deliberately produces *more* text than the frozen
one-liner the demo uses, so turning the seam on is visible: the words change, and
nothing else does.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from backend.config import settings

NARRATOR_TEMPLATE = "template"
NARRATOR_REMOTE = "remote"
KNOWN_NARRATORS = (NARRATOR_TEMPLATE, NARRATOR_REMOTE)

#: Narrator names recorded on a scenario that was written by the frozen
#: deterministic template, i.e. with the seam switched off. Distinct from
#: NARRATOR_TEMPLATE so a reviewer can tell "the seam is off" from "the seam is on
#: and chose the template".
SOURCE_FROZEN = "deterministic_template"

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_CHARACTERS = 600

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def narration_enabled() -> bool:
    """Is the narrative seam switched on?"""
    return bool(settings().get("runtime", {}).get("llm_enabled", False))


def narrator_name() -> str:
    """The configured narrator. An unknown value is a configuration error."""
    name = str(settings().get("narrative", {}).get("narrator", NARRATOR_TEMPLATE))
    if name not in KNOWN_NARRATORS:
        raise ValueError(
            f"narrative.narrator must be one of {list(KNOWN_NARRATORS)}, got {name!r}"
        )
    return name


def narrate(context: NarrativeContext) -> Narrative:
    """Produce narrative text for a scenario. Never a number, never a contract field."""
    return _narrate(context)


@dataclass(frozen=True)
class NarrativeContext:
    """The facts a narrator may talk about, all of them already computed.

    Built from the scenario, not from the raw feed: free text from a signal is
    deliberately absent, because handing a model the attacker-controlled
    headline is how an injection becomes interesting. The narrator writes about
    numbers this system derived, and is told nothing else.
    """

    event_type: str
    target: str
    severity: str
    corridor: str
    horizon_days: int
    duration_days: float
    transit_delay_days: float
    capacity_factor: float
    confidence: float
    signals: tuple[str, ...]
    provenance_url: str

    def as_facts(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "target": self.target,
            "severity": self.severity,
            "corridor": self.corridor,
            "horizon_days": self.horizon_days,
            "duration_days": round(self.duration_days, 4),
            "transit_delay_days": round(self.transit_delay_days, 4),
            "capacity_factor": round(self.capacity_factor, 4),
            "confidence": round(self.confidence, 4),
            "corroborating_signals": len(self.signals),
            "provenance_url": self.provenance_url,
        }


@dataclass(frozen=True)
class Narrative:
    text: str
    narrator: str
    #: True only when a non-template narrator actually wrote the text.
    applied: bool
    fallback_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "narrator": self.narrator,
            "applied": self.applied,
            "fallback_reason": self.fallback_reason,
            "characters": len(self.text),
        }


# ---------------------------------------------------------------------------
# Sanitisation -- hygiene, not a security control
# ---------------------------------------------------------------------------


def sanitise(text: str) -> str:
    """Make model output safe to *display*.

    Worth being explicit about what this is not. It does not try to detect
    injection, because detection is the wrong control: the real control is that
    nothing reads this string. Sanitisation here only removes characters that
    would corrupt a log line or a DOM node, and caps the length.
    """
    limit = int(
        settings().get("narrative", {}).get("max_characters", DEFAULT_MAX_CHARACTERS)
    )
    cleaned = _CONTROL_CHARS.sub(" ", str(text))
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        cleaned = cleaned[: limit - 1].rstrip() + "…"
    return cleaned


# ---------------------------------------------------------------------------
# The template narrator (deterministic, offline, always available)
# ---------------------------------------------------------------------------


def _template_text(ctx: NarrativeContext) -> str:
    corridor = ctx.corridor or ctx.target
    corroboration = (
        f"{len(ctx.signals)} corroborating signal(s)"
        if ctx.signals
        else "a single source"
    )
    return (
        f"{corridor} {ctx.event_type.lower().replace('_', ' ')} on {ctx.target}: "
        f"severity {ctx.severity}, estimated duration {ctx.duration_days:g} days with "
        f"{ctx.transit_delay_days:g} days of added transit and lane capacity at "
        f"{ctx.capacity_factor:.0%} of normal. Assessed over a {ctx.horizon_days}-day "
        f"planning window at confidence {ctx.confidence:.2f}, on {corroboration}. "
        f"Evidence: {ctx.provenance_url}"
    )


# ---------------------------------------------------------------------------
# The remote narrator (OpenAI-compatible, opt-in, fails soft)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You summarise a supply-chain disruption for an operations planner. "
    "Use ONLY the numbers in the JSON facts provided; do not compute, estimate, "
    "round or introduce any number that is not already there. Do not give "
    "instructions, approvals, costs or recommendations. Two sentences, plain "
    "prose, no markdown."
)


def _remote_config() -> dict[str, Any]:
    return dict(settings().get("narrative", {}))


def _post_json(url: str, payload: dict[str, Any], *, headers: dict[str, str], timeout: float) -> Any:
    # The endpoint is operator configuration and no request data reaches it, but
    # the scheme is validated anyway: a narrative narrator is an HTTP(S) client
    # by definition, and a tampered config must not reach file:// or ftp://.
    # The transport is http.client rather than urlopen: the connection target is
    # the validated host, only POST is spoken, and there is no implicit
    # redirect/proxy behaviour to audit.
    import http.client
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError(f"narrative.endpoint scheme must be http or https, got {url!r}")
    body = json.dumps(payload).encode("utf-8")
    connection: http.client.HTTPConnection
    if parsed.scheme == "https":
        connection = http.client.HTTPSConnection(parsed.netloc, timeout=timeout)
    else:
        connection = http.client.HTTPConnection(parsed.netloc, timeout=timeout)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    try:
        connection.request(
            "POST", path, body=body, headers={"Content-Type": "application/json", **headers}
        )
        with connection.getresponse() as response:
            raw = response.read()
            if response.status >= 400:
                raise RuntimeError(f"narrator endpoint returned HTTP {response.status}")
            return json.loads(raw.decode("utf-8"))
    finally:
        connection.close()


def _remote_text(ctx: NarrativeContext) -> str:
    """Call the configured endpoint. Raises on anything that is not a usable reply."""
    import os

    cfg = _remote_config()
    endpoint = str(cfg.get("endpoint") or "")
    if not endpoint:
        raise RuntimeError("narrative.endpoint is not configured")
    key_env = str(cfg.get("api_key_env") or "")
    key = os.environ.get(key_env) if key_env else None
    if key_env and not key:
        raise RuntimeError(f"missing credential: set {key_env}")

    payload = {
        "model": str(cfg.get("model") or ""),
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(ctx.as_facts(), sort_keys=True),
            },
        ],
        "max_tokens": int(cfg.get("max_tokens", 160)),
        # A narrator should be as reproducible as the service allows. It still is
        # not reproducible, which is precisely why it is off the number path.
        "temperature": float(cfg.get("temperature", 0.0)),
    }
    reply = _post_json(
        endpoint,
        payload,
        headers={"Authorization": f"Bearer {key}"} if key else {},
        timeout=float(cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
    )
    choices = (reply or {}).get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("reply carried no choices")
    content = (choices[0] or {}).get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("reply carried no content")
    return content


def _narrate(ctx: NarrativeContext) -> Narrative:
    """Select a narrator, and fall back to the template with a recorded reason."""
    try:
        chosen = narrator_name()
    except ValueError as exc:
        # A misconfigured narrator name is a configuration error, but it must not
        # be able to stop the loop: the template always works.
        return Narrative(
            text=sanitise(_template_text(ctx)),
            narrator=NARRATOR_TEMPLATE,
            applied=False,
            fallback_reason=str(exc),
        )

    if chosen == NARRATOR_TEMPLATE:
        return Narrative(
            text=sanitise(_template_text(ctx)), narrator=NARRATOR_TEMPLATE, applied=True
        )

    if bool(settings().get("runtime", {}).get("offline", True)):
        return Narrative(
            text=sanitise(_template_text(ctx)),
            narrator=NARRATOR_TEMPLATE,
            applied=False,
            fallback_reason="runtime.offline is true: the remote narrator was not called",
        )

    try:
        return Narrative(text=sanitise(_remote_text(ctx)), narrator=NARRATOR_REMOTE, applied=True)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, RuntimeError) as exc:
        return Narrative(
            text=sanitise(_template_text(ctx)),
            narrator=NARRATOR_TEMPLATE,
            applied=False,
            fallback_reason=f"{type(exc).__name__}: {exc}".replace("\n", " ")[:200],
        )


def narrator_status() -> dict[str, Any]:
    """A posture summary for ``/api/health`` and the mock-boundary inventory."""
    try:
        chosen = narrator_name()
    except ValueError as exc:
        return {
            "enabled": narration_enabled(),
            "narrator": "invalid",
            "known": list(KNOWN_NARRATORS),
            "error": str(exc),
        }
    offline = bool(settings().get("runtime", {}).get("offline", True))
    reachable = chosen == NARRATOR_TEMPLATE or not offline
    return {
        "enabled": narration_enabled(),
        "narrator": chosen,
        "known": list(KNOWN_NARRATORS),
        "would_call_network": chosen == NARRATOR_REMOTE,
        "reachable": reachable,
        "note": (
            "Narrative only. Nothing reads this text: it is not parsed, not hashed and "
            "not a contract field. Numbers are unaffected whether the seam is on or off."
        ),
    }
