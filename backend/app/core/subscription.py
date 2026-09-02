# =============================================================================
# Stratum AI - Subscription Status
# =============================================================================
"""
Subscription lifecycle evaluation.

Derives a tenant's subscription status (active, expiring soon, grace period,
expired) from the Tenant record's plan and expiry date. Thresholds come from
config with sensible defaults.
"""

import enum
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.core.tiers import SubscriptionTier

logger = get_logger(__name__)

__all__ = [
    "EXPIRY_WARNING_DAYS",
    "GRACE_PERIOD_DAYS",
    "SubscriptionInfo",
    "SubscriptionStatus",
    "get_expiry_warning_message",
    "get_subscription_info",
]

# Thresholds are configurable via settings; defaults applied when unset
EXPIRY_WARNING_DAYS: int = int(getattr(settings, "subscription_expiry_warning_days", 14))
GRACE_PERIOD_DAYS: int = int(getattr(settings, "subscription_grace_period_days", 7))


class SubscriptionStatus(str, enum.Enum):
    """Lifecycle status of a tenant subscription."""

    ACTIVE = "active"
    EXPIRING_SOON = "expiring_soon"
    GRACE_PERIOD = "grace_period"
    EXPIRED = "expired"


def _tier_for_plan(plan: str) -> SubscriptionTier:
    """Map a tenant plan string to a subscription tier."""
    try:
        return SubscriptionTier(plan.lower())
    except ValueError:
        # Unknown/free plans map to the starter tier
        return SubscriptionTier.STARTER


@dataclass
class SubscriptionInfo:
    """Evaluated subscription state for a tenant."""

    tenant_id: int
    plan: str
    tier: SubscriptionTier
    status: SubscriptionStatus
    expires_at: Optional[datetime] = None
    days_until_expiry: Optional[int] = None
    days_in_grace: Optional[int] = None
    is_access_restricted: bool = False
    restriction_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "tenant_id": self.tenant_id,
            "plan": self.plan,
            "tier": self.tier.value,
            "status": self.status.value,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "days_until_expiry": self.days_until_expiry,
            "days_in_grace": self.days_in_grace,
            "is_access_restricted": self.is_access_restricted,
            "restriction_reason": self.restriction_reason,
        }


def evaluate_subscription(
    tenant_id: int,
    plan: str,
    expires_at: Optional[datetime],
    now: Optional[datetime] = None,
) -> SubscriptionInfo:
    """Evaluate subscription state from plan and expiry timestamp."""
    now = now or datetime.now(UTC)
    tier = _tier_for_plan(plan)

    if expires_at is None:
        # No expiry set - treat as active (e.g. free/internal plans)
        return SubscriptionInfo(
            tenant_id=tenant_id,
            plan=plan,
            tier=tier,
            status=SubscriptionStatus.ACTIVE,
        )

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    delta_days = (expires_at - now).days

    if delta_days >= 0:
        status = (
            SubscriptionStatus.EXPIRING_SOON
            if delta_days <= EXPIRY_WARNING_DAYS
            else SubscriptionStatus.ACTIVE
        )
        return SubscriptionInfo(
            tenant_id=tenant_id,
            plan=plan,
            tier=tier,
            status=status,
            expires_at=expires_at,
            days_until_expiry=delta_days,
        )

    days_past = (now - expires_at).days
    if days_past <= GRACE_PERIOD_DAYS:
        return SubscriptionInfo(
            tenant_id=tenant_id,
            plan=plan,
            tier=tier,
            status=SubscriptionStatus.GRACE_PERIOD,
            expires_at=expires_at,
            days_until_expiry=0,
            days_in_grace=days_past,
        )

    return SubscriptionInfo(
        tenant_id=tenant_id,
        plan=plan,
        tier=tier,
        status=SubscriptionStatus.EXPIRED,
        expires_at=expires_at,
        days_until_expiry=0,
        days_in_grace=days_past,
        is_access_restricted=True,
        restriction_reason="Subscription expired. Please renew to restore access.",
    )


async def get_subscription_info(tenant_id: int) -> SubscriptionInfo:
    """Load the tenant record and evaluate its subscription state."""
    from sqlalchemy import select

    from app.base_models import Tenant
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = result.scalar_one_or_none()

    if tenant is None:
        logger.warning("subscription_tenant_not_found", tenant_id=tenant_id)
        return SubscriptionInfo(
            tenant_id=tenant_id,
            plan="unknown",
            tier=SubscriptionTier.STARTER,
            status=SubscriptionStatus.EXPIRED,
            is_access_restricted=True,
            restriction_reason="Tenant not found",
        )

    return evaluate_subscription(
        tenant_id=tenant_id,
        plan=tenant.plan or "free",
        expires_at=tenant.plan_expires_at,
    )


def get_expiry_warning_message(info: SubscriptionInfo) -> Optional[str]:
    """Build a user-facing warning message for the given subscription state."""
    if info.status == SubscriptionStatus.EXPIRING_SOON:
        days = info.days_until_expiry or 0
        if days == 0:
            return "Your subscription expires today. Renew now to avoid interruption."
        return f"Your subscription expires in {days} days. Renew now to avoid interruption."

    if info.status == SubscriptionStatus.GRACE_PERIOD:
        remaining = max(GRACE_PERIOD_DAYS - (info.days_in_grace or 0), 0)
        return (
            f"Your subscription has expired. You have {remaining} days of grace "
            "period remaining before access is restricted."
        )

    if info.status == SubscriptionStatus.EXPIRED:
        return "Your subscription has expired. Please renew to restore access."

    return None
