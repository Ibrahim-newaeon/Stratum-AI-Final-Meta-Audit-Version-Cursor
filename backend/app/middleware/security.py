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
            f"script-src 'self' https://cdn.jsdelivr.net {GTM_SCRIPT_HOSTS}",
            "style-src 'self' 'unsafe-inline'",
            "font-src 'self' data:",
            f"img-src 'self' data: https: blob: {GA_IMG_HOSTS}",
            (
                "connect-src 'self' https://api.stripe.com https://*.sentry.io wss: ws: "
                f"{GA_CONNECT_HOSTS}"
            ),
            "frame-src 'self' https://js.stripe.com https://hooks.stripe.com",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'self'",
            "upgrade-insecure-requests",
        ]
    else:
        directives = [
            "default-src 'self'",
            f"script-src 'self' 'unsafe-inline' 'unsafe-eval' {GTM_SCRIPT_HOSTS}",
            "style-src 'self' 'unsafe-inline'",
            "font-src 'self' data:",
            f"img-src 'self' data: https: blob: {GA_IMG_HOSTS}",
            (
                "connect-src 'self' ws: wss: http://localhost:* http://127.0.0.1:* "
                f"{GA_CONNECT_HOSTS}"
            ),
            "frame-src 'self'",
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
            "payment=(), "
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
