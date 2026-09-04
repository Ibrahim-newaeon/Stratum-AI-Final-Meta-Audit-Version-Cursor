# =============================================================================
# Stratum AI - API Router Authentication Guard
# =============================================================================
"""
Router-level authentication guard for the versioned API.

Defense in depth: ``TenantMiddleware`` populates the request identity, but a
middleware is a single point of failure - when it accepted a forged
``X-Tenant-ID`` header every endpoint module that had simply omitted
``Depends(get_current_user)`` became unauthenticated. This guard closes that by
requiring a verified identity for every route on ``api_router``, so an endpoint
module cannot opt out of authentication by omission.

The guard is deliberately cheap: it only reads ``request.state`` (already filled
in by the middleware from one signature-verified access token) and never touches
the database, so it adds no query to any request. Endpoints that need the user
record keep using ``Depends(get_current_user)`` on top of it.
"""

from fastapi import HTTPException, Request, status

from app.middleware.tenant import is_public_endpoint

# Platform-wide role that legitimately operates without any tenant context.
SUPERADMIN_ROLE = "superadmin"


def require_authenticated_request(request: Request) -> None:
    """
    Reject any request to a non-public API route that carries no verified identity.

    ``request.state.user_id`` is only set from the ``sub`` claim of a
    signature-verified access token, so its presence proves the caller
    authenticated. Superadmins are additionally accepted on role alone because
    they operate without a tenant context.

    Args:
        request: Incoming request, already processed by ``TenantMiddleware``

    Raises:
        HTTPException: 401 when the request has no verified identity
    """
    # Public routes (login, register, key-authenticated CDP ingest, the
    # signature-verified Paddle webhook, the onboarding agent) are exempt via
    # the same predicate the middleware uses, so the two can never disagree.
    if is_public_endpoint(request.url.path):
        return

    if getattr(request.state, "user_id", None) is not None:
        return

    if getattr(request.state, "role", None) == SUPERADMIN_ROLE:
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_superadmin_request(request: Request) -> None:
    """
    Reject any caller that is not the platform role.

    Used for surfaces that are inherently cross-tenant and therefore belong to
    the platform operator, not to a customer: the WebSocket connection
    statistics (which enumerate tenant ids) and the memory-profiling debug
    router. Both live outside ``api_router`` and would otherwise carry no
    dependency at all.

    Args:
        request: Incoming request, already processed by ``TenantMiddleware``

    Raises:
        HTTPException: 401 without a verified identity, 403 for any other role
    """
    require_authenticated_request(request)

    if getattr(request.state, "role", None) != SUPERADMIN_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform admin access required",
        )
