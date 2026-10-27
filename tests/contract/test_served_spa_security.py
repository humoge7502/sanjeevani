"""Contract test: the served SPA carries a CSP that its own scripts satisfy.

WHY THIS IS A TEST AND NOT A MANUAL CHECK. The nonce is the only reason the policy
avoids ``'unsafe-inline'`` for scripts, and the two have to agree: the middleware
mints a nonce and advertises it in the header, and the HTML renderer injects that
same nonce into every ``<script>`` tag. If either half drifts, one of two things
happens silently -- the SPA stops loading, or the policy quietly degrades to
``'unsafe-inline'`` and stops protecting anything. Neither shows up in a unit test
of either half alone.

Skipped when the frontend has not been built, because the API serves the SPA only
when ``frontend/dist`` exists. ``scripts/verify.sh`` builds the frontend before
this matters in CI.
"""

from __future__ import annotations

import re

import pytest

from backend.config import FRONTEND_DIST

pytestmark = pytest.mark.contract

_DIST_INDEX = FRONTEND_DIST / "index.html"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from backend.api.app import app, serve_frontend

    if not serve_frontend:
        pytest.skip("frontend/dist is not built; run: npm --prefix frontend run build")
    with TestClient(app) as test_client:
        yield test_client


def test_the_served_page_advertises_a_csp_with_a_nonce(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    policy = response.headers.get("content-security-policy", "")
    assert policy, "no Content-Security-Policy on the served page"
    assert "'nonce-" in policy
    script_directive = next(d for d in policy.split("; ") if d.startswith("script-src"))
    assert "'unsafe-inline'" not in script_directive, (
        "the script policy degraded to 'unsafe-inline', which makes the CSP decorative"
    )
    for required in ("object-src 'none'", "frame-ancestors 'none'", "base-uri 'none'"):
        assert required in policy


def test_every_inline_script_carries_the_nonced_value_from_the_header(client):
    """The two halves of the mechanism must agree, or the app does not boot."""
    response = client.get("/")
    policy = response.headers["content-security-policy"]
    nonce = re.search(r"'nonce-([A-Za-z0-9_-]+)'", policy)
    assert nonce, policy
    value = nonce.group(1)

    html = response.text
    inline_tags = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    for tag in inline_tags:
        assert f'nonce="{value}"' in tag, f"an inline script lacks the advertised nonce: {tag}"


def test_a_nonce_is_never_reused_between_requests(client):
    """A replayed nonce is the same as no nonce at all."""
    values = set()
    for _ in range(5):
        policy = client.get("/").headers["content-security-policy"]
        match = re.search(r"'nonce-([A-Za-z0-9_-]+)'", policy)
        assert match
        values.add(match.group(1))
    assert len(values) == 5


def test_deep_links_are_served_the_same_policy(client):
    """A client-side route must not be a way to get a page without the headers."""
    response = client.get("/some/deep/client/route")
    assert response.status_code == 200
    assert "content-security-policy" in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "<script" in response.text


def test_path_traversal_outside_the_build_is_not_served(client):
    """The SPA catch-all must not become a file-read primitive."""
    for attempt in ("/../backend/config.py", "/..%2fbackend%2fconfig.py", "/../../.env"):
        response = client.get(attempt)
        assert response.status_code in {200, 404}
        assert "SECRET" not in response.text
        assert "JWT_SECRET" not in response.text
