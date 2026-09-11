# =============================================================================
# Stratum AI - Custom Autopilot Rules (Meta queue bridge)
# =============================================================================
"""
Persisted if/then rules that enqueue Autopilot queue rows.

Never call ``write_client`` from this module. Meta writes stay on
``fact_actions_queue`` → action executor, gated by Autopilot execution flags.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class CustomAutopilotRuleStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"


class CustomAutopilotRule(Base):
    """Tenant-owned Custom Autopilot rule definition."""

    __tablename__ = "custom_autopilot_rules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=CustomAutopilotRuleStatus.DRAFT.value
    )

    # [{field, operator, value}] — AND only for MVP
    conditions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    # [{type, config}] — SAFE Autopilot action types only
    actions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )

    require_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    cooldown_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    max_executions_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=10)

    last_evaluated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_triggered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trigger_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    __table_args__ = (
        Index("ix_custom_autopilot_rules_tenant_status", "tenant_id", "status"),
    )


class CustomAutopilotRuleExecution(Base):
    """Audit row for one Custom Autopilot evaluation / enqueue attempt."""

    __tablename__ = "custom_autopilot_rule_executions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("custom_autopilot_rules.id", ondelete="CASCADE"),
        nullable=False,
    )
    campaign_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    gate_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    gate_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    matched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enqueued: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    queued_action_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    action_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    detail: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    signal_health_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_custom_autopilot_exec_tenant_created", "tenant_id", "created_at"),
        Index("ix_custom_autopilot_exec_rule", "rule_id", "created_at"),
    )
