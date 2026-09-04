# =============================================================================
# Stratum AI - OAuth platform_user_id binding
# =============================================================================
"""
The OAuth callback's handling of ``TenantPlatformConnection.platform_user_id``.

That column is what Meta's Deauthorize and Data Deletion callbacks resolve a
person by, so getting it wrong is not a cosmetic bug: a connection carrying
Meta user A's id while ``granted_by_user_id`` points at Stratum user B means a
privacy callback for A acts on B's tenant. These tests pin the one rule that
prevents it - the column is written on every reconnect, including when it could
not be read.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import oauth as oauth_endpoint
from app.models.campaign_builder import AdPlatform

pytestmark = pytest.mark.unit

TENANT_ID = 42
FIRST_USER_ID = 1
SECOND_USER_ID = 2
FIRST_META_USER_ID = "1000000000000001"
SECOND_META_USER_ID = "2000000000000002"


class _Result:
    """Minimal SQLAlchemy ``Result`` stand-in."""

    def __init__(self, scalar: Any) -> None:
        self._scalar = scalar

    def scalar_one_or_none(self) -> Any:
        return self._scalar


class _Session:
    """Async session stand-in returning one preloaded connection row."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.added: list[Any] = []
        self.committed = False

    async def execute(self, _stmt: Any) -> _Result:
        return _Result(self.connection)

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


def _existing_connection() -> SimpleNamespace:
    """A Meta connection already bound to the first person's Meta identity."""
    return SimpleNamespace(
        tenant_id=TENANT_ID,
        platform=AdPlatform.META,
        status=None,
        access_token_encrypted="old-ciphertext",
        refresh_token_encrypted=None,
        token_expires_at=None,
        scopes=[],
        connected_at=None,
        last_refreshed_at=None,
        last_error="stale",
        error_count=3,
        granted_by_user_id=FIRST_USER_ID,
        platform_user_id=FIRST_META_USER_ID,
    )


def _service(fetched_user_id: str | None) -> SimpleNamespace:
    """An OAuth service whose ``/me`` lookup returns ``fetched_user_id``."""
    return SimpleNamespace(
        validate_state=AsyncMock(
            return_value=SimpleNamespace(
                tenant_id=TENANT_ID, user_id=SECOND_USER_ID, redirect_uri=None
            )
        ),
        get_redirect_uri=lambda: "https://api.test/callback",
        exchange_code_for_tokens=AsyncMock(
            return_value=SimpleNamespace(
                access_token="new-access-token",
                refresh_token=None,
                expires_at=None,
                scopes=["ads_read"],
            )
        ),
        fetch_authorized_user_id=AsyncMock(return_value=fetched_user_id),
        encrypt_token=lambda token: f"enc:{token}",
    )


async def _run_callback(
    monkeypatch: pytest.MonkeyPatch, connection: Any, fetched_user_id: str | None
) -> None:
    """Drive ``oauth_callback`` through the existing-connection branch."""
    monkeypatch.setattr(
        oauth_endpoint, "get_oauth_service", lambda _platform: _service(fetched_user_id)
    )

    # Called directly, so the FastAPI ``Query(None)`` defaults must be passed
    # explicitly - a bare Query object is truthy and reads as an OAuth error.
    await oauth_endpoint.oauth_callback(
        platform=AdPlatform.META,
        code="auth-code",
        state="state-token",
        error=None,
        error_description=None,
        db=_Session(connection),
    )


@pytest.mark.asyncio
async def test_a_failed_me_lookup_clears_the_stale_meta_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A transient ``GET /me`` failure must not leave one person's Meta id bound
    to another person's Stratum account.

    ``tenant_platform_connection`` is unique on (tenant_id, platform), so a
    second person in the same tenant reconnecting Meta reuses this row.
    ``granted_by_user_id`` is always overwritten; if ``platform_user_id`` were
    only overwritten on success, this row would claim Meta user A authorised
    Stratum user B - and A's deletion callback would then act on B's tenant.
    An unmatched connection is harmless (the callbacks answer 200
    ``no_connection``); a mismatched one is not.
    """
    connection = _existing_connection()

    await _run_callback(monkeypatch, connection, fetched_user_id=None)

    assert connection.granted_by_user_id == SECOND_USER_ID
    assert (
        connection.platform_user_id is None
    ), "the previous person's Meta id is still bound to the new grantor"


@pytest.mark.asyncio
async def test_a_successful_me_lookup_rebinds_the_meta_user_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path replaces the id rather than keeping the previous one."""
    connection = _existing_connection()

    await _run_callback(monkeypatch, connection, fetched_user_id=SECOND_META_USER_ID)

    assert connection.granted_by_user_id == SECOND_USER_ID
    assert connection.platform_user_id == SECOND_META_USER_ID


@pytest.mark.asyncio
async def test_the_grantor_and_the_meta_id_are_never_written_apart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Whatever the ``/me`` outcome, the pair is consistent afterwards.

    Either both fields describe the person who just authorised, or the Meta id
    is absent - never a mix of the old identity and the new grantor.
    """
    for fetched in (None, SECOND_META_USER_ID):
        connection = _existing_connection()

        await _run_callback(monkeypatch, connection, fetched_user_id=fetched)

        assert connection.platform_user_id in (None, SECOND_META_USER_ID)
        assert connection.platform_user_id != FIRST_META_USER_ID
