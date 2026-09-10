# =============================================================================
# Stratum AI - Celery Application Configuration
# =============================================================================
"""
Celery application setup with Redis broker and result backend.
Includes beat schedule for periodic tasks.

Security: All beat-scheduled tasks use distributed locks to prevent
duplicate execution across multiple workers.
"""

import functools
import logging
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

import redis
from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

logger = logging.getLogger("stratum.workers.celery")


# =============================================================================
# Distributed Lock for Beat-Scheduled Tasks
# =============================================================================


class DistributedLock:
    """
    Redis-based distributed lock to prevent duplicate task execution.

    When multiple Celery workers are running with beat scheduler,
    this lock ensures only one worker executes a scheduled task.
    """

    def __init__(self, redis_url: str = None):
        self.redis_url = redis_url or settings.redis_url
        self._redis_client = None

    @property
    def redis_client(self) -> redis.Redis:
        """Lazy initialization of Redis client."""
        if self._redis_client is None:
            self._redis_client = redis.from_url(self.redis_url)
        return self._redis_client

    @contextmanager
    def acquire(self, lock_name: str, timeout: int = 3600, blocking: bool = False):
        """
        Acquire a distributed lock.

        Args:
            lock_name: Unique name for the lock (usually task name)
            timeout: Lock expiration in seconds (default 1 hour)
            blocking: Whether to wait for lock (default False - return immediately)

        Yields:
            bool: True if lock was acquired, False otherwise
        """
        lock_key = f"celery:lock:{lock_name}"
        lock = self.redis_client.lock(lock_key, timeout=timeout, blocking=blocking)

        acquired = False
        try:
            acquired = lock.acquire(blocking=blocking)
            yield acquired
        finally:
            if acquired:
                try:
                    lock.release()
                except redis.exceptions.LockNotOwnedError:
                    # Lock expired or was released by another process
                    logger.warning(f"Lock {lock_name} was not owned when releasing")


# Global lock instance
_distributed_lock = DistributedLock()


def with_distributed_lock(
    lock_name: str = None,
    timeout: int = 3600,
    skip_if_locked: bool = True
) -> Callable:
    """
    Decorator to ensure only one instance of a task runs across all workers.

    Args:
        lock_name: Name of the lock (defaults to task name)
        timeout: Lock timeout in seconds (default 1 hour)
        skip_if_locked: If True, skip task silently when locked. If False, raise exception.

    Usage:
        @celery_app.task
        @with_distributed_lock(timeout=1800)
        def my_scheduled_task():
            # Only one worker will execute this
            pass
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            name = lock_name or f"{func.__module__}.{func.__name__}"
            with _distributed_lock.acquire(name, timeout=timeout) as acquired:
                if not acquired:
                    if skip_if_locked:
                        logger.info(
                            f"Task {name} skipped - already running on another worker"
                        )
                        return {"status": "skipped", "reason": "lock_held"}
                    else:
                        raise RuntimeError(f"Could not acquire lock for task {name}")
                return func(*args, **kwargs)
        return wrapper
    return decorator

# =============================================================================
# Queue Topology
# =============================================================================
#
# Every task is routed to a named queue below. A worker only consumes the
# queues it is started with, so the routing table and the ``-Q`` list handed to
# the worker MUST stay in sync: a route to a queue nobody consumes means the
# task is accepted by the broker and then never executed. That is exactly what
# happened before this module exported CELERY_QUEUES - the worker ran with no
# ``-Q`` at all, so it consumed only Celery's built-in "celery" queue while
# beat kept publishing to sync/rules/intel/ml/cdp/default.
#
# CELERY_QUEUES is derived from TASK_ROUTES and BEAT_SCHEDULE rather than
# written out by hand, so a new route cannot silently go unconsumed.
# docker-entrypoint.sh reads it at start-up; the compose files repeat it
# literally and tests/unit/test_worker_queue_coverage.py fails when any of
# them drifts.

# Queue for tasks that match no route (Celery's task_default_queue).
DEFAULT_QUEUE = "default"

# Celery's own built-in default queue name. Anything enqueued by a client that
# does not know our routing table lands here, so the worker must drain it too.
CELERY_BUILTIN_QUEUE = "celery"

# Task routing (organized by domain module)
TASK_ROUTES: dict[str, dict[str, str]] = {
    # Sync tasks
    "app.workers.tasks.sync.sync_campaign_data": {"queue": "sync"},
    "app.workers.tasks.sync.sync_all_campaigns": {"queue": "sync"},
    "app.workers.tasks.sync.discover_tenant_campaigns_task": {"queue": "sync"},
    "app.workers.tasks.sync.discover_all_campaigns": {"queue": "sync"},
    # Measurement & Verification (GA4 read-only baseline pull)
    "app.workers.tasks.measurement.*": {"queue": "sync"},
    # Trust Layer rollups
    "tasks.signal_health_rollup": {"queue": "sync"},
    "tasks.attribution_variance_rollup": {"queue": "sync"},
    # Autopilot execution. Routed to an already-consumed queue on purpose: the
    # worker -Q list in docker-compose*.yml is written out by hand, so a new
    # queue name would be consumed by the container entrypoint (which derives
    # it from CELERY_QUEUES) and silently not by the compose stacks.
    "tasks.apply_actions_queue": {"queue": "sync"},
    # Rules tasks
    "app.workers.tasks.rules.evaluate_rules": {"queue": "rules"},
    "app.workers.tasks.rules.evaluate_all_rules": {"queue": "rules"},
    # Competitor tasks
    "app.workers.tasks.competitors.fetch_competitor_data": {"queue": "intel"},
    "app.workers.tasks.competitors.refresh_all_competitors": {"queue": "intel"},
    # ML tasks
    "app.workers.tasks.ml.*": {"queue": "ml"},
    "app.workers.tasks.forecast.*": {"queue": "ml"},
    # CDP tasks
    "app.workers.tasks.cdp.*": {"queue": "cdp"},
    # CMS tasks
    "app.workers.tasks.cms.*": {"queue": DEFAULT_QUEUE},
    # WhatsApp tasks
    "app.workers.tasks.whatsapp.*": {"queue": DEFAULT_QUEUE},
}


def collect_queue_names(
    task_routes: dict[str, dict[str, str]],
    beat_schedule: dict[str, dict[str, Any]],
) -> tuple[str, ...]:
    """
    Derive the full set of queues a worker has to consume.

    Unions every queue named by a routing rule, every queue named in a beat
    entry's ``options``, the default queue for unrouted tasks and Celery's
    built-in queue name.

    Args:
        task_routes: The ``task_routes`` mapping handed to Celery
        beat_schedule: The ``beat_schedule`` mapping handed to Celery beat

    Returns:
        Sorted tuple of queue names, safe to pass to ``celery worker -Q``
    """
    names: set[str] = {DEFAULT_QUEUE, CELERY_BUILTIN_QUEUE}

    for route in task_routes.values():
        queue = route.get("queue")
        if queue:
            names.add(queue)

    for entry in beat_schedule.values():
        queue = (entry.get("options") or {}).get("queue")
        if queue:
            names.add(queue)

    return tuple(sorted(names))


# Create Celery app
celery_app = Celery(
    "stratum_ai",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks.sync",
        "app.workers.tasks.rules",
        "app.workers.tasks.competitors",
        "app.workers.tasks.forecast",
        "app.workers.tasks.creative",
        "app.workers.tasks.audit",
        "app.workers.tasks.whatsapp",
        "app.workers.tasks.ml",
        "app.workers.tasks.billing",
        "app.workers.tasks.monitoring",
        "app.workers.tasks.scores",
        "app.workers.tasks.cdp",
        "app.workers.tasks.cms",
        # Measurement & Verification (GA4 read-only baseline) + Trust Layer rollups
        "app.workers.tasks.measurement",
        "app.tasks.attribution_variance_rollup",
        "app.tasks.signal_health_rollup",
        "app.tasks.apply_actions_queue",
    ],
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Task execution settings
    task_acks_late=True,  # Acknowledge after task completes (for reliability)
    task_reject_on_worker_lost=True,
    task_track_started=True,
    # Retry settings
    task_default_retry_delay=60,  # 1 minute
    task_max_retries=3,
    # Worker settings
    worker_prefetch_multiplier=1,  # One task at a time for memory efficiency
    worker_concurrency=4,
    # Memory safety guards (per memory audit Feb 2026)
    worker_max_memory_per_child=400_000,  # 400MB - restart worker after this limit
    worker_max_tasks_per_child=1000,  # Recycle worker after 1000 tasks to prevent fragmentation
    # Result settings
    result_expires=3600,  # Results expire after 1 hour (was 24h - reduced per memory audit)
    # Task routing (organized by domain module). Declared above so the
    # queue list handed to the worker can be derived from it.
    task_routes=TASK_ROUTES,
    # Anything that matches no route goes to a queue the worker consumes,
    # never to Celery's implicit "celery" queue by accident.
    task_default_queue=DEFAULT_QUEUE,
    # Task time limits
    task_time_limit=600,  # 10 minutes hard limit
    task_soft_time_limit=540,  # 9 minutes soft limit (for graceful shutdown)
)

# Beat schedule for periodic tasks (using new modular task paths)
BEAT_SCHEDULE: dict[str, dict[str, Any]] = {
    # ==========================================================================
    # Rules Engine Tasks
    # ==========================================================================
    "evaluate-active-rules": {
        "task": "app.workers.tasks.rules.evaluate_all_rules",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "rules"},
    },
    # ==========================================================================
    # Data Sync Tasks
    # ==========================================================================
    # Catalogue discovery runs shortly before the hourly insights pull so newly
    # listed Meta campaigns exist as local Campaign rows (with external_id)
    # before sync_campaign_data tries to attach metrics.
    "discover-all-campaigns": {
        "task": "app.workers.tasks.sync.discover_all_campaigns",
        "schedule": crontab(minute=50),
        "options": {"queue": "sync"},
    },
    "sync-all-campaigns": {
        "task": "app.workers.tasks.sync.sync_all_campaigns",
        "schedule": crontab(minute=0),
        "options": {"queue": "sync"},
    },
    # ==========================================================================
    # Measurement & Verification (GA4 read-only baseline) + Trust Layer rollups
    # ==========================================================================
    # Order matters: signal health (02:00) -> GA4 pull (02:30) -> attribution
    # variance (03:00) so the variance rollup compares against fresh GA4 rows.
    "trust-signal-health-rollup": {
        "task": "tasks.signal_health_rollup",
        "schedule": crontab(minute=0, hour=2),
        "options": {"queue": "sync"},
    },
    "measurement-ga4-daily-pull": {
        "task": "app.workers.tasks.measurement.pull_ga4_daily_baseline",
        "schedule": crontab(minute=30, hour=2),
        "options": {"queue": "sync"},
    },
    "trust-attribution-variance-rollup": {
        "task": "tasks.attribution_variance_rollup",
        "schedule": crontab(minute=0, hour=3),
        "options": {"queue": "sync"},
    },
    # Autopilot execution. Picks up actions an operator has already APPROVED;
    # it never approves anything itself.
    #
    # Being scheduled is NOT what turns writes on. The executor checks
    # autopilot_execution_enabled (default false) before it decrypts a token or
    # consults the trust gate, so on a default deployment every run refuses each
    # row with EXECUTION_DISABLED and issues no Meta request. Turning writes on
    # is still the two flags, deliberately, in that order:
    # AUTOPILOT_EXECUTION_ENABLED=true, then AUTOPILOT_EXECUTION_DRY_RUN=false.
    #
    # Five minutes matches the cadence tasks.schedule_apply_actions_queue was
    # written for. A shorter interval buys nothing: the trust gate reads the
    # daily signal-health snapshot, so only the approval backlog is fresher.
    # Overlap is harmless - a row moves to `applying` and commits before the
    # write, and the batch query does not select rows in that state.
    "autopilot-apply-actions-queue": {
        "task": "tasks.apply_actions_queue",
        "schedule": crontab(minute="*/5"),
        "options": {"queue": "sync"},
    },
    # ==========================================================================
    # Competitor Intelligence Tasks
    # ==========================================================================
    "refresh-competitor-data": {
        "task": "app.workers.tasks.competitors.refresh_all_competitors",
        "schedule": crontab(minute=0, hour="*/6"),
        "options": {"queue": "intel"},
    },
    # ==========================================================================
    # ML & Forecasting Tasks
    # ==========================================================================
    "generate-daily-forecasts": {
        "task": "app.workers.tasks.forecast.generate_daily_forecasts",
        "schedule": crontab(minute=0, hour=6),
        "options": {"queue": "ml"},
    },
    "run-all-predictions": {
        "task": "app.workers.tasks.ml.run_all_tenant_predictions",
        "schedule": crontab(minute="*/30"),
        "options": {"queue": "ml"},
    },
    # ==========================================================================
    # Creative & Scoring Tasks
    # ==========================================================================
    "calculate-fatigue-scores": {
        "task": "app.workers.tasks.creative.calculate_all_fatigue_scores",
        "schedule": crontab(minute=0, hour=3),
        "options": {"queue": DEFAULT_QUEUE},
    },
    "calculate-daily-scores": {
        "task": "app.workers.tasks.scores.calculate_daily_scores",
        "schedule": crontab(minute=0, hour=4),
        "options": {"queue": DEFAULT_QUEUE},
    },
    # ==========================================================================
    # Audit & Monitoring Tasks
    # ==========================================================================
    "process-audit-logs": {
        "task": "app.workers.tasks.audit.process_audit_log_queue",
        "schedule": crontab(minute="*"),
        "options": {"queue": DEFAULT_QUEUE},
    },
    "check-pipeline-health": {
        "task": "app.workers.tasks.monitoring.check_pipeline_health",
        "schedule": crontab(minute=30),
        "options": {"queue": DEFAULT_QUEUE},
    },
    # ==========================================================================
    # Billing & Usage Tasks
    # ==========================================================================
    "calculate-cost-allocation": {
        "task": "app.workers.tasks.billing.calculate_cost_allocation",
        "schedule": crontab(minute=0, hour=2),
        "options": {"queue": DEFAULT_QUEUE},
    },
    "calculate-usage-rollup": {
        "task": "app.workers.tasks.billing.calculate_usage_rollup",
        "schedule": crontab(minute=0, hour=1),
        "options": {"queue": DEFAULT_QUEUE},
    },
    # ==========================================================================
    # WhatsApp Tasks
    # ==========================================================================
    "process-scheduled-whatsapp": {
        "task": "app.workers.tasks.whatsapp.process_scheduled_whatsapp_messages",
        "schedule": crontab(minute="*"),
        "options": {"queue": DEFAULT_QUEUE},
    },
    # ==========================================================================
    # CDP (Customer Data Platform) Tasks
    # ==========================================================================
    "compute-cdp-segments": {
        "task": "app.workers.tasks.cdp.compute_all_cdp_segments",
        "schedule": crontab(minute=0),
        "options": {"queue": "cdp"},
    },
    "compute-cdp-funnels": {
        "task": "app.workers.tasks.cdp.compute_all_cdp_funnels",
        "schedule": crontab(minute=0, hour="*/2"),
        "options": {"queue": "cdp"},
    },
    # Auto-push due CDP → Meta Custom Audiences. PlatformAudience.next_sync_at
    # is set on create/manual sync; without this beat the column is advisory
    # only. Fifteen minutes matches the rules cadence and keeps Meta rate
    # limits calm when many tenants share one worker.
    "sync-due-audience-syncs": {
        "task": "app.workers.tasks.cdp.sync_due_audience_syncs",
        "schedule": crontab(minute="*/15"),
        "options": {"queue": "cdp"},
    },
    # ==========================================================================
    # CMS (Content Management System) Tasks
    # ==========================================================================
    "publish-scheduled-cms-posts": {
        "task": "app.workers.tasks.cms.publish_scheduled_cms_posts",
        "schedule": crontab(minute="*"),
        "options": {"queue": DEFAULT_QUEUE},
    },
}

celery_app.conf.beat_schedule = BEAT_SCHEDULE

# The exact queue list a worker must consume for every routed and every
# scheduled task to actually run. Read by docker-entrypoint.sh:
#   celery ... worker -Q "$(python -c '...print(",".join(CELERY_QUEUES))')"
CELERY_QUEUES: tuple[str, ...] = collect_queue_names(TASK_ROUTES, BEAT_SCHEDULE)


# Task decorators for common patterns
def retriable_task(**kwargs):
    """Decorator for tasks with exponential backoff retry."""
    default_kwargs = {
        "bind": True,
        "autoretry_for": (Exception,),
        "retry_backoff": True,
        "retry_backoff_max": 600,
        "retry_jitter": True,
        "max_retries": 3,
    }
    default_kwargs.update(kwargs)
    return celery_app.task(**default_kwargs)


def idempotent_task(**kwargs):
    """Decorator for idempotent tasks (safe to retry)."""
    kwargs.setdefault("acks_late", True)
    kwargs.setdefault("reject_on_worker_lost", True)
    return celery_app.task(**kwargs)


# =============================================================================
# Memory Profiling Hooks (connects to task signals for per-task tracking)
# =============================================================================

from app.monitoring.celery_hooks import CeleryMemoryHooks

celery_memory_hooks = CeleryMemoryHooks()
celery_memory_hooks.connect(celery_app)
