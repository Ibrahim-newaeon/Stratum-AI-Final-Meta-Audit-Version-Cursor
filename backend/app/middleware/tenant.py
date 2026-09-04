# =============================================================================
# Stratum AI - Multi-Tenant Middleware
# =============================================================================
"""
Middleware that extracts and validates tenant context from requests.
Implements Row-Level Security at the application level.

Tenant, user and role are derived from a single signature-verified JWT access
token. Request-supplied hints that any caller can forge - the ``X-Tenant-ID``
header and the request Host/subdomain - are never consulted for authentication.
"""

from collections.abc import Callable
from typing import Any

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# The ``type`` claim minted by app.core.security.create_access_token. Refresh
# tokens carry "refresh" and must never authenticate an API request.
ACCESS_TOKEN_TYPE = "access"

# Endpoints that don't require tenant context
PUBLIC_ENDPOINTS = {
    "/health",
    "/health/ready",
    "/health/live",
    "/docs",
    "/redoc",
    "/openapi.json",
    # Pre-authentication flows. None of them can carry an access token (the
    # caller is trying to obtain one, verify an address or reset a password)
    # and none of them read request.state.tenant_id.
    "/api/v1/auth/login",
    "/api/v1/auth/login/mfa",
    "/api/v1/auth/signup",
    "/api/v1/auth/refresh",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
    "/api/v1/auth/verify-email",
    "/api/v1/auth/resend-verification",
    "/api/v1/auth/whatsapp/send-otp",
    "/api/v1/auth/whatsapp/verify-otp",
    # Key-only CDP ingest for server-side GTM containers (Measurement & Verification).
    # Authenticates with X-Source-Key; the endpoint derives the tenant from the
    # CDP source itself and never reads request.state.tenant_id.
    "/api/v1/cdp/ingest",
    # Paddle Billing notifications. Public but signature-verified
    # (Paddle-Signature HMAC over the raw body); the handler resolves the tenant
    # from the event payload (custom_data.tenant_id / paddle_customer_id) and
    # never reads request.state.tenant_id.
    "/api/v1/webhooks/paddle",
    # Meta App Review privacy callbacks. Public because Meta calls them
    # server-to-server with no Authorization header, but not unauthenticated:
    # each POST carries a signed_request that app.services.meta.signed_request
    # verifies (HMAC-SHA256 over the raw payload, keyed with the app secret) in
    # constant time before anything is touched, and the handlers resolve the
    # tenant from the connection matching the signed Meta user id rather than
    # from request.state. The status page authenticates with the 128-bit
    # confirmation code itself and discloses only that one request's status.
    # App Review will not grant ads_read/ads_management without the first two.
    "/api/v1/meta/deauthorize",
    "/api/v1/meta/data-deletion",
    "/api/v1/meta/data-deletion/status",
    # Marketing site content. These are the endpoints cms.py documents as
    # "(public endpoint)": they are tenant-independent, read only rows with
    # status PUBLISHED and never touch request.state. They are reached from the
    # logged-out SPA routes /blog, /blog/:slug, /faq, /contact and the landing
    # pricing section, so they must answer without a token. Every CMS write and
    # admin route lives under /api/v1/cms/admin/... and stays authenticated.
    "/api/v1/cms/categories",
    "/api/v1/cms/tags",
    # Public writes, rate-limited per IP by RateLimitMiddleware: the marketing
    # contact form and the landing-page newsletter signup. Both store a lead and
    # read no tenant context.
    "/api/v1/cms/contact",
    "/api/v1/landing-cms/subscribe",
}

# Path prefixes served without an authenticated identity.
PUBLIC_ENDPOINT_PREFIXES = (
    "/docs",
    "/redoc",
    # Onboarding assistant works for visitors and signed-in users alike
    # (the endpoints take an optional user and never require tenant context)
    "/api/v1/onboarding-agent/",
    # Published blog posts: covers both /cms/posts and /cms/posts/{slug}. It
    # cannot widen to the admin surface, which lives under /api/v1/cms/admin/.
    "/api/v1/cms/posts",
)


def is_public_endpoint(path: str) -> bool:
    """
    Check whether a request path may be served without an authenticated identity.

    This is the single source of truth shared by the tenant middleware and the
    router-level authentication guard (``app.api.v1.guards``), so an endpoint
    can never be public for one and protected by the other.

    Args:
        path: Request path, e.g. ``/api/v1/campaigns``

    Returns:
        True when the path is public, False when it requires authentication
    """
    return path in PUBLIC_ENDPOINTS or path.startswith(PUBLIC_ENDPOINT_PREFIXES)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """
    Verify one bearer token and return its claims when it is an access token.

    The single place in the codebase that decides whether a token authenticates
    a caller: it checks the signature and expiry, and rejects any token whose
    ``type`` claim is not ``access`` (refresh tokens must never authenticate).
    Used by the tenant middleware for HTTP requests and by the ``/ws``
    handshake, so both apply exactly the same rules.

    Args:
        token: Raw JWT string, without the ``Bearer `` prefix

    Returns:
        The verified claims, or None when the token is missing, malformed,
        expired, wrongly signed or not an access token
    """
    if not token:
        return None

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return None

    # Refresh tokens (and any other token type) must not authenticate a request.
    if payload.get("type") != ACCESS_TOKEN_TYPE:
        logger.warning("rejected_non_access_token", token_type=payload.get("type"))
        return None

    return payload


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware that ensures tenant isolation for all requests.

    Tenant, user and role all come from the claims of one signature-verified
    JWT access token (``Authorization: Bearer <token>``), decoded once per
    request. Tokens whose ``type`` claim is not ``access`` (refresh tokens) are
    rejected, and unsigned request data - the ``X-Tenant-ID`` header, the Host
    subdomain - is ignored entirely.

    Sets request.state.tenant_id, request.state.user_id and request.state.role
    for downstream handlers.
    """

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Extract and validate tenant context."""

        # Skip public endpoints
        if self._is_public_endpoint(request.url.path):
            return await call_next(request)

        # One signature verification per request; every identity field below is
        # read from the same verified payload.
        claims = self._decode_access_token(request)

        tenant_id = self._extract_tenant_id(claims)
        user_id = self._extract_user_id(claims)
        role = self._extract_role(claims)

        if tenant_id is None:
            # Superadmins can operate without a specific tenant context
            if role == "superadmin":
                logger.debug("superadmin_no_tenant_context")
            # For development, use a default tenant for non-superadmin users.
            # Never outside development: dev_defaults_enabled requires APP_ENV
            # to be *explicitly* "development" (or an opt-in override), so an
            # unset or misspelled APP_ENV does not re-open this branch on a
            # deployed box, where a missing tenant context is a 401.
            elif settings.dev_defaults_enabled:
                tenant_id = 1
                logger.debug("using_default_tenant", tenant_id=tenant_id)
            else:
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={
                        "success": False,
                        "error": "Tenant context required",
                        "message": "Please provide a valid authentication token",
                    },
                )

        # Set tenant, user, and role context on request state
        request.state.tenant_id = tenant_id
        request.state.user_id = user_id
        request.state.role = role or "analyst"  # Default role if not in token

        # Bind to structured logging context
        import structlog

        structlog.contextvars.bind_contextvars(tenant_id=tenant_id, user_id=user_id, role=role)

        return await call_next(request)

    def _is_public_endpoint(self, path: str) -> bool:
        """Check if the endpoint is public."""
        return is_public_endpoint(path)

    def _decode_access_token(self, request: Request) -> dict[str, Any] | None:
        """
        Decode and verify the bearer access token carried by the request.

        The signature, expiry and the ``type`` claim are all checked here; this
        is the only place the token is decoded, and every identity attribute is
        read from the payload it returns.

        Args:
            request: Incoming request

        Returns:
            The verified JWT claims, or None when the request carries no bearer
            token, an invalid/expired one, or a token that is not an access token
        """
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return None

        return decode_access_token(auth_header.split(" ", 1)[1])

    def _extract_tenant_id(self, claims: dict[str, Any] | None) -> int | None:
        """
        Extract tenant_id from verified JWT claims.

        The signed token is the only source: a forgeable ``X-Tenant-ID`` header
        or request subdomain must never select a tenant.

        Args:
            claims: Verified access-token payload, or None

        Returns:
            The tenant id, or None when the token carries no usable tenant claim
        """
        if not claims:
            return None

        tenant_id = claims.get("tenant_id")
        if tenant_id is None:
            return None

        try:
            return int(tenant_id)
        except (TypeError, ValueError):
            return None

    def _extract_user_id(self, claims: dict[str, Any] | None) -> int | None:
        """
        Extract user_id from verified JWT claims.

        Args:
            claims: Verified access-token payload, or None

        Returns:
            The user id from the ``sub`` claim, or None
        """
        if not claims:
            return None

        sub = claims.get("sub")
        if sub is None:
            return None

        try:
            return int(sub)
        except (TypeError, ValueError):
            return None

    def _extract_role(self, claims: dict[str, Any] | None) -> str | None:
        """
        Extract role from verified JWT claims.

        Args:
            claims: Verified access-token payload, or None

        Returns:
            The role claim, or None
        """
        if not claims:
            return None

        role = claims.get("role")
        return role if isinstance(role, str) else None


class TenantContext:
    """
    Context manager for tenant-scoped database operations.
    Ensures all queries are filtered by tenant_id.
    """

    def __init__(self, tenant_id: int):
        self.tenant_id = tenant_id

    def filter_query(self, query, model):
        """Add tenant filter to a SQLAlchemy query."""
        if hasattr(model, "tenant_id"):
            return query.filter(model.tenant_id == self.tenant_id)
        return query

    def set_tenant_on_model(self, instance):
        """Set tenant_id on a model instance before insert."""
        if hasattr(instance, "tenant_id"):
            instance.tenant_id = self.tenant_id
        return instance


def get_tenant_context(request: Request) -> TenantContext:
    """
    FastAPI dependency to get the current tenant context.

    Usage:
        @router.get("/items")
        async def get_items(tenant: TenantContext = Depends(get_tenant_context)):
            ...
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context not found",
        )
    return TenantContext(tenant_id)
