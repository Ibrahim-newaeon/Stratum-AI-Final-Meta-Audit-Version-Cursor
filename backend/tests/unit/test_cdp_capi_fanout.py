# =============================================================================
# Stratum AI - CDP → CAPI fan-out unit tests
# =============================================================================
"""Consent gating and payload mapping for CDP → Meta CAPI fan-out."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.cdp import (
    EventConsentInput,
    EventContextInput,
    EventIdentifierInput,
    EventInput,
)
from app.services.cdp.capi_fanout import (
    advertising_consent_allows,
    cdp_event_to_capi_payload,
    fanout_cdp_events_to_capi,
)


def _event(**overrides) -> EventInput:
    base = dict(
        event_name="Purchase",
        event_time=datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
        idempotency_key="evt-1",
        identifiers=[EventIdentifierInput(type="email", value="a@example.com")],
        properties={"value": 10, "currency": "USD"},
        context=EventContextInput(page_url="https://shop.example/thanks"),
        consent=None,
    )
    base.update(overrides)
    return EventInput(**base)


def test_consent_allows_when_omitted():
    assert advertising_consent_allows(_event(consent=None)) is True


def test_consent_requires_explicit_advertising_true():
    assert advertising_consent_allows(_event(consent=EventConsentInput(advertising=True))) is True
    assert (
        advertising_consent_allows(_event(consent=EventConsentInput(advertising=False))) is False
    )
    assert advertising_consent_allows(_event(consent=EventConsentInput(marketing=True))) is False


def test_cdp_event_maps_identifiers_and_meta_fields():
    payload = cdp_event_to_capi_payload(
        _event(
            identifiers=[
                EventIdentifierInput(type="email", value="a@example.com"),
                EventIdentifierInput(type="phone", value="+15551212"),
                EventIdentifierInput(type="external_id", value="cust-9"),
            ]
        )
    )
    assert payload["event_name"] == "Purchase"
    assert payload["user_data"]["em"] == "a@example.com"
    assert payload["user_data"]["ph"] == "+15551212"
    assert payload["user_data"]["external_id"] == "cust-9"
    assert payload["parameters"]["value"] == 10
    assert payload["event_source_url"] == "https://shop.example/thanks"
    assert payload["event_id"] == "evt-1"
    assert isinstance(payload["event_time"], int)


@pytest.mark.asyncio
async def test_fanout_skips_without_credentials():
    db = MagicMock()
    service = MagicMock()
    service.ensure_loaded_from_db = AsyncMock(return_value=[])
    service.connectors = {}
    service.stream_events = AsyncMock()

    with patch("app.services.cdp.capi_fanout.CAPIService", return_value=service):
        summary = await fanout_cdp_events_to_capi(
            db,
            tenant_id=7,
            events=[_event()],
            platforms=["meta"],
        )

    assert summary["attempted"] == 1
    assert summary["skipped_no_credentials"] == 1
    assert summary["sent"] == 0
    service.stream_events.assert_not_called()


@pytest.mark.asyncio
async def test_fanout_skips_denied_advertising_consent():
    db = MagicMock()
    with patch("app.services.cdp.capi_fanout.CAPIService") as ctor:
        summary = await fanout_cdp_events_to_capi(
            db,
            tenant_id=7,
            events=[_event(consent=EventConsentInput(advertising=False))],
        )

    assert summary["attempted"] == 0
    assert summary["skipped_consent"] == 1
    ctor.assert_not_called()


@pytest.mark.asyncio
async def test_fanout_streams_when_meta_connected():
    db = MagicMock()
    service = MagicMock()
    service.ensure_loaded_from_db = AsyncMock(return_value=["meta"])
    service.connectors = {"meta": object()}
    service.stream_events = AsyncMock(
        return_value=MagicMock(total_events=1, platforms_sent=1, failed_platforms=[])
    )

    with patch("app.services.cdp.capi_fanout.CAPIService", return_value=service):
        summary = await fanout_cdp_events_to_capi(
            db,
            tenant_id=7,
            events=[_event()],
            platforms=["meta"],
        )

    assert summary["sent"] == 1
    assert summary["skipped_no_credentials"] == 0
    service.stream_events.assert_awaited_once()
