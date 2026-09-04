# =============================================================================
# Stratum AI - Meta App Review callback endpoint tests
# =============================================================================
"""
Unit tests for ``app.api.v1.endpoints.meta_callbacks``.

No network and no database: the router is mounted on a minimal FastAPI app and
``async_session_maker`` is replaced by an in-memory fake session, exactly like
``test_paddle_webhook``. Signed requests are built the way Meta builds them, so
the real verifier runs on every call.
"""

import hashlib
import hmac
import json
import logging
import time
from types import SimpleNamespace
from typing import Any, Self
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.sql import Delete, Select, Update

from app.api.v1.endpoints import meta_callbacks
from app.core.config import settings
from app.middleware.tenant import PUBLIC_ENDPOINTS, is_public_endpoint
from app.models import (
    APIKey,
    AuditAction,
    AuditLog,
    DataDeletionStatus,
    MetaDataDeletionRequest,
    NotificationPreference,
    User,
)
from app.models.campaign_builder import ConnectionStatus, TenantPlatformConnection
from tests.unit.test_meta_signed_request import _b64url

pytestmark = pytest.mark.unit

APP_SECRET = "meta-app-secret-do-not-log"
OTHER_SECRET = "not-the-right-secret"
META_USER_ID = "10223344556677889"
TENANT_ID = 42
GRANTING_USER_ID = 7

DEAUTHORIZE_URL = "/api/v1/meta/deauthorize"
DATA_DELETION_URL = "/api/v1/meta/data-deletion"
STATUS_URL = "/api/v1/meta/data-deletion/status"


# =============================================================================
# Helpers
# =============================================================================


def _signed_request(
    user_id: str | None = META_USER_ID,
    secret: str = APP_SECRET,
    algorithm: str = "HMAC-SHA256",
    issued_at: int | None = None,
) -> str:
    """
    Build a signed_request exactly as Meta does.

    ``issued_at`` defaults to now: the verifier bounds how far a signed request
    may sit from the current time, so a hardcoded timestamp would make the
    whole suite start failing on a fixed date.
    """
    payload: dict[str, Any] = {
        "algorithm": algorithm,
        "issued_at": int(time.time()) if issued_at is None else issued_at,
    }
    if user_id is not None:
        payload["user_id"] = user_id
    encoded = _b64url(json.dumps(payload).encode("utf-8"))
    signature = hmac.new(
        secret.encode(), encoded.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{_b64url(signature)}.{encoded}"


def _make_connection(
    *,
    platform_user_id: str | None = META_USER_ID,
    granted_by_user_id: int | None = GRANTING_USER_ID,
) -> SimpleNamespace:
    """A connected Meta ``TenantPlatformConnection`` stand-in holding tokens."""
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=TENANT_ID,
        platform="meta",
        platform_user_id=platform_user_id,
        status=ConnectionStatus.CONNECTED.value,
        access_token_encrypted="gAAAAAB-encrypted-access-token",
        refresh_token_encrypted="gAAAAAB-encrypted-refresh-token",
        token_ref="secrets://meta/42",
        token_expires_at="2026-12-01T00:00:00Z",
        last_error=None,
        error_count=0,
        granted_by_user_id=granted_by_user_id,
    )


def _make_user(user_id: int = GRANTING_USER_ID) -> SimpleNamespace:
    """A Stratum user stand-in carrying the fields the erasure touches."""
    return SimpleNamespace(
        id=user_id,
        tenant_id=TENANT_ID,
        email="enc:owner@acme.test",
        email_hash="hash",
        full_name="enc:Olive Owner",
        phone="enc:+15550100",
        avatar_url="https://cdn.example/avatar.png",
        is_active=True,
        gdpr_anonymized_at=None,
        preferences={"theme": "dark"},
    )


def _make_deletion_record(
    code: str = "a" * 32,
    request_status: str = DataDeletionStatus.COMPLETED.value,
    connections_cleared: int = 1,
) -> SimpleNamespace:
    """A stored deletion request stand-in for the status endpoint."""
    from datetime import UTC, datetime

    return SimpleNamespace(
        id=uuid4(),
        confirmation_code=code,
        meta_user_id=META_USER_ID,
        tenant_id=TENANT_ID,
        status=request_status,
        connections_cleared=connections_cleared,
        last_error=None,
        requested_at=datetime(2026, 9, 5, 12, 0, tzinfo=UTC),
        completed_at=datetime(2026, 9, 5, 12, 0, 1, tzinfo=UTC),
    )


class _Result:
    """Minimal stand-in for a SQLAlchemy ``Result``."""

    def __init__(
        self, scalar: Any = None, rows: list[Any] | None = None, rowcount: int = 0
    ) -> None:
        self._scalar = scalar
        self._rows = rows or []
        self.rowcount = rowcount

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class _NestedTransaction:
    """Stand-in for ``session.begin_nested()``."""

    def __init__(self, session: "FakeSession") -> None:
        self._session = session

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: type[BaseException] | None, *_: object) -> bool:
        if exc_type is not None:
            self._session.savepoint_rolled_back = True
        return False


class FakeSession:
    """In-memory ``AsyncSession`` stand-in bound through ``async_session_maker``."""

    def __init__(
        self,
        connections: list[Any] | None = None,
        users: list[Any] | None = None,
        deletion_record: Any = None,
        raise_on_erasure: bool = False,
    ) -> None:
        self.connections = connections or []
        self.users = users or []
        self.deletion_record = deletion_record
        self.raise_on_erasure = raise_on_erasure
        self.added: list[Any] = []
        self.committed = False
        self.rolled_back = False
        self.savepoint_rolled_back = False
        self.user_rows_loaded = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def begin_nested(self) -> _NestedTransaction:
        return _NestedTransaction(self)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    def added_of(self, model: type) -> list[Any]:
        """Every object of ``model`` handed to ``add()``."""
        return [obj for obj in self.added if isinstance(obj, model)]

    async def execute(self, stmt: Any) -> _Result:
        if isinstance(stmt, (Delete, Update)):
            # notification_preferences / api_keys deletes, audit_logs update
            return _Result(rowcount=1)

        assert isinstance(stmt, Select), f"unexpected statement: {type(stmt)}"
        entity = stmt.column_descriptions[0]["entity"]

        if entity is TenantPlatformConnection:
            if self.raise_on_erasure:
                raise RuntimeError("simulated database failure")
            return _Result(rows=self.connections)
        if entity is User:
            self.user_rows_loaded = True
            return _Result(rows=self.users)
        if entity is MetaDataDeletionRequest:
            return _Result(scalar=self.deletion_record)
        raise AssertionError(f"unexpected query entity: {entity}")


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def app_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure a Meta app secret for the endpoint under test."""
    monkeypatch.setattr(settings, "meta_app_secret", APP_SECRET)
    monkeypatch.setattr(
        settings, "oauth_redirect_base_url", "https://api.stratumai.test"
    )
    return APP_SECRET


@pytest.fixture
def client(app_secret: str) -> TestClient:
    """Minimal app carrying only the Meta callback router."""
    app = FastAPI()
    app.include_router(meta_callbacks.router, prefix="/api/v1")
    return TestClient(app, raise_server_exceptions=False)


def _bind(monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> FakeSession:
    """Point the endpoint module's session maker at ``session``."""
    monkeypatch.setattr(meta_callbacks, "async_session_maker", lambda: session)
    return session


# =============================================================================
# Routing and public exemption
# =============================================================================


def test_all_three_paths_are_registered_as_public() -> None:
    """The middleware and the router guard both let Meta through."""
    for url in (DEAUTHORIZE_URL, DATA_DELETION_URL, STATUS_URL):
        assert url in PUBLIC_ENDPOINTS, f"{url} is not in PUBLIC_ENDPOINTS"
        assert is_public_endpoint(url), f"{url} is not exempt from the router guard"


def test_no_other_meta_path_became_public() -> None:
    """The exemption is three exact paths, not a prefix that could widen."""
    assert not is_public_endpoint("/api/v1/meta/data-deletion/status/extra")
    assert not is_public_endpoint("/api/v1/meta")
    assert not is_public_endpoint("/api/v1/meta/anything-else")


def test_router_paths_match_the_public_list() -> None:
    """A renamed route cannot silently drift out of the public list."""
    assert sorted(route.path for route in meta_callbacks.router.routes) == [
        "/meta/data-deletion",
        "/meta/data-deletion/status",
        "/meta/deauthorize",
    ]


@pytest.mark.parametrize(
    "method,url",
    [
        ("POST", DEAUTHORIZE_URL),
        ("POST", DATA_DELETION_URL),
        ("GET", STATUS_URL),
    ],
)
def test_routes_answer_without_an_authorization_header(
    method: str, url: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    All three are Meta-to-server calls, so none may demand a bearer token.

    Anything other than 401/403 proves the request reached the handler's own
    credential check (the signed request, or the confirmation code).
    """
    _bind(monkeypatch, FakeSession())

    response = client.request(method, url)

    assert response.status_code not in (401, 403), response.text
    assert "Authorization" not in client.headers


# =============================================================================
# Deauthorize callback
# =============================================================================


def test_deauthorize_clears_tokens_and_marks_disconnected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The connection is severed and every stored token ciphertext is wiped."""
    connection = _make_connection()
    session = _bind(monkeypatch, FakeSession(connections=[connection]))

    response = client.post(DEAUTHORIZE_URL, data={"signed_request": _signed_request()})

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "disconnected", "connections_cleared": 1}

    assert connection.status == ConnectionStatus.DISCONNECTED.value
    assert connection.access_token_encrypted is None
    assert connection.refresh_token_encrypted is None
    assert connection.token_ref is None
    assert connection.token_expires_at is None
    assert session.committed is True


def test_deauthorize_writes_an_audit_row(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Severing a connection is an audited event, attributed to no user."""
    connection = _make_connection()
    session = _bind(monkeypatch, FakeSession(connections=[connection]))

    client.post(DEAUTHORIZE_URL, data={"signed_request": _signed_request()})

    audit_rows = session.added_of(AuditLog)
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row.tenant_id == TENANT_ID
    assert row.user_id is None  # Meta acted, not a Stratum user
    assert row.action == AuditAction.UPDATE
    assert row.resource_type == "tenant_platform_connection"
    assert row.resource_id == str(connection.id)
    assert row.new_value["tokens_cleared"] is True
    assert row.new_value["reason"] == "meta_deauthorize_callback"


def test_deauthorize_severs_every_connection_the_meta_user_authorised(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One person may have connected several tenants; all of them are severed."""
    first, second = _make_connection(), _make_connection()
    second.tenant_id = 99
    session = _bind(monkeypatch, FakeSession(connections=[first, second]))

    response = client.post(DEAUTHORIZE_URL, data={"signed_request": _signed_request()})

    assert response.json()["connections_cleared"] == 2
    assert first.access_token_encrypted is None
    assert second.access_token_encrypted is None
    assert len(session.added_of(AuditLog)) == 2


def test_deauthorize_returns_200_for_an_unknown_meta_user(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    An unknown Meta user is not an error and must never 500.

    Meta retries every non-2xx, and a user we hold nothing for will never start
    matching, so a retry loop would run forever.
    """
    session = _bind(monkeypatch, FakeSession(connections=[]))

    response = client.post(
        DEAUTHORIZE_URL, data={"signed_request": _signed_request(user_id="999999999")}
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "no_connection", "connections_cleared": 0}
    assert session.added == []


def test_deauthorize_500s_on_a_real_database_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuine failure answers 5xx so Meta does retry it."""
    session = _bind(monkeypatch, FakeSession(raise_on_erasure=True))

    response = client.post(DEAUTHORIZE_URL, data={"signed_request": _signed_request()})

    assert response.status_code == 500
    assert session.rolled_back is True


@pytest.mark.parametrize(
    "body,expected",
    [
        ({}, 400),  # no signed_request at all
        ({"signed_request": "garbage"}, 400),  # malformed
        ({"signed_request": _signed_request(secret=OTHER_SECRET)}, 400),  # wrong secret
        ({"signed_request": _signed_request(algorithm="none")}, 400),  # downgraded
        ({"signed_request": _signed_request(user_id=None)}, 400),  # no user_id
    ],
)
def test_deauthorize_rejects_unverified_requests(
    body: dict[str, str],
    expected: int,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is touched unless the signature verifies."""
    session = _bind(monkeypatch, FakeSession(connections=[_make_connection()]))

    response = client.post(DEAUTHORIZE_URL, data=body)

    assert response.status_code == expected, response.text
    assert session.added == []
    assert session.committed is False


def test_deauthorize_answers_503_without_a_configured_app_secret(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a secret nothing can be verified, so nothing is processed."""
    monkeypatch.setattr(settings, "meta_app_secret", None)
    session = _bind(monkeypatch, FakeSession(connections=[_make_connection()]))

    response = client.post(DEAUTHORIZE_URL, data={"signed_request": _signed_request()})

    assert response.status_code == 503
    assert session.committed is False


@pytest.mark.parametrize("url", [DEAUTHORIZE_URL, DATA_DELETION_URL])
def test_a_json_body_is_rejected_so_the_audit_trail_cannot_capture_it(
    url: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Only form bodies are read, and that is a security property, not a nicety.

    ``AuditMiddleware`` json-parses the body of every POST and stores it on a
    2xx. The signed_request IS the credential on these callbacks, so a JSON
    branch would persist it verbatim. Meta posts form-encoded, so nothing real
    is lost by refusing JSON.
    """
    connection = _make_connection()
    session = _bind(monkeypatch, FakeSession(connections=[connection]))

    response = client.post(url, json={"signed_request": _signed_request()})

    assert response.status_code == 400, response.text
    assert connection.access_token_encrypted is not None
    assert session.added == []


def test_multipart_form_bodies_are_still_accepted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Some app configurations post multipart; Starlette's form parser covers it."""
    connection = _make_connection()
    _bind(monkeypatch, FakeSession(connections=[connection]))

    response = client.post(
        DEAUTHORIZE_URL,
        files={"signed_request": (None, _signed_request())},
    )

    assert response.status_code == 200, response.text
    assert connection.access_token_encrypted is None


def test_the_audit_middleware_redacts_a_signed_request_that_reaches_it() -> None:
    """
    Belt and braces: even if a signed_request reached the audit sanitiser it is
    redacted, so the credential cannot be persisted from any other route.
    """
    from app.middleware.audit import AuditMiddleware

    sanitized = AuditMiddleware._sanitize_for_audit(
        AuditMiddleware, {"signed_request": _signed_request()}
    )

    assert sanitized == {"signed_request": "[REDACTED]"}


# =============================================================================
# Data deletion callback
# =============================================================================


def test_data_deletion_returns_exactly_the_json_meta_requires(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Meta specifies ``{url, confirmation_code}`` - no more, no fewer keys."""
    _bind(
        monkeypatch, FakeSession(connections=[_make_connection()], users=[_make_user()])
    )

    response = client.post(
        DATA_DELETION_URL, data={"signed_request": _signed_request()}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"url", "confirmation_code"}
    assert isinstance(body["url"], str) and body["url"]
    assert isinstance(body["confirmation_code"], str) and body["confirmation_code"]


def test_data_deletion_url_points_at_the_status_endpoint_with_the_code(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The URL handed to Meta must resolve to the page that reports the status."""
    _bind(
        monkeypatch, FakeSession(connections=[_make_connection()], users=[_make_user()])
    )

    body = client.post(
        DATA_DELETION_URL, data={"signed_request": _signed_request()}
    ).json()

    assert body["url"] == (
        f"https://api.stratumai.test{STATUS_URL}?code={body['confirmation_code']}"
    )


def test_confirmation_code_is_unguessable_and_unique(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public status page is protected by the code alone, so it must be random."""
    codes = set()
    for _ in range(5):
        _bind(monkeypatch, FakeSession())
        code = client.post(
            DATA_DELETION_URL, data={"signed_request": _signed_request()}
        ).json()["confirmation_code"]
        assert len(code) == 32, "expected 128 bits of hex"
        codes.add(code)

    assert len(codes) == 5, "confirmation codes repeated"


def test_data_deletion_persists_the_request_record(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record is filed so the status URL can keep reporting on the code."""
    session = _bind(
        monkeypatch, FakeSession(connections=[_make_connection()], users=[_make_user()])
    )

    body = client.post(
        DATA_DELETION_URL, data={"signed_request": _signed_request()}
    ).json()

    records = session.added_of(MetaDataDeletionRequest)
    assert len(records) == 1
    record = records[0]
    assert record.confirmation_code == body["confirmation_code"]
    assert record.meta_user_id == META_USER_ID
    assert record.status == DataDeletionStatus.COMPLETED.value
    assert record.completed_at is not None
    assert record.connections_cleared == 1
    assert record.tenant_id == TENANT_ID
    assert session.committed is True


def test_data_deletion_severs_the_connection_and_wipes_its_tokens(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting the person's data includes the credentials they granted."""
    connection = _make_connection()
    _bind(monkeypatch, FakeSession(connections=[connection], users=[_make_user()]))

    client.post(DATA_DELETION_URL, data={"signed_request": _signed_request()})

    assert connection.status == ConnectionStatus.DISCONNECTED.value
    assert connection.access_token_encrypted is None
    assert connection.refresh_token_encrypted is None


def test_data_deletion_does_not_touch_the_granting_stratum_account(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Meta's callback deletes Meta's data, not the customer's workspace login.

    The Stratum account is an email/password account of the tenant's own -
    usually an administrator's - and anonymising it deactivates the login with
    no undo. Doing that from an unauthenticated third-party callback would lock
    a paying tenant out of its own workspace, so the account is left alone and
    ``POST /api/v1/gdpr/anonymize`` stays the path for erasing one.
    """
    user = _make_user()
    session = _bind(
        monkeypatch, FakeSession(connections=[_make_connection()], users=[user])
    )

    response = client.post(
        DATA_DELETION_URL, data={"signed_request": _signed_request()}
    )

    assert response.status_code == 200, response.text
    assert user.gdpr_anonymized_at is None
    assert user.is_active is True
    assert user.email == "enc:owner@acme.test"
    assert user.email_hash == "hash"
    assert user.full_name == "enc:Olive Owner"
    assert user.preferences == {"theme": "dark"}

    # Not merely unmodified - never even loaded.
    assert session.user_rows_loaded is False
    assert not [
        row for row in session.added_of(AuditLog) if row.action == AuditAction.ANONYMIZE
    ]


def test_data_deletion_never_calls_the_account_erasure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The account-erasure function must not be reachable from this callback.

    Asserted on the module rather than on the outcome, so re-introducing the
    call fails here even if the fake user row happens not to be loaded.
    """
    from app.services import gdpr_erasure

    _bind(
        monkeypatch, FakeSession(connections=[_make_connection()], users=[_make_user()])
    )

    assert not hasattr(
        meta_callbacks, "anonymize_user"
    ), "meta_callbacks imports the account erasure again"

    with patch.object(
        gdpr_erasure, "anonymize_user", wraps=gdpr_erasure.anonymize_user
    ) as spy:
        response = client.post(
            DATA_DELETION_URL, data={"signed_request": _signed_request()}
        )

    assert response.status_code == 200, response.text
    assert spy.call_count == 0


def test_data_deletion_clears_the_meta_user_id_from_the_connection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The app-scoped Meta user id is itself Meta-derived personal data.

    Severing the connection but keeping the ASID would leave the person's Meta
    identifier on file after they asked for it to be deleted.
    """
    connection = _make_connection()
    _bind(monkeypatch, FakeSession(connections=[connection], users=[_make_user()]))

    client.post(DATA_DELETION_URL, data={"signed_request": _signed_request()})

    assert connection.platform_user_id is None


def test_deauthorize_keeps_the_meta_user_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Deauthorising asks to disconnect, not to erase.

    The mapping stays so a later deletion request from the same person can
    still find the row it needs to file against.
    """
    connection = _make_connection()
    _bind(monkeypatch, FakeSession(connections=[connection]))

    client.post(DEAUTHORIZE_URL, data={"signed_request": _signed_request()})

    assert connection.platform_user_id == META_USER_ID


def test_last_error_names_the_cause_that_actually_applied(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ``last_error`` is shown to the customer, so it must not misreport the cause.

    The platform-connection status response returns this field, and telling
    someone who asked for deletion that they "deauthorised the app" is simply
    a false statement about what happened.
    """
    deauthorized = _make_connection()
    _bind(monkeypatch, FakeSession(connections=[deauthorized]))
    client.post(DEAUTHORIZE_URL, data={"signed_request": _signed_request()})

    deleted = _make_connection()
    _bind(monkeypatch, FakeSession(connections=[deleted], users=[_make_user()]))
    client.post(DATA_DELETION_URL, data={"signed_request": _signed_request()})

    assert "deauthorize" in deauthorized.last_error
    assert "deletion" in deleted.last_error
    assert deauthorized.last_error != deleted.last_error


def test_data_deletion_still_answers_meta_when_nothing_matches(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    An unknown Meta user still gets a code and a URL.

    Meta requires the response shape unconditionally, and 'we hold nothing for
    you' is a completed request, not a failure.
    """
    session = _bind(monkeypatch, FakeSession(connections=[]))

    response = client.post(
        DATA_DELETION_URL, data={"signed_request": _signed_request(user_id="404404404")}
    )

    assert response.status_code == 200
    assert set(response.json()) == {"url", "confirmation_code"}

    record = session.added_of(MetaDataDeletionRequest)[0]
    assert record.status == DataDeletionStatus.COMPLETED.value
    assert record.connections_cleared == 0
    assert record.tenant_id is None


def test_a_failed_erasure_is_recorded_as_failed_not_hidden(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record says ``failed`` and the savepoint rolled back; Meta still gets a code."""
    session = _bind(monkeypatch, FakeSession(raise_on_erasure=True))

    response = client.post(
        DATA_DELETION_URL, data={"signed_request": _signed_request()}
    )

    assert response.status_code == 200, response.text
    record = session.added_of(MetaDataDeletionRequest)[0]
    assert record.status == DataDeletionStatus.FAILED.value
    assert record.completed_at is None
    assert session.savepoint_rolled_back is True
    # The stored cause names the exception type only - never a message that
    # could carry a row, a query or a credential.
    assert "RuntimeError" in record.last_error
    assert "simulated database failure" not in record.last_error


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"signed_request": "not.avalidsignedrequest"},
        {"signed_request": _signed_request(secret=OTHER_SECRET)},
    ],
)
def test_data_deletion_rejects_unverified_requests(
    body: dict[str, str], client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No code is issued and no row is written for an unsigned caller."""
    session = _bind(monkeypatch, FakeSession(connections=[_make_connection()]))

    response = client.post(DATA_DELETION_URL, data=body)

    assert response.status_code == 400, response.text
    assert session.added == []


# =============================================================================
# The status URL handed back to Meta
# =============================================================================


@pytest.mark.parametrize(
    "configured",
    [
        "",
        "   ",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://app.localhost",
    ],
)
def test_status_url_falls_back_to_the_request_origin_when_unusable(
    configured: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    An unset or loopback origin must never be handed to Meta.

    ``oauth_redirect_base_url`` defaults to ``http://localhost:8000``, so an
    operator who never set it would otherwise answer Meta with a dead link -
    and Meta's contract is that this URL shows the person the status of their
    request. The origin of the request Meta just made is used instead: a call
    that actually arrived proves that host is reachable.
    """
    monkeypatch.setattr(settings, "oauth_redirect_base_url", configured)
    _bind(monkeypatch, FakeSession())

    body = client.post(
        DATA_DELETION_URL, data={"signed_request": _signed_request()}
    ).json()

    assert "localhost" not in body["url"]
    assert "127.0.0.1" not in body["url"]
    assert body["url"].endswith(f"{STATUS_URL}?code={body['confirmation_code']}")
    # TestClient's default base_url is the origin the request arrived on.
    assert body["url"].startswith("http://testserver")


def test_status_url_prefers_the_configured_public_origin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real configured origin still wins over the request's own host."""
    monkeypatch.setattr(settings, "oauth_redirect_base_url", "https://api.acme.test/")
    _bind(monkeypatch, FakeSession())

    body = client.post(
        DATA_DELETION_URL, data={"signed_request": _signed_request()}
    ).json()

    assert body["url"] == (
        f"https://api.acme.test{STATUS_URL}?code={body['confirmation_code']}"
    )


# =============================================================================
# Freshness and idempotency
# =============================================================================


@pytest.mark.parametrize("url", [DEAUTHORIZE_URL, DATA_DELETION_URL])
def test_a_stale_signed_request_is_rejected(
    url: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A correctly signed but old request must not still work.

    A signed_request is not a Meta-only secret - anyone who authorised the app
    can obtain one for their own ASID from the JS SDK - so without a time bound
    one captured string would replay forever.
    """
    session = _bind(monkeypatch, FakeSession(connections=[_make_connection()]))

    response = client.post(
        url,
        data={"signed_request": _signed_request(issued_at=int(time.time()) - 86_400)},
    )

    assert response.status_code == 400, response.text
    assert session.added == []
    assert session.committed is False


@pytest.mark.parametrize("url", [DEAUTHORIZE_URL, DATA_DELETION_URL])
def test_a_far_future_signed_request_is_rejected_too(
    url: str, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window is symmetric, so a forward-dated timestamp buys nothing."""
    session = _bind(monkeypatch, FakeSession(connections=[_make_connection()]))

    response = client.post(
        url,
        data={"signed_request": _signed_request(issued_at=int(time.time()) + 86_400)},
    )

    assert response.status_code == 400, response.text
    assert session.added == []


def test_a_repeat_deletion_request_reuses_the_code_already_on_file(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Redelivery, a double click or a replay must not file a second request.

    Without this, one caller could mint unbounded rows and re-run the erasure
    on every one.
    """
    existing = _make_deletion_record(code="b" * 32)
    connection = _make_connection()
    session = _bind(
        monkeypatch,
        FakeSession(connections=[connection], deletion_record=existing),
    )

    response = client.post(
        DATA_DELETION_URL, data={"signed_request": _signed_request()}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["confirmation_code"] == existing.confirmation_code
    assert body["url"].endswith(f"code={existing.confirmation_code}")

    # Nothing was filed and nothing was re-severed.
    assert session.added_of(MetaDataDeletionRequest) == []
    assert connection.status == ConnectionStatus.CONNECTED.value
    assert connection.access_token_encrypted is not None


def test_the_dedupe_lookup_is_scoped_and_excludes_failures() -> None:
    """
    The reuse query is bounded three ways, and each one matters.

    Same Meta user only (never someone else's code), inside the window, and
    never a failed request - a retry after a failure should genuinely retry
    rather than be answered with the code that did not work.
    """
    import asyncio
    from datetime import UTC, datetime

    session = FakeSession()
    captured: list[Any] = []

    original_execute = session.execute

    async def _spy(stmt: Any) -> Any:
        captured.append(stmt)
        return await original_execute(stmt)

    session.execute = _spy  # type: ignore[method-assign]

    asyncio.run(
        meta_callbacks.find_recent_request(
            session, META_USER_ID, datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
        )
    )

    rendered = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
    assert f"meta_data_deletion_request.meta_user_id = '{META_USER_ID}'" in rendered
    assert "status != 'failed'" in rendered
    assert "requested_at >= " in rendered


# =============================================================================
# Public status page
# =============================================================================


def test_confirmation_code_round_trips_to_the_status_endpoint(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The code Meta was given resolves on the URL Meta was given."""
    write_session = _bind(
        monkeypatch, FakeSession(connections=[_make_connection()], users=[_make_user()])
    )
    body = client.post(
        DATA_DELETION_URL, data={"signed_request": _signed_request()}
    ).json()

    stored = write_session.added_of(MetaDataDeletionRequest)[0]
    _bind(monkeypatch, FakeSession(deletion_record=stored))

    response = client.get(STATUS_URL, params={"code": body["confirmation_code"]})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["confirmation_code"] == body["confirmation_code"]
    assert payload["status"] == DataDeletionStatus.COMPLETED.value
    assert payload["message"]


@pytest.mark.parametrize(
    "params",
    [{"code": "0" * 32}, {"code": "unknown"}, {"code": ""}, {}],
)
def test_unknown_code_returns_a_generic_not_found(
    params: dict[str, str], client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Every code we do not hold gets the identical 404.

    A different status or message per code would turn the page into an oracle
    for which confirmation codes exist.
    """
    _bind(monkeypatch, FakeSession(deletion_record=None))

    response = client.get(STATUS_URL, params=params)

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == meta_callbacks.UNKNOWN_CODE_DETAIL


def test_status_response_leaks_no_user_data(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page reports a status, never who the person is or what was touched."""
    record = _make_deletion_record()
    _bind(monkeypatch, FakeSession(deletion_record=record))

    response = client.get(STATUS_URL, params={"code": record.confirmation_code})

    body = response.text
    payload = response.json()

    assert set(payload) == {
        "confirmation_code",
        "status",
        "requested_at",
        "completed_at",
        "message",
    }
    assert META_USER_ID not in body
    assert str(TENANT_ID) not in payload["confirmation_code"]
    assert "meta_user_id" not in body
    assert "tenant" not in body.lower()
    assert "connections_cleared" not in body
    assert "users_anonymized" not in body


def test_status_page_renders_html_for_a_browser(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Meta requires the URL to show a human-readable explanation."""
    record = _make_deletion_record()
    _bind(monkeypatch, FakeSession(deletion_record=record))

    response = client.get(
        STATUS_URL,
        params={"code": record.confirmation_code},
        headers={"Accept": "text/html"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<h1>Data deletion request</h1>" in response.text
    assert record.confirmation_code in response.text
    assert META_USER_ID not in response.text


def test_unknown_code_renders_the_same_generic_html(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The browser view is no more revealing than the JSON one."""
    _bind(monkeypatch, FakeSession(deletion_record=None))

    response = client.get(
        STATUS_URL, params={"code": "z" * 32}, headers={"Accept": "text/html"}
    )

    assert response.status_code == 404
    assert meta_callbacks.UNKNOWN_CODE_DETAIL in response.text
    assert "z" * 32 not in response.text


def test_a_request_that_matched_nothing_does_not_claim_an_erasure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    "Completed" with nothing matched must say so, not describe a deletion.

    This is the live outcome for every connection made before the Meta user id
    was stored, and for an App Review reviewer testing with a Meta test user
    that has no Stratum connection. Meta asks this page for "a legitimate
    justification for any refusal to delete"; holding nothing is that
    justification, and claiming tokens were erased would be false.
    """
    record = _make_deletion_record(connections_cleared=0)
    _bind(monkeypatch, FakeSession(deletion_record=record))

    message = client.get(STATUS_URL, params={"code": record.confirmation_code}).json()[
        "message"
    ]

    assert message == meta_callbacks.NOTHING_HELD_MESSAGE
    assert "no data" in message.lower()
    for claim in ("has been disconnected", "have been erased", "anonymised"):
        assert claim not in message


def test_a_request_that_erased_something_says_what_it_erased(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The completion wording is kept for the case it is actually true of."""
    record = _make_deletion_record(connections_cleared=1)
    _bind(monkeypatch, FakeSession(deletion_record=record))

    message = client.get(STATUS_URL, params={"code": record.confirmation_code}).json()[
        "message"
    ]

    assert "disconnected" in message
    assert "erased" in message
    # The Stratum account is no longer part of this erasure, so the page must
    # not go on claiming it was anonymised.
    assert "anonymised" not in message


def test_every_stored_status_has_a_human_readable_message() -> None:
    """No status can reach the page without an explanation Meta would accept."""
    for member in DataDeletionStatus:
        assert meta_callbacks.STATUS_MESSAGES[member.value].strip()


# =============================================================================
# The app secret never leaks
# =============================================================================


@pytest.mark.parametrize(
    "url,body",
    [
        (DEAUTHORIZE_URL, {"signed_request": _signed_request(secret=OTHER_SECRET)}),
        (DEAUTHORIZE_URL, {"signed_request": "totally.bogus"}),
        (DEAUTHORIZE_URL, {}),
        (DATA_DELETION_URL, {"signed_request": _signed_request(secret=OTHER_SECRET)}),
        (DATA_DELETION_URL, {"signed_request": "totally.bogus"}),
    ],
)
def test_app_secret_never_appears_in_a_log_record_or_response(
    url: str,
    body: dict[str, str],
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    A rejected callback logs a warning; the secret must not be in it.

    These endpoints are public, so their logs are the most likely place for a
    credential to escape.
    """
    _bind(monkeypatch, FakeSession())

    with caplog.at_level(logging.DEBUG):
        response = client.post(url, data=body)

    assert response.status_code in (400, 503)
    assert APP_SECRET not in response.text

    logged = "\n".join(
        [record.getMessage() for record in caplog.records]
        + [str(record.__dict__) for record in caplog.records]
    )
    assert APP_SECRET not in logged
    assert "meta-app-secret" not in logged


def test_successful_callbacks_do_not_log_the_secret_either(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The happy path is checked too, not only the rejection paths."""
    _bind(
        monkeypatch, FakeSession(connections=[_make_connection()], users=[_make_user()])
    )

    with caplog.at_level(logging.DEBUG):
        client.post(DEAUTHORIZE_URL, data={"signed_request": _signed_request()})

    logged = "\n".join(str(record.__dict__) for record in caplog.records)
    assert APP_SECRET not in logged


def test_no_stored_row_carries_the_secret(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing written to the database may embed the app secret."""
    session = _bind(
        monkeypatch, FakeSession(connections=[_make_connection()], users=[_make_user()])
    )

    client.post(DATA_DELETION_URL, data={"signed_request": _signed_request()})

    for obj in session.added:
        rendered = json.dumps(
            {
                key: str(value)
                for key, value in vars(obj).items()
                if not key.startswith("_")
            }
        )
        assert APP_SECRET not in rendered


# =============================================================================
# The Meta user id mapping
# =============================================================================


def test_the_lookup_binds_the_meta_platform_and_the_signed_user_id() -> None:
    """
    The lookup is an equality match on Meta + that exact app-scoped user id.

    Compiling the statement (rather than trusting the fake session) is what
    proves a connection carrying a NULL ``platform_user_id`` - every connection
    made before this column existed - cannot be matched, and that the callback
    cannot sever some other tenant's row.
    """
    import asyncio

    session = FakeSession(connections=[_make_connection()])
    captured: list[Any] = []

    original_execute = session.execute

    async def _spy(stmt: Any) -> Any:
        captured.append(stmt)
        return await original_execute(stmt)

    session.execute = _spy  # type: ignore[method-assign]

    asyncio.run(meta_callbacks.load_connections_for_meta_user(session, META_USER_ID))

    compiled = captured[0].compile(compile_kwargs={"literal_binds": True})
    rendered = str(compiled)

    assert "tenant_platform_connection.platform = 'meta'" in rendered
    assert f"tenant_platform_connection.platform_user_id = '{META_USER_ID}'" in rendered
    # SQL equality never matches NULL, so a pre-migration row stays untouched.
    assert "IS NULL" not in rendered.upper()


def test_an_unmatched_meta_user_leaves_every_row_untouched(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Nothing is severed and nothing is written when the lookup returns nothing.

    This is the live path for anyone who connected before the app stored the
    Meta user id: the callback reports it honestly instead of guessing.
    """
    session = _bind(monkeypatch, FakeSession(connections=[]))

    response = client.post(DEAUTHORIZE_URL, data={"signed_request": _signed_request()})

    assert response.json() == {"status": "no_connection", "connections_cleared": 0}
    assert session.added == []


# =============================================================================
# The shared erasure still covers what it always did
# =============================================================================


def test_shared_erasure_touches_the_documented_tables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The extracted service performs exactly the erasure gdpr.py used to inline.

    Guards against the refactor quietly dropping a table.
    """
    import asyncio

    from app.services.gdpr_erasure import anonymize_user

    session = FakeSession()
    user = _make_user()
    statements: list[Any] = []

    original_execute = session.execute

    async def _spy(stmt: Any) -> Any:
        statements.append(stmt)
        return await original_execute(stmt)

    session.execute = _spy  # type: ignore[method-assign]

    result = asyncio.run(anonymize_user(session, user, actor_user_id=1))

    rendered = " ".join(str(stmt) for stmt in statements)
    assert "notification_preferences" in rendered
    assert "api_keys" in rendered
    assert "audit_logs" in rendered
    assert result.tables_affected == [
        "users",
        "notification_preferences",
        "api_keys",
        "audit_logs",
    ]
    assert NotificationPreference.__tablename__ == "notification_preferences"
    assert APIKey.__tablename__ == "api_keys"


def test_shared_erasure_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A retried Meta callback must not anonymise twice or double-count."""
    import asyncio

    from app.services.gdpr_erasure import anonymize_user

    session = FakeSession()
    user = _make_user()

    asyncio.run(anonymize_user(session, user))
    first_email = user.email
    second = asyncio.run(anonymize_user(session, user))

    assert second.already_anonymized is True
    assert second.records_modified == 0
    assert user.email == first_email
    assert len(session.added_of(AuditLog)) == 1
