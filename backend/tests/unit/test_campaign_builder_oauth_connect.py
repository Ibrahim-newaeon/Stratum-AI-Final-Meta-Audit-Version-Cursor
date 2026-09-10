# =============================================================================
# Campaign Builder connector endpoints — real Meta OAuth (not placeholders)
# =============================================================================
"""Ensure Connect Platforms start/refresh no longer return stub OAuth URLs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import campaign_builder as cb
from app.models.campaign_builder import AdPlatform

pytestmark = pytest.mark.unit


class _FakeState:
    state_token = "csrf-state-token"


class _FakeOAuthService:
    def __init__(self, auth_url: str = "https://www.facebook.com/v23.0/dialog/oauth?client_id=app"):
        self._auth_url = auth_url
        self.create_state = AsyncMock(return_value=_FakeState())

    def get_authorization_url(self, state, scopes=None):
        return self._auth_url


@pytest.mark.asyncio
async def test_start_platform_connection_returns_real_oauth_url():
    """Connect start must return a live authorization URL, never a placeholder."""
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=1, user_id=42))
    service = _FakeOAuthService()

    with patch.object(cb, "get_oauth_service", return_value=service):
        response = await cb.start_platform_connection(
            request=request,
            tenant_id=1,
            platform=AdPlatform.META,
            db=MagicMock(),
        )

    assert response.success is True
    assert response.data["oauth_url"].startswith("https://www.facebook.com/")
    assert "..." not in response.data["oauth_url"]
    assert response.data["authorization_url"] == response.data["oauth_url"]
    assert response.data["state"] == "csrf-state-token"
    service.create_state.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_platform_connection_requires_auth_user():
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=1, user_id=None))

    with pytest.raises(HTTPException) as exc_info:
        await cb.start_platform_connection(
            request=request,
            tenant_id=1,
            platform=AdPlatform.META,
            db=MagicMock(),
        )

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_start_platform_connection_rejects_tenant_mismatch():
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=2, user_id=42))

    with pytest.raises(HTTPException) as exc_info:
        await cb.start_platform_connection(
            request=request,
            tenant_id=1,
            platform=AdPlatform.META,
            db=MagicMock(),
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_start_platform_connection_fails_closed_without_meta_app_id():
    request = SimpleNamespace(state=SimpleNamespace(tenant_id=1, user_id=42))
    service = _FakeOAuthService()
    service.create_state = AsyncMock(return_value=_FakeState())
    service.get_authorization_url = MagicMock(side_effect=ValueError("Meta App ID not configured"))

    with (
        patch.object(cb, "get_oauth_service", return_value=service),
        pytest.raises(HTTPException) as exc_info,
    ):
        await cb.start_platform_connection(
            request=request,
            tenant_id=1,
            platform=AdPlatform.META,
            db=MagicMock(),
        )

    assert exc_info.value.status_code == 503
    assert "Meta App ID" in str(exc_info.value.detail)
