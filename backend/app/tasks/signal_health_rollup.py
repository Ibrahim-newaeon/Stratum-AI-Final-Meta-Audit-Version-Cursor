# =============================================================================
# Stratum AI - Signal Health Daily Rollup Task
# =============================================================================
"""
Celery task for the daily signal health rollup.

Writes one ``fact_signal_health_daily`` row per tenant and Meta channel from
what :mod:`app.services.signal_health` could actually measure - CAPI delivery
logs, genuine campaign sync timestamps and the platform connection's own error
state, all tenant-scoped.

**A tenant with too little evidence gets no row.** That table is what the trust
gate grades on, and the gate fails closed on its absence, so writing a row that
nothing substantiates would be worse than writing none: it would be graded as
if it were evidence. Any row this rollup wrote earlier for a tenant/channel it
can no longer substantiate is withdrawn for the same reason.
"""

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any, Optional

from celery import shared_task
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal as async_session_factory
from app.models.trust_layer import FactSignalHealthDaily, SignalHealthStatus
from app.services.signal_health import (
    COMPONENT_DELIVERY,
    COMPONENT_EMQ,
    COMPONENT_FRESHNESS,
    COMPONENT_RELIABILITY,
    SignalHealthThresholds,
    compute_signal_health,
    day_window,
    thresholds_for_tenant,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

# What ``fact_signal_health_daily.api_error_rate`` actually holds.
#
# Nothing in the system measures a request-level API error rate: the Meta
# connection records a status, a last_error and an error counter, but never a
# denominator. The column therefore carries the *connection-health deficit* -
# ``100 - the reliability component`` - which is exactly what the trust gate
# reads back out of it (``_score_record`` computes ``100 - api_error_rate``).
# Writer and reader have always agreed; this constant exists so the next reader
# does not mistake the value for a percentage of failed API calls and band it
# against percentage thresholds, which is how one recorded connection error
# used to turn a tenant scoring 97 into a CRITICAL row that BLOCKed the gate.
API_ERROR_RATE_IS_CONNECTION_DEFICIT = True

# Meta channels - matches fact_ga4_daily.meta_channel, the seeded
# fact_signal_health_daily rows and the trust layer grouping. All three share
# the single Meta platform connection (TenantPlatformConnection.platform='meta').
PLATFORMS = ["facebook", "instagram", "whatsapp"]


# =============================================================================
# Helper Functions
# =============================================================================


def determine_status(
    score: Optional[float],
    partial_evidence: bool,
    thresholds: SignalHealthThresholds,
) -> SignalHealthStatus:
    """
    Band the measured composite onto the row's status enum.

    The enum is not decoration: the trust gate floors its decision on it
    (``_STATUS_DECISIONS`` maps OK/RISK to PASS, DEGRADED to HOLD, CRITICAL to
    BLOCK), so whatever this function returns can only ever make the gate
    *stricter* than the score alone. That is precisely why it must be derived
    from the same configured bands the composite is graded against.

    It used to apply four unconfigured per-metric threshold tables - freshness
    over 360 minutes, EMQ under 70, event loss over 20, "api_error_rate" over
    10 - calibrated for the fabricated numbers the rollup used to write. Against
    real measurements those literals silently overrode the documented 70/40
    contract: a tenant with a clean connection, no failed deliveries and a
    seven-hour-old sync scored 93.5 and was written CRITICAL, and a tenant with
    one recorded connection error scored 97 and was written CRITICAL, both of
    which BLOCKed every automation.

    ``RISK`` is reserved for a composite in the healthy band that was measured
    over an incomplete set of components. It maps to PASS in the gate, so the
    decision is unchanged; it exists so the trust layer UI can say "passing, on
    partial evidence" without a second threshold table.

    Args:
        score: The measured 0-100 composite for this tenant/channel.
        partial_evidence: Whether some component went unmeasured.
        thresholds: The band edges this tenant is graded against.

    Returns:
        The status enum to store on the row.
    """
    if score is None:
        # The caller writes no row at all in this case; returning the most
        # restrictive status keeps any future caller fail-closed.
        return SignalHealthStatus.CRITICAL
    if score < thresholds.degraded:
        return SignalHealthStatus.CRITICAL
    if score < thresholds.healthy:
        return SignalHealthStatus.DEGRADED
    return SignalHealthStatus.RISK if partial_evidence else SignalHealthStatus.OK


def generate_issues(
    components: dict[str, float],
    missing_inputs: list[str],
    connection_errors: int,
    thresholds: SignalHealthThresholds,
) -> list[str]:
    """
    Describe what is wrong, in terms of what was actually measured.

    Each component is a 0-100 score on the same scale as the composite, so the
    same configured band is used to decide whether it is worth reporting - no
    parallel per-metric literals. Components that could not be measured are
    reported as gaps rather than as failures.

    Args:
        components: Measured component name to 0-100 score.
        missing_inputs: Sentences naming the components that were not measured.
        connection_errors: Errors recorded against the Meta connection.
        thresholds: The band edges this tenant is graded against.

    Returns:
        Issue sentences, safe to show to the tenant.
    """
    issues: list[str] = list(missing_inputs)
    healthy = thresholds.healthy

    emq = components.get(COMPONENT_EMQ)
    if emq is not None and emq < healthy:
        issues.append(
            f"Event match quality scores {emq:.0f}/100, below the {healthy:.0f} "
            "threshold for automated action."
        )

    delivery = components.get(COMPONENT_DELIVERY)
    if delivery is not None and delivery < healthy:
        issues.append(
            f"Conversion event delivery scores {delivery:.0f}/100 - "
            f"{100.0 - delivery:.1f}% of attempts were rejected."
        )

    freshness = components.get(COMPONENT_FRESHNESS)
    if freshness is not None and freshness < healthy:
        issues.append(
            f"Campaign data freshness scores {freshness:.0f}/100; the newest "
            "successful Meta insights sync is older than the configured window."
        )

    reliability = components.get(COMPONENT_RELIABILITY)
    if reliability is not None and reliability < healthy:
        # Deliberately not phrased as an error *rate*: nothing measures a
        # denominator, so "N recorded errors" is the whole of what is known.
        issues.append(
            f"Meta connection health scores {reliability:.0f}/100 "
            f"({connection_errors} recorded error(s))."
        )

    return issues


def generate_actions(
    components: dict[str, float],
    platform: str,
    thresholds: SignalHealthThresholds,
) -> list[str]:
    """
    Recommend what to do about the components that scored below the band.

    Args:
        components: Measured component name to 0-100 score.
        platform: Meta channel these measurements belong to.
        thresholds: The band edges this tenant is graded against.

    Returns:
        Recommended operator actions.
    """
    actions: list[str] = []
    healthy = thresholds.healthy

    emq = components.get(COMPONENT_EMQ)
    if emq is not None and emq < healthy:
        actions.append(f"Review {platform} pixel/CAPI implementation")
        actions.append("Send more hashed customer identifiers with each event")

    delivery = components.get(COMPONENT_DELIVERY)
    if delivery is not None and delivery < healthy:
        actions.append("Verify server-side event delivery")
        actions.append("Check for browser tracking blockers")

    freshness = components.get(COMPONENT_FRESHNESS)
    if freshness is not None and freshness < healthy:
        actions.append("Check the Meta insights sync schedule")
        actions.append("Verify API connection health")

    reliability = components.get(COMPONENT_RELIABILITY)
    if reliability is not None and reliability < healthy:
        actions.append(f"Review {platform} API credentials")
        actions.append("Check rate limits and quotas")

    return actions


# =============================================================================
# Main Task
# =============================================================================


@shared_task(
    name="tasks.signal_health_rollup",
    bind=True,
    max_retries=3,
    default_retry_delay=300,
)
def signal_health_rollup(self, tenant_id: Optional[int] = None, target_date: Optional[str] = None):
    """
    Daily signal health rollup task.

    Collects metrics from platform connections and calculates health status.
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
                    f"Starting signal health rollup for date={rollup_date}, tenant_id={tenant_id}"
                )

                # Get list of tenants to process
                if tenant_id:
                    tenant_ids = [tenant_id]
                else:
                    # Get all active tenants with platform connections
                    # For now, we'll use a placeholder - in production, query dim_tenant
                    from app.models.tenant import Tenant

                    result = await db.execute(
                        select(Tenant.id).where(Tenant.is_deleted.is_(False))
                    )
                    tenant_ids = [row[0] for row in result.all()]

                records_created = 0
                records_skipped = 0
                rows_withdrawn = 0

                for tid in tenant_ids:
                    # The tenant's own trust thresholds when it set any during
                    # onboarding, read once per tenant. The trust gate resolves
                    # the same pair, so the band this row is stamped with is the
                    # band the gate will enforce.
                    thresholds = await thresholds_for_tenant(db, tid)

                    for platform in PLATFORMS:
                        # Measure this tenant/channel from real, persisted,
                        # tenant-scoped data. Returns None when the inputs are
                        # not there - never a stand-in number.
                        metrics = await fetch_platform_metrics(
                            db, tid, platform, rollup_date, thresholds
                        )

                        if not metrics:
                            # Insufficient data: write nothing. This table is what
                            # the trust gate grades on, and a row it cannot
                            # substantiate would be graded as if it were evidence.
                            # Any row an earlier run wrote for the same key is
                            # withdrawn for the same reason - leaving a previously
                            # fabricated row in place would keep the gate passing
                            # on it long after the fabrication was removed.
                            withdrawn = await db.execute(
                                delete(FactSignalHealthDaily).where(
                                    and_(
                                        FactSignalHealthDaily.tenant_id == tid,
                                        FactSignalHealthDaily.date == rollup_date,
                                        FactSignalHealthDaily.platform == platform,
                                    )
                                )
                            )
                            rows_withdrawn += withdrawn.rowcount or 0
                            records_skipped += 1
                            continue

                        # Band the measured composite with the same configured
                        # thresholds the gate enforces.
                        status = determine_status(
                            metrics.get("composite_score"),
                            bool(metrics.get("partial_evidence")),
                            thresholds,
                        )

                        # Describe what was measured, in the components' own
                        # terms - never as a threshold the row does not carry.
                        components = metrics.get("components") or {}
                        issues = generate_issues(
                            components,
                            metrics.get("missing_inputs") or [],
                            int(metrics.get("connection_errors") or 0),
                            thresholds,
                        )

                        actions = generate_actions(components, platform, thresholds)

                        # Check if record already exists
                        existing = await db.execute(
                            select(FactSignalHealthDaily).where(
                                and_(
                                    FactSignalHealthDaily.tenant_id == tid,
                                    FactSignalHealthDaily.date == rollup_date,
                                    FactSignalHealthDaily.platform == platform,
                                )
                            )
                        )
                        existing_record = existing.scalar_one_or_none()

                        import json

                        if existing_record:
                            # Update existing record
                            existing_record.emq_score = metrics.get("emq_score")
                            existing_record.event_loss_pct = metrics.get("event_loss_pct")
                            existing_record.freshness_minutes = metrics.get("freshness_minutes")
                            existing_record.api_error_rate = metrics.get("api_error_rate")
                            existing_record.status = status
                            existing_record.issues = json.dumps(issues) if issues else None
                            existing_record.actions = json.dumps(actions) if actions else None
                            existing_record.updated_at = datetime.now(UTC)
                        else:
                            # Create new record
                            record = FactSignalHealthDaily(
                                tenant_id=tid,
                                date=rollup_date,
                                platform=platform,
                                emq_score=metrics.get("emq_score"),
                                event_loss_pct=metrics.get("event_loss_pct"),
                                freshness_minutes=metrics.get("freshness_minutes"),
                                api_error_rate=metrics.get("api_error_rate"),
                                status=status,
                                issues=json.dumps(issues) if issues else None,
                                actions=json.dumps(actions) if actions else None,
                            )
                            db.add(record)
                            records_created += 1

                await db.commit()

                logger.info(
                    "Signal health rollup completed: %s rows written, %s tenant/channel "
                    "pairs skipped for insufficient data, %s unsubstantiated rows withdrawn",
                    records_created,
                    records_skipped,
                    rows_withdrawn,
                )

                return {
                    "status": "success",
                    "date": rollup_date.isoformat(),
                    "records_processed": records_created,
                    "insufficient_data": records_skipped,
                    "rows_withdrawn": rows_withdrawn,
                }

            except Exception as e:
                logger.error(f"Signal health rollup failed: {e!s}")
                await db.rollback()
                raise self.retry(exc=e)

    # asyncio.run rather than get_event_loop(): a Celery worker process has no
    # current event loop, and get_event_loop() raises there rather than making
    # one (it is deprecated for exactly this).
    return asyncio.run(run_rollup())


async def fetch_platform_metrics(
    db: AsyncSession,
    tenant_id: int,
    platform: str,
    target_date: date,
    thresholds: Optional[SignalHealthThresholds] = None,
) -> Optional[dict[str, Any]]:
    """
    Measure one tenant/channel from real, persisted, tenant-scoped data.

    Delegates to :mod:`app.services.signal_health` - the single computation of
    signal health - and translates its components onto the metric columns of
    ``fact_signal_health_daily``:

    - ``emq_score``: CAPI delivery success blended with hashed-identifier
      coverage, counted from ``capi_delivery_logs`` over the rollup day
    - ``event_loss_pct``: the measured delivery failure rate over the same rows
    - ``freshness_minutes``: age of the newest genuine ``Campaign.last_synced_at``
      at the end of the rollup day
    - ``api_error_rate``: the connection-health deficit - ``100 - the
      reliability component`` - derived from ``TenantPlatformConnection``
      status and error state. It is **not** a percentage of failed API calls;
      nothing in the system measures a denominator for one. The trust gate
      reads it back as ``100 - api_error_rate``, so writer and reader agree.
      See ``API_ERROR_RATE_IS_CONNECTION_DEFICIT``.

    A column the tenant produced no evidence for is left NULL rather than
    filled in; the trust gate already renormalises over the columns that are
    populated and refuses to score a row that is too empty.

    Alongside the four columns the result carries the composite the row will be
    banded with, whether it rests on partial evidence, and the components and
    missing inputs the issue/action text is written from - so the row's status
    and its explanation come from the same measurement rather than from a
    second pass of threshold guesses over the stored columns.

    This function used to return ``emq_score`` 85 or 65, ``event_loss_pct`` 3.5
    or 15 and ``api_error_rate`` 0.5 or 8 chosen purely on whether the Meta
    connection had ever recorded an error, with freshness defaulting to 30
    minutes - numbers no tenant ever produced, written into the table the trust
    gate grades on.

    Args:
        db: Async database session
        tenant_id: Tenant to measure; every underlying query is filtered by it
        platform: Meta channel (``facebook``, ``instagram`` or ``whatsapp``)
        target_date: The UTC day this rollup row is dated for
        thresholds: The tenant's band edges; resolved from its onboarding
            record when not supplied

    Returns:
        The metric columns to write plus the composite and its supporting
        detail, or None when the tenant produced too little evidence to be
        scored - in which case the caller must write no row at all rather than
        substitute defaults.
    """
    computation = await compute_signal_health(
        db=db,
        tenant_id=tenant_id,
        channel=platform,
        window=day_window(target_date),
        thresholds=thresholds,
    )

    if computation.insufficient_data:
        logger.info(
            "Signal health insufficient for tenant=%s channel=%s date=%s: %s",
            tenant_id,
            platform,
            target_date.isoformat(),
            "; ".join(computation.missing_inputs) or "no components measured",
        )
        return None

    components = computation.component_scores()
    delivery = computation.delivery
    freshness = computation.freshness

    api_error_rate = None
    if COMPONENT_RELIABILITY in components:
        api_error_rate = round(100.0 - components[COMPONENT_RELIABILITY], 2)

    return {
        "emq_score": components.get(COMPONENT_EMQ),
        "event_loss_pct": (
            delivery.failure_rate_pct if delivery and COMPONENT_DELIVERY in components else None
        ),
        "freshness_minutes": (
            freshness.age_minutes if freshness and COMPONENT_FRESHNESS in components else None
        ),
        "api_error_rate": api_error_rate,
        # Not stored - used to band the row and to write its explanation.
        "composite_score": computation.score,
        "partial_evidence": computation.partial_evidence,
        "components": components,
        "missing_inputs": computation.missing_inputs,
        "connection_errors": (
            computation.connection.error_count if computation.connection else 0
        ),
    }


# =============================================================================
# Scheduled Task Registration
# =============================================================================


@shared_task(name="tasks.schedule_signal_health_rollup")
def schedule_signal_health_rollup():
    """
    Scheduled task to trigger signal health rollup for all tenants.
    Should be scheduled to run daily at 2:00 AM UTC.
    """
    return signal_health_rollup.delay()
