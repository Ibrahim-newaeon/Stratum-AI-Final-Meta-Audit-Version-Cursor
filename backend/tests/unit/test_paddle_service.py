# =============================================================================
# Stratum AI - Paddle Billing Service Tests
# =============================================================================
"""
Unit tests for ``app.services.paddle_service``.

No network, no database: HTTP goes through ``httpx.MockTransport`` and the
session is an ``AsyncMock``. Covers webhook signature verification, tier <->
price mapping, payload parsing, the API client (configured / not configured /
error paths) and the Tenant sync rules.
"""

import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy.dialects import postgresql

import app.models  # noqa: F401  (registers every mapper so ORM statements compile)
from app.core.config import settings
from app.core.tiers import SubscriptionTier
from app.services import paddle_service
from app.services.paddle_service import (
    PaddleClient,
    PaddleError,
    PaddleNotConfiguredError,
    PaddleSignatureError,
    PaddleSubscription,
    PortalSession,
    SubscriptionState,
    clear_tenant_subscription,
    compute_tenant_billing_updates,
    get_paddle_client,
    get_price_id_for_tier,
    get_tier_for_price_id,
    is_configured,
    parse_paddle_datetime,
    parse_signature_header,
    reset_paddle_client,
    subscription_from_payload,
    sync_tenant_paddle_customer,
    sync_tenant_subscription,
    transaction_from_payload,
    verify_webhook_signature,
)

pytestmark = pytest.mark.unit

SECRET = "pdl_ntfset_unit_test_secret"
BODY = b'{"event_id":"evt_01","event_type":"subscription.updated","data":{"id":"sub_01"}}'
NOW = 1_700_000_000

STARTER_PRICE = "pri_starter_01"
PROFESSIONAL_PRICE = "pri_professional_01"
ENTERPRISE_PRICE = "pri_enterprise_01"


def _sign(body: bytes, ts: int, secret: str = SECRET) -> str:
    """Compute the h1 signature exactly as Paddle documents it."""
    return hmac.new(secret.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()


@pytest.fixture
def paddle_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the three price ids + api key on the live settings object."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_test_api_key")
    monkeypatch.setattr(settings, "paddle_client_token", "test_client_token")
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "paddle_environment", "sandbox")
    monkeypatch.setattr(settings, "paddle_starter_price_id", STARTER_PRICE)
    monkeypatch.setattr(settings, "paddle_professional_price_id", PROFESSIONAL_PRICE)
    monkeypatch.setattr(settings, "paddle_enterprise_price_id", ENTERPRISE_PRICE)
    reset_paddle_client()
    yield
    reset_paddle_client()


@pytest.fixture
def unconfigured_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every Paddle setting."""
    for name in (
        "paddle_api_key",
        "paddle_client_token",
        "paddle_webhook_secret",
        "paddle_starter_price_id",
        "paddle_professional_price_id",
        "paddle_enterprise_price_id",
    ):
        monkeypatch.setattr(settings, name, None)
    monkeypatch.setattr(settings, "paddle_environment", "sandbox")
    reset_paddle_client()
    yield
    reset_paddle_client()


# =============================================================================
# Signature header parsing + verification
# =============================================================================


class TestParseSignatureHeader:
    def test_single_signature(self) -> None:
        ts, sigs = parse_signature_header(f"ts={NOW};h1=abc123")
        assert ts == NOW
        assert sigs == ["abc123"]

    def test_multiple_signatures_and_whitespace(self) -> None:
        ts, sigs = parse_signature_header(f" ts={NOW}; h1=AAA ; h1=bbb ")
        assert ts == NOW
        assert sigs == ["aaa", "bbb"]

    def test_unknown_keys_are_ignored(self) -> None:
        ts, sigs = parse_signature_header(f"ts={NOW};h1=abc;h2=future")
        assert (ts, sigs) == (NOW, ["abc"])

    @pytest.mark.parametrize(
        "header",
        ["", "   ", "garbage", "ts=abc;h1=deadbeef", f"ts={NOW}", "h1=deadbeef", "ts=;h1=x", "=1;h1=x"],
    )
    def test_malformed_headers_raise(self, header: str) -> None:
        with pytest.raises(PaddleSignatureError):
            parse_signature_header(header)


class TestVerifyWebhookSignature:
    def test_valid_signature(self) -> None:
        header = f"ts={NOW};h1={_sign(BODY, NOW)}"
        verify_webhook_signature(BODY, header, SECRET, now=NOW)

    def test_valid_signature_within_tolerance(self) -> None:
        header = f"ts={NOW};h1={_sign(BODY, NOW)}"
        verify_webhook_signature(BODY, header, SECRET, tolerance_seconds=300, now=NOW + 300)
        verify_webhook_signature(BODY, header, SECRET, tolerance_seconds=300, now=NOW - 300)

    def test_one_of_multiple_signatures_matches(self) -> None:
        header = f"ts={NOW};h1={'0' * 64};h1={_sign(BODY, NOW)}"
        verify_webhook_signature(BODY, header, SECRET, now=NOW)

    def test_bad_signature(self) -> None:
        header = f"ts={NOW};h1={_sign(BODY, NOW, secret='wrong-secret')}"
        with pytest.raises(PaddleSignatureError, match="does not match"):
            verify_webhook_signature(BODY, header, SECRET, now=NOW)

    def test_tampered_body(self) -> None:
        header = f"ts={NOW};h1={_sign(BODY, NOW)}"
        with pytest.raises(PaddleSignatureError):
            verify_webhook_signature(BODY + b" ", header, SECRET, now=NOW)

    def test_stale_timestamp(self) -> None:
        header = f"ts={NOW};h1={_sign(BODY, NOW)}"
        with pytest.raises(PaddleSignatureError, match="tolerance"):
            verify_webhook_signature(BODY, header, SECRET, tolerance_seconds=300, now=NOW + 301)

    def test_future_timestamp_is_also_rejected(self) -> None:
        header = f"ts={NOW + 1000};h1={_sign(BODY, NOW + 1000)}"
        with pytest.raises(PaddleSignatureError):
            verify_webhook_signature(BODY, header, SECRET, now=NOW)

    def test_missing_header(self) -> None:
        with pytest.raises(PaddleSignatureError, match="Missing"):
            verify_webhook_signature(BODY, None, SECRET, now=NOW)

    def test_malformed_header(self) -> None:
        with pytest.raises(PaddleSignatureError):
            verify_webhook_signature(BODY, "not-a-signature", SECRET, now=NOW)

    def test_empty_secret(self) -> None:
        header = f"ts={NOW};h1={_sign(BODY, NOW)}"
        with pytest.raises(PaddleSignatureError):
            verify_webhook_signature(BODY, header, "", now=NOW)

    def test_uses_current_time_by_default(self) -> None:
        ts = int(datetime.now(UTC).timestamp())
        header = f"ts={ts};h1={_sign(BODY, ts)}"
        verify_webhook_signature(BODY, header, SECRET)


# =============================================================================
# Tier <-> price mapping
# =============================================================================


class TestTierPriceMapping:
    def test_price_id_for_each_tier(self, paddle_settings: None) -> None:
        assert get_price_id_for_tier(SubscriptionTier.STARTER) == STARTER_PRICE
        assert get_price_id_for_tier(SubscriptionTier.PROFESSIONAL) == PROFESSIONAL_PRICE
        assert get_price_id_for_tier(SubscriptionTier.ENTERPRISE) == ENTERPRISE_PRICE

    def test_tier_for_each_price_id(self, paddle_settings: None) -> None:
        assert get_tier_for_price_id(STARTER_PRICE) == SubscriptionTier.STARTER
        assert get_tier_for_price_id(PROFESSIONAL_PRICE) == SubscriptionTier.PROFESSIONAL
        assert get_tier_for_price_id(ENTERPRISE_PRICE) == SubscriptionTier.ENTERPRISE

    def test_unknown_or_empty_price_id(self, paddle_settings: None) -> None:
        assert get_tier_for_price_id("pri_unknown") is None
        assert get_tier_for_price_id(None) is None
        assert get_tier_for_price_id("") is None

    def test_unconfigured_prices_never_match(self, unconfigured_settings: None) -> None:
        assert get_price_id_for_tier(SubscriptionTier.STARTER) is None
        assert get_tier_for_price_id(None) is None
        assert get_tier_for_price_id("pri_anything") is None

    def test_is_configured_reflects_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "paddle_api_key", None)
        assert is_configured() is False
        monkeypatch.setattr(settings, "paddle_api_key", "pdl_key")
        assert is_configured() is True


# =============================================================================
# Payload parsing
# =============================================================================


class TestParsePaddleDatetime:
    def test_zulu(self) -> None:
        parsed = parse_paddle_datetime("2024-05-01T12:30:45Z")
        assert parsed == datetime(2024, 5, 1, 12, 30, 45, tzinfo=UTC)
        assert parsed.tzinfo is not None

    def test_fractional_seconds(self) -> None:
        parsed = parse_paddle_datetime("2024-05-01T12:30:45.123456Z")
        assert parsed == datetime(2024, 5, 1, 12, 30, 45, 123456, tzinfo=UTC)

    def test_offset_is_normalized_to_utc(self) -> None:
        parsed = parse_paddle_datetime("2024-05-01T14:30:45+02:00")
        assert parsed == datetime(2024, 5, 1, 12, 30, 45, tzinfo=UTC)

    @pytest.mark.parametrize("value", [None, "", "   ", "not-a-date"])
    def test_none_empty_and_garbage(self, value: str | None) -> None:
        assert parse_paddle_datetime(value) is None


def _subscription_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "sub_01h",
        "status": "active",
        "customer_id": "ctm_01h",
        "currency_code": "USD",
        "created_at": "2024-01-01T00:00:00Z",
        "next_billed_at": "2024-06-01T00:00:00Z",
        "paused_at": None,
        "canceled_at": None,
        "current_billing_period": {
            "starts_at": "2024-05-01T00:00:00Z",
            "ends_at": "2024-06-01T00:00:00Z",
        },
        "scheduled_change": {"action": "cancel", "effective_at": "2024-06-01T00:00:00Z"},
        "custom_data": {"tenant_id": "42", "tier": "professional"},
        "items": [
            {
                "status": "active",
                "quantity": 1,
                "price": {"id": PROFESSIONAL_PRICE, "product_id": "pro_01"},
                "trial_dates": {"starts_at": "2024-04-17T00:00:00Z", "ends_at": "2024-05-01T00:00:00Z"},
            }
        ],
    }
    payload.update(overrides)
    return payload


class TestSubscriptionFromPayload:
    def test_representative_payload(self, paddle_settings: None) -> None:
        sub = subscription_from_payload(_subscription_payload())

        assert sub.id == "sub_01h"
        assert sub.customer_id == "ctm_01h"
        assert sub.status is SubscriptionState.ACTIVE
        assert sub.tier is SubscriptionTier.PROFESSIONAL
        assert sub.price_id == PROFESSIONAL_PRICE
        assert sub.current_period_start == datetime(2024, 5, 1, tzinfo=UTC)
        assert sub.current_period_end == datetime(2024, 6, 1, tzinfo=UTC)
        assert sub.next_billed_at == datetime(2024, 6, 1, tzinfo=UTC)
        assert sub.cancel_at_period_end is True
        assert sub.canceled_at is None
        assert sub.paused_at is None
        assert sub.trial_end == datetime(2024, 5, 1, tzinfo=UTC)
        assert sub.custom_data == {"tenant_id": "42", "tier": "professional"}

    def test_no_scheduled_change(self, paddle_settings: None) -> None:
        sub = subscription_from_payload(_subscription_payload(scheduled_change=None))
        assert sub.cancel_at_period_end is False

    def test_pause_scheduled_change_is_not_cancel(self, paddle_settings: None) -> None:
        sub = subscription_from_payload(
            _subscription_payload(scheduled_change={"action": "pause"})
        )
        assert sub.cancel_at_period_end is False

    def test_unknown_price_id_gives_no_tier(self, paddle_settings: None) -> None:
        payload = _subscription_payload(items=[{"price": {"id": "pri_unknown"}}])
        sub = subscription_from_payload(payload)
        assert sub.tier is None
        assert sub.price_id == "pri_unknown"

    def test_missing_items(self, paddle_settings: None) -> None:
        sub = subscription_from_payload(_subscription_payload(items=[]))
        assert sub.tier is None
        assert sub.price_id is None
        assert sub.trial_end is None

    def test_unknown_status_defaults_to_active(self, paddle_settings: None) -> None:
        sub = subscription_from_payload(_subscription_payload(status="something_new"))
        assert sub.status is SubscriptionState.ACTIVE

    @pytest.mark.parametrize(
        "status", ["active", "trialing", "past_due", "paused", "canceled"]
    )
    def test_every_known_status(self, paddle_settings: None, status: str) -> None:
        sub = subscription_from_payload(_subscription_payload(status=status))
        assert sub.status.value == status

    def test_canceled_payload(self, paddle_settings: None) -> None:
        sub = subscription_from_payload(
            _subscription_payload(
                status="canceled",
                canceled_at="2024-05-15T10:00:00Z",
                scheduled_change=None,
                current_billing_period=None,
            )
        )
        assert sub.status is SubscriptionState.CANCELED
        assert sub.canceled_at == datetime(2024, 5, 15, 10, tzinfo=UTC)
        assert sub.current_period_end is None


def _transaction_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": "txn_01h",
        "status": "completed",
        "customer_id": "ctm_01h",
        "subscription_id": "sub_01h",
        "invoice_number": "325-10001",
        "currency_code": "USD",
        "billed_at": "2024-05-01T00:00:01Z",
        "created_at": "2024-05-01T00:00:00Z",
        "custom_data": {"tenant_id": "42"},
        "details": {
            "totals": {
                "subtotal": "4900",
                "tax": "0",
                "total": "4900",
                "grand_total": "4900",
                "currency_code": "USD",
            }
        },
    }
    payload.update(overrides)
    return payload


class TestTransactionFromPayload:
    def test_representative_payload(self) -> None:
        txn = transaction_from_payload(_transaction_payload())
        assert txn.id == "txn_01h"
        assert txn.invoice_number == "325-10001"
        assert txn.status == "completed"
        assert txn.subscription_id == "sub_01h"
        assert txn.customer_id == "ctm_01h"
        assert txn.total_minor == 4900
        assert isinstance(txn.total_minor, int)
        assert txn.currency_code == "USD"
        assert txn.billed_at == datetime(2024, 5, 1, 0, 0, 1, tzinfo=UTC)
        assert txn.created_at == datetime(2024, 5, 1, tzinfo=UTC)
        assert txn.custom_data == {"tenant_id": "42"}

    def test_falls_back_to_total_then_zero(self) -> None:
        txn = transaction_from_payload(
            _transaction_payload(details={"totals": {"total": "1250"}})
        )
        assert txn.total_minor == 1250
        txn = transaction_from_payload(_transaction_payload(details={}))
        assert txn.total_minor == 0

    def test_optional_fields_absent(self) -> None:
        txn = transaction_from_payload(
            _transaction_payload(invoice_number=None, subscription_id=None, billed_at=None)
        )
        assert txn.invoice_number is None
        assert txn.subscription_id is None
        assert txn.billed_at is None


# =============================================================================
# PaddleClient
# =============================================================================


class _Recorder:
    """MockTransport handler that records every request and delegates the response."""

    def __init__(self, responder: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


def _mock_client(
    responder: Callable[[httpx.Request], httpx.Response],
    environment: str = "sandbox",
) -> tuple[PaddleClient, _Recorder]:
    recorder = _Recorder(responder)
    client = PaddleClient(
        api_key="pdl_test_api_key",
        environment=environment,
        transport=httpx.MockTransport(recorder),
    )
    return client, recorder


def _json_body(request: httpx.Request) -> Any:
    return json.loads(request.content.decode()) if request.content else None


def _ok(data: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _request: httpx.Response(200, json={"data": data, "meta": {"request_id": "req_1"}})


class TestPaddleClientNotConfigured:
    async def test_first_request_raises_not_configured(self, unconfigured_settings: None) -> None:
        client = PaddleClient()
        assert client.is_configured() is False
        assert is_configured() is False
        with pytest.raises(PaddleNotConfiguredError) as excinfo:
            await client.get_subscription("sub_01")
        assert excinfo.value.status_code == 503
        assert isinstance(excinfo.value, PaddleError)

    def test_singleton_does_not_raise_on_construction(self, unconfigured_settings: None) -> None:
        client = get_paddle_client()
        assert isinstance(client, PaddleClient)
        assert get_paddle_client() is client
        reset_paddle_client()
        assert get_paddle_client() is not client

    def test_singleton_picks_up_settings(self, paddle_settings: None) -> None:
        client = get_paddle_client()
        assert client.is_configured() is True
        assert client.environment == "sandbox"
        assert client.base_url == "https://sandbox-api.paddle.com"

    def test_base_url_by_environment(self) -> None:
        assert PaddleClient(api_key="k", environment="sandbox").base_url == "https://sandbox-api.paddle.com"
        assert PaddleClient(api_key="k", environment="production").base_url == "https://api.paddle.com"


class TestPaddleClientRequests:
    async def test_headers_and_base_url(self) -> None:
        client, rec = _mock_client(_ok(_subscription_payload()), environment="production")
        await client.get_subscription("sub_01h")
        request = rec.last
        assert request.method == "GET"
        assert str(request.url) == "https://api.paddle.com/subscriptions/sub_01h"
        assert request.headers["Authorization"] == "Bearer pdl_test_api_key"
        assert request.headers["Paddle-Version"] == "1"
        assert request.headers["Content-Type"] == "application/json"
        await client.aclose()

    async def test_get_subscription_maps_payload(self, paddle_settings: None) -> None:
        client, _ = _mock_client(_ok(_subscription_payload()))
        sub = await client.get_subscription("sub_01h")
        assert isinstance(sub, PaddleSubscription)
        assert sub.tier is SubscriptionTier.PROFESSIONAL
        assert sub.cancel_at_period_end is True

    async def test_create_customer(self) -> None:
        client, rec = _mock_client(
            _ok({"id": "ctm_new", "email": "owner@acme.test", "name": "Acme", "custom_data": {"tenant_id": "7"}})
        )
        customer = await client.create_customer("owner@acme.test", "Acme", tenant_id=7)
        assert customer.id == "ctm_new"
        assert customer.email == "owner@acme.test"
        assert customer.custom_data == {"tenant_id": "7"}
        request = rec.last
        assert request.method == "POST"
        assert request.url.path == "/customers"
        assert _json_body(request) == {
            "email": "owner@acme.test",
            "name": "Acme",
            "custom_data": {"tenant_id": "7"},
        }

    async def test_create_customer_conflict_falls_back_to_lookup(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(
                    409,
                    json={
                        "error": {
                            "type": "request_error",
                            "code": "customer_already_exists",
                            "detail": "customer email conflicts with customer of id ctm_existing",
                        }
                    },
                )
            return httpx.Response(
                200,
                json={"data": [{"id": "ctm_existing", "email": "owner@acme.test", "name": None, "custom_data": None}]},
            )

        client, rec = _mock_client(responder)
        customer = await client.create_customer("owner@acme.test", None, tenant_id=7)
        assert customer.id == "ctm_existing"
        assert customer.custom_data == {}
        lookup = rec.last
        assert lookup.method == "GET"
        assert lookup.url.path == "/customers"
        assert lookup.url.params["email"] == "owner@acme.test"
        assert lookup.url.params["status"] == "active"
        assert "name" not in (_json_body(rec.requests[0]) or {})

    async def test_create_customer_conflict_without_match_reraises(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                return httpx.Response(409, json={"error": {"code": "customer_already_exists", "detail": "dup"}})
            return httpx.Response(200, json={"data": []})

        client, _ = _mock_client(responder)
        with pytest.raises(PaddleError) as excinfo:
            await client.create_customer("owner@acme.test", None, tenant_id=7)
        assert excinfo.value.status_code == 409
        assert excinfo.value.code == "customer_already_exists"

    async def test_get_customer_404_returns_none(self) -> None:
        client, _ = _mock_client(
            lambda _r: httpx.Response(404, json={"error": {"code": "entity_not_found", "detail": "nope"}})
        )
        assert await client.get_customer("ctm_missing") is None

    async def test_list_subscriptions_params(self, paddle_settings: None) -> None:
        client, rec = _mock_client(_ok([_subscription_payload(), _subscription_payload(id="sub_02")]))
        subs = await client.list_subscriptions("ctm_01h", statuses=["active", "trialing", "past_due"])
        assert [s.id for s in subs] == ["sub_01h", "sub_02"]
        params = rec.last.url.params
        assert rec.last.url.path == "/subscriptions"
        assert params["customer_id"] == "ctm_01h"
        assert params["status"] == "active,trialing,past_due"
        assert params["per_page"] == "10"

    async def test_list_subscriptions_without_status_filter(self, paddle_settings: None) -> None:
        client, rec = _mock_client(_ok([]))
        assert await client.list_subscriptions("ctm_01h") == []
        assert "status" not in rec.last.url.params

    async def test_update_subscription_tier(self, paddle_settings: None) -> None:
        client, rec = _mock_client(
            _ok(_subscription_payload(items=[{"price": {"id": ENTERPRISE_PRICE}}], scheduled_change=None))
        )
        sub = await client.update_subscription_tier("sub_01h", SubscriptionTier.ENTERPRISE, prorate=True)
        assert sub.tier is SubscriptionTier.ENTERPRISE
        request = rec.last
        assert request.method == "PATCH"
        assert request.url.path == "/subscriptions/sub_01h"
        assert _json_body(request) == {
            "items": [{"price_id": ENTERPRISE_PRICE, "quantity": 1}],
            "proration_billing_mode": "prorated_immediately",
            "custom_data": {"tier": "enterprise"},
        }

    async def test_update_subscription_tier_without_proration(self, paddle_settings: None) -> None:
        client, rec = _mock_client(_ok(_subscription_payload()))
        await client.update_subscription_tier("sub_01h", SubscriptionTier.STARTER, prorate=False)
        assert _json_body(rec.last)["proration_billing_mode"] == "full_next_billing_period"

    async def test_update_subscription_tier_unconfigured_price(self, unconfigured_settings: None) -> None:
        client, rec = _mock_client(_ok(_subscription_payload()))
        with pytest.raises(PaddleNotConfiguredError):
            await client.update_subscription_tier("sub_01h", SubscriptionTier.STARTER)
        assert rec.requests == []

    async def test_cancel_subscription(self) -> None:
        client, rec = _mock_client(_ok(_subscription_payload()))
        await client.cancel_subscription("sub_01h", at_period_end=True)
        assert rec.last.method == "POST"
        assert rec.last.url.path == "/subscriptions/sub_01h/cancel"
        assert _json_body(rec.last) == {"effective_from": "next_billing_period"}

        await client.cancel_subscription("sub_01h", at_period_end=False)
        assert _json_body(rec.last) == {"effective_from": "immediately"}

    async def test_reactivate_subscription_clears_scheduled_change(self) -> None:
        client, rec = _mock_client(_ok(_subscription_payload(scheduled_change=None)))
        sub = await client.reactivate_subscription("sub_01h")
        assert sub.cancel_at_period_end is False
        assert rec.last.method == "PATCH"
        assert rec.last.url.path == "/subscriptions/sub_01h"
        assert _json_body(rec.last) == {"scheduled_change": None}

    async def test_list_transactions_params_and_cap(self) -> None:
        client, rec = _mock_client(_ok([_transaction_payload()]))
        txns = await client.list_transactions("ctm_01h", subscription_id="sub_01h", limit=500)
        assert len(txns) == 1
        assert txns[0].total_minor == 4900
        params = rec.last.url.params
        assert rec.last.url.path == "/transactions"
        assert params["customer_id"] == "ctm_01h"
        assert params["subscription_id"] == "sub_01h"
        assert params["status"] == "completed,billed,past_due,paid"
        assert params["per_page"] == "100"
        assert params["order_by"] == "created_at[DESC]"

    async def test_list_transactions_default_limit(self) -> None:
        client, rec = _mock_client(_ok([]))
        await client.list_transactions("ctm_01h")
        assert rec.last.url.params["per_page"] == "10"
        assert "subscription_id" not in rec.last.url.params

    async def test_get_transaction(self) -> None:
        client, rec = _mock_client(_ok(_transaction_payload()))
        txn = await client.get_transaction("txn_01h")
        assert txn.id == "txn_01h"
        assert rec.last.url.path == "/transactions/txn_01h"

    async def test_get_transaction_invoice_url(self) -> None:
        client, rec = _mock_client(_ok({"url": "https://sandbox-invoices.paddle.com/inv.pdf"}))
        assert await client.get_transaction_invoice_url("txn_01h") == "https://sandbox-invoices.paddle.com/inv.pdf"
        assert rec.last.url.path == "/transactions/txn_01h/invoice"

    async def test_get_transaction_invoice_url_missing(self) -> None:
        client, _ = _mock_client(_ok({}))
        with pytest.raises(PaddleError) as excinfo:
            await client.get_transaction_invoice_url("txn_01h")
        assert excinfo.value.code == "invoice_url_missing"

    async def test_create_portal_session(self) -> None:
        client, rec = _mock_client(
            _ok(
                {
                    "id": "cpls_01",
                    "customer_id": "ctm_01h",
                    "urls": {
                        "general": {"overview": "https://customer-portal.paddle.com/cpl_01"},
                        "subscriptions": [
                            {
                                "id": "sub_01h",
                                "cancel_subscription": "https://customer-portal.paddle.com/cpl_01/cancel",
                                "update_subscription_payment_method": "https://customer-portal.paddle.com/cpl_01/pm",
                            }
                        ],
                    },
                }
            )
        )
        session = await client.create_portal_session("ctm_01h", subscription_ids=["sub_01h"])
        assert session == PortalSession(
            overview_url="https://customer-portal.paddle.com/cpl_01",
            cancel_url="https://customer-portal.paddle.com/cpl_01/cancel",
            update_payment_method_url="https://customer-portal.paddle.com/cpl_01/pm",
        )
        assert rec.last.method == "POST"
        assert rec.last.url.path == "/customers/ctm_01h/portal-sessions"
        assert _json_body(rec.last) == {"subscription_ids": ["sub_01h"]}

    async def test_create_portal_session_without_subscriptions(self) -> None:
        client, rec = _mock_client(_ok({"urls": {"general": {"overview": "https://portal/x"}, "subscriptions": []}}))
        session = await client.create_portal_session("ctm_01h")
        assert session.overview_url == "https://portal/x"
        assert session.cancel_url is None
        assert session.update_payment_method_url is None
        assert _json_body(rec.last) == {}

    async def test_list_prices(self) -> None:
        client, rec = _mock_client(_ok([{"id": STARTER_PRICE, "status": "active"}]))
        prices = await client.list_prices()
        assert prices == [{"id": STARTER_PRICE, "status": "active"}]
        assert rec.last.url.path == "/prices"
        assert rec.last.url.params["status"] == "active"


class TestPaddleClientErrors:
    async def test_paddle_error_from_error_body(self) -> None:
        client, _ = _mock_client(
            lambda _r: httpx.Response(
                400,
                json={
                    "error": {
                        "type": "request_error",
                        "code": "bad_request",
                        "detail": "Invalid request",
                        "errors": [{"field": "items", "message": "is required"}],
                    }
                },
            )
        )
        with pytest.raises(PaddleError) as excinfo:
            await client.get_subscription("sub_01h")
        err = excinfo.value
        assert err.status_code == 400
        assert err.code == "bad_request"
        assert "Invalid request" in err.detail
        assert "items: is required" in err.detail
        assert "400" in str(err)

    async def test_paddle_error_from_non_json_body(self) -> None:
        client, _ = _mock_client(lambda _r: httpx.Response(500, text="upstream exploded"))
        with pytest.raises(PaddleError) as excinfo:
            await client.get_subscription("sub_01h")
        assert excinfo.value.status_code == 500
        assert excinfo.value.code is None
        assert "500" in excinfo.value.detail

    async def test_transport_failure_becomes_502(self) -> None:
        def responder(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client, _ = _mock_client(responder)
        with pytest.raises(PaddleError) as excinfo:
            await client.get_subscription("sub_01h")
        assert excinfo.value.status_code == 502
        assert excinfo.value.code == "paddle_unreachable"

    async def test_non_json_success_body_becomes_502(self) -> None:
        client, _ = _mock_client(lambda _r: httpx.Response(200, text="<html>"))
        with pytest.raises(PaddleError) as excinfo:
            await client.get_subscription("sub_01h")
        assert excinfo.value.code == "paddle_invalid_response"


# =============================================================================
# Tenant sync rules
# =============================================================================

PERIOD_END = datetime(2024, 6, 1, tzinfo=UTC)
CANCELED_AT = datetime(2024, 5, 15, 10, tzinfo=UTC)


def _sub(**overrides: Any) -> PaddleSubscription:
    values: dict[str, Any] = {
        "id": "sub_01h",
        "customer_id": "ctm_01h",
        "status": SubscriptionState.ACTIVE,
        "tier": SubscriptionTier.PROFESSIONAL,
        "price_id": PROFESSIONAL_PRICE,
        "current_period_start": datetime(2024, 5, 1, tzinfo=UTC),
        "current_period_end": PERIOD_END,
        "next_billed_at": PERIOD_END,
        "cancel_at_period_end": False,
        "canceled_at": None,
        "paused_at": None,
        "trial_end": None,
        "custom_data": {"tenant_id": "42"},
    }
    values.update(overrides)
    return PaddleSubscription(**values)


class TestComputeTenantBillingUpdates:
    def test_active_with_known_tier(self) -> None:
        updates = compute_tenant_billing_updates(_sub())
        assert updates == {
            "paddle_subscription_id": "sub_01h",
            "subscription_status": "active",
            "current_period_end": PERIOD_END,
            "plan": "professional",
            "plan_expires_at": PERIOD_END,
        }

    @pytest.mark.parametrize("status", [SubscriptionState.TRIALING, SubscriptionState.PAST_DUE])
    def test_trialing_and_past_due_keep_tier(self, status: SubscriptionState) -> None:
        updates = compute_tenant_billing_updates(_sub(status=status, tier=SubscriptionTier.STARTER))
        assert updates["plan"] == "starter"
        assert updates["plan_expires_at"] == PERIOD_END
        assert updates["subscription_status"] == status.value

    def test_active_with_unknown_price_leaves_plan_untouched(self) -> None:
        updates = compute_tenant_billing_updates(_sub(tier=None, price_id="pri_unknown"))
        assert "plan" not in updates
        assert updates["plan_expires_at"] == PERIOD_END
        assert updates["paddle_subscription_id"] == "sub_01h"

    def test_paused_with_period_end(self) -> None:
        updates = compute_tenant_billing_updates(
            _sub(status=SubscriptionState.PAUSED, paused_at=CANCELED_AT)
        )
        assert "plan" not in updates
        assert updates["plan_expires_at"] == PERIOD_END
        assert updates["subscription_status"] == "paused"

    def test_paused_without_period_end(self) -> None:
        updates = compute_tenant_billing_updates(
            _sub(status=SubscriptionState.PAUSED, current_period_end=None)
        )
        assert "plan" not in updates
        assert "plan_expires_at" not in updates
        assert updates["current_period_end"] is None

    def test_canceled_uses_canceled_at(self) -> None:
        updates = compute_tenant_billing_updates(
            _sub(status=SubscriptionState.CANCELED, canceled_at=CANCELED_AT)
        )
        assert updates["plan"] == "free"
        assert updates["plan_expires_at"] == CANCELED_AT
        assert updates["subscription_status"] == "canceled"

    def test_canceled_falls_back_to_period_end(self) -> None:
        updates = compute_tenant_billing_updates(_sub(status=SubscriptionState.CANCELED))
        assert updates["plan"] == "free"
        assert updates["plan_expires_at"] == PERIOD_END

    def test_canceled_falls_back_to_now(self) -> None:
        now = datetime(2024, 7, 4, tzinfo=UTC)
        updates = compute_tenant_billing_updates(
            _sub(status=SubscriptionState.CANCELED, current_period_end=None), now=now
        )
        assert updates["plan_expires_at"] == now

        before = datetime.now(UTC)
        updates = compute_tenant_billing_updates(
            _sub(status=SubscriptionState.CANCELED, current_period_end=None)
        )
        assert before <= updates["plan_expires_at"] <= datetime.now(UTC) + timedelta(seconds=5)


def _compiled(db: AsyncMock) -> tuple[str, dict[str, Any]]:
    """Return (SQL, params) of the single statement passed to ``db.execute``."""
    db.execute.assert_awaited_once()
    statement = db.execute.await_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    return str(compiled), dict(compiled.params)


class TestTenantSyncDb:
    async def test_sync_tenant_subscription_updates_and_commits(self) -> None:
        db = AsyncMock()
        await sync_tenant_subscription(db, tenant_id=42, subscription=_sub())

        sql, params = _compiled(db)
        db.commit.assert_awaited_once()
        assert sql.startswith("UPDATE tenants SET")
        for column in (
            "paddle_subscription_id",
            "subscription_status",
            "current_period_end",
            "plan",
            "plan_expires_at",
            "paddle_customer_id",
        ):
            assert f"{column}=" in sql, sql
        # Customer id is linked only when the tenant has none yet.
        assert "coalesce(nullif(tenants.paddle_customer_id" in sql
        assert "WHERE tenants.id =" in sql
        assert params["paddle_subscription_id"] == "sub_01h"
        assert params["subscription_status"] == "active"
        assert params["plan"] == "professional"
        assert params["plan_expires_at"] == PERIOD_END
        assert "ctm_01h" in params.values()
        assert 42 in params.values()

    async def test_sync_tenant_subscription_unknown_tier_never_touches_plan(self) -> None:
        db = AsyncMock()
        await sync_tenant_subscription(db, 42, _sub(tier=None, price_id="pri_unknown"))
        sql, params = _compiled(db)
        assert "plan=" not in sql.replace("plan_expires_at=", "")
        assert "plan" not in params
        db.commit.assert_awaited_once()

    async def test_sync_tenant_subscription_canceled_downgrades_to_free(self) -> None:
        db = AsyncMock()
        await sync_tenant_subscription(
            db, 42, _sub(status=SubscriptionState.CANCELED, canceled_at=CANCELED_AT)
        )
        _, params = _compiled(db)
        assert params["plan"] == "free"
        assert params["plan_expires_at"] == CANCELED_AT
        assert params["subscription_status"] == "canceled"

    async def test_sync_tenant_subscription_without_customer_id(self) -> None:
        db = AsyncMock()
        await sync_tenant_subscription(db, 42, _sub(customer_id=""))
        sql, _ = _compiled(db)
        assert "paddle_customer_id" not in sql

    async def test_sync_tenant_paddle_customer(self) -> None:
        db = AsyncMock()
        await sync_tenant_paddle_customer(db, tenant_id=42, customer_id="ctm_01h")
        sql, params = _compiled(db)
        db.commit.assert_awaited_once()
        assert sql.startswith("UPDATE tenants SET paddle_customer_id=")
        assert params["paddle_customer_id"] == "ctm_01h"
        assert 42 in params.values()

    async def test_clear_tenant_subscription_with_ended_at(self) -> None:
        db = AsyncMock()
        await clear_tenant_subscription(db, tenant_id=42, ended_at=CANCELED_AT)
        sql, params = _compiled(db)
        db.commit.assert_awaited_once()
        assert sql.startswith("UPDATE tenants SET")
        assert params["plan"] == "free"
        assert params["plan_expires_at"] == CANCELED_AT
        assert params["subscription_status"] == "canceled"
        assert "paddle_subscription_id" not in sql  # Paddle ids are kept for audit

    async def test_clear_tenant_subscription_defaults_to_now(self) -> None:
        db = AsyncMock()
        before = datetime.now(UTC)
        await clear_tenant_subscription(db, tenant_id=42, ended_at=None)
        _, params = _compiled(db)
        assert before <= params["plan_expires_at"] <= datetime.now(UTC) + timedelta(seconds=5)


# =============================================================================
# Module hygiene
# =============================================================================


class TestModuleSurface:
    def test_no_sdk_import(self) -> None:
        """The client is a thin httpx wrapper - the Paddle SDK is never imported."""
        import sys

        assert "paddle_billing" not in sys.modules
        assert not hasattr(paddle_service, "paddle_billing")

    def test_public_names_exist(self) -> None:
        for name in (
            "PaddleError",
            "PaddleNotConfiguredError",
            "PaddleSignatureError",
            "SubscriptionState",
            "PaddleCustomer",
            "PaddleSubscription",
            "PaddleTransaction",
            "PortalSession",
            "is_configured",
            "get_price_id_for_tier",
            "get_tier_for_price_id",
            "parse_paddle_datetime",
            "subscription_from_payload",
            "transaction_from_payload",
            "parse_signature_header",
            "verify_webhook_signature",
            "PaddleClient",
            "get_paddle_client",
            "reset_paddle_client",
            "sync_tenant_subscription",
            "sync_tenant_paddle_customer",
            "clear_tenant_subscription",
        ):
            assert hasattr(paddle_service, name), name

    def test_subscription_state_values(self) -> None:
        assert [s.value for s in SubscriptionState] == [
            "active",
            "trialing",
            "past_due",
            "paused",
            "canceled",
        ]
