# =============================================================================
# Stratum AI - "Log in with Facebook" endpoints
# =============================================================================
"""
Sign-in with a Facebook account, and linking one to an existing Stratum login.

Flow
----
The SPA loads Meta's JS SDK, calls ``FB.login()`` (or reads ``FB.getLoginStatus``
when the person is already connected) and posts the resulting
``authResponse.accessToken`` to ``POST /api/v1/auth/facebook``. The browser's
``userID`` is ignored entirely: this endpoint re-derives the identity server-side
through ``app.services.meta.login_client``, which proves the token was minted for
*this* Meta app before anything is looked up. See that module for why the
browser's word is not evidence.

Endpoints
---------
- ``GET  /auth/facebook/config`` - public. Tells the SPA whether the button
  should render at all, and with which app id, Graph version, scopes and
  optional Login-for-Business ``config_id``. Only public values; the app secret
  is never part of it.
- ``POST /auth/facebook`` - public. Verifies the token and signs the person in,
  provisioning a tenant on first contact when that is enabled.
- ``GET/POST/DELETE /auth/facebook/link`` - authenticated. Read, attach and
  detach the Facebook identity on the caller's *own* account.

Account resolution, in order
----------------------------
1. **Known identity.** A ``user_social_identity`` row for the ASID wins
   outright. This is the only path that is pure recognition.
2. **Existing email.** An active account already holds this email address.
   Attaching to it is refused by default (``facebook_login_auto_link_by_email``
   is False) and answers ``409``, because "the same email address" is not proof
   of "the same person": anyone able to put an address on a Facebook profile
   could otherwise take over the Stratum account holding it, skipping both the
   password and MFA. The person logs in with their password and links from
   settings instead - an act only someone who already controls the account can
   perform. An operator who accepts that trade-off can turn the setting on.
3. **Nobody.** A new tenant and admin user are provisioned, mirroring
   ``POST /auth/signup`` - unless ``facebook_login_allow_signup`` is off, in
   which case the sign-in is refused rather than silently doing nothing.

MFA is not bypassed
-------------------
An account with TOTP enabled gets the same two-step answer a password login
gets: ``mfa_required=True`` plus an ``mfa_session_token``, completed by the
existing ``POST /auth/login/mfa``. Signing in with Facebook is a first factor,
never a way around the second.

What is stored
--------------
The ASID, the granted scopes and timestamps. The Facebook access token is used
for the two Graph reads and then discarded - it is never written to the database,
a log or a response.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.auth import (
    MFA_SESSION_EXPIRY,
    MFA_SESSION_PREFIX,
    LoginResponse,
    get_redis_client,
)
from app.auth.deps import CurrentUserDep
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decrypt_pii,
    encrypt_pii,
    get_password_hash,
    hash_pii_for_lookup,
)
from app.db.session import get_async_session
from app.models import (
    AuditAction,
    AuditLog,
    SocialProvider,
    Tenant,
    User,
    UserRole,
    UserSocialIdentity,
)
from app.schemas import APIResponse
from app.services.meta.login_client import (
    FacebookLoginError,
    FacebookLoginNotConfigured,
    FacebookProfile,
    MetaLoginClient,
)
from app.services.mfa_service import check_mfa_required, is_user_locked

logger = get_logger(__name__)
router = APIRouter(tags=["Authentication"])

#: Access token lifetime echoed to the client, matching ``POST /auth/login``.
ACCESS_TOKEN_EXPIRES_IN_SECONDS = 30 * 60

#: Bytes of entropy behind the unusable password given to a provisioned
#: account. It has to be a *real* bcrypt hash - the column is NOT NULL and
#: ``bcrypt.checkpw`` raises on anything that is not one - so a sentinel string
#: would turn every password login into a 500. 48 bytes is 64 base64url
#: characters, comfortably under the 72-byte input limit this bcrypt build
#: *rejects* rather than truncates, and 384 bits nobody is going to guess.
UNUSABLE_PASSWORD_BYTES = 48

#: Fallback tenant name when Meta returned no display name.
DEFAULT_WORKSPACE_NAME = "My Workspace"


# =============================================================================
# Schemas
# =============================================================================
class FacebookLoginConfigResponse(BaseModel):
    """Public configuration the SPA needs to initialise the Facebook JS SDK."""

    enabled: bool = Field(
        ..., description="False when the button must not render at all"
    )
    app_id: str | None = Field(
        None, description="Meta app id passed to FB.init (public value)"
    )
    api_version: str | None = Field(
        None, description="Graph API version passed to FB.init, e.g. 'v23.0'"
    )
    config_id: str | None = Field(
        None,
        description=(
            "Facebook Login for Business configuration id for FB.login(). "
            "Null means use the plain scope list below."
        ),
    )
    use_code_flow: bool = Field(
        False,
        description=(
            "True when the configuration requires the authorization code "
            "grant, so FB.login() must send response_type 'code'. False is "
            "what a User access token configuration expects."
        ),
    )
    scopes: list[str] = Field(
        default_factory=list, description="Permissions the login dialog requests"
    )


class FacebookLoginRequest(BaseModel):
    """
    The short-lived credential ``FB.login()`` handed the browser.

    Which of the two arrives depends on how the Meta app is configured, not on
    anything the caller chooses:

    - **Facebook Login for Business** must use ``response_type: 'code'``, so the
      browser gets ``authResponse.code`` and sends ``code``. The app secret is
      needed to redeem it, so the exchange happens server-side.
    - **Classic Facebook Login** yields ``authResponse.accessToken`` and sends
      ``access_token``.

    Exactly one must be present. Neither is trusted on arrival: both end up in
    the same ``debug_token`` + ``/me`` verification, including the check that
    the token was minted for *our* app.
    """

    access_token: str | None = Field(
        None,
        min_length=20,
        max_length=1024,
        description="authResponse.accessToken from the Facebook JS SDK",
    )
    code: str | None = Field(
        None,
        min_length=8,
        max_length=1024,
        description="authResponse.code from Facebook Login for Business",
    )

    @model_validator(mode="after")
    def _exactly_one_credential(self) -> FacebookLoginRequest:
        """Reject a body carrying both credentials, or neither."""
        if bool(self.access_token) == bool(self.code):
            raise ValueError("Supply exactly one of 'access_token' or 'code'")
        return self


class FacebookLinkStatusResponse(BaseModel):
    """Whether the caller's own account has a Facebook identity attached."""

    linked: bool
    #: The caller's app-scoped Meta id. Safe to return to its owner: it is
    #: meaningless outside this app and it is their own.
    provider_user_id: str | None = None
    linked_at: datetime | None = None
    last_login_at: datetime | None = None
    #: False means unlinking would remove the account's only way in.
    can_unlink: bool = False


class FacebookLinkResponse(BaseModel):
    """Result of attaching or detaching a Facebook identity."""

    linked: bool
    message: str


# =============================================================================
# Helpers
# =============================================================================
def _login_client() -> MetaLoginClient:
    """
    Build a verification client, or refuse when the feature is switched off.

    Returns:
        A configured :class:`MetaLoginClient`.

    Raises:
        HTTPException: 503 when the feature flag is off or credentials are
            missing. 503 rather than 500: nothing is broken, the deployment
            simply does not offer this sign-in method.
    """
    if not settings.facebook_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Facebook Login is not enabled",
        )
    try:
        return MetaLoginClient()
    except FacebookLoginNotConfigured as exc:
        logger.error("facebook_login_misconfigured", reason=exc.reason)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Facebook Login is not enabled",
        ) from exc


async def _verified_profile(login_data: FacebookLoginRequest) -> FacebookProfile:
    """
    Turn a browser credential into an identity Meta vouches for.

    Args:
        login_data: The request body, carrying exactly one of ``code``
            (Facebook Login for Business) or ``access_token`` (classic login).
            The schema has already rejected a body with both or neither.

    Returns:
        The verified profile.

    Raises:
        HTTPException: 401 when the credential does not verify, 502 when Graph
            could not be reached (the caller may legitimately retry that one).
    """
    client = _login_client()
    try:
        if login_data.code:
            return await client.verify_and_fetch_profile_from_code(login_data.code)
        return await client.verify_and_fetch_profile(login_data.access_token or "")
    except FacebookLoginError as exc:
        logger.info("facebook_login_rejected", reason=exc.reason)
        if exc.reason == "graph_unreachable":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not reach Facebook. Please try again.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Facebook sign-in could not be verified",
        ) from exc
    finally:
        await client.aclose()


def _scope_list(profile: FacebookProfile) -> list[str]:
    """Granted scopes as a JSON-serialisable list."""
    return list(profile.granted_scopes)


async def _find_identity(
    db: AsyncSession, provider_user_id: str
) -> UserSocialIdentity | None:
    """
    Look up the Facebook identity row for one app-scoped Meta id.

    Deliberately not tenant-scoped: the ASID is unique platform-wide and this
    runs before any tenant context exists.

    Args:
        db: Database session.
        provider_user_id: The ASID from the verified profile.

    Returns:
        The identity row, or None.
    """
    result = await db.execute(
        select(UserSocialIdentity).where(
            UserSocialIdentity.provider == SocialProvider.FACEBOOK,
            UserSocialIdentity.provider_user_id == provider_user_id,
        )
    )
    return result.scalar_one_or_none()


async def _find_identity_for_user(
    db: AsyncSession, user_id: int
) -> UserSocialIdentity | None:
    """
    Look up the Facebook identity attached to one account.

    Args:
        db: Database session.
        user_id: Account to inspect.

    Returns:
        The identity row, or None.
    """
    result = await db.execute(
        select(UserSocialIdentity).where(
            UserSocialIdentity.provider == SocialProvider.FACEBOOK,
            UserSocialIdentity.user_id == user_id,
        )
    )
    return result.scalar_one_or_none()


async def _load_active_user(db: AsyncSession, user_id: int) -> User | None:
    """
    Load a live account by id.

    Args:
        db: Database session.
        user_id: Account id.

    Returns:
        The user when active and not soft-deleted, otherwise None.
    """
    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.is_deleted == False,
            User.is_active == True,
        )
    )
    return result.scalar_one_or_none()


async def _find_user_by_email(db: AsyncSession, email: str) -> User | None:
    """
    Find a live account by email address.

    Args:
        db: Database session.
        email: Lower-cased email address.

    Returns:
        The user, or None. Email is stored encrypted, so the lookup goes
        through the deterministic hash column, never a decrypt-and-compare.
    """
    result = await db.execute(
        select(User).where(
            User.email_hash == hash_pii_for_lookup(email),
            User.is_deleted == False,
            User.is_active == True,
        )
    )
    return result.scalars().first()


def _unusable_password_hash() -> str:
    """
    Build a valid bcrypt hash of a secret nobody holds.

    Returns:
        A bcrypt hash that no supplied password can ever match, so a
        social-only account cannot be signed into with ``POST /auth/login``.
    """
    return get_password_hash(secrets.token_urlsafe(UNUSABLE_PASSWORD_BYTES))


async def _unique_tenant_slug(db: AsyncSession, name: str) -> str:
    """
    Derive a unique tenant slug from a display name.

    Mirrors the slug rules in ``POST /auth/signup`` so a workspace created by
    either route looks the same.

    Args:
        db: Database session.
        name: Proposed workspace name.

    Returns:
        A slug not already taken.
    """
    slug = "".join(c if c.isalnum() else "-" for c in name.lower())
    slug = "-".join(filter(None, slug.split("-"))) or "workspace"
    base_slug = slug[:80]
    slug = base_slug
    counter = 1
    while True:
        result = await db.execute(select(Tenant).where(Tenant.slug == slug))
        if not result.scalar_one_or_none():
            return slug
        slug = f"{base_slug}-{counter}"
        counter += 1


async def _provision_account(db: AsyncSession, profile: FacebookProfile) -> User:
    """
    Create a tenant and its first admin user for a brand-new Facebook identity.

    The account is marked verified only when Meta actually returned an email:
    Meta returns the address on the person's confirmed Facebook account, which
    is the same assurance the emailed verification link provides. With no email
    there is nothing to verify and nothing to send to, so the account stays
    unverified and the person is prompted to add an address.

    Args:
        db: Database session.
        profile: The verified Facebook profile.

    Returns:
        The persisted user, flushed so its id is available.
    """
    workspace_name = profile.name or DEFAULT_WORKSPACE_NAME
    tenant = Tenant(
        name=workspace_name,
        slug=await _unique_tenant_slug(db, workspace_name),
        plan="free",
        settings={},
        feature_flags={},
    )
    db.add(tenant)
    await db.flush()

    # An account with no email still needs a unique, non-null email_hash: the
    # column is NOT NULL and carries a (tenant_id, email_hash) unique key. The
    # ASID is unique per app, so it makes a stable placeholder that can never
    # collide with a real address and can never be typed into a login form.
    email = profile.email
    email_source = email or f"facebook:{profile.user_id}"

    user = User(
        tenant_id=tenant.id,
        email=encrypt_pii(email or ""),
        email_hash=hash_pii_for_lookup(email_source),
        password_hash=_unusable_password_hash(),
        has_usable_password=False,
        full_name=encrypt_pii(profile.name) if profile.name else None,
        role=UserRole.ADMIN,  # First user in a fresh tenant, as in /auth/signup
        is_active=True,
        is_verified=bool(email),
    )
    db.add(user)
    await db.flush()
    return user


def _issue_tokens(user: User, email: str) -> LoginResponse:
    """
    Mint the access/refresh pair for a signed-in account.

    Args:
        user: The authenticated account.
        email: Decrypted email for the token's convenience claim.

    Returns:
        A populated :class:`LoginResponse`.
    """
    return LoginResponse(
        mfa_required=False,
        access_token=create_access_token(
            subject=user.id,
            additional_claims={
                "tenant_id": user.tenant_id,
                "role": user.role.value,
                "email": email,
            },
        ),
        refresh_token=create_refresh_token(subject=user.id),
        token_type="bearer",
        expires_in=ACCESS_TOKEN_EXPIRES_IN_SECONDS,
    )


async def _start_mfa_challenge(user: User, email: str) -> LoginResponse:
    """
    Hand back the same second-factor challenge a password login would.

    Args:
        user: The account being signed in.
        email: Decrypted email, stored in the session payload.

    Returns:
        A :class:`LoginResponse` carrying only ``mfa_session_token``.

    Raises:
        HTTPException: 500 when the session could not be stored. Failing closed
            is the point - without the session the second factor cannot be
            checked, and issuing tokens anyway would skip MFA entirely.
    """
    mfa_session_token = secrets.token_urlsafe(48)
    try:
        redis_client = await get_redis_client()
        await redis_client.setex(
            f"{MFA_SESSION_PREFIX}{mfa_session_token}",
            MFA_SESSION_EXPIRY,
            f"{user.id}:{user.tenant_id}:{email}",
        )
        await redis_client.close()
    except Exception as exc:
        logger.error("Redis error storing MFA session", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initiate MFA verification",
        ) from exc

    return LoginResponse(mfa_required=True, mfa_session_token=mfa_session_token)


def _audit(
    request: Request, user: User, action: AuditAction, details: dict
) -> AuditLog:
    """
    Build an audit row for a Facebook sign-in or link change.

    Args:
        request: Incoming request, for IP and user agent.
        user: Account the event belongs to.
        action: Audit action.
        details: Extra machine-readable context, stored in ``new_value``.
            Never a token.

    Returns:
        An unpersisted :class:`AuditLog`.
    """
    return AuditLog(
        tenant_id=user.tenant_id,
        user_id=user.id,
        action=action,
        resource_type="user",
        resource_id=str(user.id),
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent", "")[:500],
        new_value=details,
    )


# =============================================================================
# Public endpoints
# =============================================================================
@router.get("/facebook/config", response_model=APIResponse[FacebookLoginConfigResponse])
async def facebook_login_config():
    """
    Report whether Facebook Login is available, and how to initialise the SDK.

    Public and unauthenticated: the sign-in page is reached logged out. Every
    field is a value that ships to the browser anyway (the app id appears in
    every ``FB.init`` call); the app secret is not among them and never will be.

    Returns:
        The SDK configuration, with ``enabled: false`` when the deployment has
        the feature off or is missing credentials.
    """
    configured = bool(
        settings.facebook_login_enabled
        and (settings.meta_app_id or "").strip()
        and (settings.meta_app_secret or "").strip()
    )
    if not configured:
        return APIResponse(
            success=True,
            data=FacebookLoginConfigResponse(enabled=False),
            message="Facebook Login is not enabled",
        )

    scopes = [
        scope.strip()
        for scope in settings.facebook_login_scopes.split(",")
        if scope.strip()
    ]
    return APIResponse(
        success=True,
        data=FacebookLoginConfigResponse(
            enabled=True,
            app_id=(settings.meta_app_id or "").strip(),
            api_version=settings.meta_graph_api_version,
            config_id=settings.facebook_login_config_id,
            use_code_flow=settings.facebook_login_use_code_flow,
            scopes=scopes,
        ),
        message="Facebook Login is enabled",
    )


@router.post("/facebook", response_model=APIResponse[LoginResponse])
async def login_with_facebook(
    request: Request,
    login_data: FacebookLoginRequest,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Sign in with a Facebook account.

    The supplied token is verified server-side before any lookup: see
    ``app.services.meta.login_client`` for the checks and why they matter.

    Args:
        request: Incoming request (audit context).
        login_data: The browser's Facebook access token.
        db: Database session.

    Returns:
        The same :class:`LoginResponse` shape ``POST /auth/login`` returns -
        tokens directly, or an MFA challenge when the account has TOTP on.

    Raises:
        HTTPException: 401 when the token does not verify or the matched
            account is inactive; 403 when signup by Facebook is disabled; 409
            when the email belongs to an account that must be linked from an
            authenticated session; 429 when MFA lockout applies; 502/503 for
            Graph and configuration failures.
    """
    profile = await _verified_profile(login_data)

    identity = await _find_identity(db, profile.user_id)
    user: User | None = None
    provisioned = False

    if identity is not None:
        user = await _load_active_user(db, identity.user_id)
        if user is None:
            # The link outlived the account (deactivated or soft-deleted).
            # Refuse rather than silently provisioning a second workspace for
            # someone an operator deliberately switched off.
            logger.warning("facebook_login_inactive_account", user_id=identity.user_id)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This account is not active. Contact your administrator.",
            )
    else:
        existing = (
            await _find_user_by_email(db, profile.email) if profile.email else None
        )
        if existing is not None:
            if not settings.facebook_login_auto_link_by_email:
                logger.info("facebook_login_link_required", user_id=existing.id)
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "An account already uses this email address. Sign in "
                        "with your password, then connect Facebook from your "
                        "account settings."
                    ),
                )
            user = existing
        else:
            if not settings.facebook_login_allow_signup:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        "No Stratum account is connected to this Facebook "
                        "account. Ask your administrator for an invitation."
                    ),
                )
            user = await _provision_account(db, profile)
            provisioned = True

        identity = UserSocialIdentity(
            user_id=user.id,
            tenant_id=user.tenant_id,
            provider=SocialProvider.FACEBOOK,
            provider_user_id=profile.user_id,
            granted_scopes=_scope_list(profile),
        )
        db.add(identity)

    now = datetime.now(UTC)
    identity.granted_scopes = _scope_list(profile)
    identity.last_login_at = now

    # MFA is checked before any token is minted, and before last_login_at is
    # touched: a challenge that is never completed is not a login.
    if await check_mfa_required(db, user.id):
        is_locked, lockout_until = await is_user_locked(db, user.id)
        if is_locked:
            # A lockout with no stated end is still a lockout; report a minute
            # rather than dividing by a None and turning a 429 into a 500.
            remaining = (
                max(1, int((lockout_until - now).total_seconds()) // 60)
                if lockout_until is not None
                else 1
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    "Account locked due to too many failed MFA attempts. "
                    f"Try again in {remaining} minutes."
                ),
            )
        email = decrypt_pii(user.email) if user.email else ""
        challenge = await _start_mfa_challenge(user, email)
        await db.commit()
        logger.info("facebook_login_mfa_required", user_id=user.id)
        return APIResponse(
            success=True,
            data=challenge,
            message="MFA verification required. Use /auth/login/mfa to complete login.",
        )

    user.last_login_at = now
    email = decrypt_pii(user.email) if user.email else ""
    tokens = _issue_tokens(user, email)

    db.add(
        _audit(
            request,
            user,
            AuditAction.LOGIN,
            {
                "method": "facebook",
                "provisioned": provisioned,
                "email_from_provider": bool(profile.email),
            },
        )
    )
    await db.commit()

    logger.info(
        "facebook_login_succeeded",
        user_id=user.id,
        tenant_id=user.tenant_id,
        provisioned=provisioned,
    )
    return APIResponse(success=True, data=tokens, message="Login successful")


# =============================================================================
# Authenticated link management
# =============================================================================
@router.get("/facebook/link", response_model=APIResponse[FacebookLinkStatusResponse])
async def facebook_link_status(
    current_user: CurrentUserDep,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Report whether the caller's own account has Facebook attached.

    Args:
        current_user: Authenticated caller.
        db: Database session.

    Returns:
        The link status. Scoped to the caller's own account id - it never takes
        a user id from the request, so it cannot be pointed at anyone else.
    """
    identity = await _find_identity_for_user(db, current_user.id)
    if identity is None:
        return APIResponse(
            success=True,
            data=FacebookLinkStatusResponse(linked=False),
            message="Facebook is not connected",
        )

    return APIResponse(
        success=True,
        data=FacebookLinkStatusResponse(
            linked=True,
            provider_user_id=identity.provider_user_id,
            linked_at=identity.linked_at,
            last_login_at=identity.last_login_at,
            # Unlinking is only safe once a password exists to fall back on.
            can_unlink=bool(current_user.user.has_usable_password),
        ),
        message="Facebook is connected",
    )


@router.post("/facebook/link", response_model=APIResponse[FacebookLinkResponse])
async def link_facebook(
    request: Request,
    login_data: FacebookLoginRequest,
    current_user: CurrentUserDep,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Attach a Facebook account to the caller's own Stratum login.

    This is the deliberate act that ``POST /auth/facebook`` refuses to perform
    on an email match alone: it requires a live session, so only someone who
    already controls the account can do it.

    Args:
        request: Incoming request (audit context).
        login_data: The browser's Facebook access token.
        current_user: Authenticated caller.
        db: Database session.

    Returns:
        Confirmation that the link now exists.

    Raises:
        HTTPException: 401 when the token does not verify; 409 when the caller
            already linked a different Facebook account, or when this Facebook
            account is attached to somebody else.
    """
    profile = await _verified_profile(login_data)

    existing_for_user = await _find_identity_for_user(db, current_user.id)
    if existing_for_user is not None:
        if existing_for_user.provider_user_id == profile.user_id:
            existing_for_user.granted_scopes = _scope_list(profile)
            await db.commit()
            return APIResponse(
                success=True,
                data=FacebookLinkResponse(
                    linked=True, message="Facebook is already connected"
                ),
                message="Facebook is already connected",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A different Facebook account is already connected. "
                "Disconnect it first."
            ),
        )

    # One Facebook account may not sign in to two Stratum logins: whichever it
    # resolved to first would become ambiguous.
    taken = await _find_identity(db, profile.user_id)
    if taken is not None:
        logger.info("facebook_link_conflict", user_id=current_user.id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Facebook account is already connected to another Stratum account.",
        )

    db.add(
        UserSocialIdentity(
            user_id=current_user.id,
            tenant_id=current_user.tenant_id,
            provider=SocialProvider.FACEBOOK,
            provider_user_id=profile.user_id,
            granted_scopes=_scope_list(profile),
        )
    )
    db.add(
        _audit(
            request,
            current_user.user,
            AuditAction.UPDATE,
            {"action": "facebook_linked"},
        )
    )
    await db.commit()

    logger.info("facebook_linked", user_id=current_user.id)
    return APIResponse(
        success=True,
        data=FacebookLinkResponse(linked=True, message="Facebook connected"),
        message="Facebook connected",
    )


@router.delete("/facebook/link", response_model=APIResponse[FacebookLinkResponse])
async def unlink_facebook(
    request: Request,
    current_user: CurrentUserDep,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Detach Facebook from the caller's own Stratum login.

    Args:
        request: Incoming request (audit context).
        current_user: Authenticated caller.
        db: Database session.

    Returns:
        Confirmation that the link is gone (idempotent when it never existed).

    Raises:
        HTTPException: 409 when the account has no password, because Facebook
            is then its only way in and unlinking would lock its owner out with
            no undo. The remedy is offered in the message: set a password
            through the reset flow first.
    """
    identity = await _find_identity_for_user(db, current_user.id)
    if identity is None:
        return APIResponse(
            success=True,
            data=FacebookLinkResponse(
                linked=False, message="Facebook is not connected"
            ),
            message="Facebook is not connected",
        )

    if not current_user.user.has_usable_password:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Facebook is the only way to sign in to this account. Set a "
                "password first, then disconnect Facebook."
            ),
        )

    await db.delete(identity)
    db.add(
        _audit(
            request,
            current_user.user,
            AuditAction.UPDATE,
            {"action": "facebook_unlinked"},
        )
    )
    await db.commit()

    logger.info("facebook_unlinked", user_id=current_user.id)
    return APIResponse(
        success=True,
        data=FacebookLinkResponse(linked=False, message="Facebook disconnected"),
        message="Facebook disconnected",
    )
