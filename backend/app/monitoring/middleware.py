# =============================================================================
# Stratum AI - Memory Profiling Middleware
# =============================================================================
"""
Per-endpoint memory profiling middleware.

Tracks the process RSS delta produced by each request so that hot,
memory-hungry endpoints can be identified via /debug/memory/endpoints.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

__all__ = ["MemoryProfilingMiddleware"]

_KB = 1024


class _EndpointStats:
    """Aggregated memory stats for one endpoint."""

    __slots__ = ("count", "max_rss_delta_kb", "total_duration_ms", "total_rss_delta_kb")

    def __init__(self) -> None:
        self.count = 0
        self.total_rss_delta_kb = 0.0
        self.max_rss_delta_kb = 0.0
        self.total_duration_ms = 0.0


class MemoryProfilingMiddleware(BaseHTTPMiddleware):
    """Tracks per-endpoint RSS deltas. Safe no-op when psutil is missing."""

    MAX_RECENT = 200

    def __init__(self, app: Any, enabled: bool = True) -> None:
        super().__init__(app)
        self.enabled = enabled and psutil is not None
        self._process = psutil.Process(os.getpid()) if psutil else None
        self._lock = threading.Lock()
        self._stats: dict[str, _EndpointStats] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=self.MAX_RECENT)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        """Measure RSS before/after the request and record the delta."""
        if not self.enabled or self._process is None:
            return await call_next(request)

        rss_before = self._safe_rss()
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000
        rss_after = self._safe_rss()

        key = f"{request.method} {request.url.path}"
        delta_kb = (rss_after - rss_before) / _KB

        with self._lock:
            stats = self._stats.setdefault(key, _EndpointStats())
            stats.count += 1
            stats.total_rss_delta_kb += delta_kb
            stats.max_rss_delta_kb = max(stats.max_rss_delta_kb, delta_kb)
            stats.total_duration_ms += duration_ms
            self._recent.append(
                {
                    "endpoint": key,
                    "timestamp": time.time(),
                    "rss_delta_kb": round(delta_kb, 2),
                    "duration_ms": round(duration_ms, 2),
                    "status_code": response.status_code,
                }
            )

        return response

    def _safe_rss(self) -> int:
        """Get RSS bytes, returning 0 on failure."""
        try:
            return self._process.memory_info().rss if self._process else 0
        except Exception:  # pragma: no cover
            return 0

    # -------------------------------------------------------------------
    # Reporting API (used by /debug/memory endpoints)
    # -------------------------------------------------------------------

    def _rows(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "endpoint": key,
                    "count": s.count,
                    "avg_rss_delta_kb": round(s.total_rss_delta_kb / max(s.count, 1), 2),
                    "total_rss_delta_kb": round(s.total_rss_delta_kb, 2),
                    "max_rss_delta_kb": round(s.max_rss_delta_kb, 2),
                    "avg_duration_ms": round(s.total_duration_ms / max(s.count, 1), 2),
                }
                for key, s in self._stats.items()
            ]

    def get_summary(self) -> dict[str, Any]:
        """Get overall profiling summary."""
        rows = self._rows()
        return {
            "enabled": self.enabled,
            "endpoints_tracked": len(rows),
            "total_requests": sum(r["count"] for r in rows),
            "total_rss_delta_kb": round(sum(r["total_rss_delta_kb"] for r in rows), 2),
        }

    def get_endpoint_stats(
        self, sort_by: str = "avg_rss_delta_kb", limit: int = 50
    ) -> list[dict[str, Any]]:
        """Get per-endpoint stats sorted by the given metric."""
        rows = self._rows()
        rows.sort(key=lambda r: r.get(sort_by, 0) or 0, reverse=True)
        return rows[:limit]

    def get_growing_endpoints(self, threshold_kb: float = 256.0) -> list[dict[str, Any]]:
        """Get endpoints whose average RSS delta suggests memory growth."""
        return [
            r
            for r in self._rows()
            if r["avg_rss_delta_kb"] >= threshold_kb and r["count"] >= 3
        ]

    def get_top_consumers(self, limit: int = 15) -> list[dict[str, Any]]:
        """Get endpoints with the largest total RSS growth."""
        return self.get_endpoint_stats(sort_by="total_rss_delta_kb", limit=limit)

    def get_recent_requests(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get the most recent profiled requests."""
        with self._lock:
            return list(self._recent)[-limit:]

    def reset_stats(self) -> None:
        """Clear all collected profiling data."""
        with self._lock:
            self._stats.clear()
            self._recent.clear()
