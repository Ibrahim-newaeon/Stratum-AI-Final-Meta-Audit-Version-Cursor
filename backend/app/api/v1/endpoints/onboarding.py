# =============================================================================
# Stratum AI - Onboarding Wizard API Endpoints
# =============================================================================
"""
Onboarding wizard endpoints.

Tracks a tenant's progress through the guided setup wizard
(business profile -> platforms -> goals -> automation -> trust gate).
"""

import math
from datetime import UTC, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import get_current_user
from app.base_models import User
from app.core.logging import get_logger
from app.db.session import get_async_session
from app.models.onboarding import OnboardingStatus, OnboardingStep, TenantOnboarding
from app.services.tenant.onboarding import get_or_create_onboarding

logger = get_logger(__name__)
router = APIRouter(prefix="/onboarding", tags=["onboarding"])

_STEP_ORDER = [step.value for step in OnboardingStep]

#: What each wizard step is allowed to write, mapping the payload key the SPA
#: sends to the column it lands in.
#:
#: This is an allowlist rather than a convenience. The previous implementation
#: walked ``payload.data`` and called ``setattr`` for any name the model happened
#: to have, excluding only ``id`` and ``tenant_id`` - so a caller could post
#: ``{"status": "completed"}`` or rewrite ``completed_steps`` and skip the wizard
#: outright. Naming the fields per step closes that and makes the two renames
#: below explicit instead of silent.
#:
#: Three payload keys do not match their column, and every one of them used to be
#: dropped without a word because the model has no attribute by that name:
#: ``platforms``, ``target_cpa`` and ``monthly_budget``.
_STEP_FIELDS: dict[OnboardingStep, dict[str, str]] = {
    OnboardingStep.BUSINESS_PROFILE: {
        "industry": "industry",
        "industry_other": "industry_other",
        "monthly_ad_spend": "monthly_ad_spend",
        "team_size": "team_size",
        "company_website": "company_website",
        "target_markets": "target_markets",
    },
    OnboardingStep.PLATFORM_SELECTION: {
        "platforms": "selected_platforms",
    },
    OnboardingStep.GOALS_SETUP: {
        "primary_kpi": "primary_kpi",
        "target_roas": "target_roas",
        "target_cpa": "target_cpa_cents",
        "monthly_budget": "monthly_budget_cents",
        "currency": "currency",
        "timezone": "timezone",
    },
    OnboardingStep.AUTOMATION_PREFERENCES: {
        "automation_mode": "automation_mode",
        "auto_pause_enabled": "auto_pause_enabled",
        "auto_scale_enabled": "auto_scale_enabled",
        "notification_email": "notification_email",
        "notification_slack": "notification_slack",
        "notification_whatsapp": "notification_whatsapp",
    },
    OnboardingStep.TRUST_GATE_CONFIG: {
        "trust_threshold_autopilot": "trust_threshold_autopilot",
        "trust_threshold_alert": "trust_threshold_alert",
        "require_approval_above": "require_approval_above",
        "max_daily_actions": "max_daily_actions",
    },
}

#: Payload keys the wizard collects in major units and the schema stores in
#: hundredths of them. The form's inputs are plain numbers - "5000" means 5000
#: of the tenant's currency - so they are scaled here rather than in the browser,
#: where a later UI change could silently start sending the other unit.
_MAJOR_UNIT_FIELDS = frozenset({"target_cpa", "monthly_budget"})


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


def _to_cents(value: Any, field_name: str) -> int:
    """
    Scale a major-unit amount to the schema's hundredths-of-major-unit column.

    Args:
        value: The number the wizard collected, in whole currency units.
        field_name: Payload key, used only for the error message.

    Returns:
        The value in hundredths, rounded to the nearest whole unit.

    Raises:
        HTTPException: 422 when the value is not a finite number. Storing a
            budget the caller did not mean is worse than refusing the step.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{field_name}' must be a number",
        )
    if not math.isfinite(value) or value < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"'{field_name}' must be a non-negative finite number",
        )
    return round(value * 100)


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
    record = await get_or_create_onboarding(db, current_user.tenant_id)
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
    record = await get_or_create_onboarding(db, current_user.tenant_id)
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
    record = await get_or_create_onboarding(db, current_user.tenant_id)

    completed = list(record.completed_steps or [])
    if payload.step.value not in completed:
        completed.append(payload.step.value)
    record.completed_steps = completed
    record.status = OnboardingStatus.IN_PROGRESS.value

    # Persist this step's fields, and only this step's fields.
    allowed = _STEP_FIELDS.get(payload.step, {})
    for field_name, value in payload.data.items():
        column = allowed.get(field_name)
        if column is None or value is None:
            continue
        if field_name in _MAJOR_UNIT_FIELDS:
            value = _to_cents(value, field_name)
        setattr(record, column, value)

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
    record = await get_or_create_onboarding(db, current_user.tenant_id)
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
    record = await get_or_create_onboarding(db, current_user.tenant_id)
    if record.status == OnboardingStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Onboarding is already completed",
        )
    record.status = OnboardingStatus.SKIPPED.value
    await db.commit()
    return _to_response(record)
