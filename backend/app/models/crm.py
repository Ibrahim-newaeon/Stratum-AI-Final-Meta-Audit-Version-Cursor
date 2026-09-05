# =============================================================================
# Stratum AI - CRM Integration Database Models
# =============================================================================
"""
Database models for CRM integrations (HubSpot, Salesforce, etc.).

Models:
- CRMConnection: OAuth connections to CRM providers
- CRMContact: Synced contacts with identity matching
- CRMDeal: Synced deals with attribution
- Touchpoint: Ad touchpoints for attribution tracking
- DailyPipelineMetrics: Aggregated pipeline/revenue metrics
"""

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, Date, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.base_models import Tenant, User

# =============================================================================
# Enums
# =============================================================================


class CRMProvider(str, enum.Enum):
    """Supported CRM providers."""

    HUBSPOT = "hubspot"
    SALESFORCE = "salesforce"
    PIPEDRIVE = "pipedrive"
    ZOHO = "zoho"


class CRMConnectionStatus(str, enum.Enum):
    """CRM connection status."""

    PENDING = "pending"
    CONNECTED = "connected"
    EXPIRED = "expired"
    REVOKED = "revoked"
    ERROR = "error"


class DealStage(str, enum.Enum):
    """Standard deal stages for pipeline tracking."""

    LEAD = "lead"
    MQL = "mql"  # Marketing Qualified Lead
    SQL = "sql"  # Sales Qualified Lead
    OPPORTUNITY = "opportunity"
    PROPOSAL = "proposal"
    NEGOTIATION = "negotiation"
    WON = "won"
    LOST = "lost"


class AttributionModel(str, enum.Enum):
    """Attribution models for touchpoint credit."""

    LAST_TOUCH = "last_touch"
    FIRST_TOUCH = "first_touch"
    LINEAR = "linear"
    POSITION_BASED = "position_based"  # 40% first, 40% last, 20% middle
    TIME_DECAY = "time_decay"
    DATA_DRIVEN = "data_driven"


# =============================================================================
# CRM Connection Model
# =============================================================================


class CRMConnection(Base):
    """
    OAuth connections to CRM providers (per tenant).
    Stores encrypted tokens for secure API access.
    """

    __tablename__ = "crm_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Provider details
    provider: Mapped[CRMProvider] = mapped_column(SQLEnum(CRMProvider), nullable=False)
    provider_account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # HubSpot portal ID, etc.
    provider_account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # OAuth tokens (encrypted)
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Scopes granted
    scopes: Mapped[str | None] = mapped_column(Text, nullable=True)  # Comma-separated scopes

    # Connection status
    status: Mapped[CRMConnectionStatus] = mapped_column(
        SQLEnum(CRMConnectionStatus), nullable=False, default=CRMConnectionStatus.PENDING
    )
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Sync configuration
    sync_contacts: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_deals: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_companies: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    webhook_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Sync tracking
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[str | None] = mapped_column(String(50), nullable=True)  # success, partial, failed
    last_sync_contacts_count: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    last_sync_deals_count: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", foreign_keys=[tenant_id])
    contacts: Mapped[list["CRMContact"]] = relationship("CRMContact", back_populates="connection", cascade="all, delete-orphan")
    deals: Mapped[list["CRMDeal"]] = relationship("CRMDeal", back_populates="connection", cascade="all, delete-orphan")
    writeback_config: Mapped["CRMWritebackConfig | None"] = relationship(
        "CRMWritebackConfig",
        back_populates="connection",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_crm_connections_tenant_provider", "tenant_id", "provider"),
        Index("ix_crm_connections_status", "status"),
    )


# =============================================================================
# CRM Contact Model
# =============================================================================


class CRMContact(Base):
    """
    Synced contacts from CRM with identity matching fields.
    Links ad touchpoints to CRM contacts for attribution.
    """

    __tablename__ = "crm_contacts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_connections.id", ondelete="CASCADE"), nullable=False
    )

    # CRM identifiers
    crm_contact_id: Mapped[str] = mapped_column(String(255), nullable=False)  # HubSpot contact ID
    crm_owner_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Sales rep owner

    # Identity matching (hashed for privacy)
    email_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # SHA256 lowercase trimmed
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # UTM parameters (captured at conversion)
    utm_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_content: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_term: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Click IDs
    fbclid: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Meta

    # Visitor IDs
    ga_client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stratum_visitor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Lifecycle tracking
    lifecycle_stage: Mapped[str | None] = mapped_column(String(100), nullable=True)  # lead, mql, sql, customer, etc.
    lead_source: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Attribution (computed)
    first_touch_campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_touch_campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_touch_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_touch_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    touch_count: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)

    # Stratum quality score (optional writeback)
    stratum_quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Raw CRM data (JSON for flexibility)
    raw_properties: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Timestamps
    crm_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    crm_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", foreign_keys=[tenant_id])
    connection: Mapped["CRMConnection"] = relationship("CRMConnection", back_populates="contacts")
    deals: Mapped[list["CRMDeal"]] = relationship("CRMDeal", back_populates="contact")
    touchpoints: Mapped[list["Touchpoint"]] = relationship("Touchpoint", back_populates="contact")

    __table_args__ = (
        Index("ix_crm_contacts_tenant_email", "tenant_id", "email_hash"),
        Index("ix_crm_contacts_tenant_phone", "tenant_id", "phone_hash"),
        Index("ix_crm_contacts_tenant_fbclid", "tenant_id", "fbclid"),
        Index("ix_crm_contacts_crm_id", "connection_id", "crm_contact_id"),
        Index("ix_crm_contacts_lifecycle", "tenant_id", "lifecycle_stage"),
    )


# =============================================================================
# CRM Deal Model
# =============================================================================


class CRMDeal(Base):
    """
    Synced deals from CRM with revenue attribution.
    Enables Pipeline ROAS and Won ROAS calculations.
    """

    __tablename__ = "crm_deals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_connections.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_contacts.id", ondelete="SET NULL"), nullable=True
    )

    # CRM identifiers
    crm_deal_id: Mapped[str] = mapped_column(String(255), nullable=False)
    crm_pipeline_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    crm_owner_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Deal details
    deal_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Raw stage from CRM
    stage_normalized: Mapped[DealStage | None] = mapped_column(SQLEnum(DealStage), nullable=True)  # Normalized stage

    # Financials
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)  # Deal value
    amount_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # Deal value in cents
    currency: Mapped[str] = mapped_column(String(10), default="USD", nullable=False)

    # Dates
    close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Win/Loss tracking
    is_won: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    won_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Attribution
    attributed_campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attributed_adset_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attributed_ad_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    attributed_platform: Mapped[str | None] = mapped_column(String(50), nullable=True)  # meta, organic
    attribution_model: Mapped[AttributionModel | None] = mapped_column(SQLEnum(AttributionModel), default=AttributionModel.LAST_TOUCH, nullable=True)
    attribution_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-1

    # Attribution touchpoint link
    attributed_touchpoint_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("touchpoints.id", ondelete="SET NULL"), nullable=True
    )

    # Raw CRM data
    raw_properties: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Timestamps
    crm_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    crm_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", foreign_keys=[tenant_id])
    connection: Mapped["CRMConnection"] = relationship("CRMConnection", back_populates="deals")
    contact: Mapped["CRMContact | None"] = relationship("CRMContact", back_populates="deals")
    attributed_touchpoint: Mapped["Touchpoint | None"] = relationship("Touchpoint", foreign_keys=[attributed_touchpoint_id])

    __table_args__ = (
        Index("ix_crm_deals_tenant_stage", "tenant_id", "stage_normalized"),
        Index("ix_crm_deals_tenant_won", "tenant_id", "is_won"),
        Index("ix_crm_deals_tenant_close_date", "tenant_id", "close_date"),
        Index("ix_crm_deals_crm_id", "connection_id", "crm_deal_id"),
        Index("ix_crm_deals_attributed_campaign", "tenant_id", "attributed_campaign_id"),
    )


# =============================================================================
# Touchpoint Model
# =============================================================================


class Touchpoint(Base):
    """
    Ad touchpoints for multi-touch attribution.
    Captures every ad interaction that can be linked to a conversion.
    """

    __tablename__ = "touchpoints"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Contact link (if matched)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_contacts.id", ondelete="SET NULL"), nullable=True
    )

    # Touchpoint timing
    event_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)  # click, view, impression, conversion

    # Platform/source
    source: Mapped[str] = mapped_column(String(50), nullable=False)  # meta

    # Campaign hierarchy
    account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    campaign_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    adset_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    adset_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ad_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ad_name: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # UTM parameters
    utm_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_content: Mapped[str | None] = mapped_column(String(255), nullable=True)
    utm_term: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Click IDs
    click_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Generic click ID
    fbclid: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Meta

    # Identity signals
    email_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    visitor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ga_client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Device/geo context
    device_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # mobile, desktop, tablet
    country: Mapped[str | None] = mapped_column(String(10), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Landing page
    landing_page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    referrer_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Cost (if available at touchpoint level)
    cost_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Attribution flags
    is_first_touch: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    is_last_touch: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    is_converting_touch: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)

    # Position in journey (1-based)
    touch_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_touches: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Attribution weight (for multi-touch models)
    attribution_weight: Mapped[float | None] = mapped_column(Float, default=1.0, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", foreign_keys=[tenant_id])
    contact: Mapped["CRMContact | None"] = relationship("CRMContact", back_populates="touchpoints")

    __table_args__ = (
        Index("ix_touchpoints_tenant_contact", "tenant_id", "contact_id"),
        Index("ix_touchpoints_tenant_event_ts", "tenant_id", "event_ts"),
        Index("ix_touchpoints_tenant_campaign", "tenant_id", "campaign_id"),
        Index("ix_touchpoints_email_hash", "tenant_id", "email_hash"),
        Index("ix_touchpoints_click_ids", "tenant_id", "fbclid"),
        Index("ix_touchpoints_visitor", "tenant_id", "visitor_id"),
    )


# =============================================================================
# Daily Pipeline Metrics (Aggregated)
# =============================================================================


class DailyPipelineMetrics(Base):
    """
    Aggregated daily metrics combining ad spend with CRM pipeline data.
    Enables Pipeline ROAS and Won ROAS calculations.
    """

    __tablename__ = "daily_pipeline_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)

    # Dimension (optional granularity)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)  # meta (null = all)
    campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # (null = all)

    # Ad platform metrics
    spend_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    impressions: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    platform_conversions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    platform_revenue_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # CRM pipeline metrics
    leads_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    mqls_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sqls_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    opportunities_created: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Pipeline value (all open deals)
    pipeline_value_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    pipeline_deal_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Won deals
    deals_won: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    won_revenue_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # Lost deals
    deals_lost: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lost_value_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # Computed ROAS metrics
    platform_roas: Mapped[float | None] = mapped_column(Float, nullable=True)  # platform_revenue / spend
    pipeline_roas: Mapped[float | None] = mapped_column(Float, nullable=True)  # pipeline_value / spend
    won_roas: Mapped[float | None] = mapped_column(Float, nullable=True)  # won_revenue / spend

    # Funnel conversion rates
    lead_to_mql_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    mql_to_sql_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    sql_to_won_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    # CAC metrics
    cac_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # spend / deals_won

    # Time-to-close (average days)
    avg_time_to_close_days: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        Index("ix_daily_pipeline_metrics_tenant_date", "tenant_id", "date"),
        Index("ix_daily_pipeline_metrics_tenant_platform", "tenant_id", "platform", "date"),
        Index("ix_daily_pipeline_metrics_tenant_campaign", "tenant_id", "campaign_id", "date"),
    )


# =============================================================================
# CRM Sync Log Model
# =============================================================================


class CRMSyncLog(Base):
    """
    Log of CRM synchronization operations.
    Tracks sync history for auditing and troubleshooting.
    """

    __tablename__ = "crm_sync_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Sync details
    provider: Mapped[CRMProvider] = mapped_column(SQLEnum(CRMProvider), nullable=False)
    sync_type: Mapped[str] = mapped_column(String(50), nullable=False)  # full, incremental, manual
    status: Mapped[str] = mapped_column(String(50), nullable=False)  # success, failed, partial

    # Timing
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Results
    records_processed: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    records_created: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    records_updated: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    records_failed: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)

    # Error details
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_metadata: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        Index("ix_crm_sync_logs_tenant_date", "tenant_id", "started_at"),
        Index("ix_crm_sync_logs_provider", "tenant_id", "provider"),
    )


# =============================================================================
# Writeback Status Enum
# =============================================================================


class WritebackStatus(str, enum.Enum):
    """Writeback operation status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


# =============================================================================
# CRM Writeback Configuration
# =============================================================================


class CRMWritebackConfig(Base):
    """
    Configuration for CRM writeback operations.
    Controls what data is pushed back to the CRM.
    """

    __tablename__ = "crm_writeback_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_connections.id", ondelete="CASCADE"), nullable=False
    )

    # Writeback toggles
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_contacts: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_deals: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Sync schedule
    auto_sync_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_interval_hours: Mapped[int] = mapped_column(Integer, default=24, nullable=False)  # Default: daily

    # What to sync
    sync_attribution: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_profit_metrics: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sync_touchpoint_count: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Property setup status
    properties_created: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    properties_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Last sync tracking
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[WritebackStatus | None] = mapped_column(SQLEnum(WritebackStatus), nullable=True)
    last_sync_contacts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sync_deals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_sync_errors: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Next scheduled sync
    next_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", foreign_keys=[tenant_id])
    connection: Mapped["CRMConnection"] = relationship("CRMConnection", back_populates="writeback_config")

    __table_args__ = (Index("ix_writeback_config_tenant", "tenant_id"),)


# =============================================================================
# CRM Writeback Sync History
# =============================================================================


class CRMWritebackSync(Base):
    """
    History of CRM writeback sync operations.
    Tracks each sync run for auditing and troubleshooting.
    """

    __tablename__ = "crm_writeback_syncs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("crm_connections.id", ondelete="CASCADE"), nullable=False
    )

    # Sync details
    sync_type: Mapped[str] = mapped_column(String(50), nullable=False)  # full, incremental, manual, scheduled
    status: Mapped[WritebackStatus] = mapped_column(SQLEnum(WritebackStatus), nullable=False, default=WritebackStatus.PENDING)

    # Timing
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Scope
    sync_contacts: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    sync_deals: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    modified_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Results
    contacts_processed: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    contacts_synced: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    contacts_failed: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    deals_processed: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    deals_synced: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    deals_failed: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)

    # Error details
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Triggered by
    triggered_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    tenant: Mapped["Tenant"] = relationship("Tenant", foreign_keys=[tenant_id])
    connection: Mapped["CRMConnection"] = relationship("CRMConnection")
    triggered_by: Mapped["User | None"] = relationship("User", foreign_keys=[triggered_by_user_id])

    __table_args__ = (
        Index("ix_writeback_sync_tenant_date", "tenant_id", "started_at"),
        Index("ix_writeback_sync_status", "tenant_id", "status"),
    )
