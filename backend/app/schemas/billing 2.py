# =============================================================================
# Stratum AI - Billing Schemas (Paddle Billing)
# =============================================================================
"""
Pydantic models for the tenant billing API (``/api/v1/billing/*``).

Every endpoint wraps one of these models in ``APIResponse[...]`` from
``app.schemas.response``. Timestamps are ISO-8601 UTC strings and amounts are
integers in minor units (cents) exactly as Paddle reports them.
"""

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Subscription tiers that can be purchased. Mirrors ``app.core.tiers.SubscriptionTier``.
TierName = Literal["starter", "professional", "enterprise"]

# Paddle environments (sandbox is the default for local/CI).
PaddleEnvironment = Literal["sandbox", "production"]

# Paddle subscription status strings surfaced to the frontend.
SubscriptionStatusName = Literal["active", "trialing", "past_due", "paused", "canceled"]


class BillingSchema(BaseModel):
    """Base schema for billing models (attribute access + whitespace stripping)."""

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


# =============================================================================
# Configuration
# =============================================================================


class TierPriceInfo(BillingSchema):
    """Display information for a purchasable tier."""

    tier: TierName = Field(..., description="Tier identifier")
    name: str = Field(..., description="Human readable tier name")
    price: Optional[float] = Field(
        None, description="List price per billing period (null = custom)"
    )
    currency: str = Field(..., description="ISO-4217 currency code")
    billing_period: str = Field(..., description="Billing period, e.g. 'monthly'")
    description: str = Field(..., description="Short marketing description")


class TierPriceIds(BillingSchema):
    """Paddle price ids configured per tier (``pri_...``)."""

    starter: Optional[str] = None
    professional: Optional[str] = None
    enterprise: Optional[str] = None


class BillingConfigResponse(BillingSchema):
    """Public billing configuration consumed by the frontend (Paddle.js bootstrap)."""

    paddle_configured: bool = Field(..., description="True when a Paddle API key is set")
    environment: PaddleEnvironment = Field(..., description="Paddle environment")
    client_token: Optional[str] = Field(None, description="Paddle.js client-side token")
    price_ids: TierPriceIds = Field(default_factory=TierPriceIds)
    tiers: list[TierPriceInfo] = Field(default_factory=list)


# =============================================================================
# Subscription
# =============================================================================


class BillingSubscriptionResponse(BillingSchema):
    """Current subscription state for the tenant."""

    paddle_configured: bool
    has_customer: bool
    has_subscription: bool
    customer_id: Optional[str] = None
    subscription_id: Optional[str] = None
    status: Optional[SubscriptionStatusName] = None
    tier: Optional[TierName] = None
    plan: str = Field(..., description="Tenant.plan as stored locally (free|starter|...)")
    current_period_start: Optional[str] = None
    current_period_end: Optional[str] = None
    next_billed_at: Optional[str] = None
    cancel_at_period_end: bool = False
    canceled_at: Optional[str] = None
    paused_at: Optional[str] = None
    trial_end: Optional[str] = None


# =============================================================================
# Checkout (client-side Paddle.js overlay)
# =============================================================================


class CheckoutSessionRequest(BillingSchema):
    """Request to prepare a Paddle.js overlay checkout."""

    tier: TierName = Field(..., description="Tier to purchase")
    success_url: Optional[str] = Field(
        None, max_length=2048, description="Where Paddle redirects after a successful checkout"
    )


class CheckoutCustomData(BillingSchema):
    """``customData`` passed to Paddle.Checkout.open and echoed back on webhooks."""

    tenant_id: str
    tier: str


class CheckoutSessionResponse(BillingSchema):
    """Everything Paddle.js needs to open the overlay checkout."""

    price_id: str
    client_token: str
    environment: PaddleEnvironment
    customer_id: str
    customer_email: str
    custom_data: CheckoutCustomData
    success_url: str
    display_mode: Literal["overlay"] = "overlay"


# =============================================================================
# Customer portal
# =============================================================================


class PortalSessionRequest(BillingSchema):
    """Request to open the Paddle customer portal.

    ``return_url`` is accepted for API symmetry; Paddle portal sessions do not
    take a return URL, so it is currently ignored by the backend.
    """

    return_url: Optional[str] = Field(None, max_length=2048)


class PortalSessionResponse(BillingSchema):
    """Authenticated Paddle customer portal links."""

    portal_url: str = Field(..., description="Portal overview URL")
    cancel_url: Optional[str] = Field(None, description="Deep link to cancel the subscription")
    update_payment_method_url: Optional[str] = Field(
        None, description="Deep link to update the payment method"
    )


# =============================================================================
# Subscription changes
# =============================================================================


class CancelRequest(BillingSchema):
    """Cancel the current subscription."""

    at_period_end: bool = Field(
        default=True, description="Cancel at the end of the billing period (default) or now"
    )


class UpgradeRequest(BillingSchema):
    """Move the current subscription to another tier."""

    new_tier: TierName
    prorate: bool = Field(default=True, description="Prorate the charge for the current period")


# =============================================================================
# Transactions / invoices
# =============================================================================


class BillingTransactionResponse(BillingSchema):
    """A Paddle transaction (invoice line for the billing history table)."""

    id: str
    invoice_number: Optional[str] = None
    status: str
    subscription_id: Optional[str] = None
    amount_minor: int = Field(..., description="Total in minor units (cents)")
    currency: str
    billed_at: Optional[str] = None
    created_at: Optional[str] = None


class TransactionInvoiceResponse(BillingSchema):
    """Short-lived URL of the invoice PDF for a transaction."""

    transaction_id: str
    invoice_url: str


__all__ = [
    "BillingConfigResponse",
    "BillingSubscriptionResponse",
    "BillingTransactionResponse",
    "CancelRequest",
    "CheckoutCustomData",
    "CheckoutSessionRequest",
    "CheckoutSessionResponse",
    "PaddleEnvironment",
    "PortalSessionRequest",
    "PortalSessionResponse",
    "SubscriptionStatusName",
    "TierName",
    "TierPriceIds",
    "TierPriceInfo",
    "TransactionInvoiceResponse",
    "UpgradeRequest",
]
