# =============================================================================
# Stratum AI - Tenant-Scoped Signal Health Service
# =============================================================================
"""
The single computation of signal health from real, persisted, tenant-scoped data.

Signal health is the number the trust gate grades on, so every input here is
something a tenant genuinely produced, read back from a table that has a
writer, filtered by ``tenant_id`` in the query itself:

| Component  | Source                                          | Fact column        |
|------------|-------------------------------------------------|--------------------|
| emq        | ``capi_delivery_logs`` success rate blended with | ``emq_score``      |
|            | hashed-identifier coverage over the window       |                    |
| freshness  | newest genuine ``Campaign.last_synced_at``       |``freshness_minutes``|
| loss       | ``capi_delivery_logs`` failure rate              |``event_loss_pct``  |
| connection | ``TenantPlatformConnection`` status/error state  |``api_error_rate``  |

Two components come from the delivery table because two of the fact table's
four metric columns are delivery-derived; that is a property of the schema and
is documented in docs/architecture/trust-engine.md rather than hidden.

**No input is ever defaulted.** A component that cannot be measured is
reported as a :class:`~app.services.signal_health.model.MissingInput` naming
what was missing, and when too little of the weight remains the computation
returns ``score=None`` - insufficient data. Callers must render or enforce that
as *unknown*: the rollup writes no ``fact_signal_health_daily`` row, the trust
gate consequently has nothing to pass on, and the API says "insufficient_data"
rather than sending a number.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import Integer, and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.base_models import AdPlatform, Campaign
from app.core.config import settings
from app.models.campaign_builder import TenantPlatformConnection
from app.models.capi_delivery import CAPIDeliveryLog
from app.services.signal_health.model import (
    MISSING_NO_CAMPAIGN_SYNC,
    MISSING_NO_DELIVERY_EVENTS,
    MISSING_NO_PLATFORM_CONNECTION,
    MISSING_TOO_FEW_DELIVERY_EVENTS,
    ConnectionMeasurement,
    DeliveryMeasurement,
    FreshnessMeasurement,
    MissingInput,
    ScoredComponent,
    SignalHealthComputation,
    SignalHealthThresholds,
    SignalHealthWindow,
)
from app.services.signal_health.scoring import (
    COMPONENT_DELIVERY,
    COMPONENT_EMQ,
    COMPONENT_FRESHNESS,
    COMPONENT_RELIABILITY,
    component_weights,
    connection_component_score,
    emq_component_score,
    freshness_component_score,
    status_for_score,
    weighted_score,
)

logger = logging.getLogger(__name__)

__all__ = [
    "META_CHANNELS",
    "compute_signal_health",
    "compute_tenant_signal_health",
    "day_window",
    "default_window",
    "measure_connection",
    "measure_delivery",
    "measure_freshness",
    "summarise_channels",
    "thresholds_for_tenant",
]

# The Meta channels the trust layer grades, matching fact_signal_health_daily
# and fact_ga4_daily.meta_channel.
META_CHANNELS: tuple[str, ...] = ("facebook", "instagram", "whatsapp")

# Which capi_delivery_logs.platform values carry a channel's events. Facebook
# and Instagram share the single Meta CAPI dataset, so both read the "meta"
# rows; WhatsApp has its own connector and its own rows.
CHANNEL_DELIVERY_PLATFORMS: dict[str, tuple[str, ...]] = {
    "facebook": ("meta",),
    "instagram": ("meta",),
    "whatsapp": ("whatsapp",),
}

# capi_delivery_logs.status values that mean the platform accepted the event.
DELIVERY_SUCCESS_STATUS = "success"


def default_window(now: datetime | None = None) -> SignalHealthWindow:
    """
    Build the default delivery window ending now.

    Width comes from ``signal_health_delivery_window_hours``.

    Args:
        now: Optional end of the window; defaults to the current UTC time.

    Returns:
        The window to measure over.
    """
    end = now or datetime.now(UTC)
    return SignalHealthWindow(
        start=end - timedelta(hours=settings.signal_health_delivery_window_hours),
        end=end,
    )


def day_window(target_date: date) -> SignalHealthWindow:
    """
    Build the window covering one whole UTC calendar day.

    The daily rollup uses this so a row dated for a given day is measured over
    that day, and a re-run for a historical date reproduces the same numbers
    instead of drifting with wall-clock time.

    Args:
        target_date: The UTC date to cover.

    Returns:
        The window from 00:00 UTC on that date to 00:00 UTC the next day.
    """
    start = datetime.combine(target_date, time.min, tzinfo=UTC)
    return SignalHealthWindow(start=start, end=start + timedelta(days=1))


async def measure_delivery(
    db: AsyncSession,
    tenant_id: int,
    platforms: Sequence[str],
    window: SignalHealthWindow,
) -> DeliveryMeasurement:
    """
    Count this tenant's CAPI delivery attempts over the window.

    Reads ``capi_delivery_logs``, the one event-delivery table that has a
    writer. The count is of delivery *attempts*: a retried event contributes
    one row per attempt, so the success rate is the share of attempts the
    platform accepted.

    The query is filtered by ``tenant_id`` in SQL, not after the fact - these
    rows are another tenant's conversion traffic and must never be aggregated
    across tenants.

    Args:
        db: Async database session.
        tenant_id: Tenant whose deliveries to count.
        platforms: ``capi_delivery_logs.platform`` values for this channel.
        window: Half-open window to count over.

    Returns:
        The measured counts, which may be all zero when there was no traffic.
    """
    result = await db.execute(
        select(
            func.count(CAPIDeliveryLog.id).label("total"),
            func.coalesce(
                func.sum(
                    case(
                        (CAPIDeliveryLog.status == DELIVERY_SUCCESS_STATUS, 1), else_=0
                    )
                ),
                0,
            ).label("successful"),
            func.coalesce(
                func.sum(
                    case((CAPIDeliveryLog.user_data_hash.isnot(None), 1), else_=0)
                ),
                0,
            ).label("identified"),
        ).where(
            and_(
                CAPIDeliveryLog.tenant_id == tenant_id,
                CAPIDeliveryLog.platform.in_(list(platforms)),
                CAPIDeliveryLog.delivery_time >= window.start,
                CAPIDeliveryLog.delivery_time < window.end,
            )
        )
    )
    row = result.one()
    total = int(row.total or 0)
    successful = int(row.successful or 0)
    identified = int(row.identified or 0)
    return DeliveryMeasurement(
        total_events=total,
        successful_events=successful,
        failed_events=max(total - successful, 0),
        identifier_events=identified,
    )


async def measure_freshness(
    db: AsyncSession,
    tenant_id: int,
    as_of: datetime,
) -> FreshnessMeasurement | None:
    """
    Find how old this tenant's newest genuine Meta insights pull is.

    ``Campaign.last_synced_at`` is advanced only when Meta actually returned
    rows and is deliberately left alone on failure, so it is a real freshness
    reading rather than a "the task ran" marker. Soft-deleted campaigns are
    excluded: a deleted campaign's last sync says nothing about live signal.

    Only syncs that had already happened by ``as_of`` count. Without that bound
    a sync performed *after* the window - the 02:00 UTC rollup writing a row for
    yesterday, or a re-run of a historical date - would produce a negative age,
    and clamping that to zero would report the day as perfectly fresh on the
    strength of data that did not exist yet. That is the same "a snapshot dated
    in the future is invalid, not fresh" rule the trust gate applies to rows,
    applied one level down, and it is what makes :func:`day_window`'s promise
    that a re-run reproduces the same numbers true.

    Args:
        db: Async database session.
        tenant_id: Tenant whose campaigns to read.
        as_of: The instant to measure the age against (the window end). Syncs
            later than this are outside the measurement and are ignored.

    Returns:
        The measurement, or None when no campaign of this tenant had recorded a
        successful sync by ``as_of``.
    """
    result = await db.execute(
        select(func.max(Campaign.last_synced_at)).where(
            and_(
                Campaign.tenant_id == tenant_id,
                Campaign.is_deleted.is_(False),
                Campaign.last_synced_at <= as_of,
            )
        )
    )
    newest = result.scalar_one_or_none()
    if newest is None:
        return None
    if newest.tzinfo is None:
        newest = newest.replace(tzinfo=UTC)
    # The SQL bound already excludes later syncs; max(..., 0.0) only absorbs
    # sub-second skew between the bound and this subtraction.
    age_minutes = int(max((as_of - newest).total_seconds(), 0.0) // 60)
    return FreshnessMeasurement(last_synced_at=newest, age_minutes=age_minutes)


async def thresholds_for_tenant(
    db: AsyncSession,
    tenant_id: int,
) -> SignalHealthThresholds:
    """
    Read the band edges this tenant is graded against.

    Onboarding asks the tenant for an autopilot threshold and an alert
    threshold and stores them on ``TenantOnboarding``. Until this function
    existed nothing read them back: the columns, the onboarding form and the
    onboarding chat all collected a number that no code path applied, and a
    tenant who asked for 90 was silently graded at the configured default. The
    same resolved pair now feeds the dashboard summary, the daily rollup's row
    status and the trust gate, so what is shown is what is enforced.

    Args:
        db: Async database session.
        tenant_id: Tenant whose thresholds to read.

    Returns:
        The tenant's thresholds, or the configured defaults when the tenant has
        no onboarding record or stored an unusable pair.
    """
    from app.models.onboarding import TenantOnboarding

    result = await db.execute(
        select(
            TenantOnboarding.trust_threshold_autopilot,
            TenantOnboarding.trust_threshold_alert,
        ).where(TenantOnboarding.tenant_id == tenant_id)
    )
    row = result.first()
    if row is None:
        return SignalHealthThresholds.from_settings()
    return SignalHealthThresholds.resolve(row[0], row[1])


async def measure_connection(
    db: AsyncSession,
    tenant_id: int,
) -> ConnectionMeasurement | None:
    """
    Read the state of this tenant's Meta platform connection.

    All three Meta channels share the single ``platform='meta'`` connection, so
    this measurement is the same for each of them.

    Args:
        db: Async database session.
        tenant_id: Tenant whose connection to read.

    Returns:
        The measurement, or None when the tenant has never connected Meta.
    """
    result = await db.execute(
        select(TenantPlatformConnection).where(
            and_(
                TenantPlatformConnection.tenant_id == tenant_id,
                TenantPlatformConnection.platform == AdPlatform.META.value,
            )
        )
    )
    connection = result.scalars().first()
    if connection is None:
        return None
    return ConnectionMeasurement(
        status=str(connection.status),
        error_count=int(connection.error_count or 0),
        has_last_error=bool(connection.last_error),
        last_error=connection.last_error,
    )


def _build_computation(
    tenant_id: int,
    channel: str,
    window: SignalHealthWindow,
    delivery: DeliveryMeasurement,
    freshness: FreshnessMeasurement | None,
    connection: ConnectionMeasurement | None,
    thresholds: SignalHealthThresholds | None = None,
) -> SignalHealthComputation:
    """
    Turn three measurements into a scored - or explicitly unscorable - result.

    Pure: no database access, so the whole decision table is directly testable.
    Each component is either measured or named as missing; nothing is defaulted.

    Args:
        tenant_id: Tenant the measurements belong to.
        channel: Meta channel the measurements belong to.
        window: The window they were measured over.
        delivery: Counted CAPI delivery attempts (possibly zero).
        freshness: Newest genuine sync, or None when there is none.
        connection: Meta connection state, or None when never connected.
        thresholds: Band edges to grade the composite against; defaults to the
            configured deployment-wide pair.

    Returns:
        The computation, with ``score=None`` when too little was measured.
    """
    bands = thresholds or SignalHealthThresholds.from_settings()
    weights = component_weights()
    components: list[ScoredComponent] = []
    missing: list[MissingInput] = []
    values: dict[str, float | None] = {}

    minimum_events = settings.signal_health_min_delivery_events
    success_rate = delivery.success_rate_pct
    if success_rate is None or delivery.total_events < minimum_events:
        # Describe the window in words: this reason is shown to the tenant, and
        # a raw ISO pair reads as debug output rather than an explanation.
        window_hours = max(round((window.end - window.start).total_seconds() / 3600), 1)
        window_label = (
            f"the {window_hours}h window ending {window.end:%Y-%m-%d %H:%M} UTC"
        )
        if delivery.total_events == 0:
            code = MISSING_NO_DELIVERY_EVENTS
            reason = f"No CAPI events were delivered for {channel} in {window_label}."
        else:
            code = MISSING_TOO_FEW_DELIVERY_EVENTS
            reason = (
                f"Only {delivery.total_events} CAPI delivery attempts for {channel} "
                f"in {window_label}; {minimum_events} are needed before delivery "
                "quality can be scored."
            )
        for name in (COMPONENT_EMQ, COMPONENT_DELIVERY):
            missing.append(
                MissingInput(name=name, weight=weights[name], reason=reason, code=code)
            )
            values[name] = None
    else:
        emq = emq_component_score(success_rate, delivery.identifier_coverage_pct)
        values[COMPONENT_EMQ] = emq
        components.append(
            ScoredComponent(
                name=COMPONENT_EMQ,
                score=emq,
                weight=weights[COMPONENT_EMQ],
                detail={
                    "delivery_success_rate_pct": success_rate,
                    "identifier_coverage_pct": delivery.identifier_coverage_pct,
                    "events": delivery.total_events,
                },
            )
        )
        loss = round(100.0 - (delivery.failure_rate_pct or 0.0), 2)
        values[COMPONENT_DELIVERY] = loss
        components.append(
            ScoredComponent(
                name=COMPONENT_DELIVERY,
                score=loss,
                weight=weights[COMPONENT_DELIVERY],
                detail={
                    "event_loss_pct": delivery.failure_rate_pct,
                    "failed_events": delivery.failed_events,
                },
            )
        )

    freshness_score = freshness_component_score(
        freshness.age_minutes if freshness else None
    )
    if freshness is None or freshness_score is None:
        missing.append(
            MissingInput(
                name=COMPONENT_FRESHNESS,
                weight=weights[COMPONENT_FRESHNESS],
                reason=(
                    "No campaign of this tenant has recorded a successful Meta "
                    "insights sync, so data freshness cannot be measured."
                ),
                code=MISSING_NO_CAMPAIGN_SYNC,
            )
        )
        values[COMPONENT_FRESHNESS] = None
    else:
        values[COMPONENT_FRESHNESS] = freshness_score
        components.append(
            ScoredComponent(
                name=COMPONENT_FRESHNESS,
                score=freshness_score,
                weight=weights[COMPONENT_FRESHNESS],
                detail={
                    "age_minutes": freshness.age_minutes,
                    "last_synced_at": freshness.last_synced_at.isoformat(),
                },
            )
        )

    if connection is None:
        missing.append(
            MissingInput(
                name=COMPONENT_RELIABILITY,
                weight=weights[COMPONENT_RELIABILITY],
                reason=(
                    "No Meta platform connection exists for this tenant, so "
                    "connection health cannot be measured."
                ),
                code=MISSING_NO_PLATFORM_CONNECTION,
            )
        )
        values[COMPONENT_RELIABILITY] = None
    else:
        connection_score = connection_component_score(
            connection.status, connection.error_count, connection.has_last_error
        )
        values[COMPONENT_RELIABILITY] = connection_score
        components.append(
            ScoredComponent(
                name=COMPONENT_RELIABILITY,
                score=connection_score,
                weight=weights[COMPONENT_RELIABILITY],
                detail={
                    "status": connection.status,
                    "error_count": connection.error_count,
                    "has_last_error": connection.has_last_error,
                },
            )
        )

    score, available_weight, required_weight = weighted_score(values)
    return SignalHealthComputation(
        tenant_id=tenant_id,
        channel=channel,
        window=window,
        score=score,
        status=status_for_score(score, bands),
        thresholds=bands,
        components=tuple(components),
        missing=tuple(missing),
        available_weight=available_weight,
        required_weight=required_weight,
        delivery=delivery,
        freshness=freshness,
        connection=connection,
    )


async def compute_signal_health(
    db: AsyncSession,
    tenant_id: int,
    channel: str,
    window: SignalHealthWindow | None = None,
    thresholds: SignalHealthThresholds | None = None,
) -> SignalHealthComputation:
    """
    Compute signal health for one tenant and one Meta channel.

    Args:
        db: Async database session.
        tenant_id: Tenant to compute for. Every query is filtered by it.
        channel: Meta channel (``facebook``, ``instagram`` or ``whatsapp``).
        window: Optional explicit window; defaults to the configured trailing
            delivery window ending now.
        thresholds: Optional band edges; loaded from the tenant's onboarding
            record when not supplied.

    Returns:
        The computation - scored, or explicitly insufficient with the missing
        inputs named.
    """
    window = window or default_window()
    platforms = CHANNEL_DELIVERY_PLATFORMS.get(channel, (channel,))

    delivery = await measure_delivery(db, tenant_id, platforms, window)
    freshness = await measure_freshness(db, tenant_id, window.end)
    connection = await measure_connection(db, tenant_id)
    bands = thresholds or await thresholds_for_tenant(db, tenant_id)

    return _build_computation(
        tenant_id=tenant_id,
        channel=channel,
        window=window,
        delivery=delivery,
        freshness=freshness,
        connection=connection,
        thresholds=bands,
    )


async def compute_tenant_signal_health(
    db: AsyncSession,
    tenant_id: int,
    channels: Sequence[str] = META_CHANNELS,
    window: SignalHealthWindow | None = None,
    thresholds: SignalHealthThresholds | None = None,
) -> dict[str, SignalHealthComputation]:
    """
    Compute signal health for every Meta channel of one tenant.

    Freshness, connection health and the tenant's thresholds are tenant-wide,
    so they are read once and reused; only the delivery evidence differs per
    channel.

    Args:
        db: Async database session.
        tenant_id: Tenant to compute for.
        channels: Channels to compute; defaults to all Meta channels.
        window: Optional explicit window.
        thresholds: Optional band edges; loaded from the tenant's onboarding
            record when not supplied.

    Returns:
        Mapping of channel name to its computation.
    """
    window = window or default_window()
    freshness = await measure_freshness(db, tenant_id, window.end)
    connection = await measure_connection(db, tenant_id)
    bands = thresholds or await thresholds_for_tenant(db, tenant_id)

    computations: dict[str, SignalHealthComputation] = {}
    for channel in channels:
        platforms = CHANNEL_DELIVERY_PLATFORMS.get(channel, (channel,))
        delivery = await measure_delivery(db, tenant_id, platforms, window)
        computations[channel] = _build_computation(
            tenant_id=tenant_id,
            channel=channel,
            window=window,
            delivery=delivery,
            freshness=freshness,
            connection=connection,
            thresholds=bands,
        )
    return computations


def summarise_channels(
    computations: dict[str, SignalHealthComputation],
) -> SignalHealthComputation | None:
    """
    Reduce per-channel computations to the one the tenant should be shown.

    The worst scorable channel wins, mirroring the trust gate: one bad channel
    holds the tenant back. A channel that could not be scored is not treated as
    bad - it simply has no row for the gate to read - so it does not drag the
    tenant-level number down. When no channel could be scored at all, the
    channel with the most measured evidence is returned so the caller can
    report the specific missing inputs.

    Args:
        computations: Per-channel computations, as returned by
            :func:`compute_tenant_signal_health`.

    Returns:
        The representative computation, or None when there were no channels.
    """
    if not computations:
        return None
    scored = [item for item in computations.values() if item.score is not None]
    if scored:
        return min(scored, key=lambda item: item.score or 0.0)
    return max(computations.values(), key=lambda item: item.available_weight)


async def daily_delivery_history(
    db: AsyncSession,
    tenant_id: int,
    platform: str,
    days: int,
    now: datetime | None = None,
) -> list[dict[str, object]]:
    """
    Aggregate this tenant's CAPI deliveries per UTC day.

    Days with no delivery attempts are omitted rather than reported as zero or
    filled in: the caller should show "no data for this day", not a datapoint
    nobody measured.

    Args:
        db: Async database session.
        tenant_id: Tenant whose deliveries to aggregate.
        platform: ``capi_delivery_logs.platform`` value to aggregate.
        days: How many days back to cover.
        now: Optional end of the range; defaults to the current UTC time.

    Returns:
        One dict per day that had traffic, oldest first.
    """
    end = now or datetime.now(UTC)
    start = end - timedelta(days=days)
    day_column = func.date(CAPIDeliveryLog.delivery_time).label("day")

    result = await db.execute(
        select(
            day_column,
            func.count(CAPIDeliveryLog.id).label("total"),
            func.coalesce(
                func.sum(
                    case(
                        (CAPIDeliveryLog.status == DELIVERY_SUCCESS_STATUS, 1), else_=0
                    )
                ),
                0,
            ).label("successful"),
            func.coalesce(
                func.sum(
                    case((CAPIDeliveryLog.user_data_hash.isnot(None), 1), else_=0)
                ),
                0,
            ).label("identified"),
            func.cast(
                func.coalesce(func.avg(CAPIDeliveryLog.latency_ms), 0), Integer
            ).label("avg_latency_ms"),
        )
        .where(
            and_(
                CAPIDeliveryLog.tenant_id == tenant_id,
                CAPIDeliveryLog.platform == platform,
                CAPIDeliveryLog.delivery_time >= start,
                CAPIDeliveryLog.delivery_time <= end,
            )
        )
        .group_by(day_column)
        .order_by(day_column)
    )

    history: list[dict[str, object]] = []
    for row in result.all():
        measurement = DeliveryMeasurement(
            total_events=int(row.total or 0),
            successful_events=int(row.successful or 0),
            failed_events=max(int(row.total or 0) - int(row.successful or 0), 0),
            identifier_events=int(row.identified or 0),
        )
        success_rate = measurement.success_rate_pct
        history.append(
            {
                "date": (
                    row.day.isoformat()
                    if hasattr(row.day, "isoformat")
                    else str(row.day)
                ),
                "events": measurement.total_events,
                "delivery_success_rate_pct": success_rate,
                "identifier_coverage_pct": measurement.identifier_coverage_pct,
                "emq_score": (
                    emq_component_score(
                        success_rate, measurement.identifier_coverage_pct
                    )
                    if success_rate is not None
                    else None
                ),
                "avg_latency_ms": int(row.avg_latency_ms or 0),
            }
        )
    return history
