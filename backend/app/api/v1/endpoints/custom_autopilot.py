# =============================================================================
# Stratum AI - Custom Autopilot Rules Endpoints
# =============================================================================
"""
CRUD + evaluate for Custom Autopilot rules.

Rules enqueue SAFE Autopilot actions into ``fact_actions_queue``. They never
call Meta write clients directly.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_async_session
from app.models.custom_autopilot import (
    CustomAutopilotRule,
    CustomAutopilotRuleExecution,
)
from app.schemas.custom_autopilot import (
    CustomAutopilotExecutionResponse,
    CustomAutopilotRuleCreate,
    CustomAutopilotRuleResponse,
    CustomAutopilotRuleUpdate,
)
from app.schemas import APIResponse, PaginatedResponse
from app.services.custom_autopilot_engine import CustomAutopilotEngine

logger = get_logger(__name__)
router = APIRouter()


def _tenant_id(request: Request) -> int:
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context required",
        )
    return int(tenant_id)


def _dump_conditions_actions(payload: dict) -> dict:
    """Convert nested pydantic models to plain dicts for JSONB storage."""
    out = dict(payload)
    if "conditions" in out and out["conditions"] is not None:
        out["conditions"] = [
            c.model_dump() if hasattr(c, "model_dump") else c for c in out["conditions"]
        ]
    if "actions" in out and out["actions"] is not None:
        out["actions"] = [
            a.model_dump() if hasattr(a, "model_dump") else a for a in out["actions"]
        ]
    return out


@router.get("", response_model=APIResponse[PaginatedResponse[CustomAutopilotRuleResponse]])
async def list_custom_autopilot_rules(
    request: Request,
    db: AsyncSession = Depends(get_async_session),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: Optional[str] = Query(None, alias="status"),
):
    """List Custom Autopilot rules for the current tenant."""
    tenant_id = _tenant_id(request)
    query = select(CustomAutopilotRule).where(
        CustomAutopilotRule.tenant_id == tenant_id,
        CustomAutopilotRule.is_deleted.is_(False),
    )
    if status_filter:
        query = query.where(CustomAutopilotRule.status == status_filter)

    count_query = select(func.count()).select_from(query.subquery())
    total = int((await db.execute(count_query)).scalar() or 0)

    offset = (page - 1) * page_size
    result = await db.execute(
        query.order_by(CustomAutopilotRule.created_at.desc()).offset(offset).limit(page_size)
    )
    rules = list(result.scalars().all())

    return APIResponse(
        success=True,
        data=PaginatedResponse(
            items=[CustomAutopilotRuleResponse.model_validate(r) for r in rules],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size if page_size else 0,
        ),
    )


@router.get("/{rule_id}", response_model=APIResponse[CustomAutopilotRuleResponse])
async def get_custom_autopilot_rule(
    request: Request,
    rule_id: UUID,
    db: AsyncSession = Depends(get_async_session),
):
    """Get one Custom Autopilot rule."""
    tenant_id = _tenant_id(request)
    result = await db.execute(
        select(CustomAutopilotRule).where(
            CustomAutopilotRule.id == rule_id,
            CustomAutopilotRule.tenant_id == tenant_id,
            CustomAutopilotRule.is_deleted.is_(False),
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    return APIResponse(success=True, data=CustomAutopilotRuleResponse.model_validate(rule))


@router.post(
    "",
    response_model=APIResponse[CustomAutopilotRuleResponse],
    status_code=status.HTTP_201_CREATED,
)
async def create_custom_autopilot_rule(
    request: Request,
    body: CustomAutopilotRuleCreate,
    db: AsyncSession = Depends(get_async_session),
):
    """Create a Custom Autopilot rule."""
    tenant_id = _tenant_id(request)
    data = _dump_conditions_actions(body.model_dump())
    rule = CustomAutopilotRule(tenant_id=tenant_id, **data)
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    logger.info("custom_autopilot_rule_created", rule_id=str(rule.id), tenant_id=tenant_id)
    return APIResponse(
        success=True,
        data=CustomAutopilotRuleResponse.model_validate(rule),
        message="Rule created successfully",
    )


@router.patch("/{rule_id}", response_model=APIResponse[CustomAutopilotRuleResponse])
async def update_custom_autopilot_rule(
    request: Request,
    rule_id: UUID,
    body: CustomAutopilotRuleUpdate,
    db: AsyncSession = Depends(get_async_session),
):
    """Update a Custom Autopilot rule."""
    tenant_id = _tenant_id(request)
    result = await db.execute(
        select(CustomAutopilotRule).where(
            CustomAutopilotRule.id == rule_id,
            CustomAutopilotRule.tenant_id == tenant_id,
            CustomAutopilotRule.is_deleted.is_(False),
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    updates = _dump_conditions_actions(body.model_dump(exclude_unset=True))
    for field, value in updates.items():
        setattr(rule, field, value)
    rule.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(rule)
    logger.info("custom_autopilot_rule_updated", rule_id=str(rule_id))
    return APIResponse(
        success=True,
        data=CustomAutopilotRuleResponse.model_validate(rule),
        message="Rule updated successfully",
    )


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_custom_autopilot_rule(
    request: Request,
    rule_id: UUID,
    db: AsyncSession = Depends(get_async_session),
):
    """Soft-delete a Custom Autopilot rule."""
    tenant_id = _tenant_id(request)
    result = await db.execute(
        select(CustomAutopilotRule).where(
            CustomAutopilotRule.id == rule_id,
            CustomAutopilotRule.tenant_id == tenant_id,
            CustomAutopilotRule.is_deleted.is_(False),
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    rule.is_deleted = True
    rule.updated_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("custom_autopilot_rule_deleted", rule_id=str(rule_id))


@router.post("/{rule_id}/activate", response_model=APIResponse[CustomAutopilotRuleResponse])
async def activate_custom_autopilot_rule(
    request: Request,
    rule_id: UUID,
    db: AsyncSession = Depends(get_async_session),
):
    """Set rule status to active."""
    return await _set_status(request, rule_id, db, "active")


@router.post("/{rule_id}/pause", response_model=APIResponse[CustomAutopilotRuleResponse])
async def pause_custom_autopilot_rule(
    request: Request,
    rule_id: UUID,
    db: AsyncSession = Depends(get_async_session),
):
    """Set rule status to paused."""
    return await _set_status(request, rule_id, db, "paused")


async def _set_status(
    request: Request,
    rule_id: UUID,
    db: AsyncSession,
    new_status: str,
) -> APIResponse[CustomAutopilotRuleResponse]:
    tenant_id = _tenant_id(request)
    result = await db.execute(
        select(CustomAutopilotRule).where(
            CustomAutopilotRule.id == rule_id,
            CustomAutopilotRule.tenant_id == tenant_id,
            CustomAutopilotRule.is_deleted.is_(False),
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")
    rule.status = new_status
    rule.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(rule)
    return APIResponse(
        success=True,
        data=CustomAutopilotRuleResponse.model_validate(rule),
        message=f"Rule {new_status}",
    )


@router.post("/{rule_id}/evaluate", response_model=APIResponse[dict])
async def evaluate_custom_autopilot_rule(
    request: Request,
    rule_id: UUID,
    db: AsyncSession = Depends(get_async_session),
):
    """Evaluate one rule now (still subject to Trust Gate)."""
    tenant_id = _tenant_id(request)
    result = await db.execute(
        select(CustomAutopilotRule).where(
            CustomAutopilotRule.id == rule_id,
            CustomAutopilotRule.tenant_id == tenant_id,
            CustomAutopilotRule.is_deleted.is_(False),
        )
    )
    rule = result.scalar_one_or_none()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    engine = CustomAutopilotEngine(db)
    outcome = await engine.evaluate_rule(rule)
    await db.commit()
    return APIResponse(success=True, data=outcome, message="Evaluation complete")


@router.get(
    "/{rule_id}/executions",
    response_model=APIResponse[PaginatedResponse[CustomAutopilotExecutionResponse]],
)
async def list_custom_autopilot_executions(
    request: Request,
    rule_id: UUID,
    db: AsyncSession = Depends(get_async_session),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List execution audit rows for a rule."""
    tenant_id = _tenant_id(request)
    rule_result = await db.execute(
        select(CustomAutopilotRule.id).where(
            CustomAutopilotRule.id == rule_id,
            CustomAutopilotRule.tenant_id == tenant_id,
            CustomAutopilotRule.is_deleted.is_(False),
        )
    )
    if rule_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rule not found")

    base = select(CustomAutopilotRuleExecution).where(
        CustomAutopilotRuleExecution.tenant_id == tenant_id,
        CustomAutopilotRuleExecution.rule_id == rule_id,
    )
    total = int(
        (
            await db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar()
        or 0
    )
    offset = (page - 1) * page_size
    result = await db.execute(
        base.order_by(CustomAutopilotRuleExecution.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    rows = list(result.scalars().all())
    return APIResponse(
        success=True,
        data=PaginatedResponse(
            items=[CustomAutopilotExecutionResponse.model_validate(r) for r in rows],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size if page_size else 0,
        ),
    )
