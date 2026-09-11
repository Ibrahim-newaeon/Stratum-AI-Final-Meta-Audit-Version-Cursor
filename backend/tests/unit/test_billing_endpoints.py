# =============================================================================
# Stratum AI - Billing Endpoint Tests (Paddle Billing)
# =============================================================================
"""
Unit tests for ``app.api.v1.endpoints.billing``.

No network, no database: the router is mounted on a minimal FastAPI app.
``get_current_user`` is overridden with a header-driven fake (``X-Test-Tenant``
/ ``X-Test-Role``) so each test picks its caller; ``get_async_session`` is
overridden with an in-memory fake and ``paddle_service.get_paddle_client`` is
monkeypatched to return a spec'd ``MagicMock`` with ``AsyncMock`` methods.

The authentication section at the bottom mounts the router behind the *real*
``TenantMiddleware`` and the *real* ``get_current_user`` to prove that
``X-Tenant-ID``-only, token-less and garbage-token requests are rejected with
401 in both production and development middleware modes.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.api.v1.endpoints import billing
from app.auth.deps import CurrentUser, get_current_user
from app.base_models import Tenant, User, UserRole
from app.core.config import settings
from app.core.security import create_access_token
from app.core.tiers import SubscriptionTier
from app.db.session import get_async_session
from app.middleware.tenant import TenantMiddleware
from app.services import paddle_service
from app.services.paddle_service import (
    PaddleClient,
    PaddleCustomer,
    PaddleError,
    PaddleSubscription,
    PaddleTransaction,
    PortalSession,
    SubscriptionState,
)

pytestmark = pytest.mark.unit

BASE = "/api/v1/billing"
TENANT_ID = 7
CUSTOMER_ID = "ctm_01hv8billingcustomer"
SUBSCRIPTION_ID = "sub_01hv8billingsubscription"
TRANSACTION_ID = "txn_01hv8billingtransaction"
PRICE_IDS: dict[SubscriptionTier, Optional[str]] = {
    SubscriptionTier.STARTER: "pri_01hv8starter",
    SubscriptionTier.PROFESSIONAL: "pri_01hv8professional",
    SubscriptionTier.ENTERPRISE: None,
}
CLIENT_TOKEN = "test_7d5f4paddleclienttoken"
PERIOD_START = datetime(2026, 9, 1, 10, tzinfo=UTC)
PERIOD_END = datetime(2026, 10, 1, 10, tzinfo=UTC)


# =============================================================================
# Helpers
# =============================================================================


USER_ID = 1
NO_TENANT = "none"  # X-Test-Tenant value for an authenticated user without a tenant

# (method, path, json body) for every route that requires authentication.
AUTHENTICATED_ROUTES: list[tuple[str, str, Optional[dict[str, Any]]]] = [
    ("GET", f"{BASE}/subscription", None),
    ("POST", f"{BASE}/checkout-session", {"tier": "professional"}),
    ("POST", f"{BASE}/portal-session", {}),
    ("POST", f"{BASE}/cancel", {}),
    ("POST", f"{BASE}/reactivate", None),
    ("POST", f"{BASE}/upgrade", {"new_tier": "professional"}),
    ("GET", f"{BASE}/transactions", None),
    ("GET", f"{BASE}/transactions/{TRANSACTION_ID}/invoice", None),
]
# Subset that changes billing state and therefore requires the admin role.
MUTATING_ROUTES: list[tuple[str, str, Optional[dict[str, Any]]]] = [
    route for route in AUTHENTICATED_ROUTES if route[0] == "POST"
]
READ_ROUTES: list[tuple[str, str, Optional[dict[str, Any]]]] = [
    route for route in AUTHENTICATED_ROUTES if route[0] == "GET"
]


def _headers(
    tenant_id: Optional[int] = TENANT_ID, role: UserRole = UserRole.ADMIN
) -> dict[str, str]:
    """Identity headers understood by the ``get_current_user`` test override.

    ``tenant_id=None`` sends no identity at all (an unauthenticated request).
    """
    if tenant_id is None:
        return {}
    return {"X-Test-Tenant": str(tenant_id), "X-Test-Role": role.value}


def _current_user(tenant_id: Optional[int], role: UserRole) -> CurrentUser:
    """Build a ``CurrentUser`` around a duck-typed user record."""
    user = SimpleNamespace(
        id=USER_ID,
        tenant_id=tenant_id,
        role=role,
        is_active=True,
        is_verified=True,
        permissions={},
    )
    return CurrentUser(user=user, email="owner@acme.test", full_name="Olivia Owner")  # type: ignore[arg-type]


def _call(
    client: TestClient, method: str, path: str, body: Optional[dict[str, Any]], **kwargs: Any
) -> Any:
    """Issue ``method path`` with an optional JSON body."""
    if method == "GET":
        return client.get(path, **kwargs)
    return client.post(path, json=body, **kwargs)


def _paddle_subscription(**overrides: Any) -> PaddleSubscription:
    fields: dict[str, Any] = {
        "id": SUBSCRIPTION_ID,
        "customer_id": CUSTOMER_ID,
        "status": SubscriptionState.ACTIVE,
        "tier": SubscriptionTier.STARTER,
        "price_id": PRICE_IDS[SubscriptionTier.STARTER],
        "current_period_start": PERIOD_START,
        "current_period_end": PERIOD_END,
        "next_billed_at": PERIOD_END,
        "cancel_at_period_end": False,
        "canceled_at": None,
        "paused_at": None,
        "trial_end": None,
        "custom_data": {"tenant_id": str(TENANT_ID)},
    }
    fields.update(overrides)
    return PaddleSubscription(**fields)


def _paddle_transaction(**overrides: Any) -> PaddleTransaction:
    fields: dict[str, Any] = {
        "id": TRANSACTION_ID,
        "invoice_number": "INV-0001",
        "status": "completed",
        "subscription_id": SUBSCRIPTION_ID,
        "customer_id": CUSTOMER_ID,
        "total_minor": 49900,
        "currency_code": "USD",
        "billed_at": PERIOD_START,
        "created_at": PERIOD_START,
        "custom_data": {},
    }
    fields.update(overrides)
    return PaddleTransaction(**fields)


def _paddle_error(
    status_code: int = 500, detail: str = "upstream failure", code: Optional[str] = None
) -> PaddleError:
    """Build a ``PaddleError`` regardless of its constructor signature."""
    try:
        err = PaddleError(status_code=status_code, code=code, detail=detail)
    except TypeError:
        err = PaddleError.__new__(PaddleError)
        Exception.__init__(err, detail)
    err.status_code = status_code
    err.code = code
    err.detail = detail
    return err


class _Result:
    def __init__(self, scalar: Any = None, rows: Optional[list[Any]] = None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class FakeDB:
    """Stand-in for the request-scoped ``AsyncSession``."""

    def __init__(
        self, tenant: Optional[SimpleNamespace], users: Optional[list[SimpleNamespace]] = None
    ) -> None:
        self.tenant = tenant
        self.users = users or []
        self.queries: list[Any] = []
        self.refreshed: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        self.queries.append(stmt)
        entity = stmt.column_descriptions[0]["entity"]
        if entity is Tenant:
            return _Result(scalar=self.tenant)
        if entity is User:
            return _Result(scalar=self.users[0] if self.users else None, rows=self.users)
        raise AssertionError(f"unexpected query entity: {entity}")

    async def refresh(self, obj: Any) -> None:
        self.refreshed.append(obj)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tenant() -> SimpleNamespace:
    return SimpleNamespace(
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


@pytest.fixture
def admin_user() -> SimpleNamespace:
    """Tenant admin as stored in the DB (also what the real ``get_current_user`` loads)."""
    return SimpleNamespace(
        id=USER_ID,
        tenant_id=TENANT_ID,
        email="enc:owner@acme.test",
        full_name="enc:Olivia Owner",
        role=UserRole.ADMIN,
        is_active=True,
        is_verified=True,
        is_deleted=False,
        permissions={},
    )


@pytest.fixture
def db(tenant: SimpleNamespace, admin_user: SimpleNamespace) -> FakeDB:
    return FakeDB(tenant=tenant, users=[admin_user])


@pytest.fixture
def sync_mocks(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    mocks = SimpleNamespace(sync_subscription=AsyncMock(), sync_customer=AsyncMock())
    monkeypatch.setattr(paddle_service, "sync_tenant_subscription", mocks.sync_subscription)
    monkeypatch.setattr(paddle_service, "sync_tenant_paddle_customer", mocks.sync_customer)
    return mocks


@pytest.fixture
def paddle(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Configured Paddle: settings + price map + a spec'd client mock."""
    monkeypatch.setattr(settings, "paddle_api_key", "pdl_sdbx_apikey_test")
    monkeypatch.setattr(settings, "billing_payments_enabled", True)
    monkeypatch.setattr(settings, "paddle_client_token", CLIENT_TOKEN)
    monkeypatch.setattr(settings, "paddle_environment", "sandbox")
    monkeypatch.setattr(settings, "paddle_starter_price_id", PRICE_IDS[SubscriptionTier.STARTER])
    monkeypatch.setattr(
        settings, "paddle_professional_price_id", PRICE_IDS[SubscriptionTier.PROFESSIONAL]
    )
    monkeypatch.setattr(settings, "paddle_enterprise_price_id", None)
    monkeypatch.setattr(settings, "frontend_url", "https://app.stratum.test")
    monkeypatch.setattr(paddle_service, "is_configured", lambda: True)
    monkeypatch.setattr(paddle_service, "get_price_id_for_tier", lambda tier: PRICE_IDS.get(tier))

    client = MagicMock(spec=PaddleClient)
    client.create_customer = AsyncMock(
        return_value=PaddleCustomer(
            id=CUSTOMER_ID,
            email="owner@acme.test",
            name="Acme Inc",
            custom_data={"tenant_id": str(TENANT_ID)},
        )
    )
    client.get_customer = AsyncMock(return_value=None)
    client.get_subscription = AsyncMock(return_value=_paddle_subscription())
    client.list_subscriptions = AsyncMock(return_value=[])
    client.update_subscription_tier = AsyncMock(
        return_value=_paddle_subscription(tier=SubscriptionTier.PROFESSIONAL)
    )
    client.cancel_subscription = AsyncMock(
        return_value=_paddle_subscription(cancel_at_period_end=True)
    )
    client.reactivate_subscription = AsyncMock(return_value=_paddle_subscription())
    client.list_transactions = AsyncMock(return_value=[])
    client.get_transaction = AsyncMock(return_value=_paddle_transaction())
    client.get_transaction_invoice_url = AsyncMock(
        return_value="https://sandbox-api.paddle.com/invoice.pdf"
    )
    client.create_portal_session = AsyncMock(
        return_value=PortalSession(
            overview_url="https://sandbox-customer-portal.paddle.com/cpl_overview",
            cancel_url="https://sandbox-customer-portal.paddle.com/cpl_cancel",
            update_payment_method_url="https://sandbox-customer-portal.paddle.com/cpl_payment",
        )
    )
    monkeypatch.setattr(paddle_service, "get_paddle_client", lambda: client)
    return client


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Paddle not configured: no API key/client token and the client must never be built."""

    def _must_not_be_called() -> Any:
        raise AssertionError("get_paddle_client must not be called when Paddle is unconfigured")

    monkeypatch.setattr(settings, "paddle_api_key", None)
    monkeypatch.setattr(settings, "paddle_client_token", None)
    monkeypatch.setattr(settings, "paddle_environment", "sandbox")
    monkeypatch.setattr(settings, "paddle_starter_price_id", None)
    monkeypatch.setattr(settings, "paddle_professional_price_id", None)
    monkeypatch.setattr(settings, "paddle_enterprise_price_id", None)
    monkeypatch.setattr(paddle_service, "is_configured", lambda: False)
    monkeypatch.setattr(paddle_service, "get_paddle_client", _must_not_be_called)


@pytest.fixture
def client(db: FakeDB, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Router with ``get_current_user`` replaced by a header-driven fake.

    No ``X-Test-Tenant`` header means "no credentials" and yields the same 401
    the real dependency raises; ``X-Test-Tenant: none`` authenticates a user
    without any tenant; ``X-Test-Role`` (default ``admin``) picks the role.
    """
    monkeypatch.setattr(billing, "decrypt_pii", lambda value: value.removeprefix("enc:"))

    async def _fake_current_user(request: Request) -> CurrentUser:
        raw = request.headers.get("X-Test-Tenant")
        if raw is None:
            raise HTTPException(
                status_code=401,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        tenant_id = None if raw == NO_TENANT else int(raw)
        role = UserRole(request.headers.get("X-Test-Role", UserRole.ADMIN.value))
        return _current_user(tenant_id, role)

    app = FastAPI()
    app.include_router(billing.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = _fake_current_user
    app.dependency_overrides[get_async_session] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(params=["production", "development"], ids=["prod-middleware", "dev-middleware"])
def real_auth_client(
    request: pytest.FixtureRequest, db: FakeDB, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """Router behind the real ``TenantMiddleware`` and the real ``get_current_user``.

    Parametrised over the middleware's environment switch: in development the
    middleware falls back to tenant 1 when no context is present, in production
    it rejects outright. Either way the endpoints must demand a valid token.
    """
    is_dev = request.param == "development"
    monkeypatch.setattr(type(settings), "is_development", property(lambda self: is_dev))
    monkeypatch.setattr(billing, "decrypt_pii", lambda value: value.removeprefix("enc:"))
    monkeypatch.setattr(
        "app.core.security.decrypt_pii", lambda value: value.removeprefix("enc:")
    )

    app = FastAPI()
    app.add_middleware(TenantMiddleware)
    app.include_router(billing.router, prefix="/api/v1")
    app.dependency_overrides[get_async_session] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _bearer(user_id: int, tenant_id: int, role: UserRole) -> dict[str, str]:
    """Authorization header carrying a real, signed access token."""
    token = create_access_token(
        subject=user_id,
        additional_claims={"tenant_id": tenant_id, "role": role.value, "email": "owner@acme.test"},
    )
    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# GET /billing/config
# =============================================================================


def test_config_when_not_configured(client: TestClient, unconfigured: None) -> None:
    response = client.get(f"{BASE}/config", headers=_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["paddle_configured"] is False
    assert data["payments_enabled"] is False
    assert data["client_token"] is None
    assert data["environment"] == "sandbox"
    assert data["price_ids"] == {"starter": None, "professional": None, "enterprise": None}
    assert [t["tier"] for t in data["tiers"]] == ["starter", "professional", "enterprise"]
    starter = data["tiers"][0]
    assert starter["name"] == "Starter"
    assert starter["price"] == 499
    assert starter["currency"] == "USD"
    assert starter["billing_period"] == "monthly"
    assert data["tiers"][2]["price"] is None


def test_config_when_configured(client: TestClient, paddle: MagicMock) -> None:
    response = client.get(f"{BASE}/config", headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["paddle_configured"] is True
    assert data["payments_enabled"] is True
    assert data["client_token"] == CLIENT_TOKEN
    assert data["price_ids"]["starter"] == PRICE_IDS[SubscriptionTier.STARTER]
    assert data["price_ids"]["professional"] == PRICE_IDS[SubscriptionTier.PROFESSIONAL]
    assert data["price_ids"]["enterprise"] is None


# =============================================================================
# GET /billing/subscription
# =============================================================================


def test_subscription_when_not_configured_uses_tenant_plan(
    client: TestClient, unconfigured: None, tenant: SimpleNamespace
) -> None:
    tenant.plan = "professional"
    tenant.paddle_customer_id = CUSTOMER_ID

    response = client.get(f"{BASE}/subscription", headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["paddle_configured"] is False
    assert data["has_customer"] is True
    assert data["has_subscription"] is False
    assert data["customer_id"] == CUSTOMER_ID
    assert data["plan"] == "professional"
    assert data["subscription_id"] is None
    assert data["status"] is None
    assert data["cancel_at_period_end"] is False


def test_subscription_without_customer(client: TestClient, paddle: MagicMock) -> None:
    response = client.get(f"{BASE}/subscription", headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data == {
        "paddle_configured": True,
        "has_customer": False,
        "has_subscription": False,
        "customer_id": None,
        "subscription_id": None,
        "status": None,
        "tier": None,
        "plan": "free",
        "current_period_start": None,
        "current_period_end": None,
        "next_billed_at": None,
        "cancel_at_period_end": False,
        "canceled_at": None,
        "paused_at": None,
        "trial_end": None,
    }
    paddle.get_subscription.assert_not_awaited()
    paddle.list_subscriptions.assert_not_awaited()


def test_subscription_uses_stored_subscription_id(
    client: TestClient, paddle: MagicMock, tenant: SimpleNamespace
) -> None:
    tenant.plan = "starter"
    tenant.paddle_customer_id = CUSTOMER_ID
    tenant.paddle_subscription_id = SUBSCRIPTION_ID
    paddle.get_subscription.return_value = _paddle_subscription(
        trial_end=datetime(2026, 9, 15, tzinfo=UTC)
    )

    response = client.get(f"{BASE}/subscription", headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["has_subscription"] is True
    assert data["subscription_id"] == SUBSCRIPTION_ID
    assert data["status"] == "active"
    assert data["tier"] == "starter"
    assert data["plan"] == "starter"
    assert data["current_period_start"] == "2026-09-01T10:00:00+00:00"
    assert data["current_period_end"] == "2026-10-01T10:00:00+00:00"
    assert data["next_billed_at"] == "2026-10-01T10:00:00+00:00"
    assert data["trial_end"] == "2026-09-15T00:00:00+00:00"
    assert data["cancel_at_period_end"] is False
    paddle.get_subscription.assert_awaited_once_with(SUBSCRIPTION_ID)
    paddle.list_subscriptions.assert_not_awaited()


def test_subscription_lists_and_prefers_live_subscription(
    client: TestClient, paddle: MagicMock, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    canceled = _paddle_subscription(id="sub_old", status=SubscriptionState.CANCELED)
    active = _paddle_subscription(id="sub_new", status=SubscriptionState.ACTIVE)
    paddle.list_subscriptions.return_value = [canceled, active]

    response = client.get(f"{BASE}/subscription", headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["subscription_id"] == "sub_new"
    assert data["status"] == "active"
    paddle.list_subscriptions.assert_awaited_once_with(CUSTOMER_ID)
    paddle.get_subscription.assert_not_awaited()


def test_subscription_falls_back_to_listing_when_stored_is_canceled(
    client: TestClient, paddle: MagicMock, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    tenant.paddle_subscription_id = "sub_old"
    paddle.get_subscription.return_value = _paddle_subscription(
        id="sub_old", status=SubscriptionState.CANCELED
    )
    paddle.list_subscriptions.return_value = [
        _paddle_subscription(id="sub_new", status=SubscriptionState.TRIALING)
    ]

    response = client.get(f"{BASE}/subscription", headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["subscription_id"] == "sub_new"
    assert data["status"] == "trialing"


def test_subscription_reports_canceled_when_nothing_live(
    client: TestClient, paddle: MagicMock, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    tenant.paddle_subscription_id = "sub_old"
    canceled = _paddle_subscription(
        id="sub_old",
        status=SubscriptionState.CANCELED,
        canceled_at=datetime(2026, 9, 15, tzinfo=UTC),
    )
    paddle.get_subscription.return_value = canceled
    paddle.list_subscriptions.return_value = []

    response = client.get(f"{BASE}/subscription", headers=_headers())

    data = response.json()["data"]
    assert data["has_subscription"] is True
    assert data["status"] == "canceled"
    assert data["canceled_at"] == "2026-09-15T00:00:00+00:00"


def test_subscription_stored_id_missing_upstream_falls_back(
    client: TestClient, paddle: MagicMock, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    tenant.paddle_subscription_id = "sub_gone"
    paddle.get_subscription.side_effect = _paddle_error(status_code=404, detail="not found")
    paddle.list_subscriptions.return_value = []

    response = client.get(f"{BASE}/subscription", headers=_headers())

    assert response.status_code == 200
    assert response.json()["data"]["has_subscription"] is False


def test_paddle_error_maps_to_502(
    client: TestClient, paddle: MagicMock, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    paddle.list_subscriptions.side_effect = _paddle_error(status_code=500, detail="paddle down")

    response = client.get(f"{BASE}/subscription", headers=_headers())

    assert response.status_code == 502
    assert response.json()["detail"] == "paddle down"


def test_missing_credentials_is_401(client: TestClient, paddle: MagicMock) -> None:
    response = client.get(f"{BASE}/subscription", headers=_headers(None))
    assert response.status_code == 401


def test_user_without_tenant_is_403(client: TestClient, paddle: MagicMock, db: FakeDB) -> None:
    response = client.get(f"{BASE}/subscription", headers={"X-Test-Tenant": NO_TENANT})

    assert response.status_code == 403
    assert db.queries == []  # never touched the tenant table


def test_unknown_tenant_is_404(client: TestClient, paddle: MagicMock, db: FakeDB) -> None:
    db.tenant = None
    response = client.get(f"{BASE}/subscription", headers=_headers())
    assert response.status_code == 404


# =============================================================================
# POST /billing/checkout-session
# =============================================================================


def test_checkout_session_when_not_configured_is_503(
    client: TestClient, unconfigured: None
) -> None:
    response = client.post(f"{BASE}/checkout-session", json={"tier": "starter"}, headers=_headers())
    assert response.status_code == 503


def test_checkout_session_when_payments_disabled_is_503(
    client: TestClient, paddle: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paddle keys alone must not open checkout on this free portal."""
    monkeypatch.setattr(settings, "billing_payments_enabled", False)
    response = client.post(f"{BASE}/checkout-session", json={"tier": "starter"}, headers=_headers())
    assert response.status_code == 503
    assert response.json()["detail"] == "This portal does not take payments"
    paddle.create_customer.assert_not_awaited()


def test_checkout_session_creates_customer_once(
    client: TestClient,
    paddle: MagicMock,
    sync_mocks: SimpleNamespace,
    db: FakeDB,
    tenant: SimpleNamespace,
) -> None:
    first = client.post(f"{BASE}/checkout-session", json={"tier": "starter"}, headers=_headers())

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["success"] is True
    data = body["data"]
    assert data["price_id"] == PRICE_IDS[SubscriptionTier.STARTER]
    assert data["client_token"] == CLIENT_TOKEN
    assert data["environment"] == "sandbox"
    assert data["customer_id"] == CUSTOMER_ID
    assert data["customer_email"] == "owner@acme.test"
    assert data["custom_data"] == {"tenant_id": str(TENANT_ID), "tier": "starter"}
    assert data["success_url"] == "https://app.stratum.test/dashboard/billing/success"
    assert data["display_mode"] == "overlay"

    paddle.create_customer.assert_awaited_once_with(
        email="owner@acme.test", name="Acme Inc", tenant_id=TENANT_ID
    )
    sync_mocks.sync_customer.assert_awaited_once_with(db, TENANT_ID, CUSTOMER_ID)
    assert tenant.paddle_customer_id == CUSTOMER_ID

    second = client.post(
        f"{BASE}/checkout-session", json={"tier": "professional"}, headers=_headers()
    )

    assert second.status_code == 200
    assert second.json()["data"]["customer_id"] == CUSTOMER_ID
    assert second.json()["data"]["custom_data"]["tier"] == "professional"
    paddle.create_customer.assert_awaited_once()  # still only one creation
    sync_mocks.sync_customer.assert_awaited_once()


def test_checkout_session_honours_custom_success_url(
    client: TestClient, paddle: MagicMock, sync_mocks: SimpleNamespace
) -> None:
    response = client.post(
        f"{BASE}/checkout-session",
        json={"tier": "starter", "success_url": "https://app.stratum.test/welcome"},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["data"]["success_url"] == "https://app.stratum.test/welcome"


def test_checkout_session_without_price_id_is_400(
    client: TestClient, paddle: MagicMock, sync_mocks: SimpleNamespace
) -> None:
    response = client.post(
        f"{BASE}/checkout-session", json={"tier": "enterprise"}, headers=_headers()
    )

    assert response.status_code == 400
    assert "enterprise" in response.json()["detail"]
    paddle.create_customer.assert_not_awaited()


def test_checkout_session_rejects_unknown_tier(
    client: TestClient, paddle: MagicMock, sync_mocks: SimpleNamespace
) -> None:
    response = client.post(
        f"{BASE}/checkout-session", json={"tier": "platinum"}, headers=_headers()
    )
    assert response.status_code == 422


def test_checkout_session_without_admin_user_is_400(
    client: TestClient, paddle: MagicMock, sync_mocks: SimpleNamespace, db: FakeDB
) -> None:
    db.users = []

    response = client.post(f"{BASE}/checkout-session", json={"tier": "starter"}, headers=_headers())

    assert response.status_code == 400
    paddle.create_customer.assert_not_awaited()


def test_checkout_session_existing_customer_email_from_paddle(
    client: TestClient,
    paddle: MagicMock,
    sync_mocks: SimpleNamespace,
    db: FakeDB,
    tenant: SimpleNamespace,
) -> None:
    db.users = []
    tenant.paddle_customer_id = CUSTOMER_ID
    paddle.get_customer.return_value = PaddleCustomer(
        id=CUSTOMER_ID, email="finance@acme.test", name="Acme", custom_data={}
    )

    response = client.post(f"{BASE}/checkout-session", json={"tier": "starter"}, headers=_headers())

    assert response.status_code == 200
    assert response.json()["data"]["customer_email"] == "finance@acme.test"
    paddle.get_customer.assert_awaited_once_with(CUSTOMER_ID)
    paddle.create_customer.assert_not_awaited()


def test_checkout_session_without_client_token_is_503(
    client: TestClient, paddle: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "paddle_client_token", None)
    response = client.post(f"{BASE}/checkout-session", json={"tier": "starter"}, headers=_headers())
    assert response.status_code == 503


# =============================================================================
# POST /billing/portal-session
# =============================================================================


def test_portal_session_without_customer_is_400(client: TestClient, paddle: MagicMock) -> None:
    response = client.post(f"{BASE}/portal-session", json={}, headers=_headers())

    assert response.status_code == 400
    assert response.json()["detail"].startswith("No billing account found")
    paddle.create_portal_session.assert_not_awaited()


def test_portal_session_returns_links(
    client: TestClient, paddle: MagicMock, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    tenant.paddle_subscription_id = SUBSCRIPTION_ID

    response = client.post(
        f"{BASE}/portal-session",
        json={"return_url": "https://app.stratum.test/settings"},
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["data"] == {
        "portal_url": "https://sandbox-customer-portal.paddle.com/cpl_overview",
        "cancel_url": "https://sandbox-customer-portal.paddle.com/cpl_cancel",
        "update_payment_method_url": "https://sandbox-customer-portal.paddle.com/cpl_payment",
    }
    paddle.create_portal_session.assert_awaited_once_with(
        CUSTOMER_ID, subscription_ids=[SUBSCRIPTION_ID]
    )


def test_portal_session_when_not_configured_is_503(
    client: TestClient, unconfigured: None, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    response = client.post(f"{BASE}/portal-session", json={}, headers=_headers())
    assert response.status_code == 503


# =============================================================================
# POST /billing/cancel, /reactivate, /upgrade
# =============================================================================


def test_cancel_without_live_subscription_is_404(
    client: TestClient, paddle: MagicMock, sync_mocks: SimpleNamespace, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    paddle.list_subscriptions.return_value = [
        _paddle_subscription(status=SubscriptionState.CANCELED)
    ]

    response = client.post(f"{BASE}/cancel", json={}, headers=_headers())

    assert response.status_code == 404
    paddle.cancel_subscription.assert_not_awaited()
    sync_mocks.sync_subscription.assert_not_awaited()


def test_cancel_at_period_end_and_sync(
    client: TestClient,
    paddle: MagicMock,
    sync_mocks: SimpleNamespace,
    db: FakeDB,
    tenant: SimpleNamespace,
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    tenant.paddle_subscription_id = SUBSCRIPTION_ID

    response = client.post(f"{BASE}/cancel", json={}, headers=_headers())

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["has_subscription"] is True
    assert data["cancel_at_period_end"] is True
    assert data["status"] == "active"
    paddle.cancel_subscription.assert_awaited_once_with(SUBSCRIPTION_ID, at_period_end=True)
    sync_mocks.sync_subscription.assert_awaited_once_with(
        db, TENANT_ID, paddle.cancel_subscription.return_value
    )
    assert db.refreshed == [tenant]


def test_cancel_immediately(
    client: TestClient, paddle: MagicMock, sync_mocks: SimpleNamespace, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    tenant.paddle_subscription_id = SUBSCRIPTION_ID
    paddle.cancel_subscription.return_value = _paddle_subscription(
        status=SubscriptionState.CANCELED, canceled_at=datetime(2026, 9, 2, tzinfo=UTC)
    )

    response = client.post(f"{BASE}/cancel", json={"at_period_end": False}, headers=_headers())

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "canceled"
    paddle.cancel_subscription.assert_awaited_once_with(SUBSCRIPTION_ID, at_period_end=False)


def test_reactivate_requires_scheduled_cancellation(
    client: TestClient, paddle: MagicMock, sync_mocks: SimpleNamespace, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    tenant.paddle_subscription_id = SUBSCRIPTION_ID
    paddle.get_subscription.return_value = _paddle_subscription(cancel_at_period_end=False)

    response = client.post(f"{BASE}/reactivate", headers=_headers())

    assert response.status_code == 404
    paddle.reactivate_subscription.assert_not_awaited()


def test_reactivate_clears_scheduled_cancellation(
    client: TestClient,
    paddle: MagicMock,
    sync_mocks: SimpleNamespace,
    db: FakeDB,
    tenant: SimpleNamespace,
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    tenant.paddle_subscription_id = SUBSCRIPTION_ID
    paddle.get_subscription.return_value = _paddle_subscription(cancel_at_period_end=True)

    response = client.post(f"{BASE}/reactivate", headers=_headers())

    assert response.status_code == 200
    assert response.json()["data"]["cancel_at_period_end"] is False
    paddle.reactivate_subscription.assert_awaited_once_with(SUBSCRIPTION_ID)
    sync_mocks.sync_subscription.assert_awaited_once_with(
        db, TENANT_ID, paddle.reactivate_subscription.return_value
    )


def test_upgrade_changes_tier_with_proration_flag(
    client: TestClient,
    paddle: MagicMock,
    sync_mocks: SimpleNamespace,
    db: FakeDB,
    tenant: SimpleNamespace,
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    tenant.paddle_subscription_id = SUBSCRIPTION_ID

    response = client.post(
        f"{BASE}/upgrade", json={"new_tier": "professional", "prorate": False}, headers=_headers()
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["tier"] == "professional"
    paddle.update_subscription_tier.assert_awaited_once_with(
        SUBSCRIPTION_ID, SubscriptionTier.PROFESSIONAL, prorate=False
    )
    sync_mocks.sync_subscription.assert_awaited_once_with(
        db, TENANT_ID, paddle.update_subscription_tier.return_value
    )


def test_upgrade_to_same_tier_is_400(
    client: TestClient, paddle: MagicMock, sync_mocks: SimpleNamespace, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    tenant.paddle_subscription_id = SUBSCRIPTION_ID

    response = client.post(f"{BASE}/upgrade", json={"new_tier": "starter"}, headers=_headers())

    assert response.status_code == 400
    paddle.update_subscription_tier.assert_not_awaited()


def test_upgrade_without_subscription_is_404(
    client: TestClient, paddle: MagicMock, sync_mocks: SimpleNamespace, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID

    response = client.post(f"{BASE}/upgrade", json={"new_tier": "professional"}, headers=_headers())

    assert response.status_code == 404
    paddle.update_subscription_tier.assert_not_awaited()


def test_upgrade_to_tier_without_price_is_400(
    client: TestClient, paddle: MagicMock, sync_mocks: SimpleNamespace, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    tenant.paddle_subscription_id = SUBSCRIPTION_ID

    response = client.post(f"{BASE}/upgrade", json={"new_tier": "enterprise"}, headers=_headers())

    assert response.status_code == 400
    paddle.update_subscription_tier.assert_not_awaited()


# =============================================================================
# GET /billing/transactions
# =============================================================================


def test_transactions_map_amounts(
    client: TestClient, paddle: MagicMock, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    paddle.list_transactions.return_value = [
        _paddle_transaction(),
        _paddle_transaction(
            id="txn_02",
            invoice_number=None,
            status="billed",
            total_minor=99900,
            currency_code="EUR",
            billed_at=None,
            created_at=PERIOD_END,
        ),
    ]

    response = client.get(f"{BASE}/transactions", headers=_headers())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data == [
        {
            "id": TRANSACTION_ID,
            "invoice_number": "INV-0001",
            "status": "completed",
            "subscription_id": SUBSCRIPTION_ID,
            "amount_minor": 49900,
            "currency": "USD",
            "billed_at": "2026-09-01T10:00:00+00:00",
            "created_at": "2026-09-01T10:00:00+00:00",
        },
        {
            "id": "txn_02",
            "invoice_number": None,
            "status": "billed",
            "subscription_id": SUBSCRIPTION_ID,
            "amount_minor": 99900,
            "currency": "EUR",
            "billed_at": None,
            "created_at": "2026-10-01T10:00:00+00:00",
        },
    ]
    paddle.list_transactions.assert_awaited_once_with(CUSTOMER_ID, limit=10)


@pytest.mark.parametrize("requested,expected", [(500, 100), (0, 1), (-5, 1), (25, 25)])
def test_transactions_limit_is_clamped(
    requested: int, expected: int, client: TestClient, paddle: MagicMock, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID

    response = client.get(f"{BASE}/transactions", params={"limit": requested}, headers=_headers())

    assert response.status_code == 200
    paddle.list_transactions.assert_awaited_once_with(CUSTOMER_ID, limit=expected)


def test_transactions_without_customer_is_empty(client: TestClient, paddle: MagicMock) -> None:
    response = client.get(f"{BASE}/transactions", headers=_headers())

    assert response.status_code == 200
    assert response.json()["data"] == []
    paddle.list_transactions.assert_not_awaited()


def test_transactions_when_not_configured_is_empty(
    client: TestClient, unconfigured: None, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID

    response = client.get(f"{BASE}/transactions", headers=_headers())

    assert response.status_code == 200
    assert response.json()["data"] == []


# =============================================================================
# GET /billing/transactions/{id}/invoice
# =============================================================================


def test_invoice_when_not_configured_is_503(
    client: TestClient, unconfigured: None, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    response = client.get(f"{BASE}/transactions/{TRANSACTION_ID}/invoice", headers=_headers())
    assert response.status_code == 503


def test_invoice_without_customer_is_400(client: TestClient, paddle: MagicMock) -> None:
    response = client.get(f"{BASE}/transactions/{TRANSACTION_ID}/invoice", headers=_headers())

    assert response.status_code == 400
    paddle.get_transaction_invoice_url.assert_not_awaited()


def test_invoice_for_other_customer_is_404(
    client: TestClient, paddle: MagicMock, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = "ctm_someone_else"

    response = client.get(f"{BASE}/transactions/{TRANSACTION_ID}/invoice", headers=_headers())

    assert response.status_code == 404
    paddle.get_transaction_invoice_url.assert_not_awaited()


def test_invoice_unknown_transaction_is_404(
    client: TestClient, paddle: MagicMock, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    paddle.get_transaction.side_effect = _paddle_error(status_code=404, detail="not found")

    response = client.get(f"{BASE}/transactions/txn_missing/invoice", headers=_headers())

    assert response.status_code == 404


def test_invoice_returns_url(
    client: TestClient, paddle: MagicMock, tenant: SimpleNamespace
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID

    response = client.get(f"{BASE}/transactions/{TRANSACTION_ID}/invoice", headers=_headers())

    assert response.status_code == 200
    assert response.json()["data"] == {
        "transaction_id": TRANSACTION_ID,
        "invoice_url": "https://sandbox-api.paddle.com/invoice.pdf",
    }
    paddle.get_transaction.assert_awaited_once_with(TRANSACTION_ID)
    paddle.get_transaction_invoice_url.assert_awaited_once_with(TRANSACTION_ID)


# =============================================================================
# Authentication & authorization
# =============================================================================


def _assert_no_side_effects(paddle: MagicMock, db: FakeDB) -> None:
    """Rejected requests must never reach the DB or create/alter Paddle state."""
    assert db.queries == []
    paddle.create_customer.assert_not_awaited()
    paddle.create_portal_session.assert_not_awaited()
    paddle.cancel_subscription.assert_not_awaited()
    paddle.reactivate_subscription.assert_not_awaited()
    paddle.update_subscription_tier.assert_not_awaited()
    paddle.get_transaction_invoice_url.assert_not_awaited()


@pytest.mark.parametrize("method,path,body", AUTHENTICATED_ROUTES)
def test_tokenless_request_is_401_with_real_auth(
    method: str,
    path: str,
    body: Optional[dict[str, Any]],
    real_auth_client: TestClient,
    paddle: MagicMock,
    db: FakeDB,
) -> None:
    """No headers at all: 401 even when the dev middleware defaults to tenant 1."""
    response = _call(real_auth_client, method, path, body)

    assert response.status_code == 401, response.text
    _assert_no_side_effects(paddle, db)


@pytest.mark.parametrize("method,path,body", AUTHENTICATED_ROUTES)
def test_tenant_header_only_is_401_with_real_auth(
    method: str,
    path: str,
    body: Optional[dict[str, Any]],
    real_auth_client: TestClient,
    paddle: MagicMock,
    db: FakeDB,
) -> None:
    """An unauthenticated ``X-Tenant-ID`` header must not grant access to a tenant."""
    response = _call(real_auth_client, method, path, body, headers={"X-Tenant-ID": str(TENANT_ID)})

    assert response.status_code == 401, response.text
    _assert_no_side_effects(paddle, db)


@pytest.mark.parametrize("method,path,body", AUTHENTICATED_ROUTES)
def test_garbage_bearer_token_is_401_with_real_auth(
    method: str,
    path: str,
    body: Optional[dict[str, Any]],
    real_auth_client: TestClient,
    paddle: MagicMock,
    db: FakeDB,
) -> None:
    headers = {"Authorization": "Bearer not-a-jwt", "X-Tenant-ID": str(TENANT_ID)}
    response = _call(real_auth_client, method, path, body, headers=headers)

    assert response.status_code == 401, response.text
    _assert_no_side_effects(paddle, db)


def test_valid_jwt_derives_tenant_from_token_not_header(
    real_auth_client: TestClient,
    paddle: MagicMock,
    sync_mocks: SimpleNamespace,
    admin_user: SimpleNamespace,
) -> None:
    """A signed token for tenant 7 wins over a conflicting ``X-Tenant-ID`` header."""
    headers = {**_bearer(admin_user.id, TENANT_ID, UserRole.ADMIN), "X-Tenant-ID": "999"}

    response = real_auth_client.post(
        f"{BASE}/checkout-session", json={"tier": "starter"}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["custom_data"]["tenant_id"] == str(TENANT_ID)
    paddle.create_customer.assert_awaited_once_with(
        email="owner@acme.test", name="Acme Inc", tenant_id=TENANT_ID
    )


def test_valid_jwt_for_non_admin_cannot_checkout(
    real_auth_client: TestClient,
    paddle: MagicMock,
    sync_mocks: SimpleNamespace,
    admin_user: SimpleNamespace,
) -> None:
    admin_user.role = UserRole.ANALYST

    response = real_auth_client.post(
        f"{BASE}/checkout-session",
        json={"tier": "starter"},
        headers=_bearer(admin_user.id, TENANT_ID, UserRole.ANALYST),
    )

    assert response.status_code == 403
    paddle.create_customer.assert_not_awaited()


@pytest.mark.parametrize("method,path,body", AUTHENTICATED_ROUTES)
def test_missing_credentials_is_401_on_every_route(
    method: str,
    path: str,
    body: Optional[dict[str, Any]],
    client: TestClient,
    paddle: MagicMock,
    db: FakeDB,
) -> None:
    response = _call(client, method, path, body, headers=_headers(None))

    assert response.status_code == 401, response.text
    _assert_no_side_effects(paddle, db)


@pytest.mark.parametrize("method,path,body", MUTATING_ROUTES)
@pytest.mark.parametrize("role", [UserRole.MANAGER, UserRole.ANALYST, UserRole.VIEWER])
def test_non_admin_cannot_change_billing(
    role: UserRole,
    method: str,
    path: str,
    body: Optional[dict[str, Any]],
    client: TestClient,
    paddle: MagicMock,
    sync_mocks: SimpleNamespace,
    db: FakeDB,
    tenant: SimpleNamespace,
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    tenant.paddle_subscription_id = SUBSCRIPTION_ID

    response = _call(client, method, path, body, headers=_headers(role=role))

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == f"Role {role.value} not authorized for this action"
    _assert_no_side_effects(paddle, db)


@pytest.mark.parametrize("method,path,body", MUTATING_ROUTES)
@pytest.mark.parametrize("role", [UserRole.ADMIN, UserRole.SUPERADMIN])
def test_admin_roles_can_change_billing(
    role: UserRole,
    method: str,
    path: str,
    body: Optional[dict[str, Any]],
    client: TestClient,
    paddle: MagicMock,
    sync_mocks: SimpleNamespace,
    tenant: SimpleNamespace,
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID
    tenant.paddle_subscription_id = SUBSCRIPTION_ID
    paddle.get_subscription.return_value = _paddle_subscription(cancel_at_period_end=True)

    response = _call(client, method, path, body, headers=_headers(role=role))

    assert response.status_code == 200, response.text


@pytest.mark.parametrize("method,path,body", READ_ROUTES)
@pytest.mark.parametrize("role", [UserRole.MANAGER, UserRole.ANALYST, UserRole.VIEWER])
def test_any_tenant_member_can_read_billing(
    role: UserRole,
    method: str,
    path: str,
    body: Optional[dict[str, Any]],
    client: TestClient,
    paddle: MagicMock,
    tenant: SimpleNamespace,
) -> None:
    tenant.paddle_customer_id = CUSTOMER_ID

    response = _call(client, method, path, body, headers=_headers(role=role))

    assert response.status_code == 200, response.text


def test_x_tenant_id_header_is_ignored_for_authenticated_user(
    client: TestClient, paddle: MagicMock, sync_mocks: SimpleNamespace
) -> None:
    """The tenant comes from the verified user, never from ``X-Tenant-ID``."""
    headers = {**_headers(), "X-Tenant-ID": "999"}

    response = client.post(f"{BASE}/checkout-session", json={"tier": "starter"}, headers=headers)

    assert response.status_code == 200, response.text
    assert response.json()["data"]["custom_data"]["tenant_id"] == str(TENANT_ID)
