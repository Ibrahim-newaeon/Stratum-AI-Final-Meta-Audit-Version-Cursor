# =============================================================================
# Stratum AI - Billing API Endpoints (Paddle Billing)
# =============================================================================
"""
Tenant-facing billing endpoints backed by Paddle Billing.

Checkout itself happens client-side through the Paddle.js overlay; this API
prepares what the overlay needs (price id, client token, customer, custom
data) and mirrors subscription state that Paddle reports back via webhooks
(see ``app.api.v1.endpoints.paddle_webhook``).

Endpoints (all under ``/api/v1/billing``, every response is ``APIResponse``):
- GET  /billing/config                              - Public Paddle config + tier catalogue
- GET  /billing/subscription                        - Current subscription for the tenant
- POST /billing/checkout-session                    - Prepare a Paddle.js overlay checkout
- POST /billing/portal-session                      - Paddle customer portal links
- POST /billing/cancel                              - Cancel (at period end by default)
- POST /billing/reactivate                          - Undo a scheduled cancellation
- POST /billing/upgrade                             - Change tier (prorated by default)
- GET  /billing/transactions                        - Billing history (Paddle transactions)
- GET  /billing/transactions/{transaction_id}/invoice - Invoice PDF URL for one transaction

Authentication: everything except ``/billing/config`` requires a valid Bearer
token (``get_current_user``); the tenant is always taken from the verified
user, never from ``X-Tenant-ID`` or ``request.state``. Routes that change
billing state (checkout, portal, cancel, reactivate, upgrade) additionally
require the admin or superadmin role.

Error mapping: ``PaddleNotConfiguredError`` -> 503, ``PaddleError`` -> 502.
"""

import functools
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Optional, TypeVar, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CurrentUser, get_current_user, require_role
from app.base_models import Tenant, User, UserRole
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import decrypt_pii
from app.core.tiers import TIER_PRICING, SubscriptionTier
from app.db.session import get_async_session
from app.schemas.billing import (
    BillingConfigResponse,
    BillingSubscriptionResponse,
    BillingTransactionResponse,
    CancelRequest,
    CheckoutCustomData,
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    PortalSessionRequest,
    PortalSessionResponse,
    TierPriceIds,
    TierPriceInfo,
    TransactionInvoiceResponse,
    UpgradeRequest,
)
from app.schemas.response import APIResponse
from app.services import paddle_service
from app.services.paddle_service import (
    PaddleClient,
    PaddleError,
    PaddleNotConfiguredError,
    PaddleSubscription,
    SubscriptionState,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/billing", tags=["Billing"])

# Subscription states that count as "live" for cancel/upgrade decisions.
LIVE_STATES: frozenset[SubscriptionState] = frozenset(
    {SubscriptionState.ACTIVE, SubscriptionState.TRIALING, SubscriptionState.PAST_DUE}
)

# Preference order when a customer has several subscriptions.
_STATE_PREFERENCE: tuple[SubscriptionState, ...] = (
    SubscriptionState.ACTIVE,
    SubscriptionState.TRIALING,
    SubscriptionState.PAST_DUE,
    SubscriptionState.PAUSED,
    SubscriptionState.CANCELED,
)

# Roles allowed to act as the billing contact when creating the Paddle customer.
_BILLING_CONTACT_ROLES: tuple[UserRole, ...] = (UserRole.ADMIN, UserRole.SUPERADMIN)

# Roles allowed to change billing state (checkout, portal, cancel, reactivate,
# upgrade). Read-only routes only require an authenticated user.
BILLING_ADMIN_ROLES: tuple[UserRole, ...] = (UserRole.ADMIN, UserRole.SUPERADMIN)
require_billing_admin = require_role(*BILLING_ADMIN_ROLES)

_NOT_CONFIGURED_DETAIL = "Billing is not configured for this environment"
_PAYMENTS_DISABLED_DETAIL = "This portal does not take payments"
_NO_TENANT_CONTEXT_DETAIL = "Billing requires a tenant context"
_NO_BILLING_ACCOUNT_DETAIL = "No billing account found. Please subscribe to a plan first."

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


# =============================================================================
# Helpers
# =============================================================================


def _map_paddle_errors(func: F) -> F:
    """Translate Paddle client errors into HTTP errors (503 not configured, 502 upstream)."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except PaddleNotConfiguredError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=getattr(exc, "detail", None) or _NOT_CONFIGURED_DETAIL,
            ) from exc
        except PaddleError as exc:
            logger.error(
                "paddle_request_failed",
                endpoint=func.__name__,
                status_code=getattr(exc, "status_code", None),
                code=getattr(exc, "code", None),
                detail=getattr(exc, "detail", str(exc)),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=getattr(exc, "detail", None) or "Paddle request failed",
            ) from exc

    return wrapper  # type: ignore[return-value]


def _iso(value: Optional[datetime]) -> Optional[str]:
    """Render a datetime as an ISO-8601 string (``None`` passes through)."""
    return value.isoformat() if value else None


def _payments_live() -> bool:
    """Checkout is on only when payments are enabled and Paddle keys exist."""
    return bool(settings.billing_payments_enabled) and paddle_service.is_configured()


def _require_configured() -> None:
    """Raise 503 when this portal does not take payments or Paddle is not configured."""
    if not settings.billing_payments_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_PAYMENTS_DISABLED_DETAIL,
        )
    if not paddle_service.is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_NOT_CONFIGURED_DETAIL,
        )


def _get_client() -> PaddleClient:
    """Return the shared Paddle client (lazy singleton from the service module)."""
    return paddle_service.get_paddle_client()


async def get_tenant_for_user(current_user: CurrentUser, db: AsyncSession) -> Tenant:
    """Resolve the billing tenant of the authenticated user.

    The tenant id comes exclusively from the verified JWT subject
    (``current_user.tenant_id``) so a caller can never pick another tenant's
    billing account through headers or request state. Raises 403 when the user
    carries no tenant context (e.g. a platform superadmin outside any tenant)
    and 404 when the tenant does not exist or has been soft-deleted.
    """
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_NO_TENANT_CONTEXT_DETAIL,
        )

    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.is_deleted == False)
    )
    tenant = result.scalar_one_or_none()
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )
    return tenant


def validate_tier(tier_str: str) -> SubscriptionTier:
    """Validate and convert a tier string to ``SubscriptionTier`` (400 on failure)."""
    try:
        return SubscriptionTier(tier_str.lower())
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid tier: {tier_str}. Must be starter, professional, or enterprise.",
        ) from exc


def _require_price_id(tier: SubscriptionTier) -> str:
    """Return the Paddle price id for a tier or raise 400 when none is configured."""
    price_id = paddle_service.get_price_id_for_tier(tier)
    if not price_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No Paddle price is configured for the {tier.value} tier",
        )
    return price_id


def _pick_subscription(subscriptions: list[PaddleSubscription]) -> Optional[PaddleSubscription]:
    """Choose the most relevant subscription (active > trialing > past_due > paused > canceled)."""
    for state in _STATE_PREFERENCE:
        for sub in subscriptions:
            if sub.status == state:
                return sub
    return subscriptions[0] if subscriptions else None


async def load_current_subscription(
    client: PaddleClient, tenant: Tenant
) -> Optional[PaddleSubscription]:
    """Fetch the tenant's current Paddle subscription.

    Uses the stored ``paddle_subscription_id`` when present; otherwise (or when
    the stored one is canceled) lists the customer's subscriptions and prefers a
    live one.
    """
    if not tenant.paddle_customer_id:
        return None

    stored: Optional[PaddleSubscription] = None
    if tenant.paddle_subscription_id:
        try:
            stored = await client.get_subscription(tenant.paddle_subscription_id)
        except PaddleError as exc:
            if getattr(exc, "status_code", None) != 404:
                raise
            logger.warning(
                "paddle_stored_subscription_missing",
                tenant_id=tenant.id,
                subscription_id=tenant.paddle_subscription_id,
            )
        if stored is not None and stored.status != SubscriptionState.CANCELED:
            return stored

    listed = await client.list_subscriptions(tenant.paddle_customer_id)
    return _pick_subscription(listed) or stored


def _subscription_response(
    tenant: Tenant, subscription: Optional[PaddleSubscription], configured: bool
) -> BillingSubscriptionResponse:
    """Build the API representation of a tenant + optional Paddle subscription."""
    if subscription is None:
        return BillingSubscriptionResponse(
            paddle_configured=configured,
            has_customer=bool(tenant.paddle_customer_id),
            has_subscription=False,
            customer_id=tenant.paddle_customer_id,
            plan=tenant.plan,
        )

    return BillingSubscriptionResponse(
        paddle_configured=configured,
        has_customer=True,
        has_subscription=True,
        customer_id=tenant.paddle_customer_id or subscription.customer_id,
        subscription_id=subscription.id,
        status=subscription.status.value,
        tier=subscription.tier.value if subscription.tier else None,
        plan=tenant.plan,
        current_period_start=_iso(subscription.current_period_start),
        current_period_end=_iso(subscription.current_period_end),
        next_billed_at=_iso(subscription.next_billed_at),
        cancel_at_period_end=subscription.cancel_at_period_end,
        canceled_at=_iso(subscription.canceled_at),
        paused_at=_iso(subscription.paused_at),
        trial_end=_iso(subscription.trial_end),
    )


async def _sync_and_respond(
    db: AsyncSession, tenant: Tenant, subscription: PaddleSubscription
) -> APIResponse[BillingSubscriptionResponse]:
    """Persist a subscription change on the tenant and return the fresh state."""
    await paddle_service.sync_tenant_subscription(db, tenant.id, subscription)
    await db.refresh(tenant)
    return APIResponse(data=_subscription_response(tenant, subscription, configured=True))


async def _billing_contact_email(db: AsyncSession, tenant: Tenant) -> Optional[str]:
    """Return the decrypted email of the tenant's first admin user, if any."""
    result = await db.execute(
        select(User)
        .where(
            User.tenant_id == tenant.id,
            User.role.in_(_BILLING_CONTACT_ROLES),
            User.is_deleted == False,
        )
        .order_by(User.id.asc())
        .limit(1)
    )
    admin_user = result.scalar_one_or_none()
    if admin_user is None or not admin_user.email:
        return None
    return decrypt_pii(admin_user.email)


async def ensure_paddle_customer(
    db: AsyncSession, tenant: Tenant, client: PaddleClient
) -> tuple[str, str]:
    """Return ``(customer_id, customer_email)``, creating the Paddle customer on first use.

    The billing contact is the tenant's first non-deleted admin user. When the
    tenant already has a ``paddle_customer_id`` no Paddle request is made.
    """
    email = await _billing_contact_email(db, tenant)

    if tenant.paddle_customer_id:
        if not email:
            customer = await client.get_customer(tenant.paddle_customer_id)
            email = customer.email if customer else ""
        return tenant.paddle_customer_id, email or ""

    if not email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No admin user with an email address is available as billing contact",
        )

    customer = await client.create_customer(email=email, name=tenant.name, tenant_id=tenant.id)
    await paddle_service.sync_tenant_paddle_customer(db, tenant.id, customer.id)
    tenant.paddle_customer_id = customer.id

    logger.info("paddle_customer_created", tenant_id=tenant.id, customer_id=customer.id)
    return customer.id, customer.email or email


# =============================================================================
# Endpoints
# =============================================================================


@router.get("/config", response_model=APIResponse[BillingConfigResponse])
async def get_billing_config() -> APIResponse[BillingConfigResponse]:
    """Public billing configuration (safe to expose to the SPA).

    Works even when Paddle is not configured: ``paddle_configured`` is False and
    ``client_token`` is null so the UI can render the catalogue read-only.
    """
    configured = _payments_live()
    tiers: list[TierPriceInfo] = []
    for tier, raw_pricing in TIER_PRICING.items():
        pricing = cast(dict[str, Any], raw_pricing)
        tiers.append(
            TierPriceInfo(
                tier=tier.value,
                name=pricing["name"],
                price=pricing["price"],
                currency=pricing["currency"],
                billing_period=pricing["billing_period"],
                description=pricing["description"],
            )
        )
    return APIResponse(
        data=BillingConfigResponse(
            payments_enabled=bool(settings.billing_payments_enabled),
            paddle_configured=configured,
            environment=settings.paddle_environment,
            client_token=settings.paddle_client_token if configured else None,
            price_ids=TierPriceIds(
                starter=settings.paddle_starter_price_id,
                professional=settings.paddle_professional_price_id,
                enterprise=settings.paddle_enterprise_price_id,
            ),
            tiers=tiers,
        )
    )


@router.get("/subscription", response_model=APIResponse[BillingSubscriptionResponse])
@_map_paddle_errors
async def get_subscription(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[BillingSubscriptionResponse]:
    """Current subscription for the authenticated user's tenant.

    Returns ``has_subscription: false`` (with the locally stored plan) when Paddle
    is not configured, the tenant has no Paddle customer, or no subscription
    exists.
    """
    tenant = await get_tenant_for_user(current_user, db)
    configured = _payments_live()
    if not configured or not tenant.paddle_customer_id:
        return APIResponse(data=_subscription_response(tenant, None, configured))

    subscription = await load_current_subscription(_get_client(), tenant)
    return APIResponse(data=_subscription_response(tenant, subscription, configured))


@router.post("/checkout-session", response_model=APIResponse[CheckoutSessionResponse])
@_map_paddle_errors
async def create_checkout_session(
    body: CheckoutSessionRequest,
    current_user: CurrentUser = Depends(require_billing_admin),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[CheckoutSessionResponse]:
    """Prepare a Paddle.js overlay checkout for a tier (admin only).

    Ensures the tenant has a Paddle customer (created from the first admin
    user's email on first use) and returns the price id, client token and
    ``custom_data`` (``tenant_id`` + ``tier``) the frontend passes to
    ``Paddle.Checkout.open``.
    """
    _require_configured()
    if not settings.paddle_client_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Paddle client token is not configured",
        )

    tier = validate_tier(body.tier)
    price_id = _require_price_id(tier)
    tenant = await get_tenant_for_user(current_user, db)
    customer_id, customer_email = await ensure_paddle_customer(db, tenant, _get_client())

    success_url = body.success_url or f"{settings.frontend_url}/dashboard/billing/success"
    logger.info(
        "paddle_checkout_prepared",
        tenant_id=tenant.id,
        user_id=current_user.id,
        tier=tier.value,
        price_id=price_id,
        customer_id=customer_id,
    )
    return APIResponse(
        data=CheckoutSessionResponse(
            price_id=price_id,
            client_token=settings.paddle_client_token,
            environment=settings.paddle_environment,
            customer_id=customer_id,
            customer_email=customer_email,
            custom_data=CheckoutCustomData(tenant_id=str(tenant.id), tier=tier.value),
            success_url=success_url,
            display_mode="overlay",
        )
    )


@router.post("/portal-session", response_model=APIResponse[PortalSessionResponse])
@_map_paddle_errors
async def create_portal_session(
    body: PortalSessionRequest,
    current_user: CurrentUser = Depends(require_billing_admin),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[PortalSessionResponse]:
    """Create Paddle customer portal links (overview, cancel, update payment method). Admin only."""
    _require_configured()
    tenant = await get_tenant_for_user(current_user, db)
    if not tenant.paddle_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_NO_BILLING_ACCOUNT_DETAIL,
        )

    subscription_ids = [tenant.paddle_subscription_id] if tenant.paddle_subscription_id else None
    session = await _get_client().create_portal_session(
        tenant.paddle_customer_id, subscription_ids=subscription_ids
    )
    return APIResponse(
        data=PortalSessionResponse(
            portal_url=session.overview_url,
            cancel_url=session.cancel_url,
            update_payment_method_url=session.update_payment_method_url,
        )
    )


@router.post("/cancel", response_model=APIResponse[BillingSubscriptionResponse])
@_map_paddle_errors
async def cancel_subscription(
    body: CancelRequest,
    current_user: CurrentUser = Depends(require_billing_admin),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[BillingSubscriptionResponse]:
    """Cancel the current subscription (at period end by default). Admin only."""
    _require_configured()
    tenant = await get_tenant_for_user(current_user, db)
    client = _get_client()

    subscription = await load_current_subscription(client, tenant)
    if subscription is None or subscription.status not in LIVE_STATES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active subscription to cancel",
        )

    updated = await client.cancel_subscription(subscription.id, at_period_end=body.at_period_end)
    logger.info(
        "paddle_subscription_cancel_requested",
        tenant_id=tenant.id,
        user_id=current_user.id,
        subscription_id=subscription.id,
        at_period_end=body.at_period_end,
    )
    return await _sync_and_respond(db, tenant, updated)


@router.post("/reactivate", response_model=APIResponse[BillingSubscriptionResponse])
@_map_paddle_errors
async def reactivate_subscription(
    current_user: CurrentUser = Depends(require_billing_admin),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[BillingSubscriptionResponse]:
    """Undo a scheduled cancellation (clears Paddle's ``scheduled_change``). Admin only."""
    _require_configured()
    tenant = await get_tenant_for_user(current_user, db)
    client = _get_client()

    subscription = await load_current_subscription(client, tenant)
    if subscription is None or not subscription.cancel_at_period_end:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No subscription scheduled for cancellation to reactivate",
        )

    updated = await client.reactivate_subscription(subscription.id)
    logger.info(
        "paddle_subscription_reactivated",
        tenant_id=tenant.id,
        user_id=current_user.id,
        subscription_id=subscription.id,
    )
    return await _sync_and_respond(db, tenant, updated)


@router.post("/upgrade", response_model=APIResponse[BillingSubscriptionResponse])
@_map_paddle_errors
async def upgrade_subscription(
    body: UpgradeRequest,
    current_user: CurrentUser = Depends(require_billing_admin),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[BillingSubscriptionResponse]:
    """Move the current subscription to another tier (prorated by default). Admin only."""
    _require_configured()
    new_tier = validate_tier(body.new_tier)
    _require_price_id(new_tier)
    tenant = await get_tenant_for_user(current_user, db)
    client = _get_client()

    subscription = await load_current_subscription(client, tenant)
    if subscription is None or subscription.status not in LIVE_STATES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active subscription to change. Use checkout to subscribe first.",
        )
    if subscription.tier == new_tier:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Subscription is already on the {new_tier.value} tier",
        )

    updated = await client.update_subscription_tier(subscription.id, new_tier, prorate=body.prorate)
    logger.info(
        "paddle_subscription_tier_changed",
        tenant_id=tenant.id,
        user_id=current_user.id,
        subscription_id=subscription.id,
        new_tier=new_tier.value,
        prorate=body.prorate,
    )
    return await _sync_and_respond(db, tenant, updated)


@router.get("/transactions", response_model=APIResponse[list[BillingTransactionResponse]])
@_map_paddle_errors
async def list_transactions(
    limit: int = Query(default=10, description="Max transactions to return (clamped to 1..100)"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[list[BillingTransactionResponse]]:
    """Billing history for the tenant (empty when not configured or no customer)."""
    tenant = await get_tenant_for_user(current_user, db)
    if not _payments_live() or not tenant.paddle_customer_id:
        return APIResponse(data=[])

    limit = max(1, min(limit, 100))
    transactions = await _get_client().list_transactions(tenant.paddle_customer_id, limit=limit)
    return APIResponse(
        data=[
            BillingTransactionResponse(
                id=txn.id,
                invoice_number=txn.invoice_number,
                status=txn.status,
                subscription_id=txn.subscription_id,
                amount_minor=txn.total_minor,
                currency=txn.currency_code,
                billed_at=_iso(txn.billed_at),
                created_at=_iso(txn.created_at),
            )
            for txn in transactions
        ]
    )


@router.get(
    "/transactions/{transaction_id}/invoice",
    response_model=APIResponse[TransactionInvoiceResponse],
)
@_map_paddle_errors
async def get_transaction_invoice(
    transaction_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_session),
) -> APIResponse[TransactionInvoiceResponse]:
    """Invoice PDF URL for one of the tenant's transactions.

    The transaction must belong to the tenant's Paddle customer; anything else
    is reported as 404 so ids cannot be probed across tenants.
    """
    _require_configured()
    tenant = await get_tenant_for_user(current_user, db)
    if not tenant.paddle_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_NO_BILLING_ACCOUNT_DETAIL,
        )

    client = _get_client()
    try:
        transaction = await client.get_transaction(transaction_id)
    except PaddleError as exc:
        if getattr(exc, "status_code", None) == 404:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found"
            ) from exc
        raise

    if transaction.customer_id != tenant.paddle_customer_id:
        logger.warning(
            "paddle_invoice_tenant_mismatch",
            tenant_id=tenant.id,
            transaction_id=transaction_id,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

    invoice_url = await client.get_transaction_invoice_url(transaction_id)
    return APIResponse(
        data=TransactionInvoiceResponse(transaction_id=transaction_id, invoice_url=invoice_url)
    )
