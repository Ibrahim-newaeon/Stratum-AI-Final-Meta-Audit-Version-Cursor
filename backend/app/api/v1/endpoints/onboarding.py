# =============================================================================
# Stratum AI - Onboarding Wizard API Endpoints
# =============================================================================
"""
Onboarding wizard endpoints.

Tracks a tenant's progress through the guided setup wizard
(business profile -> platforms -> goals -> automation -> trust gate).
"""

from datetime import UTC, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.base_models import User
from app.core.logging import get_logger
from app.db.session import get_async_session
from app.models.onboarding import OnboardingStatus, OnboardingStep, TenantOnboarding

logger = get_logger(__name__)
router = APIRouter(prefix="/onboarding", tags=["onboarding"])

_STEP_ORDER = [step.value for step in OnboardingStep]


# =============================================================================
# Schemas
# =============================================================================


class OnboardingStatusResponse(BaseModel):
    """Current onboarding progress for a tenant."""

    tenant_id: int
    status: str
    current_step: str
    completed_steps: list[str] = Field(default_factory=list)
    progress_percent: int = 0


class OnboardingStepUpdate(BaseModel):
    """Payload for completing a wizard step."""

    step: OnboardingStep
    data: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Helpers
# =============================================================================


async def _get_or_create_onboarding(
    db: AsyncSession, tenant_id: int
) -> TenantOnboarding:
    """Fetch (or lazily create) the tenant's onboarding record."""
    result = await db.execute(
        select(TenantOnboarding).where(TenantOnboarding.tenant_id == tenant_id)
    )
    record = result.scalar_one_or_none()
    if record is None:
        record = TenantOnboarding(
            tenant_id=tenant_id,
            status=OnboardingStatus.NOT_STARTED.value,
            current_step=OnboardingStep.BUSINESS_PROFILE.value,
            completed_steps=[],
        )
        db.add(record)
        await db.flush()
    return record


def _progress(record: TenantOnboarding) -> int:
    """Compute progress percent from completed steps."""
    completed = len(record.completed_steps or [])
    return min(int(completed / len(_STEP_ORDER) * 100), 100)


def _to_response(record: TenantOnboarding) -> OnboardingStatusResponse:
    return OnboardingStatusResponse(
        tenant_id=record.tenant_id,
        status=record.status,
        current_step=record.current_step,
        completed_steps=list(record.completed_steps or []),
        progress_percent=_progress(record),
    )


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/status", response_model=OnboardingStatusResponse)
async def get_onboarding_status(
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user),
) -> OnboardingStatusResponse:
    """Get onboarding status for the current tenant."""
    record = await _get_or_create_onboarding(db, current_user.tenant_id)
    await db.commit()
    return _to_response(record)


@router.get("/check")
async def check_onboarding(
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """
    Lightweight gate used by the frontend on every dashboard load.

    Returns the APIResponse envelope with ``required`` (True while the wizard
    is neither completed nor skipped) and the current step.
    """
    record = await _get_or_create_onboarding(db, current_user.tenant_id)
    await db.commit()
    finished = {OnboardingStatus.COMPLETED.value, OnboardingStatus.SKIPPED.value}
    return {
        "success": True,
        "data": {
            "required": record.status not in finished,
            "current_step": record.current_step,
        },
        "message": None,
        "errors": None,
    }


@router.post("/steps", response_model=OnboardingStatusResponse)
async def complete_onboarding_step(
    payload: OnboardingStepUpdate,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user),
) -> OnboardingStatusResponse:
    """Mark a wizard step as completed and store its collected data."""
    record = await _get_or_create_onboarding(db, current_user.tenant_id)

    completed = list(record.completed_steps or [])
    if payload.step.value not in completed:
        completed.append(payload.step.value)
    record.completed_steps = completed
    record.status = OnboardingStatus.IN_PROGRESS.value

    # Persist known fields from the step data onto the record
    for field_name, value in payload.data.items():
        if hasattr(record, field_name) and field_name not in {"id", "tenant_id"}:
            setattr(record, field_name, value)

    # Advance to the next incomplete step
    next_step: Optional[str] = None
    for step_value in _STEP_ORDER:
        if step_value not in completed:
            next_step = step_value
            break
    if next_step:
        record.current_step = next_step
    else:
        record.status = OnboardingStatus.COMPLETED.value

    await db.commit()

    logger.info(
        "onboarding_step_completed",
        tenant_id=current_user.tenant_id,
        step=payload.step.value,
    )
    return _to_response(record)


@router.post("/complete", response_model=OnboardingStatusResponse)
async def complete_onboarding(
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user),
) -> OnboardingStatusResponse:
    """Mark onboarding as fully completed."""
    record = await _get_or_create_onboarding(db, current_user.tenant_id)
    record.status = OnboardingStatus.COMPLETED.value
    if hasattr(record, "completed_at"):
        record.completed_at = datetime.now(UTC)
    await db.commit()

    logger.info("onboarding_completed", tenant_id=current_user.tenant_id)
    return _to_response(record)


@router.post("/skip", response_model=OnboardingStatusResponse)
async def skip_onboarding(
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_user),
) -> OnboardingStatusResponse:
    """Skip the onboarding wizard."""
    record = await _get_or_create_onboarding(db, current_user.tenant_id)
    if record.status == OnboardingStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Onboarding is already completed",
        )
    record.status = OnboardingStatus.SKIPPED.value
    await db.commit()
    return _to_response(record)
