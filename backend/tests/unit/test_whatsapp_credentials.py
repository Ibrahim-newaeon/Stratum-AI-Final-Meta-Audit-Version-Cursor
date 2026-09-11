# =============================================================================
# Per-tenant WhatsApp credentials — encrypt at rest, fail closed, never leak
# =============================================================================
"""Regression tests for Module G ``tenant_whatsapp_credentials`` helpers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.whatsapp_credentials import TenantWhatsAppCredential
from app.services.encryption import decrypt_token, encrypt_token
from app.services.whatsapp import credentials_store as store

pytestmark = pytest.mark.unit


def test_tenant_whatsapp_credential_model_is_registered():
    from pathlib import Path

    init_text = Path("app/models/__init__.py").read_text(encoding="utf-8")
    assert "whatsapp_credentials" in init_text
    assert "TenantWhatsAppCredential" in init_text
    assert TenantWhatsAppCredential.__tablename__ == "tenant_whatsapp_credentials"


def test_public_status_never_includes_secrets():
    row = TenantWhatsAppCredential(
        tenant_id=1,
        phone_number_id="1234567890",
        business_account_id="waba-1",
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
    assert public["phone_number_id"] == "1234567890"


def test_from_row_decrypts_access_token():
    row = TenantWhatsAppCredential(
        tenant_id=1,
        phone_number_id="999",
        access_token_encrypted=encrypt_token("plain-token"),
        status="connected",
        is_active=True,
    )
    creds = store._from_row(row)
    assert creds.phone_number_id == "999"
    assert creds.access_token == "plain-token"
    assert creds.source == "tenant"
    assert decrypt_token(row.access_token_encrypted) == "plain-token"


@pytest.mark.asyncio
async def test_upsert_credentials_encrypts_access_token():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()

    with patch.object(store, "get_credential", AsyncMock(return_value=None)):
        row = await store.upsert_credentials(
            db,
            tenant_id=7,
            phone_number_id="pn-1",
            access_token="secret-live-token",
            business_account_id="ba-1",
            verify_message="ok",
        )

    assert row.tenant_id == 7
    assert row.phone_number_id == "pn-1"
    assert row.business_account_id == "ba-1"
    assert row.access_token_encrypted != "secret-live-token"
    assert decrypt_token(row.access_token_encrypted) == "secret-live-token"
    assert row.is_active is True
    assert row.status == "connected"
    db.add.assert_called_once()
    db.flush.assert_awaited()


@pytest.mark.asyncio
async def test_deactivate_credentials_clears_ciphertext():
    row = TenantWhatsAppCredential(
        tenant_id=1,
        phone_number_id="pn",
        access_token_encrypted=encrypt_token("still-secret"),
        status="connected",
        is_active=True,
    )
    db = AsyncMock()
    db.flush = AsyncMock()

    with patch.object(store, "get_credential", AsyncMock(return_value=row)):
        cleared = await store.deactivate_credentials(db, tenant_id=1)

    assert cleared is True
    assert row.is_active is False
    assert row.status == "disconnected"
    assert row.access_token_encrypted is None


@pytest.mark.asyncio
async def test_resolve_credentials_prefers_tenant_row():
    row = TenantWhatsAppCredential(
        tenant_id=3,
        phone_number_id="tenant-pn",
        access_token_encrypted=encrypt_token("tenant-tok"),
        status="connected",
        is_active=True,
    )
    with (
        patch.object(store, "get_credential", AsyncMock(return_value=row)),
        patch.object(store, "_global_dev_fallback", return_value=None),
    ):
        creds = await store.resolve_credentials(AsyncMock(), 3)

    assert creds.source == "tenant"
    assert creds.phone_number_id == "tenant-pn"
    assert creds.access_token == "tenant-tok"


@pytest.mark.asyncio
async def test_resolve_credentials_fail_closed_without_fallback():
    with (
        patch.object(store, "get_credential", AsyncMock(return_value=None)),
        patch.object(store, "_global_dev_fallback", return_value=None),
        pytest.raises(store.WhatsAppNotConfiguredError),
    ):
        await store.resolve_credentials(AsyncMock(), 99)


def test_resolve_credentials_sync_uses_dev_fallback(monkeypatch):
    fallback = store.ResolvedWhatsAppCredentials(
        phone_number_id="global-pn",
        access_token="global-tok",
        business_account_id=None,
        source="global_dev_fallback",
    )
    monkeypatch.setattr(store, "get_credential_sync", lambda db, tenant_id: None)
    monkeypatch.setattr(store, "_global_dev_fallback", lambda: fallback)

    creds = store.resolve_credentials_sync(SimpleNamespace(), 1)
    assert creds.source == "global_dev_fallback"
    assert creds.access_token == "global-tok"


def test_migration_revision_chains_from_0011():
    from pathlib import Path

    mig = next(Path("migrations/versions").glob("*0012*whatsapp*"))
    text = mig.read_text(encoding="utf-8")
    assert 'revision = "0012_tenant_whatsapp_credentials"' in text
    assert 'down_revision = "0011_aud_sync_sched"' in text
    assert "tenant_whatsapp_credentials" in text
