# =============================================================================
# Stratum AI - Tenant Management Endpoints
# =============================================================================
"""
Tenant (Organization) management endpoints.
Provides CRUD operations for multi-tenant administration.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import get_async_session
from app.models import Tenant, User, UserRole
from app.schemas import (
    APIResponse,
    TenantCreate,
    TenantResponse,
    TenantUpdate,
)

logger = get_logger(__name__)
router = APIRouter()


def _authorize_tenant_action(
    request: Request,
    target_tenant_id: int | None,
    allowed_roles: tuple[str, ...] | None,
    forbidden_detail: str,
) -> int:
    """
    Authorize the caller for an action on a specific tenant.

    Role semantics come from ``app.base_models.UserRole``: SUPERADMIN is the
    cross-tenant platform role, ADMIN is scoped to a single tenant. Because
    ``auth.signup`` makes the first user of every signup an ADMIN, a role check
    alone is not authorization - the target tenant must be compared with the
    caller's own tenant, otherwise any customer could administer any other
    customer (read their tenant, change their plan, flip their feature flags).

    Args:
        request: Incoming request (identity set by TenantMiddleware from a
            signature-verified access token)
        target_tenant_id: Tenant the action is aimed at, or None for actions
            that are not scoped to an existing tenant
        allowed_roles: Roles permitted to perform the action besides SUPERADMIN,
            or None when every authenticated member of the tenant may perform it
        forbidden_detail: Message for the 403 raised on a role mismatch

    Returns:
        The authenticated caller's user id

    Raises:
        HTTPException: 401 when unauthenticated, 403 on a role mismatch or when
            a tenant-scoped caller targets another tenant
    """
    user_role = getattr(request.state, "role", None)
    user_id = getattr(request.state, "user_id", None)
    caller_tenant_id = getattr(request.state, "tenant_id", None)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    # Platform role: may operate on any tenant, with or without tenant context.
    if user_role == UserRole.SUPERADMIN.value:
        return user_id

    if allowed_roles is not None and user_role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=forbidden_detail,
        )

    # Tenant-scoped roles may only ever act on their own tenant.
    if target_tenant_id is not None and target_tenant_id != caller_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied",
        )

    return user_id


def require_admin(request: Request, target_tenant_id: int | None = None) -> int:
    """
    Verify the caller may administer ``target_tenant_id``.

    A SUPERADMIN may administer any tenant; an ADMIN only their own. Passing no
    target authorizes an action that is not scoped to an existing tenant.

    Args:
        request: Incoming request
        target_tenant_id: Tenant the action is aimed at, if any

    Returns:
        The authenticated caller's user id

    Raises:
        HTTPException: 401 when unauthenticated, 403 when the caller is not an
            admin or is an admin of a different tenant
    """
    return _authorize_tenant_action(
        request,
        target_tenant_id,
        allowed_roles=(UserRole.ADMIN.value,),
        forbidden_detail="Admin access required",
    )


def require_platform_admin(request: Request, target_tenant_id: int | None = None) -> int:
    """
    Verify the caller holds the cross-tenant platform role (SUPERADMIN).

    Used for the entitlement-bearing writes. A tenant ADMIN owns their tenant's
    data but must not be able to grant themselves an entitlement: the plan (and
    the feature flags derived from it) are reconciled from the Paddle Billing
    webhook, so a customer-callable write next to it would make the paid tier
    optional - PATCH /tenants/{own_id}/plan?plan=enterprise was a free upgrade
    to max_users 100 / max_campaigns 1000. Creating tenants is likewise a
    platform action; self-service organisations are created by POST
    /auth/signup.

    Args:
        request: Incoming request
        target_tenant_id: Tenant the action is aimed at, if any

    Returns:
        The authenticated caller's user id

    Raises:
        HTTPException: 401 when unauthenticated, 403 for every non-platform role

    Note:
        ``allowed_roles=()`` is what makes this superadmin-only:
        ``_authorize_tenant_action`` returns early for SUPERADMIN and no other
        role can be in an empty tuple.
    """
    return _authorize_tenant_action(
        request,
        target_tenant_id,
        allowed_roles=(),
        forbidden_detail="Platform admin access required",
    )


def require_tenant_access(request: Request, target_tenant_id: int) -> int:
    """
    Verify the caller may read ``target_tenant_id``.

    Any authenticated member may read their own tenant; only a SUPERADMIN may
    read another one.

    Args:
        request: Incoming request
        target_tenant_id: Tenant being read

    Returns:
        The authenticated caller's user id

    Raises:
        HTTPException: 401 when unauthenticated, 403 when the tenant is not the
            caller's own
    """
    return _authorize_tenant_action(
        request,
        target_tenant_id,
        allowed_roles=None,
        forbidden_detail="Access denied",
    )


@router.get("", response_model=APIResponse[list[TenantResponse]])
async def list_tenants(
    request: Request,
    db: AsyncSession = Depends(get_async_session),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None, max_length=100),
):
    """
    List tenants.

    Only the platform role (SUPERADMIN) sees every tenant; every tenant-scoped
    role - ADMIN included - sees just its own tenant.
    """
    user_role = getattr(request.state, "role", None)
    tenant_id = getattr(request.state, "tenant_id", None)

    query = select(Tenant).where(Tenant.is_deleted == False)

    # Cross-tenant listing is a platform action: a tenant ADMIN is an admin of
    # their own tenant only and must never enumerate other customers.
    if user_role != UserRole.SUPERADMIN.value:
        query = query.where(Tenant.id == tenant_id)

    # Search filter
    if search:
        query = query.where((Tenant.name.ilike(f"%{search}%")) | (Tenant.slug.ilike(f"%{search}%")))

    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    tenants = result.scalars().all()

    return APIResponse(
        success=True,
        data=[
            TenantResponse(
                id=t.id,
                name=t.name,
                slug=t.slug,
                domain=t.domain,
                plan=t.plan,
                plan_expires_at=t.plan_expires_at,
                max_users=t.max_users,
                max_campaigns=t.max_campaigns,
                settings=t.settings or {},
                feature_flags=t.feature_flags or {},
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
            for t in tenants
        ],
    )


@router.get("/current", response_model=APIResponse[TenantResponse])
async def get_current_tenant(
    request: Request,
    db: AsyncSession = Depends(get_async_session),
):
    """Get the current user's tenant."""
    tenant_id = getattr(request.state, "tenant_id", None)

    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.is_deleted == False)
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    return APIResponse(
        success=True,
        data=TenantResponse(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            domain=tenant.domain,
            plan=tenant.plan,
            plan_expires_at=tenant.plan_expires_at,
            max_users=tenant.max_users,
            max_campaigns=tenant.max_campaigns,
            settings=tenant.settings or {},
            feature_flags=tenant.feature_flags or {},
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        ),
    )


@router.get("/{tenant_id}", response_model=APIResponse[TenantResponse])
async def get_tenant(
    request: Request,
    tenant_id: int,
    db: AsyncSession = Depends(get_async_session),
):
    """Get a specific tenant by ID."""
    # Own tenant for any member, any tenant for the platform role.
    require_tenant_access(request, tenant_id)

    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.is_deleted == False)
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    return APIResponse(
        success=True,
        data=TenantResponse(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            domain=tenant.domain,
            plan=tenant.plan,
            plan_expires_at=tenant.plan_expires_at,
            max_users=tenant.max_users,
            max_campaigns=tenant.max_campaigns,
            settings=tenant.settings or {},
            feature_flags=tenant.feature_flags or {},
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        ),
    )


@router.post("", response_model=APIResponse[TenantResponse], status_code=status.HTTP_201_CREATED)
async def create_tenant(
    request: Request,
    tenant_data: TenantCreate,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Create a new tenant.

    Platform action: only the SUPERADMIN role may create tenants. A customer
    ADMIN creating tenants at will (with the plan taken from the request body,
    enterprise limits included) is the same billing bypass as the plan write
    below. Self-service organisation creation is POST /auth/signup.
    """
    require_platform_admin(request)

    # Check for duplicate slug
    result = await db.execute(select(Tenant).where(Tenant.slug == tenant_data.slug))
    existing = result.scalar_one_or_none()

    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tenant with slug '{tenant_data.slug}' already exists",
        )

    # Set plan limits based on plan type
    plan_limits = {
        "free": {"max_users": 5, "max_campaigns": 10},
        "starter": {"max_users": 10, "max_campaigns": 50},
        "professional": {"max_users": 25, "max_campaigns": 200},
        "enterprise": {"max_users": 100, "max_campaigns": 1000},
    }

    limits = plan_limits.get(tenant_data.plan, plan_limits["free"])

    # Create tenant
    tenant = Tenant(
        name=tenant_data.name,
        slug=tenant_data.slug,
        domain=tenant_data.domain,
        plan=tenant_data.plan,
        max_users=limits["max_users"],
        max_campaigns=limits["max_campaigns"],
        settings={},
        feature_flags={},
    )

    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)

    logger.info(f"Tenant created: {tenant.slug} (ID: {tenant.id})")

    return APIResponse(
        success=True,
        data=TenantResponse(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            domain=tenant.domain,
            plan=tenant.plan,
            plan_expires_at=tenant.plan_expires_at,
            max_users=tenant.max_users,
            max_campaigns=tenant.max_campaigns,
            settings=tenant.settings or {},
            feature_flags=tenant.feature_flags or {},
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        ),
        message="Tenant created successfully",
    )


@router.patch("/{tenant_id}", response_model=APIResponse[TenantResponse])
async def update_tenant(
    request: Request,
    tenant_id: int,
    update_data: TenantUpdate,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Update a tenant (name, domain, settings, ...).

    Admins and managers may update their own tenant; only the platform role
    (SUPERADMIN) may update another tenant.
    """
    _authorize_tenant_action(
        request,
        tenant_id,
        allowed_roles=(UserRole.ADMIN.value, UserRole.MANAGER.value),
        forbidden_detail="Admin or manager access required",
    )

    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.is_deleted == False)
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    # Update fields
    update_dict = update_data.model_dump(exclude_unset=True)

    for field, value in update_dict.items():
        if hasattr(tenant, field):
            setattr(tenant, field, value)

    await db.commit()
    await db.refresh(tenant)

    logger.info(f"Tenant updated: {tenant.slug} (ID: {tenant.id})")

    return APIResponse(
        success=True,
        data=TenantResponse(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            domain=tenant.domain,
            plan=tenant.plan,
            plan_expires_at=tenant.plan_expires_at,
            max_users=tenant.max_users,
            max_campaigns=tenant.max_campaigns,
            settings=tenant.settings or {},
            feature_flags=tenant.feature_flags or {},
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        ),
        message="Tenant updated successfully",
    )


@router.delete("/{tenant_id}", response_model=APIResponse[None])
async def delete_tenant(
    request: Request,
    tenant_id: int,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Soft delete a tenant.

    Requires admin rights on the target tenant, so in practice only the platform
    role (SUPERADMIN) can delete: a tenant ADMIN is limited to their own tenant
    and deleting your own tenant is refused below.
    """
    require_admin(request, tenant_id)

    user_tenant_id = getattr(request.state, "tenant_id", None)

    # Prevent self-deletion
    if tenant_id == user_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own tenant",
        )

    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.is_deleted == False)
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    # Soft delete
    tenant.is_deleted = True
    await db.commit()

    logger.info(f"Tenant deleted: {tenant.slug} (ID: {tenant.id})")

    return APIResponse(
        success=True,
        message="Tenant deleted successfully",
    )


@router.get("/{tenant_id}/users", response_model=APIResponse[dict])
async def get_tenant_users(
    request: Request,
    tenant_id: int,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Get users count and list for a tenant.
    """
    # Own tenant for any member, any tenant for the platform role.
    require_tenant_access(request, tenant_id)

    # Get tenant
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.is_deleted == False)
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    # Get user count
    count_result = await db.execute(
        select(func.count(User.id)).where(User.tenant_id == tenant_id, User.is_deleted == False)
    )
    user_count = count_result.scalar()

    return APIResponse(
        success=True,
        data={
            "tenant_id": tenant_id,
            "user_count": user_count,
            "max_users": tenant.max_users,
            "slots_available": tenant.max_users - user_count,
        },
    )


@router.patch("/{tenant_id}/plan", response_model=APIResponse[TenantResponse])
async def update_tenant_plan(
    request: Request,
    tenant_id: int,
    plan: str = Query(..., regex="^(free|starter|professional|enterprise)$"),
    db: AsyncSession = Depends(get_async_session),
):
    """
    Update tenant subscription plan.

    Platform role only. The plan is an entitlement: it is owned by Paddle
    Billing and reconciled by the paddle webhook, so a tenant ADMIN - which is
    what the first user of every signup is - must not be able to PATCH their
    own tenant to enterprise and pick up max_users 100 / max_campaigns 1000 for
    free. Support and billing corrections go through a SUPERADMIN.
    """
    require_platform_admin(request, tenant_id)

    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.is_deleted == False)
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    # Update plan and limits
    plan_limits = {
        "free": {"max_users": 5, "max_campaigns": 10},
        "starter": {"max_users": 10, "max_campaigns": 50},
        "professional": {"max_users": 25, "max_campaigns": 200},
        "enterprise": {"max_users": 100, "max_campaigns": 1000},
    }

    limits = plan_limits[plan]
    tenant.plan = plan
    tenant.max_users = limits["max_users"]
    tenant.max_campaigns = limits["max_campaigns"]

    await db.commit()
    await db.refresh(tenant)

    logger.info(f"Tenant plan updated: {tenant.slug} -> {plan}")

    return APIResponse(
        success=True,
        data=TenantResponse(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            domain=tenant.domain,
            plan=tenant.plan,
            plan_expires_at=tenant.plan_expires_at,
            max_users=tenant.max_users,
            max_campaigns=tenant.max_campaigns,
            settings=tenant.settings or {},
            feature_flags=tenant.feature_flags or {},
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        ),
        message=f"Plan updated to {plan}",
    )


@router.patch("/{tenant_id}/features", response_model=APIResponse[TenantResponse])
async def update_tenant_features(
    request: Request,
    tenant_id: int,
    feature_flags: dict,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Update tenant feature flags.

    Platform role only, for the same reason as the plan write: feature flags
    are the other plan-gated surface, so letting a tenant ADMIN flip their own
    would hand back whatever the plan check withholds.
    """
    require_platform_admin(request, tenant_id)

    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.is_deleted == False)
    )
    tenant = result.scalar_one_or_none()

    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )

    # Merge feature flags
    current_flags = tenant.feature_flags or {}
    current_flags.update(feature_flags)
    tenant.feature_flags = current_flags

    await db.commit()
    await db.refresh(tenant)

    logger.info(f"Tenant features updated: {tenant.slug}")

    return APIResponse(
        success=True,
        data=TenantResponse(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            domain=tenant.domain,
            plan=tenant.plan,
            plan_expires_at=tenant.plan_expires_at,
            max_users=tenant.max_users,
            max_campaigns=tenant.max_campaigns,
            settings=tenant.settings or {},
            feature_flags=tenant.feature_flags or {},
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        ),
        message="Feature flags updated",
    )
