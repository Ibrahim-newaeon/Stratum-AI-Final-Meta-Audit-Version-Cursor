# =============================================================================
# Stratum AI - Authentication Bypass Regression Tests
# =============================================================================
"""
Regression tests for the authentication and tenant-isolation bypasses.

Four live vulnerabilities are pinned here:

V1  Unauthenticated API access. ``TenantMiddleware`` accepted a raw
    ``X-Tenant-ID`` request header as tenant context, and most endpoint modules
    never depended on ``get_current_user``, so a forged header alone reached
    real tenant data. The headline test walks *every* route mounted on the real
    application (``app.main:app``) and asserts that a request without an
    ``Authorization`` header is rejected. A 422 fails the assertion on purpose:
    reaching schema validation means authentication was already bypassed.

V2  Refresh tokens authenticated as access tokens: the middleware decoded the
    JWT without ever checking the ``type`` claim.

V3  Tenant admin == platform admin: ``tenants.require_admin`` checked the role
    but never compared the tenant in the path with the caller's own tenant, so
    any customer admin could read, re-plan, re-flag or delete any other tenant.

V4  The frontend sent the (now untrusted) ``X-Tenant-ID`` header; the header
    must not authenticate anything.

The review of that fix found five more live holes, pinned here as well:

R1  ``POST /api/v1/auth/register`` was public and copied ``tenant_id`` and
    ``role`` out of the request body, so anyone could mint a superadmin. The
    route is gone; self-service signup is ``POST /auth/signup``.
R2  The ``/ws`` WebSocket accepted anonymous connections and subscribed them to
    whatever ``?tenant_id=`` named. HTTP middleware never runs for a WebSocket
    scope, so the handshake now verifies the token itself.
R3  Every ``integrations`` route took the tenant from a query parameter, so any
    authenticated user could read and mutate another tenant's CRM data.
R4  A tenant ADMIN could PATCH their own plan to enterprise for free; plans and
    feature flags are now platform-role writes reconciled by Paddle.
R5  ``/ws/stats`` and ``/debug/memory/*`` sit outside ``/api/v1`` and answered
    anonymously; the marketing CMS reads sit outside authentication and must
    keep answering anonymously.

No database and no network: the routes under test are rejected before any
handler runs, and the tenant-authorization tests drive the real router behind
the real middleware with an in-memory fake session.
"""

import inspect
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from starlette.routing import WebSocketRoute
from starlette.websockets import WebSocketDisconnect

from app.api.v1.endpoints import integrations, tenants
from app.api.v1.guards import require_authenticated_request
from app.base_models import UserRole
from app.core.security import create_access_token, create_refresh_token
from app.core.websocket import ws_manager
from app.db.session import get_async_session
from app.main import app as real_app
from app.middleware import rate_limit as rate_limit_module
from app.middleware.tenant import (
    PUBLIC_ENDPOINTS,
    TenantMiddleware,
    decode_access_token,
    is_public_endpoint,
)

pytestmark = pytest.mark.unit

API_PREFIX = "/api/v1"
GUARD_DETAIL = "Not authenticated"

TENANT_ONE = 1
TENANT_TWO = 2
ADMIN_USER_ID = 11
SUPERADMIN_USER_ID = 99


# =============================================================================
# Route inventory
# =============================================================================


def _expand(route: Any) -> Iterator[Any]:
    """
    Yield the concrete endpoint objects behind one entry of ``app.routes``.

    FastAPI >= 0.140 keeps included routers as lazy container objects instead of
    flattening them into ``app.routes``; those expose ``effective_route_contexts()``
    which walks the whole tree. Older versions flatten, so the route itself is
    the endpoint.

    WebSocket routes are yielded too (they have a ``path`` but no ``methods``):
    ``/ws`` was the one route the first version of this file could not see, and
    it was subscribing anonymous callers to other tenants' event streams.

    Args:
        route: One entry of ``app.routes``

    Returns:
        Iterator over objects exposing ``path`` (and ``methods`` when HTTP)
    """
    contexts = getattr(route, "effective_route_contexts", None)
    if callable(contexts):
        yield from contexts()
        return
    if getattr(route, "path", None):
        yield route


def _is_websocket(endpoint: Any) -> bool:
    """Report whether a walked route is a WebSocket route rather than HTTP."""
    return isinstance(endpoint, WebSocketRoute) or not getattr(endpoint, "methods", None)


def _all_routes(app: Any) -> list[tuple[str, str]]:
    """
    Collect every (HTTP method, path template) pair mounted on an application.

    Args:
        app: The FastAPI application to walk

    Returns:
        Sorted list of (method, path) pairs, excluding HEAD/OPTIONS
    """
    pairs = {
        (method, endpoint.path)
        for route in app.routes
        for endpoint in _expand(route)
        if not _is_websocket(endpoint)
        for method in endpoint.methods
        if method not in {"HEAD", "OPTIONS"}
    }
    return sorted(pairs)


def _all_websocket_routes(app: Any) -> list[str]:
    """
    Collect every WebSocket path mounted on an application.

    Args:
        app: The FastAPI application to walk

    Returns:
        Sorted list of WebSocket path templates
    """
    return sorted(
        {
            endpoint.path
            for route in app.routes
            for endpoint in _expand(route)
            if _is_websocket(endpoint)
        }
    )


ALL_ROUTES: list[tuple[str, str]] = _all_routes(real_app)
WEBSOCKET_ROUTES: list[str] = _all_websocket_routes(real_app)

# Routes that must never answer an unauthenticated caller.
GUARDED_ROUTES: list[tuple[str, str]] = [
    (method, path)
    for method, path in ALL_ROUTES
    if path.startswith(API_PREFIX) and not is_public_endpoint(path)
]

# Documented public API routes: login/register/refresh/password flows, the
# key-authenticated CDP ingest, the signature-verified Paddle webhook and the
# onboarding agent.
PUBLIC_API_ROUTES: list[tuple[str, str]] = [
    (method, path)
    for method, path in ALL_ROUTES
    if path.startswith(API_PREFIX) and is_public_endpoint(path)
]

# Deliberately open surfaces outside the versioned API: orchestrator probes,
# the OpenAPI schema and its two viewers, and the Prometheus scrape endpoint.
# Everything else outside /api/v1 must carry its own dependency, because
# api_router's guard cannot reach it.
OPEN_NON_API_PATHS = frozenset(
    {
        "/health",
        "/health/live",
        "/health/ready",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
        "/metrics",
    }
)

NON_API_ROUTES: list[tuple[str, str]] = [
    (method, path) for method, path in ALL_ROUTES if not path.startswith(API_PREFIX)
]

OPEN_NON_API_ROUTES: list[tuple[str, str]] = [
    (method, path) for method, path in NON_API_ROUTES if path in OPEN_NON_API_PATHS
]

# /ws/stats (a census of which tenants are online) and /debug/memory/* (process
# internals, plus gc/collect, snapshot and reset writes) answered anonymously.
GUARDED_NON_API_ROUTES: list[tuple[str, str]] = [
    (method, path) for method, path in NON_API_ROUTES if path not in OPEN_NON_API_PATHS
]


def _concrete(path: str) -> str:
    """
    Turn a path template into a callable URL.

    Path parameters are replaced with ``1``; the value is irrelevant because an
    unauthenticated request must be rejected before parameters are parsed.

    Args:
        path: Route path template, e.g. ``/api/v1/campaigns/{campaign_id}``

    Returns:
        A concrete request path
    """
    return re.sub(r"{[^}]+}", "1", path)


def _route_id(method: str, path: str) -> str:
    """Readable parametrisation id so a failure names the offending route."""
    return f"{method} {path}"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(scope="module", autouse=True)
def _unlimited_rate_limit() -> Iterator[None]:
    """
    Disable the in-memory token bucket for this module.

    The all-routes test issues hundreds of requests from one client; the rate
    limiter would answer 429 and mask the authentication status being asserted.
    """
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            rate_limit_module.TokenBucket, "consume", lambda self, tokens=1: True
        )
        yield


@pytest.fixture(scope="module")
def anonymous_client() -> TestClient:
    """
    Client for the real application, never sending credentials.

    The app is used without its lifespan (no database, Redis or WebSocket
    manager start-up) because every request under test is rejected by the
    middleware/guard before a handler runs.
    """
    return TestClient(real_app, raise_server_exceptions=False)


# =============================================================================
# V1 - no route answers an unauthenticated caller
# =============================================================================


def test_route_inventory_is_complete() -> None:
    """Every mounted route is accounted for as guarded, public or out of scope."""
    assert len(ALL_ROUTES) == len(GUARDED_ROUTES) + len(PUBLIC_API_ROUTES) + len(
        NON_API_ROUTES
    )
    assert len(NON_API_ROUTES) == len(OPEN_NON_API_ROUTES) + len(GUARDED_NON_API_ROUTES)
    # Guard against a future refactor that quietly stops discovering routes.
    assert len(GUARDED_ROUTES) > 500
    # The WebSocket routes are now walked as well, so a new @app.websocket
    # cannot be added without landing in the handshake test below.
    assert WEBSOCKET_ROUTES == ["/ws"]


@pytest.mark.parametrize(
    "method,path",
    GUARDED_NON_API_ROUTES,
    ids=[_route_id(method, path) for method, path in GUARDED_NON_API_ROUTES],
)
def test_non_api_routes_outside_the_open_list_are_guarded(
    method: str, path: str, anonymous_client: TestClient
) -> None:
    """/ws/stats and the memory-debug router are not served to anonymous callers."""
    response = anonymous_client.request(method, _concrete(path))

    assert response.status_code in (401, 403), (
        f"{method} {path} answered {response.status_code} with no Authorization "
        "header. Routes outside /api/v1 are not covered by api_router's guard, "
        "so they need their own dependency."
    )


@pytest.mark.parametrize(
    "method,path",
    GUARDED_ROUTES,
    ids=[_route_id(method, path) for method, path in GUARDED_ROUTES],
)
def test_every_non_public_route_rejects_an_unauthenticated_request(
    method: str, path: str, anonymous_client: TestClient
) -> None:
    """No route may answer 2xx (or reach validation) without an access token."""
    response = anonymous_client.request(method, _concrete(path))

    assert response.status_code in (401, 403), (
        f"{method} {path} answered {response.status_code} with no Authorization header. "
        "Anything other than 401/403 - a 422 from schema validation included - means "
        "the request passed authentication."
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/campaigns"),
        ("POST", "/api/v1/campaigns"),
        ("GET", "/api/v1/tenants"),
        ("PATCH", "/api/v1/tenants/1/plan?plan=enterprise"),
        ("GET", "/api/v1/dashboard/overview"),
    ],
)
def test_x_tenant_id_header_alone_does_not_authenticate(
    method: str, path: str, anonymous_client: TestClient
) -> None:
    """V1 reproduction: the forged header used to return live tenant data."""
    response = anonymous_client.request(method, path, headers={"X-Tenant-ID": "1"})

    assert response.status_code == 401, response.text


def test_x_tenant_id_header_is_ignored_by_the_middleware() -> None:
    """The middleware derives no tenant from an unsigned header."""
    middleware = TenantMiddleware(app=real_app)
    request = _bare_request(headers=[(b"x-tenant-id", b"1")])

    claims = middleware._decode_access_token(request)

    assert claims is None
    assert middleware._extract_tenant_id(claims) is None


def test_subdomain_resolution_is_gone() -> None:
    """The subdomain stub (which always returned None) is no longer consulted."""
    assert not hasattr(TenantMiddleware, "_extract_from_subdomain")


# =============================================================================
# V2 - refresh tokens are not access tokens
# =============================================================================


def _bare_request(headers: list[tuple[bytes, bytes]]) -> Request:
    """Build a minimal ASGI request carrying the given raw headers."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/campaigns",
            "raw_path": b"/api/v1/campaigns",
            "query_string": b"",
            "headers": headers,
        }
    )


def test_refresh_token_is_rejected_where_an_access_token_is_required(
    anonymous_client: TestClient,
) -> None:
    """V2: a valid refresh token must not authenticate an API request."""
    refresh_token = create_refresh_token(subject=ADMIN_USER_ID)

    response = anonymous_client.get(
        "/api/v1/campaigns", headers={"Authorization": f"Bearer {refresh_token}"}
    )

    assert response.status_code == 401, response.text


def test_middleware_reads_claims_only_from_access_tokens() -> None:
    """The ``type`` claim decides: refresh yields no identity, access does."""
    middleware = TenantMiddleware(app=real_app)

    refresh_token = create_refresh_token(subject=ADMIN_USER_ID)
    refresh_request = _bare_request(
        headers=[(b"authorization", f"Bearer {refresh_token}".encode())]
    )
    assert middleware._decode_access_token(refresh_request) is None

    access_token = create_access_token(
        subject=ADMIN_USER_ID,
        additional_claims={"tenant_id": TENANT_ONE, "role": UserRole.ADMIN.value},
    )
    access_request = _bare_request(
        headers=[(b"authorization", f"Bearer {access_token}".encode())]
    )
    claims = middleware._decode_access_token(access_request)

    assert claims is not None
    assert middleware._extract_tenant_id(claims) == TENANT_ONE
    assert middleware._extract_user_id(claims) == ADMIN_USER_ID
    assert middleware._extract_role(claims) == UserRole.ADMIN.value


def test_garbage_and_missing_tokens_yield_no_identity(
    anonymous_client: TestClient,
) -> None:
    """An unsigned or malformed bearer token is worth exactly nothing."""
    for header in ({}, {"Authorization": "Bearer not-a-jwt"}, {"Authorization": "Basic x"}):
        response = anonymous_client.get("/api/v1/campaigns", headers=header)
        assert response.status_code == 401, response.text


# =============================================================================
# R1 - the public registration privilege-escalation route is gone
# =============================================================================


def test_public_register_route_no_longer_exists(anonymous_client: TestClient) -> None:
    """
    ``POST /auth/register`` copied tenant_id and role from the body.

    It was in PUBLIC_ENDPOINTS, so an anonymous caller could create a
    role="superadmin" user in any tenant and then log in with it - which
    defeats the middleware, the router guard and the tenant checks at once.
    """
    assert ("POST", "/api/v1/auth/register") not in ALL_ROUTES
    assert "/api/v1/auth/register" not in PUBLIC_ENDPOINTS
    assert not is_public_endpoint("/api/v1/auth/register")

    response = anonymous_client.post(
        "/api/v1/auth/register",
        json={
            "email": "attacker@example.com",
            "password": "Passw0rdX1",
            "tenant_id": TENANT_TWO,
            "role": "superadmin",
        },
    )

    # 404/405: the route does not exist. What must never happen is a 2xx (the
    # user was created) or a 422 (the body reached schema validation).
    assert response.status_code in (404, 405), response.text


def test_no_public_route_accepts_a_role_field() -> None:
    """No route reachable without a token may take a role out of the body."""
    for method, path in PUBLIC_API_ROUTES:
        assert "register" not in path, f"{method} {path} looks like a privilege sink"


# =============================================================================
# R2 - the /ws handshake authenticates
# =============================================================================


def _websocket_endpoint() -> Any:
    """Return the function behind the ``/ws`` WebSocket route."""
    for route in real_app.routes:
        for endpoint in _expand(route):
            if _is_websocket(endpoint) and endpoint.path == "/ws":
                return endpoint.endpoint
    raise AssertionError("/ws route not found")


def test_websocket_no_longer_takes_a_tenant_id_query_parameter() -> None:
    """The subscribed tenant comes from the token claims, never from the URL."""
    parameters = inspect.signature(_websocket_endpoint()).parameters

    assert "tenant_id" not in parameters
    assert "token" in parameters


@pytest.mark.parametrize(
    "query",
    [
        "",
        "?tenant_id=1",
        "?token=not-a-jwt",
        "?token=not-a-jwt&tenant_id=1",
    ],
)
def test_websocket_rejects_connections_without_a_valid_access_token(
    query: str, anonymous_client: TestClient
) -> None:
    """R2: an anonymous or bogus handshake is closed, not subscribed."""
    before = dict(ws_manager._tenant_connections)

    with pytest.raises(WebSocketDisconnect), anonymous_client.websocket_connect(f"/ws{query}"):
        pass

    assert dict(ws_manager._tenant_connections) == before


def test_websocket_rejects_a_refresh_token(anonymous_client: TestClient) -> None:
    """The handshake applies the same access-token rules as the middleware."""
    refresh_token = create_refresh_token(subject=ADMIN_USER_ID)

    with pytest.raises(WebSocketDisconnect), anonymous_client.websocket_connect(
        f"/ws?token={refresh_token}"
    ):
        pass


def test_websocket_subscribes_to_the_tenant_in_the_token_not_the_url(
    anonymous_client: TestClient,
) -> None:
    """A tenant-1 token joins tenant 1 even while asking for tenant 2."""
    token = create_access_token(
        subject=ADMIN_USER_ID,
        additional_claims={"tenant_id": TENANT_ONE, "role": UserRole.ADMIN.value},
    )

    with anonymous_client.websocket_connect(f"/ws?token={token}&tenant_id={TENANT_TWO}"):
        subscribed = {
            tenant_id
            for tenant_id, clients in ws_manager._tenant_connections.items()
            if clients
        }
        assert TENANT_TWO not in subscribed
        assert TENANT_ONE in subscribed


# =============================================================================
# V3 - tenant admins are not platform admins
# =============================================================================


def _tenant_row(tenant_id: int, slug: str, name: str) -> SimpleNamespace:
    """Build a duck-typed tenant record for the fake session."""
    now = datetime(2026, 9, 1, 12, tzinfo=UTC)
    return SimpleNamespace(
        id=tenant_id,
        name=name,
        slug=slug,
        domain=None,
        plan="free",
        plan_expires_at=None,
        max_users=5,
        max_campaigns=10,
        settings={},
        feature_flags={},
        created_at=now,
        updated_at=now,
        is_deleted=False,
    )


class FakeResult:
    """Minimal stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "FakeResult":
        """Return self; the rows are already scalar entities."""
        return self

    def all(self) -> list[Any]:
        """Return every row."""
        return list(self._rows)

    def scalar_one_or_none(self) -> Any | None:
        """Return the first row, or None."""
        return self._rows[0] if self._rows else None

    def scalar(self) -> Any:
        """Return the first row, or None."""
        return self._rows[0] if self._rows else None


class FakeSession:
    """
    In-memory session that honours the tenant filter of the real query.

    ``execute`` compiles the statement the endpoint built and applies its
    ``tenants.id = :id_1`` bind parameter, so the scoping assertions test the
    query the endpoint actually produced rather than a hand-written stub.
    """

    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.statements: list[Any] = []
        self.commits = 0

    async def execute(self, statement: Any) -> FakeResult:
        """Run a select against the in-memory rows."""
        self.statements.append(statement)
        params = statement.compile().params
        descriptions = getattr(statement, "column_descriptions", [])
        entity = descriptions[0].get("entity") if descriptions else None

        if entity is not None and entity.__name__ == "Tenant":
            rows = [row for row in self.rows if not row.is_deleted]
            wanted = params.get("id_1")
            if wanted is not None:
                rows = [row for row in rows if row.id == wanted]
            return FakeResult(rows)

        if entity is not None and entity.__name__ == "User":
            # GET /tenants/{id}/users selects the member rows themselves now,
            # not just a count. This fixture seeds tenants only, so the tenant
            # has no members - which is what the endpoint reports. Returning
            # the old FakeResult([0]) here handed the endpoint a list holding
            # the integer 0 and turned these authorization assertions into 500s.
            return FakeResult([])

        return FakeResult([0])

    async def commit(self) -> None:
        """Record a commit."""
        self.commits += 1

    async def refresh(self, instance: Any) -> None:
        """No-op refresh."""
        return

    def add(self, instance: Any) -> None:
        """No-op add."""
        return


@pytest.fixture
def tenant_session() -> FakeSession:
    """Two tenants: the caller's own (1) and somebody else's (2)."""
    return FakeSession(
        [
            _tenant_row(TENANT_ONE, "acme", "Acme Inc"),
            _tenant_row(TENANT_TWO, "globex", "Globex Corp"),
        ]
    )


@pytest.fixture
def tenants_client(tenant_session: FakeSession) -> TestClient:
    """The real tenants router wired exactly like production."""
    app = FastAPI()
    app.add_middleware(TenantMiddleware)
    guarded = APIRouter(dependencies=[Depends(require_authenticated_request)])
    guarded.include_router(tenants.router, prefix="/tenants")
    app.include_router(guarded, prefix=API_PREFIX)
    app.dependency_overrides[get_async_session] = lambda: tenant_session
    return TestClient(app, raise_server_exceptions=False)


def _auth(user_id: int, role: UserRole, tenant_id: int | None) -> dict[str, str]:
    """Authorization header carrying a real, signed access token."""
    claims: dict[str, Any] = {"role": role.value}
    if tenant_id is not None:
        claims["tenant_id"] = tenant_id
    token = create_access_token(subject=user_id, additional_claims=claims)
    return {"Authorization": f"Bearer {token}"}


def _admin_of_one() -> dict[str, str]:
    """Headers for the admin of tenant 1 (the role every signup gets)."""
    return _auth(ADMIN_USER_ID, UserRole.ADMIN, TENANT_ONE)


def _superadmin() -> dict[str, str]:
    """Headers for a platform superadmin operating without tenant context."""
    return _auth(SUPERADMIN_USER_ID, UserRole.SUPERADMIN, None)


# (method, path template, body) for the handlers a tenant ADMIN may use on
# their own tenant and on no other.
TENANT_SCOPED_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/v1/tenants/{tenant_id}", None),
    ("GET", "/api/v1/tenants/{tenant_id}/users", None),
    ("PATCH", "/api/v1/tenants/{tenant_id}", {"name": "Renamed", "settings": {"x": 1}}),
]

# Entitlement writes: platform role only, on any tenant - including the
# caller's own. PATCH /tenants/{own_id}/plan?plan=enterprise was a free upgrade
# to max_users 100 / max_campaigns 1000 for every customer, because signup
# makes the first user of every tenant an ADMIN, and nothing reconciled the
# write (the plan is owned by the Paddle webhook).
PLATFORM_ONLY_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("PATCH", "/api/v1/tenants/{tenant_id}/plan?plan=enterprise", None),
    ("PATCH", "/api/v1/tenants/{tenant_id}/features", {"autopilot": True}),
]

ALL_TENANT_PATH_ROUTES = TENANT_SCOPED_ROUTES + PLATFORM_ONLY_ROUTES


def _call(
    client: TestClient, method: str, path: str, body: dict[str, Any] | None, headers: dict
) -> Any:
    """Issue ``method path`` with an optional JSON body."""
    return client.request(method, path, json=body, headers=headers)


@pytest.mark.parametrize("method,template,body", ALL_TENANT_PATH_ROUTES)
def test_tenant_admin_cannot_act_on_another_tenant(
    method: str,
    template: str,
    body: dict[str, Any] | None,
    tenants_client: TestClient,
    tenant_session: FakeSession,
) -> None:
    """V3: the admin of tenant 1 is forbidden on tenant 2."""
    path = template.format(tenant_id=TENANT_TWO)

    response = _call(tenants_client, method, path, body, _admin_of_one())

    assert response.status_code == 403, response.text
    assert tenant_session.commits == 0


@pytest.mark.parametrize("method,template,body", TENANT_SCOPED_ROUTES)
def test_tenant_admin_can_act_on_their_own_tenant(
    method: str,
    template: str,
    body: dict[str, Any] | None,
    tenants_client: TestClient,
) -> None:
    """Legitimate callers keep their 200 and their response shape."""
    path = template.format(tenant_id=TENANT_ONE)

    response = _call(tenants_client, method, path, body, _admin_of_one())

    assert response.status_code == 200, response.text
    assert response.json()["success"] is True


@pytest.mark.parametrize("method,template,body", PLATFORM_ONLY_ROUTES)
def test_tenant_admin_cannot_change_their_own_entitlements(
    method: str,
    template: str,
    body: dict[str, Any] | None,
    tenants_client: TestClient,
    tenant_session: FakeSession,
) -> None:
    """R4: the plan and the feature flags are not customer-writable."""
    path = template.format(tenant_id=TENANT_ONE)

    response = _call(tenants_client, method, path, body, _admin_of_one())

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Platform admin access required"
    assert tenant_session.commits == 0
    assert tenant_session.rows[0].plan == "free"
    assert tenant_session.rows[0].max_users == 5


def test_tenant_admin_cannot_create_a_tenant(
    tenants_client: TestClient, tenant_session: FakeSession
) -> None:
    """Creating tenants (with a caller-chosen plan) is a platform action."""
    response = tenants_client.post(
        "/api/v1/tenants",
        json={"name": "Free Enterprise", "slug": "free-enterprise", "plan": "enterprise"},
        headers=_admin_of_one(),
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Platform admin access required"
    assert tenant_session.commits == 0


@pytest.mark.parametrize("method,template,body", ALL_TENANT_PATH_ROUTES)
@pytest.mark.parametrize("target", [TENANT_ONE, TENANT_TWO])
def test_superadmin_can_act_on_any_tenant(
    target: int,
    method: str,
    template: str,
    body: dict[str, Any] | None,
    tenants_client: TestClient,
) -> None:
    """The platform role crosses tenant boundaries, with or without context."""
    path = template.format(tenant_id=target)

    response = _call(tenants_client, method, path, body, _superadmin())

    assert response.status_code == 200, response.text


def test_plan_upgrade_of_another_tenant_is_refused(
    tenants_client: TestClient, tenant_session: FakeSession
) -> None:
    """The reported billing bypass: free enterprise plan on somebody else's tenant."""
    response = tenants_client.patch(
        f"/api/v1/tenants/{TENANT_TWO}/plan?plan=enterprise", headers=_admin_of_one()
    )

    assert response.status_code == 403
    assert tenant_session.rows[1].plan == "free"
    assert tenant_session.commits == 0


def test_tenant_admin_cannot_delete_another_tenant(
    tenants_client: TestClient, tenant_session: FakeSession
) -> None:
    """Deleting a foreign tenant is a 403, not a soft delete."""
    response = tenants_client.delete(
        f"/api/v1/tenants/{TENANT_TWO}", headers=_admin_of_one()
    )

    assert response.status_code == 403
    assert tenant_session.rows[1].is_deleted is False
    assert tenant_session.commits == 0


def test_deleting_your_own_tenant_still_returns_400(tenants_client: TestClient) -> None:
    """The pre-existing self-deletion guard keeps its status code."""
    response = tenants_client.delete(
        f"/api/v1/tenants/{TENANT_ONE}", headers=_admin_of_one()
    )

    assert response.status_code == 400


def test_list_tenants_returns_only_the_callers_tenant_for_an_admin(
    tenants_client: TestClient,
) -> None:
    """V3: ``list_tenants`` used to skip the tenant filter for role admin."""
    response = tenants_client.get("/api/v1/tenants", headers=_admin_of_one())

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [tenant["id"] for tenant in data] == [TENANT_ONE]


def test_list_tenants_returns_every_tenant_for_a_superadmin(
    tenants_client: TestClient,
) -> None:
    """Only the platform role enumerates all customers."""
    response = tenants_client.get("/api/v1/tenants", headers=_superadmin())

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert sorted(tenant["id"] for tenant in data) == [TENANT_ONE, TENANT_TWO]


@pytest.mark.parametrize("method,template,body", ALL_TENANT_PATH_ROUTES)
def test_tenant_routes_reject_unauthenticated_callers(
    method: str,
    template: str,
    body: dict[str, Any] | None,
    tenants_client: TestClient,
) -> None:
    """The router guard fires before any tenant handler, header or not."""
    path = template.format(tenant_id=TENANT_ONE)

    response = _call(tenants_client, method, path, body, {"X-Tenant-ID": str(TENANT_ONE)})

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == GUARD_DETAIL


# =============================================================================
# R3 - the integrations router no longer trusts ?tenant_id=
# =============================================================================


class RecordingSession(FakeSession):
    """Fake session that fails the test if a rejected request reaches the DB."""

    async def execute(self, statement: Any) -> FakeResult:
        """Record the statement and return nothing."""
        self.statements.append(statement)
        return FakeResult([])


@pytest.fixture
def integrations_client() -> Iterator[tuple[TestClient, RecordingSession]]:
    """The real integrations router behind the real middleware and guard."""
    session = RecordingSession([])
    app = FastAPI()
    app.add_middleware(TenantMiddleware)
    guarded = APIRouter(dependencies=[Depends(require_authenticated_request)])
    guarded.include_router(integrations.router)
    app.include_router(guarded, prefix=API_PREFIX)
    app.dependency_overrides[get_async_session] = lambda: session
    yield TestClient(app, raise_server_exceptions=False), session


# A representative read, a status read and a destructive write, all of which
# used to act on whatever tenant the query string named.
CROSS_TENANT_INTEGRATION_ROUTES: list[tuple[str, str]] = [
    ("GET", "/api/v1/integrations/contacts"),
    ("GET", "/api/v1/integrations/deals"),
    ("GET", "/api/v1/integrations/hubspot/status"),
    ("GET", "/api/v1/integrations/crm/status"),
    ("DELETE", "/api/v1/integrations/hubspot/disconnect"),
]


@pytest.mark.parametrize(
    "method,path",
    CROSS_TENANT_INTEGRATION_ROUTES,
    ids=[_route_id(method, path) for method, path in CROSS_TENANT_INTEGRATION_ROUTES],
)
def test_integrations_reject_a_foreign_tenant_id_query_parameter(
    method: str,
    path: str,
    integrations_client: tuple[TestClient, RecordingSession],
) -> None:
    """R3: an analyst of tenant 1 cannot name tenant 2 in the query string."""
    client, session = integrations_client

    response = client.request(
        method,
        f"{path}?tenant_id={TENANT_TWO}",
        headers=_auth(ADMIN_USER_ID, UserRole.ANALYST, TENANT_ONE),
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Access denied"
    assert session.statements == []


def test_integrations_accept_the_callers_own_tenant(
    integrations_client: tuple[TestClient, RecordingSession],
) -> None:
    """A caller naming their own tenant still reaches the handler."""
    client, session = integrations_client

    response = client.get(
        f"/api/v1/integrations/contacts?tenant_id={TENANT_ONE}",
        headers=_auth(ADMIN_USER_ID, UserRole.ANALYST, TENANT_ONE),
    )

    assert response.status_code == 200, response.text
    assert session.statements, "the handler should have queried the database"


def test_integrations_default_to_the_callers_own_tenant(
    integrations_client: tuple[TestClient, RecordingSession],
) -> None:
    """The query parameter is optional: the tenant comes from the token."""
    client, _ = integrations_client

    response = client.get(
        "/api/v1/integrations/contacts",
        headers=_auth(ADMIN_USER_ID, UserRole.ANALYST, TENANT_ONE),
    )

    assert response.status_code == 200, response.text


def test_integrations_let_the_platform_role_target_any_tenant(
    integrations_client: tuple[TestClient, RecordingSession],
) -> None:
    """Superadmin keeps its documented cross-tenant reach."""
    client, _ = integrations_client

    response = client.get(
        f"/api/v1/integrations/contacts?tenant_id={TENANT_TWO}",
        headers=_auth(SUPERADMIN_USER_ID, UserRole.SUPERADMIN, TENANT_ONE),
    )

    assert response.status_code == 200, response.text


def test_no_integrations_handler_declares_its_own_tenant_id_parameter() -> None:
    """Every handler takes the tenant from the resolver, not from the caller."""
    offenders = [
        route.path
        for route in integrations.router.routes
        if "tenant_id" in inspect.signature(route.endpoint).parameters
        and inspect.signature(route.endpoint).parameters["tenant_id"].default is not None
        and getattr(
            inspect.signature(route.endpoint).parameters["tenant_id"].default,
            "dependency",
            None,
        )
        is not integrations.resolve_tenant_id
    ]
    assert offenders == [], offenders

    resolved = [
        route.path
        for route in integrations.router.routes
        if getattr(
            inspect.signature(route.endpoint).parameters.get("tenant_id", None),
            "default",
            None,
        )
        is not None
    ]
    # Non-vacuous: the router really does carry the tenant-scoped handlers.
    assert len(resolved) >= 40, len(resolved)


# =============================================================================
# R5 - shared ML artefacts are not writable by every authenticated user
# =============================================================================


@pytest.mark.parametrize(
    "method,path",
    [
        ("DELETE", "/api/v1/ml/models/roas_predictor"),
        ("POST", "/api/v1/ml/train"),
        ("POST", "/api/v1/ml/generate-sample"),
        ("GET", "/api/v1/ml/training-data"),
    ],
)
def test_ml_artefact_routes_reject_a_non_admin(
    method: str, path: str, anonymous_client: TestClient
) -> None:
    """An analyst of any tenant could delete or retrain shared platform models."""
    response = anonymous_client.request(
        method, path, json={}, headers=_auth(ADMIN_USER_ID, UserRole.ANALYST, TENANT_ONE)
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Admin access required"


# =============================================================================
# Public endpoints keep working
# =============================================================================


def test_health_probe_needs_no_token(anonymous_client: TestClient) -> None:
    """Liveness stays open for orchestrators."""
    response = anonymous_client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_login_reaches_validation_without_a_token(anonymous_client: TestClient) -> None:
    """A 422 here is the proof that login is still public."""
    response = anonymous_client.post("/api/v1/auth/login", json={})

    assert response.status_code == 422, response.text


def test_paddle_webhook_reaches_its_signature_check(anonymous_client: TestClient) -> None:
    """The webhook is public; it authenticates with the Paddle-Signature HMAC."""
    response = anonymous_client.post("/api/v1/webhooks/paddle", json={"event_type": "ping"})

    # 400 = bad/missing signature, 503 = no notification secret configured.
    assert response.status_code in (400, 503), response.text


def test_cdp_ingest_reaches_its_source_key_check(anonymous_client: TestClient) -> None:
    """Ingest is public; it authenticates with X-Source-Key, not a JWT."""
    response = anonymous_client.post("/api/v1/cdp/ingest", json={"events": []})

    # The request reached the endpoint's own credential check (the missing
    # X-Source-Key header), which it could not do if the guard had rejected it.
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail != GUARD_DETAIL
    assert any(error["loc"] == ["header", "X-Source-Key"] for error in detail), detail


def test_documented_public_paths_are_exempt() -> None:
    """The middleware and the router guard share one public-path predicate."""
    for path in PUBLIC_ENDPOINTS:
        assert is_public_endpoint(path)
    assert is_public_endpoint("/api/v1/onboarding-agent/start")
    assert is_public_endpoint("/api/v1/onboarding-agent/status/abc")
    assert not is_public_endpoint("/api/v1/campaigns")
    assert not is_public_endpoint("/api/v1/auth/me")


def test_oauth_platform_callbacks_are_public() -> None:
    """Meta redirects to /oauth/{platform}/callback with no bearer token."""
    assert is_public_endpoint("/api/v1/oauth/meta/callback")
    # Authorize / status must stay authenticated — only the browser callback
    # is exempt.
    assert not is_public_endpoint("/api/v1/oauth/meta/authorize")
    assert not is_public_endpoint("/api/v1/oauth/meta/status")
    assert not is_public_endpoint("/api/v1/oauth/status")
    assert not is_public_endpoint("/api/v1/oauth/meta/callback/extra")


# The logged-out marketing surface: the SPA renders /blog, /blog/:slug, /faq,
# /contact and the landing pricing section without a token, and the CMS
# handlers behind them are documented "(public endpoint)" and read no tenant
# context. They only ever worked because the old client attached X-Tenant-ID to
# every request, so removing that header would have 401'd the marketing site.
PUBLIC_MARKETING_PATHS = [
    "/api/v1/cms/posts",
    "/api/v1/cms/posts/hello-world",
    "/api/v1/cms/categories",
    "/api/v1/cms/tags",
    "/api/v1/cms/contact",
    "/api/v1/landing-cms/subscribe",
]


@pytest.mark.parametrize("path", PUBLIC_MARKETING_PATHS)
def test_marketing_content_paths_are_public(path: str) -> None:
    """R5: the blog, the contact form and the newsletter stay reachable."""
    assert is_public_endpoint(path), path


def test_cms_admin_surface_is_not_public() -> None:
    """The /cms/posts prefix must not widen to the CMS admin routes."""
    for path in (
        "/api/v1/cms/admin/posts",
        "/api/v1/cms/admin/posts/1",
        "/api/v1/cms/admin/categories",
        "/api/v1/cms/admin/contacts",
    ):
        assert not is_public_endpoint(path), path


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/cms/posts"),
        ("GET", "/api/v1/cms/categories"),
        ("GET", "/api/v1/cms/tags"),
    ],
)
def test_marketing_reads_are_not_rejected_by_the_guard(
    method: str, path: str, anonymous_client: TestClient
) -> None:
    """A logged-out visitor reaches the handler (which then needs the DB)."""
    response = anonymous_client.request(method, path)

    assert response.status_code != 401, response.text
    assert response.status_code != 403, response.text


def test_cms_admin_route_still_requires_a_token(anonymous_client: TestClient) -> None:
    """Every CMS write and admin read stays behind the guard."""
    response = anonymous_client.get("/api/v1/cms/admin/posts")

    assert response.status_code == 401, response.text
    assert response.json()["detail"] == GUARD_DETAIL


def test_access_token_helper_is_shared_by_middleware_and_websocket() -> None:
    """One decoder decides what authenticates, for HTTP and for /ws alike."""
    access_token = create_access_token(
        subject=ADMIN_USER_ID,
        additional_claims={"tenant_id": TENANT_ONE, "role": UserRole.ADMIN.value},
    )

    assert decode_access_token(access_token) is not None
    assert decode_access_token(create_refresh_token(subject=ADMIN_USER_ID)) is None
    assert decode_access_token("") is None
    assert decode_access_token("not-a-jwt") is None
