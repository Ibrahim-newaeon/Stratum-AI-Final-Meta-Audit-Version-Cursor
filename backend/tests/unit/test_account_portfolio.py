# =============================================================================
# Stratum AI - Account Manager Portfolio Tests
# =============================================================================
"""
Regression tests for the fabricated account-manager portfolio.

``frontend/src/views/am/Portfolio.tsx`` generated every per-tenant metric with
``Math.random()`` and attached it to the *real*, named tenant list: an invented
EMQ score and status, an invented autopilot mode, an invented budget at risk, an
invented number of open incidents, an invented spend, ROAS, renewal date, plan
and last-contact date - re-rolled on every mount - and then sorted, filtered and
raised "priority alerts" on them.

The fabrication was removed by nulling the fields. What is pinned here is the
replacement: every field is measured from the table that owns it, batched across
the whole tenant list, and a field with no source stays ``None`` rather than
becoming a ``0``. A ``0`` is a claim - no spend, no held budget, no open
incident - and "nobody measured this" is not that claim.

Also pinned: the batched signal health path is the *same* computation as the
single-tenant one (a tenant cannot be ``insufficient_data`` on its own dashboard
and scored in its account manager's list), and no query crosses tenants.
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models import UserRole
from app.services.account_portfolio import (
    SPEND_WINDOW_DAYS,
    build_tenant_portfolio,
)
from app.services.signal_health import (
    STATUS_INSUFFICIENT_DATA,
    compute_portfolio_signal_health,
    compute_tenant_signal_health,
)
from app.services.signal_health.model import SignalHealthWindow

TENANT = 1
OTHER_TENANT = 2
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
TODAY = NOW.date()
WINDOW = SignalHealthWindow(start=NOW - timedelta(hours=24), end=NOW)


# =============================================================================
# Test doubles
# =============================================================================


def delivery(
    tenant_id: int,
    platform: str = "meta",
    *,
    status: str = "success",
    identified: bool = True,
    at: datetime | None = None,
):
    """One persisted ``capi_delivery_logs`` row."""
    return SimpleNamespace(
        tenant_id=tenant_id,
        platform=platform,
        status=status,
        user_data_hash="abc" if identified else None,
        delivery_time=at or (NOW - timedelta(hours=1)),
    )


def metric(tenant_id: int, *, days_ago: int, spend_cents: int, revenue_cents: int):
    """One persisted ``campaign_metrics`` row."""
    return SimpleNamespace(
        tenant_id=tenant_id,
        date=TODAY - timedelta(days=days_ago),
        spend_cents=spend_cents,
        revenue_cents=revenue_cents,
    )


def tenant_row(tenant_id: int, **overrides):
    """A ``tenants`` row as the endpoint hands it to the builder."""
    defaults = {
        "id": tenant_id,
        "name": f"Tenant {tenant_id}",
        "slug": f"tenant-{tenant_id}",
        "plan": "professional",
        "subscription_status": "active",
        "current_period_end": None,
        "plan_expires_at": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class _Result:
    """Stand-in for a SQLAlchemy ``Result`` over a list of rows."""

    def __init__(self, rows=()) -> None:
        """Store the rows this result hands back."""
        self._rows = list(rows)

    def all(self):
        """Return every row."""
        return list(self._rows)

    def one(self):
        """Return the single aggregate row."""
        return self._rows[0]

    def scalars(self):
        """Return the row accessor (the rows are already entities)."""
        return self

    def first(self):
        """Return the first row, or None."""
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        """Return the single scalar, or None."""
        return self._rows[0] if self._rows else None


class PortfolioSession:
    """
    An AsyncSession stand-in backed by a multi-tenant in-memory store.

    Every aggregate is computed here from the store **using only the predicates
    the statement actually carries** - the compiled bind parameters are read
    back and applied. A batched query that forgot its ``tenant_id`` filter would
    therefore see every tenant's rows, which is what the isolation test asserts
    must not happen: the filter is proven, not assumed.

    Anything the store was not seeded with answers empty, which is the honest
    state of a fresh deployment and the case the null contract is about.
    """

    def __init__(
        self,
        *,
        deliveries=(),
        last_synced_at=None,
        connections=None,
        onboarding=None,
        metrics=(),
        alerts=(),
        queued_actions=(),
        campaigns=(),
        logins=None,
        health_rows=(),
    ) -> None:
        """Seed the store with rows belonging to several tenants."""
        self.deliveries = list(deliveries)
        self.last_synced_at = last_synced_at or {}
        self.connections = connections or {}
        self.onboarding = onboarding or {}
        self.metrics = list(metrics)
        self.alerts = list(alerts)
        self.queued_actions = list(queued_actions)
        self.campaigns = list(campaigns)
        self.logins = logins or {}
        self.health_rows = list(health_rows)
        self.statements: list[str] = []

    @staticmethod
    def _tenants(params) -> list[int] | None:
        """The tenant ids the statement filtered on, or None when it did not."""
        for key, value in params.items():
            if key.startswith("tenant_id_") and isinstance(value, (list, tuple)):
                return list(value)
        return None

    def _scoped(self, rows, params):
        """Apply the statement's own tenant filter, and nothing else."""
        wanted = self._tenants(params)
        if wanted is None:
            return list(rows)
        return [row for row in rows if row.tenant_id in wanted]

    async def execute(self, statement):
        """Answer one of the queries the portfolio builder issues."""
        sql = str(statement)
        params = dict(statement.compile().params)
        self.statements.append(sql)

        if "capi_delivery_logs" in sql:
            rows = self._scoped(self.deliveries, params)
            platforms = params.get("platform_1") or []
            rows = [row for row in rows if row.platform in platforms]
            start = params.get("delivery_time_1")
            end = params.get("delivery_time_2")
            rows = [
                row
                for row in rows
                if (start is None or row.delivery_time >= start)
                and (end is None or row.delivery_time < end)
            ]

            def counts(group):
                return {
                    "total": len(group),
                    "successful": sum(1 for r in group if r.status == "success"),
                    "identified": sum(1 for r in group if r.user_data_hash is not None),
                }

            if "GROUP BY" not in sql:
                # The single-tenant form aggregates the whole filtered set.
                return _Result([SimpleNamespace(**counts(rows))])
            grouped: dict[tuple[int, str], list] = {}
            for row in rows:
                grouped.setdefault((row.tenant_id, row.platform), []).append(row)
            return _Result(
                SimpleNamespace(tenant_id=key[0], platform=key[1], **counts(group))
                for key, group in grouped.items()
            )

        if "max(campaigns.last_synced_at)" in sql:
            bound = params.get("last_synced_at_1")
            wanted = self._tenants(params)
            if wanted is None:
                # The single-tenant form filters on one id and reads a scalar.
                single = params.get("tenant_id_1")
                newest = self.last_synced_at.get(single)
                if newest is not None and bound is not None and newest > bound:
                    newest = None
                return _Result([newest] if newest is not None else [])
            rows = []
            for tenant_id in wanted:
                newest = self.last_synced_at.get(tenant_id)
                if newest is None or (bound is not None and newest > bound):
                    continue
                rows.append(SimpleNamespace(tenant_id=tenant_id, newest=newest))
            return _Result(rows)

        if "tenant_platform_connection" in sql:
            wanted = self._tenants(params)
            if wanted is None:
                wanted = [params.get("tenant_id_1")]
            return _Result(self.connections[t] for t in wanted if t in self.connections)

        if "tenant_onboarding" in sql:
            wanted = self._tenants(params)
            if wanted is None:
                wanted = [params.get("tenant_id_1")]
            records = [(t, self.onboarding[t]) for t in wanted if t in self.onboarding]
            if "industry" in sql:
                return _Result((t, r.industry, r.industry_other) for t, r in records)
            return _Result(
                (t, r.trust_threshold_autopilot, r.trust_threshold_alert)
                for t, r in records
            )

        if "campaign_metrics" in sql:
            rows = self._scoped(self.metrics, params)
            # The statement binds three distinct dates - the previous window's
            # start, the boundary between the windows and today - but their
            # parameter names depend on clause order, so they are recovered by
            # value rather than by guessing a name.
            bounds = sorted({v for v in params.values() if isinstance(v, date)})
            low, boundary, high = bounds
            rows = [
                row
                for row in rows
                if (low is None or row.date > low)
                and (high is None or row.date <= high)
            ]
            grouped: dict[int, list] = {}
            for row in rows:
                grouped.setdefault(row.tenant_id, []).append(row)
            out = []
            for tenant_id, group in grouped.items():
                current = [r for r in group if boundary is None or r.date > boundary]
                previous = [
                    r for r in group if boundary is not None and r.date <= boundary
                ]
                out.append(
                    SimpleNamespace(
                        tenant_id=tenant_id,
                        current_spend=sum(r.spend_cents for r in current),
                        current_revenue=sum(r.revenue_cents for r in current),
                        current_days=len(current),
                        previous_spend=sum(r.spend_cents for r in previous),
                        previous_revenue=sum(r.revenue_cents for r in previous),
                        previous_days=len(previous),
                    )
                )
            return _Result(out)

        if "pacing_alerts" in sql:
            rows = self._scoped(self.alerts, params)
            grouped: dict[int, list] = {}
            for row in rows:
                grouped.setdefault(row.tenant_id, []).append(row)
            return _Result(
                SimpleNamespace(
                    tenant_id=tenant_id,
                    open_alerts=len(group),
                    oldest=min(r.created_at for r in group),
                )
                for tenant_id, group in grouped.items()
            )

        if "fact_actions_queue" in sql and "campaigns" in sql:
            rows = self._scoped(self.queued_actions, params)
            by_tenant: dict[int, set] = {}
            for action in rows:
                if action.entity_type != "campaign":
                    continue
                for campaign in self.campaigns:
                    if (
                        campaign.tenant_id == action.tenant_id
                        and campaign.external_id == action.entity_id
                        and campaign.daily_budget_cents is not None
                    ):
                        by_tenant.setdefault(action.tenant_id, set()).add(
                            (campaign.id, campaign.daily_budget_cents)
                        )
            return _Result(
                SimpleNamespace(
                    tenant_id=tenant_id,
                    budget_cents=sum(budget for _, budget in matched),
                    campaigns=len(matched),
                )
                for tenant_id, matched in by_tenant.items()
            )

        if "fact_actions_queue" in sql:
            rows = self._scoped(self.queued_actions, params)
            grouped: dict[int, int] = {}
            for row in rows:
                grouped[row.tenant_id] = grouped.get(row.tenant_id, 0) + 1
            return _Result(
                SimpleNamespace(tenant_id=tenant_id, queued_actions=count)
                for tenant_id, count in grouped.items()
            )

        if "max(users.last_login_at)" in sql:
            wanted = self._tenants(params) or list(self.logins)
            return _Result(
                SimpleNamespace(tenant_id=t, last_login_at=self.logins[t])
                for t in wanted
                if t in self.logins
            )

        if "max(fact_signal_health_daily.date)" in sql:
            rows = self._scoped(self.health_rows, params)
            grouped: dict[int, date] = {}
            for row in rows:
                grouped[row.tenant_id] = max(
                    grouped.get(row.tenant_id, row.date), row.date
                )
            return _Result((t, d) for t, d in grouped.items())

        if (
            "fact_signal_health_daily.emq_score" in sql
            and "fact_signal_health_daily.id" not in sql
        ):
            rows = self._scoped(self.health_rows, params)
            rows = [row for row in rows if row.emq_score is not None]
            rows.sort(key=lambda r: (r.tenant_id, r.platform, r.date), reverse=True)
            return _Result(
                (row.tenant_id, row.platform, row.date, row.emq_score) for row in rows
            )

        if "fact_signal_health_daily" in sql:
            # The gate reads whole rows for the newest date it found.
            return _Result(self.health_rows)

        if "tenant_enforcement_settings" in sql:
            return _Result([])

        raise AssertionError(f"unexpected query: {sql}")


def connection(status: str = "connected", errors: int = 0):
    """A ``tenant_platform_connection`` row."""
    return SimpleNamespace(
        tenant_id=TENANT, status=status, error_count=errors, last_error=None
    )


# =============================================================================
# The batched signal health path is the same computation as the per-tenant one
# =============================================================================


@pytest.mark.asyncio
async def test_batched_signal_health_matches_the_single_tenant_path():
    """
    A tenant scores identically whether measured alone or in a portfolio.

    The account manager's list and the tenant's own dashboard must not be able
    to disagree, so the batched path hands its measurements to the same
    ``_build_computation`` rather than re-deriving a score of its own.
    """
    deliveries = [delivery(TENANT) for _ in range(40)]
    deliveries += [
        delivery(TENANT, status="failed", identified=False) for _ in range(10)
    ]
    store = {
        "deliveries": deliveries,
        "last_synced_at": {TENANT: NOW - timedelta(minutes=5)},
        "connections": {TENANT: connection()},
    }

    single = await compute_tenant_signal_health(
        PortfolioSession(**store), TENANT, window=WINDOW
    )
    batched = await compute_portfolio_signal_health(
        PortfolioSession(**store), [TENANT], window=WINDOW
    )

    assert set(batched) == {TENANT}
    for channel, computation in single.items():
        other = batched[TENANT][channel]
        assert other.score == computation.score
        assert other.status == computation.status
        assert other.component_scores() == computation.component_scores()
        assert other.missing_input_codes == computation.missing_input_codes


@pytest.mark.asyncio
async def test_a_tenant_with_no_delivery_events_is_unscored_not_zero():
    """
    Nothing to measure yields ``insufficient_data`` with the gaps named.

    This is the state the portfolio was inventing a 60-99 EMQ score for.
    """
    session = PortfolioSession(
        last_synced_at={TENANT: NOW - timedelta(minutes=5)},
        connections={TENANT: connection()},
    )

    batched = await compute_portfolio_signal_health(session, [TENANT], window=WINDOW)

    for computation in batched[TENANT].values():
        assert computation.score is None
        assert computation.status == STATUS_INSUFFICIENT_DATA
        assert "no_delivery_events" in computation.missing_input_codes


@pytest.mark.asyncio
async def test_one_tenants_delivery_logs_never_reach_another_tenants_score():
    """
    The batched delivery query is grouped *and* filtered by tenant.

    Grouping alone is not isolation: the WHERE clause has to name the tenants
    the caller was authorised for, or a portfolio would aggregate every
    customer's conversion traffic into whichever rows it happened to render.
    """
    session = PortfolioSession(
        deliveries=[delivery(OTHER_TENANT) for _ in range(50)],
        last_synced_at={TENANT: NOW - timedelta(minutes=5)},
        connections={TENANT: connection()},
    )

    batched = await compute_portfolio_signal_health(session, [TENANT], window=WINDOW)

    assert set(batched) == {TENANT}
    for computation in batched[TENANT].values():
        assert computation.score is None
        assert computation.delivery is not None
        assert computation.delivery.total_events == 0


# =============================================================================
# Every unmeasured field stays null
# =============================================================================


@pytest.mark.asyncio
async def test_an_empty_deployment_reports_nulls_rather_than_zeroes():
    """
    A tenant nothing has been measured for gets nulls, not a row of zeroes.

    Spend, ROAS and its trend, the renewal date and the last login are all
    ``None``; the score is ``None`` with status ``insufficient_data``. The one
    genuine zero is the incident count, because "no unresolved pacing alert" is
    something the query actually established.
    """
    session = PortfolioSession()

    (row,) = await build_tenant_portfolio(session, [tenant_row(TENANT)], now=NOW)

    assert row.signal_health_score is None
    assert row.signal_health_status == STATUS_INSUFFICIENT_DATA
    assert row.emq_score is None
    assert row.emq_trend is None
    assert row.monthly_spend is None
    assert row.roas is None
    assert row.roas_trend is None
    assert row.renewal_date is None
    assert row.last_login_at is None
    assert row.industry is None
    # Measured zeroes, not absences.
    assert row.active_incidents == 0
    assert row.incident_open_hours is None
    assert row.queued_actions == 0
    assert row.budget_at_risk == 0.0


@pytest.mark.asyncio
async def test_spend_and_roas_come_from_the_daily_metric_rows():
    """
    Spend is summed from ``campaign_metrics`` and ROAS derived from it.

    The trend compares the trailing window against the window of equal width
    before it, so it is a like-for-like comparison rather than a partial month
    racing a full one.
    """
    session = PortfolioSession(
        metrics=[
            metric(TENANT, days_ago=5, spend_cents=100_000, revenue_cents=400_000),
            metric(TENANT, days_ago=10, spend_cents=100_000, revenue_cents=400_000),
            metric(
                TENANT,
                days_ago=SPEND_WINDOW_DAYS + 5,
                spend_cents=100_000,
                revenue_cents=200_000,
            ),
        ]
    )

    (row,) = await build_tenant_portfolio(session, [tenant_row(TENANT)], now=NOW)

    assert row.monthly_spend == 2000.0
    assert row.roas == 4.0
    # 4.0 this window against 2.0 in the one before it.
    assert row.roas_trend == 2.0


@pytest.mark.asyncio
async def test_roas_is_null_when_there_was_no_spend_to_divide_by():
    """
    Zero spend has no ROAS - it is not a ROAS of zero.

    A tenant whose campaigns reported rows but spent nothing would otherwise be
    ranked alongside a genuinely failing account.
    """
    session = PortfolioSession(
        metrics=[metric(TENANT, days_ago=3, spend_cents=0, revenue_cents=0)]
    )

    (row,) = await build_tenant_portfolio(session, [tenant_row(TENANT)], now=NOW)

    assert row.monthly_spend == 0.0
    assert row.roas is None
    assert row.roas_trend is None


@pytest.mark.asyncio
async def test_roas_trend_needs_both_windows():
    """A single window is a reading, not a trend."""
    session = PortfolioSession(
        metrics=[metric(TENANT, days_ago=3, spend_cents=50_000, revenue_cents=150_000)]
    )

    (row,) = await build_tenant_portfolio(session, [tenant_row(TENANT)], now=NOW)

    assert row.roas == 3.0
    assert row.roas_trend is None


# =============================================================================
# Operations: incidents and held budget
# =============================================================================


@pytest.mark.asyncio
async def test_open_incidents_come_from_unresolved_pacing_alerts():
    """
    Active incidents are counted, and the oldest one dates the exposure.

    ``pacing_alerts`` is the one tenant-scoped alert table with both a writer
    and a resolution lifecycle, so "active" means exactly the alerts nobody has
    acknowledged, resolved or dismissed.
    """
    session = PortfolioSession(
        alerts=[
            SimpleNamespace(tenant_id=TENANT, created_at=NOW - timedelta(hours=6)),
            SimpleNamespace(tenant_id=TENANT, created_at=NOW - timedelta(hours=30)),
        ]
    )

    (row,) = await build_tenant_portfolio(session, [tenant_row(TENANT)], now=NOW)

    assert row.active_incidents == 2
    assert row.incident_open_hours == 30.0


@pytest.mark.asyncio
async def test_budget_at_risk_prices_the_campaigns_whose_actions_are_held():
    """
    Held automation is priced from the campaigns' own daily budgets.

    Each campaign counts once however many actions target it, so a tenant with
    three queued changes to one campaign is not charged three budgets.
    """
    session = PortfolioSession(
        queued_actions=[
            SimpleNamespace(
                tenant_id=TENANT, entity_type="campaign", entity_id="ext-1"
            ),
            SimpleNamespace(
                tenant_id=TENANT, entity_type="campaign", entity_id="ext-1"
            ),
            SimpleNamespace(
                tenant_id=TENANT, entity_type="campaign", entity_id="ext-2"
            ),
        ],
        campaigns=[
            SimpleNamespace(
                tenant_id=TENANT, id=11, external_id="ext-1", daily_budget_cents=250_000
            ),
            SimpleNamespace(
                tenant_id=TENANT, id=12, external_id="ext-2", daily_budget_cents=150_000
            ),
        ],
    )

    (row,) = await build_tenant_portfolio(session, [tenant_row(TENANT)], now=NOW)

    assert row.queued_actions == 3
    assert row.budget_at_risk == 4000.0


@pytest.mark.asyncio
async def test_budget_at_risk_is_null_when_held_actions_cannot_be_priced():
    """
    Something is being withheld and we cannot say how much: that is not zero.

    Reporting 0 here would tell an account manager nothing is at stake when the
    truth is that the amount is unknown.
    """
    session = PortfolioSession(
        queued_actions=[
            SimpleNamespace(tenant_id=TENANT, entity_type="adset", entity_id="ext-9")
        ],
        campaigns=[],
    )

    (row,) = await build_tenant_portfolio(session, [tenant_row(TENANT)], now=NOW)

    assert row.queued_actions == 1
    assert row.budget_at_risk is None


# =============================================================================
# Commercials
# =============================================================================


@pytest.mark.asyncio
async def test_renewal_date_prefers_the_paddle_period_end():
    """
    Paddle is the merchant of record, so its period end wins.

    ``plan_expires_at`` remains the fallback for admin-granted plans Paddle does
    not bill.
    """
    paddle_end = datetime(2026, 4, 1, tzinfo=UTC)
    granted_end = datetime(2026, 9, 1, tzinfo=UTC)
    tenants = [
        tenant_row(TENANT, current_period_end=paddle_end, plan_expires_at=granted_end),
        tenant_row(OTHER_TENANT, current_period_end=None, plan_expires_at=granted_end),
    ]

    rows = await build_tenant_portfolio(PortfolioSession(), tenants, now=NOW)

    assert rows[0].renewal_date == paddle_end
    assert rows[1].renewal_date == granted_end


@pytest.mark.asyncio
async def test_mrr_follows_the_paddle_subscription_state():
    """
    A trialing or canceled subscription contributes no recurring revenue.

    The portfolio's MRR tile is the sum of these, so a trial counted at list
    price would inflate it. This is the same rule the platform revenue views
    apply, from the same helper.
    """
    tenants = [
        tenant_row(TENANT, subscription_status="active"),
        tenant_row(OTHER_TENANT, subscription_status="trialing"),
    ]

    rows = await build_tenant_portfolio(PortfolioSession(), tenants, now=NOW)

    assert rows[0].mrr > 0
    assert rows[1].mrr == 0.0


@pytest.mark.asyncio
async def test_industry_comes_from_onboarding_and_is_null_when_never_stated():
    """
    The industry is the tenant's own answer, or nothing.

    Every customer used to be labelled "E-commerce" by a frontend default.
    """
    session = PortfolioSession(
        onboarding={
            TENANT: SimpleNamespace(
                industry="retail",
                industry_other=None,
                trust_threshold_autopilot=None,
                trust_threshold_alert=None,
            )
        }
    )

    rows = await build_tenant_portfolio(
        session, [tenant_row(TENANT), tenant_row(OTHER_TENANT)], now=NOW
    )

    assert rows[0].industry == "retail"
    assert rows[1].industry is None


# =============================================================================
# EMQ trend
# =============================================================================


@pytest.mark.asyncio
async def test_emq_trend_differences_two_recorded_snapshots():
    """
    The trend is the change between two readings the rollup actually wrote.

    ``fact_signal_health_daily.emq_score`` is the only persisted history of this
    quantity, so the trend is a like-for-like difference rather than a live
    window compared against a snapshot.
    """
    session = PortfolioSession(
        deliveries=[delivery(TENANT) for _ in range(50)],
        last_synced_at={TENANT: NOW - timedelta(minutes=5)},
        connections={TENANT: connection()},
        health_rows=[
            SimpleNamespace(
                tenant_id=TENANT,
                platform=channel,
                date=TODAY - timedelta(days=1),
                emq_score=80.0,
                status=None,
            )
            for channel in ("facebook", "instagram", "whatsapp")
        ]
        + [
            SimpleNamespace(
                tenant_id=TENANT,
                platform=channel,
                date=TODAY - timedelta(days=2),
                emq_score=72.0,
                status=None,
            )
            for channel in ("facebook", "instagram", "whatsapp")
        ],
    )

    (row,) = await build_tenant_portfolio(session, [tenant_row(TENANT)], now=NOW)

    assert row.emq_trend == 8.0


@pytest.mark.asyncio
async def test_emq_trend_is_null_with_only_one_snapshot():
    """One reading is not a trend."""
    session = PortfolioSession(
        deliveries=[delivery(TENANT) for _ in range(50)],
        last_synced_at={TENANT: NOW - timedelta(minutes=5)},
        connections={TENANT: connection()},
        health_rows=[
            SimpleNamespace(
                tenant_id=TENANT,
                platform=channel,
                date=TODAY - timedelta(days=1),
                emq_score=80.0,
                status=None,
            )
            for channel in ("facebook", "instagram", "whatsapp")
        ],
    )

    (row,) = await build_tenant_portfolio(session, [tenant_row(TENANT)], now=NOW)

    assert row.emq_trend is None


# =============================================================================
# The batched builder does not fan out per tenant
# =============================================================================


def _populated_session(tenant_ids):
    """A store where every listed tenant has rows for every metric."""
    return PortfolioSession(
        deliveries=[delivery(t) for t in tenant_ids for _ in range(20)],
        last_synced_at={t: NOW - timedelta(minutes=5) for t in tenant_ids},
        connections={
            t: SimpleNamespace(
                tenant_id=t, status="connected", error_count=0, last_error=None
            )
            for t in tenant_ids
        },
        onboarding={
            t: SimpleNamespace(
                industry="retail",
                industry_other=None,
                trust_threshold_autopilot=None,
                trust_threshold_alert=None,
            )
            for t in tenant_ids
        },
        metrics=[
            metric(t, days_ago=d, spend_cents=10_000, revenue_cents=30_000)
            for t in tenant_ids
            for d in (3, SPEND_WINDOW_DAYS + 3)
        ],
        alerts=[
            SimpleNamespace(tenant_id=t, created_at=NOW - timedelta(hours=4))
            for t in tenant_ids
        ],
        queued_actions=[
            SimpleNamespace(tenant_id=t, entity_type="campaign", entity_id=f"ext-{t}")
            for t in tenant_ids
        ],
        campaigns=[
            SimpleNamespace(
                tenant_id=t,
                id=100 + t,
                external_id=f"ext-{t}",
                daily_budget_cents=50_000,
            )
            for t in tenant_ids
        ],
        logins={t: NOW - timedelta(days=1) for t in tenant_ids},
        health_rows=[
            SimpleNamespace(
                tenant_id=t,
                platform=channel,
                date=TODAY - timedelta(days=offset),
                emq_score=80.0 - offset,
                status=None,
            )
            for t in tenant_ids
            for channel in ("facebook", "instagram", "whatsapp")
            for offset in (1, 2)
        ],
    )


@pytest.mark.asyncio
async def test_the_whole_portfolio_costs_a_fixed_number_of_queries():
    """
    Query count does not grow with the number of tenants listed.

    This is the difference between a batched endpoint and a per-tenant loop, and
    it is why the view can afford measured numbers at all rather than the
    invented ones it used to render for free. Both stores are fully populated so
    every branch actually runs.
    """
    one = [TENANT]
    many = list(range(1, 26))
    few_session = _populated_session(one)
    many_session = _populated_session(many)

    few_rows = await build_tenant_portfolio(
        few_session, [tenant_row(t) for t in one], now=NOW
    )
    many_rows = await build_tenant_portfolio(
        many_session, [tenant_row(t) for t in many], now=NOW
    )

    assert len(few_rows) == 1
    assert len(many_rows) == 25
    # Every tenant was really measured, not merely counted.
    assert all(row.signal_health_score is not None for row in many_rows)
    assert len(many_session.statements) == len(few_session.statements)


# =============================================================================
# The endpoint shows only the tenants the caller may list
# =============================================================================


def _caller(role: str, tenant_id: int | None):
    """A request whose identity TenantMiddleware would have set."""
    return SimpleNamespace(state=SimpleNamespace(role=role, tenant_id=tenant_id))


def test_the_portfolio_lists_exactly_what_the_plain_tenant_list_lists():
    """
    Portfolio visibility is the tenant listing's visibility, from one helper.

    Cross-tenant listing is a platform action. A tenant ADMIN administers their
    own tenant only and must never enumerate other customers - and a portfolio
    endpoint that forgot that would hand them every other customer's spend,
    MRR and renewal date. Both routes build their query here so the rule cannot
    be tightened in one and left open in the other.
    """
    from app.api.v1.endpoints.tenants import _visible_tenants_query

    platform = str(_visible_tenants_query(_caller(UserRole.SUPERADMIN.value, None)))
    admin = str(_visible_tenants_query(_caller(UserRole.ADMIN.value, 7)))
    analyst = str(_visible_tenants_query(_caller(UserRole.ANALYST.value, 7)))

    assert "tenants.id =" not in platform
    assert "tenants.id =" in admin
    assert "tenants.id =" in analyst


def test_a_tenant_scoped_caller_is_pinned_to_their_own_tenant_id():
    """The id in the filter is the caller's own, not one they supplied."""
    from app.api.v1.endpoints.tenants import _visible_tenants_query

    query = _visible_tenants_query(_caller(UserRole.ADMIN.value, 7))

    assert dict(query.compile().params)["id_1"] == 7
