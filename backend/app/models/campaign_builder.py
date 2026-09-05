# =============================================================================
# Stratum AI - Campaign Builder Models
# =============================================================================
"""
Database models for the Campaign Builder feature:
- TenantPlatformConnection: OAuth tokens and connection metadata per tenant
- TenantAdAccount: Ad accounts enabled for use by tenant
- CampaignDraft: Campaign drafts with approval workflow
- CampaignPublishLog: Audit trail for publish attempts
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (Boolean, DateTime, ForeignKey, Index, Integer, Numeric,
                        String, Text, UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

# =============================================================================
# Enums
# =============================================================================


class AdPlatform(str, enum.Enum):
    """Supported advertising platforms."""

    META = "meta"


class ConnectionStatus(str, enum.Enum):
    """Platform connection status."""

    CONNECTED = "connected"
    EXPIRED = "expired"
    ERROR = "error"
    DISCONNECTED = "disconnected"


class DraftStatus(str, enum.Enum):
    """Campaign draft status."""

    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


class PublishResult(str, enum.Enum):
    """Publish attempt result."""

    SUCCESS = "success"
    FAILURE = "failure"


# =============================================================================
# Models
# =============================================================================


class TenantPlatformConnection(Base):
    """
    Stores OAuth tokens and connection metadata per tenant.
    One record per (tenant, platform) pair.
    """

    __tablename__ = "tenant_platform_connection"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default=ConnectionStatus.DISCONNECTED.value)

    # Token storage (encrypted in production)
    token_ref: Mapped[str | None] = mapped_column(Text, nullable=True)  # Reference to encrypted token in secrets manager
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # OAuth metadata
    scopes: Mapped[Any] = mapped_column(JSONB, nullable=True, default=list)
    granted_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # The platform's own id for the person who authorised the app. For Meta this
    # is the app-scoped user id (ASID) read from /me at OAuth time. It is the
    # ONLY thing Meta sends in its Deauthorize and Data Deletion callbacks, so
    # without it those callbacks cannot tell which connection to sever. Indexed
    # but not unique: one Meta user may connect several tenants.
    platform_user_id = Column(String(64), nullable=True, index=True)

    # Timestamps
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_refreshed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Error tracking
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_count: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id], back_populates="platform_connections")
    granted_by = relationship("User", foreign_keys=[granted_by_user_id])
    ad_accounts = relationship(
        "TenantAdAccount", back_populates="connection", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "platform", name="uq_tenant_platform_connection"),
        Index("ix_tenant_platform_connection_tenant_platform", "tenant_id", "platform"),
    )


class TenantAdAccount(Base):
    """
    Ad accounts that tenant has enabled for use in Stratum AI.
    Synced from platform after OAuth authorization.
    """

    __tablename__ = "tenant_ad_account"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenant_platform_connection.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)

    # Platform identifiers
    platform_account_id: Mapped[str] = mapped_column(String(255), nullable=False)  # e.g., act_123456789
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    business_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Account configuration
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="USD")
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Budget controls
    daily_budget_cap: Mapped[Decimal | None] = mapped_column(Numeric(precision=12, scale=2), nullable=True)
    monthly_budget_cap: Mapped[Decimal | None] = mapped_column(Numeric(precision=12, scale=2), nullable=True)

    # Platform permissions and metadata
    permissions_json: Mapped[Any] = mapped_column(JSONB, nullable=True, default=dict)
    account_status: Mapped[str | None] = mapped_column(String(50), nullable=True)  # active, disabled, etc.

    # Sync tracking
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id], back_populates="ad_accounts")
    connection = relationship("TenantPlatformConnection", back_populates="ad_accounts")
    campaign_drafts = relationship("CampaignDraft", back_populates="ad_account")

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "platform", "platform_account_id", name="uq_tenant_ad_account"
        ),
        Index("ix_tenant_ad_account_enabled", "tenant_id", "is_enabled"),
    )


class CampaignDraft(Base):
    """
    Stores campaign drafts with approval workflow.
    Draft JSON is platform-agnostic, converted at publish time.
    """

    __tablename__ = "campaign_draft"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ad_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenant_ad_account.id", ondelete="SET NULL"), nullable=True
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)

    # Draft identification
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default=DraftStatus.DRAFT.value)

    # Campaign configuration (canonical JSON format)
    draft_json: Mapped[Any] = mapped_column(JSONB, nullable=False, default=dict)

    # Workflow tracking
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    submitted_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rejected_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Published campaign reference
    platform_campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id], back_populates="campaign_drafts")
    ad_account = relationship("TenantAdAccount", back_populates="campaign_drafts")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    submitted_by = relationship("User", foreign_keys=[submitted_by_user_id])
    approved_by = relationship("User", foreign_keys=[approved_by_user_id])
    rejected_by = relationship("User", foreign_keys=[rejected_by_user_id])
    publish_logs = relationship(
        "CampaignPublishLog", back_populates="draft", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_campaign_draft_tenant_status", "tenant_id", "status"),
        Index("ix_campaign_draft_platform", "tenant_id", "platform"),
    )


class CampaignPublishLog(Base):
    """
    Audit trail for campaign publish attempts.
    Records request/response for debugging and compliance.
    """

    __tablename__ = "campaign_publish_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    draft_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("campaign_draft.id", ondelete="SET NULL"), nullable=True
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    platform_account_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # Actor
    published_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Event timing
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Request/Response (for debugging)
    request_json: Mapped[Any] = mapped_column(JSONB, nullable=True)
    response_json: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Result
    result_status: Mapped[str] = mapped_column(String(50), nullable=False)
    platform_campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # If successful

    # Error details
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Retry tracking
    retry_count: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    last_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id], back_populates="publish_logs")
    draft = relationship("CampaignDraft", back_populates="publish_logs")
    published_by = relationship("User", foreign_keys=[published_by_user_id])

    __table_args__ = (
        Index("ix_campaign_publish_log_tenant_time", "tenant_id", "event_time"),
        Index("ix_campaign_publish_log_draft", "draft_id"),
    )
