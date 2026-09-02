# =============================================================================
# Stratum AI - Paddle Webhook Handler
# =============================================================================
"""
Public, signature-verified notification endpoint for Paddle Billing.

Endpoint:
- POST /api/v1/webhooks/paddle  (listed in ``TenantMiddleware.PUBLIC_ENDPOINTS``)

Security:
- Every request must carry ``Paddle-Signature: ts=<unix>;h1=<hex>``. The
  signature is HMAC-SHA256 over ``"<ts>:" + raw body`` with the notification
  endpoint secret (``settings.paddle_webhook_secret``) and is rejected when
  older than 5 minutes. Without a configured secret the endpoint answers 503
  and never processes unverified payloads.

Idempotency:
- ``PaddleWebhookEvent(event_id)`` is inserted first; a duplicate ``event_id``
  answers ``200 {"status": "duplicate"}`` without dispatching again.

Event -> state mapping:
- subscription.created/activated/updated/trialing/past_due/paused/resumed
    -> ``sync_tenant_subscription`` (+ link ``paddle_customer_id``)
- subscription.canceled           -> ``clear_tenant_subscription`` (plan = free)
- transaction.completed/paid      -> fetch the subscription and sync
                                     (link the customer only when unconfigured)
- transaction.payment_failed      -> email tenant admins (no DB change)
- customer.created/updated        -> ``sync_tenant_paddle_customer``
- everything else                 -> ``200 {"status": "ignored"}``

Handler failures roll back and answer 500 so Paddle retries the notification.
"""

import asyncio
import json
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.base_models import PaddleWebhookEvent, Tenant, User, UserRole
from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import decrypt_pii
from app.db.session import async_session_maker
from app.services import paddle_service
from app.services.email_service import get_email_service
from app.services.paddle_service import PaddleNotConfiguredError, PaddleSignatureError

logger = get_logger(__name__)
router = APIRouter(tags=["Paddle Webhooks"])

# Keep in sync with TenantMiddleware.PUBLIC_ENDPOINTS ("/api/v1" + WEBHOOK_PATH).
WEBHOOK_PATH = "/webhooks/paddle"

SUBSCRIPTION_SYNC_EVENTS: frozenset[str] = frozenset(
    {
        "subscription.created",
        "subscription.activated",
        "subscription.updated",
        "subscription.trialing",
        "subscription.past_due",
        "subscription.paused",
        "subscription.resumed",
    }
)
SUBSCRIPTION_CANCELED_EVENT = "subscription.canceled"
TRANSACTION_SYNC_EVENTS: frozenset[str] = frozenset({"transaction.completed", "transaction.paid"})
TRANSACTION_FAILED_EVENT = "transaction.payment_failed"
CUSTOMER_EVENTS: frozenset[str] = frozenset({"customer.created", "customer.updated"})


class WebhookAck(BaseModel):
    """Acknowledgement returned to Paddle."""

    status: Literal["received", "duplicate", "ignored"] = Field(
        ..., description="received = state applied, duplicate = seen before, ignored = no-op"
    )
    event_type: str


# =============================================================================
# Tenant resolution
# =============================================================================


def _parse_tenant_id(value: Any) -> Optional[int]:
    """Coerce a ``custom_data.tenant_id`` value (str/int) to int, else ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _custom_data(data: dict[str, Any]) -> dict[str, Any]:
    """Return the entity's ``custom_data`` as a dict (Paddle sends null when unset)."""
    custom = data.get("custom_data")
    return custom if isinstance(custom, dict) else {}


async def get_tenant_by_id(db: AsyncSession, tenant_id: int) -> Optional[Tenant]:
    """Load a non-deleted tenant by primary key."""
    result = await db.execute(
        select(Tenant).where(Tenant.id == tenant_id, Tenant.is_deleted == False)
    )
    return result.scalar_one_or_none()


async def get_tenant_by_customer_id(db: AsyncSession, customer_id: str) -> Optional[Tenant]:
    """Load a non-deleted tenant by its Paddle customer id (``ctm_...``)."""
    result = await db.execute(
        select(Tenant).where(
            Tenant.paddle_customer_id == customer_id,
            Tenant.is_deleted == False,
        )
    )
    return result.scalar_one_or_none()


async def resolve_tenant(db: AsyncSession, data: dict[str, Any]) -> Optional[Tenant]:
    """Resolve the tenant for an event entity.

    ``custom_data.tenant_id`` (set at checkout) wins; otherwise fall back to the
    tenant whose ``paddle_customer_id`` matches ``data.customer_id``.
    """
    tenant_id = _parse_tenant_id(_custom_data(data).get("tenant_id"))
    if tenant_id is not None:
        tenant = await get_tenant_by_id(db, tenant_id)
        if tenant is not None:
            return tenant
        logger.warning("paddle_webhook_tenant_id_unknown", tenant_id=tenant_id)

    customer_id = data.get("customer_id")
    if isinstance(customer_id, str) and customer_id:
        return await get_tenant_by_customer_id(db, customer_id)
    return None


async def _link_customer(db: AsyncSession, tenant: Tenant, customer_id: Any) -> None:
    """Store the Paddle customer id on the tenant when it is not linked yet."""
    if isinstance(customer_id, str) and customer_id and not tenant.paddle_customer_id:
        await paddle_service.sync_tenant_paddle_customer(db, tenant.id, customer_id)
        tenant.paddle_customer_id = customer_id


# =============================================================================
# Event handlers (return True when tenant state was touched)
# =============================================================================


async def handle_subscription_sync(db: AsyncSession, data: dict[str, Any]) -> bool:
    """Mirror a subscription entity onto the tenant (created/activated/updated/...)."""
    tenant = await resolve_tenant(db, data)
    if tenant is None:
        logger.warning("paddle_webhook_subscription_no_tenant", subscription_id=data.get("id"))
        return False

    subscription = paddle_service.subscription_from_payload(data)
    await _link_customer(db, tenant, subscription.customer_id)
    await paddle_service.sync_tenant_subscription(db, tenant.id, subscription)
    logger.info(
        "paddle_webhook_subscription_synced",
        tenant_id=tenant.id,
        subscription_id=subscription.id,
        status=subscription.status.value,
    )
    return True


async def handle_subscription_canceled(db: AsyncSession, data: dict[str, Any]) -> bool:
    """Drop the tenant to the free plan when a subscription is canceled."""
    tenant = await resolve_tenant(db, data)
    if tenant is None:
        logger.warning("paddle_webhook_cancel_no_tenant", subscription_id=data.get("id"))
        return False

    canceled_at = paddle_service.parse_paddle_datetime(data.get("canceled_at"))
    await paddle_service.clear_tenant_subscription(db, tenant.id, canceled_at)
    logger.info(
        "paddle_webhook_subscription_canceled",
        tenant_id=tenant.id,
        subscription_id=data.get("id"),
        canceled_at=canceled_at.isoformat() if canceled_at else None,
    )
    return True


async def handle_transaction_paid(db: AsyncSession, data: dict[str, Any]) -> bool:
    """Refresh the subscription after a successful payment (completed/paid)."""
    tenant = await resolve_tenant(db, data)
    if tenant is None:
        logger.warning("paddle_webhook_transaction_no_tenant", transaction_id=data.get("id"))
        return False

    await _link_customer(db, tenant, data.get("customer_id"))

    subscription_id = data.get("subscription_id")
    if not isinstance(subscription_id, str) or not subscription_id:
        # One-off transaction: nothing to sync beyond the customer link.
        return True

    if not paddle_service.is_configured():
        logger.warning(
            "paddle_webhook_transaction_not_configured",
            tenant_id=tenant.id,
            subscription_id=subscription_id,
        )
        return True

    try:
        subscription = await paddle_service.get_paddle_client().get_subscription(subscription_id)
    except PaddleNotConfiguredError:
        logger.warning("paddle_webhook_transaction_client_unavailable", tenant_id=tenant.id)
        return True

    await paddle_service.sync_tenant_subscription(db, tenant.id, subscription)
    logger.info(
        "paddle_webhook_transaction_synced",
        tenant_id=tenant.id,
        transaction_id=data.get("id"),
        subscription_id=subscription_id,
    )
    return True


async def handle_payment_failed(db: AsyncSession, data: dict[str, Any]) -> bool:
    """Notify tenant admins about a failed payment (no local state change)."""
    tenant = await resolve_tenant(db, data)
    if tenant is None:
        logger.warning("paddle_webhook_payment_failed_no_tenant", transaction_id=data.get("id"))
        return False

    transaction = paddle_service.transaction_from_payload(data)
    payments = data.get("payments")
    attempt_count = max(1, len(payments) if isinstance(payments, list) else 0)
    amount_due = f"{transaction.total_minor / 100:.2f} {transaction.currency_code}"

    logger.warning(
        "paddle_webhook_payment_failed",
        tenant_id=tenant.id,
        transaction_id=transaction.id,
        attempt_count=attempt_count,
        amount_due=amount_due,
    )

    result = await db.execute(
        select(User).where(
            User.tenant_id == tenant.id,
            User.role == UserRole.ADMIN,
            User.is_active == True,
            User.is_deleted == False,
        )
    )
    admin_users = result.scalars().all()

    email_service = get_email_service()
    for user in admin_users:
        try:
            email = decrypt_pii(user.email) if user.email else ""
            if not email:
                continue
            full_name = decrypt_pii(user.full_name) if user.full_name else ""
            # SMTP is blocking; keep it off the event loop.
            await asyncio.to_thread(
                email_service.send_payment_failed_email,
                email,
                full_name or email,
                attempt_count,
                amount_due,
            )
            logger.info(
                "payment_failed_email_sent",
                tenant_id=tenant.id,
                user_id=user.id,
                attempt_count=attempt_count,
            )
        except Exception as exc:  # noqa: BLE001 - one bad address must not block the rest
            logger.error(
                "payment_failed_email_error",
                tenant_id=tenant.id,
                user_id=getattr(user, "id", None),
                error=str(exc),
            )
    return True


async def handle_customer_event(db: AsyncSession, data: dict[str, Any]) -> bool:
    """Link a Paddle customer to the tenant named in ``custom_data.tenant_id``."""
    tenant_id = _parse_tenant_id(_custom_data(data).get("tenant_id"))
    customer_id = data.get("id")
    if tenant_id is None or not isinstance(customer_id, str) or not customer_id:
        logger.debug("paddle_webhook_customer_without_tenant", customer_id=customer_id)
        return False

    tenant = await get_tenant_by_id(db, tenant_id)
    if tenant is None:
        logger.warning("paddle_webhook_customer_tenant_unknown", tenant_id=tenant_id)
        return False

    await paddle_service.sync_tenant_paddle_customer(db, tenant.id, customer_id)
    tenant.paddle_customer_id = customer_id
    logger.info("paddle_webhook_customer_linked", tenant_id=tenant.id, customer_id=customer_id)
    return True


async def dispatch_event(db: AsyncSession, event_type: str, data: dict[str, Any]) -> bool:
    """Route an event to its handler. Returns True when tenant state was touched."""
    if event_type in SUBSCRIPTION_SYNC_EVENTS:
        return await handle_subscription_sync(db, data)
    if event_type == SUBSCRIPTION_CANCELED_EVENT:
        return await handle_subscription_canceled(db, data)
    if event_type in TRANSACTION_SYNC_EVENTS:
        return await handle_transaction_paid(db, data)
    if event_type == TRANSACTION_FAILED_EVENT:
        return await handle_payment_failed(db, data)
    if event_type in CUSTOMER_EVENTS:
        return await handle_customer_event(db, data)

    logger.debug("paddle_webhook_ignored", event_type=event_type)
    return False


# =============================================================================
# Endpoint
# =============================================================================


def _parse_payload(raw_body: bytes) -> tuple[str, str, Optional[str], dict[str, Any]]:
    """Decode the notification envelope -> (event_id, event_type, occurred_at, data)."""
    try:
        payload = json.loads(raw_body)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed JSON payload"
        ) from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed payload")

    event_id = payload.get("event_id")
    event_type = payload.get("event_type")
    data = payload.get("data")
    if (
        not isinstance(event_id, str)
        or not event_id
        or not isinstance(event_type, str)
        or not event_type
        or not isinstance(data, dict)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payload must contain event_id, event_type and data",
        )

    occurred_at = payload.get("occurred_at")
    return event_id, event_type, occurred_at if isinstance(occurred_at, str) else None, data


@router.post(WEBHOOK_PATH, response_model=WebhookAck)
async def paddle_webhook(request: Request) -> WebhookAck:
    """Receive a Paddle notification, verify its signature and apply it once.

    Responses: 503 without a webhook secret, 400 for a missing/invalid/stale
    signature or malformed JSON, 200 ``received``/``duplicate``/``ignored``,
    500 (after rollback) when a handler fails so Paddle retries.
    """
    secret = settings.paddle_webhook_secret
    if not secret:
        logger.warning("paddle_webhook_secret_missing")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Paddle webhook secret is not configured",
        )

    raw_body = await request.body()
    signature_header = request.headers.get("Paddle-Signature")
    if not signature_header:
        logger.warning("paddle_webhook_missing_signature")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Paddle-Signature header",
        )

    try:
        paddle_service.verify_webhook_signature(raw_body, signature_header, secret)
    except PaddleSignatureError as exc:
        logger.warning("paddle_webhook_invalid_signature", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Paddle signature",
        ) from exc

    event_id, event_type, occurred_at_raw, data = _parse_payload(raw_body)
    occurred_at = paddle_service.parse_paddle_datetime(occurred_at_raw)

    logger.info("paddle_webhook_received", event_type=event_type, event_id=event_id)

    async with async_session_maker() as db:
        # Idempotency: claim the event id before touching any tenant state.
        try:
            async with db.begin_nested():
                db.add(
                    PaddleWebhookEvent(
                        event_id=event_id, event_type=event_type, occurred_at=occurred_at
                    )
                )
                await db.flush()
        except IntegrityError:
            await db.rollback()
            logger.info("paddle_webhook_duplicate", event_type=event_type, event_id=event_id)
            return WebhookAck(status="duplicate", event_type=event_type)

        try:
            handled = await dispatch_event(db, event_type, data)
            await db.commit()
        except Exception as exc:  # surfaced as 500 so Paddle retries
            logger.exception(
                "paddle_webhook_handler_error",
                event_type=event_type,
                event_id=event_id,
                error=str(exc),
            )
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Webhook handler failed; Paddle will retry",
            ) from exc

    return WebhookAck(status="received" if handled else "ignored", event_type=event_type)
