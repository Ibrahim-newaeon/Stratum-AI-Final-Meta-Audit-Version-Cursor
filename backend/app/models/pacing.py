# =============================================================================
# Stratum AI - Pacing & Forecasting Database Models
# =============================================================================
"""
Database models for targets, pacing, and forecasting.

Models:
- Target: Monthly/quarterly targets for spend, revenue, ROAS
- DailyKPI: Materialized daily KPIs for fast pacing queries
- PacingAlert: Alert records for pacing issues
- Forecast: Stored forecasts for trend analysis
"""

import enum
import uuid
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, Date, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import (Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

# =============================================================================
# Enums
# =============================================================================


class TargetPeriod(str, enum.Enum):
    """Target period types."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    CUSTOM = "custom"


class TargetMetric(str, enum.Enum):
    """Target metric types."""

    SPEND = "spend"
    REVENUE = "revenue"
    ROAS = "roas"
    CONVERSIONS = "conversions"
    LEADS = "leads"
    PIPELINE_VALUE = "pipeline_value"
    WON_REVENUE = "won_revenue"
    CPA = "cpa"
    CPL = "cpl"


class AlertSeverity(str, enum.Enum):
    """Alert severity levels."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class AlertType(str, enum.Enum):
    """Alert types for pacing."""

    UNDERPACING_SPEND = "underpacing_spend"
    OVERPACING_SPEND = "overpacing_spend"
    ROAS_BELOW_TARGET = "roas_below_target"
    CONVERSIONS_BELOW_TARGET = "conversions_below_target"
    REVENUE_BELOW_TARGET = "revenue_below_target"
    PIPELINE_BELOW_TARGET = "pipeline_below_target"
    PACING_CLIFF = "pacing_cliff"  # Sudden drop
    BUDGET_EXHAUSTION = "budget_exhaustion"


class AlertStatus(str, enum.Enum):
    """Alert status."""

    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


# =============================================================================
# Target Model
# =============================================================================


class Target(Base):
    """
    Monthly/quarterly targets for spend, revenue, ROAS, etc.
    Supports targets at account, campaign, or platform level.
    """

    __tablename__ = "targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Target identification
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Period
    period_type: Mapped[TargetPeriod] = mapped_column(SQLEnum(TargetPeriod), nullable=False, default=TargetPeriod.MONTHLY)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    # Scope (optional - null means account-wide)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)  # meta
    campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    adset_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Target metrics
    metric_type: Mapped[TargetMetric] = mapped_column(SQLEnum(TargetMetric), nullable=False)
    target_value: Mapped[float] = mapped_column(Float, nullable=False)
    target_value_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # For monetary values

    # Bounds (optional)
    min_value: Mapped[float | None] = mapped_column(Float, nullable=True)  # Alert if below
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)  # Alert if above

    # Alert thresholds (percentage deviation)
    warning_threshold_pct: Mapped[float | None] = mapped_column(Float, default=10.0, nullable=True)  # Warn at 10% deviation
    critical_threshold_pct: Mapped[float | None] = mapped_column(Float, default=20.0, nullable=True)  # Critical at 20% deviation

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Notification settings
    notify_slack: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    notify_email: Mapped[bool | None] = mapped_column(Boolean, default=True, nullable=True)
    notify_whatsapp: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    notification_recipients: Mapped[Any] = mapped_column(JSONB, nullable=True)  # List of user IDs or emails

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    alerts = relationship("PacingAlert", back_populates="target", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_targets_tenant_period", "tenant_id", "period_start", "period_end"),
        Index("ix_targets_tenant_metric", "tenant_id", "metric_type"),
        Index("ix_targets_tenant_active", "tenant_id", "is_active"),
        UniqueConstraint(
            "tenant_id",
            "period_start",
            "period_end",
            "metric_type",
            "platform",
            "campaign_id",
            name="uq_target_scope",
        ),
    )


# =============================================================================
# Daily KPI Model (Materialized)
# =============================================================================


class DailyKPI(Base):
    """
    Materialized daily KPIs for fast pacing calculations.
    Pre-aggregated from platform data and CRM metrics.
    """

    __tablename__ = "daily_kpis"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)

    # Scope (for granular tracking)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)  # null = all platforms
    campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # null = all campaigns
    account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Spend metrics
    spend_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    budget_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # Daily budget if set

    # Traffic metrics
    impressions: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    clicks: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    ctr: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Cost metrics
    cpm_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cpc_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cpa_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cpl_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Conversion metrics (platform-reported)
    conversions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    leads: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    purchases: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Revenue metrics (platform-reported)
    revenue_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    roas: Mapped[float | None] = mapped_column(Float, nullable=True)

    # CRM metrics (from HubSpot/Salesforce)
    crm_leads: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    crm_mqls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    crm_sqls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    crm_opportunities: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    crm_deals_won: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    crm_pipeline_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    crm_won_revenue_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # Computed CRM ROAS
    pipeline_roas: Mapped[float | None] = mapped_column(Float, nullable=True)
    won_roas: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Quality metrics
    frequency: Mapped[float | None] = mapped_column(Float, nullable=True)
    emq_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Day of week (for seasonality)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Monday, 6=Sunday
    is_weekend: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        Index("ix_daily_kpis_tenant_date", "tenant_id", "date"),
        Index("ix_daily_kpis_tenant_platform_date", "tenant_id", "platform", "date"),
        Index("ix_daily_kpis_tenant_campaign_date", "tenant_id", "campaign_id", "date"),
        UniqueConstraint("tenant_id", "date", "platform", "campaign_id", name="uq_daily_kpi_scope"),
    )


# =============================================================================
# Pacing Alert Model
# =============================================================================


class PacingAlert(Base):
    """
    Alert records for pacing issues.
    Tracks when targets are at risk of being missed.
    """

    __tablename__ = "pacing_alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("targets.id", ondelete="CASCADE"), nullable=True
    )

    # Alert details
    alert_type: Mapped[AlertType] = mapped_column(SQLEnum(AlertType), nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(SQLEnum(AlertSeverity), nullable=False, default=AlertSeverity.WARNING)
    status: Mapped[AlertStatus] = mapped_column(SQLEnum(AlertStatus), nullable=False, default=AlertStatus.ACTIVE)

    # Alert message
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[Any] = mapped_column(JSONB, nullable=True)  # Additional context

    # Metrics at time of alert
    current_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    deviation_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Pacing context
    pacing_date: Mapped[date] = mapped_column(Date, nullable=False)
    days_remaining: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mtd_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    mtd_expected: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_eom: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Scope
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Resolution
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Notification tracking
    notifications_sent: Mapped[Any] = mapped_column(JSONB, nullable=True)  # Track what was sent where

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    target = relationship("Target", back_populates="alerts")
    resolved_by = relationship("User", foreign_keys=[resolved_by_user_id])

    __table_args__ = (
        Index("ix_pacing_alerts_tenant_status", "tenant_id", "status"),
        Index("ix_pacing_alerts_tenant_date", "tenant_id", "pacing_date"),
        Index("ix_pacing_alerts_tenant_type", "tenant_id", "alert_type"),
        Index("ix_pacing_alerts_target", "target_id"),
    )


# =============================================================================
# Forecast Model
# =============================================================================


class Forecast(Base):
    """
    Stored forecasts for trend analysis and auditing.
    Tracks daily, weekly, and EOM projections.
    """

    __tablename__ = "forecasts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Forecast metadata
    forecast_date: Mapped[date] = mapped_column(Date, nullable=False)  # Date forecast was made
    forecast_for_date: Mapped[date] = mapped_column(Date, nullable=False)  # Date being forecasted
    forecast_type: Mapped[str] = mapped_column(String(50), nullable=False)  # daily, eom, custom

    # Scope
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Metric being forecasted
    metric_type: Mapped[TargetMetric] = mapped_column(SQLEnum(TargetMetric), nullable=False)

    # Forecast values
    forecasted_value: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_lower: Mapped[float | None] = mapped_column(Float, nullable=True)  # Lower bound (e.g., 90% CI)
    confidence_upper: Mapped[float | None] = mapped_column(Float, nullable=True)  # Upper bound
    confidence_level: Mapped[float | None] = mapped_column(Float, default=0.9, nullable=True)  # Confidence level (e.g., 0.9 for 90%)

    # Model info
    model_type: Mapped[str] = mapped_column(String(50), nullable=False)  # ewma, linear, prophet, etc.
    model_params: Mapped[Any] = mapped_column(JSONB, nullable=True)  # Model parameters used

    # Accuracy tracking (filled in after actual is known)
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    error: Mapped[float | None] = mapped_column(Float, nullable=True)  # actual - forecasted
    error_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # Percentage error
    mape: Mapped[float | None] = mapped_column(Float, nullable=True)  # Mean Absolute Percentage Error

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        Index("ix_forecasts_tenant_date", "tenant_id", "forecast_date"),
        Index("ix_forecasts_tenant_for_date", "tenant_id", "forecast_for_date"),
        Index("ix_forecasts_tenant_metric", "tenant_id", "metric_type"),
    )


# =============================================================================
# Pacing Summary View (Helper)
# =============================================================================


class PacingSummary(Base):
    """
    Pacing summary snapshots (could be a materialized view).
    Stores current pacing status for quick dashboard loading.
    """

    __tablename__ = "pacing_summaries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("targets.id", ondelete="CASCADE"), nullable=False
    )

    # Snapshot date
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)

    # Period info
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    days_elapsed: Mapped[int] = mapped_column(Integer, nullable=False)
    days_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    days_total: Mapped[int] = mapped_column(Integer, nullable=False)

    # Progress
    target_value: Mapped[float] = mapped_column(Float, nullable=False)
    mtd_actual: Mapped[float] = mapped_column(Float, nullable=False)
    mtd_expected: Mapped[float] = mapped_column(Float, nullable=False)  # Pro-rated target

    # Pacing metrics
    pacing_pct: Mapped[float] = mapped_column(Float, nullable=False)  # mtd_actual / mtd_expected * 100
    completion_pct: Mapped[float] = mapped_column(Float, nullable=False)  # mtd_actual / target_value * 100

    # Projections
    projected_eom: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_eom_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_eom_upper: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Gap analysis
    gap_to_target: Mapped[float | None] = mapped_column(Float, nullable=True)  # target - projected_eom
    gap_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Daily needed to hit target
    daily_needed: Mapped[float | None] = mapped_column(Float, nullable=True)  # (target - mtd_actual) / days_remaining
    daily_average: Mapped[float | None] = mapped_column(Float, nullable=True)  # mtd_actual / days_elapsed

    # Status flags
    on_track: Mapped[bool] = mapped_column(Boolean, nullable=False)
    at_risk: Mapped[bool] = mapped_column(Boolean, nullable=False)
    will_miss: Mapped[bool] = mapped_column(Boolean, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    target = relationship("Target", foreign_keys=[target_id])

    __table_args__ = (
        Index("ix_pacing_summaries_tenant_date", "tenant_id", "snapshot_date"),
        Index("ix_pacing_summaries_target", "target_id", "snapshot_date"),
        UniqueConstraint("target_id", "snapshot_date", name="uq_pacing_summary_target_date"),
    )
