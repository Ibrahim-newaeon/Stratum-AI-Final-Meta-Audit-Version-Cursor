# =============================================================================
# Stratum AI - Subscription Endpoint Tests
# =============================================================================
"""
Unit tests for ``app.api.v1.endpoints.subscription`` and the
``app.services.tenant`` package it imports lazily.

Regression: ``GET /api/v1/subscription/usage-summary`` used to 500 with
``ImportError: cannot import name 'check_tenant_limit'`` because
``app/services/tenant/__init__.py`` re-exported a name ``limits.py`` never
defined. The Paddle downgrade path (``subscription.canceled`` -> plan ``free``)
made that reachable for every canceled tenant, so these tests import the
package from scratch and exercise the endpoint for both free and paid plans.

No network, no database: ``get_subscription_info`` is monkeypatched and the
lazily imported ``get_async_session`` yields an in-memory fake.
"""

import importlib
import sys
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.v1.endpoints import subscription
from app.base_models import Tenant
from app.core.subscription import SubscriptionInfo, SubscriptionStatus
from app.core.tiers import SubscriptionTier

pytestmark = pytest.mark.unit

BASE = "/api/v1/subscription"
TENANT_ID = 7
TENANT_PACKAGE = "app.services.tenant"


# =============================================================================
# Helpers
# =============================================================================


def _purge_tenant_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forget the tenant services package so the next import re-runs ``__init__``.

    ``monkeypatch.delitem`` restores the original module objects on teardown.
    """
    for name in list(sys.modules):
        if name == TENANT_PACKAGE or name.startswith(f"{TENANT_PACKAGE}."):
            monkeypatch.delitem(sys.modules, name, raising=False)


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalar(self) -> Any:
        return self._value


class FakeDB:
    """Answers ``select(Tenant)`` with the tenant and ``count(<model>.id)`` per table."""

    def __init__(self, tenant: Optional[SimpleNamespace], counts: dict[str, int]) -> None:
        self.tenant = tenant
        self.counts = counts
        self.queries: list[Any] = []

    async def execute(self, stmt: Any) -> _Result:
        self.queries.append(stmt)
        expr = stmt.column_descriptions[0]["expr"]
        if expr is Tenant:
            return _Result(self.tenant)
        # func.count(Model.id): the single clause is the counted column.
        column = next(iter(expr.clauses))
        return _Result(self.counts.get(column.table.name, 0))


def _subscription_info(plan: str) -> SubscriptionInfo:
    return SubscriptionInfo(
        tenant_id=TENANT_ID,
        plan=plan,
        tier=SubscriptionTier.STARTER if plan == "free" else SubscriptionTier(plan),
        status=SubscriptionStatus.ACTIVE,
    )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tenant() -> SimpleNamespace:
    return SimpleNamespace(id=TENANT_ID, plan="free", max_users=5, max_campaigns=10)


@pytest.fixture
def db(tenant: SimpleNamespace) -> FakeDB:
    return FakeDB(tenant=tenant, counts={"users": 3, "campaigns": 4})


@pytest.fixture
def client(db: FakeDB, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    async def _session() -> AsyncIterator[FakeDB]:
        yield db

    monkeypatch.setattr("app.db.session.get_async_session", _session)

    app = FastAPI()

    @app.middleware("http")
    async def _inject_tenant(request: Request, call_next: Any) -> Any:
        raw = request.headers.get("X-Test-Tenant")
        if raw:
            request.state.tenant_id = int(raw)
        return await call_next(request)

    app.include_router(subscription.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


# =============================================================================
# app.services.tenant package
# =============================================================================


def test_tenant_services_package_imports_and_exports_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every name in ``__all__`` must exist; a dangling re-export breaks the whole package."""
    _purge_tenant_services(monkeypatch)

    package = importlib.import_module(TENANT_PACKAGE)

    assert "TenantLimitService" in package.__all__
    for name in package.__all__:
        assert getattr(package, name) is not None, f"{TENANT_PACKAGE}.{name} is not defined"


def test_tenant_limits_module_imports_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact lazy import used by the usage-summary endpoint, from a cold cache."""
    _purge_tenant_services(monkeypatch)

    limits = importlib.import_module(f"{TENANT_PACKAGE}.limits")

    assert hasattr(limits, "TenantLimitService")


# =============================================================================
# GET /subscription/usage-summary
# =============================================================================


@pytest.mark.parametrize("plan", ["free", "professional"])
def test_usage_summary_returns_200_for_plan(
    plan: str,
    client: TestClient,
    tenant: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant.plan = plan
    monkeypatch.setattr(
        subscription, "get_subscription_info", AsyncMock(return_value=_subscription_info(plan))
    )
    _purge_tenant_services(monkeypatch)  # force the endpoint's lazy import to run cold

    response = client.get(f"{BASE}/usage-summary", headers={"X-Test-Tenant": str(TENANT_ID)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["subscription"]["plan"] == plan
    assert body["subscription"]["status"] == "active"
    assert body["usage"] == {
        "users": {"used": 3, "limit": 5, "pct_used": 60.0},
        "campaigns": {"used": 4, "limit": 10, "pct_used": 40.0},
    }
    assert body["warning_message"] is None


def test_usage_summary_without_tenant_context_is_401(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subscription, "get_subscription_info", AsyncMock())

    response = client.get(f"{BASE}/usage-summary")

    assert response.status_code == 401
