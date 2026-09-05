# =============================================================================
# Stratum AI - CRM Writeback Attribution Tests
# =============================================================================
"""
Unit tests for the attribution readers behind the CRM writeback services.

No network, no database: the session is an in-memory fake that returns real
``CRMDeal`` / ``Touchpoint`` instances, so the attribute names the writebacks
read are exercised against the actual models.

These functions used to reach for model attributes that do not exist
(``deal.closed_at``, ``deal.primary_contact_id``, ``deal.probability``,
``tp.platform``, ``tp.attributed_spend_cents``, ``tp.match_confidence``,
``Touchpoint.touchpoint_time``), so every deal with an amount, and every
contact with touchpoints, raised ``AttributeError`` and was counted as a
failed writeback. What is pinned here:

* the non-empty touchpoint branch runs and returns real numbers,
* ``days_to_close`` comes from ``CRMDeal.close_date``, which is a ``Date`` and
  is therefore already the day the deal closed,
* profit metrics are booked from ``is_won``, not from a probability guess,
* attributed spend is summed from ``Touchpoint.cost_cents``,
* confidence is the shared attribution-confidence score.
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

import pytest

import app.models  # noqa: F401  (registers every mapper so ORM statements compile)
from app.models.crm import CRMDeal, Touchpoint
from app.services.crm.identity_matching import calculate_attribution_confidence
from app.services.crm.pipedrive_writeback import PipedriveWritebackService
from app.services.crm.salesforce_writeback import SalesforceWritebackService

pytestmark = pytest.mark.unit

TENANT = 1


# =============================================================================
# In-memory session
# =============================================================================


class _ScalarResult:
    """Stand-in for a SQLAlchemy ``ScalarResult``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        """Return every row."""
        return list(self._rows)


class _Result:
    """Stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any | None:
        """Return the single row, or None."""
        if len(self._rows) > 1:
            raise AssertionError("scalar_one_or_none() got more than one row")
        return self._rows[0] if self._rows else None

    def scalars(self) -> _ScalarResult:
        """Return a scalar result view."""
        return _ScalarResult(self._rows)


def _selected_entity(statement: Any) -> type | None:
    """Return the ORM entity a ``select()`` targets."""
    for description in statement.column_descriptions:
        entity = description.get("entity")
        if entity is not None and description.get("expr") is entity:
            return entity
    return None


class FakeSession:
    """
    In-memory stand-in for ``AsyncSession``.

    Touchpoints are filtered by ``contact_id`` and returned in ``event_ts``
    order, the way the real ``ORDER BY`` would, so first-touch and last-touch
    really are the first and last touch.
    """

    def __init__(
        self,
        *,
        deal: CRMDeal | None = None,
        touchpoints: list[Touchpoint] | None = None,
    ) -> None:
        self.deal = deal
        self.touchpoints = touchpoints or []
        self.commits = 0

    async def execute(self, statement: Any) -> _Result:
        """Answer the two queries the attribution readers issue."""
        entity = _selected_entity(statement)
        params = statement.compile().params

        if entity is CRMDeal:
            return _Result([self.deal] if self.deal is not None else [])
        if entity is Touchpoint:
            contact_id = params.get("contact_id_1")
            matches = [tp for tp in self.touchpoints if tp.contact_id == contact_id]
            return _Result(sorted(matches, key=lambda tp: tp.event_ts))

        raise AssertionError(f"unexpected query against {entity}")

    async def commit(self) -> None:
        """Record the commit."""
        self.commits += 1


# =============================================================================
# Fixtures
# =============================================================================

CONTACT_ID = uuid4()
DEAL_ID = uuid4()

FIRST_TOUCH_TS = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
LAST_TOUCH_TS = datetime(2026, 3, 10, 9, 0, tzinfo=UTC)
CLOSE_DATE = date(2026, 3, 21)


def make_touchpoints() -> list[Touchpoint]:
    """Two Meta touchpoints, $125.00 then $75.00, deliberately out of order."""
    last = Touchpoint(
        id=uuid4(),
        tenant_id=TENANT,
        contact_id=CONTACT_ID,
        event_ts=LAST_TOUCH_TS,
        event_type="click",
        source="meta",
        campaign_id="camp-2",
        campaign_name="Spring Retarget",
        adset_id="adset-2",
        ad_id="ad-2",
        cost_cents=7_500,
    )
    first = Touchpoint(
        id=uuid4(),
        tenant_id=TENANT,
        contact_id=CONTACT_ID,
        event_ts=FIRST_TOUCH_TS,
        event_type="click",
        source="meta",
        campaign_id="camp-1",
        campaign_name="Spring Prospecting",
        adset_id="adset-1",
        ad_id="ad-1",
        cost_cents=12_500,
        fbclid="fb.1.abc",
    )
    return [last, first]


def make_deal(*, is_won: bool = True, close_date: date | None = CLOSE_DATE) -> CRMDeal:
    """A $4,000.00 deal linked to the contact that owns the touchpoints."""
    return CRMDeal(
        id=DEAL_ID,
        tenant_id=TENANT,
        connection_id=uuid4(),
        contact_id=CONTACT_ID,
        crm_deal_id="deal-1",
        deal_name="Acme expansion",
        amount_cents=400_000,
        close_date=close_date,
        is_won=is_won,
        is_closed=is_won,
    )


def services(session: FakeSession) -> list[Any]:
    """The two writeback services under test, sharing one fake session."""
    return [
        SalesforceWritebackService(session, TENANT),
        PipedriveWritebackService(session, TENANT),
    ]


# =============================================================================
# 1. The branch that used to raise AttributeError
# =============================================================================


@pytest.mark.asyncio
async def test_deal_attribution_with_touchpoints_returns_real_numbers():
    """
    Deal attribution over a non-empty touchpoint set.

    Every value here used to be unreachable: ``deal.probability`` raised before
    the spend was ever summed.
    """
    for service in services(FakeSession(deal=make_deal(), touchpoints=make_touchpoints())):
        attribution = await service._get_deal_attribution(DEAL_ID)

        assert attribution["touchpoints_count"] == 2
        # $125.00 + $75.00 of touchpoint cost.
        assert attribution["attributed_spend"] == 200.0
        # $4,000.00 of revenue against $200.00 of spend.
        assert attribution["revenue_roas"] == 20.0
        assert attribution["platform"] == "meta"
        assert attribution["campaign_name"] == "Spring Retarget"
        assert attribution["attribution_model"] == "last_touch"


@pytest.mark.asyncio
async def test_days_to_close_is_measured_from_close_date():
    """
    ``CRMDeal.close_date`` is a ``Date``: it is the day, not a timestamp.

    The old code called ``.date()`` on it, which is what made this branch raise
    as soon as a deal had touchpoints.
    """
    for service in services(FakeSession(deal=make_deal(), touchpoints=make_touchpoints())):
        attribution = await service._get_deal_attribution(DEAL_ID)

        assert attribution["days_to_close"] == (CLOSE_DATE - FIRST_TOUCH_TS.date()).days == 20


@pytest.mark.asyncio
async def test_days_to_close_is_none_without_a_close_date():
    """An open deal has no close date, so there is no time-to-close to report."""
    session = FakeSession(deal=make_deal(is_won=False, close_date=None), touchpoints=make_touchpoints())
    for service in services(session):
        attribution = await service._get_deal_attribution(DEAL_ID)

        assert attribution["days_to_close"] is None


# =============================================================================
# 2. Profit is booked from is_won, not from a probability guess
# =============================================================================


@pytest.mark.asyncio
async def test_profit_metrics_are_booked_on_a_won_deal():
    """COGS is estimated at 70% of a won deal's revenue."""
    for service in services(FakeSession(deal=make_deal(), touchpoints=make_touchpoints())):
        attribution = await service._get_deal_attribution(DEAL_ID)

        # $4,000.00 revenue - $2,800.00 COGS.
        assert attribution["gross_profit"] == 1200.0
        # Gross profit less the $200.00 of ad spend.
        assert attribution["net_profit"] == 1000.0
        assert attribution["profit_roas"] == 6.0


@pytest.mark.asyncio
async def test_profit_metrics_are_withheld_on_an_open_deal():
    """An unwon deal has no realised profit to write back."""
    session = FakeSession(deal=make_deal(is_won=False), touchpoints=make_touchpoints())
    for service in services(session):
        attribution = await service._get_deal_attribution(DEAL_ID)

        assert attribution["gross_profit"] is None
        assert attribution["net_profit"] is None
        assert attribution["profit_roas"] is None
        # Revenue attribution still stands: the pipeline value is real.
        assert attribution["revenue_roas"] == 20.0


@pytest.mark.asyncio
async def test_deal_without_touchpoints_still_reports_profit():
    """
    A deal with an amount but no matched touchpoints.

    This is the case the old ``deal.probability`` read raised on even before
    any touchpoint was loaded.
    """
    for service in services(FakeSession(deal=make_deal(), touchpoints=[])):
        attribution = await service._get_deal_attribution(DEAL_ID)

        assert attribution["touchpoints_count"] == 0
        assert attribution["attributed_spend"] is None
        assert attribution["revenue_roas"] is None
        assert attribution["profit_roas"] is None
        assert attribution["gross_profit"] == 1200.0
        assert attribution["platform"] is None


# =============================================================================
# 3. Contact attribution over the same touchpoints
# =============================================================================


@pytest.mark.asyncio
async def test_contact_attribution_with_touchpoints_returns_real_numbers():
    """Contact attribution reads Touchpoint.source, .cost_cents and .event_ts."""
    touchpoints = make_touchpoints()
    for service in services(FakeSession(touchpoints=touchpoints)):
        attribution = await service._get_contact_attribution(CONTACT_ID)

        assert attribution["platform"] == "meta"
        assert attribution["campaign_id"] == "camp-2"
        assert attribution["campaign_name"] == "Spring Retarget"
        assert attribution["first_touch_source"] == "meta:Spring Prospecting"
        assert attribution["last_touch_source"] == "meta:Spring Retarget"
        assert attribution["total_spend"] == 200.0
        assert attribution["touchpoints_count"] == 2


@pytest.mark.asyncio
async def test_contact_confidence_is_the_shared_attribution_score():
    """
    Confidence is the one attribution-confidence definition, as a percentage.

    Two touchpoints, one carrying a click ID: 0.5 base + 0.1 for the touch
    count + 0.3 for the click-ID match quality.
    """
    touchpoints = make_touchpoints()
    expected = round(calculate_attribution_confidence(touchpoints) * 100, 1)
    assert expected == 90.0

    for service in services(FakeSession(touchpoints=touchpoints)):
        attribution = await service._get_contact_attribution(CONTACT_ID)

        assert attribution["confidence"] == expected


@pytest.mark.asyncio
async def test_contact_without_touchpoints_has_no_attribution():
    """An unmatched contact has nothing to write back."""
    for service in services(FakeSession(touchpoints=[])):
        assert await service._get_contact_attribution(CONTACT_ID) == {}
