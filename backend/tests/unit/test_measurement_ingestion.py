# =============================================================================
# Stratum AI - GA4 Ingestion Unit Tests (read-only measurement)
# =============================================================================
"""
Unit tests for ``app.services.measurement.ga4_ingestion``.

No network, no database: the session is an ``AsyncMock`` and the GA4 client
is a fake.
"""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.measurement import MeasurementStatus, TenantGA4Integration
from app.services.measurement import ga4_ingestion
from app.services.measurement.ga4_client import (
    GA4AuthError,
    GA4ClientError,
    GA4DailyRow,
)
from app.services.measurement.ga4_ingestion import (
    META_SOURCE_TOKENS,
    NOT_CONFIGURED_MESSAGE,
    GA4SyncResult,
    classify_meta_traffic,
    is_paid_medium,
    list_tenants_with_ga4,
    load_ga4_client_for_tenant,
    rows_to_fact_values,
    sync_ga4_for_tenant,
)

pytestmark = pytest.mark.unit


# =============================================================================
# classify_meta_traffic
# =============================================================================


@pytest.mark.parametrize(
    "source,medium,expected",
    [
        # Facebook variants
        ("facebook", "paid_social", (True, "facebook")),
        ("Facebook", "cpc", (True, "facebook")),
        ("fb", "paid", (True, "facebook")),
        ("meta", "paidsocial", (True, "facebook")),
        ("facebook.com", "referral", (True, "facebook")),
        ("l.facebook.com", "referral", (True, "facebook")),
        ("m.facebook.com", "referral", (True, "facebook")),
        ("https://www.facebook.com/", "referral", (True, "facebook")),
        ("facebook_paid", "ppc", (True, "facebook")),
        ("meta_ads", "social", (True, "facebook")),
        ("fb_stories", "social", (True, "facebook")),
        # Instagram variants
        ("instagram", "social", (True, "instagram")),
        ("ig", "paid_social", (True, "instagram")),
        ("l.instagram.com", "referral", (True, "instagram")),
        ("instagram_stories", "paid_social", (True, "instagram")),
        ("ig_reels", "social", (True, "instagram")),
        # WhatsApp variants
        ("whatsapp", "referral", (True, "whatsapp")),
        ("wa", "(not set)", (True, "whatsapp")),
        ("api.whatsapp.com", "referral", (True, "whatsapp")),
        ("wa_broadcast", "message", (True, "whatsapp")),
        # Organic Meta traffic still counts as Meta (medium does not restrict)
        ("facebook", "organic", (True, "facebook")),
        ("instagram", "(none)", (True, "instagram")),
        # Never Meta
        ("google", "cpc", (False, None)),
        ("google", "gclid", (False, None)),
        ("gclid", "cpc", (False, None)),
        ("google_ads", "paid_social", (False, None)),
        ("doubleclick", "cpc", (False, None)),
        ("youtube", "social", (False, None)),
        ("tiktok", "paid_social", (False, None)),
        ("snapchat", "cpc", (False, None)),
        ("(direct)", "(none)", (False, None)),
        ("(not set)", "paid_social", (False, None)),
        ("", "cpc", (False, None)),
        ("newsletter", "email", (False, None)),
        ("bing", "cpc", (False, None)),
    ],
)
def test_classify_meta_traffic_matrix(source, medium, expected):
    assert classify_meta_traffic(source, medium) == expected


def test_meta_source_tokens_never_include_google():
    for token, channel in META_SOURCE_TOKENS.items():
        assert "google" not in token
        assert "gclid" not in token
        assert channel in {"facebook", "instagram", "whatsapp"}


@pytest.mark.parametrize(
    "medium,expected",
    [
        ("paid_social", True),
        ("CPC", True),
        ("paid", True),
        ("social", True),
        ("paidsocial", True),
        ("ppc", True),
        ("organic", False),
        ("referral", False),
        ("(none)", False),
        ("", False),
        (None, False),
    ],
)
def test_is_paid_medium(medium, expected):
    assert is_paid_medium(medium) is expected


# =============================================================================
# rows_to_fact_values
# =============================================================================


def test_rows_to_fact_values_classifies_and_merges_duplicates():
    rows = [
        GA4DailyRow(
            date(2026, 3, 1), "facebook", "paid_social", "spring", 10, 2, 100.0, 120.0, "USD"
        ),
        GA4DailyRow(date(2026, 3, 1), "facebook", "paid_social", "spring", 5, 1, 50.0, 60.0, "USD"),
        GA4DailyRow(date(2026, 3, 1), "google", "cpc", "brand", 8, 3, 80.0, 80.0, "USD"),
    ]
    values = rows_to_fact_values(rows, tenant_id=3, property_id="123")
    assert len(values) == 2

    fb = next(v for v in values if v["utm_source"] == "facebook")
    assert fb["tenant_id"] == 3
    assert fb["property_id"] == "123"
    assert fb["is_meta_traffic"] is True
    assert fb["meta_channel"] == "facebook"
    assert fb["sessions"] == 15
    assert fb["conversions"] == 3
    assert fb["revenue"] == pytest.approx(150.0)
    assert fb["total_revenue"] == pytest.approx(180.0)
    assert fb["currency"] == "USD"
    assert fb["ingested_at"].tzinfo is not None

    g = next(v for v in values if v["utm_source"] == "google")
    assert g["is_meta_traffic"] is False
    assert g["meta_channel"] is None


# =============================================================================
# Session fakes
# =============================================================================


def _session_with_integration(integration):
    """AsyncMock session whose first select returns ``integration``."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = integration
    result.scalar.return_value = 0
    result.scalars.return_value.all.return_value = []
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


def _integration(**overrides):
    values = {
        "tenant_id": 1,
        "property_id": "123",
        "service_account_json_encrypted": "enc",
        "conversion_event_names": ["purchase"],
        "status": MeasurementStatus.DISCONNECTED.value,
        "is_active": True,
    }
    values.update(overrides)
    return TenantGA4Integration(**values)


# =============================================================================
# sync_ga4_for_tenant - not configured paths (never raise)
# =============================================================================


async def test_sync_returns_not_configured_when_no_integration():
    db = _session_with_integration(None)
    result = await sync_ga4_for_tenant(db, tenant_id=1)

    assert isinstance(result, GA4SyncResult)
    assert result.tenant_id == 1
    assert result.configured is False
    assert result.success is False
    assert result.rows_upserted == 0
    assert result.start_date is None
    assert result.end_date is None
    assert result.message == NOT_CONFIGURED_MESSAGE == "GA4 not configured"
    db.commit.assert_not_awaited()


async def test_sync_returns_not_configured_when_no_credentials():
    db = _session_with_integration(_integration(service_account_json_encrypted=None))
    result = await sync_ga4_for_tenant(db, tenant_id=1, lookback_days=5, backfill=True)
    assert result.configured is False
    assert result.success is False
    assert result.message == "GA4 not configured"


async def test_sync_returns_not_configured_when_credentials_invalid(monkeypatch):
    monkeypatch.setattr(ga4_ingestion, "decrypt_token", lambda _enc: "not json at all")
    db = _session_with_integration(_integration())
    result = await sync_ga4_for_tenant(db, tenant_id=1)
    assert result.configured is False
    assert result.message == "GA4 not configured"


async def test_sync_returns_not_configured_when_decrypt_fails(monkeypatch):
    def boom(_enc):
        raise ValueError("bad key")

    monkeypatch.setattr(ga4_ingestion, "decrypt_token", boom)
    db = _session_with_integration(_integration())
    result = await sync_ga4_for_tenant(db, tenant_id=1)
    assert result.configured is False


async def test_load_ga4_client_for_tenant_none_when_missing():
    db = _session_with_integration(None)
    assert await load_ga4_client_for_tenant(db, 1) is None


async def test_list_tenants_with_ga4_returns_ints():
    result = MagicMock()
    result.scalars.return_value.all.return_value = [3, 1, 2]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    assert await list_tenants_with_ga4(db) == [3, 1, 2]


# =============================================================================
# sync_ga4_for_tenant - configured paths with a fake client
# =============================================================================


class _FakeClient:
    def __init__(self, rows=None, error=None):
        self.property_id = "123"
        self.rows = rows or []
        self.error = error
        self.calls = []

    async def run_daily_report(self, start_date, end_date, conversion_events=None):
        self.calls.append((start_date, end_date, conversion_events))
        if self.error:
            raise self.error
        return self.rows


async def test_sync_marks_error_status_on_client_failure(monkeypatch):
    integration = _integration()
    fake = _FakeClient(error=GA4AuthError("The service account does not have access"))
    monkeypatch.setattr(ga4_ingestion, "build_client_from_integration", lambda _i: fake)
    db = _session_with_integration(integration)

    result = await sync_ga4_for_tenant(db, tenant_id=1, lookback_days=3)

    assert result.configured is True
    assert result.success is False
    assert result.rows_upserted == 0
    assert "does not have access" in result.message
    assert integration.status == MeasurementStatus.ERROR.value
    assert "does not have access" in integration.last_error
    db.commit.assert_awaited()


async def test_sync_upserts_rows_and_marks_connected(monkeypatch):
    integration = _integration(conversion_event_names=["purchase", "lead"])
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    fake = _FakeClient(
        rows=[
            GA4DailyRow(yesterday, "instagram", "paid_social", "c1", 20, 4, 200.0, 210.0, "USD"),
            GA4DailyRow(yesterday, "google", "cpc", "c2", 30, 6, 300.0, 300.0, "USD"),
        ]
    )
    monkeypatch.setattr(ga4_ingestion, "build_client_from_integration", lambda _i: fake)

    upserted: list[list[dict]] = []

    async def fake_upsert(db, values):
        upserted.append(values)
        return len(values)

    monkeypatch.setattr(ga4_ingestion, "upsert_fact_rows", fake_upsert)

    db = _session_with_integration(integration)  # scalar() -> 0 fact rows => backfill window
    result = await sync_ga4_for_tenant(db, tenant_id=1, lookback_days=3)

    assert result.configured is True
    assert result.success is True
    assert result.rows_upserted == 2
    assert result.end_date == yesterday
    # No existing rows -> backfill window (>= ga4_backfill_days)
    assert (result.end_date - result.start_date).days + 1 >= 30
    assert fake.calls[0][2] == ["purchase", "lead"]

    assert integration.status == MeasurementStatus.CONNECTED.value
    assert integration.last_sync_rows == 2
    assert integration.last_error is None
    assert integration.last_sync_at is not None
    db.commit.assert_awaited()

    assert len(upserted) == 1
    meta_rows = [v for v in upserted[0] if v["is_meta_traffic"]]
    assert len(meta_rows) == 1
    assert meta_rows[0]["meta_channel"] == "instagram"
    # The Meta row is identified and persisted to fact_ga4_daily. It used to be
    # forwarded to an in-process EMQ store as well; that store was per-process
    # and read by nothing that survived the request, so it is gone and
    # fact_ga4_daily is the GA4 baseline's only home.
    assert meta_rows[0]["conversions"] == 4
    assert meta_rows[0]["revenue"] == pytest.approx(200.0)


async def test_sync_uses_lookback_window_when_rows_exist(monkeypatch):
    integration = _integration()
    fake = _FakeClient(rows=[])
    monkeypatch.setattr(ga4_ingestion, "build_client_from_integration", lambda _i: fake)

    async def fake_upsert(db, values):
        return 0

    monkeypatch.setattr(ga4_ingestion, "upsert_fact_rows", fake_upsert)

    db = _session_with_integration(integration)
    db.execute.return_value.scalar.return_value = 42  # existing fact rows

    result = await sync_ga4_for_tenant(db, tenant_id=1, lookback_days=3)
    assert result.success is True
    assert (result.end_date - result.start_date).days + 1 == 3
    today = datetime.now(UTC).date()
    assert result.end_date == today - timedelta(days=1)
    assert result.start_date == today - timedelta(days=3)


async def test_sync_generic_client_error_does_not_raise(monkeypatch):
    integration = _integration()
    fake = _FakeClient(error=GA4ClientError("quota exhausted"))
    monkeypatch.setattr(ga4_ingestion, "build_client_from_integration", lambda _i: fake)
    db = _session_with_integration(integration)
    result = await sync_ga4_for_tenant(db, tenant_id=1)
    assert result.success is False
    assert result.configured is True
    assert "quota" in result.message
