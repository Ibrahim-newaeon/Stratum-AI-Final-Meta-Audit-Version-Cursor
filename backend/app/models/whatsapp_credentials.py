# =============================================================================
# Stratum AI - Per-tenant WhatsApp Cloud API credentials (encrypted at rest)
# =============================================================================
"""Durable storage for Module G WhatsApp Business (Cloud API) messaging credentials.

This is separate from ``tenant_capi_credentials``: CAPI WhatsApp rows carry
Conversions API tokens; Module G messaging needs a phone-number-scoped Cloud
API token. Reusing the CAPI table would conflate two products and two token
shapes.

Secrets are Fernet-encrypted via ``app.services.encryption.encrypt_token`` and
must never be returned by the API.
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


class TenantWhatsAppCredential(Base, TimestampMixin):
    """One encrypted WhatsApp Cloud API credential set per tenant."""

    __tablename__ = "tenant_whatsapp_credentials"

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    # Non-secret identifiers (safe for status UI)
    phone_number_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_account_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Secrets — Fernet ciphertext only
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

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
            name="uq_tenant_whatsapp_credentials_tenant",
        ),
    )

    @property
    def has_credentials(self) -> bool:
        """Whether an encrypted access token and phone number id are stored."""
        return bool(self.access_token_encrypted and self.phone_number_id)

    def __repr__(self) -> str:
        return (
            f"<TenantWhatsAppCredential tenant={self.tenant_id} "
            f"active={self.is_active} status={self.status}>"
        )
