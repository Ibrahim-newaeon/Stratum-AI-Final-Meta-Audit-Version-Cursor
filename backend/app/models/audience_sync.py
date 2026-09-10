# =============================================================================
# Stratum AI - CDP Audience Sync Database Models
# =============================================================================
"""
Database models for CDP audience sync.

Push CDP segments to Meta as Custom Audiences for targeting across
Facebook, Instagram and WhatsApp.

All models are multi-tenant with tenant_id column.
"""

import enum
import uuid
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin

# =============================================================================
# Enums
# =============================================================================


class SyncPlatform(str, enum.Enum):
    """Ad platforms supported for audience sync (Meta channels only)."""

    META = "meta"


class SyncStatus(str, enum.Enum):
    """Status of an audience sync job."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class SyncOperation(str, enum.Enum):
    """Type of sync operation performed."""

    CREATE = "create"
    UPDATE = "update"
    REPLACE = "replace"
    DELETE = "delete"


class AudienceType(str, enum.Enum):
    """Type of platform audience."""

    CUSTOMER_LIST = "customer_list"
    LOOKALIKE = "lookalike"


# =============================================================================
# Models
# =============================================================================


class AudienceSyncCredential(Base, TimestampMixin):
    """Stored credentials for a platform ad account used for audience sync."""

    __tablename__ = "audience_sync_credentials"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    # tenant_id is an Integer everywhere else in the platform (tenants.id) and the
    # service layer compares it against an int, so keep it Integer here too.
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    platform: Mapped[str] = mapped_column(String(20), nullable=False, default=SyncPlatform.META.value)
    ad_account_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # Legacy plaintext column — nullable during cutover; new writes clear it.
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Fernet ciphertext via ``app.services.encryption.encrypt_token``.
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[Any] = mapped_column(JSONB, nullable=False, default=dict)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("ix_audience_sync_credentials_tenant_platform", "tenant_id", "platform"),
        Index(
            "ix_audience_sync_credentials_tenant_platform_account",
            "tenant_id",
            "platform",
            "ad_account_id",
            unique=True,
        ),
    )

    def resolved_access_token(self) -> str:
        """Return the plaintext token, preferring encrypted storage.

        Raises:
            ValueError: If neither encrypted nor legacy plaintext token exists.
        """
        from app.services.encryption import decrypt_token

        if self.access_token_encrypted:
            return decrypt_token(self.access_token_encrypted)
        if self.access_token:
            return self.access_token
        raise ValueError("No audience-sync access token stored")

    @property
    def has_credentials(self) -> bool:
        """Whether any usable token is stored."""
        return bool(self.access_token_encrypted or self.access_token)


class PlatformAudience(Base, TimestampMixin):
    """A platform audience linked to a CDP segment."""

    __tablename__ = "platform_audiences"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    segment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default=SyncPlatform.META.value)
    ad_account_id: Mapped[str] = mapped_column(String(100), nullable=False)

    audience_type: Mapped[str] = mapped_column(String(30), nullable=False, default=AudienceType.CUSTOMER_LIST.value)
    platform_audience_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    platform_audience_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Auto-sync configuration
    auto_sync: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sync_interval_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Last sync results
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Size / match metrics
    platform_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    matched_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    match_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_platform_audiences_tenant_platform", "tenant_id", "platform"),
        Index(
            "ix_platform_audiences_auto_sync_due",
            "auto_sync",
            "next_sync_at",
        ),
    )


class AudienceSyncJob(Base, TimestampMixin):
    """A single audience sync job execution record."""

    __tablename__ = "audience_sync_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)

    platform_audience_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(20), nullable=False, default=SyncOperation.UPDATE.value)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=SyncStatus.PENDING.value)

    # Who kicked off the job (``manual``, ``schedule``, ``api``, …).
    triggered_by: Mapped[str | None] = mapped_column(String(50), nullable=True)
    triggered_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Profile counts
    profiles_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profiles_sent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profiles_added: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profiles_removed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    profiles_failed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Results
    platform_response: Mapped[Any] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details: Mapped[Any] = mapped_column(JSONB, nullable=True)


__all__ = [
    "AudienceSyncCredential",
    "AudienceSyncJob",
    "AudienceType",
    "PlatformAudience",
    "SyncOperation",
    "SyncPlatform",
    "SyncStatus",
]
