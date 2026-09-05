# =============================================================================
# Stratum AI - CAPI Delivery Database Models (P0 Gap Fix)
# =============================================================================
"""
Database models for CAPI delivery logging and Dead Letter Queue.

These tables provide persistent storage for:
- All CAPI delivery attempts (success and failure)
- Dead Letter Queue entries for failed events
- Deduplication tracking (optional persistent mode)
"""

import uuid
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (BigInteger, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

# =============================================================================
# CAPI Delivery Log
# =============================================================================


class CAPIDeliveryLog(Base):
    """
    Persistent log of all CAPI delivery attempts.

    Stores both successful and failed deliveries for:
    - Auditing and compliance
    - Debugging failed deliveries
    - EMQ calculation from real delivery data
    - Performance monitoring
    """

    __tablename__ = "capi_delivery_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Event identification
    platform: Mapped[str] = mapped_column(String(50), nullable=False)  # meta, whatsapp
    event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # External event ID for deduplication
    event_name: Mapped[str] = mapped_column(String(100), nullable=False)  # Purchase, Lead, etc.

    # Timing
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # Original event timestamp
    delivery_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)  # When we sent it

    # Delivery status
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # success, failed, retrying, rate_limited
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)  # Delivery latency in milliseconds
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Error details (for failed deliveries)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Platform response
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Platform's request ID
    platform_response: Mapped[Any] = mapped_column(JSONB, nullable=True)  # Full platform response

    # Event data (hashed for correlation, not storing PII)
    user_data_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # SHA256 of user identifiers
    event_value_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # Event value in cents
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)  # Currency code

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        # Query by tenant and time range
        Index("ix_capi_delivery_tenant_time", "tenant_id", "delivery_time"),
        # Query by platform
        Index("ix_capi_delivery_platform", "tenant_id", "platform", "delivery_time"),
        # Query by status for monitoring
        Index("ix_capi_delivery_status", "tenant_id", "status", "delivery_time"),
        # Query by event_id for deduplication investigation
        Index("ix_capi_delivery_event_id", "tenant_id", "platform", "event_id"),
    )


# =============================================================================
# Dead Letter Queue Entry
# =============================================================================


class CAPIDeadLetterEntry(Base):
    """
    Dead Letter Queue entries for failed CAPI events.

    Stores events that failed after max retries for:
    - Manual investigation
    - Automated replay when issues are resolved
    - Root cause analysis
    """

    __tablename__ = "capi_dead_letter_queue"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Event identification
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Full event data (needed for replay)
    event_data: Mapped[Any] = mapped_column(JSONB, nullable=False)

    # Failure information
    failure_reason: Mapped[str] = mapped_column(Text, nullable=False)
    failure_category: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # network_error, rate_limited, auth_error, etc.
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False)

    # Status
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # pending, retrying, recovered, expired, discarded

    # Platform response at failure
    platform_response: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Additional context
    context: Mapped[Any] = mapped_column(JSONB, nullable=True)

    # Timestamps
    first_failure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_failure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        # Query pending entries for retry
        Index("ix_dlq_pending", "tenant_id", "status", "last_failure_at"),
        # Query by platform
        Index("ix_dlq_platform", "tenant_id", "platform", "status"),
        # Query by failure category for analysis
        Index("ix_dlq_category", "tenant_id", "failure_category", "first_failure_at"),
    )


# =============================================================================
# Deduplication Persistence (Optional)
# =============================================================================


class CAPIEventDedupeRecord(Base):
    """
    Optional persistent deduplication records.

    Can be used in addition to Redis for:
    - Cross-datacenter deduplication
    - Long-term deduplication (beyond Redis TTL)
    - Audit trail of processed events
    """

    __tablename__ = "capi_event_dedupe"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Deduplication key
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)  # {platform}:{event_id} or MD5 hash
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # First seen timestamp
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Expiration (for cleanup)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Metadata
    event_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    event_value_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    __table_args__ = (
        # Unique constraint on dedupe key per tenant
        Index("ix_dedupe_key", "tenant_id", "dedupe_key", unique=True),
        # Query for cleanup
        Index("ix_dedupe_expires", "expires_at"),
    )


# =============================================================================
# CAPI Delivery Aggregates (for dashboards)
# =============================================================================


class CAPIDeliveryDailyStats(Base):
    """
    Pre-aggregated daily delivery statistics.

    Reduces query load for dashboards and reporting.
    Populated by scheduled job.
    """

    __tablename__ = "capi_delivery_daily_stats"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Aggregation period
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)

    # Counts
    total_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    successful_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retried_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deduplicated_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Success rate
    success_rate_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Latency statistics (milliseconds)
    avg_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    p50_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    p95_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    p99_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Value statistics
    total_value_cents: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    avg_value_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Error breakdown
    error_counts: Mapped[Any] = mapped_column(JSONB, nullable=True)  # {error_category: count}

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    tenant = relationship("Tenant", foreign_keys=[tenant_id])

    __table_args__ = (
        # Query by date
        Index("ix_delivery_stats_date", "tenant_id", "date", "platform"),
        # Unique per tenant/date/platform
        Index("ix_delivery_stats_unique", "tenant_id", "date", "platform", unique=True),
    )
