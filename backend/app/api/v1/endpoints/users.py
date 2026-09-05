# =============================================================================
# Stratum AI - User Management Endpoints
# =============================================================================
"""
User profile and management endpoints.
"""

import secrets
from datetime import UTC, datetime
from typing import Optional

import redis.asyncio as redis
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import (
    decrypt_pii,
    encrypt_pii,
    get_password_hash,
    hash_pii_for_lookup,
)
from app.db.session import get_async_session
from app.models import AuditAction, AuditLog, Tenant, User, UserRole
from app.schemas import APIResponse, UserProfileResponse, UserResponse, UserUpdate
from app.api.v1.endpoints.tenants import require_admin
from app.services.email_service import get_email_service

# Redis key prefix for invite tokens
INVITE_TOKEN_PREFIX = "user_invite:"
INVITE_TOKEN_EXPIRY = 7 * 24 * 3600  # 7 days

# Roles a tenant admin may grant through these endpoints. UserRole.SUPERADMIN is
# excluded on purpose - it is the cross-tenant platform role, so granting it from
# a tenant-scoped request would be privilege escalation. An unrecognised role is
# rejected rather than silently defaulted: quietly downgrading a requested role
# hides the mistake, and quietly upgrading one is a security hole.
ASSIGNABLE_ROLES: dict[str, UserRole] = {
    "admin": UserRole.ADMIN,
    "manager": UserRole.MANAGER,
    "analyst": UserRole.ANALYST,
    "viewer": UserRole.VIEWER,
}
INVALID_ROLE_DETAIL = (
    f"Invalid role. Must be one of: {', '.join(sorted(ASSIGNABLE_ROLES))}"
)


class InviteUserRequest(BaseModel):
    """Request schema for inviting a new user."""

    email: EmailStr
    full_name: Optional[str] = None
    # Which tenant to invite into. Omitted, or set to the caller's own tenant,
    # this behaves exactly as before. A different tenant is honoured only for
    # the cross-tenant platform role; see resolve_target_tenant.
    tenant_id: int | None = None
    # Defaults to the least-privileged role: an invite that omits `role` should
    # not hand out more access than the inviter asked for. "user" was the old
    # default and is not a real UserRole member.
    role: str = Field(
        default="viewer", description="User role: admin, manager, analyst, viewer"
    )


class UpdateUserRequest(BaseModel):
    """Request schema for admin updating a user."""

    full_name: Optional[str] = None
    # See InviteUserRequest.tenant_id - same rule, same resolver.
    tenant_id: int | None = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


logger = get_logger(__name__)
router = APIRouter()


async def record_cross_tenant_user_change(
    db: AsyncSession,
    request: Request,
    action: AuditAction,
    target_tenant_id: int,
    caller_tenant_id: int | None,
    subject_user_id: int,
    detail: dict | None = None,
) -> None:
    """
    Audit a user-management write that crossed a tenant boundary.

    Only cross-tenant writes are recorded here. A tenant administering its own
    members is ordinary activity that the endpoints already log; a platform-role
    operator reaching into somebody else's tenant is the thing that has to be
    answerable afterwards, and it is newly possible - before this, naming
    another tenant was not expressible at all.

    The row is written against the **target** tenant, not the caller's: the
    question it exists to answer is "who changed our people?", and that is asked
    from the affected tenant's side. ``user_id`` is the operator who acted.

    No PII is stored - not the invitee's address, only its domain - because this
    row outlives the account it describes and a GDPR erasure of that account
    should not have to chase the audit trail.

    Args:
        db: Async database session; the caller commits.
        request: Incoming request, for the client context.
        action: What was done.
        target_tenant_id: The tenant whose member changed.
        caller_tenant_id: The operator's own tenant, or None.
        subject_user_id: The member that was created, changed or removed.
        detail: Extra non-PII context for the row.
    """
    if caller_tenant_id == target_tenant_id:
        return

    db.add(
        AuditLog(
            tenant_id=target_tenant_id,
            user_id=getattr(request.state, "user_id", None),
            action=action,
            resource_type="user",
            resource_id=str(subject_user_id),
            new_value={
                "acted_by_role": getattr(request.state, "role", None),
                "acted_from_tenant_id": caller_tenant_id,
                **(detail or {}),
            },
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("User-Agent", "")[:500],
            endpoint=str(request.url.path),
            http_method=request.method,
        )
    )


def resolve_target_tenant(request: Request, requested: int | None) -> int:
    """
    Decide which tenant a user-management call acts on, and authorise it.

    These endpoints used to read ``request.state.tenant_id`` and offer no way to
    name a tenant, so a platform-role operator could not administer anybody but
    their own colleagues - and a screen that tried (team management, which
    renders per tenant) silently edited the wrong tenant instead.

    The rule is the one ``tenants.require_admin`` already applies to every other
    tenant-scoped administrative write, and it is deliberately narrow:

    * omit ``requested``, or name your own tenant, and nothing changes - the
      caller's own tenant, exactly as before;
    * name a *different* tenant and only SUPERADMIN, the cross-tenant platform
      role, is honoured. An ADMIN is an admin of one tenant, and gets a 403
      rather than a silent fall back to their own - falling back is how a write
      aimed at somebody else lands at home unnoticed.

    Args:
        request: Incoming request; identity was set by TenantMiddleware from a
            signature-verified access token.
        requested: The tenant the caller asked to act on, or None for their own.

    Returns:
        The tenant id the call is authorised to act on.

    Raises:
        HTTPException: 401 unauthenticated, 403 when the caller may not
            administer the tenant they named, 400 when they have no tenant of
            their own and named none.
    """
    # Authorise before discussing tenants at all. Passing ``requested`` through
    # - None included - means an anonymous or under-privileged caller is refused
    # as such (401/403) rather than being told to name a tenant; with a target
    # it is the full check, and with none it verifies identity and role only.
    require_admin(request, requested)

    caller_tenant_id = getattr(request.state, "tenant_id", None)
    target = requested if requested is not None else caller_tenant_id

    if target is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No tenant to act on: name one with tenant_id",
        )

    return int(target)


@router.get("/me", response_model=APIResponse[UserProfileResponse])
async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_async_session),
):
    """Get the current authenticated user's profile."""
    user_id = getattr(request.state, "user_id", None)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Decrypt PII for response
    return APIResponse(
        success=True,
        data=UserProfileResponse(
            id=user.id,
            tenant_id=user.tenant_id,
            email=decrypt_pii(user.email),
            full_name=decrypt_pii(user.full_name) if user.full_name else None,
            phone=decrypt_pii(user.phone) if user.phone else None,
            role=user.role,
            locale=user.locale,
            timezone=user.timezone,
            is_active=user.is_active,
            is_verified=user.is_verified,
            last_login_at=user.last_login_at,
            avatar_url=user.avatar_url,
            preferences=user.preferences,
            consent_marketing=user.consent_marketing,
            consent_analytics=user.consent_analytics,
            created_at=user.created_at,
            updated_at=user.updated_at,
        ),
    )


@router.patch("/me", response_model=APIResponse[UserProfileResponse])
async def update_current_user(
    request: Request,
    update_data: UserUpdate,
    db: AsyncSession = Depends(get_async_session),
):
    """Update the current user's profile."""
    user_id = getattr(request.state, "user_id", None)

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Update fields
    update_dict = update_data.model_dump(exclude_unset=True)

    # Encrypt PII fields
    if update_dict.get("full_name"):
        update_dict["full_name"] = encrypt_pii(update_dict["full_name"])
    if update_dict.get("phone"):
        update_dict["phone"] = encrypt_pii(update_dict["phone"])

    for field, value in update_dict.items():
        if hasattr(user, field):
            setattr(user, field, value)

    await db.commit()
    await db.refresh(user)

    return APIResponse(
        success=True,
        data=UserProfileResponse(
            id=user.id,
            tenant_id=user.tenant_id,
            email=decrypt_pii(user.email),
            full_name=decrypt_pii(user.full_name) if user.full_name else None,
            phone=decrypt_pii(user.phone) if user.phone else None,
            role=user.role,
            locale=user.locale,
            timezone=user.timezone,
            is_active=user.is_active,
            is_verified=user.is_verified,
            last_login_at=user.last_login_at,
            avatar_url=user.avatar_url,
            preferences=user.preferences,
            consent_marketing=user.consent_marketing,
            consent_analytics=user.consent_analytics,
            created_at=user.created_at,
            updated_at=user.updated_at,
        ),
        message="Profile updated successfully",
    )


@router.get("", response_model=APIResponse[list[UserResponse]])
async def list_users(
    request: Request,
    db: AsyncSession = Depends(get_async_session),
    skip: int = 0,
    limit: int = 50,
):
    """
    List all users in the tenant.
    Requires admin or manager role.
    """
    tenant_id = getattr(request.state, "tenant_id", None)

    result = await db.execute(
        select(User)
        .where(User.tenant_id == tenant_id, User.is_deleted == False)
        .offset(skip)
        .limit(limit)
    )
    users = result.scalars().all()

    return APIResponse(
        success=True,
        data=[
            UserResponse(
                id=u.id,
                tenant_id=u.tenant_id,
                email=decrypt_pii(u.email),
                full_name=decrypt_pii(u.full_name) if u.full_name else None,
                role=u.role,
                locale=u.locale,
                timezone=u.timezone,
                is_active=u.is_active,
                is_verified=u.is_verified,
                last_login_at=u.last_login_at,
                avatar_url=u.avatar_url,
                created_at=u.created_at,
                updated_at=u.updated_at,
            )
            for u in users
        ],
    )


@router.post("/invite", response_model=APIResponse[UserResponse])
async def invite_user(
    request: Request,
    invite_data: InviteUserRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Invite a new user to the tenant.
    Requires admin role.
    Sends an invite email with a link to set up their account.
    """
    requester_user_id = getattr(request.state, "user_id", None)
    caller_tenant_id = getattr(request.state, "tenant_id", None)

    # Resolves the target and authorises it in one place: an ADMIN may invite
    # into their own tenant, only the platform role into anybody else's.
    tenant_id = resolve_target_tenant(request, invite_data.tenant_id)

    # Check if email already exists
    email_hash = hash_pii_for_lookup(invite_data.email.lower())
    result = await db.execute(
        select(User).where(
            User.tenant_id == tenant_id,
            User.email_hash == email_hash,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists",
        )

    # Get tenant name for invite email
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    tenant_name = tenant.name if tenant else "Stratum AI"

    # Get inviter's name
    inviter_name = "An administrator"
    if requester_user_id:
        inviter_result = await db.execute(
            select(User).where(User.id == requester_user_id)
        )
        inviter = inviter_result.scalar_one_or_none()
        if inviter and inviter.full_name:
            inviter_name = decrypt_pii(inviter.full_name)

    # Map role string to enum. SUPERADMIN is deliberately absent: it is a
    # platform role, and a tenant admin must never be able to grant it.
    user_role = ASSIGNABLE_ROLES.get(invite_data.role.lower())
    if user_role is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=INVALID_ROLE_DETAIL,
        )

    # Create user with temporary password (will need to set password on first login)
    temp_password = secrets.token_urlsafe(16)

    user = User(
        tenant_id=tenant_id,
        email=encrypt_pii(invite_data.email.lower()),
        email_hash=email_hash,
        password_hash=get_password_hash(temp_password),
        full_name=encrypt_pii(invite_data.full_name) if invite_data.full_name else None,
        role=user_role,
        is_active=True,
        is_verified=False,  # Needs to verify email
    )

    db.add(user)
    await db.commit()
    await db.refresh(user)

    # Generate invite token and store in Redis
    invite_token = secrets.token_urlsafe(32)
    try:
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        token_key = f"{INVITE_TOKEN_PREFIX}{invite_token}"
        token_data = f"{user.id}:{tenant_id}:{invite_data.email.lower()}"
        await redis_client.setex(token_key, INVITE_TOKEN_EXPIRY, token_data)
        await redis_client.close()
    except Exception as e:
        logger.error(f"Redis error storing invite token: {e}")
        # Continue anyway - user can request password reset

    # Send invite email in background
    async def send_invite():
        try:
            email_service = get_email_service()
            email_service.send_user_invite_email(
                to_email=invite_data.email.lower(),
                inviter_name=inviter_name,
                tenant_name=tenant_name,
                invite_token=invite_token,
                role=invite_data.role,
            )
            logger.info(f"Invite email sent to {invite_data.email[:10]}...")
        except Exception as e:
            logger.error(f"Error sending invite email: {e}")

    background_tasks.add_task(send_invite)

    await record_cross_tenant_user_change(
        db=db,
        request=request,
        action=AuditAction.CREATE,
        target_tenant_id=tenant_id,
        caller_tenant_id=caller_tenant_id,
        subject_user_id=user.id,
        detail={
            "email_domain": invite_data.email.split("@")[-1],
            "role": user_role.value,
        },
    )

    logger.info(f"Invited user {user.id} to tenant {tenant_id}")

    return APIResponse(
        success=True,
        data=UserResponse(
            id=user.id,
            tenant_id=user.tenant_id,
            email=invite_data.email,
            full_name=invite_data.full_name,
            role=user.role,
            locale=user.locale,
            timezone=user.timezone,
            is_active=user.is_active,
            is_verified=user.is_verified,
            last_login_at=user.last_login_at,
            avatar_url=user.avatar_url,
            created_at=user.created_at,
            updated_at=user.updated_at,
        ),
        message="User invited successfully",
    )


@router.patch("/{user_id}", response_model=APIResponse[UserResponse])
async def update_user(
    request: Request,
    user_id: int,
    update_data: UpdateUserRequest,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Update a user's details.
    Requires admin role.
    """
    caller_tenant_id = getattr(request.state, "tenant_id", None)
    tenant_id = resolve_target_tenant(request, update_data.tenant_id)

    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.tenant_id == tenant_id,
            User.is_deleted == False,
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Check if user is protected (root admin)
    if user.is_protected:
        # Protected users cannot have their role changed or be deactivated
        if update_data.role is not None and update_data.role.lower() != user.role.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot change role of protected admin account",
            )
        if update_data.is_active is not None and not update_data.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot deactivate protected admin account",
            )

    # Update fields
    if update_data.full_name is not None:
        user.full_name = (
            encrypt_pii(update_data.full_name) if update_data.full_name else None
        )

    if update_data.role is not None and not user.is_protected:
        new_role = ASSIGNABLE_ROLES.get(update_data.role.lower())
        if new_role is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=INVALID_ROLE_DETAIL,
            )
        user.role = new_role

    if update_data.is_active is not None and not user.is_protected:
        user.is_active = update_data.is_active

    await db.commit()
    await db.refresh(user)

    await record_cross_tenant_user_change(
        db=db,
        request=request,
        action=AuditAction.UPDATE,
        target_tenant_id=tenant_id,
        caller_tenant_id=caller_tenant_id,
        subject_user_id=user_id,
        detail={
            "changed": sorted(
                field
                for field in ("full_name", "role", "is_active")
                if getattr(update_data, field) is not None
            ),
            "role": user.role.value,
        },
    )

    logger.info(f"Updated user {user_id} in tenant {tenant_id}")

    return APIResponse(
        success=True,
        data=UserResponse(
            id=user.id,
            tenant_id=user.tenant_id,
            email=decrypt_pii(user.email),
            full_name=decrypt_pii(user.full_name) if user.full_name else None,
            role=user.role,
            locale=user.locale,
            timezone=user.timezone,
            is_active=user.is_active,
            is_verified=user.is_verified,
            last_login_at=user.last_login_at,
            avatar_url=user.avatar_url,
            created_at=user.created_at,
            updated_at=user.updated_at,
        ),
        message="User updated successfully",
    )


@router.delete("/{user_id}", response_model=APIResponse)
async def delete_user(
    request: Request,
    user_id: int,
    tenant_id: int | None = Query(
        None,
        description=(
            "Tenant to remove the member from. Defaults to the caller's own; "
            "naming another is honoured only for the platform role."
        ),
    ),
    db: AsyncSession = Depends(get_async_session),
):
    """
    Remove a user from a tenant (soft delete).

    Requires admin of the tenant being changed - see resolve_target_tenant.
    """
    requester_id = getattr(request.state, "user_id", None)
    caller_tenant_id = getattr(request.state, "tenant_id", None)
    tenant_id = resolve_target_tenant(request, tenant_id)

    # Cannot delete yourself
    if user_id == requester_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot remove yourself",
        )

    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.tenant_id == tenant_id,
            User.is_deleted == False,
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Cannot delete protected users (root admin)
    if user.is_protected:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot remove protected admin account",
        )

    # Soft delete
    user.is_deleted = True
    user.is_active = False
    user.deleted_at = datetime.now(UTC)

    await db.commit()

    await record_cross_tenant_user_change(
        db=db,
        request=request,
        action=AuditAction.DELETE,
        target_tenant_id=tenant_id,
        caller_tenant_id=caller_tenant_id,
        subject_user_id=user_id,
        detail={"role": user.role.value},
    )

    logger.info(f"Deleted user {user_id} from tenant {tenant_id}")

    return APIResponse(
        success=True,
        message="User removed successfully",
    )
