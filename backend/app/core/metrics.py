# =============================================================================
# Stratum AI - Prometheus Metrics
# =============================================================================
"""
Prometheus metrics instrumentation for the FastAPI application.

Uses a lightweight custom middleware built on prometheus_client. The route
template is read from ``request.scope["route"]`` after routing, so it never
iterates ``app.routes`` (which may contain deferred routers in newer FastAPI).
Falls back to a no-op when prometheus_client is unavailable.
"""

import time
from typing import Any

from fastapi import FastAPI, Request

from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["setup_metrics"]


def setup_metrics(app: FastAPI) -> Any:
    """
    Instrument the FastAPI app with Prometheus metrics and expose /metrics.

    Returns the metrics registry (or None when prometheus_client is missing).
    """
    try:
        from prometheus_client import (
            CONTENT_TYPE_LATEST,
            Counter,
            Histogram,
            generate_latest,
        )
    except ImportError:  # pragma: no cover - prometheus_client not installed
        logger.warning("prometheus_client_unavailable", fallback="noop")
        return None

    from starlette.responses import Response

    requests_total = Counter(
        "http_requests_total",
        "Total HTTP requests",
        ["method", "handler", "status"],
    )
    request_duration = Histogram(
        "http_request_duration_seconds",
        "HTTP request duration in seconds",
        ["method", "handler"],
    )

    @app.middleware("http")
    async def _prometheus_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        start = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        handler = getattr(route, "path", None) or request.url.path
        if handler not in ("/metrics",) and not handler.startswith("/health"):
            requests_total.labels(request.method, handler, str(response.status_code)).inc()
            request_duration.labels(request.method, handler).observe(time.perf_counter() - start)
        return response

    @app.get("/metrics", include_in_schema=False)
    async def _metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    logger.info("prometheus_metrics_ready", endpoint="/metrics")
    return None
