# =============================================================================
# Stratum AI - Memory Auditor
# =============================================================================
"""
Lightweight process memory auditing.

Combines psutil process stats with tracemalloc allocation tracking to power
the /debug/memory/* endpoints. Designed to be safe in any environment: all
collection methods degrade to empty results rather than raising.
"""

from __future__ import annotations

import gc
import os
import time
import tracemalloc
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.logging import get_logger

logger = get_logger(__name__)

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

__all__ = ["MemoryAuditor"]

_MB = 1024 * 1024
_KB = 1024


@dataclass
class ProcessInfo:
    """Snapshot of process-level resource usage."""

    pid: int
    rss_mb: float = 0.0
    vms_mb: float = 0.0
    percent: float = 0.0
    num_threads: int = 0
    num_fds: int = 0
    cpu_percent: float = 0.0
    open_files_count: int = 0
    connections_count: int = 0


@dataclass
class AllocationInfo:
    """A single tracemalloc allocation group."""

    file: str
    line: int
    size_kb: float
    count: int
    code_line: str = ""


@dataclass
class ObjectStat:
    """Count/size statistics for a Python object type."""

    type_name: str
    count: int
    size_kb: float


@dataclass
class MemorySnapshot:
    """A point-in-time memory snapshot for later diffing."""

    label: str
    timestamp: float
    rss_mb: float
    tracemalloc_current_kb: float
    tm_snapshot: Optional[Any] = None


@dataclass
class SnapshotDiff:
    """Difference between two memory snapshots."""

    timestamp_start: float
    timestamp_end: float
    duration_seconds: float
    rss_delta_mb: float
    tracemalloc_delta_kb: float
    new_allocations: list[AllocationInfo] = field(default_factory=list)
    freed_allocations: list[AllocationInfo] = field(default_factory=list)
    grown_types: list[dict[str, Any]] = field(default_factory=list)


class MemoryAuditor:
    """Collects process memory metrics and tracemalloc snapshots."""

    def __init__(self) -> None:
        self.is_tracking: bool = False
        self.snapshots: list[MemorySnapshot] = []
        self.timeline: list[dict[str, Any]] = []
        self._process = psutil.Process(os.getpid()) if psutil else None

    # -------------------------------------------------------------------
    # Tracking lifecycle
    # -------------------------------------------------------------------

    def start_tracking(self) -> None:
        """Start tracemalloc-based allocation tracking."""
        if not tracemalloc.is_tracing():
            try:
                tracemalloc.start(10)
            except Exception as e:  # pragma: no cover
                logger.warning("tracemalloc_start_failed", error=str(e))
                return
        self.is_tracking = True
        logger.info("memory_tracking_started")

    def stop_tracking(self) -> None:
        """Stop tracemalloc tracking."""
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        self.is_tracking = False
        logger.info("memory_tracking_stopped")

    # -------------------------------------------------------------------
    # Process / system stats
    # -------------------------------------------------------------------

    def get_process_info(self) -> ProcessInfo:
        """Get current process resource usage."""
        if self._process is None:
            return ProcessInfo(pid=os.getpid())
        try:
            mem = self._process.memory_info()
            info = ProcessInfo(
                pid=self._process.pid,
                rss_mb=round(mem.rss / _MB, 2),
                vms_mb=round(mem.vms / _MB, 2),
                percent=round(self._process.memory_percent(), 2),
                num_threads=self._process.num_threads(),
                cpu_percent=self._process.cpu_percent(interval=None),
            )
            try:
                info.num_fds = self._process.num_fds()
            except Exception:
                info.num_fds = 0
            try:
                info.open_files_count = len(self._process.open_files())
            except Exception:
                info.open_files_count = 0
            try:
                info.connections_count = len(self._process.net_connections())
            except Exception:
                info.connections_count = 0
            return info
        except Exception as e:  # pragma: no cover
            logger.warning("process_info_failed", error=str(e))
            return ProcessInfo(pid=os.getpid())

    def get_system_memory(self) -> dict[str, Any]:
        """Get system-wide virtual memory statistics."""
        if psutil is None:
            return {}
        try:
            vm = psutil.virtual_memory()
            return {
                "total_mb": round(vm.total / _MB, 2),
                "available_mb": round(vm.available / _MB, 2),
                "used_mb": round(vm.used / _MB, 2),
                "percent_used": vm.percent,
            }
        except Exception:  # pragma: no cover
            return {}

    def get_child_processes(self) -> list[dict[str, Any]]:
        """List child processes with their RSS usage."""
        if self._process is None:
            return []
        children = []
        try:
            for child in self._process.children(recursive=True):
                try:
                    children.append(
                        {
                            "pid": child.pid,
                            "name": child.name(),
                            "rss_mb": round(child.memory_info().rss / _MB, 2),
                        }
                    )
                except Exception:
                    continue
        except Exception:  # pragma: no cover
            pass
        return children

    # -------------------------------------------------------------------
    # Tracemalloc stats
    # -------------------------------------------------------------------

    def get_tracemalloc_stats(self) -> dict[str, Any]:
        """Get current and peak traced memory from tracemalloc."""
        if not tracemalloc.is_tracing():
            return {"tracing": False, "current_kb": 0.0, "peak_kb": 0.0}
        current, peak = tracemalloc.get_traced_memory()
        return {
            "tracing": True,
            "current_kb": round(current / _KB, 2),
            "peak_kb": round(peak / _KB, 2),
        }

    def get_top_allocations(
        self, limit: int = 30, key_type: str = "lineno"
    ) -> list[AllocationInfo]:
        """Get the top memory allocations grouped by the given key."""
        if not tracemalloc.is_tracing():
            return []
        try:
            snapshot = tracemalloc.take_snapshot()
            stats = snapshot.statistics(key_type if key_type != "traceback" else "traceback")
        except Exception:  # pragma: no cover
            return []

        allocations = []
        for stat in stats[:limit]:
            frame = stat.traceback[0] if stat.traceback else None
            allocations.append(
                AllocationInfo(
                    file=frame.filename if frame else "unknown",
                    line=frame.lineno if frame else 0,
                    size_kb=round(stat.size / _KB, 2),
                    count=stat.count,
                )
            )
        return allocations

    def get_top_files(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get files responsible for the most allocated memory."""
        if not tracemalloc.is_tracing():
            return []
        try:
            snapshot = tracemalloc.take_snapshot()
            stats = snapshot.statistics("filename")
        except Exception:  # pragma: no cover
            return []
        return [
            {
                "file": stat.traceback[0].filename if stat.traceback else "unknown",
                "size_kb": round(stat.size / _KB, 2),
                "count": stat.count,
            }
            for stat in stats[:limit]
        ]

    # -------------------------------------------------------------------
    # Object / GC stats
    # -------------------------------------------------------------------

    def get_object_stats(self, limit: int = 30) -> list[ObjectStat]:
        """Get counts of the most common Python object types."""
        import sys
        from collections import Counter

        counts: Counter[str] = Counter()
        sizes: dict[str, int] = {}
        for obj in gc.get_objects():
            type_name = type(obj).__name__
            counts[type_name] += 1
            try:
                sizes[type_name] = sizes.get(type_name, 0) + sys.getsizeof(obj)
            except Exception:
                continue

        return [
            ObjectStat(
                type_name=name,
                count=count,
                size_kb=round(sizes.get(name, 0) / _KB, 2),
            )
            for name, count in counts.most_common(limit)
        ]

    def detect_reference_cycles(self, limit: int = 10) -> list[dict[str, Any]]:
        """Report uncollectable garbage objects (potential reference cycles)."""
        return [
            {"type": type(obj).__name__, "repr": repr(obj)[:120]}
            for obj in gc.garbage[:limit]
        ]

    def get_gc_stats(self) -> dict[str, Any]:
        """Get garbage collector generation statistics."""
        return {
            "counts": gc.get_count(),
            "thresholds": gc.get_threshold(),
            "stats": gc.get_stats(),
            "garbage_count": len(gc.garbage),
        }

    def force_gc(self) -> dict[str, Any]:
        """Force a full garbage collection pass."""
        before = self.get_process_info().rss_mb
        collected = gc.collect()
        after = self.get_process_info().rss_mb
        return {
            "collected_objects": collected,
            "rss_before_mb": before,
            "rss_after_mb": after,
            "rss_freed_mb": round(before - after, 2),
        }

    # -------------------------------------------------------------------
    # Timeline & snapshots
    # -------------------------------------------------------------------

    def record_point(self, label: str = "") -> None:
        """Record a point on the memory timeline."""
        info = self.get_process_info()
        self.timeline.append(
            {
                "timestamp": time.time(),
                "label": label,
                "rss_mb": info.rss_mb,
            }
        )

    def take_snapshot(self, label: str = "") -> MemorySnapshot:
        """Take a memory snapshot for later diffing."""
        tm = self.get_tracemalloc_stats()
        tm_snapshot = None
        if tracemalloc.is_tracing():
            try:
                tm_snapshot = tracemalloc.take_snapshot()
            except Exception:  # pragma: no cover
                tm_snapshot = None

        snapshot = MemorySnapshot(
            label=label,
            timestamp=time.time(),
            rss_mb=self.get_process_info().rss_mb,
            tracemalloc_current_kb=tm.get("current_kb", 0.0),
            tm_snapshot=tm_snapshot,
        )
        self.snapshots.append(snapshot)
        self.record_point(label=f"snapshot:{label}" if label else "snapshot")
        return snapshot

    def diff_snapshots(self) -> Optional[SnapshotDiff]:
        """Diff the last two snapshots; returns None if fewer than 2 exist."""
        if len(self.snapshots) < 2:
            return None

        older, newer = self.snapshots[-2], self.snapshots[-1]
        diff = SnapshotDiff(
            timestamp_start=older.timestamp,
            timestamp_end=newer.timestamp,
            duration_seconds=round(newer.timestamp - older.timestamp, 2),
            rss_delta_mb=round(newer.rss_mb - older.rss_mb, 2),
            tracemalloc_delta_kb=round(
                newer.tracemalloc_current_kb - older.tracemalloc_current_kb, 2
            ),
        )

        if older.tm_snapshot is not None and newer.tm_snapshot is not None:
            try:
                stats = newer.tm_snapshot.compare_to(older.tm_snapshot, "lineno")
                for stat in stats[:20]:
                    frame = stat.traceback[0] if stat.traceback else None
                    alloc = AllocationInfo(
                        file=frame.filename if frame else "unknown",
                        line=frame.lineno if frame else 0,
                        size_kb=round(stat.size_diff / _KB, 2),
                        count=stat.count_diff,
                    )
                    if stat.size_diff > 0:
                        diff.new_allocations.append(alloc)
                    elif stat.size_diff < 0:
                        diff.freed_allocations.append(alloc)
            except Exception:  # pragma: no cover
                pass

        return diff

    # -------------------------------------------------------------------
    # Full audit
    # -------------------------------------------------------------------

    def full_audit(self) -> dict[str, Any]:
        """Run a full audit and return JSON-serializable data."""
        info = self.get_process_info()
        return {
            "generated_at": time.time(),
            "process": {
                "pid": info.pid,
                "rss_mb": info.rss_mb,
                "vms_mb": info.vms_mb,
                "memory_percent": info.percent,
                "num_threads": info.num_threads,
                "cpu_percent": info.cpu_percent,
            },
            "system": self.get_system_memory(),
            "tracemalloc": self.get_tracemalloc_stats(),
            "top_allocations": [
                {
                    "file": a.file,
                    "line": a.line,
                    "size_kb": a.size_kb,
                    "count": a.count,
                }
                for a in self.get_top_allocations(limit=20)
            ],
            "top_files": self.get_top_files(limit=15),
            "gc": self.get_gc_stats(),
            "timeline": self.timeline[-200:],
            "snapshots": [
                {
                    "label": s.label,
                    "timestamp": s.timestamp,
                    "rss_mb": s.rss_mb,
                    "tracemalloc_current_kb": s.tracemalloc_current_kb,
                }
                for s in self.snapshots
            ],
        }
