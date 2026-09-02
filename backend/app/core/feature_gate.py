# =============================================================================
# Stratum AI - Feature Gating (Subscription Tier Context)
# =============================================================================
"""
Request-scoped subscription tier context for feature gating.

Middleware (or dependencies) can set the current tenant's tier with
``set_current_tier``; endpoints then call ``get_current_tier`` and combine it
with ``app.core.tiers.has_feature`` to gate functionality.
"""

from contextvars import ContextVar, Token

from app.core.config import settings
from app.core.tiers import SubscriptionTier

__all__ = ["get_current_tier", "reset_current_tier", "set_current_tier"]

_current_tier: ContextVar[SubscriptionTier | None] = ContextVar("current_tier", default=None)


def _default_tier() -> SubscriptionTier:
    """Resolve the fallback tier from config (defaults to enterprise in dev)."""
    configured = getattr(settings, "default_subscription_tier", None)
    if configured:
        try:
            return SubscriptionTier(str(configured).lower())
        except ValueError:
            pass
    return SubscriptionTier.ENTERPRISE


def get_current_tier() -> SubscriptionTier:
    """Get the subscription tier for the current request context."""
    tier = _current_tier.get()
    return tier if tier is not None else _default_tier()


def set_current_tier(tier: SubscriptionTier) -> Token:
    """Bind the subscription tier for the current request context."""
    return _current_tier.set(tier)


def reset_current_tier(token: Token) -> None:
    """Reset the tier context to its previous value."""
    _current_tier.reset(token)
