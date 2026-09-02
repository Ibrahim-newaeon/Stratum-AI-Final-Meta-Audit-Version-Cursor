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
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

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

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    # tenant_id is an Integer everywhere else in the platform (tenants.id) and the
    # service layer compares it against an int, so keep it Integer here too.
    tenant_id = Column(Integer, nullable=False, index=True)

    platform = Column(String(20), nullable=False, default=SyncPlatform.META.value)
    ad_account_id = Column(String(100), nullable=False)
    access_token = Column(Text, nullable=False)
    config = Column(JSONB, nullable=False, default=dict)

    is_active = Column(Boolean, nullable=False, default=True)

    __table_args__ = (
        Index("ix_audience_sync_credentials_tenant_platform", "tenant_id", "platform"),
    )


class PlatformAudience(Base, TimestampMixin):
    """A platform audience linked to a CDP segment."""

    __tablename__ = "platform_audiences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(Integer, nullable=False, index=True)

    segment_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    platform = Column(String(20), nullable=False, default=SyncPlatform.META.value)
    ad_account_id = Column(String(100), nullable=False)

    audience_type = Column(String(30), nullable=False, default=AudienceType.CUSTOMER_LIST.value)
    platform_audience_id = Column(String(100), nullable=True)
    platform_audience_name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Auto-sync configuration
    auto_sync = Column(Boolean, nullable=False, default=False)
    sync_interval_hours = Column(Integer, nullable=True)
    next_sync_at = Column(DateTime, nullable=True)

    # Last sync results
    last_sync_at = Column(DateTime, nullable=True)
    last_sync_status = Column(String(20), nullable=True)
    last_sync_error = Column(Text, nullable=True)

    # Size / match metrics
    platform_size = Column(Integer, nullable=True)
    matched_size = Column(Integer, nullable=True)
    match_rate = Column(Float, nullable=True)

    __table_args__ = (
        Index("ix_platform_audiences_tenant_platform", "tenant_id", "platform"),
    )


class AudienceSyncJob(Base, TimestampMixin):
    """A single audience sync job execution record."""

    __tablename__ = "audience_sync_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id = Column(Integer, nullable=False, index=True)

    platform_audience_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    operation = Column(String(20), nullable=False, default=SyncOperation.UPDATE.value)
    status = Column(String(20), nullable=False, default=SyncStatus.PENDING.value)

    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, nullable=True)

    # Profile counts
    profiles_total = Column(Integer, nullable=True)
    profiles_sent = Column(Integer, nullable=True)
    profiles_added = Column(Integer, nullable=True)
    profiles_removed = Column(Integer, nullable=True)
    profiles_failed = Column(Integer, nullable=True)

    # Results
    platform_response = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)
    error_details = Column(JSONB, nullable=True)


__all__ = [
    "SyncPlatform",
    "SyncStatus",
    "SyncOperation",
    "AudienceType",
    "AudienceSyncCredential",
    "PlatformAudience",
    "AudienceSyncJob",
]
