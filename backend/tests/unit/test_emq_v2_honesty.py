# =============================================================================
# Stratum AI - EMQ v2 Honesty Tests
# =============================================================================
"""
Regression tests for the fabricated EMQ v2 defect.

``app/services/emq_service.py`` substituted a plausible number wherever a
tenant had no persisted signal health, and every one of those substitutions
reached the UI:

E1 ``_get_default_emq_response`` returned score 75.0, previousScore 73.0 and
   band ``directional`` with a full synthesised driver breakdown - for exactly
   the tenants ``feat/real-signal-health`` had made the rollup write no row
   for. ``get_autopilot_state`` then graded that 75 and reported ``limited``,
   i.e. "automation is partly live", for a tenant nothing was known about.
E2 ``_calculate_emq_from_records`` invented the driver values by multiplying
   the score by fixed coefficients (1.05 / 1.10 / 0.85 / 0.95 / 1.15) and used
   ``score - 2.0`` as the previous day, drawing a "-2 points from yesterday"
   delta against a comparison that had never been measured.
E3 ``_calculate_emq_from_variance`` extrapolated an entire EMQ from attribution
   accuracy and gave it a "slight boost since this is partial data".
E4 ``budget_at_risk = pending_count * 5000.0``; ``_get_default_volatility``
   generated its series from ``12.5 + i * 0.8 - (i % 3) * 2.1``;
   ``_get_default_impact`` published $24,350; ``_get_default_benchmarks``
   published a **LinkedIn** row, which is not a Meta channel and so could
   never have had a source; ``_get_default_portfolio`` published 156 tenants
   and $2,450,000 at risk.

None of it was reachable from the UI, because the frontend client asked for
``/emq/v2/tenants/{id}/...`` while the router mounts ``/tenants/{id}/emq/...``.
Repointing the client is what makes these paths live, so the guards below cover
both halves: the service tells the truth, and the client asks the right URL.
"""

import pathlib
import re
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.services.emq_service import EmqAdminService, EmqService
from app.services.signal_health import (
    COMPONENT_DELIVERY,
    COMPONENT_EMQ,
    COMPONENT_FRESHNESS,
    COMPONENT_RELIABILITY,
    weighted_score,
)

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]

TENANT = 1
TODAY = date(2026, 3, 10)
YESTERDAY = date(2026, 3, 9)


# =============================================================================
# Test doubles
# =============================================================================


def health_row(
    day: date,
    *,
    platform: str = "meta",
    emq_score: float | None = None,
    event_loss_pct: float | None = None,
    freshness_minutes: float | None = None,
    api_error_rate: float | None = None,
) -> SimpleNamespace:
    """Build one ``fact_signal_health_daily`` row with the columns given."""
    return SimpleNamespace(
        date=day,
        platform=platform,
        emq_score=emq_score,
        event_loss_pct=event_loss_pct,
        freshness_minutes=freshness_minutes,
        api_error_rate=api_error_rate,
        updated_at=datetime(2026, 3, 10, 6, 0, tzinfo=UTC),
    )


class _Result:
    """Stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, rows=()) -> None:
        """Store the rows this result hands back."""
        self._rows = list(rows)

    def scalars(self):
        """Return the row accessor."""
        return self

    def all(self):
        """Return every row."""
        return list(self._rows)

    def one_or_none(self):
        """Return the single aggregate row, or None."""
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        """Return the single scalar value, or None."""
        return self._rows[0] if self._rows else None


class FakeSession:
    """
    An AsyncSession stand-in that answers the day-scoped signal health query.

    The date filter is read back from the statement's bind parameters rather
    than assumed, so a query that stopped scoping by day would be visible here
    instead of silently passing.
    """

    def __init__(self, rows_by_date: dict[date, list] | None = None, aggregate=()) -> None:
        """Seed the store with rows per day."""
        self.rows_by_date = rows_by_date or {}
        self.aggregate = list(aggregate)

    async def execute(self, statement):
        """Answer one of the queries the EMQ service issues."""
        sql = str(statement)
        params = dict(statement.compile().params)

        if "fact_signal_health_daily" in sql and "date_trunc" not in sql:
            # Latest-date probe used when today has no rows.
            if "max(" in sql.lower():
                if not self.rows_by_date:
                    return _Result([None])
                return _Result([max(self.rows_by_date.keys())])
            # "count(" rather than "count": the row query selects
            # fact_signal_health_daily.account_id, which contains it.
            if "count(" in sql or "percentile_cont" in sql:
                return _Result(self.aggregate)
            day = params.get("date_1")
            return _Result(self.rows_by_date.get(day, []))

        # Volatility's grouped query and everything else: no rows.
        return _Result([])


# =============================================================================
# 1. A tenant with no signal health gets no score - and no autopilot
# =============================================================================


@pytest.mark.asyncio
async def test_unmeasured_tenant_gets_no_score_and_no_drivers():
    """
    The score is None and the driver list is empty, not 75 and five defaults.

    This is the common path, not an edge case: the rollup deliberately writes
    no row for a tenant whose signal cannot be substantiated, so every such
    tenant used to be handed ``_get_default_emq_response``'s mid-band score.
    """
    service = EmqService(FakeSession())

    data = await service.get_emq_score(TENANT, TODAY)

    assert data["score"] is None
    assert data["previousScore"] is None
    assert data["confidenceBand"] is None
    assert data["drivers"] == []


@pytest.mark.asyncio
async def test_unmeasured_tenant_is_frozen_not_limited():
    """
    With nothing measured the mode is ``frozen`` and no action is allowed.

    The trust gate fails closed on an unscorable tenant, so this endpoint must
    not report something more permissive than what the gate will do. It used to
    grade the invented 75 and answer ``limited``.
    """
    service = EmqService(FakeSession())

    state = await service.get_autopilot_state(TENANT)

    assert state["mode"] == "frozen"
    assert state["allowedActions"] == []
    assert state["budgetAtRisk"] is None
    assert "no signal health" in state["reason"].lower()


@pytest.mark.asyncio
async def test_budget_at_risk_is_not_sized_from_the_queue_length():
    """
    Budget at risk is None even for a tenant that *is* measured.

    ``fact_actions_queue`` carries no budget column. The number shown to
    account managers as "$12,000 protected" was the count of queued rows times
    a flat $5,000.
    """
    # get_autopilot_state always asks about the current day, so seed that one.
    today = datetime.now(UTC).date()
    session = FakeSession(
        {
            today: [
                health_row(
                    today,
                    emq_score=90.0,
                    event_loss_pct=1.0,
                    freshness_minutes=10.0,
                    api_error_rate=0.5,
                )
            ]
        }
    )
    service = EmqService(session)

    state = await service.get_autopilot_state(TENANT)

    assert state["mode"] == "normal"
    assert state["budgetAtRisk"] is None


# =============================================================================
# 2. A measured tenant is scored from its own columns
# =============================================================================


@pytest.mark.asyncio
async def test_score_is_the_shared_composite_over_the_measured_columns():
    """
    The published score is what ``weighted_score`` makes of the real columns.

    It is computed with the same function the trust gate grades rows with, so
    the number cannot mean one thing where it is shown and another where it is
    enforced. The drivers are the four columns themselves rather than the score
    multiplied by 1.05, 1.10, 0.85 and 0.95.
    """
    rows = [
        health_row(
            TODAY,
            emq_score=80.0,
            event_loss_pct=10.0,  # -> delivery 90
            freshness_minutes=10.0,  # -> freshness 100 (inside fresh bound)
            api_error_rate=4.0,  # -> reliability 96
        )
    ]
    service = EmqService(FakeSession({TODAY: rows}))

    data = await service.get_emq_score(TENANT, TODAY)

    expected, _, _ = weighted_score(
        {
            COMPONENT_EMQ: 80.0,
            COMPONENT_DELIVERY: 90.0,
            COMPONENT_FRESHNESS: 100.0,
            COMPONENT_RELIABILITY: 96.0,
        }
    )
    assert data["score"] == expected
    assert expected is not None

    by_name = {d["name"]: d["value"] for d in data["drivers"]}
    assert by_name == {
        "Event Match Quality": 80.0,
        "Freshness": 100.0,
        "Delivery": 90.0,
        "API Reliability": 96.0,
    }
    # No previous day was recorded, so there is no direction to report.
    assert all(d["trend"] is None for d in data["drivers"])


@pytest.mark.asyncio
async def test_an_unmeasured_column_is_dropped_rather_than_defaulted():
    """
    A NULL column produces no driver at all, and never a substituted value.

    ``weighted_score`` renormalises over what is present and returns None below
    the configured evidence floor, so a row carrying one trivially-perfect
    column cannot renormalise its way to a passing score.
    """
    rows = [health_row(TODAY, emq_score=88.0, freshness_minutes=10.0)]
    service = EmqService(FakeSession({TODAY: rows}))

    data = await service.get_emq_score(TENANT, TODAY)

    names = {d["name"] for d in data["drivers"]}
    assert names == {"Event Match Quality", "Freshness"}
    assert "Delivery" not in names
    assert "API Reliability" not in names


@pytest.mark.asyncio
async def test_previous_score_comes_from_the_previous_day_or_is_absent():
    """
    ``previousScore`` is measured or None - never ``score - 2.0``.

    The endpoint's fallback drew a gentle two-point decline for every tenant
    whose history began today.
    """
    service = EmqService(
        FakeSession(
            {
                TODAY: [health_row(TODAY, emq_score=70.0, event_loss_pct=0.0)],
                YESTERDAY: [health_row(YESTERDAY, emq_score=60.0, event_loss_pct=0.0)],
            }
        )
    )

    data = await service.get_emq_score(TENANT, TODAY)

    assert data["previousScore"] is not None
    assert data["previousScore"] != data["score"] - 2.0
    # Both days measured, so each driver carries a real direction.
    assert {d["trend"] for d in data["drivers"]} <= {"up", "down", "flat"}

    alone = EmqService(FakeSession({TODAY: [health_row(TODAY, emq_score=70.0, event_loss_pct=0.0)]}))
    assert (await alone.get_emq_score(TENANT, TODAY))["previousScore"] is None


# =============================================================================
# 3. The remaining surfaces report absence rather than a shape
# =============================================================================


@pytest.mark.asyncio
async def test_volatility_without_history_is_absent_not_a_generated_series():
    """No history means no SVI, no trend and no points."""
    volatility = await EmqService(FakeSession()).get_volatility(TENANT)

    assert volatility["svi"] is None
    assert volatility["trend"] is None
    assert volatility["weeklyData"] == []


@pytest.mark.asyncio
async def test_impact_reports_no_counterfactual_roas():
    """Nothing models ROAS under perfect attribution, so nothing claims one."""
    impact = await EmqService(FakeSession()).get_impact(TENANT, YESTERDAY, TODAY)

    assert impact["totalImpact"] is None
    assert impact["breakdown"] == []


@pytest.mark.asyncio
async def test_admin_surfaces_are_empty_rather_than_populated():
    """
    Benchmarks and the portfolio report nothing when nothing was measured.

    The removed fallbacks published a LinkedIn benchmark - not a Meta channel,
    so it could not have had a source - and a portfolio of 156 tenants with
    $2,450,000 at risk, on the super admin's platform-wide view.
    """
    admin = EmqAdminService(FakeSession())

    assert await admin.get_benchmarks(TODAY) == []

    portfolio = await admin.get_portfolio(TODAY)
    assert portfolio["totalTenants"] == 0
    assert portfolio["avgScore"] is None
    assert portfolio["atRiskBudget"] is None
    assert portfolio["topIssues"] == []


# =============================================================================
# 4. Source guards
# =============================================================================


def _code_only(relative_path: str) -> str:
    """Return a module's source with comments and string literals stripped."""
    import tokenize

    path = BACKEND_ROOT / relative_path
    with path.open("rb") as handle:
        return "".join(
            token.string
            for token in tokenize.tokenize(handle.readline)
            if token.type not in (tokenize.COMMENT, tokenize.STRING)
        )


def _tsx_dense(relative_path: str) -> str:
    """Return a TSX file's comment-free source with all whitespace removed."""
    source = (BACKEND_ROOT.parent / relative_path).read_text()
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    source = re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)
    return re.sub(r"\s+", "", source)


def test_the_emq_service_keeps_no_default_response():
    """Every fabricated constant is gone from the service's code."""
    source = _code_only("app/services/emq_service.py")

    assert "_get_default_emq_response" not in source
    assert "_get_estimated_drivers" not in source
    assert "_get_default_volatility" not in source
    assert "_get_default_impact" not in source
    assert "_get_default_benchmarks" not in source
    assert "_get_default_portfolio" not in source
    assert "_calculate_emq_from_variance" not in source

    # The literals those functions were built out of.
    for invented in ("75.0", "73.0", "5000.0", "15.3", "24350", "2450000", "156"):
        assert invented not in source, invented

    # The score is the shared composite, not a private average with private
    # driver coefficients and private band edges.
    assert "weighted_score" in source
    assert "component_weights" in source
    assert "emq_confidence_reliable_threshold" in source


def test_the_frontend_client_asks_for_the_mounted_routes():
    """
    The client's paths match the router, and its types can say "not measured".

    Every hook in this module 404'd: ``emq_v2.router`` is registered with no
    prefix, so its routes are ``/tenants/{id}/emq/...``, and the client asked
    for ``/emq/v2/tenants/{id}/...``.
    """
    client = _tsx_dense("frontend/src/api/emqV2.ts")

    assert "/emq/v2/" not in client
    assert "`/tenants/${tenantId}/emq/score`" in client
    assert "`/tenants/${tenantId}/emq/autopilot-state`" in client
    assert "`/tenants/${tenantId}/emq/autopilot-mode`" in client
    assert "'/emq/benchmarks'" in client
    assert "'/emq/portfolio'" in client

    assert "score:number|null" in client
    assert "budgetAtRisk:number|null" in client


def test_no_view_restores_the_score_the_api_refused_to_invent():
    """
    The consumers of these hooks no longer supply their own fallback.

    Repointing the client is what makes the endpoints reachable, so a view
    still carrying ``?? 85`` would simply move the fabrication one layer out -
    and ``?? 'normal'`` would tell a tenant that full automation was live for a
    mode that had never been read.
    """
    for view in (
        "frontend/src/views/tenant/Overview.tsx",
        "frontend/src/views/tenant/SignalHub.tsx",
        "frontend/src/views/tenant/Console.tsx",
    ):
        source = _tsx_dense(view)
        assert "??85" not in source, view
        assert "??82" not in source, view
        assert "??'normal'" not in source, view
        assert "emqData?.score??null" in source, view

    # An unknown autopilot mode must hold actions, not clear them: the panel
    # defaulted the mode to 'normal' when none was supplied.
    panel = _tsx_dense("frontend/src/components/shared/ActionsPanel.tsx")
    assert "autopilotMode='normal'" not in panel
    assert "autopilotMode===null" in panel

@pytest.mark.asyncio
async def test_falls_back_to_latest_measured_day_when_today_empty():
    """Default score reads yesterday when today has no rollup row yet."""
    rows = [
        health_row(
            YESTERDAY,
            emq_score=80.0,
            event_loss_pct=10.0,
            freshness_minutes=10.0,
            api_error_rate=4.0,
        )
    ]
    service = EmqService(FakeSession({YESTERDAY: rows}))

    data = await service.get_emq_score(TENANT)  # no explicit date

    assert data["score"] is not None
    assert data["measuredDate"] == YESTERDAY.isoformat()

