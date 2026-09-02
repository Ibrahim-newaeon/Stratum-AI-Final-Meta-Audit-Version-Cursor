# =============================================================================
# Stratum AI - GA4 Data API Client Unit Tests (read-only measurement)
# =============================================================================
"""
Unit tests for ``app.services.measurement.ga4_client``.

No network, no database: the GA4 transport is replaced with fakes.
"""

import json
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.measurement.ga4_client import (
    GA4_READONLY_SCOPE,
    GA4AuthError,
    GA4ClientError,
    GA4DailyRow,
    GA4DataClient,
    GA4NotConfiguredError,
    GA4TestResult,
    normalize_property_id,
    response_to_dicts,
    translate_google_exception,
)

pytestmark = pytest.mark.unit

VALID_SA = {
    "type": "service_account",
    "project_id": "stratum-test",
    "client_email": "ga4-reader@stratum-test.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
}


# =============================================================================
# Fakes
# =============================================================================


def _fake_response(
    dimension_names: list[str],
    metric_names: list[str],
    rows: list[list[str]],
    currency: str = "USD",
) -> Any:
    """Build a RunReportResponse-like object from header names and row values."""
    n_dims = len(dimension_names)
    fake_rows = []
    for values in rows:
        fake_rows.append(
            SimpleNamespace(
                dimension_values=[SimpleNamespace(value=v) for v in values[:n_dims]],
                metric_values=[SimpleNamespace(value=v) for v in values[n_dims:]],
            )
        )
    return SimpleNamespace(
        dimension_headers=[SimpleNamespace(name=n) for n in dimension_names],
        metric_headers=[SimpleNamespace(name=n) for n in metric_names],
        rows=fake_rows,
        row_count=len(fake_rows),
        metadata=SimpleNamespace(currency_code=currency),
    )


# =============================================================================
# Constants / helpers
# =============================================================================


def test_readonly_scope_constant():
    assert GA4_READONLY_SCOPE == "https://www.googleapis.com/auth/analytics.readonly"


def test_exception_hierarchy():
    assert issubclass(GA4NotConfiguredError, GA4ClientError)
    assert issubclass(GA4AuthError, GA4ClientError)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("123456789", "123456789"),
        (" properties/123456789 ", "123456789"),
        ("PROPERTIES/42", "42"),
    ],
)
def test_normalize_property_id(raw, expected):
    assert normalize_property_id(raw) == expected


def test_response_to_dicts_maps_headers_to_values():
    response = _fake_response(
        ["date", "sessionSource"], ["sessions"], [["20260101", "facebook", "12"]]
    )
    assert response_to_dicts(response) == [
        {"date": "20260101", "sessionSource": "facebook", "sessions": "12"}
    ]


# =============================================================================
# from_service_account_json validation
# =============================================================================


def test_from_service_account_json_valid():
    client = GA4DataClient.from_service_account_json("properties/555", json.dumps(VALID_SA))
    assert client.property_id == "555"
    assert client.property_path == "properties/555"
    assert client.service_account_email == VALID_SA["client_email"]
    assert client.timeout_seconds == 30.0


@pytest.mark.parametrize(
    "payload,fragment",
    [
        ("", "empty"),
        ("not json", "not valid JSON"),
        ("[1, 2]", "JSON object"),
        (json.dumps({**VALID_SA, "type": "authorized_user"}), "type='service_account'"),
        (json.dumps({k: v for k, v in VALID_SA.items() if k != "client_email"}), "client_email"),
        (json.dumps({k: v for k, v in VALID_SA.items() if k != "private_key"}), "private_key"),
    ],
)
def test_from_service_account_json_rejects_invalid(payload, fragment):
    with pytest.raises(GA4AuthError) as exc_info:
        GA4DataClient.from_service_account_json("123", payload)
    assert fragment in str(exc_info.value)


def test_missing_property_id_raises_not_configured():
    with pytest.raises(GA4NotConfiguredError):
        GA4DataClient("", VALID_SA)


def test_private_key_never_exposed_via_email_property():
    client = GA4DataClient("123", VALID_SA)
    assert "PRIVATE KEY" not in client.service_account_email
    assert "PRIVATE KEY" not in repr(client.service_account_email)


# =============================================================================
# run_daily_report parsing
# =============================================================================


async def test_run_daily_report_parses_rows_and_merges_conversion_report(monkeypatch):
    client = GA4DataClient("123", VALID_SA)
    calls: list[dict[str, Any]] = []

    async def fake_run_report(**kwargs):
        calls.append(kwargs)
        if kwargs.get("event_names"):
            # Second report: eventCount filtered by eventName
            return _fake_response(
                ["date", "sessionSource", "sessionMedium", "sessionCampaignName", "eventName"],
                ["eventCount"],
                [
                    ["20260301", "facebook", "paid_social", "spring_sale", "purchase", "4"],
                    ["20260301", "facebook", "paid_social", "spring_sale", "lead", "2"],
                    ["20260301", "google", "cpc", "brand", "purchase", "9"],
                ],
            )
        return _fake_response(
            ["date", "sessionSource", "sessionMedium", "sessionCampaignName"],
            ["sessions", "keyEvents", "purchaseRevenue", "totalRevenue"],
            [
                ["20260301", "facebook", "paid_social", "spring_sale", "120", "7", "540.5", "600"],
                ["20260301", "google", "cpc", "brand", "80", "10", "900", "950"],
                ["20260302", "instagram", "social", "(not set)", "15", "0", "0", "0"],
            ],
        )

    monkeypatch.setattr(client, "_run_report", fake_run_report)

    rows = await client.run_daily_report(
        date(2026, 3, 1), date(2026, 3, 2), conversion_events=["purchase", "lead"]
    )

    assert len(calls) == 2
    assert calls[0]["dimensions"] == (
        "date",
        "sessionSource",
        "sessionMedium",
        "sessionCampaignName",
    )
    assert calls[0]["metrics"] == ("sessions", "keyEvents", "purchaseRevenue", "totalRevenue")
    assert calls[0]["start_date"] == "2026-03-01"
    assert calls[0]["end_date"] == "2026-03-02"
    assert calls[1]["event_names"] == ["purchase", "lead"]
    assert calls[1]["metrics"] == ("eventCount",)

    assert all(isinstance(r, GA4DailyRow) for r in rows)
    by_key = {(r.date, r.utm_source): r for r in rows}

    fb = by_key[(date(2026, 3, 1), "facebook")]
    assert fb.utm_medium == "paid_social"
    assert fb.utm_campaign == "spring_sale"
    assert fb.sessions == 120
    assert fb.conversions == 6  # purchase(4) + lead(2) from the filtered report
    assert fb.revenue == pytest.approx(540.5)
    assert fb.total_revenue == pytest.approx(600.0)
    assert fb.currency == "USD"

    g = by_key[(date(2026, 3, 1), "google")]
    assert g.conversions == 9

    ig = by_key[(date(2026, 3, 2), "instagram")]
    assert ig.conversions == 0  # no matching conversion events -> 0, not keyEvents
    assert ig.sessions == 15


async def test_run_daily_report_uses_key_events_without_conversion_filter(monkeypatch):
    client = GA4DataClient("123", VALID_SA)

    async def fake_run_report(**kwargs):
        assert not kwargs.get("event_names")
        return _fake_response(
            ["date", "sessionSource", "sessionMedium", "sessionCampaignName"],
            ["sessions", "keyEvents", "purchaseRevenue", "totalRevenue"],
            [["20260301", "fb", "cpc", "x", "10", "3", "99.0", "99.0"]],
        )

    monkeypatch.setattr(client, "_run_report", fake_run_report)
    rows = await client.run_daily_report(date(2026, 3, 1), date(2026, 3, 1))
    assert len(rows) == 1
    assert rows[0].conversions == 3
    assert rows[0].revenue == pytest.approx(99.0)


async def test_run_daily_report_rejects_inverted_window():
    client = GA4DataClient("123", VALID_SA)
    with pytest.raises(GA4ClientError):
        await client.run_daily_report(date(2026, 3, 2), date(2026, 3, 1))


async def test_run_daily_report_paginates(monkeypatch):
    client = GA4DataClient("123", VALID_SA)
    offsets: list[int] = []

    async def fake_run_report(**kwargs):
        offsets.append(kwargs["offset"])
        from app.services.measurement import ga4_client as mod

        page_size = mod.GA4_PAGE_LIMIT
        total = page_size + 5
        start = kwargs["offset"]
        end = min(start + page_size, total)
        rows = [
            [f"2026030{1 + (i % 2)}", f"src{i}", "cpc", "c", "1", "0", "0", "0"]
            for i in range(start, end)
        ]
        resp = _fake_response(
            ["date", "sessionSource", "sessionMedium", "sessionCampaignName"],
            ["sessions", "keyEvents", "purchaseRevenue", "totalRevenue"],
            rows,
        )
        resp.row_count = total
        return resp

    monkeypatch.setattr(client, "_run_report", fake_run_report)
    rows = await client.run_daily_report(date(2026, 3, 1), date(2026, 3, 2))
    assert offsets == [0, 10_000]
    assert len(rows) == 10_005


# =============================================================================
# test_connection
# =============================================================================


async def test_test_connection_success(monkeypatch):
    client = GA4DataClient("123", VALID_SA)

    async def fake_run_report(**kwargs):
        assert kwargs["start_date"] == "7daysAgo"
        assert kwargs["end_date"] == "yesterday"
        assert kwargs["metrics"] == ("sessions", "keyEvents", "purchaseRevenue")
        return _fake_response(
            [], ["sessions", "keyEvents", "purchaseRevenue"], [["321", "12", "1500.25"]]
        )

    monkeypatch.setattr(client, "_run_report", fake_run_report)
    result = await client.test_connection()
    assert isinstance(result, GA4TestResult)
    assert result.success is True
    assert result.property_id == "123"
    assert result.sessions_last_7d == 321
    assert result.conversions_last_7d == 12
    assert result.revenue_last_7d == pytest.approx(1500.25)
    assert "Read-only" in result.message


async def test_test_connection_reports_auth_error_without_raising(monkeypatch):
    client = GA4DataClient("123", VALID_SA)

    async def fake_run_report(**kwargs):
        raise GA4AuthError("The service account does not have access to this GA4 property.")

    monkeypatch.setattr(client, "_run_report", fake_run_report)
    result = await client.test_connection()
    assert result.success is False
    assert "does not have access" in result.message
    assert result.sessions_last_7d is None


async def test_test_connection_reports_missing_package(monkeypatch):
    client = GA4DataClient("123", VALID_SA)

    async def fake_run_report(**kwargs):
        raise GA4ClientError("google-analytics-data not installed")

    monkeypatch.setattr(client, "_run_report", fake_run_report)
    result = await client.test_connection()
    assert result.success is False
    assert "not installed" in result.message


# =============================================================================
# Google exception mapping
# =============================================================================


def test_translate_permission_denied_to_auth_error():
    gexc = pytest.importorskip("google.api_core.exceptions")
    mapped = translate_google_exception(gexc.PermissionDenied("nope"))
    assert isinstance(mapped, GA4AuthError)
    assert "Viewer" in str(mapped)


def test_translate_unauthenticated_to_auth_error():
    gexc = pytest.importorskip("google.api_core.exceptions")
    mapped = translate_google_exception(gexc.Unauthenticated("bad key"))
    assert isinstance(mapped, GA4AuthError)
    assert "authentication failed" in str(mapped).lower()


def test_translate_not_found_is_client_error_not_auth():
    gexc = pytest.importorskip("google.api_core.exceptions")
    mapped = translate_google_exception(gexc.NotFound("missing"))
    assert isinstance(mapped, GA4ClientError)
    assert not isinstance(mapped, GA4AuthError)


def test_translate_value_error_is_auth_error():
    mapped = translate_google_exception(ValueError("Could not deserialize key data"))
    assert isinstance(mapped, GA4AuthError)


def test_translate_passthrough_for_ga4_errors():
    original = GA4ClientError("x")
    assert translate_google_exception(original) is original


def test_get_client_requests_readonly_scope_only(monkeypatch):
    """The credentials must be created with exactly the read-only scope."""
    pytest.importorskip("google.analytics.data_v1beta")
    from google.oauth2 import service_account

    captured: dict[str, Any] = {}

    def fake_from_info(info, scopes=None, **kwargs):
        captured["scopes"] = list(scopes or [])
        captured["email"] = info.get("client_email")
        return object()

    class FakeBetaClient:
        def __init__(self, credentials=None, **kwargs):
            captured["credentials"] = credentials

    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", fake_from_info)
    from google.analytics import data_v1beta

    monkeypatch.setattr(data_v1beta, "BetaAnalyticsDataClient", FakeBetaClient)

    client = GA4DataClient("123", VALID_SA)
    built = client._get_client()
    assert isinstance(built, FakeBetaClient)
    assert captured["scopes"] == [GA4_READONLY_SCOPE]
    assert captured["email"] == VALID_SA["client_email"]
    # cached
    assert client._get_client() is built
