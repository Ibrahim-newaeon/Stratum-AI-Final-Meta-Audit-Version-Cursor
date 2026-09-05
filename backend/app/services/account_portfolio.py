# =============================================================================
# Stratum AI - Account Manager Portfolio Metrics
# =============================================================================
"""
The commercial and trust metrics an account manager sees for a list of tenants.

One batched read per metric across the whole list, rather than a per-tenant
fan-out, because the portfolio view asks the same question of every tenant it
renders at once.

**Every field here is either measured or ``None``.** ``None`` means "nothing in
this deployment can answer that for this tenant", and the caller must render it
as unknown. It is never rounded down to ``0``: a zero is a claim - no spend, no
budget held, no incidents - and the whole point of this module is that the
portfolio stopped making claims it could not support. Where a zero *is* the
measurement (a tenant with no active pacing alert genuinely has none) it is
returned as a zero, and the docstring for that field says so.

The trust numbers are not recomputed here. ``compute_portfolio_signal_health``
and ``check_signal_health_for_tenants`` are the batched forms of the very
functions the tenant's own dashboard and the autopilot use, so a tenant cannot
be ``insufficient_data`` on its dashboard and scored in its account manager's
list.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.autopilot.service import ActionStatus
from app.base_models import Campaign, CampaignMetric, User
from app.models.onboarding import TenantOnboarding
from app.models.pacing import AlertStatus, PacingAlert
from app.models.trust_layer import FactActionsQueue, FactSignalHealthDaily
from app.services.signal_health import (
    COMPONENT_EMQ,
    STATUS_INSUFFICIENT_DATA,
    compute_portfolio_signal_health,
    default_window,
    summarise_channels,
)
from app.services.tenant_revenue import (
    plan_display_name,
    tenant_mrr,
    tenant_renewal_date,
    tenant_subscription_status,
)

__all__ = [
    "EMQ_TREND_LOOKBACK_DAYS",
    "SPEND_WINDOW_DAYS",
    "TenantPortfolioMetrics",
    "build_tenant_portfolio",
]

# How much history the spend, ROAS and ROAS-trend figures cover. The trailing
# window is compared against the window of equal width before it, so "ROAS
# trend" is a like-for-like comparison rather than a month-to-date figure
# racing a full month.
SPEND_WINDOW_DAYS = 30

# How far back a ``fact_signal_health_daily`` snapshot may be and still count as
# the "previous" reading for the EMQ trend. Beyond this the two rows are too far
# apart for their difference to describe a trend, so no trend is reported.
EMQ_TREND_LOOKBACK_DAYS = 30

# fact_actions_queue.entity_type for an action aimed at a whole campaign, the
# only entity whose budget this module can price from Campaign.daily_budget_cents.
ACTION_ENTITY_CAMPAIGN = "campaign"

# The statuses that mean autopilot has proposed an action and it has not been
# applied. Both belong here, and reading only ``queued`` was wrong in the one
# direction that matters: the executor selects ``approved`` rows
# (``app.tasks.apply_actions_queue``), and when the trust gate holds or blocks
# one it records the reason on ``error`` and leaves the status at ``approved``.
# So the actions the gate is actively withholding are exactly the ones a
# ``queued``-only filter cannot see, and a tenant whose whole approved queue was
# blocked reported a measured "nothing at stake".
#
# ``applying`` is excluded deliberately: that row's write may already have
# reached Meta, so it is being reconciled rather than withheld. ``applied``,
# ``failed`` and ``dismissed`` are terminal.
UNAPPLIED_ACTION_STATUSES: tuple[str, ...] = (
    ActionStatus.QUEUED.value,
    ActionStatus.APPROVED.value,
)


@dataclass(frozen=True)
class TenantPortfolioMetrics:
    """
    One tenant's row in the account-manager portfolio.

    Read the ``None``s literally: they mark metrics this deployment has no
    source for on this tenant, not metrics that measured zero.
    """

    tenant_id: int
    name: str
    slug: str

    # Identity and commercials, from the tenant row and its onboarding record.
    industry: str | None
    plan: str | None
    subscription_status: str
    mrr: float
    renewal_date: datetime | None

    # Trust. ``signal_health_score`` is the composite the trust gate grades;
    # ``emq_score`` is its EMQ component, measured from capi_delivery_logs.
    signal_health_score: float | None
    signal_health_status: str
    emq_score: float | None
    emq_trend: float | None
    channel: str | None
    # Channels with no score of their own. The representative channel does not
    # speak for these, so they are named rather than left implicit - otherwise a
    # tenant with one healthy channel and two dark ones reads as unqualified
    # "healthy", which is a cleaner picture than its own dashboard shows.
    unscored_channels: list[str] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)
    missing_input_codes: list[str] = field(default_factory=list)

    # What the trust gate decides for this tenant right now, from the gate
    # itself rather than re-derived from the live score - the two answer
    # different questions and can legitimately disagree.
    # ``gate_health_date`` is null exactly when the gate had no snapshot to
    # grade, which the UI renders as "no data" rather than as a BLOCK: nothing
    # is wrong, nothing is known yet.
    gate_decision: str | None = None
    gate_reason: str | None = None
    gate_health_date: date | None = None

    # Operations.
    budget_at_risk: float | None = None
    unapplied_actions: int = 0
    active_incidents: int | None = None
    incident_open_hours: float | None = None

    # Performance over the trailing SPEND_WINDOW_DAYS.
    monthly_spend: float | None = None
    roas: float | None = None
    roas_trend: float | None = None

    # Newest sign-in by any user of the tenant. Not a record of the account
    # manager contacting them - nothing in this product keeps one - so it is
    # labelled as the sign-in it actually is.
    last_login_at: datetime | None = None


def _aware(value: datetime | None) -> datetime | None:
    """
    Read a timestamp as UTC when the column handed back a naive one.

    Several of these tables default their timestamps with ``datetime.utcnow``,
    which stores a naive value in a ``timezone=True`` column. Subtracting one
    of those from an aware "now" raises, so they are stamped as UTC here rather
    than at every call site.

    Args:
        value: The timestamp read from the database, or None.

    Returns:
        The same instant with a UTC tzinfo, or None.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def _read_industries(
    db: AsyncSession, tenant_ids: Sequence[int]
) -> dict[int, str]:
    """
    Read each tenant's self-declared industry from its onboarding record.

    Args:
        db: Async database session.
        tenant_ids: Tenants to read.

    Returns:
        ``{tenant_id: industry}`` for the tenants that stated one. A tenant
        that skipped the business-profile step is absent, and the portfolio
        shows the industry as unknown rather than guessing "E-commerce", which
        is what it used to display for every customer.
    """
    if not tenant_ids:
        return {}

    result = await db.execute(
        select(
            TenantOnboarding.tenant_id,
            TenantOnboarding.industry,
            TenantOnboarding.industry_other,
        ).where(TenantOnboarding.tenant_id.in_(list(tenant_ids)))
    )
    industries: dict[int, str] = {}
    for tenant_id, industry, industry_other in result.all():
        # "other" is a placeholder for the free-text answer beside it.
        label = industry_other if industry in (None, "other") else industry
        label = (label or "").strip()
        if label:
            industries[int(tenant_id)] = label
    return industries


async def _read_spend(
    db: AsyncSession,
    tenant_ids: Sequence[int],
    today: date,
) -> dict[int, dict[str, Any]]:
    """
    Sum each tenant's ad spend and revenue over two adjacent windows.

    Reads ``campaign_metrics``, the daily fact table the Meta insights
    ingestion writes, in a single grouped query. Rows are counted as well as
    summed: a tenant with no rows in a window has *no* spend figure, which is
    different from a tenant whose campaigns ran and spent nothing.

    Soft-deleted campaigns are not excluded. Their spend still happened and
    still belongs in what the tenant spent this month; removing a campaign from
    Stratum does not un-spend its budget.

    Args:
        db: Async database session.
        tenant_ids: Tenants to sum for.
        today: The last day of the current window, inclusive.

    Returns:
        ``{tenant_id: {"current_spend": .., "current_revenue": ..,
        "current_days": .., "previous_spend": .., "previous_revenue": ..,
        "previous_days": ..}}`` in major currency units, for tenants with rows
        in either window.
    """
    if not tenant_ids:
        return {}

    current_start = today - timedelta(days=SPEND_WINDOW_DAYS)
    previous_start = today - timedelta(days=SPEND_WINDOW_DAYS * 2)
    in_current = CampaignMetric.date > current_start

    result = await db.execute(
        select(
            CampaignMetric.tenant_id.label("tenant_id"),
            func.coalesce(
                func.sum(case((in_current, CampaignMetric.spend_cents), else_=0)), 0
            ).label("current_spend"),
            func.coalesce(
                func.sum(case((in_current, CampaignMetric.revenue_cents), else_=0)), 0
            ).label("current_revenue"),
            func.coalesce(func.sum(case((in_current, 1), else_=0)), 0).label(
                "current_days"
            ),
            func.coalesce(
                func.sum(case((in_current, 0), else_=CampaignMetric.spend_cents)), 0
            ).label("previous_spend"),
            func.coalesce(
                func.sum(case((in_current, 0), else_=CampaignMetric.revenue_cents)), 0
            ).label("previous_revenue"),
            func.coalesce(func.sum(case((in_current, 0), else_=1)), 0).label(
                "previous_days"
            ),
        )
        .where(
            and_(
                CampaignMetric.tenant_id.in_(list(tenant_ids)),
                CampaignMetric.date > previous_start,
                CampaignMetric.date <= today,
            )
        )
        .group_by(CampaignMetric.tenant_id)
    )

    return {
        int(row.tenant_id): {
            "current_spend": int(row.current_spend or 0) / 100,
            "current_revenue": int(row.current_revenue or 0) / 100,
            "current_days": int(row.current_days or 0),
            "previous_spend": int(row.previous_spend or 0) / 100,
            "previous_revenue": int(row.previous_revenue or 0) / 100,
            "previous_days": int(row.previous_days or 0),
        }
        for row in result.all()
    }


async def _read_incidents(
    db: AsyncSession, tenant_ids: Sequence[int]
) -> dict[int, tuple[int, datetime | None]]:
    """
    Count each tenant's unresolved pacing alerts and find the oldest.

    ``pacing_alerts`` is the one tenant-scoped alert table with both a writer
    (``app.services.pacing.alert_service``) and a resolution lifecycle, so
    "active incidents" means exactly the alerts that have been raised and not
    yet acknowledged, resolved or dismissed.

    Args:
        db: Async database session.
        tenant_ids: Tenants to count for.

    Returns:
        ``{tenant_id: (count, oldest_created_at)}`` for tenants with at least
        one active alert. Absence means zero active alerts, which is a real
        measurement and is reported as ``0``.
    """
    if not tenant_ids:
        return {}

    result = await db.execute(
        select(
            PacingAlert.tenant_id.label("tenant_id"),
            func.count(PacingAlert.id).label("open_alerts"),
            func.min(PacingAlert.created_at).label("oldest"),
        )
        .where(
            and_(
                PacingAlert.tenant_id.in_(list(tenant_ids)),
                PacingAlert.status == AlertStatus.ACTIVE,
            )
        )
        .group_by(PacingAlert.tenant_id)
    )
    return {
        int(row.tenant_id): (int(row.open_alerts or 0), _aware(row.oldest))
        for row in result.all()
    }


async def _read_budget_at_risk(
    db: AsyncSession, tenant_ids: Sequence[int]
) -> dict[int, tuple[int, float | None]]:
    """
    Price the automation each tenant has proposed and not applied.

    "Budget at risk" is the daily budget of the campaigns that autopilot has
    proposed an action for and has not applied - whether it is still waiting on
    a human (``queued``) or has been released and is being withheld by the trust
    gate (``approved`` with the refusal recorded on ``error``). Two queries: how
    many such actions exist, and the summed daily budget of the distinct
    campaigns those actions name.

    An action is matched to a campaign on
    ``fact_actions_queue.entity_id == campaigns.external_id`` within the same
    tenant - ``entity_id`` is the platform's own id, which is what
    ``external_id`` holds. Campaigns are counted once however many actions
    target them, and a campaign on a lifetime rather than a daily budget
    contributes nothing, because a lifetime total and a daily rate are not the
    same quantity and adding them would produce a number with no unit.

    Args:
        db: Async database session.
        tenant_ids: Tenants to price for.

    Returns:
        ``{tenant_id: (unapplied_action_count, priced_daily_budget_or_None)}``.
        The budget is ``None`` when actions are outstanding but none of them
        could be priced - something is being held and we cannot say how much -
        and tenants with no outstanding actions are absent, which the caller
        reports as a measured zero.
    """
    if not tenant_ids:
        return {}

    ids = list(tenant_ids)
    unapplied = FactActionsQueue.status.in_(UNAPPLIED_ACTION_STATUSES)

    counts_result = await db.execute(
        select(
            FactActionsQueue.tenant_id.label("tenant_id"),
            func.count(FactActionsQueue.id).label("unapplied_actions"),
        )
        .where(and_(FactActionsQueue.tenant_id.in_(ids), unapplied))
        .group_by(FactActionsQueue.tenant_id)
    )
    counts = {
        int(row.tenant_id): int(row.unapplied_actions or 0)
        for row in counts_result.all()
    }
    if not counts:
        return {}

    # Distinct so that three outstanding actions on one campaign do not charge its
    # budget three times.
    targeted = (
        select(
            FactActionsQueue.tenant_id.label("tenant_id"),
            Campaign.id.label("campaign_id"),
            Campaign.daily_budget_cents.label("daily_budget_cents"),
        )
        .join(
            Campaign,
            and_(
                Campaign.tenant_id == FactActionsQueue.tenant_id,
                Campaign.external_id == FactActionsQueue.entity_id,
                Campaign.is_deleted.is_(False),
            ),
        )
        .where(
            and_(
                FactActionsQueue.tenant_id.in_(list(counts)),
                unapplied,
                FactActionsQueue.entity_type == ACTION_ENTITY_CAMPAIGN,
                Campaign.daily_budget_cents.isnot(None),
            )
        )
        .distinct()
        .subquery()
    )
    budget_result = await db.execute(
        select(
            targeted.c.tenant_id,
            func.coalesce(func.sum(targeted.c.daily_budget_cents), 0).label(
                "budget_cents"
            ),
            func.count(targeted.c.campaign_id).label("campaigns"),
        ).group_by(targeted.c.tenant_id)
    )
    priced = {
        int(row.tenant_id): int(row.budget_cents or 0) / 100
        for row in budget_result.all()
        if int(row.campaigns or 0) > 0
    }

    return {
        tenant_id: (unapplied_actions, priced.get(tenant_id))
        for tenant_id, unapplied_actions in counts.items()
    }


async def _read_last_logins(
    db: AsyncSession, tenant_ids: Sequence[int]
) -> dict[int, datetime]:
    """
    Find the newest sign-in by any user of each tenant.

    Args:
        db: Async database session.
        tenant_ids: Tenants to read.

    Returns:
        ``{tenant_id: last_login_at}`` for tenants where at least one user has
        signed in. A tenant is absent when none ever has, and the portfolio
        renders that as unknown rather than as a very old date.
    """
    if not tenant_ids:
        return {}

    result = await db.execute(
        select(
            User.tenant_id.label("tenant_id"),
            func.max(User.last_login_at).label("last_login_at"),
        )
        .where(
            and_(
                User.tenant_id.in_(list(tenant_ids)),
                User.is_deleted.is_(False),
                User.last_login_at.isnot(None),
            )
        )
        .group_by(User.tenant_id)
    )
    return {
        int(row.tenant_id): _aware(row.last_login_at)
        for row in result.all()
        if row.last_login_at is not None
    }


async def _read_emq_trends(
    db: AsyncSession,
    tenant_ids: Sequence[int],
    today: date,
) -> dict[int, dict[str, float]]:
    """
    Difference the two most recent recorded EMQ scores per tenant and channel.

    The composite is not stored anywhere, but ``fact_signal_health_daily``
    stores the EMQ component the daily rollup measured, per tenant, date and
    channel. The trend is therefore a like-for-like difference of two readings
    of the same quantity from the same writer - not a live window compared
    against a snapshot, which would move whenever the window slid.

    Both readings must fall inside ``EMQ_TREND_LOOKBACK_DAYS``. Two rows a
    quarter apart do not describe a trend, so no trend is reported for them.

    Args:
        db: Async database session.
        tenant_ids: Tenants to read.
        today: Rows dated after this are ignored, matching the trust gate's
            rule that a row dated in the future is invalid rather than newest.

    Returns:
        ``{tenant_id: {channel: delta}}`` for the tenant/channel pairs with two
        qualifying readings.
    """
    if not tenant_ids:
        return {}

    result = await db.execute(
        select(
            FactSignalHealthDaily.tenant_id,
            FactSignalHealthDaily.platform,
            FactSignalHealthDaily.date,
            FactSignalHealthDaily.emq_score,
        )
        .where(
            and_(
                FactSignalHealthDaily.tenant_id.in_(list(tenant_ids)),
                FactSignalHealthDaily.date <= today,
                FactSignalHealthDaily.date
                > today - timedelta(days=EMQ_TREND_LOOKBACK_DAYS),
                FactSignalHealthDaily.emq_score.isnot(None),
            )
        )
        .order_by(
            FactSignalHealthDaily.tenant_id,
            FactSignalHealthDaily.platform,
            FactSignalHealthDaily.date.desc(),
        )
    )

    readings: dict[int, dict[str, list[float]]] = {}
    for tenant_id, platform, _row_date, emq_score in result.all():
        by_channel = readings.setdefault(int(tenant_id), {})
        scores = by_channel.setdefault(str(platform), [])
        if len(scores) < 2:
            scores.append(float(emq_score))

    return {
        tenant_id: {
            channel: round(scores[0] - scores[1], 2)
            for channel, scores in by_channel.items()
            if len(scores) == 2
        }
        for tenant_id, by_channel in readings.items()
    }


def _roas(spend: float, revenue: float) -> float | None:
    """
    Return on ad spend, or None when there is no spend to divide by.

    Args:
        spend: Money spent in the window.
        revenue: Attributed revenue in the window.

    Returns:
        The ratio, or None. Zero spend has no ROAS - not a ROAS of zero.
    """
    if spend <= 0:
        return None
    return round(revenue / spend, 2)


async def build_tenant_portfolio(
    db: AsyncSession,
    tenants: Sequence[Any],
    now: datetime | None = None,
) -> list[TenantPortfolioMetrics]:
    """
    Build the portfolio rows for an already-authorised list of tenants.

    Authorisation is the caller's job: every tenant passed in is read, and the
    batched queries below are filtered to exactly this set. Pass only the
    tenants the requester may see.

    Args:
        db: Async database session.
        tenants: Tenant rows to build metrics for, in the order to return them.
        now: Optional clock override for the windows; defaults to the current
            UTC time.

    Returns:
        One row per tenant, in the order given. Metrics with no source for a
        given tenant are ``None``.
    """
    if not tenants:
        return []

    # Imported here: app.tasks.apply_actions_queue pulls in the Celery app, and
    # the API must stay importable without it.
    from app.tasks.apply_actions_queue import check_signal_health_for_tenants

    now = now or datetime.now(UTC)
    today = now.date()
    tenant_ids = [int(tenant.id) for tenant in tenants]

    # The clock override reaches the measurement window and the gate as well,
    # or the trust numbers would be measured over a different period than
    # everything else on the row - and the gate's staleness rule, which is the
    # part most worth testing, could never be exercised against a fixed clock.
    health = await compute_portfolio_signal_health(
        db, tenant_ids, window=default_window(now)
    )
    gates = await check_signal_health_for_tenants(db, tenant_ids, today=today)
    industries = await _read_industries(db, tenant_ids)
    spend = await _read_spend(db, tenant_ids, today)
    incidents = await _read_incidents(db, tenant_ids)
    budgets = await _read_budget_at_risk(db, tenant_ids)
    last_logins = await _read_last_logins(db, tenant_ids)
    emq_trends = await _read_emq_trends(db, tenant_ids, today)

    rows: list[TenantPortfolioMetrics] = []
    for tenant in tenants:
        tenant_id = int(tenant.id)
        computations = health.get(tenant_id, {})
        representative = summarise_channels(computations)
        gate = gates.get(tenant_id)

        components = representative.component_scores() if representative else {}
        # Two components can share one cause (both delivery components go
        # missing together when there is no CAPI traffic); say it once.
        missing_inputs = (
            list(dict.fromkeys(representative.missing_inputs)) if representative else []
        )
        # A scorable channel does not speak for the dark ones beside it. Without
        # this the worst *scorable* channel is published alone, so a tenant with
        # facebook at 95 and no instagram or whatsapp traffic at all read as an
        # unqualified "healthy" - a cleaner picture than the tenant's own
        # dashboard shows, and cleaner in the flattering direction. The
        # dashboard names them (app/api/v1/endpoints/dashboard.py), so this does
        # too.
        unscored = sorted(
            channel for channel, item in computations.items() if item.insufficient_data
        )
        # Published as ``unscored_channels`` rather than appended to
        # ``missing_inputs``: that field names the *components* of one channel's
        # score that could not be measured, and a dark channel is a different
        # fact. Folding one into the other stated the same gap twice on the card.

        window = spend.get(tenant_id)
        current_spend = (
            window["current_spend"] if window and window["current_days"] else None
        )
        current_roas = (
            _roas(window["current_spend"], window["current_revenue"])
            if window and window["current_days"]
            else None
        )
        previous_roas = (
            _roas(window["previous_spend"], window["previous_revenue"])
            if window and window["previous_days"]
            else None
        )

        open_alerts, oldest_alert = incidents.get(tenant_id, (0, None))
        unapplied_actions, priced_budget = budgets.get(tenant_id, (0, 0.0))

        rows.append(
            TenantPortfolioMetrics(
                tenant_id=tenant_id,
                name=tenant.name,
                slug=tenant.slug,
                industry=industries.get(tenant_id),
                plan=plan_display_name(tenant.plan),
                subscription_status=tenant_subscription_status(tenant),
                mrr=tenant_mrr(tenant),
                renewal_date=_aware(tenant_renewal_date(tenant)),
                signal_health_score=(
                    round(representative.score, 1)
                    if representative and representative.score is not None
                    else None
                ),
                signal_health_status=(
                    representative.status
                    if representative
                    else STATUS_INSUFFICIENT_DATA
                ),
                emq_score=components.get(COMPONENT_EMQ),
                emq_trend=(
                    emq_trends.get(tenant_id, {}).get(representative.channel)
                    if representative
                    else None
                ),
                channel=representative.channel if representative else None,
                unscored_channels=unscored,
                missing_inputs=missing_inputs,
                missing_input_codes=(
                    representative.missing_input_codes if representative else []
                ),
                gate_decision=gate.decision.value if gate else None,
                gate_reason=gate.reason if gate else None,
                gate_health_date=gate.health_date if gate else None,
                budget_at_risk=priced_budget,
                unapplied_actions=unapplied_actions,
                active_incidents=open_alerts,
                incident_open_hours=(
                    round((now - oldest_alert).total_seconds() / 3600, 1)
                    if oldest_alert
                    else None
                ),
                monthly_spend=current_spend,
                roas=current_roas,
                roas_trend=(
                    round(current_roas - previous_roas, 2)
                    if current_roas is not None and previous_roas is not None
                    else None
                ),
                last_login_at=last_logins.get(tenant_id),
            )
        )
    return rows
