# =============================================================================
# Stratum AI - Attribution Variance Daily Rollup Task
# =============================================================================
"""
Celery task for daily attribution variance rollup.

Compares Meta-reported conversions/revenue (campaign metrics) with the
independent, read-only GA4 baseline stored in ``fact_ga4_daily`` to identify
discrepancies per Meta channel (facebook / instagram / whatsapp).
"""

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any, Optional

from celery import shared_task
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import AsyncSessionLocal as async_session_factory
from app.models.trust_layer import (
    AttributionVarianceStatus,
    FactAttributionVarianceDaily,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

# Variance thresholds for status determination (percent).
# Read from settings when ATTRIBUTION_VARIANCE_{HIGH,MODERATE,MINOR}_PCT are
# defined on the Settings model; otherwise these module defaults apply.
_DEFAULT_VARIANCE_THRESHOLDS = {
    "high": 30,  # >30% variance = high
    "moderate": 15,  # 15-30% = moderate
    "minor": 5,  # 5-15% = minor
    # <5% = healthy
}


def _load_variance_thresholds() -> dict[str, float]:
    """Resolve variance thresholds from settings when available, else defaults."""
    resolved: dict[str, float] = {}
    for level, default in _DEFAULT_VARIANCE_THRESHOLDS.items():
        value = getattr(settings, f"attribution_variance_{level}_pct", None)
        resolved[level] = float(value) if value is not None else float(default)
    return resolved


VARIANCE_THRESHOLDS = _load_variance_thresholds()

# Meta channels - matches fact_ga4_daily.meta_channel, the seeded
# fact_attribution_variance_daily rows and the trust layer grouping.
PLATFORMS = ["facebook", "instagram", "whatsapp"]


# =============================================================================
# Helper Functions
# =============================================================================


def determine_variance_status(delta_pct: float) -> AttributionVarianceStatus:
    """
    Determine attribution variance status based on percentage difference.
    """
    abs_delta = abs(delta_pct)

    if abs_delta >= VARIANCE_THRESHOLDS["high"]:
        return AttributionVarianceStatus.HIGH_VARIANCE
    elif abs_delta >= VARIANCE_THRESHOLDS["moderate"]:
        return AttributionVarianceStatus.MODERATE_VARIANCE
    elif abs_delta >= VARIANCE_THRESHOLDS["minor"]:
        return AttributionVarianceStatus.MINOR_VARIANCE
    else:
        return AttributionVarianceStatus.HEALTHY


def calculate_confidence(
    ga4_revenue: float,
    platform_revenue: float,
    ga4_conversions: int,
    platform_conversions: int,
) -> float:
    """
    Calculate confidence score based on data volume and consistency.

    Higher confidence when:
    - Both sources have significant data volume
    - Multiple conversions to validate
    - Revenue and conversion trends align
    """
    # Base confidence from data volume
    min_revenue = min(ga4_revenue, platform_revenue)
    if min_revenue < 100:
        volume_confidence = 0.3
    elif min_revenue < 1000:
        volume_confidence = 0.6
    elif min_revenue < 10000:
        volume_confidence = 0.8
    else:
        volume_confidence = 0.95

    # Conversion count confidence
    min_conversions = min(ga4_conversions, platform_conversions)
    if min_conversions < 5:
        conversion_confidence = 0.4
    elif min_conversions < 20:
        conversion_confidence = 0.7
    elif min_conversions < 100:
        conversion_confidence = 0.85
    else:
        conversion_confidence = 0.95

    # Check if revenue and conversion trends align
    if ga4_conversions > 0 and platform_conversions > 0:
        ga4_aov = ga4_revenue / ga4_conversions
        platform_aov = platform_revenue / platform_conversions
        aov_diff = abs(ga4_aov - platform_aov) / max(ga4_aov, platform_aov, 1) * 100

        if aov_diff < 10:
            trend_confidence = 1.0
        elif aov_diff < 25:
            trend_confidence = 0.8
        else:
            trend_confidence = 0.6
    else:
        trend_confidence = 0.5

    # Weighted average
    confidence = (
        (volume_confidence * 0.4) + (conversion_confidence * 0.3) + (trend_confidence * 0.3)
    )

    return round(min(confidence, 1.0), 2)


# =============================================================================
# Main Task
# =============================================================================


@shared_task(
    name="tasks.attribution_variance_rollup",
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def attribution_variance_rollup(
    self, tenant_id: Optional[int] = None, target_date: Optional[str] = None
):
    """
    Daily attribution variance rollup task.

    Compares platform-reported revenue/conversions with web analytics data.
    Can run for a specific tenant or all tenants.

    Args:
        tenant_id: Optional tenant ID to process (None = all tenants)
        target_date: Date to process in ISO format (default: yesterday)
    """
    import asyncio

    async def run_rollup():
        async with async_session_factory() as db:
            try:
                rollup_date = (
                    date.fromisoformat(target_date)
                    if target_date
                    else datetime.now(UTC).date() - timedelta(days=1)
                )

                logger.info(
                    f"Starting attribution variance rollup for date={rollup_date}, tenant_id={tenant_id}"
                )

                # Get list of tenants to process
                if tenant_id:
                    tenant_ids = [tenant_id]
                else:
                    from app.models.tenant import Tenant

                    result = await db.execute(
                        select(Tenant.id).where(Tenant.is_deleted.is_(False))
                    )
                    tenant_ids = [row[0] for row in result.all()]

                records_created = 0

                for tid in tenant_ids:
                    for platform in PLATFORMS:
                        # Fetch GA4 baseline and Meta-reported metrics
                        metrics = await fetch_attribution_metrics(db, tid, platform, rollup_date)

                        if not metrics:
                            # No GA4 rows for this date/channel: skip the upsert
                            # (never write ga4_revenue=0, which would drive the
                            # EMQ attribution driver to zero).
                            continue

                        # Calculate deltas
                        ga4_revenue = metrics["ga4_revenue"]
                        platform_revenue = metrics["platform_revenue"]
                        ga4_conversions = metrics["ga4_conversions"]
                        platform_conversions = metrics["platform_conversions"]

                        revenue_delta_abs = platform_revenue - ga4_revenue
                        revenue_delta_pct = (
                            (revenue_delta_abs / ga4_revenue * 100) if ga4_revenue > 0 else 0
                        )

                        conversion_delta_abs = platform_conversions - ga4_conversions
                        conversion_delta_pct = (
                            (conversion_delta_abs / ga4_conversions * 100)
                            if ga4_conversions > 0
                            else 0
                        )

                        # Determine status and confidence
                        status = determine_variance_status(revenue_delta_pct)
                        confidence = calculate_confidence(
                            ga4_revenue, platform_revenue, ga4_conversions, platform_conversions
                        )

                        # Check if record already exists
                        existing = await db.execute(
                            select(FactAttributionVarianceDaily).where(
                                and_(
                                    FactAttributionVarianceDaily.tenant_id == tid,
                                    FactAttributionVarianceDaily.date == rollup_date,
                                    FactAttributionVarianceDaily.platform == platform,
                                )
                            )
                        )
                        existing_record = existing.scalar_one_or_none()

                        if existing_record:
                            # Update existing record
                            existing_record.ga4_revenue = ga4_revenue
                            existing_record.platform_revenue = platform_revenue
                            existing_record.revenue_delta_abs = revenue_delta_abs
                            existing_record.revenue_delta_pct = revenue_delta_pct
                            existing_record.ga4_conversions = ga4_conversions
                            existing_record.platform_conversions = platform_conversions
                            existing_record.conversion_delta_abs = conversion_delta_abs
                            existing_record.conversion_delta_pct = conversion_delta_pct
                            existing_record.confidence = confidence
                            existing_record.status = status
                            existing_record.updated_at = datetime.now(UTC)
                        else:
                            # Create new record
                            record = FactAttributionVarianceDaily(
                                tenant_id=tid,
                                date=rollup_date,
                                platform=platform,
                                ga4_revenue=ga4_revenue,
                                platform_revenue=platform_revenue,
                                revenue_delta_abs=revenue_delta_abs,
                                revenue_delta_pct=revenue_delta_pct,
                                ga4_conversions=ga4_conversions,
                                platform_conversions=platform_conversions,
                                conversion_delta_abs=conversion_delta_abs,
                                conversion_delta_pct=conversion_delta_pct,
                                confidence=confidence,
                                status=status,
                            )
                            db.add(record)
                            records_created += 1

                await db.commit()

                logger.info(
                    f"Attribution variance rollup completed: {records_created} records created/updated"
                )

                return {
                    "status": "success",
                    "date": rollup_date.isoformat(),
                    "records_processed": records_created,
                }

            except Exception as e:
                logger.error(f"Attribution variance rollup failed: {e!s}")
                await db.rollback()
                raise self.retry(exc=e)

    return asyncio.get_event_loop().run_until_complete(run_rollup())


async def fetch_attribution_metrics(
    db: AsyncSession,
    tenant_id: int,
    platform: str,
    target_date: date,
) -> Optional[dict[str, Any]]:
    """
    Fetch attribution metrics for one Meta channel on one date.

    GA4 side: SUM(conversions, revenue) from ``fact_ga4_daily`` rows classified
    as Meta traffic for the channel (``is_meta_traffic`` and ``meta_channel``).

    Platform side: SUM(CampaignMetric.conversions, revenue_cents / 100) joined
    to Campaign for the same date and channel (``raw_data->>'channel'``, the
    column the demo seed uses).

    Returns ``None`` when GA4 has no rows for the date/channel so the caller
    skips the upsert instead of writing a zero baseline.
    """
    from app.base_models import AdPlatform, Campaign, CampaignMetric
    from app.models.campaign_builder import ConnectionStatus, TenantPlatformConnection
    from app.models.measurement import FactGA4Daily

    # Informational only: note whether the Meta platform connection is live.
    # Not a hard gate - the GA4 baseline is what decides whether we roll up.
    conn_result = await db.execute(
        select(TenantPlatformConnection.status).where(
            and_(
                TenantPlatformConnection.tenant_id == tenant_id,
                TenantPlatformConnection.platform == AdPlatform.META.value,
            )
        )
    )
    connection_status = conn_result.scalar_one_or_none()
    if connection_status != ConnectionStatus.CONNECTED.value:
        logger.debug(
            "Meta platform connection not connected (status=%s) for tenant %s - "
            "rolling up %s from stored metrics anyway",
            connection_status,
            tenant_id,
            platform,
        )

    # GA4 (independent, read-only baseline)
    ga4_result = await db.execute(
        select(
            func.count(FactGA4Daily.id),
            func.coalesce(func.sum(FactGA4Daily.conversions), 0),
            func.coalesce(func.sum(FactGA4Daily.revenue), 0.0),
        ).where(
            and_(
                FactGA4Daily.tenant_id == tenant_id,
                FactGA4Daily.date == target_date,
                FactGA4Daily.is_meta_traffic.is_(True),
                FactGA4Daily.meta_channel == platform,
            )
        )
    )
    ga4_row_count, ga4_conversions, ga4_revenue = ga4_result.one()
    if not ga4_row_count:
        return None

    # Meta-reported (campaign metrics for the same channel)
    platform_result = await db.execute(
        select(
            func.coalesce(func.sum(CampaignMetric.conversions), 0),
            func.coalesce(func.sum(CampaignMetric.revenue_cents), 0),
        )
        .join(Campaign, Campaign.id == CampaignMetric.campaign_id)
        .where(
            and_(
                CampaignMetric.tenant_id == tenant_id,
                CampaignMetric.date == target_date,
                Campaign.tenant_id == tenant_id,
                Campaign.raw_data["channel"].astext == platform,
            )
        )
    )
    platform_conversions, platform_revenue_cents = platform_result.one()

    return {
        "ga4_revenue": float(ga4_revenue or 0.0),
        "platform_revenue": float(platform_revenue_cents or 0) / 100.0,
        "ga4_conversions": int(ga4_conversions or 0),
        "platform_conversions": int(platform_conversions or 0),
    }


# =============================================================================
# Scheduled Task Registration
# =============================================================================


@shared_task(name="tasks.schedule_attribution_variance_rollup")
def schedule_attribution_variance_rollup():
    """
    Scheduled task to trigger attribution variance rollup for all tenants.
    Should be scheduled to run daily at 3:00 AM UTC (after signal health).
    """
    return attribution_variance_rollup.delay()
