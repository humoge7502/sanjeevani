"""Security response headers, including a nonce-based Content-Security-Policy.

A missing CSP is not a theoretical problem for a single-page app served from the
same origin: it is the difference between "an injected string is inert" and "an
injected string can call the API". The middleware here sets a strict policy and,
crucially, **makes the nonce available to the HTML renderer** so the policy does
not need ``'unsafe-inline'`` for scripts.

Implemented as a pure ASGI middleware rather than a ``BaseHTTPMiddleware``
subclass: the latter buffers and re-wraps the response stream, and this response
is a file-backed SPA in the hot path.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable, MutableMapping
from typing import Any, Final

#: Where the per-request nonce is stashed on the ASGI scope.
NONCE_SCOPE_KEY: Final[str] = "sanjeevani.csp_nonce"

#: Response headers applied to every response. These are cheap, unambiguous wins:
#: each one closes a class of browser-side attack that has nothing to do with the
#: application logic.
BASE_HEADERS: Final[dict[str, str]] = {
    # Stop the browser guessing a content type and executing an upload as script.
    "X-Content-Type-Options": "nosniff",
    # Legacy framing control (frame-ancestors in the CSP is the modern one).
    "X-Frame-Options": "DENY",
    # Do not leak the command-centre URL to third parties in the Referer header.
    "Referrer-Policy": "no-referrer",
    # The UI needs none of these; denying them removes a whole capability surface.
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    # Belongs on every response; harmless over plain HTTP because browsers ignore
    # it there, and correct the moment TLS is terminated in front of the app.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    # Deny by default for capabilities this app never uses.
    "X-Permitted-Cross-Domain-Policies": "none",
}


def build_csp(nonce: str | None, *, extra_connect_src: Iterable[str] = ()) -> str:
    """Compose the Content-Security-Policy for this build.

    The policy is deliberately tight and explainable rather than long:

      * ``script-src 'self' 'nonce-...'`` -- only our own bundles, plus the single
        inline module-preload shim Vite emits, which carries the nonce. No
        ``'unsafe-inline'``, so an injected ``<script>`` cannot execute.
      * ``style-src 'self' 'unsafe-inline'`` -- React sets ``style`` attributes
        directly for the SVG network map, and style *attributes* are governed by
        this directive. Scripts, which are what matter, stay nonce-only.
      * ``connect-src 'self'`` -- the UI talks only to its own origin, because the
        API and the SPA share a port. No CORS, no third-party callback.
      * ``object-src 'none'``, ``base-uri 'none'``, ``frame-ancestors 'none'`` --
        plugins, ``<base>`` hijacking and framing are all closed.
    """
    script_src = "'self'"
    if nonce:
        script_src = f"'self' 'nonce-{nonce}'"
    connect = " ".join(("'self'", *extra_connect_src))
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src {script_src}",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self' data:",
            f"connect-src {connect}",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


def nonce_for(scope: MutableMapping[str, Any]) -> str:
    """The nonce for this request, creating it if the middleware has not run."""
    existing = scope.get(NONCE_SCOPE_KEY)
    if isinstance(existing, str):
        return existing
    created = secrets.token_urlsafe(16)
    scope[NONCE_SCOPE_KEY] = created
    return created


class SecurityHeadersMiddleware:
    """Attach security headers to every HTTP response and mint a CSP nonce."""

    def __init__(
        self,
        app: Any,
        *,
        enabled: bool = True,
        extra_connect_src: Iterable[str] = (),
        headers: dict[str, str] | None = None,
    ) -> None:
        self.app = app
        self.enabled = enabled
        self.extra_connect_src = tuple(extra_connect_src)
        self.headers = {**BASE_HEADERS, **(headers or {})}

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not self.enabled:
            await self.app(scope, receive, send)
            return

        nonce = secrets.token_urlsafe(16)
        scope[NONCE_SCOPE_KEY] = nonce
        csp = build_csp(nonce, extra_connect_src=self.extra_connect_src)

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                raw = list(message.get("headers", []))
                existing = {k.decode("latin-1").lower() for k, _ in raw}
                additions = [
                    (name.lower().encode("latin-1"), value.encode("latin-1"))
                    for name, value in self.headers.items()
                    if name.lower() not in existing
                ]
                if "content-security-policy" not in existing:
                    additions.append((b"content-security-policy", csp.encode("latin-1")))
                message = {**message, "headers": [*raw, *additions]}
            await send(message)

        await self.app(scope, receive, send_with_headers)
