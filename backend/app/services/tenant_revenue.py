# =============================================================================
# Stratum AI - Tenant Subscription Revenue
# =============================================================================
"""
How a tenant's plan turns into money, in one place.

These rules used to live as private helpers inside
``app/api/v1/endpoints/superadmin.py``. The account-manager portfolio needs the
same answers - which plan a tenant is on, what it is worth per month, when it
renews - and a second copy would have drifted from the platform revenue
figures the first one produces. Paddle is the merchant of record; nothing here
computes a price of its own beyond reading the tier table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.tiers import TIER_PRICING, SubscriptionTier

__all__ = [
    "BILLABLE_SUBSCRIPTION_STATUSES",
    "plan_display_name",
    "plan_list_price",
    "tenant_mrr",
    "tenant_renewal_date",
    "tenant_subscription_status",
]

# Paddle subscription statuses that generate recurring revenue. Trials, paused and
# canceled subscriptions (and tenants with no Paddle subscription) contribute 0 MRR.
BILLABLE_SUBSCRIPTION_STATUSES = frozenset({"active", "past_due"})


def tenant_subscription_status(tenant: Any) -> str:
    """
    Return the Paddle subscription status of a tenant for platform analytics.

    Tenants without a Paddle subscription (``subscription_status`` is NULL) are treated as
    ``active`` platform accounts so that free / admin-granted plans still count as live
    tenants in the revenue and portfolio views.

    Args:
        tenant: The tenant row to read.

    Returns:
        The Paddle status string, or ``active`` when the tenant has none.
    """
    return getattr(tenant, "subscription_status", None) or "active"


def plan_display_name(plan: str | None) -> str | None:
    """
    Human readable plan name, taken from ``TIER_PRICING`` when the plan is a paid tier.

    Args:
        plan: The stored plan slug, or None.

    Returns:
        The display name, or None when the tenant has no plan at all. An
        unrecognised slug is titled rather than dropped - it is a real plan the
        tier table has not been told about, not a missing one.
    """
    if not plan:
        return None
    try:
        return str(TIER_PRICING[SubscriptionTier(plan)]["name"])
    except (ValueError, KeyError):
        return plan.replace("_", " ").title()


def plan_list_price(plan: str | None) -> float:
    """
    Monthly list price of a plan from ``TIER_PRICING`` (0 for free / custom pricing).

    Args:
        plan: The stored plan slug, or None.

    Returns:
        The monthly list price in major currency units.
    """
    if not plan:
        return 0.0
    try:
        price = TIER_PRICING[SubscriptionTier(plan)]["price"]
    except (ValueError, KeyError):
        return 0.0
    return float(price) if price else 0.0


def tenant_mrr(tenant: Any) -> float:
    """
    Monthly recurring revenue attributed to a tenant.

    Uses an explicit ``mrr_cents`` value when the tenant carries one; otherwise derives it
    from the plan's list price while the Paddle subscription is billable (active or
    past_due). Trials, paused / canceled subscriptions, tenants without a Paddle
    subscription and custom-priced (enterprise) plans contribute 0.

    Args:
        tenant: The tenant row to read.

    Returns:
        The tenant's MRR in major currency units.
    """
    mrr_cents = getattr(tenant, "mrr_cents", None)
    if mrr_cents:
        return mrr_cents / 100
    if (
        getattr(tenant, "subscription_status", None)
        not in BILLABLE_SUBSCRIPTION_STATUSES
    ):
        return 0.0
    return plan_list_price(tenant.plan)


def tenant_renewal_date(tenant: Any) -> datetime | None:
    """
    When the tenant's subscription next renews, or None when nothing says.

    ``current_period_end`` is the Paddle-authoritative answer and wins whenever
    Paddle has told us one. ``plan_expires_at`` is the fallback for
    admin-granted plans that Paddle does not bill. A tenant with neither has no
    known renewal date, which must be rendered as unknown rather than as a
    renewal that is due today or overdue.

    Args:
        tenant: The tenant row to read.

    Returns:
        The renewal instant, or None.
    """
    return getattr(tenant, "current_period_end", None) or getattr(
        tenant, "plan_expires_at", None
    )
