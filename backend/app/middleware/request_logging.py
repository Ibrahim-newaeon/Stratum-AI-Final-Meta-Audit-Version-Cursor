# =============================================================================
# Stratum AI - Request Logging Middleware
# =============================================================================
"""
Structured request logging middleware.

Assigns a request ID to every request, binds it to structlog context vars,
measures request duration, and emits a structured access log entry.
"""

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)

__all__ = ["RequestLoggingMiddleware"]


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware that adds request IDs, timing, and structured access logs."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Process the request, logging start/end with a bound request ID."""
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id

        structlog.contextvars.bind_contextvars(request_id=request_id)
        start_time = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start_time) * 1000
            logger.error(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration_ms, 2),
            )
            structlog.contextvars.unbind_contextvars("request_id")
            raise

        duration_ms = (time.perf_counter() - start_time) * 1000
        response.headers["X-Request-ID"] = request_id

        # Skip noisy health-check access logs
        if not request.url.path.startswith("/health"):
            logger.info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )

        structlog.contextvars.unbind_contextvars("request_id")
        return response
