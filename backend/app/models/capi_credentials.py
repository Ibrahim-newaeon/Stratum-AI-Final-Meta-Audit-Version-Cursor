# =============================================================================
# Stratum AI - Per-tenant CAPI credentials (encrypted at rest)
# =============================================================================
"""Durable storage for Meta / WhatsApp Conversions API credentials.

In-memory ``CAPIService.connectors`` is only a cache. Tokens must survive
process restarts so EMQ / signal health can keep receiving attributed
``capi_delivery_logs``. Secrets are Fernet-encrypted via
``app.services.encryption.encrypt_token`` and must never be returned by the API.
"""

from datetime import datetime
from uuid import UUID as PyUUID
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin


class TenantCAPICredential(Base, TimestampMixin):
    """One encrypted CAPI credential set per tenant + platform."""

    __tablename__ = "tenant_capi_credentials"

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)

    # Non-secret identifiers (safe for status UI)
    pixel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone_number_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Secrets — Fernet ciphertext only
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    webhook_verify_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="disconnected",
        server_default=text("'disconnected'"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true")
    )

    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_verify_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "platform",
            name="uq_tenant_capi_credentials_tenant_platform",
        ),
    )

    @property
    def has_credentials(self) -> bool:
        """Whether an encrypted access token is stored."""
        return bool(self.access_token_encrypted)

    def __repr__(self) -> str:
        return (
            f"<TenantCAPICredential tenant={self.tenant_id} "
            f"platform={self.platform} active={self.is_active}>"
        )
