# =============================================================================
# Custom Autopilot schemas
# =============================================================================
"""Pydantic models for Custom Autopilot rules CRUD."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

ALLOWED_CONDITION_FIELDS = frozenset(
    {"roas", "ctr", "cpc", "cpa", "spend", "impressions", "clicks", "conversions"}
)
ALLOWED_OPERATORS = frozenset({"gt", "gte", "lt", "lte", "eq"})
# Map to AutopilotService SAFE_ACTIONS (pause_creative omitted — not in default allowlist)
ALLOWED_ACTION_TYPES = frozenset(
    {"budget_decrease", "pause_adset", "bid_decrease"}
)


class ConditionIn(BaseModel):
    field: str
    operator: str
    value: float

    @field_validator("field")
    @classmethod
    def field_ok(cls, v: str) -> str:
        if v not in ALLOWED_CONDITION_FIELDS:
            raise ValueError(f"Unsupported condition field: {v}")
        return v

    @field_validator("operator")
    @classmethod
    def op_ok(cls, v: str) -> str:
        if v not in ALLOWED_OPERATORS:
            raise ValueError(f"Unsupported operator: {v}")
        return v


class ActionIn(BaseModel):
    type: str
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def type_ok(cls, v: str) -> str:
        if v not in ALLOWED_ACTION_TYPES:
            raise ValueError(
                f"Action '{v}' is not allowed. Custom Autopilot may only enqueue "
                f"SAFE Autopilot actions: {sorted(ALLOWED_ACTION_TYPES)}"
            )
        return v


class CustomAutopilotRuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    status: Literal["draft", "active", "paused"] = "draft"
    conditions: list[ConditionIn] = Field(..., min_length=1)
    actions: list[ActionIn] = Field(..., min_length=1)
    require_approval: bool = True
    cooldown_hours: int = Field(24, ge=0, le=168)
    max_executions_per_day: int = Field(10, ge=1, le=100)


class CustomAutopilotRuleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    status: Optional[Literal["draft", "active", "paused"]] = None
    conditions: Optional[list[ConditionIn]] = None
    actions: Optional[list[ActionIn]] = None
    require_approval: Optional[bool] = None
    cooldown_hours: Optional[int] = Field(None, ge=0, le=168)
    max_executions_per_day: Optional[int] = Field(None, ge=1, le=100)


class CustomAutopilotRuleResponse(BaseModel):
    id: UUID
    tenant_id: int
    name: str
    description: Optional[str]
    status: str
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    require_approval: bool
    cooldown_hours: int
    max_executions_per_day: int
    last_evaluated_at: Optional[datetime]
    last_triggered_at: Optional[datetime]
    trigger_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CustomAutopilotExecutionResponse(BaseModel):
    id: UUID
    rule_id: UUID
    campaign_id: Optional[int]
    gate_decision: str
    gate_reason: Optional[str]
    matched: bool
    enqueued: bool
    queued_action_id: Optional[UUID]
    action_type: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}
