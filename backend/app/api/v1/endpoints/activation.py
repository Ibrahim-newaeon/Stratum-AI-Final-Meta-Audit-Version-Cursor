# =============================================================================
# Stratum AI - Meta activation hub API
# =============================================================================
"""Endpoints for the Meta integration activation checklist."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CurrentUserDep
from app.db.session import get_async_session
from app.schemas import APIResponse
from app.services.tenant.activation import compute_activation_status

router = APIRouter(prefix="/activation", tags=["Activation"])


class ActivationStepResponse(BaseModel):
    id: str
    title: str
    description: str
    required: bool
    complete: bool
    action_path: str


class ActivationStatusResponse(BaseModel):
    steps: list[ActivationStepResponse]
    required_complete: bool
    fully_integrated: bool
    progress_percent: int
    required_done: int
    required_total: int


@router.get("/status", response_model=APIResponse[ActivationStatusResponse])
async def get_activation_status(
    current_user: CurrentUserDep,
    db: AsyncSession = Depends(get_async_session),
):
    """Return Meta integration checklist status for the current tenant."""
    payload = await compute_activation_status(db, current_user.tenant_id)
    return APIResponse(success=True, data=payload)
