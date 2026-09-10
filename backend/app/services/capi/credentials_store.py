# =============================================================================
# Stratum AI - Durable CAPI credential store
# =============================================================================
"""Persist / hydrate per-tenant CAPI credentials with Fernet encryption.

``CAPIService.connectors`` remains a warm cache. This module is the source of
truth in Postgres. Secrets are written only as ciphertext and never appear in
public status payloads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.capi_credentials import TenantCAPICredential
from app.services.encryption import decrypt_token, encrypt_token

logger = get_logger(__name__)

SUPPORTED_PLATFORMS = frozenset({"meta", "whatsapp"})


def _public_status(row: TenantCAPICredential) -> dict[str, Any]:
    """Build an API-safe status dict (no tokens, no ciphertext)."""
    payload: dict[str, Any] = {
        "connected": bool(row.is_active and row.has_credentials and row.status == "connected"),
        "platform": row.platform,
        "platform_name": row.platform.title(),
        "status": row.status,
        "has_credentials": row.has_credentials,
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "last_verify_message": row.last_verify_message,
        "last_error": row.last_error,
    }
    if row.pixel_id:
        payload["pixel_id"] = row.pixel_id
    if row.phone_number_id:
        payload["phone_number_id"] = row.phone_number_id
    if row.business_account_id:
        payload["business_account_id"] = row.business_account_id
    return payload


def credentials_dict_for_connector(row: TenantCAPICredential) -> dict[str, str]:
    """Decrypt a stored row into the plain dict connectors expect.

    Raises:
        ValueError: If the access token is missing or cannot be decrypted.
    """
    if not row.access_token_encrypted:
        raise ValueError(f"No encrypted access token for {row.platform}")

    creds: dict[str, str] = {"access_token": decrypt_token(row.access_token_encrypted)}
    if row.platform == "meta":
        if row.pixel_id:
            creds["pixel_id"] = row.pixel_id
    elif row.platform == "whatsapp":
        if row.phone_number_id:
            creds["phone_number_id"] = row.phone_number_id
        if row.business_account_id:
            creds["business_account_id"] = row.business_account_id
        if row.webhook_verify_token_encrypted:
            creds["webhook_verify_token"] = decrypt_token(row.webhook_verify_token_encrypted)
    return creds


async def get_credential(
    db: AsyncSession,
    tenant_id: int,
    platform: str,
) -> TenantCAPICredential | None:
    """Return the credential row for one tenant/platform, if any."""
    platform = platform.lower()
    result = await db.execute(
        select(TenantCAPICredential).where(
            and_(
                TenantCAPICredential.tenant_id == tenant_id,
                TenantCAPICredential.platform == platform,
            )
        )
    )
    return result.scalar_one_or_none()


async def list_active_credentials(
    db: AsyncSession,
    tenant_id: int,
) -> list[TenantCAPICredential]:
    """Return active rows that still hold an encrypted access token."""
    result = await db.execute(
        select(TenantCAPICredential).where(
            and_(
                TenantCAPICredential.tenant_id == tenant_id,
                TenantCAPICredential.is_active.is_(True),
                TenantCAPICredential.access_token_encrypted.is_not(None),
            )
        )
    )
    return list(result.scalars().all())


async def upsert_connected_credentials(
    db: AsyncSession,
    *,
    tenant_id: int,
    platform: str,
    credentials: dict[str, str],
    verify_message: str | None = None,
) -> TenantCAPICredential:
    """Encrypt and upsert credentials after a successful connector connect."""
    platform = platform.lower()
    if platform not in SUPPORTED_PLATFORMS:
        raise ValueError(f"Unsupported CAPI platform: {platform}")

    access_token = credentials.get("access_token")
    if not access_token:
        raise ValueError("access_token is required")

    row = await get_credential(db, tenant_id, platform)
    if row is None:
        row = TenantCAPICredential(tenant_id=tenant_id, platform=platform)
        db.add(row)

    row.access_token_encrypted = encrypt_token(access_token)
    row.is_active = True
    row.status = "connected"
    row.last_verified_at = datetime.now(UTC)
    row.last_verify_message = verify_message
    row.last_error = None

    if platform == "meta":
        row.pixel_id = credentials.get("pixel_id") or row.pixel_id
        row.phone_number_id = None
        row.business_account_id = None
        row.webhook_verify_token_encrypted = None
    else:
        row.phone_number_id = credentials.get("phone_number_id") or row.phone_number_id
        row.business_account_id = credentials.get("business_account_id") or row.business_account_id
        webhook = credentials.get("webhook_verify_token")
        if webhook:
            row.webhook_verify_token_encrypted = encrypt_token(webhook)
        row.pixel_id = None

    await db.flush()
    logger.info(
        "capi_credentials_persisted",
        tenant_id=tenant_id,
        platform=platform,
        has_credentials=True,
    )
    return row


async def deactivate_credentials(
    db: AsyncSession,
    *,
    tenant_id: int,
    platform: str,
) -> bool:
    """Soft-disconnect: clear ciphertext and mark inactive. Returns False if missing."""
    row = await get_credential(db, tenant_id, platform)
    if row is None:
        return False

    row.is_active = False
    row.status = "disconnected"
    row.access_token_encrypted = None
    row.webhook_verify_token_encrypted = None
    row.last_error = None
    row.last_verify_message = "Disconnected by operator"
    await db.flush()
    logger.info("capi_credentials_deactivated", tenant_id=tenant_id, platform=platform)
    return True


async def public_connected_platforms(
    db: AsyncSession,
    tenant_id: int,
) -> dict[str, dict[str, Any]]:
    """Status map for the API — never includes secrets."""
    rows = await list_active_credentials(db, tenant_id)
    return {row.platform: _public_status(row) for row in rows if row.status == "connected"}
