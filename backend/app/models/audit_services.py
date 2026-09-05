# =============================================================================
# Stratum AI - Audit Services Database Models
# =============================================================================
"""
Database models for audit-recommended services:
- EMQ Measurements
- Offline Conversions
- Model A/B Testing
- Conversion Latency
- Creative Performance
- Competitor Benchmarks
- Budget Reallocation
- Audience Insights
- LTV Predictions
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


class EMQStatus(str, enum.Enum):
    """EMQ calculation status."""

    PENDING = "pending"
    CALCULATED = "calculated"
    FAILED = "failed"


class ConversionUploadStatus(str, enum.Enum):
    """Offline conversion upload status."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class ExperimentStatus(str, enum.Enum):
    """Model experiment status."""

    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ReallocationStatus(str, enum.Enum):
    """Budget reallocation status."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"


class CustomerSegment(str, enum.Enum):
    """Customer LTV segment."""

    VIP = "vip"
    HIGH_VALUE = "high_value"
    MEDIUM_VALUE = "medium_value"
    LOW_VALUE = "low_value"
    AT_RISK = "at_risk"


# =============================================================================
# EMQ Measurements
# =============================================================================


class EMQMeasurement(Base):
    """
    Stores EMQ (Event Match Quality) measurements from platforms.
    """

    __tablename__ = "emq_measurements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Measurement details
    platform: Mapped[str] = mapped_column(String(50), nullable=False)  # meta
    pixel_id: Mapped[str] = mapped_column(String(255), nullable=False)
    measurement_date: Mapped[date] = mapped_column(Date, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)  # Purchase, Lead, AddToCart, etc.

    # EMQ scores (0-10 scale)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    parameter_quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    deduplication_quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_coverage: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Event counts
    events_received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    events_matched: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    events_attributed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Match rates
    email_match_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    phone_match_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    combined_match_rate: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Status
    status: Mapped[EMQStatus] = mapped_column(SQLEnum(EMQStatus), default=EMQStatus.PENDING, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Raw response
    raw_response: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Recommendations
    recommendations: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        Index("ix_emq_tenant_date", "tenant_id", "measurement_date"),
        Index("ix_emq_platform", "tenant_id", "platform", "pixel_id"),
        UniqueConstraint(
            "tenant_id",
            "platform",
            "pixel_id",
            "measurement_date",
            "event_type",
            name="uq_emq_measurement",
        ),
    )


# =============================================================================
# Offline Conversions
# =============================================================================


class OfflineConversionBatch(Base):
    """
    Tracks batches of offline conversions uploaded to platforms.
    """

    __tablename__ = "offline_conversion_batches"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Batch details
    batch_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    upload_type: Mapped[str] = mapped_column(String(50), nullable=False)  # crm_sync, manual, scheduled

    # Counts
    total_records: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successful_records: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_records: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duplicate_records: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Status
    status: Mapped[ConversionUploadStatus] = mapped_column(
        SQLEnum(ConversionUploadStatus), default=ConversionUploadStatus.PENDING, nullable=False
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Platform response
    platform_batch_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform_response: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # File info
    source_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Timestamps
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Created by
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        Index("ix_offline_batch_tenant", "tenant_id", "created_at"),
        Index("ix_offline_batch_status", "tenant_id", "status"),
    )


class OfflineConversion(Base):
    """
    Individual offline conversion records.
    """

    __tablename__ = "offline_conversions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("offline_conversion_batches.id", ondelete="CASCADE"),
        nullable=True,
    )

    # Conversion details
    event_name: Mapped[str] = mapped_column(String(100), nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)

    # Identifiers (hashed)
    email_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    click_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # e.g. fbclid

    # Platform info
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    platform_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Upload status
    uploaded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    upload_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_upload_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    batch = relationship("OfflineConversionBatch", foreign_keys=[batch_id])

    __table_args__ = (
        Index("ix_offline_conv_tenant", "tenant_id", "event_time"),
        Index("ix_offline_conv_batch", "batch_id"),
        Index("ix_offline_conv_uploaded", "tenant_id", "uploaded"),
    )


# =============================================================================
# Model A/B Testing
# =============================================================================


class ModelExperiment(Base):
    """
    ML model A/B testing experiments.
    """

    __tablename__ = "model_experiments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Experiment details
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)  # roas_model, ltv_model, etc.

    # Variants
    champion_version: Mapped[str] = mapped_column(String(50), nullable=False)
    challenger_version: Mapped[str] = mapped_column(String(50), nullable=False)

    # Traffic split (0-1, percentage going to challenger)
    traffic_split: Mapped[float] = mapped_column(Float, default=0.1, nullable=False)

    # Status
    status: Mapped[ExperimentStatus] = mapped_column(SQLEnum(ExperimentStatus), default=ExperimentStatus.DRAFT, nullable=False)

    # Configuration
    min_samples: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    significance_threshold: Mapped[float] = mapped_column(Float, default=0.05, nullable=False)
    primary_metric: Mapped[str] = mapped_column(String(50), default="mae", nullable=False)

    # Results
    champion_predictions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    challenger_predictions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    champion_metrics: Mapped[Any] = mapped_column(JSONB, nullable=True)
    challenger_metrics: Mapped[Any] = mapped_column(JSONB, nullable=True)
    winner: Mapped[str | None] = mapped_column(String(20), nullable=True)  # champion, challenger, inconclusive

    # Statistical results
    p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    effect_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_interval: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Timestamps
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Created by
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        Index("ix_model_exp_tenant", "tenant_id"),
        Index("ix_model_exp_status", "tenant_id", "status"),
        Index("ix_model_exp_model", "tenant_id", "model_name"),
    )


class ExperimentPrediction(Base):
    """
    Individual predictions made during an experiment.
    """

    __tablename__ = "experiment_predictions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_experiments.id", ondelete="CASCADE"), nullable=False
    )

    # Prediction details
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)  # campaign_id, customer_id, etc.
    variant: Mapped[str] = mapped_column(String(20), nullable=False)  # champion, challenger
    predicted_value: Mapped[float] = mapped_column(Float, nullable=False)
    actual_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Features used
    features: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Timestamps
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    actual_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_exp_pred_experiment", "experiment_id"),
        Index("ix_exp_pred_variant", "experiment_id", "variant"),
    )


# =============================================================================
# Conversion Latency
# =============================================================================


class ConversionLatency(Base):
    """
    Tracks latency between ad click and conversion.
    """

    __tablename__ = "conversion_latencies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Event details
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Latency measurement (milliseconds)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)

    # Context
    campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)  # browser, server, mobile

    # Timestamps
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        Index("ix_conv_latency_tenant", "tenant_id", "event_time"),
        Index("ix_conv_latency_platform", "tenant_id", "platform", "event_type"),
    )


class ConversionLatencyStats(Base):
    """
    Aggregated conversion latency statistics by period.
    """

    __tablename__ = "conversion_latency_stats"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Aggregation period
    period_date: Mapped[date] = mapped_column(Date, nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)

    # Statistics (milliseconds)
    event_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    avg_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    median_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    p95_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    p99_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        Index("ix_latency_stats_tenant", "tenant_id", "period_date"),
        UniqueConstraint(
            "tenant_id", "period_date", "platform", "event_type", name="uq_latency_stats"
        ),
    )


# =============================================================================
# Creative Performance
# =============================================================================


class Creative(Base):
    """
    Creative assets being tracked.
    """

    __tablename__ = "creatives"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Creative details
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)  # Platform's creative ID
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    creative_type: Mapped[str | None] = mapped_column(String(50), nullable=True)  # image, video, carousel, etc.

    # Asset info
    asset_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadata
    metadata: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        Index("ix_creative_tenant", "tenant_id"),
        Index("ix_creative_external", "tenant_id", "platform", "external_id"),
        UniqueConstraint("tenant_id", "platform", "external_id", name="uq_creative"),
    )


class CreativePerformance(Base):
    """
    Daily creative performance metrics.
    """

    __tablename__ = "creative_performance"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    creative_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("creatives.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Period
    date: Mapped[date] = mapped_column(Date, nullable=False)
    campaign_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Metrics
    impressions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conversions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    spend_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    revenue_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    # Calculated metrics
    ctr: Mapped[float | None] = mapped_column(Float, nullable=True)
    cvr: Mapped[float | None] = mapped_column(Float, nullable=True)
    cpc_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cpm_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    roas: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Engagement metrics
    video_views: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    video_completions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    engagements: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shares: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Fatigue indicators
    frequency: Mapped[float | None] = mapped_column(Float, nullable=True)  # avg impressions per user
    reach: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    creative = relationship("Creative", foreign_keys=[creative_id])
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        Index("ix_creative_perf_tenant", "tenant_id", "date"),
        Index("ix_creative_perf_creative", "creative_id", "date"),
        UniqueConstraint("creative_id", "date", "campaign_id", name="uq_creative_perf"),
    )


class CreativeFatigueAlert(Base):
    """
    Alerts when creative fatigue is detected.
    """

    __tablename__ = "creative_fatigue_alerts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    creative_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("creatives.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Alert details
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)  # declining_ctr, high_frequency, etc.
    severity: Mapped[str] = mapped_column(String(20), nullable=False)  # info, warning, critical

    # Metrics at alert time
    current_ctr: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_ctr: Mapped[float | None] = mapped_column(Float, nullable=True)
    ctr_decline_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_frequency: Mapped[float | None] = mapped_column(Float, nullable=True)
    days_active: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Recommendations
    recommendation: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status
    is_acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    creative = relationship("Creative", foreign_keys=[creative_id])
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    acknowledged_by = relationship("User", foreign_keys=[acknowledged_by_user_id])

    __table_args__ = (
        Index("ix_fatigue_alert_tenant", "tenant_id", "created_at"),
        Index("ix_fatigue_alert_creative", "creative_id"),
    )


# =============================================================================
# Competitor Benchmarking
# =============================================================================


class CompetitorBenchmark(Base):
    """
    Stores competitor benchmark comparisons.
    """

    __tablename__ = "competitor_benchmarks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Benchmark context
    date: Mapped[date] = mapped_column(Date, nullable=False)
    industry: Mapped[str] = mapped_column(String(100), nullable=False)
    region: Mapped[str] = mapped_column(String(50), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)

    # Your metrics
    your_metrics: Mapped[Any] = mapped_column(JSONB, nullable=False)

    # Industry benchmarks
    industry_metrics: Mapped[Any] = mapped_column(JSONB, nullable=False)  # {metric: {p25, p50, p75, p90, avg}}

    # Percentile rankings
    percentile_rankings: Mapped[Any] = mapped_column(JSONB, nullable=False)  # {metric: percentile}

    # Performance summary
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-100
    metrics_above_median: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metrics_below_median: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Recommendations
    recommendations: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        Index("ix_benchmark_tenant", "tenant_id", "date"),
        Index("ix_benchmark_industry", "tenant_id", "industry", "platform"),
    )


# =============================================================================
# Budget Reallocation
# =============================================================================


class BudgetReallocationPlan(Base):
    """
    Budget reallocation plans.
    """

    __tablename__ = "budget_reallocation_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Plan details
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    strategy: Mapped[str] = mapped_column(String(50), nullable=False)  # roas_maximization, volume_maximization, etc.

    # Configuration
    config: Mapped[Any] = mapped_column(JSONB, nullable=False)

    # Budget summary
    total_current_budget_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_new_budget_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    campaigns_affected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Expected impact
    projected_roas_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_spend_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    projected_revenue_change: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Status
    status: Mapped[ReallocationStatus] = mapped_column(
        SQLEnum(ReallocationStatus), default=ReallocationStatus.PROPOSED, nullable=False
    )

    # Approval
    approved_by_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Execution
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rollback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Created by
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    approved_by = relationship("User", foreign_keys=[approved_by_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    __table_args__ = (
        Index("ix_realloc_plan_tenant", "tenant_id", "created_at"),
        Index("ix_realloc_plan_status", "tenant_id", "status"),
    )


class BudgetReallocationChange(Base):
    """
    Individual campaign budget changes in a reallocation plan.
    """

    __tablename__ = "budget_reallocation_changes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("budget_reallocation_plans.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Campaign details
    campaign_id: Mapped[str] = mapped_column(String(255), nullable=False)
    campaign_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)

    # Budget change
    current_budget_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    new_budget_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    change_percent: Mapped[float] = mapped_column(Float, nullable=False)

    # Rationale
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    performance_metrics: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Execution status
    executed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    plan = relationship("BudgetReallocationPlan", foreign_keys=[plan_id])

    __table_args__ = (Index("ix_realloc_change_plan", "plan_id"),)


# =============================================================================
# Audience Insights
# =============================================================================


class AudienceRecord(Base):
    """
    Audience records for analysis.
    """

    __tablename__ = "audience_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Audience details
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    audience_type: Mapped[str] = mapped_column(String(50), nullable=False)  # custom, lookalike, interest, etc.
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Configuration
    lookalike_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_audience_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Quality scores
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-100
    expansion_potential: Mapped[str | None] = mapped_column(String(20), nullable=True)  # high, medium, low, saturated

    # Performance
    current_metrics: Mapped[Any] = mapped_column(JSONB, nullable=True)
    historical_metrics: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # LTV data
    avg_ltv: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        Index("ix_audience_tenant", "tenant_id"),
        Index("ix_audience_external", "tenant_id", "platform", "external_id"),
        UniqueConstraint("tenant_id", "platform", "external_id", name="uq_audience"),
    )


class AudienceOverlapRecord(Base):
    """
    Records of overlap between audiences.
    """

    __tablename__ = "audience_overlaps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    audience_id_1: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audience_records.id", ondelete="CASCADE"), nullable=False
    )
    audience_id_2: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("audience_records.id", ondelete="CASCADE"), nullable=False
    )

    # Overlap metrics
    overlap_percent: Mapped[float] = mapped_column(Float, nullable=False)
    overlap_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Analysis date
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    audience_1 = relationship("AudienceRecord", foreign_keys=[audience_id_1])
    audience_2 = relationship("AudienceRecord", foreign_keys=[audience_id_2])

    __table_args__ = (
        Index("ix_overlap_tenant", "tenant_id"),
        Index("ix_overlap_audiences", "audience_id_1", "audience_id_2"),
    )


# =============================================================================
# LTV Predictions
# =============================================================================


class CustomerLTVPrediction(Base):
    """
    Customer lifetime value predictions.
    """

    __tablename__ = "customer_ltv_predictions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Customer identifier
    customer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Acquisition info
    acquisition_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    acquisition_channel: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Behavior metrics (at prediction time)
    total_orders: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_revenue_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    avg_order_value_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    days_since_last_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Predictions
    predicted_ltv_30d_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    predicted_ltv_90d_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    predicted_ltv_365d_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    predicted_ltv_lifetime_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Segment
    segment: Mapped[CustomerSegment | None] = mapped_column(SQLEnum(CustomerSegment), nullable=True)

    # Risk
    churn_probability: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Confidence
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Recommendation
    max_cac_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Model info
    model_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Timestamps
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        Index("ix_ltv_pred_tenant", "tenant_id", "predicted_at"),
        Index("ix_ltv_pred_customer", "tenant_id", "customer_id"),
        Index("ix_ltv_pred_segment", "tenant_id", "segment"),
    )


class LTVCohortAnalysis(Base):
    """
    Cohort-based LTV analysis.
    """

    __tablename__ = "ltv_cohort_analyses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Cohort identifier
    cohort_month: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM

    # Cohort metrics
    customer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_revenue_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    avg_ltv_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    median_ltv_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ltv_p90_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Purchase metrics
    avg_orders: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_retention_days: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Segment distribution
    segment_distribution: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Analysis date
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        Index("ix_ltv_cohort_tenant", "tenant_id"),
        UniqueConstraint("tenant_id", "cohort_month", name="uq_ltv_cohort"),
    )


# =============================================================================
# Model Retraining
# =============================================================================


class ModelRetrainingJob(Base):
    """
    Model retraining job records.
    """

    __tablename__ = "model_retraining_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )  # Null for global models

    # Job details
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)  # scheduled, manual, triggered
    trigger_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status
    status: Mapped[str] = mapped_column(String(50), nullable=False)  # pending, running, completed, failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Training data
    training_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    training_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Model info
    old_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    new_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Metrics
    old_metrics: Mapped[Any] = mapped_column(JSONB, nullable=True)
    new_metrics: Mapped[Any] = mapped_column(JSONB, nullable=True)
    improvement: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Timing
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        Index("ix_retrain_job_tenant", "tenant_id", "created_at"),
        Index("ix_retrain_job_model", "model_name", "created_at"),
        Index("ix_retrain_job_status", "status"),
    )
