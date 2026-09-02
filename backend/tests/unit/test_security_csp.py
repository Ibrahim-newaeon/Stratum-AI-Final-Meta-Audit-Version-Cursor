# =============================================================================
# Stratum AI - CSP Allow-list Tests (Measurement & Verification)
# =============================================================================
"""
Unit tests for the Content-Security-Policy allow-lists.

GA4 and GTM are measurement-only integrations (read-only GA4 baseline, GTM tag
deployment). Their hosts must be allowed in ``script-src`` / ``connect-src`` /
``img-src`` in all three CSP definitions, while Google Fonts stays out.

Covers:
- ``app.middleware.security`` (production + development policies, via build_csp
  and via the middleware with ``settings.is_production`` patched)
- ``app.services.embed_widgets.security.EmbedSecurityService``
- ``frontend/nginx.conf`` (parsed as text)
"""

import re
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.config import settings
from app.middleware.security import SecurityHeadersMiddleware, build_csp
from app.services.embed_widgets.security import EmbedSecurityService

pytestmark = pytest.mark.unit

BACKEND_DIR = Path(__file__).resolve().parents[2]
NGINX_CONF = BACKEND_DIR.parent / "frontend" / "nginx.conf"

GTM_SCRIPT_HOST = "https://www.googletagmanager.com"
GA_CONNECT_HOSTS = [
    "https://www.google-analytics.com",
    "https://analytics.google.com",
    "https://*.google-analytics.com",
    "https://*.analytics.google.com",
    "https://*.googletagmanager.com",
]
GA_IMG_HOSTS = [
    "https://www.google-analytics.com",
    "https://*.google-analytics.com",
    "https://*.googletagmanager.com",
]
FORBIDDEN_HOSTS = ["fonts.googleapis.com", "fonts.gstatic.com"]


def _directives(csp: str) -> dict[str, list[str]]:
    """Parse a CSP string into {directive: [sources]}."""
    parsed: dict[str, list[str]] = {}
    for part in csp.split(";"):
        tokens = part.strip().split()
        if not tokens:
            continue
        parsed[tokens[0]] = tokens[1:]
    return parsed


def _assert_measurement_hosts(csp: str) -> None:
    """Shared assertions for any CSP definition."""
    directives = _directives(csp)

    assert GTM_SCRIPT_HOST in directives["script-src"], csp
    for host in GA_CONNECT_HOSTS:
        assert host in directives["connect-src"], f"{host} missing from connect-src: {csp}"
    for host in GA_IMG_HOSTS:
        assert host in directives["img-src"], f"{host} missing from img-src: {csp}"

    for forbidden in FORBIDDEN_HOSTS:
        assert forbidden not in csp, f"{forbidden} must not be allowed: {csp}"

    # Still 'self'-anchored - the allow-list is additive, not a wildcard.
    assert "'self'" in directives["default-src"]
    assert "'none'" in directives["object-src"]


def _app_with_security_headers() -> Starlette:
    async def ok(_request):  # type: ignore[no-untyped-def]
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/api/v1/ping", ok)])
    app.add_middleware(SecurityHeadersMiddleware)
    return app


# =============================================================================
# build_csp
# =============================================================================


class TestBuildCsp:
    """Direct tests of the CSP builder."""

    def test_production_policy_allows_measurement_hosts(self) -> None:
        csp = build_csp(production=True)
        _assert_measurement_hosts(csp)
        directives = _directives(csp)
        assert "upgrade-insecure-requests" in directives
        assert "'unsafe-eval'" not in directives["script-src"]
        # Existing allow-list entries are preserved.
        assert "https://cdn.jsdelivr.net" in directives["script-src"]
        assert "https://api.stripe.com" in directives["connect-src"]

    def test_development_policy_allows_measurement_hosts(self) -> None:
        csp = build_csp(production=False)
        _assert_measurement_hosts(csp)
        directives = _directives(csp)
        assert "'unsafe-eval'" in directives["script-src"]
        assert "http://localhost:*" in directives["connect-src"]

    def test_no_ad_platform_hosts_are_allowed(self) -> None:
        """GA4/GTM are measurement-only; no Google Ads / TikTok / Snap hosts sneak in."""
        for production in (True, False):
            csp = build_csp(production=production).lower()
            for host in ("googleads", "doubleclick", "googleadservices", "tiktok", "snapchat", "sc-static"):
                assert host not in csp, f"{host} must not be in CSP"


# =============================================================================
# SecurityHeadersMiddleware (settings.is_production patched)
# =============================================================================


class TestSecurityHeadersMiddleware:
    """The middleware emits the production/development policy based on settings."""

    def test_production_headers(self) -> None:
        with patch.object(
            type(settings), "is_production", new_callable=PropertyMock, return_value=True
        ):
            client = TestClient(_app_with_security_headers())
            response = client.get("/api/v1/ping")

        assert response.status_code == 200
        csp = response.headers["Content-Security-Policy"]
        _assert_measurement_hosts(csp)
        assert csp == build_csp(production=True)
        assert "Strict-Transport-Security" in response.headers
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_development_headers(self) -> None:
        with patch.object(
            type(settings), "is_production", new_callable=PropertyMock, return_value=False
        ):
            client = TestClient(_app_with_security_headers())
            response = client.get("/api/v1/ping")

        assert response.status_code == 200
        csp = response.headers["Content-Security-Policy"]
        _assert_measurement_hosts(csp)
        assert csp == build_csp(production=False)
        assert "Strict-Transport-Security" not in response.headers


# =============================================================================
# Embed widget CSP
# =============================================================================


class TestEmbedWidgetCsp:
    """Embed widgets get the same measurement allow-list."""

    def test_embed_csp_allows_measurement_hosts(self) -> None:
        service = EmbedSecurityService(signing_key="test-key")
        headers = service.get_csp_headers(["example.com"], widget_type="dashboard")
        csp = headers["Content-Security-Policy"]
        _assert_measurement_hosts(csp)
        directives = _directives(csp)
        assert "'none'" in directives["form-action"]
        assert "https://example.com" in directives["frame-ancestors"]


# =============================================================================
# nginx.conf
# =============================================================================


class TestNginxCsp:
    """The frontend nginx CSP mirrors the backend allow-list."""

    @pytest.mark.skipif(not NGINX_CONF.exists(), reason="frontend/nginx.conf not present")
    def test_nginx_csp_allows_measurement_hosts(self) -> None:
        text = NGINX_CONF.read_text(encoding="utf-8")
        match = re.search(r'add_header\s+Content-Security-Policy\s+"([^"]+)"', text)
        assert match, "Content-Security-Policy header not found in nginx.conf"
        csp = match.group(1)
        _assert_measurement_hosts(csp)
