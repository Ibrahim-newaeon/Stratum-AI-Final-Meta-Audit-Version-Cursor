# =============================================================================
# Stratum AI - Prometheus Metrics
# =============================================================================
"""
Prometheus metrics instrumentation for the FastAPI application.

Uses a lightweight custom middleware built on prometheus_client. The route
template is read from ``request.scope["route"]`` after routing, so it never
iterates ``app.routes`` (which may contain deferred routers in newer FastAPI).
Metric objects are created once per process (module level) so that building
the app more than once - as the test suite does - does not re-register them.
Falls back to a no-op when prometheus_client is unavailable.
"""

import time
from typing import Any

from fastapi import FastAPI, Request

from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["setup_metrics"]

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Histogram,
        generate_latest,
    )

    _REQUESTS_TOTAL: Any = Counter(
        "http_requests_total",
        "Total HTTP requests",
        ["method", "handler", "status"],
    )
    _REQUEST_DURATION: Any = Histogram(
        "http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "handler"],
    )
    _AVAILABLE = True
except ImportError:  # pragma: no cover - prometheus_client not installed
    _REQUESTS_TOTAL = None
    _REQUEST_DURATION = None
    _AVAILABLE = False


def setup_metrics(app: FastAPI) -> Any:
    """
    Instrument the FastAPI app with Prometheus metrics and expose /metrics.

    Safe to call once per app instance; metric registration is process-wide.
    Returns None (kept for call-site compatibility).
    """
    if not _AVAILABLE:
        logger.warning("prometheus_client_unavailable", fallback="noop")
        return None

    from starlette.responses import Response

    @app.middleware("http")
    async def _prometheus_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        handler = getattr(route, "path", None) or request.url.path
        if handler != "/metrics" and not handler.startswith("/health"):
            _REQUESTS_TOTAL.labels(request.method, handler, str(response.status_code)).inc()
            _REQUEST_DURATION.labels(request.method, handler).observe(time.perf_counter() - start)
        return response

    @app.get("/metrics", include_in_schema=False)
    async def _metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    logger.info("prometheus_metrics_ready", endpoint="/metrics")
    return None
