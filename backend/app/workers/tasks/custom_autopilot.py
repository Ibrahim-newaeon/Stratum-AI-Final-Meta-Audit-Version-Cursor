# =============================================================================
# Stratum AI - Custom Autopilot Tasks
# =============================================================================
"""
Background evaluation of Custom Autopilot rules.

Enqueues SAFE Autopilot actions into ``fact_actions_queue`` only. Never calls
Meta write clients. Trust Gate is enforced inside CustomAutopilotEngine.
"""

from __future__ import annotations

import asyncio
from typing import Any

from celery import shared_task
from celery.utils.log import get_task_logger
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.custom_autopilot import CustomAutopilotRule, CustomAutopilotRuleStatus
from app.services.custom_autopilot_engine import CustomAutopilotEngine
from app.workers.celery_app import with_distributed_lock

logger = get_task_logger(__name__)


async def _evaluate_tenant(tenant_id: int) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        engine = CustomAutopilotEngine(db)
        summary = await engine.evaluate_tenant_rules(tenant_id)
        await db.commit()
        return summary


async def _evaluate_all() -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(CustomAutopilotRule.tenant_id)
            .where(
                CustomAutopilotRule.status == CustomAutopilotRuleStatus.ACTIVE.value,
                CustomAutopilotRule.is_deleted.is_(False),
            )
            .distinct()
        )
        tenant_ids = [int(row[0]) for row in result.all()]

    summaries = []
    for tenant_id in tenant_ids:
        summaries.append(await _evaluate_tenant(tenant_id))

    return {
        "tenants": len(tenant_ids),
        "summaries": summaries,
        "enqueued": sum(s.get("enqueued", 0) for s in summaries),
        "blocked": sum(s.get("blocked", 0) for s in summaries),
        "held": sum(s.get("held", 0) for s in summaries),
    }


@shared_task(bind=True, name="app.workers.tasks.custom_autopilot.evaluate_tenant_custom_autopilot")
def evaluate_tenant_custom_autopilot(self, tenant_id: int) -> dict[str, Any]:
    """Evaluate all active Custom Autopilot rules for one tenant."""
    logger.info("custom_autopilot_evaluate_tenant tenant_id=%s", tenant_id)
    return asyncio.run(_evaluate_tenant(tenant_id))


@shared_task(bind=True, name="app.workers.tasks.custom_autopilot.evaluate_all_custom_autopilot")
@with_distributed_lock(timeout=900)
def evaluate_all_custom_autopilot(self) -> dict[str, Any]:
    """Beat entry: evaluate Custom Autopilot rules for every tenant with active rules."""
    logger.info("custom_autopilot_evaluate_all_start")
    result = asyncio.run(_evaluate_all())
    logger.info("custom_autopilot_evaluate_all_done %s", result)
    return result
