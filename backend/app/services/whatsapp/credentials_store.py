# =============================================================================
# Stratum AI - Durable WhatsApp Cloud API credential store (Module G)
# =============================================================================
"""Persist / resolve per-tenant WhatsApp messaging credentials.

Secrets are Fernet-encrypted and never appear in public status payloads.

Resolution order for outbound Module G sends:
1. Active row in ``tenant_whatsapp_credentials`` (preferred).
2. Global ``WHATSAPP_*`` settings — only when ``dev_defaults_allowed()`` is true.
3. Otherwise fail closed (raise ``WhatsAppNotConfiguredError``).

Platform OTP / auth flows keep using global env via ``get_whatsapp_client()``
and must not call this resolver.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.core.config import dev_defaults_allowed, settings
from app.core.logging import get_logger
from app.models.whatsapp_credentials import TenantWhatsAppCredential
from app.services.encryption import decrypt_token, encrypt_token

logger = get_logger(__name__)

CredentialSource = Literal["tenant", "global_dev_fallback"]


class WhatsAppNotConfiguredError(Exception):
    """Raised when a tenant has no WhatsApp credentials and no safe fallback."""

    def __init__(self, tenant_id: int, message: str | None = None):
        self.tenant_id = tenant_id
        super().__init__(
            message
            or (
                f"WhatsApp Cloud API is not configured for tenant {tenant_id}. "
                "Connect credentials via PUT /whatsapp/credentials."
            )
        )


@dataclass(frozen=True)
class ResolvedWhatsAppCredentials:
    """Plain credentials ready for a Graph API client (never log this object)."""

    phone_number_id: str
    access_token: str
    business_account_id: Optional[str]
    source: CredentialSource
    display_phone_number: Optional[str] = None


def _public_status(row: TenantWhatsAppCredential) -> dict[str, Any]:
    """Build an API-safe status dict (no tokens, no ciphertext)."""
    return {
        "connected": bool(row.is_active and row.has_credentials and row.status == "connected"),
        "status": row.status,
        "has_credentials": row.has_credentials,
        "phone_number_id": row.phone_number_id,
        "business_account_id": row.business_account_id,
        "display_phone_number": row.display_phone_number,
        "last_verified_at": row.last_verified_at.isoformat() if row.last_verified_at else None,
        "last_verify_message": row.last_verify_message,
        "last_error": row.last_error,
    }


def _disconnected_public_status() -> dict[str, Any]:
    return {
        "connected": False,
        "status": "disconnected",
        "has_credentials": False,
        "phone_number_id": None,
        "business_account_id": None,
        "display_phone_number": None,
        "last_verified_at": None,
        "last_verify_message": None,
        "last_error": None,
    }


def _from_row(row: TenantWhatsAppCredential) -> ResolvedWhatsAppCredentials:
    if not row.access_token_encrypted or not row.phone_number_id:
        raise ValueError("Incomplete WhatsApp credential row")
    return ResolvedWhatsAppCredentials(
        phone_number_id=row.phone_number_id,
        access_token=decrypt_token(row.access_token_encrypted),
        business_account_id=row.business_account_id,
        source="tenant",
        display_phone_number=row.display_phone_number,
    )


def _global_dev_fallback() -> ResolvedWhatsAppCredentials | None:
    """Return global env credentials only when development fallbacks are allowed."""
    if not dev_defaults_allowed():
        return None
    phone = settings.whatsapp_phone_number_id
    token = settings.whatsapp_access_token
    if not phone or not token:
        return None
    return ResolvedWhatsAppCredentials(
        phone_number_id=phone,
        access_token=token,
        business_account_id=settings.whatsapp_business_account_id,
        source="global_dev_fallback",
    )


async def get_credential(
    db: AsyncSession,
    tenant_id: int,
) -> TenantWhatsAppCredential | None:
    """Return the credential row for one tenant, if any."""
    result = await db.execute(
        select(TenantWhatsAppCredential).where(TenantWhatsAppCredential.tenant_id == tenant_id)
    )
    return result.scalar_one_or_none()


def get_credential_sync(db: Session, tenant_id: int) -> TenantWhatsAppCredential | None:
    """Sync variant for Celery workers."""
    return db.execute(
        select(TenantWhatsAppCredential).where(TenantWhatsAppCredential.tenant_id == tenant_id)
    ).scalar_one_or_none()


async def public_status(db: AsyncSession, tenant_id: int) -> dict[str, Any]:
    """Status payload for the API — never includes secrets."""
    row = await get_credential(db, tenant_id)
    if row is None:
        return _disconnected_public_status()
    return _public_status(row)


async def upsert_credentials(
    db: AsyncSession,
    *,
    tenant_id: int,
    phone_number_id: str,
    access_token: str,
    business_account_id: str | None = None,
    display_phone_number: str | None = None,
    verify_message: str | None = None,
) -> TenantWhatsAppCredential:
    """Encrypt and upsert Module G messaging credentials after a successful connect."""
    if not phone_number_id or not access_token:
        raise ValueError("phone_number_id and access_token are required")

    row = await get_credential(db, tenant_id)
    if row is None:
        row = TenantWhatsAppCredential(tenant_id=tenant_id)
        db.add(row)

    row.phone_number_id = phone_number_id
    row.access_token_encrypted = encrypt_token(access_token)
    if business_account_id is not None:
        row.business_account_id = business_account_id or None
    if display_phone_number is not None:
        row.display_phone_number = display_phone_number or None
    row.is_active = True
    row.status = "connected"
    row.last_verified_at = datetime.now(UTC)
    row.last_verify_message = verify_message
    row.last_error = None

    await db.flush()
    logger.info(
        "whatsapp_credentials_persisted",
        tenant_id=tenant_id,
        phone_number_id=phone_number_id,
        has_credentials=True,
    )
    return row


async def deactivate_credentials(db: AsyncSession, *, tenant_id: int) -> bool:
    """Soft-disconnect: clear ciphertext and mark inactive. Returns False if missing."""
    row = await get_credential(db, tenant_id)
    if row is None:
        return False

    row.is_active = False
    row.status = "disconnected"
    row.access_token_encrypted = None
    row.last_error = None
    row.last_verify_message = "Disconnected by operator"
    await db.flush()
    logger.info("whatsapp_credentials_deactivated", tenant_id=tenant_id)
    return True


async def resolve_credentials(
    db: AsyncSession,
    tenant_id: int,
) -> ResolvedWhatsAppCredentials:
    """Resolve credentials for an async Module G send path."""
    row = await get_credential(db, tenant_id)
    if row is not None and row.is_active and row.has_credentials and row.status == "connected":
        return _from_row(row)

    fallback = _global_dev_fallback()
    if fallback is not None:
        logger.warning(
            "whatsapp_using_global_dev_fallback",
            tenant_id=tenant_id,
        )
        return fallback

    raise WhatsAppNotConfiguredError(tenant_id)


def resolve_credentials_sync(
    db: Session,
    tenant_id: int,
) -> ResolvedWhatsAppCredentials:
    """Resolve credentials for Celery / sync Module G send paths."""
    row = get_credential_sync(db, tenant_id)
    if row is not None and row.is_active and row.has_credentials and row.status == "connected":
        return _from_row(row)

    fallback = _global_dev_fallback()
    if fallback is not None:
        logger.warning(
            "whatsapp_using_global_dev_fallback",
            tenant_id=tenant_id,
        )
        return fallback

    raise WhatsAppNotConfiguredError(tenant_id)
