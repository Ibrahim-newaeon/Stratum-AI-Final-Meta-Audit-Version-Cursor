# =============================================================================
# Stratum AI - Measurement & Verification Tasks (GA4 read-only baseline)
# =============================================================================
"""
Background tasks that pull the read-only GA4 baseline into ``fact_ga4_daily``.

GA4 is a measurement integration only - these tasks never touch ad
campaigns. The nightly pull runs before the Trust Layer rollups so
attribution variance / signal health see fresh independent numbers.

Security: the beat-scheduled task uses a distributed lock to prevent
duplicate execution across multiple Celery workers.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any

from celery import shared_task
from celery.utils.log import get_task_logger

from app.core.config import settings
from app.workers.celery_app import with_distributed_lock

logger = get_task_logger(__name__)


def _run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a coroutine on a fresh event loop (Celery workers are sync)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _dispose_engine() -> None:
    """Drop pooled asyncpg connections so the next event loop starts clean."""
    try:
        from app.db.session import async_engine

        await async_engine.dispose()
    except Exception as exc:  # noqa: BLE001 - pragma: no cover - best effort
        logger.debug(f"async engine dispose skipped: {exc}")


async def _pull_all_tenants() -> dict[str, Any]:
    """Sync every tenant that has an active GA4 integration."""
    from app.db.session import AsyncSessionLocal
    from app.services.measurement.ga4_ingestion import (
        list_tenants_with_ga4,
        sync_ga4_for_tenant,
    )

    tenants = 0
    rows = 0
    errors: list[dict[str, Any]] = []
    try:
        async with AsyncSessionLocal() as db:
            tenant_ids = await list_tenants_with_ga4(db)

        for tenant_id in tenant_ids:
            async with AsyncSessionLocal() as db:
                try:
                    result = await sync_ga4_for_tenant(db, tenant_id)
                except Exception as exc:  # keep going for the other tenants
                    logger.exception(f"GA4 sync crashed for tenant {tenant_id}")
                    errors.append({"tenant_id": tenant_id, "message": str(exc)})
                    continue
            tenants += 1
            rows += result.rows_upserted
            if not result.success:
                errors.append({"tenant_id": tenant_id, "message": result.message})
    finally:
        await _dispose_engine()

    return {"status": "success", "tenants": tenants, "rows": rows, "errors": errors}


async def _sync_one_tenant(
    tenant_id: int, lookback_days: int | None, backfill: bool
) -> dict[str, Any]:
    """Sync a single tenant and return the JSON-friendly result."""
    from app.db.session import AsyncSessionLocal
    from app.services.measurement.ga4_ingestion import sync_ga4_for_tenant

    try:
        async with AsyncSessionLocal() as db:
            result = await sync_ga4_for_tenant(
                db, tenant_id, lookback_days=lookback_days, backfill=backfill
            )
        return result.to_dict()
    finally:
        await _dispose_engine()


@shared_task(name="app.workers.tasks.measurement.pull_ga4_daily_baseline")
@with_distributed_lock(
    lock_name="app.workers.tasks.measurement.pull_ga4_daily_baseline", timeout=3600
)
def pull_ga4_daily_baseline() -> dict[str, Any]:
    """
    Nightly read-only GA4 baseline pull for all tenants (Celery beat, 02:30 UTC).

    No-op when ``GA4_SYNC_ENABLED`` is false. Returns
    ``{tenants: n, rows: n, errors: [...]}``.
    """
    if not settings.ga4_sync_enabled:
        logger.info("GA4 sync disabled (GA4_SYNC_ENABLED=false) - skipping baseline pull")
        return {"status": "disabled", "tenants": 0, "rows": 0, "errors": []}

    logger.info("Starting GA4 daily baseline pull")
    result = _run_async(_pull_all_tenants())
    logger.info(
        f"GA4 daily baseline pull complete: tenants={result['tenants']} "
        f"rows={result['rows']} errors={len(result['errors'])}"
    )
    return result


@shared_task(
    name="app.workers.tasks.measurement.sync_ga4_tenant",
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def sync_ga4_tenant(
    self,
    tenant_id: int,
    lookback_days: int | None = None,
    backfill: bool = False,
) -> dict[str, Any]:
    """
    Manual / on-demand GA4 baseline sync for one tenant.

    Args:
        tenant_id: Tenant to sync
        lookback_days: Days to re-pull (defaults to ``GA4_LOOKBACK_DAYS``)
        backfill: Pull ``GA4_BACKFILL_DAYS`` regardless of existing rows

    Returns:
        ``GA4SyncResult`` as a dict (never raises for missing configuration).
    """
    logger.info(
        f"GA4 sync requested for tenant {tenant_id} "
        f"(lookback_days={lookback_days}, backfill={backfill})"
    )
    try:
        return _run_async(_sync_one_tenant(tenant_id, lookback_days, backfill))
    except Exception as exc:  # noqa: BLE001 - retried by Celery
        logger.error(f"GA4 sync failed for tenant {tenant_id}: {exc}")
        raise self.retry(exc=exc)
