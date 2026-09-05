# =============================================================================
# Stratum AI - Tenant Onboarding Persistence
# =============================================================================
"""
Shared persistence for a tenant's ``TenantOnboarding`` record.

Both onboarding front doors write through here: the wizard endpoints under
``/onboarding`` and the conversational agent's completion step. The trust-gate
thresholds stored on that record are live configuration - ``signal_health``
resolves them for the dashboard summary, the daily rollup's row status and the
trust gate - so the two doors must not disagree about how they are written.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.onboarding import OnboardingStatus, OnboardingStep, TenantOnboarding
from app.services.agents.root_agent import OnboardingData

logger = get_logger(__name__)

__all__ = ["get_or_create_onboarding", "persist_chat_onboarding"]


async def get_or_create_onboarding(
    db: AsyncSession, tenant_id: int
) -> TenantOnboarding:
    """
    Fetch (or lazily create) the tenant's onboarding record.

    Args:
        db: Async database session.
        tenant_id: Tenant whose record is wanted.

    Returns:
        The tenant's onboarding record, flushed when newly created so its
        column defaults are populated.
    """
    result = await db.execute(
        select(TenantOnboarding).where(TenantOnboarding.tenant_id == tenant_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        record = TenantOnboarding(
            tenant_id=tenant_id,
            status=OnboardingStatus.NOT_STARTED.value,
            current_step=OnboardingStep.BUSINESS_PROFILE.value,
            completed_steps=[],
        )
        db.add(record)
        await db.flush()
    return record


async def persist_chat_onboarding(
    db: AsyncSession,
    tenant_id: int,
    data: OnboardingData,
) -> TenantOnboarding:
    """
    Write the conversational agent's collected settings onto the tenant.

    The agent is a pure state machine with no database access, so a threshold
    answered in the chat lived only on the conversation context and a tenant
    who asked for 90 was still graded at the configured default. This is the
    step that lands it on the same ``TenantOnboarding`` columns the wizard's
    trust gate step writes, which is where ``thresholds_for_tenant`` reads it
    back.

    Only the trust-gate thresholds are written: they are the part of
    ``OnboardingData`` that governs runtime behaviour. The alert edge is kept
    at or below the autopilot edge, because ``SignalHealthThresholds.resolve``
    discards an out-of-order pair in favour of the deployment defaults - a
    tenant lowering autopilot to 45 under a stored alert of 50 would otherwise
    silently get 70/40 back.

    Args:
        db: Async database session; the caller owns the commit.
        tenant_id: Tenant the conversation belongs to.
        data: What the agent collected during the conversation.

    Returns:
        The updated onboarding record.
    """
    record = await get_or_create_onboarding(db, tenant_id)

    autopilot = int(data.trust_threshold)
    alert = record.trust_threshold_alert
    if alert is None:
        alert = int(settings.signal_health_degraded_threshold)

    record.trust_threshold_autopilot = autopilot
    record.trust_threshold_alert = min(int(alert), autopilot)

    logger.info(
        "onboarding_chat_thresholds_persisted",
        tenant_id=tenant_id,
        trust_threshold_autopilot=record.trust_threshold_autopilot,
        trust_threshold_alert=record.trust_threshold_alert,
    )
    return record
