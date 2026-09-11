# =============================================================================
# Competitor refresh — MarketIntelligenceService wiring + fail-closed policy
# =============================================================================
"""Unit tests for Module D competitor fetch persistence."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.market_proxy import CompetitorData, MarketIntelligenceService
from app.workers.tasks import competitors as competitors_tasks

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_paid_provider_failure_does_not_invent_mock_traffic(monkeypatch):
    """When serpapi fails, estimated_traffic stays None (fail closed)."""
    monkeypatch.setattr(
        "app.services.market_proxy.settings.market_intel_provider", "serpapi"
    )
    monkeypatch.setattr("app.services.market_proxy.settings.serpapi_key", "fake-key")

    service = MarketIntelligenceService()

    async def boom(domain: str) -> CompetitorData:
        return CompetitorData(domain=domain, error="serpapi down", data_source="serpapi")

    async def scraper_ok(domain: str) -> CompetitorData:
        return CompetitorData(
            domain=domain,
            meta_title="Acme",
            data_source="scraper",
            fetched_at=datetime.now(UTC),
        )

    service.providers["serpapi"].get_competitor_data = boom  # type: ignore[method-assign]
    service.providers["scraper"].get_competitor_data = scraper_ok  # type: ignore[method-assign]

    data = await service.get_competitor_data("acme.example")

    assert data.error == "serpapi down"
    assert data.estimated_traffic is None
    assert data.top_keywords is None
    assert data.meta_title == "Acme"


def test_apply_market_data_maps_fields():
    competitor = SimpleNamespace(
        data_source="scraper",
        last_fetched_at=None,
        fetch_error="old",
    )
    data = CompetitorData(
        domain="acme.example",
        meta_title="Acme",
        estimated_traffic=1200,
        traffic_trend="up",
        paid_keywords_count=3,
        organic_keywords_count=9,
        data_source="mock",
        fetched_at=datetime(2026, 3, 10, tzinfo=UTC),
        error=None,
    )
    competitors_tasks._apply_market_data(competitor, data)
    assert competitor.meta_title == "Acme"
    assert competitor.estimated_traffic == 1200
    assert competitor.paid_keywords_count == 3
    assert competitor.data_source == "mock"
    assert competitor.fetch_error is None
    assert competitor.last_fetched_at == data.fetched_at


def test_fetch_competitor_data_persists_mock_result():
    """
    Exercise the Celery task without ``select(MagicMock())``.

    SQLAlchemy rejects a MagicMock entity in ``select()``, so the model and
    ``select`` are stubbed at module level while ``db.execute`` returns the
    row fixtures directly.
    """
    model = MagicMock(name="CompetitorBenchmark")
    competitor = SimpleNamespace(
        id=7,
        tenant_id=1,
        domain="acme.example",
        data_source="scraper",
        last_fetched_at=None,
        fetch_error=None,
        estimated_traffic=None,
        share_of_voice=None,
        meta_title=None,
        meta_description=None,
        meta_keywords=None,
        social_links=None,
        traffic_trend=None,
        top_keywords=None,
        paid_keywords_count=None,
        organic_keywords_count=None,
        estimated_ad_spend_cents=None,
        detected_ad_platforms=None,
    )

    db = MagicMock()
    db_cm = MagicMock()
    db_cm.__enter__.return_value = db
    db_cm.__exit__.return_value = False

    load_result = MagicMock()
    load_result.scalar_one_or_none.return_value = competitor
    sov_scalars = MagicMock()
    sov_scalars.all.return_value = [competitor]
    sov_result = MagicMock()
    sov_result.scalars.return_value = sov_scalars
    db.execute.side_effect = [load_result, sov_result]

    # Chainable select().where(...) stub — never hits SQLAlchemy's entity check.
    select_query = MagicMock(name="select_query")
    select_query.where.return_value = select_query

    async def fake_get(domain: str) -> CompetitorData:
        return CompetitorData(
            domain=domain,
            estimated_traffic=5000,
            data_source="mock",
            fetched_at=datetime.now(UTC),
        )

    with (
        patch.object(competitors_tasks, "_get_competitor_model", return_value=model),
        patch.object(competitors_tasks, "select", return_value=select_query),
        patch.object(competitors_tasks, "SyncSessionLocal", return_value=db_cm),
        patch.object(
            MarketIntelligenceService,
            "get_competitor_data",
            fake_get,
        ),
    ):
        result = competitors_tasks.fetch_competitor_data.run(1, 7)

    assert result["status"] == "ok"
    assert result["synthetic"] is True
    assert competitor.estimated_traffic == 5000
    assert competitor.share_of_voice == 100.0
    db.commit.assert_called()
