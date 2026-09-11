# =============================================================================
# Audience sync credentials — encrypt + upsert API
# =============================================================================
"""Ensure Meta audience tokens are Fernet-encrypted and never returned."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.audience_sync import AudienceSyncCredential
from app.services.cdp.audience_sync.service import AudienceSyncService
from app.services.encryption import decrypt_token, encrypt_token

pytestmark = pytest.mark.unit


def test_resolved_access_token_prefers_encrypted():
    row = AudienceSyncCredential(
        tenant_id=1,
        platform="meta",
        ad_account_id="act_1",
        access_token="legacy-plain",
        access_token_encrypted=encrypt_token("secret-encrypted"),
        config={},
        is_active=True,
    )
    assert row.resolved_access_token() == "secret-encrypted"
    assert row.has_credentials is True


def test_resolved_access_token_falls_back_to_legacy_plaintext():
    row = AudienceSyncCredential(
        tenant_id=1,
        platform="meta",
        ad_account_id="act_1",
        access_token="legacy-plain",
        access_token_encrypted=None,
        config={},
        is_active=True,
    )
    assert row.resolved_access_token() == "legacy-plain"


def test_get_connector_uses_resolved_token_not_plaintext_attr():
    service = AudienceSyncService(db=MagicMock(), tenant_id=1)
    row = AudienceSyncCredential(
        tenant_id=1,
        platform="meta",
        ad_account_id="act_99",
        access_token=None,
        access_token_encrypted=encrypt_token("live-token"),
        config={"app_secret": "s"},
        is_active=True,
    )
    with patch.object(service, "CONNECTOR_CLASSES", {"meta": MagicMock()}) as classes:
        connector_cls = classes["meta"]
        service._get_connector("meta", row)
        kwargs = connector_cls.call_args.kwargs
        assert kwargs["access_token"] == "live-token"
        assert kwargs["ad_account_id"] == "act_99"


@pytest.mark.asyncio
async def test_upsert_credentials_encrypts_and_clears_plaintext():
    service = AudienceSyncService(db=AsyncMock(), tenant_id=5)
    service.db.add = MagicMock()
    service.db.flush = AsyncMock()

    with patch.object(service, "_get_credentials", AsyncMock(return_value=None)):
        # inactive lookup also empty
        empty = MagicMock()
        empty.scalar_one_or_none.return_value = None
        service.db.execute = AsyncMock(return_value=empty)

        row = await service.upsert_credentials(
            platform="meta",
            ad_account_id="act_55",
            access_token="plain-secret",
            config={"ad_account_name": "Demo"},
        )

    assert row.access_token is None
    assert row.access_token_encrypted is not None
    assert decrypt_token(row.access_token_encrypted) == "plain-secret"
    assert row.is_active is True
    assert "access_token" not in str(row.config)


@pytest.mark.asyncio
async def test_deactivate_credentials_clears_secrets():
    service = AudienceSyncService(db=AsyncMock(), tenant_id=5)
    row = AudienceSyncCredential(
        tenant_id=5,
        platform="meta",
        ad_account_id="act_1",
        access_token_encrypted=encrypt_token("x"),
        access_token="leftover",
        config={},
        is_active=True,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    service.db.execute = AsyncMock(return_value=result)
    service.db.flush = AsyncMock()

    ok = await service.deactivate_credentials(platform="meta", ad_account_id="act_1")
    assert ok is True
    assert row.is_active is False
    assert row.access_token_encrypted is None
    assert row.access_token is None
