from typing import TYPE_CHECKING

# =============================================================================
# Stratum AI - Trust Layer Database Models
# =============================================================================
"""
Database models for the Trust Layer:
- FactSignalHealthDaily: Daily signal health metrics
- FactAttributionVarianceDaily: Daily attribution variance metrics
"""

import enum
import uuid
from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import Date, DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.base_models import Tenant, User

# =============================================================================
# Enums
# =============================================================================


class SignalHealthStatus(str, enum.Enum):
    """Signal health status levels."""

    OK = "ok"
    RISK = "risk"
    DEGRADED = "degraded"
    CRITICAL = "critical"


class AttributionVarianceStatus(str, enum.Enum):
    """Attribution variance status levels."""

    HEALTHY = "healthy"
    MINOR_VARIANCE = "minor_variance"
    MODERATE_VARIANCE = "moderate_variance"
    HIGH_VARIANCE = "high_variance"


# =============================================================================
# Models
# =============================================================================


class FactSignalHealthDaily(Base):
    """
    Daily signal health metrics per tenant/platform.
    Tracks EMQ scores, event loss, freshness, and API health.
    """

    __tablename__ = "fact_signal_health_daily"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)  # meta
    account_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Optional, for account-level tracking

    # Signal health metrics
    emq_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # Event Match Quality (0-100)
    event_loss_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # Percentage of lost events (0-100)
    freshness_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)  # Data freshness in minutes
    api_error_rate: Mapped[float | None] = mapped_column(Float, nullable=True)  # API error rate percentage (0-100)

    # Computed status
    status: Mapped[SignalHealthStatus] = mapped_column(
        SQLEnum(
            SignalHealthStatus,
            name="signal_health_status",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=SignalHealthStatus.OK,
    )

    # Additional context
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    issues: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of issue strings
    actions: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of recommended actions

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships - use foreign_keys to resolve ambiguity
    tenant: Mapped["Tenant"] = relationship(
        "Tenant", foreign_keys=[tenant_id], back_populates="signal_health_records"
    )

    __table_args__ = (
        Index("ix_fact_signal_health_daily_tenant_date", "tenant_id", "date"),
        Index("ix_fact_signal_health_daily_tenant_platform", "tenant_id", "platform"),
        Index("ix_fact_signal_health_daily_status", "tenant_id", "status"),
    )


class FactAttributionVarianceDaily(Base):
    """
    Daily attribution variance metrics (Platform vs Analytics).
    Tracks divergence between platform-reported and web analytics data.
    """

    __tablename__ = "fact_attribution_variance_daily"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)

    # Revenue comparison
    ga4_revenue: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    platform_revenue: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    revenue_delta_abs: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    revenue_delta_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Conversion comparison
    ga4_conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    platform_conversions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversion_delta_abs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversion_delta_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Confidence and status
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)  # 0-1
    status: Mapped[AttributionVarianceStatus] = mapped_column(
        SQLEnum(
            AttributionVarianceStatus,
            name="attribution_variance_status",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=AttributionVarianceStatus.HEALTHY,
    )

    # Additional context
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships - use foreign_keys to resolve ambiguity
    tenant: Mapped["Tenant"] = relationship(
        "Tenant", foreign_keys=[tenant_id], back_populates="attribution_variance_records"
    )

    __table_args__ = (
        Index("ix_fact_attribution_variance_daily_tenant_date", "tenant_id", "date"),
        Index("ix_fact_attribution_variance_daily_tenant_platform", "tenant_id", "platform"),
    )


class SignalHealthHistory(Base):
    """
    Aggregated signal health history for trend analysis.
    Stores daily rollups of signal health metrics.
    """

    __tablename__ = "signal_health_history"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)

    # Aggregated scores
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)  # 0-100
    emq_score_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_loss_pct_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    freshness_minutes_avg: Mapped[int | None] = mapped_column(Integer, nullable=True)
    api_error_rate_avg: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Status counts
    platforms_ok: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    platforms_risk: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    platforms_degraded: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    platforms_critical: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)

    # Computed overall status
    status: Mapped[SignalHealthStatus] = mapped_column(
        SQLEnum(
            SignalHealthStatus,
            name="signal_health_status",
            create_type=False,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=SignalHealthStatus.OK,
    )

    # Automation state
    automation_blocked: Mapped[int | None] = mapped_column(
        Integer, default=0
    , nullable=True)  # Boolean stored as int for SQLite compatibility

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_signal_health_history_tenant_date", "tenant_id", "date", unique=True),
    )


class TrustGateAuditLog(Base):
    """
    Audit log for trust gate decisions.
    Tracks every automation decision made by the trust gate.
    """

    __tablename__ = "trust_gate_audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Decision context
    decision_type: Mapped[str] = mapped_column(String(50), nullable=False)  # execute, hold, block
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)  # budget_increase, pause, etc.
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)  # campaign, adset, creative
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Signal health at decision time
    signal_health_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    signal_health_status: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Trust gate evaluation
    gate_passed: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)  # Boolean as int
    gate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON with reasons

    # Thresholds used
    healthy_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    degraded_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Dry run indicator
    is_dry_run: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)  # Boolean as int

    # Action details
    action_payload: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    action_result: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON

    # User context
    triggered_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    triggered_by_system: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)  # Boolean as int

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    triggered_by: Mapped["User | None"] = relationship("User", foreign_keys=[triggered_by_user_id])

    __table_args__ = (
        Index("ix_trust_gate_audit_tenant_date", "tenant_id", "created_at"),
        Index("ix_trust_gate_audit_decision", "tenant_id", "decision_type"),
        Index("ix_trust_gate_audit_entity", "tenant_id", "entity_type", "entity_id"),
    )


class FactActionsQueue(Base):
    """
    Queue for autopilot actions requiring approval or execution.
    Tracks action lifecycle from creation to application.
    """

    __tablename__ = "fact_actions_queue"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)

    # Action details
    action_type: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # budget_increase, budget_decrease, pause, etc.
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)  # campaign, adset, creative
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)

    # Action payload
    action_json: Mapped[str] = mapped_column(Text, nullable=False)  # Full action details as JSON

    # Before/after values for audit
    before_value: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    after_value: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON

    # Workflow status
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="queued"
    )  # queued, approved, applied, failed, dismissed

    # Actors
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    applied_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Result
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform_response: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON

    # Relationships - use foreign_keys to resolve ambiguity
    tenant: Mapped["Tenant"] = relationship("Tenant", foreign_keys=[tenant_id], back_populates="actions_queue")
    created_by: Mapped["User | None"] = relationship("User", foreign_keys=[created_by_user_id])
    approved_by: Mapped["User | None"] = relationship("User", foreign_keys=[approved_by_user_id])
    applied_by: Mapped["User | None"] = relationship("User", foreign_keys=[applied_by_user_id])

    __table_args__ = (
        Index("ix_fact_actions_queue_tenant_date", "tenant_id", "date"),
        Index("ix_fact_actions_queue_status", "tenant_id", "status"),
    )
