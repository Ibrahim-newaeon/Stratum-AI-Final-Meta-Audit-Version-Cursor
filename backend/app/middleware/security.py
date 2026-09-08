# =============================================================================
# Stratum AI - Security Headers Middleware
# =============================================================================
"""
Security middleware that adds protective HTTP headers to all responses.
Implements OWASP security header recommendations.
"""

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings

# =============================================================================
# CSP allow-lists
# =============================================================================

# Measurement & Verification: GTM web container + GA4 collect endpoints (read-only measurement, not an ad platform)
# Google Fonts (fonts.googleapis.com / fonts.gstatic.com) are intentionally NOT allowed.
GTM_SCRIPT_HOSTS = "https://www.googletagmanager.com"
GA_CONNECT_HOSTS = (
    "https://www.google-analytics.com "
    "https://analytics.google.com "
    "https://*.google-analytics.com "
    "https://*.analytics.google.com "
    "https://*.googletagmanager.com"
)
GA_IMG_HOSTS = (
    "https://www.google-analytics.com "
    "https://*.google-analytics.com "
    "https://*.googletagmanager.com"
)

# Paddle Billing: Paddle.js v2 (script) + checkout/API hosts (connect + overlay iframe).
# The *.paddle.com wildcard also covers the sandbox hosts (sandbox-api.paddle.com,
# sandbox-buy.paddle.com, sandbox-checkout-service.paddle.com, sandbox-cdn.paddle.com).
PADDLE_SCRIPT_HOSTS = "https://cdn.paddle.com"
PADDLE_CONNECT_HOSTS = "https://*.paddle.com"
PADDLE_FRAME_HOSTS = "https://*.paddle.com"

# "Log in with Facebook": Meta's JS SDK. Authentication only - this is not an
# ad-platform integration and grants no ads_read/ads_management (see
# docs/integrations/README.md). Three directives are involved, and omitting any
# one of them breaks the button with a console-only error:
#
#   script-src   connect.facebook.net serves sdk.js. Meta documents this host
#                explicitly as the one a CSP must allow.
#   frame-src    the SDK injects a hidden cross-domain iframe (the "xd_arbiter"
#                on staticxx.facebook.com) to talk to Facebook. The login dialog
#                itself is a popup window, which CSP does not govern, so this is
#                needed for FB.init/FB.getLoginStatus rather than for the dialog.
#   connect-src  the SDK's own XHRs to Facebook while resolving login status.
#
# Nothing here loads on a page that does not render the button: the SDK is
# injected on demand by frontend/src/lib/facebookSdk.ts, so a deployment with
# FACEBOOK_LOGIN_ENABLED off contacts none of these hosts.
FACEBOOK_SCRIPT_HOSTS = "https://connect.facebook.net"
FACEBOOK_FRAME_HOSTS = "https://www.facebook.com https://staticxx.facebook.com"
FACEBOOK_CONNECT_HOSTS = "https://graph.facebook.com https://www.facebook.com"

# Permissions-Policy: the Payment Request API is only needed inside the Paddle
# checkout frames (Apple Pay / Google Pay).
PAYMENT_PERMISSION_POLICY = 'payment=(self "https://buy.paddle.com" "https://sandbox-buy.paddle.com")'


def build_csp(production: bool) -> str:
    """
    Build the Content-Security-Policy header value.

    Args:
        production: True for the strict production policy, False for the
            permissive development policy (hot reload, eval, localhost).

    Returns:
        The ``;``-joined CSP directive string.
    """
    if production:
        directives = [
            "default-src 'self'",
            (
                f"script-src 'self' https://cdn.jsdelivr.net {GTM_SCRIPT_HOSTS} "
                f"{PADDLE_SCRIPT_HOSTS} {FACEBOOK_SCRIPT_HOSTS}"
            ),
            "style-src 'self' 'unsafe-inline'",
            "font-src 'self' data:",
            f"img-src 'self' data: https: blob: {GA_IMG_HOSTS}",
            (
                f"connect-src 'self' {PADDLE_CONNECT_HOSTS} https://*.sentry.io wss: ws: "
                f"{GA_CONNECT_HOSTS} {FACEBOOK_CONNECT_HOSTS}"
            ),
            f"frame-src 'self' {PADDLE_FRAME_HOSTS} {FACEBOOK_FRAME_HOSTS}",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'self'",
            "upgrade-insecure-requests",
        ]
    else:
        directives = [
            "default-src 'self'",
            (
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
                f"{GTM_SCRIPT_HOSTS} {PADDLE_SCRIPT_HOSTS} {FACEBOOK_SCRIPT_HOSTS}"
            ),
            "style-src 'self' 'unsafe-inline'",
            "font-src 'self' data:",
            f"img-src 'self' data: https: blob: {GA_IMG_HOSTS}",
            (
                f"connect-src 'self' {PADDLE_CONNECT_HOSTS} ws: wss: "
                f"http://localhost:* http://127.0.0.1:* {GA_CONNECT_HOSTS} "
                f"{FACEBOOK_CONNECT_HOSTS}"
            ),
            f"frame-src 'self' {PADDLE_FRAME_HOSTS} {FACEBOOK_FRAME_HOSTS}",
            "object-src 'none'",
        ]
    return "; ".join(directives)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware that adds security headers to all HTTP responses.

    Headers added:
    - X-Content-Type-Options: Prevents MIME type sniffing
    - X-Frame-Options: Prevents clickjacking
    - X-XSS-Protection: Legacy XSS protection (for older browsers)
    - Referrer-Policy: Controls referrer information
    - Permissions-Policy: Restricts browser features
    - Content-Security-Policy: Controls resource loading (production only)
    - Strict-Transport-Security: Forces HTTPS (production only)
    - X-Permitted-Cross-Domain-Policies: Controls Flash/PDF cross-domain
    - Cache-Control: Prevents caching of sensitive data
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)

        # Skip security headers for health check endpoints (performance)
        if request.url.path.startswith("/health"):
            return response

        # =================================================================
        # Core Security Headers (always applied)
        # =================================================================

        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # Prevent clickjacking - page cannot be embedded in iframes
        response.headers["X-Frame-Options"] = "SAMEORIGIN"

        # Legacy XSS protection for older browsers
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Control how much referrer info is sent
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # Restrict browser features/APIs
        response.headers["Permissions-Policy"] = (
            "camera=(), "
            "microphone=(), "
            "geolocation=(), "
            f"{PAYMENT_PERMISSION_POLICY}, "
            "usb=(), "
            "magnetometer=(), "
            "gyroscope=(), "
            "accelerometer=()"
        )

        # Prevent Flash/Acrobat from loading data
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"

        # =================================================================
        # Environment-Specific Headers
        # =================================================================

        if settings.is_production:
            # HSTS - Force HTTPS for 1 year, include subdomains
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

            # Content Security Policy for production
            # Adjust these values based on your actual CDN/API domains (see build_csp)
            response.headers["Content-Security-Policy"] = build_csp(production=True)

        else:
            # Development CSP - more permissive for hot reload, etc. (see build_csp)
            response.headers["Content-Security-Policy"] = build_csp(production=False)

        # =================================================================
        # API-Specific Headers
        # =================================================================

        # Prevent caching of API responses with sensitive data
        if request.url.path.startswith("/api/"):
            # Check if response might contain sensitive data
            sensitive_paths = ["/api/v1/auth/", "/api/v1/users/", "/api/v1/settings/"]
            if any(request.url.path.startswith(path) for path in sensitive_paths):
                response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
                response.headers["Pragma"] = "no-cache"
                response.headers["Expires"] = "0"

        return response
