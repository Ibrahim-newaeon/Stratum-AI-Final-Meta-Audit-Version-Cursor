"""Unit tests for Meta ad-account upsert / sync helpers."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.api.v1.endpoints import campaign_builder as campaign_builder_module
from app.api.v1.endpoints import oauth as oauth_module
from app.services.oauth.ad_account_sync import sync_connection_ad_accounts, upsert_ad_accounts
from app.services.oauth.base import AdAccountInfo

pytestmark = pytest.mark.unit


def _account(account_id: str = "act_111") -> AdAccountInfo:
    return AdAccountInfo(
        account_id=account_id,
        name=f"Account {account_id}",
        business_name="Biz",
        currency="USD",
        timezone="UTC",
        status="active",
    )


@pytest.mark.asyncio
async def test_upsert_ad_accounts_creates_enabled_rows():
    connection = SimpleNamespace(id=uuid4(), tenant_id=7, platform="meta")
    existing_result = MagicMock()
    existing_result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=existing_result)
    db.add = MagicMock()
    db.flush = AsyncMock()

    rows = await upsert_ad_accounts(
        db,
        connection=connection,
        accounts=[_account("act_1"), _account("act_2")],
        enable=True,
    )

    assert len(rows) == 2
    assert db.add.call_count == 2
    for row in rows:
        assert row.is_enabled is True
        assert row.platform_account_id.startswith("act_")
        assert row.tenant_id == 7
        assert row.connection_id == connection.id


@pytest.mark.asyncio
async def test_upsert_ad_accounts_rejects_unknown_ids():
    connection = SimpleNamespace(id=uuid4(), tenant_id=7, platform="meta")
    db = AsyncMock()

    with pytest.raises(ValueError, match="not found"):
        await upsert_ad_accounts(
            db,
            connection=connection,
            accounts=[_account("act_1")],
            enable=True,
            account_ids={"act_missing"},
        )


@pytest.mark.asyncio
async def test_sync_connection_ad_accounts_uses_oauth_fetch():
    connection = SimpleNamespace(
        id=uuid4(),
        tenant_id=3,
        platform="meta",
        access_token_encrypted="enc",
    )
    oauth = MagicMock()
    oauth.decrypt_token.return_value = "token"
    oauth.fetch_ad_accounts = AsyncMock(return_value=[_account("act_9")])

    existing_result = MagicMock()
    existing_result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=existing_result)
    db.add = MagicMock()
    db.flush = AsyncMock()

    rows = await sync_connection_ad_accounts(
        db,
        connection=connection,
        oauth_service=oauth,
        enable=True,
    )

    oauth.decrypt_token.assert_called_once_with("enc")
    oauth.fetch_ad_accounts.assert_awaited_once_with("token")
    assert len(rows) == 1
    assert rows[0].platform_account_id == "act_9"
    assert rows[0].is_enabled is True


def test_oauth_callback_autosyncs_ad_accounts():
    source = inspect.getsource(oauth_module.oauth_callback)
    assert "sync_connection_ad_accounts" in source
    assert "oauth_ad_accounts_autosync_failed" in source


def test_oauth_and_campaign_builder_expose_sync_routes():
    oauth_paths = {getattr(route, "path", None) for route in oauth_module.router.routes}
    assert any(
        path and path.endswith("/{platform}/accounts/sync") for path in oauth_paths
    )

    source = inspect.getsource(campaign_builder_module.sync_ad_accounts)
    assert "sync_connection_ad_accounts" in source
    assert "synced_count" in source
