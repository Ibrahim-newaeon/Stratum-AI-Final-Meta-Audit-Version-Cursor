# =============================================================================
# Stratum AI - Paddle Webhook Endpoint Tests
# =============================================================================
"""
Unit tests for ``app.api.v1.endpoints.paddle_webhook``.

No network, no database: the router is mounted on a minimal FastAPI app,
``async_session_maker`` is replaced by an in-memory fake session and the tenant
sync helpers in ``app.services.paddle_service`` are ``AsyncMock``s. Signatures
are computed in the tests exactly like Paddle does (HMAC-SHA256 over
``"<ts>:" + body``) so the real verifier runs.
"""

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Optional, Self
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.api.v1.endpoints import paddle_webhook
from app.base_models import PaddleWebhookEvent, Tenant, User, UserRole
from app.core.config import settings
from app.core.tiers import SubscriptionTier
from app.services import paddle_service
from app.services.paddle_service import PaddleSubscription, SubscriptionState

pytestmark = pytest.mark.unit

SECRET = "whsec-test"
WEBHOOK_URL = "/api/v1/webhooks/paddle"
TENANT_ID = 42
CUSTOMER_ID = "ctm_01hv8paddlecustomer"
SUBSCRIPTION_ID = "sub_01hv8paddlesubscription"
TRANSACTION_ID = "txn_01hv8paddletransaction"
PRICE_ID = "pri_01hv8starterprice"
PERIOD_START = "2026-09-01T10:00:00.000000Z"
PERIOD_END = "2026-10-01T10:00:00.000000Z"


# =============================================================================
# Helpers
# =============================================================================


def _sign(body: bytes, ts: Optional[int] = None, secret: str = SECRET) -> str:
    """Build a ``Paddle-Signature`` header for ``body``."""
    ts = int(time.time()) if ts is None else ts
    digest = hmac.new(secret.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={digest}"


def _envelope(event_type: str, data: dict[str, Any], event_id: str = "evt_01hv8event") -> bytes:
    """Serialize a Paddle notification envelope."""
    return json.dumps(
        {
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": "2026-09-01T10:00:05.000000Z",
            "notification_id": "ntf_01hv8notification",
            "data": data,
        }
    ).encode("utf-8")


def _subscription_data(**overrides: Any) -> dict[str, Any]:
    """A Paddle subscription entity as delivered on ``subscription.*`` events."""
    data: dict[str, Any] = {
        "id": SUBSCRIPTION_ID,
        "status": "active",
        "customer_id": CUSTOMER_ID,
        "address_id": "add_01hv8address",
        "business_id": None,
        "currency_code": "USD",
        "created_at": "2026-09-01T09:59:00.000000Z",
        "updated_at": "2026-09-01T10:00:00.000000Z",
        "started_at": PERIOD_START,
        "first_billed_at": PERIOD_START,
        "next_billed_at": PERIOD_END,
        "paused_at": None,
        "canceled_at": None,
        "collection_mode": "automatic",
        "billing_cycle": {"interval": "month", "frequency": 1},
        "scheduled_change": None,
        "current_billing_period": {"starts_at": PERIOD_START, "ends_at": PERIOD_END},
        "items": [
            {
                "status": "active",
                "quantity": 1,
                "recurring": True,
                "price": {"id": PRICE_ID, "product_id": "pro_01hv8product", "name": "Starter"},
                "product": {"id": "pro_01hv8product", "name": "Starter"},
                "trial_dates": None,
            }
        ],
        "custom_data": {"tenant_id": str(TENANT_ID), "tier": "starter"},
    }
    data.update(overrides)
    return data


def _transaction_data(**overrides: Any) -> dict[str, Any]:
    """A Paddle transaction entity as delivered on ``transaction.*`` events."""
    data: dict[str, Any] = {
        "id": TRANSACTION_ID,
        "status": "completed",
        "customer_id": CUSTOMER_ID,
        "address_id": "add_01hv8address",
        "business_id": None,
        "subscription_id": SUBSCRIPTION_ID,
        "invoice_id": "inv_01hv8invoice",
        "invoice_number": "INV-0001",
        "origin": "subscription_recurring",
        "currency_code": "USD",
        "collection_mode": "automatic",
        "billed_at": PERIOD_START,
        "created_at": PERIOD_START,
        "updated_at": PERIOD_START,
        "details": {
            "totals": {
                "subtotal": "49900",
                "discount": "0",
                "tax": "0",
                "total": "49900",
                "grand_total": "49900",
                "balance": "0",
                "currency_code": "USD",
            }
        },
        "items": [{"price": {"id": PRICE_ID}, "quantity": 1}],
        "payments": [],
        "custom_data": {"tenant_id": str(TENANT_ID), "tier": "starter"},
    }
    data.update(overrides)
    return data


def _paddle_subscription(**overrides: Any) -> PaddleSubscription:
    """Build a ``PaddleSubscription`` dataclass with sensible defaults."""
    fields: dict[str, Any] = {
        "id": SUBSCRIPTION_ID,
        "customer_id": CUSTOMER_ID,
        "status": SubscriptionState.ACTIVE,
        "tier": SubscriptionTier.STARTER,
        "price_id": PRICE_ID,
        "current_period_start": datetime(2026, 9, 1, 10, tzinfo=UTC),
        "current_period_end": datetime(2026, 10, 1, 10, tzinfo=UTC),
        "next_billed_at": datetime(2026, 10, 1, 10, tzinfo=UTC),
        "cancel_at_period_end": False,
        "canceled_at": None,
        "paused_at": None,
        "trial_end": None,
        "custom_data": {"tenant_id": str(TENANT_ID)},
    }
    fields.update(overrides)
    return PaddleSubscription(**fields)


def _make_tenant(**overrides: Any) -> SimpleNamespace:
    """A tenant row stand-in with the Paddle columns."""
    tenant = SimpleNamespace(
        id=TENANT_ID,
        name="Acme Inc",
        plan="free",
        plan_expires_at=None,
        paddle_customer_id=None,
        paddle_subscription_id=None,
        subscription_status=None,
        current_period_end=None,
        is_deleted=False,
    )
    for key, value in overrides.items():
        setattr(tenant, key, value)
    return tenant


def _make_admin(
    email: str = "enc:admin@acme.test", full_name: str = "enc:Ada Admin"
) -> SimpleNamespace:
    return SimpleNamespace(
        id=11,
        tenant_id=TENANT_ID,
        email=email,
        full_name=full_name,
        role=UserRole.ADMIN,
        is_active=True,
        is_deleted=False,
    )


class _Result:
    """Minimal stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, scalar: Any = None, rows: Optional[list[Any]] = None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _NestedTransaction:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class FakeSession:
    """In-memory ``AsyncSession`` stand-in used through ``async_session_maker``."""

    def __init__(
        self,
        tenant: Optional[SimpleNamespace] = None,
        users: Optional[list[SimpleNamespace]] = None,
        duplicate: bool = False,
    ) -> None:
        self.tenant = tenant
        self.users = users or []
        self.duplicate = duplicate
        self.added: list[Any] = []
        self.queries: list[Any] = []
        self.committed = False
        self.rolled_back = False
        self.closed = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        self.closed = True
        return False

    def begin_nested(self) -> _NestedTransaction:
        return _NestedTransaction()

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        if self.duplicate:
            raise IntegrityError(
                "INSERT INTO paddle_webhook_events", {}, Exception("duplicate key")
            )

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    async def execute(self, stmt: Any) -> _Result:
        self.queries.append(stmt)
        entity = stmt.column_descriptions[0]["entity"]
        if entity is Tenant:
            return _Result(scalar=self.tenant)
        if entity is User:
            return _Result(rows=self.users)
        raise AssertionError(f"unexpected query entity: {entity}")


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sync_mocks(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Replace the tenant sync helpers with AsyncMocks."""
    mocks = SimpleNamespace(
        sync_subscription=AsyncMock(),
        clear_subscription=AsyncMock(),
        sync_customer=AsyncMock(),
    )
    monkeypatch.setattr(paddle_service, "sync_tenant_subscription", mocks.sync_subscription)
    monkeypatch.setattr(paddle_service, "clear_tenant_subscription", mocks.clear_subscription)
    monkeypatch.setattr(paddle_service, "sync_tenant_paddle_customer", mocks.sync_customer)
    return mocks


@pytest.fixture
def session(monkeypatch: pytest.MonkeyPatch) -> FakeSession:
    """Fake session bound to ``async_session_maker`` (tenant 42 without a customer)."""
    fake = FakeSession(tenant=_make_tenant())
    monkeypatch.setattr(paddle_webhook, "async_session_maker", lambda: fake)
    return fake


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Minimal app with the webhook router and a test webhook secret."""
    monkeypatch.setattr(settings, "paddle_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "paddle_starter_price_id", PRICE_ID)
    app = FastAPI()
    app.include_router(paddle_webhook.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def _post(client: TestClient, body: bytes, headers: Optional[dict[str, str]] = None) -> Any:
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    return client.post(WEBHOOK_URL, content=body, headers=request_headers)


def _post_signed(client: TestClient, event_type: str, data: dict[str, Any], **kwargs: Any) -> Any:
    body = _envelope(event_type, data, **kwargs)
    return _post(client, body, {"Paddle-Signature": _sign(body)})


# =============================================================================
# Routing
# =============================================================================


def test_route_path_matches_public_endpoint() -> None:
    from app.middleware.tenant import PUBLIC_ENDPOINTS

    assert paddle_webhook.WEBHOOK_PATH == "/webhooks/paddle"
    assert WEBHOOK_URL in PUBLIC_ENDPOINTS
    assert [r.path for r in paddle_webhook.router.routes] == ["/webhooks/paddle"]


# =============================================================================
# Signature verification
# =============================================================================


def test_signed_request_is_accepted(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    body = _envelope("subscription.activated", _subscription_data())
    response = _post(client, body, {"Paddle-Signature": _sign(body)})

    assert response.status_code == 200
    assert response.json() == {"status": "received", "event_type": "subscription.activated"}
    assert session.committed and not session.rolled_back and session.closed
    assert len(session.added) == 1
    event = session.added[0]
    assert isinstance(event, PaddleWebhookEvent)
    assert event.event_id == "evt_01hv8event"
    assert event.event_type == "subscription.activated"
    assert event.occurred_at == datetime(2026, 9, 1, 10, 0, 5, tzinfo=UTC)


def test_bad_signature_is_rejected(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    body = _envelope("subscription.activated", _subscription_data())
    tampered = _sign(body, secret="whsec-other")
    response = _post(client, body, {"Paddle-Signature": tampered})

    assert response.status_code == 400
    assert session.added == []
    sync_mocks.sync_subscription.assert_not_awaited()


def test_stale_timestamp_is_rejected(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    body = _envelope("subscription.activated", _subscription_data())
    stale = _sign(body, ts=int(time.time()) - 600)
    response = _post(client, body, {"Paddle-Signature": stale})

    assert response.status_code == 400
    assert session.added == []
    sync_mocks.sync_subscription.assert_not_awaited()


def test_missing_signature_header_is_rejected(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    body = _envelope("subscription.activated", _subscription_data())
    response = _post(client, body)

    assert response.status_code == 400
    assert "Paddle-Signature" in response.json()["detail"]
    assert session.added == []


def test_unset_secret_returns_503(
    client: TestClient,
    session: FakeSession,
    sync_mocks: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "paddle_webhook_secret", None)
    body = _envelope("subscription.activated", _subscription_data())
    response = _post(client, body, {"Paddle-Signature": _sign(body)})

    assert response.status_code == 503
    assert session.added == []
    sync_mocks.sync_subscription.assert_not_awaited()


def test_malformed_json_is_rejected(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    body = b"{not json"
    response = _post(client, body, {"Paddle-Signature": _sign(body)})

    assert response.status_code == 400
    assert session.added == []


def test_envelope_without_event_id_is_rejected(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    body = json.dumps({"event_type": "subscription.activated", "data": {}}).encode()
    response = _post(client, body, {"Paddle-Signature": _sign(body)})

    assert response.status_code == 400
    assert session.added == []


# =============================================================================
# Idempotency
# =============================================================================


def test_duplicate_event_is_not_dispatched_again(
    client: TestClient, sync_mocks: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeSession(tenant=_make_tenant(), duplicate=True)
    monkeypatch.setattr(paddle_webhook, "async_session_maker", lambda: fake)

    response = _post_signed(client, "subscription.activated", _subscription_data())

    assert response.status_code == 200
    assert response.json() == {"status": "duplicate", "event_type": "subscription.activated"}
    assert fake.rolled_back and not fake.committed
    sync_mocks.sync_subscription.assert_not_awaited()
    sync_mocks.sync_customer.assert_not_awaited()


# =============================================================================
# subscription.* events
# =============================================================================


def test_subscription_activated_syncs_tenant(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    response = _post_signed(client, "subscription.activated", _subscription_data())

    assert response.status_code == 200
    assert response.json()["status"] == "received"

    sync_mocks.sync_subscription.assert_awaited_once()
    db_arg, tenant_id, subscription = sync_mocks.sync_subscription.await_args.args
    assert db_arg is session
    assert tenant_id == TENANT_ID
    assert isinstance(subscription, PaddleSubscription)
    assert subscription.id == SUBSCRIPTION_ID
    assert subscription.customer_id == CUSTOMER_ID
    assert subscription.status == SubscriptionState.ACTIVE
    assert subscription.price_id == PRICE_ID
    assert subscription.tier == SubscriptionTier.STARTER
    assert subscription.current_period_end == datetime(2026, 10, 1, 10, tzinfo=UTC)
    assert subscription.cancel_at_period_end is False
    assert subscription.custom_data.get("tenant_id") == str(TENANT_ID)

    # Tenant had no customer yet -> the customer id gets linked.
    sync_mocks.sync_customer.assert_awaited_once_with(session, TENANT_ID, CUSTOMER_ID)


@pytest.mark.parametrize(
    "event_type",
    [
        "subscription.created",
        "subscription.updated",
        "subscription.trialing",
        "subscription.past_due",
        "subscription.paused",
        "subscription.resumed",
    ],
)
def test_all_subscription_state_events_sync(
    event_type: str, client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    response = _post_signed(client, event_type, _subscription_data())

    assert response.status_code == 200
    assert response.json() == {"status": "received", "event_type": event_type}
    sync_mocks.sync_subscription.assert_awaited_once()
    sync_mocks.clear_subscription.assert_not_awaited()


def test_subscription_sync_keeps_existing_customer_link(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    session.tenant.paddle_customer_id = CUSTOMER_ID

    response = _post_signed(client, "subscription.updated", _subscription_data())

    assert response.status_code == 200
    sync_mocks.sync_subscription.assert_awaited_once()
    sync_mocks.sync_customer.assert_not_awaited()


def test_subscription_scheduled_cancel_is_reflected(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    data = _subscription_data(
        scheduled_change={"action": "cancel", "effective_at": PERIOD_END, "resume_at": None}
    )
    response = _post_signed(client, "subscription.updated", data)

    assert response.status_code == 200
    subscription = sync_mocks.sync_subscription.await_args.args[2]
    assert subscription.cancel_at_period_end is True


def test_subscription_canceled_clears_tenant(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    data = _subscription_data(status="canceled", canceled_at="2026-09-15T12:30:00.000000Z")
    response = _post_signed(client, "subscription.canceled", data)

    assert response.status_code == 200
    assert response.json() == {"status": "received", "event_type": "subscription.canceled"}
    sync_mocks.clear_subscription.assert_awaited_once_with(
        session, TENANT_ID, datetime(2026, 9, 15, 12, 30, tzinfo=UTC)
    )
    sync_mocks.sync_subscription.assert_not_awaited()


def test_tenant_resolved_by_customer_id_without_custom_data(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    session.tenant.paddle_customer_id = CUSTOMER_ID
    data = _subscription_data(custom_data=None)

    response = _post_signed(client, "subscription.activated", data)

    assert response.status_code == 200
    assert response.json()["status"] == "received"
    tenant_queries = [
        str(q) for q in session.queries if q.column_descriptions[0]["entity"] is Tenant
    ]
    assert tenant_queries, "expected a tenant lookup"
    assert any("paddle_customer_id" in q for q in tenant_queries)
    sync_mocks.sync_subscription.assert_awaited_once()


def test_unresolvable_tenant_is_ignored_but_recorded(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    session.tenant = None

    response = _post_signed(client, "subscription.activated", _subscription_data())

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "event_type": "subscription.activated"}
    assert session.committed
    assert len(session.added) == 1
    sync_mocks.sync_subscription.assert_not_awaited()


# =============================================================================
# transaction.* events
# =============================================================================


def test_transaction_completed_fetches_subscription_and_syncs(
    client: TestClient,
    session: FakeSession,
    sync_mocks: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched = _paddle_subscription()
    paddle_client = MagicMock()
    paddle_client.get_subscription = AsyncMock(return_value=fetched)
    monkeypatch.setattr(paddle_service, "is_configured", lambda: True)
    monkeypatch.setattr(paddle_service, "get_paddle_client", lambda: paddle_client)

    response = _post_signed(client, "transaction.completed", _transaction_data())

    assert response.status_code == 200
    assert response.json() == {"status": "received", "event_type": "transaction.completed"}
    paddle_client.get_subscription.assert_awaited_once_with(SUBSCRIPTION_ID)
    sync_mocks.sync_subscription.assert_awaited_once_with(session, TENANT_ID, fetched)
    sync_mocks.sync_customer.assert_awaited_once_with(session, TENANT_ID, CUSTOMER_ID)


def test_transaction_paid_is_handled_like_completed(
    client: TestClient,
    session: FakeSession,
    sync_mocks: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paddle_client = MagicMock()
    paddle_client.get_subscription = AsyncMock(return_value=_paddle_subscription())
    monkeypatch.setattr(paddle_service, "is_configured", lambda: True)
    monkeypatch.setattr(paddle_service, "get_paddle_client", lambda: paddle_client)

    response = _post_signed(client, "transaction.paid", _transaction_data())

    assert response.status_code == 200
    assert response.json()["status"] == "received"
    sync_mocks.sync_subscription.assert_awaited_once()


def test_transaction_completed_without_paddle_config_links_customer_only(
    client: TestClient,
    session: FakeSession,
    sync_mocks: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _must_not_be_called() -> Any:
        raise AssertionError("get_paddle_client must not be called when unconfigured")

    monkeypatch.setattr(paddle_service, "is_configured", lambda: False)
    monkeypatch.setattr(paddle_service, "get_paddle_client", _must_not_be_called)

    response = _post_signed(client, "transaction.completed", _transaction_data())

    assert response.status_code == 200
    assert response.json()["status"] == "received"
    sync_mocks.sync_customer.assert_awaited_once_with(session, TENANT_ID, CUSTOMER_ID)
    sync_mocks.sync_subscription.assert_not_awaited()


def test_transaction_completed_without_subscription_links_customer_only(
    client: TestClient,
    session: FakeSession,
    sync_mocks: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paddle_service, "is_configured", lambda: True)
    monkeypatch.setattr(paddle_service, "get_paddle_client", MagicMock())

    response = _post_signed(
        client, "transaction.completed", _transaction_data(subscription_id=None)
    )

    assert response.status_code == 200
    sync_mocks.sync_customer.assert_awaited_once_with(session, TENANT_ID, CUSTOMER_ID)
    sync_mocks.sync_subscription.assert_not_awaited()


def test_payment_failed_emails_tenant_admins(
    client: TestClient,
    session: FakeSession,
    sync_mocks: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session.users = [_make_admin()]
    email_service = MagicMock()
    email_service.send_payment_failed_email.return_value = True
    monkeypatch.setattr(paddle_webhook, "get_email_service", lambda: email_service)
    monkeypatch.setattr(paddle_webhook, "decrypt_pii", lambda value: value.removeprefix("enc:"))

    data = _transaction_data(
        status="past_due",
        payments=[
            {"payment_attempt_id": "pay_1", "status": "error", "error_code": "declined"},
            {"payment_attempt_id": "pay_2", "status": "error", "error_code": "declined"},
        ],
    )
    response = _post_signed(client, "transaction.payment_failed", data)

    assert response.status_code == 200
    assert response.json() == {"status": "received", "event_type": "transaction.payment_failed"}
    email_service.send_payment_failed_email.assert_called_once_with(
        "admin@acme.test", "Ada Admin", 2, "499.00 USD"
    )
    sync_mocks.sync_subscription.assert_not_awaited()
    sync_mocks.clear_subscription.assert_not_awaited()


def test_payment_failed_defaults_attempt_count_to_one(
    client: TestClient,
    session: FakeSession,
    sync_mocks: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session.users = [_make_admin(full_name="")]
    email_service = MagicMock()
    monkeypatch.setattr(paddle_webhook, "get_email_service", lambda: email_service)
    monkeypatch.setattr(paddle_webhook, "decrypt_pii", lambda value: value.removeprefix("enc:"))

    response = _post_signed(client, "transaction.payment_failed", _transaction_data(payments=None))

    assert response.status_code == 200
    email_service.send_payment_failed_email.assert_called_once_with(
        "admin@acme.test", "admin@acme.test", 1, "499.00 USD"
    )


def test_payment_failed_email_error_does_not_fail_webhook(
    client: TestClient,
    session: FakeSession,
    sync_mocks: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session.users = [_make_admin()]
    email_service = MagicMock()
    email_service.send_payment_failed_email.side_effect = RuntimeError("smtp down")
    monkeypatch.setattr(paddle_webhook, "get_email_service", lambda: email_service)
    monkeypatch.setattr(paddle_webhook, "decrypt_pii", lambda value: value.removeprefix("enc:"))

    response = _post_signed(client, "transaction.payment_failed", _transaction_data())

    assert response.status_code == 200
    assert session.committed


# =============================================================================
# customer.* events
# =============================================================================


def test_customer_created_links_tenant(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    data = {
        "id": CUSTOMER_ID,
        "email": "billing@acme.test",
        "name": "Acme Inc",
        "status": "active",
        "custom_data": {"tenant_id": str(TENANT_ID)},
        "created_at": PERIOD_START,
        "updated_at": PERIOD_START,
    }
    response = _post_signed(client, "customer.created", data)

    assert response.status_code == 200
    assert response.json() == {"status": "received", "event_type": "customer.created"}
    sync_mocks.sync_customer.assert_awaited_once_with(session, TENANT_ID, CUSTOMER_ID)


def test_customer_updated_without_tenant_is_ignored(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    data = {"id": CUSTOMER_ID, "email": "billing@acme.test", "custom_data": None}
    response = _post_signed(client, "customer.updated", data)

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "event_type": "customer.updated"}
    sync_mocks.sync_customer.assert_not_awaited()


# =============================================================================
# Ignored events and failures
# =============================================================================


@pytest.mark.parametrize(
    "event_type",
    [
        "adjustment.created",
        "transaction.created",
        "transaction.billed",
        "transaction.updated",
        "transaction.ready",
        "something.unknown",
    ],
)
def test_unhandled_events_are_ignored(
    event_type: str, client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    response = _post_signed(client, event_type, _transaction_data())

    assert response.status_code == 200
    assert response.json() == {"status": "ignored", "event_type": event_type}
    assert session.committed
    assert len(session.added) == 1  # still recorded for idempotency
    sync_mocks.sync_subscription.assert_not_awaited()
    sync_mocks.sync_customer.assert_not_awaited()
    sync_mocks.clear_subscription.assert_not_awaited()


def test_handler_exception_returns_500_after_rollback(
    client: TestClient, session: FakeSession, sync_mocks: SimpleNamespace
) -> None:
    sync_mocks.sync_subscription.side_effect = RuntimeError("boom")

    response = _post_signed(client, "subscription.activated", _subscription_data())

    assert response.status_code == 500
    assert session.rolled_back and not session.committed
    assert session.closed
