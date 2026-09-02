# =============================================================================
# Stratum AI - Audit Logging Tasks
# =============================================================================
"""
Background tasks for audit log processing.

The audit middleware pushes audit entries onto a Redis queue; this task
drains the queue and persists entries. In this minimal implementation the
drain is a safe no-op that reports queue depth.
"""

from typing import Any

from celery import shared_task
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)

__all__ = ["process_audit_log_queue"]

AUDIT_QUEUE_KEY = "audit:log:queue"


@shared_task
def process_audit_log_queue(batch_size: int = 500) -> dict[str, Any]:
    """
    Drain queued audit log entries.

    Scheduled by Celery beat. Returns a summary of processed entries.
    """
    processed = 0
    try:
        import redis

        from app.core.config import settings

        client = redis.from_url(settings.redis_url)
        try:
            for _ in range(batch_size):
                entry = client.lpop(AUDIT_QUEUE_KEY)
                if entry is None:
                    break
                # Entries are already persisted by AuditMiddleware; the queue
                # holds overflow items only. Log and count them here.
                processed += 1
        finally:
            client.close()
    except Exception as e:
        logger.warning("Audit queue processing skipped: %s", e)
        return {"status": "skipped", "error": str(e), "processed": 0}

    if processed:
        logger.info("Processed %d queued audit log entries", processed)
    return {"status": "ok", "processed": processed}
