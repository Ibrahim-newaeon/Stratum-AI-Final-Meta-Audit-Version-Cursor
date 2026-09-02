# =============================================================================
# Stratum AI - Celery Memory Hooks
# =============================================================================
"""
Per-Celery-task memory tracking hooks.

Connects to Celery task signals to record the RSS delta of each task run.
Degrades to a safe no-op when psutil or Celery signals are unavailable.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

__all__ = ["CeleryMemoryHooks"]

_KB = 1024


class _TaskStats:
    """Aggregated memory stats for one task type."""

    __slots__ = ("count", "max_rss_delta_kb", "total_rss_delta_kb")

    def __init__(self) -> None:
        self.count = 0
        self.total_rss_delta_kb = 0.0
        self.max_rss_delta_kb = 0.0


class CeleryMemoryHooks:
    """Tracks per-task RSS deltas via Celery signals."""

    MAX_RECENT = 200

    def __init__(self) -> None:
        self.enabled = psutil is not None
        self._process = psutil.Process(os.getpid()) if psutil else None
        self._lock = threading.Lock()
        self._stats: dict[str, _TaskStats] = {}
        self._recent: deque[dict[str, Any]] = deque(maxlen=self.MAX_RECENT)
        self._inflight: dict[str, int] = {}

    def connect(self, celery_app: Any) -> None:
        """Connect memory tracking to Celery task signals (safe no-op on error)."""
        if not self.enabled:
            logger.info("celery_memory_hooks_disabled", reason="psutil unavailable")
            return
        try:
            from celery.signals import task_postrun, task_prerun

            task_prerun.connect(self._on_task_prerun, weak=False)
            task_postrun.connect(self._on_task_postrun, weak=False)
            logger.info("celery_memory_hooks_connected")
        except Exception as e:  # pragma: no cover
            self.enabled = False
            logger.warning("celery_memory_hooks_connect_failed", error=str(e))

    def _safe_rss(self) -> int:
        try:
            return self._process.memory_info().rss if self._process else 0
        except Exception:  # pragma: no cover
            return 0

    def _on_task_prerun(self, task_id: str = "", **kwargs: Any) -> None:
        with self._lock:
            self._inflight[task_id] = self._safe_rss()
            # Prevent unbounded growth if postrun signals are missed
            if len(self._inflight) > 10_000:
                self._inflight.clear()

    def _on_task_postrun(
        self, task_id: str = "", task: Any = None, **kwargs: Any
    ) -> None:
        rss_after = self._safe_rss()
        task_name = getattr(task, "name", "unknown")
        with self._lock:
            rss_before = self._inflight.pop(task_id, None)
            if rss_before is None:
                return
            delta_kb = (rss_after - rss_before) / _KB
            stats = self._stats.setdefault(task_name, _TaskStats())
            stats.count += 1
            stats.total_rss_delta_kb += delta_kb
            stats.max_rss_delta_kb = max(stats.max_rss_delta_kb, delta_kb)
            self._recent.append(
                {
                    "task": task_name,
                    "timestamp": time.time(),
                    "rss_delta_kb": round(delta_kb, 2),
                }
            )

    # -------------------------------------------------------------------
    # Reporting API
    # -------------------------------------------------------------------

    def _rows(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "task": name,
                    "count": s.count,
                    "avg_rss_delta_kb": round(s.total_rss_delta_kb / max(s.count, 1), 2),
                    "total_rss_delta_kb": round(s.total_rss_delta_kb, 2),
                    "max_rss_delta_kb": round(s.max_rss_delta_kb, 2),
                }
                for name, s in self._stats.items()
            ]

    def get_summary(self) -> dict[str, Any]:
        """Get overall task profiling summary."""
        rows = self._rows()
        return {
            "enabled": self.enabled,
            "tasks_tracked": len(rows),
            "total_runs": sum(r["count"] for r in rows),
            "total_rss_delta_kb": round(sum(r["total_rss_delta_kb"] for r in rows), 2),
        }

    def get_task_stats(
        self, sort_by: str = "avg_rss_delta_kb", limit: int = 50
    ) -> list[dict[str, Any]]:
        """Get per-task stats sorted by the given metric."""
        rows = self._rows()
        rows.sort(key=lambda r: r.get(sort_by, 0) or 0, reverse=True)
        return rows[:limit]

    def get_top_consumers(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get task types with the largest total RSS growth."""
        return self.get_task_stats(sort_by="total_rss_delta_kb", limit=limit)

    def get_leak_risks(self, threshold_kb: float = 512.0) -> list[dict[str, Any]]:
        """Get task types with consistently positive RSS deltas (leak candidates)."""
        return [
            r
            for r in self._rows()
            if r["avg_rss_delta_kb"] >= threshold_kb and r["count"] >= 3
        ]

    def reset_stats(self) -> None:
        """Clear all collected task profiling data."""
        with self._lock:
            self._stats.clear()
            self._recent.clear()
            self._inflight.clear()
