# =============================================================================
# Durable CAPI credentials — encrypt at rest, never leak in status
# =============================================================================
"""Regression tests for tenant_capi_credentials persistence helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.capi_credentials import TenantCAPICredential
from app.services.capi import credentials_store as store
from app.services.capi.capi_service import CAPIService
from app.services.capi.platform_connectors import ConnectionResult, ConnectionStatus
from app.services.encryption import decrypt_token, encrypt_token

pytestmark = pytest.mark.unit


def test_tenant_capi_credential_model_is_registered():
    from pathlib import Path

    init_text = Path("app/models/__init__.py").read_text(encoding="utf-8")
    assert "capi_credentials" in init_text
    assert "TenantCAPICredential" in init_text
    assert "tenant_capi_credentials" == TenantCAPICredential.__tablename__


def test_public_status_never_includes_secrets():
    row = TenantCAPICredential(
        tenant_id=1,
        platform="meta",
        pixel_id="1234567890",
        access_token_encrypted=encrypt_token("EAAB-secret-token"),
        status="connected",
        is_active=True,
    )
    public = store._public_status(row)
    dumped = str(public)
    assert "EAAB-secret-token" not in dumped
    assert "access_token" not in public
    assert "encrypted" not in dumped.lower()
    assert public["has_credentials"] is True
    assert public["connected"] is True
    assert public["pixel_id"] == "1234567890"


def test_credentials_dict_for_connector_decrypts_meta():
    row = TenantCAPICredential(
        tenant_id=1,
        platform="meta",
        pixel_id="999",
        access_token_encrypted=encrypt_token("plain-token"),
        status="connected",
        is_active=True,
    )
    creds = store.credentials_dict_for_connector(row)
    assert creds == {"access_token": "plain-token", "pixel_id": "999"}
    assert decrypt_token(row.access_token_encrypted) == "plain-token"


@pytest.mark.asyncio
async def test_upsert_connected_credentials_encrypts_access_token():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch.object(store, "get_credential", AsyncMock(return_value=None)):
        row = await store.upsert_connected_credentials(
            db,
            tenant_id=7,
            platform="meta",
            credentials={"pixel_id": "px-1", "access_token": "secret-live-token"},
            verify_message="ok",
        )

    assert row.tenant_id == 7
    assert row.platform == "meta"
    assert row.pixel_id == "px-1"
    assert row.access_token_encrypted != "secret-live-token"
    assert decrypt_token(row.access_token_encrypted) == "secret-live-token"
    assert row.is_active is True
    assert row.status == "connected"
    db.add.assert_called_once()
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_deactivate_credentials_clears_ciphertext():
    row = TenantCAPICredential(
        tenant_id=1,
        platform="meta",
        pixel_id="px",
        access_token_encrypted=encrypt_token("still-secret"),
        status="connected",
        is_active=True,
    )
    db = AsyncMock()
    db.flush = AsyncMock()

    with patch.object(store, "get_credential", AsyncMock(return_value=row)):
        cleared = await store.deactivate_credentials(db, tenant_id=1, platform="meta")

    assert cleared is True
    assert row.is_active is False
    assert row.status == "disconnected"
    assert row.access_token_encrypted is None


@pytest.mark.asyncio
async def test_ensure_loaded_from_db_hydrates_connector_cache():
    service = CAPIService(tenant_id=3)
    row = SimpleNamespace(
        platform="meta",
        access_token_encrypted=encrypt_token("tok"),
        pixel_id="111",
    )

    with (
        patch(
            "app.services.capi.credentials_store.list_active_credentials",
            AsyncMock(return_value=[row]),
        ),
        patch(
            "app.services.capi.credentials_store.credentials_dict_for_connector",
            return_value={"pixel_id": "111", "access_token": "tok"},
        ),
        patch.object(
            service,
            "connect_platform",
            AsyncMock(
                return_value=ConnectionResult(
                    status=ConnectionStatus.CONNECTED,
                    platform="meta",
                    message="ok",
                )
            ),
        ) as connect,
    ):
        # connect_platform normally sets self.connectors; simulate that.
        async def _connect(platform, credentials):
            service.connectors[platform] = object()
            return ConnectionResult(
                status=ConnectionStatus.CONNECTED, platform=platform, message="ok"
            )

        connect.side_effect = _connect
        loaded = await service.ensure_loaded_from_db(AsyncMock())

    assert loaded == ["meta"]
    assert "meta" in service.connectors


@pytest.mark.asyncio
async def test_require_tenant_id_rejects_anonymous():
    from fastapi import HTTPException

    from app.api.v1.endpoints import capi as capi_ep

    request = SimpleNamespace(state=SimpleNamespace())
    with pytest.raises(HTTPException) as exc:
        capi_ep._require_tenant_id(request)
    assert exc.value.status_code == 401
