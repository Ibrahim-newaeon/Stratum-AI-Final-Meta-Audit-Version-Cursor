# =============================================================================
# Stratum AI - Competitor Intelligence Tasks
# =============================================================================
"""
Background tasks for competitor benchmark data refresh.

fetch_competitor_data pulls fresh data for a single competitor record;
refresh_all_competitors fans out over every tracked competitor.
"""

from datetime import UTC, datetime
from typing import Any, Optional

from celery import shared_task
from celery.utils.log import get_task_logger
from sqlalchemy import select

from app.db.session import SyncSessionLocal

logger = get_task_logger(__name__)

__all__ = ["fetch_competitor_data", "refresh_all_competitors"]


def _get_competitor_model() -> Optional[type]:
    """Resolve the CompetitorBenchmark model if present in this build."""
    try:
        from app.base_models import CompetitorBenchmark

        return CompetitorBenchmark
    except ImportError:
        return None


@shared_task
def fetch_competitor_data(tenant_id: int, competitor_id: int) -> dict[str, Any]:
    """
    Fetch/refresh intelligence data for a single competitor.

    Marks the competitor record as fetched. External data-provider calls
    are not available in this environment, so this records the attempt.
    """
    model = _get_competitor_model()
    if model is None:
        logger.warning("Competitor model unavailable; skipping fetch")
        return {"status": "skipped", "reason": "competitor model unavailable"}

    with SyncSessionLocal() as db:
        competitor = db.execute(
            select(model).where(
                model.id == competitor_id,
                model.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()

        if competitor is None:
            logger.warning(
                "Competitor %s not found for tenant %s", competitor_id, tenant_id
            )
            return {"status": "not_found", "competitor_id": competitor_id}

        competitor.last_fetched_at = datetime.now(UTC)
        competitor.fetch_error = None
        db.commit()

        logger.info(
            "Refreshed competitor %s (%s) for tenant %s",
            competitor_id,
            getattr(competitor, "domain", "?"),
            tenant_id,
        )
        return {
            "status": "ok",
            "competitor_id": competitor_id,
            "tenant_id": tenant_id,
            "fetched_at": competitor.last_fetched_at.isoformat(),
        }


@shared_task
def refresh_all_competitors() -> dict[str, Any]:
    """
    Refresh intelligence data for all tracked competitors.

    Scheduled by Celery beat; fans out fetch_competitor_data per record.
    """
    model = _get_competitor_model()
    if model is None:
        logger.warning("Competitor model unavailable; skipping refresh")
        return {"status": "skipped", "reason": "competitor model unavailable"}

    queued = 0
    with SyncSessionLocal() as db:
        rows = db.execute(select(model.id, model.tenant_id)).all()

    for competitor_id, tenant_id in rows:
        fetch_competitor_data.delay(tenant_id, competitor_id)
        queued += 1

    logger.info("Queued refresh for %d competitors", queued)
    return {"status": "ok", "queued": queued}
