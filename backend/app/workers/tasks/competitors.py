# =============================================================================
# Stratum AI - Competitor Intelligence Tasks
# =============================================================================
"""
Background tasks for competitor benchmark data refresh.

``fetch_competitor_data`` calls ``MarketIntelligenceService`` for one
CompetitorBenchmark row; ``refresh_all_competitors`` fans out over every
tracked competitor (Celery beat).

With ``MARKET_INTEL_PROVIDER=mock`` (default) this produces deterministic
synthetic traffic so Module D works without paid keys. Paid providers
(serpapi / dataforseo) fail closed when keys are missing — they do not
silently invent traffic.
"""

from __future__ import annotations

import asyncio
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


def _run_async(coro):
    """Run an async coroutine from a sync Celery worker."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Nested loop (rare in Celery); use a fresh one.
            return asyncio.new_event_loop().run_until_complete(coro)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _apply_market_data(competitor, data) -> None:
    """Copy MarketIntelligenceService output onto a CompetitorBenchmark row."""
    competitor.meta_title = data.meta_title
    competitor.meta_description = data.meta_description
    competitor.meta_keywords = data.meta_keywords
    competitor.social_links = data.social_links
    competitor.estimated_traffic = data.estimated_traffic
    competitor.traffic_trend = data.traffic_trend
    competitor.top_keywords = data.top_keywords
    competitor.paid_keywords_count = data.paid_keywords_count
    competitor.organic_keywords_count = data.organic_keywords_count
    if data.estimated_ad_spend_cents is not None:
        competitor.estimated_ad_spend_cents = data.estimated_ad_spend_cents
    competitor.detected_ad_platforms = data.detected_ad_platforms
    competitor.data_source = data.data_source or competitor.data_source
    competitor.last_fetched_at = data.fetched_at or datetime.now(UTC)
    competitor.fetch_error = data.error


def _recompute_share_of_voice(db, model, tenant_id: int) -> None:
    """Redistribute share_of_voice from estimated_traffic for one tenant."""
    rows = list(
        db.execute(select(model).where(model.tenant_id == tenant_id)).scalars().all()
    )
    total = sum((r.estimated_traffic or 0) for r in rows)
    for row in rows:
        if total > 0 and row.estimated_traffic:
            row.share_of_voice = round((row.estimated_traffic / total) * 100, 2)
        else:
            row.share_of_voice = None


@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def fetch_competitor_data(self, tenant_id: int, competitor_id: int) -> dict[str, Any]:
    """
    Fetch/refresh intelligence data for a single competitor via MarketIntelligenceService.
    """
    model = _get_competitor_model()
    if model is None:
        logger.warning("Competitor model unavailable; skipping fetch")
        return {"status": "skipped", "reason": "competitor model unavailable"}

    from app.core.config import settings
    from app.services.market_proxy import MarketIntelligenceService

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

        service = MarketIntelligenceService()
        try:
            data = _run_async(service.get_competitor_data(competitor.domain))
        except Exception as exc:
            competitor.fetch_error = str(exc)
            competitor.last_fetched_at = datetime.now(UTC)
            db.commit()
            logger.exception(
                "Competitor fetch failed for %s (tenant %s)", competitor_id, tenant_id
            )
            raise self.retry(exc=exc) from exc

        _apply_market_data(competitor, data)
        _recompute_share_of_voice(db, model, tenant_id)
        db.commit()

        status = "error" if data.error else "ok"
        logger.info(
            "Refreshed competitor %s (%s) for tenant %s via %s [%s]",
            competitor_id,
            competitor.domain,
            tenant_id,
            settings.market_intel_provider,
            status,
        )
        return {
            "status": status,
            "competitor_id": competitor_id,
            "tenant_id": tenant_id,
            "provider": settings.market_intel_provider,
            "data_source": competitor.data_source,
            "synthetic": competitor.data_source == "mock",
            "error": competitor.fetch_error,
            "fetched_at": competitor.last_fetched_at.isoformat()
            if competitor.last_fetched_at
            else None,
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
