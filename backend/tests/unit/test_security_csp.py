# =============================================================================
# Stratum AI - CSP Allow-list Tests (Measurement & Verification + Paddle Billing)
# =============================================================================
"""
Unit tests for the Content-Security-Policy allow-lists.

GA4 and GTM are measurement-only integrations (read-only GA4 baseline, GTM tag
deployment). Their hosts must be allowed in ``script-src`` / ``connect-src`` /
``img-src`` in all three CSP definitions, while Google Fonts stays out.

Paddle Billing (merchant of record) needs Paddle.js from ``cdn.paddle.com``
(``script-src``) and the checkout / API hosts ``*.paddle.com`` in
``connect-src`` and ``frame-src``. No other payment-gateway host may appear.

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

PADDLE_SCRIPT_HOST = "https://cdn.paddle.com"
PADDLE_WILDCARD_HOST = "https://*.paddle.com"

# Every third-party source allowed in script-src / connect-src / frame-src must
# belong to one of these (host suffixes) - this is how we assert that no former
# payment-gateway host survives in any policy.
ALLOWED_HOST_SUFFIXES = (
    "cdn.jsdelivr.net",
    "googletagmanager.com",
    "google-analytics.com",
    "analytics.google.com",
    "sentry.io",
    "paddle.com",
    "localhost:*",
    "127.0.0.1:*",
)
SCHEME_SOURCES = {"ws:", "wss:", "https:", "http:", "data:", "blob:"}


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


def _assert_paddle_hosts(csp: str) -> None:
    """Paddle.js + checkout/API hosts are allowed where Paddle needs them."""
    directives = _directives(csp)

    assert PADDLE_SCRIPT_HOST in directives["script-src"], csp
    assert PADDLE_WILDCARD_HOST in directives["connect-src"], csp
    assert "frame-src" in directives, f"frame-src missing: {csp}"
    assert "'self'" in directives["frame-src"], csp
    assert PADDLE_WILDCARD_HOST in directives["frame-src"], csp


def _assert_only_allowed_third_parties(csp: str) -> None:
    """No host outside the known allow-list (e.g. a former payment gateway) is present."""
    directives = _directives(csp)
    for directive in ("script-src", "connect-src", "frame-src"):
        for source in directives.get(directive, []):
            if source.startswith("'") or source in SCHEME_SOURCES:
                continue
            host = re.sub(r"^https?://", "", source)
            assert any(
                host == suffix or host.endswith(("." + suffix, suffix))
                for suffix in ALLOWED_HOST_SUFFIXES
            ), f"unexpected third-party source {source!r} in {directive}: {csp}"


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
        assert "https://*.sentry.io" in directives["connect-src"]

    def test_development_policy_allows_measurement_hosts(self) -> None:
        csp = build_csp(production=False)
        _assert_measurement_hosts(csp)
        directives = _directives(csp)
        assert "'unsafe-eval'" in directives["script-src"]
        assert "http://localhost:*" in directives["connect-src"]

    def test_production_policy_allows_paddle_hosts(self) -> None:
        csp = build_csp(production=True)
        _assert_paddle_hosts(csp)
        _assert_only_allowed_third_parties(csp)

    def test_development_policy_allows_paddle_hosts(self) -> None:
        csp = build_csp(production=False)
        _assert_paddle_hosts(csp)
        _assert_only_allowed_third_parties(csp)
        assert _directives(csp)["frame-src"] == ["'self'", PADDLE_WILDCARD_HOST]

    def test_paddle_hosts_identical_in_both_policies(self) -> None:
        prod = _directives(build_csp(production=True))
        dev = _directives(build_csp(production=False))
        for directive in ("script-src", "connect-src", "frame-src"):
            prod_paddle = {s for s in prod[directive] if "paddle.com" in s}
            dev_paddle = {s for s in dev[directive] if "paddle.com" in s}
            assert prod_paddle == dev_paddle, directive

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
        _assert_paddle_hosts(csp)
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
        _assert_paddle_hosts(csp)
        assert csp == build_csp(production=False)
        assert "Strict-Transport-Security" not in response.headers

    def test_permissions_policy_allows_payment_only_for_paddle_checkout(self) -> None:
        client = TestClient(_app_with_security_headers())
        response = client.get("/api/v1/ping")
        policy = response.headers["Permissions-Policy"]
        match = re.search(r"payment=\(([^)]*)\)", policy)
        assert match, policy
        allowed = match.group(1)
        assert "self" in allowed
        assert '"https://buy.paddle.com"' in allowed
        assert '"https://sandbox-buy.paddle.com"' in allowed
        assert "camera=()" in policy


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

    @staticmethod
    def _nginx_csp() -> str:
        text = NGINX_CONF.read_text(encoding="utf-8")
        match = re.search(r'add_header\s+Content-Security-Policy\s+"([^"]+)"', text)
        assert match, "Content-Security-Policy header not found in nginx.conf"
        return match.group(1)

    @pytest.mark.skipif(not NGINX_CONF.exists(), reason="frontend/nginx.conf not present")
    def test_nginx_csp_allows_measurement_hosts(self) -> None:
        _assert_measurement_hosts(self._nginx_csp())

    @pytest.mark.skipif(not NGINX_CONF.exists(), reason="frontend/nginx.conf not present")
    def test_nginx_csp_allows_paddle_hosts_only(self) -> None:
        csp = self._nginx_csp()
        _assert_paddle_hosts(csp)
        _assert_only_allowed_third_parties(csp)
