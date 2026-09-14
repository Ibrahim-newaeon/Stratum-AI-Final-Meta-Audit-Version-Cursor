# =============================================================================
# Stratum AI - Dashboard API Endpoints
# =============================================================================
"""
Unified dashboard API for the main application dashboard.

Provides consolidated endpoints for:
- Overview metrics (spend, revenue, ROAS, conversions)
- Campaign performance summary
- Signal health status
- AI recommendations with approve/reject
- Recent activity and alerts
- Platform breakdown

All data is scoped to the authenticated user's tenant.
"""

import csv
import json
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from io import StringIO
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CurrentUserDep, VerifiedUserDep
from app.core.logging import get_logger
from app.db.session import get_async_session
from app.models import (
    AdPlatform,
    AuditAction,
    AuditLog,
    Campaign,
    CampaignStatus,
)
from app.api.v1.endpoints.oauth import FRONTEND_CONNECT_PATH
from app.models.campaign_builder import ConnectionStatus, TenantPlatformConnection
from app.models.onboarding import OnboardingStatus, TenantOnboarding
from app.schemas import APIResponse
from app.services.signal_health import (
    COMPONENT_EMQ,
    STATUS_CRITICAL,
    STATUS_DEGRADED,
    STATUS_HEALTHY,
    STATUS_INSUFFICIENT_DATA,
    SignalHealthThresholds,
    compute_tenant_signal_health,
    summarise_channels,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])

RECOMMENDATION_DECISION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


def _recommendation_decision_key(tenant_id: int) -> str:
    return f"dashboard:recommendation_decisions:{tenant_id}"


async def _get_recommendation_decisions(tenant_id: int) -> dict[str, str]:
    """Return {recommendation_id: approved|rejected} for this tenant."""
    try:
        from app.api.v1.endpoints.auth import get_redis_client

        redis_client = await get_redis_client()
        raw = await redis_client.hgetall(_recommendation_decision_key(tenant_id))
        if not raw:
            return {}
        return {str(k): str(v) for k, v in raw.items()}
    except Exception:
        logger.warning("recommendation_decisions_unavailable", tenant_id=tenant_id, exc_info=True)
        return {}


async def _set_recommendation_decision(
    tenant_id: int, recommendation_id: str, decision: str
) -> None:
    try:
        from app.api.v1.endpoints.auth import get_redis_client

        redis_client = await get_redis_client()
        key = _recommendation_decision_key(tenant_id)
        await redis_client.hset(key, recommendation_id, decision)
        await redis_client.expire(key, RECOMMENDATION_DECISION_TTL_SECONDS)
    except Exception:
        logger.warning(
            "recommendation_decision_persist_failed",
            tenant_id=tenant_id,
            recommendation_id=recommendation_id,
            exc_info=True,
        )



# =============================================================================
# Enums
# =============================================================================


class TimePeriod(str, Enum):
    """Dashboard time period options."""

    TODAY = "today"
    YESTERDAY = "yesterday"
    LAST_7_DAYS = "7d"
    LAST_30_DAYS = "30d"
    LAST_90_DAYS = "90d"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"


class TrendDirection(str, Enum):
    """Trend direction indicators."""

    UP = "up"
    DOWN = "down"
    STABLE = "stable"


class RecommendationType(str, Enum):
    """Types of AI recommendations."""

    SCALE = "scale"
    WATCH = "watch"
    FIX = "fix"
    PAUSE = "pause"


class RecommendationStatus(str, Enum):
    """Recommendation action status."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"


# =============================================================================
# Pydantic Schemas
# =============================================================================


class MetricValue(BaseModel):
    """A metric with value, change, and trend."""

    value: float
    previous_value: Optional[float] = None
    change_percent: Optional[float] = None
    trend: TrendDirection = TrendDirection.STABLE
    formatted: str = ""  # Formatted display value


class OverviewMetrics(BaseModel):
    """Key performance metrics for dashboard overview."""

    spend: MetricValue
    revenue: MetricValue
    roas: MetricValue
    conversions: MetricValue
    cpa: MetricValue
    impressions: MetricValue
    clicks: MetricValue
    ctr: MetricValue


class SignalHealthSummary(BaseModel):
    """
    Signal health status for the trust gate.

    ``status`` may be ``insufficient_data``, in which case ``overall_score`` is
    ``None`` and ``missing_inputs`` names what could not be measured. That is a
    distinct state from a low score: unknown is not the same as bad, and the UI
    must not render it as a zero. Every populated number here was measured from
    the tenant's own persisted data over ``window_start``..``window_end``.

    ``status`` and ``gate_decision`` answer two different questions and can
    legitimately disagree. ``status`` bands a **live** trailing-window
    measurement; ``gate_decision`` is what the trust gate would actually decide
    right now, and the gate grades the newest ``fact_signal_health_daily``
    snapshot - yesterday's rollup - not the live window. A tenant that started
    delivering events this morning measures healthy while the gate still BLOCKs
    for want of a snapshot, and the UI must show the gate's answer wherever it
    uses gate verbs, or it promises automation that will not run.
    """

    overall_score: int | None = Field(default=None, ge=0, le=100)
    status: str  # healthy, degraded, critical, insufficient_data
    emq_score: float | None = None
    data_freshness_minutes: int | None = None
    api_health: bool | None = None
    issues: list[str] = []
    autopilot_enabled: bool = False
    missing_inputs: list[str] = []
    # Stable codes for the same gaps, so the UI can render them in the viewer's
    # language instead of showing the server's English sentence.
    missing_input_codes: list[str] = []
    components: dict[str, float] = {}
    channel: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    # What the trust gate decides for this tenant right now: "pass", "hold" or
    # "block". Sourced from the gate itself, not re-derived from the live score.
    gate_decision: str | None = None
    gate_reason: str | None = None
    gate_health_date: date | None = None
    # The band edges applied, which are the tenant's own onboarding overrides
    # when it set any.
    healthy_threshold: float | None = None
    degraded_threshold: float | None = None


class PlatformSummary(BaseModel):
    """Performance summary for a single platform."""

    platform: str
    status: str  # connected, disconnected, error
    spend: float = 0
    revenue: float = 0
    roas: Optional[float] = None
    campaigns_count: int = 0
    last_synced_at: Optional[datetime] = None


class CampaignSummaryItem(BaseModel):
    """Summary of a campaign for dashboard listing."""

    id: int
    name: str
    platform: str
    status: str
    spend: float = 0
    revenue: float = 0
    roas: Optional[float] = None
    conversions: int = 0
    trend: TrendDirection = TrendDirection.STABLE
    scaling_score: Optional[float] = None
    recommendation: Optional[str] = None


class RecommendationItem(BaseModel):
    """AI recommendation for action."""

    id: str
    type: RecommendationType
    entity_type: str  # campaign, adset, ad
    entity_id: int
    entity_name: str
    platform: str
    title: str
    description: str
    impact_estimate: Optional[str] = None
    confidence: float = Field(ge=0, le=1)
    status: RecommendationStatus = RecommendationStatus.PENDING
    created_at: datetime


class ActivityItem(BaseModel):
    """Recent activity/event item."""

    id: int
    type: str  # action, alert, system
    title: str
    description: Optional[str] = None
    severity: Optional[str] = None  # info, warning, error, success
    timestamp: datetime
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None


class QuickAction(BaseModel):
    """Quick action button for dashboard."""

    id: str
    label: str
    icon: str
    action: str  # route or action identifier
    count: Optional[int] = None  # Badge count if applicable


class DashboardOverviewResponse(BaseModel):
    """Complete dashboard overview response."""

    # Account status
    onboarding_complete: bool = False
    has_connected_platforms: bool = False
    has_campaigns: bool = False

    # Time period
    period: str
    period_label: str
    date_range: dict[str, str]

    # Core metrics
    metrics: OverviewMetrics

    # Signal health
    signal_health: SignalHealthSummary

    # Platform breakdown
    platforms: list[PlatformSummary] = []

    # Quick stats
    total_campaigns: int = 0
    active_campaigns: int = 0
    pending_recommendations: int = 0
    active_alerts: int = 0


class CampaignPerformanceResponse(BaseModel):
    """Campaign performance list response."""

    campaigns: list[CampaignSummaryItem]
    total: int
    page: int
    page_size: int
    sort_by: str
    sort_order: str


class RecommendationsResponse(BaseModel):
    """Recommendations list response."""

    recommendations: list[RecommendationItem]
    total: int
    by_type: dict[str, int]


class ActivityFeedResponse(BaseModel):
    """Activity feed response."""

    activities: list[ActivityItem]
    total: int
    has_more: bool


class QuickActionsResponse(BaseModel):
    """Quick actions for dashboard."""

    actions: list[QuickAction]


class ExportFormat(str, Enum):
    """Export format options."""

    CSV = "csv"
    JSON = "json"


class DashboardExportRequest(BaseModel):
    """Dashboard export request."""

    format: ExportFormat = ExportFormat.CSV
    period: TimePeriod = TimePeriod.LAST_30_DAYS
    include_campaigns: bool = True
    include_metrics: bool = True
    include_recommendations: bool = True


# =============================================================================
# Helper Functions
# =============================================================================


def get_date_range(period: TimePeriod) -> tuple[date, date]:
    """Get start and end dates for a time period."""
    today = datetime.now(UTC).date()

    if period == TimePeriod.TODAY:
        return today, today
    elif period == TimePeriod.YESTERDAY:
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday
    elif period == TimePeriod.LAST_7_DAYS:
        return today - timedelta(days=6), today
    elif period == TimePeriod.LAST_30_DAYS:
        return today - timedelta(days=29), today
    elif period == TimePeriod.LAST_90_DAYS:
        return today - timedelta(days=89), today
    elif period == TimePeriod.THIS_MONTH:
        return today.replace(day=1), today
    elif period == TimePeriod.LAST_MONTH:
        first_of_month = today.replace(day=1)
        last_month_end = first_of_month - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return last_month_start, last_month_end
    else:
        return today - timedelta(days=6), today


def get_previous_period(start: date, end: date) -> tuple[date, date]:
    """Get the previous period for comparison."""
    period_length = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=period_length - 1)
    return prev_start, prev_end


def calculate_change(current: float, previous: float) -> tuple[float, TrendDirection]:
    """Calculate percentage change and trend direction."""
    if previous == 0:
        if current > 0:
            return 100.0, TrendDirection.UP
        return 0.0, TrendDirection.STABLE

    change = ((current - previous) / previous) * 100

    if change > 1:
        trend = TrendDirection.UP
    elif change < -1:
        trend = TrendDirection.DOWN
    else:
        trend = TrendDirection.STABLE

    return round(change, 1), trend


def format_currency(value: float, currency: str = "USD") -> str:
    """Format value as currency."""
    if value >= 1000000:
        return f"${value/1000000:.1f}M"
    elif value >= 1000:
        return f"${value/1000:.1f}K"
    else:
        return f"${value:.2f}"


def format_number(value: float) -> str:
    """Format large numbers with K/M suffix."""
    if value >= 1000000:
        return f"{value/1000000:.1f}M"
    elif value >= 1000:
        return f"{value/1000:.1f}K"
    else:
        return f"{value:.0f}"


def format_percentage(value: float) -> str:
    """Format as percentage."""
    return f"{value:.2f}%"


async def build_signal_health_summary(
    db: AsyncSession,
    tenant_id: int,
    onboarding: TenantOnboarding | None,
) -> SignalHealthSummary:
    """
    Build the dashboard's signal health payload from measured data.

    The single place this endpoint module computes signal health, so the
    overview card and the dedicated signal health endpoint cannot disagree.
    Both used to return a hardcoded ``overall_score=85``, ``emq_score=0.92``
    and ``data_freshness_minutes=5`` for any tenant that merely had campaigns
    or a connected platform - a healthy-looking trust gate for a tenant that
    had never sent a single event.

    The tenant-level number mirrors the trust gate: the worst channel that
    could be scored wins, and a channel with no evidence of its own is
    reported rather than counted against the tenant (the gate likewise only
    grades the rows that exist). When no channel could be scored at all the
    status is ``insufficient_data`` with the missing inputs named, and no
    score is sent - a zero would read as "terrible" when the truth is
    "unknown".

    The gate's own current decision is published alongside the measurement.
    They are different questions: this function measures a live trailing
    window, while the gate grades yesterday's ``fact_signal_health_daily``
    snapshot. Publishing only the live measurement let the UI show "PASS -
    autopilot executes" to a tenant whose every automation the gate was
    BLOCKing for want of a snapshot, and the divergence was always in the
    flattering direction on day one.

    Args:
        db: Async database session
        tenant_id: Tenant to measure; every underlying query is filtered by it
        onboarding: The tenant's onboarding record, for the automation mode

    Returns:
        The summary, either scored or explicitly insufficient.
    """
    # Import here: app.tasks.apply_actions_queue pulls in the Celery app, and
    # the endpoint modules must stay importable without it.
    from app.tasks.apply_actions_queue import check_signal_health

    thresholds = SignalHealthThresholds.resolve(
        getattr(onboarding, "trust_threshold_autopilot", None),
        getattr(onboarding, "trust_threshold_alert", None),
    )
    computations = await compute_tenant_signal_health(
        db, tenant_id, thresholds=thresholds
    )
    representative = summarise_channels(computations)
    gate = await check_signal_health(db, tenant_id)

    if representative is None:
        return SignalHealthSummary(
            status=STATUS_INSUFFICIENT_DATA,
            issues=["No Meta channels are configured for this tenant."],
            missing_inputs=["No Meta channels are configured for this tenant."],
            missing_input_codes=["no_meta_channels"],
            gate_decision=gate.decision.value,
            gate_reason=gate.reason,
            gate_health_date=gate.health_date,
            healthy_threshold=thresholds.healthy,
            degraded_threshold=thresholds.degraded,
        )

    unscored = sorted(
        channel for channel, item in computations.items() if item.insufficient_data
    )
    components = representative.component_scores()
    connection = representative.connection

    issues: list[str] = []
    # Two components can share one cause (both delivery components go missing
    # together when there is no CAPI traffic); say it once.
    missing_inputs = list(dict.fromkeys(representative.missing_inputs))

    if representative.insufficient_data:
        issues.extend(missing_inputs)
    else:
        if unscored:
            issues.append(
                "No signal data yet for: " + ", ".join(unscored) + "."
            )
        if connection is not None and connection.error_count:
            issues.append(
                f"The Meta connection has recorded {connection.error_count} error(s)."
            )
        if representative.status == STATUS_DEGRADED:
            issues.append("Signal health is below the autopilot threshold; actions are held.")
        elif representative.status == STATUS_CRITICAL:
            issues.append("Signal health is critical; automated actions are blocked.")

    # Autopilot only actually runs when the gate lets it, so this flag follows
    # the gate rather than the live measurement.
    autopilot_enabled = bool(
        onboarding
        and onboarding.automation_mode == "autopilot"
        and gate.may_execute
    )
    if representative.status == STATUS_HEALTHY and not gate.may_execute:
        issues.append(gate.reason)

    return SignalHealthSummary(
        overall_score=(
            round(representative.score) if representative.score is not None else None
        ),
        status=representative.status,
        emq_score=components.get(COMPONENT_EMQ),
        data_freshness_minutes=(
            representative.freshness.age_minutes if representative.freshness else None
        ),
        # None means "not measured", which is not the same as "offline".
        api_health=(
            None
            if connection is None
            else connection.is_connected and not connection.has_last_error
        ),
        issues=issues,
        autopilot_enabled=autopilot_enabled,
        missing_inputs=missing_inputs,
        missing_input_codes=representative.missing_input_codes,
        components=components,
        channel=representative.channel,
        window_start=representative.window.start,
        window_end=representative.window.end,
        gate_decision=gate.decision.value,
        gate_reason=gate.reason,
        gate_health_date=gate.health_date,
        healthy_threshold=thresholds.healthy,
        degraded_threshold=thresholds.degraded,
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/overview", response_model=APIResponse[DashboardOverviewResponse])
async def get_dashboard_overview(
    current_user: CurrentUserDep,
    period: TimePeriod = Query(default=TimePeriod.LAST_7_DAYS),
    db: AsyncSession = Depends(get_async_session),
):
    """
    Get the main dashboard overview.

    Returns key metrics, signal health, platform breakdown, and quick stats.
    """
    tenant_id = current_user.tenant_id

    # Get date range
    start_date, end_date = get_date_range(period)
    prev_start, prev_end = get_previous_period(start_date, end_date)

    # Check onboarding status
    onboarding_result = await db.execute(
        select(TenantOnboarding).where(TenantOnboarding.tenant_id == tenant_id)
    )
    onboarding = onboarding_result.scalar_one_or_none()
    onboarding_complete = onboarding and onboarding.status == OnboardingStatus.COMPLETED

    # Get connected platforms
    platforms_result = await db.execute(
        select(TenantPlatformConnection).where(
            and_(
                TenantPlatformConnection.tenant_id == tenant_id,
                TenantPlatformConnection.status == ConnectionStatus.CONNECTED,
            )
        )
    )
    connected_platforms = platforms_result.scalars().all()
    has_connected_platforms = len(connected_platforms) > 0

    # Get campaigns and aggregate metrics
    campaigns_result = await db.execute(
        select(Campaign).where(
            and_(
                Campaign.tenant_id == tenant_id,
                Campaign.is_deleted == False,
            )
        )
    )
    campaigns = campaigns_result.scalars().all()
    has_campaigns = len(campaigns) > 0

    # Aggregate current period metrics
    current_spend = sum(c.total_spend_cents or 0 for c in campaigns) / 100
    current_revenue = sum(c.revenue_cents or 0 for c in campaigns) / 100
    current_conversions = sum(c.conversions or 0 for c in campaigns)
    current_impressions = sum(c.impressions or 0 for c in campaigns)
    current_clicks = sum(c.clicks or 0 for c in campaigns)

    # Calculate derived metrics
    current_roas = current_revenue / current_spend if current_spend > 0 else 0
    current_cpa = current_spend / current_conversions if current_conversions > 0 else 0
    current_ctr = (current_clicks / current_impressions * 100) if current_impressions > 0 else 0

    # For demo, use mock previous period (in production, query historical data)
    prev_spend = current_spend * 0.9  # Mock 10% growth
    prev_revenue = current_revenue * 0.85
    prev_conversions = int(current_conversions * 0.88)
    prev_impressions = int(current_impressions * 0.95)
    prev_clicks = int(current_clicks * 0.92)

    prev_roas = prev_revenue / prev_spend if prev_spend > 0 else 0
    prev_cpa = prev_spend / prev_conversions if prev_conversions > 0 else 0
    prev_ctr = (prev_clicks / prev_impressions * 100) if prev_impressions > 0 else 0

    # Build metrics with changes
    def build_metric(current: float, previous: float, formatter=format_currency) -> MetricValue:
        change, trend = calculate_change(current, previous)
        return MetricValue(
            value=current,
            previous_value=previous,
            change_percent=change,
            trend=trend,
            formatted=formatter(current),
        )

    metrics = OverviewMetrics(
        spend=build_metric(current_spend, prev_spend),
        revenue=build_metric(current_revenue, prev_revenue),
        roas=build_metric(current_roas, prev_roas, lambda v: f"{v:.2f}x"),
        conversions=build_metric(current_conversions, prev_conversions, format_number),
        cpa=build_metric(current_cpa, prev_cpa),
        impressions=build_metric(current_impressions, prev_impressions, format_number),
        clicks=build_metric(current_clicks, prev_clicks, format_number),
        ctr=build_metric(current_ctr, prev_ctr, format_percentage),
    )

    # Signal health, measured from this tenant's own persisted data. Having
    # campaigns is not evidence that the signals behind them are healthy.
    signal_health = await build_signal_health_summary(db, tenant_id, onboarding)

    # Platform breakdown
    platforms_summary = []
    for platform in AdPlatform:
        connection = next((c for c in connected_platforms if c.platform == platform), None)

        platform_campaigns = [c for c in campaigns if c.platform == platform]
        platform_spend = sum(c.total_spend_cents or 0 for c in platform_campaigns) / 100
        platform_revenue = sum(c.revenue_cents or 0 for c in platform_campaigns) / 100

        platforms_summary.append(
            PlatformSummary(
                platform=platform.value,
                status="connected" if connection else "disconnected",
                spend=platform_spend,
                revenue=platform_revenue,
                roas=platform_revenue / platform_spend if platform_spend > 0 else None,
                campaigns_count=len(platform_campaigns),
                last_synced_at=connection.last_refreshed_at if connection else None,
            )
        )

    # Campaign stats
    active_campaigns = len([c for c in campaigns if c.status == CampaignStatus.ACTIVE])

    # Period label
    period_labels = {
        TimePeriod.TODAY: "Today",
        TimePeriod.YESTERDAY: "Yesterday",
        TimePeriod.LAST_7_DAYS: "Last 7 Days",
        TimePeriod.LAST_30_DAYS: "Last 30 Days",
        TimePeriod.LAST_90_DAYS: "Last 90 Days",
        TimePeriod.THIS_MONTH: "This Month",
        TimePeriod.LAST_MONTH: "Last Month",
    }

    return APIResponse(
        success=True,
        data=DashboardOverviewResponse(
            onboarding_complete=onboarding_complete,
            has_connected_platforms=has_connected_platforms,
            has_campaigns=has_campaigns,
            period=period.value,
            period_label=period_labels.get(period, "Last 7 Days"),
            date_range={
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
            },
            metrics=metrics,
            signal_health=signal_health,
            platforms=platforms_summary,
            total_campaigns=len(campaigns),
            active_campaigns=active_campaigns,
            pending_recommendations=3 if has_campaigns else 0,  # Mock
            active_alerts=1 if has_campaigns else 0,  # Mock
        ),
    )


@router.get("/campaigns", response_model=APIResponse[CampaignPerformanceResponse])
async def get_campaign_performance(
    current_user: CurrentUserDep,
    period: TimePeriod = Query(default=TimePeriod.LAST_7_DAYS),
    platform: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    sort_by: str = Query(default="spend"),
    sort_order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_async_session),
):
    """
    Get campaign performance for dashboard.

    Returns paginated list of campaigns with key metrics and recommendations.
    """
    tenant_id = current_user.tenant_id

    # Build query
    query = select(Campaign).where(
        and_(
            Campaign.tenant_id == tenant_id,
            Campaign.is_deleted == False,
        )
    )

    # Apply filters
    if platform:
        try:
            platform_enum = AdPlatform(platform.lower())
            query = query.where(Campaign.platform == platform_enum)
        except ValueError:
            pass

    if status_filter:
        try:
            status_enum = CampaignStatus(status_filter.lower())
            query = query.where(Campaign.status == status_enum)
        except ValueError:
            pass

    # Apply sorting
    sort_column = getattr(Campaign, sort_by, Campaign.total_spend_cents)
    if sort_order.lower() == "desc":
        query = query.order_by(desc(sort_column))
    else:
        query = query.order_by(sort_column)

    # Get total count
    count_result = await db.execute(select(func.count()).select_from(query.subquery()))
    total = count_result.scalar() or 0

    # Apply pagination
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    campaigns = result.scalars().all()

    # Build response
    campaign_items = []
    for c in campaigns:
        spend = (c.total_spend_cents or 0) / 100
        revenue = (c.revenue_cents or 0) / 100

        # Determine recommendation based on ROAS
        roas = revenue / spend if spend > 0 else 0
        if roas >= 3:
            recommendation = "scale"
            scaling_score = 0.8
        elif roas >= 1.5:
            recommendation = "watch"
            scaling_score = 0.2
        elif roas > 0:
            recommendation = "fix"
            scaling_score = -0.3
        else:
            recommendation = None
            scaling_score = None

        campaign_items.append(
            CampaignSummaryItem(
                id=c.id,
                name=c.name,
                platform=c.platform.value,
                status=c.status.value,
                spend=spend,
                revenue=revenue,
                roas=roas if spend > 0 else None,
                conversions=c.conversions or 0,
                trend=TrendDirection.UP if roas >= 2 else TrendDirection.STABLE,
                scaling_score=scaling_score,
                recommendation=recommendation,
            )
        )

    return APIResponse(
        success=True,
        data=CampaignPerformanceResponse(
            campaigns=campaign_items,
            total=total,
            page=page,
            page_size=page_size,
            sort_by=sort_by,
            sort_order=sort_order,
        ),
    )


@router.get("/recommendations", response_model=APIResponse[RecommendationsResponse])
async def get_recommendations(
    current_user: VerifiedUserDep,
    type_filter: Optional[RecommendationType] = Query(None, alias="type"),
    status_filter: Optional[RecommendationStatus] = Query(None, alias="status"),
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_async_session),
):
    """
    Get AI recommendations for the dashboard.

    Returns prioritized recommendations for campaigns that need attention.
    """
    tenant_id = current_user.tenant_id

    # Get campaigns to generate recommendations
    result = await db.execute(
        select(Campaign)
        .where(
            and_(
                Campaign.tenant_id == tenant_id,
                Campaign.is_deleted == False,
                Campaign.status == CampaignStatus.ACTIVE,
            )
        )
        .limit(50)
    )
    campaigns = result.scalars().all()

    # Generate recommendations based on campaign performance
    recommendations = []
    by_type = {t.value: 0 for t in RecommendationType}

    for c in campaigns:
        spend = (c.total_spend_cents or 0) / 100
        revenue = (c.revenue_cents or 0) / 100
        roas = revenue / spend if spend > 0 else 0

        # Determine recommendation type
        if roas >= 3:
            rec_type = RecommendationType.SCALE
            title = f"Scale {c.name}"
            description = (
                f"ROAS of {roas:.2f}x exceeds target. Consider increasing budget by 20-30%."
            )
            confidence = 0.85
            impact = f"+${spend * 0.25:.0f} potential revenue"
        elif roas < 1:
            rec_type = RecommendationType.FIX
            title = f"Fix {c.name}"
            description = f"ROAS of {roas:.2f}x is below breakeven. Review targeting and creatives."
            confidence = 0.75
            impact = f"Save ${spend * 0.3:.0f} in wasted spend"
        elif roas < 1.5:
            rec_type = RecommendationType.WATCH
            title = f"Watch {c.name}"
            description = f"ROAS of {roas:.2f}x is marginal. Monitor for 24-48 hours."
            confidence = 0.65
            impact = None
        else:
            continue  # Skip stable campaigns

        # Apply filters
        if type_filter and rec_type != type_filter:
            continue

        by_type[rec_type.value] += 1

        recommendations.append(
            RecommendationItem(
                id=f"rec_{c.id}_{rec_type.value}",
                type=rec_type,
                entity_type="campaign",
                entity_id=c.id,
                entity_name=c.name,
                platform=c.platform.value,
                title=title,
                description=description,
                impact_estimate=impact,
                confidence=confidence,
                status=RecommendationStatus.PENDING,
                created_at=datetime.now(UTC),
            )
        )

    # Apply persisted approve/reject decisions so actions survive refetch.
    decisions = await _get_recommendation_decisions(tenant_id)
    for rec in recommendations:
        decision = decisions.get(rec.id)
        if decision == "approved":
            rec.status = RecommendationStatus.APPROVED
        elif decision == "rejected":
            rec.status = RecommendationStatus.REJECTED

    if status_filter:
        recommendations = [r for r in recommendations if r.status == status_filter]
    else:
        recommendations = [
            r for r in recommendations if r.status == RecommendationStatus.PENDING
        ]

    # Sort by confidence and limit
    recommendations.sort(key=lambda r: r.confidence, reverse=True)
    recommendations = recommendations[:limit]

    return APIResponse(
        success=True,
        data=RecommendationsResponse(
            recommendations=recommendations,
            total=len(recommendations),
            by_type=by_type,
        ),
    )


@router.post("/recommendations/{recommendation_id}/approve")
async def approve_recommendation(
    recommendation_id: str,
    current_user: VerifiedUserDep,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Approve a recommendation for execution.

    In autopilot mode, this queues the action for automatic execution.
    In manual/assisted mode, this marks it as approved.
    """
    # Parse recommendation ID to get campaign and type
    parts = recommendation_id.split("_")
    if len(parts) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid recommendation ID"
        )

    await _set_recommendation_decision(
        current_user.tenant_id, recommendation_id, "approved"
    )

    logger.info(
        "recommendation_approved",
        recommendation_id=recommendation_id,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
    )

    return APIResponse(
        success=True,
        data={"message": "Recommendation approved", "status": "approved"},
    )


@router.post("/recommendations/{recommendation_id}/reject")
async def reject_recommendation(
    recommendation_id: str,
    current_user: VerifiedUserDep,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Reject a recommendation.
    """
    await _set_recommendation_decision(
        current_user.tenant_id, recommendation_id, "rejected"
    )

    logger.info(
        "recommendation_rejected",
        recommendation_id=recommendation_id,
        user_id=current_user.id,
        tenant_id=current_user.tenant_id,
    )

    return APIResponse(
        success=True,
        data={"message": "Recommendation rejected", "status": "rejected"},
    )


@router.get("/activity", response_model=APIResponse[ActivityFeedResponse])
async def get_activity_feed(
    current_user: CurrentUserDep,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_async_session),
):
    """
    Get recent activity feed for the dashboard.

    Returns recent actions, alerts, and system events.
    """
    tenant_id = current_user.tenant_id

    # Get audit logs for activity
    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id)
        .order_by(desc(AuditLog.created_at))
        .offset(offset)
        .limit(limit + 1)  # Get one extra to check has_more
    )
    logs = result.scalars().all()

    has_more = len(logs) > limit
    logs = logs[:limit]

    # Build activity items
    activities = []
    for log in logs:
        # Determine activity type and severity
        if log.action in [AuditAction.LOGIN, AuditAction.LOGOUT]:
            activity_type = "auth"
            severity = "info"
            title = "User logged in" if log.action == AuditAction.LOGIN else "User logged out"
        elif log.action == AuditAction.CREATE:
            activity_type = "action"
            severity = "success"
            title = f"Created {log.resource_type}"
        elif log.action == AuditAction.UPDATE:
            activity_type = "action"
            severity = "info"
            title = f"Updated {log.resource_type}"
        elif log.action == AuditAction.DELETE:
            activity_type = "action"
            severity = "warning"
            title = f"Deleted {log.resource_type}"
        else:
            activity_type = "system"
            severity = "info"
            title = f"{log.action.value.title()} {log.resource_type}"

        activities.append(
            ActivityItem(
                id=log.id,
                type=activity_type,
                title=title,
                description=None,
                severity=severity,
                timestamp=log.created_at,
                entity_type=log.resource_type,
                entity_id=log.resource_id,
            )
        )

    # Get total count
    count_result = await db.execute(select(func.count()).where(AuditLog.tenant_id == tenant_id))
    total = count_result.scalar() or 0

    return APIResponse(
        success=True,
        data=ActivityFeedResponse(
            activities=activities,
            total=total,
            has_more=has_more,
        ),
    )


@router.get("/quick-actions", response_model=APIResponse[QuickActionsResponse])
async def get_quick_actions(
    current_user: CurrentUserDep,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Get quick action buttons for the dashboard.

    Returns contextual actions based on the current state.
    """
    tenant_id = current_user.tenant_id

    actions = []

    # Check onboarding status
    onboarding_result = await db.execute(
        select(TenantOnboarding).where(TenantOnboarding.tenant_id == tenant_id)
    )
    onboarding = onboarding_result.scalar_one_or_none()

    if not onboarding or onboarding.status != OnboardingStatus.COMPLETED:
        actions.append(
            QuickAction(
                id="complete_onboarding",
                label="Complete Setup",
                icon="settings",
                action="/onboarding",
            )
        )

    # Check for connected platforms
    platforms_result = await db.execute(
        select(TenantPlatformConnection).where(
            and_(
                TenantPlatformConnection.tenant_id == tenant_id,
                TenantPlatformConnection.status == ConnectionStatus.CONNECTED,
            )
        )
    )
    connected = platforms_result.scalars().all()

    if len(connected) == 0:
        actions.append(
            QuickAction(
                id="connect_platform",
                label="Connect Platform",
                icon="link",
                action=FRONTEND_CONNECT_PATH,
            )
        )

    # Check for campaigns
    campaigns_result = await db.execute(
        select(func.count()).where(
            and_(
                Campaign.tenant_id == tenant_id,
                Campaign.is_deleted == False,
            )
        )
    )
    campaign_count = campaigns_result.scalar() or 0

    if campaign_count == 0 and len(connected) > 0:
        # SPA route: Campaigns page auto-queues Meta discovery when discover=1.
        # Do not use /campaigns/sync — that path is not a frontend route (404).
        actions.append(
            QuickAction(
                id="sync_campaigns",
                label="Sync Campaigns",
                icon="refresh",
                action="/dashboard/campaigns?discover=1",
            )
        )

    # Standard actions — paths must match App.tsx dashboard routes
    actions.extend(
        [
            QuickAction(
                id="create_campaign",
                label="New Campaign",
                icon="plus",
                action="/dashboard/campaigns?create=1",
            ),
            QuickAction(
                id="view_reports",
                label="Reports",
                icon="chart",
                action="/dashboard/custom-reports",
            ),
            QuickAction(
                id="manage_rules",
                label="Automation",
                icon="zap",
                action="/dashboard/rules",
            ),
        ]
    )

    return APIResponse(
        success=True,
        data=QuickActionsResponse(actions=actions),
    )


@router.get("/signal-health", response_model=APIResponse[SignalHealthSummary])
async def get_signal_health(
    current_user: CurrentUserDep,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Get detailed signal health status.

    Returns the current trust gate status including EMQ, data freshness,
    and any issues that may affect autopilot.

    Everything here is measured from the tenant's own persisted data by
    :func:`build_signal_health_summary`. When there is not enough of it the
    response carries ``status="insufficient_data"``, no score and the list of
    missing inputs - it does not simulate a healthy tenant, which is what this
    endpoint did before (``overall_score = 85``, ``emq_score = 0.92``,
    ``data_freshness = 5``, under the comment "Simulated healthy status").
    """
    tenant_id = current_user.tenant_id

    # Get onboarding settings for the tenant's automation mode
    onboarding_result = await db.execute(
        select(TenantOnboarding).where(TenantOnboarding.tenant_id == tenant_id)
    )
    onboarding = onboarding_result.scalar_one_or_none()

    return APIResponse(
        success=True,
        data=await build_signal_health_summary(db, tenant_id, onboarding),
    )


@router.post("/export")
async def export_dashboard(
    request: DashboardExportRequest,
    current_user: CurrentUserDep,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Export dashboard data as CSV or JSON.

    Returns a file download with campaigns, metrics, and recommendations.
    """
    tenant_id = current_user.tenant_id

    # Get date range
    start_date, end_date = get_date_range(request.period)

    # Get campaigns
    campaigns_result = await db.execute(
        select(Campaign).where(
            and_(
                Campaign.tenant_id == tenant_id,
                Campaign.is_deleted == False,
            )
        )
    )
    campaigns = campaigns_result.scalars().all()

    # Build export data
    export_data = {
        "export_date": datetime.now(UTC).isoformat(),
        "period": request.period.value,
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
    }

    # Add metrics summary
    if request.include_metrics:
        total_spend = sum(c.total_spend_cents or 0 for c in campaigns) / 100
        total_revenue = sum(c.revenue_cents or 0 for c in campaigns) / 100
        total_conversions = sum(c.conversions or 0 for c in campaigns)
        total_impressions = sum(c.impressions or 0 for c in campaigns)
        total_clicks = sum(c.clicks or 0 for c in campaigns)

        export_data["metrics"] = {
            "total_spend": total_spend,
            "total_revenue": total_revenue,
            "roas": total_revenue / total_spend if total_spend > 0 else 0,
            "conversions": total_conversions,
            "cpa": total_spend / total_conversions if total_conversions > 0 else 0,
            "impressions": total_impressions,
            "clicks": total_clicks,
            "ctr": (total_clicks / total_impressions * 100) if total_impressions > 0 else 0,
        }

    # Add campaigns
    if request.include_campaigns:
        export_data["campaigns"] = [
            {
                "id": c.id,
                "name": c.name,
                "platform": c.platform.value,
                "status": c.status.value,
                "spend": (c.total_spend_cents or 0) / 100,
                "revenue": (c.revenue_cents or 0) / 100,
                "conversions": c.conversions or 0,
                "impressions": c.impressions or 0,
                "clicks": c.clicks or 0,
                "roas": ((c.revenue_cents or 0) / (c.total_spend_cents or 1))
                if (c.total_spend_cents or 0) > 0
                else 0,
            }
            for c in campaigns
        ]

    # Add recommendations (mock for demo)
    if request.include_recommendations:
        recommendations = []
        for c in campaigns:
            spend = (c.total_spend_cents or 0) / 100
            revenue = (c.revenue_cents or 0) / 100
            roas = revenue / spend if spend > 0 else 0

            if roas >= 3:
                recommendations.append(
                    {
                        "campaign": c.name,
                        "type": "scale",
                        "description": f"Scale budget by 20-30% - ROAS {roas:.2f}x",
                    }
                )
            elif roas < 1 and spend > 0:
                recommendations.append(
                    {
                        "campaign": c.name,
                        "type": "fix",
                        "description": f"Review targeting - ROAS {roas:.2f}x below breakeven",
                    }
                )

        export_data["recommendations"] = recommendations

    # Generate response based on format
    if request.format == ExportFormat.CSV:
        # Create CSV
        output = StringIO()
        writer = csv.writer(output)

        # Write metrics header
        if request.include_metrics:
            writer.writerow(["=== METRICS SUMMARY ==="])
            writer.writerow(["Metric", "Value"])
            for key, value in export_data.get("metrics", {}).items():
                if isinstance(value, float):
                    writer.writerow([key, f"{value:.2f}"])
                else:
                    writer.writerow([key, value])
            writer.writerow([])

        # Write campaigns
        if request.include_campaigns and export_data.get("campaigns"):
            writer.writerow(["=== CAMPAIGNS ==="])
            headers = [
                "ID",
                "Name",
                "Platform",
                "Status",
                "Spend",
                "Revenue",
                "ROAS",
                "Conversions",
                "Impressions",
                "Clicks",
            ]
            writer.writerow(headers)
            for campaign in export_data["campaigns"]:
                writer.writerow(
                    [
                        campaign["id"],
                        campaign["name"],
                        campaign["platform"],
                        campaign["status"],
                        f"${campaign['spend']:.2f}",
                        f"${campaign['revenue']:.2f}",
                        f"{campaign['roas']:.2f}x",
                        campaign["conversions"],
                        campaign["impressions"],
                        campaign["clicks"],
                    ]
                )
            writer.writerow([])

        # Write recommendations
        if request.include_recommendations and export_data.get("recommendations"):
            writer.writerow(["=== RECOMMENDATIONS ==="])
            writer.writerow(["Campaign", "Type", "Description"])
            for rec in export_data["recommendations"]:
                writer.writerow([rec["campaign"], rec["type"], rec["description"]])

        output.seek(0)
        filename = f"stratum-dashboard-export-{datetime.now(UTC).date().isoformat()}.csv"

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    else:
        # JSON format
        filename = f"stratum-dashboard-export-{datetime.now(UTC).date().isoformat()}.json"

        return StreamingResponse(
            iter([json.dumps(export_data, indent=2, default=str)]),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
