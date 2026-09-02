# =============================================================================
# Stratum AI - Subscription Tier API Endpoints
# =============================================================================
"""
Subscription tier endpoints.

Exposes the current tenant's tier, available features, limits, and the
public tier/pricing catalog.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.deps import get_current_user
from app.base_models import User
from app.core.feature_gate import get_current_tier
from app.core.tiers import (
    TIER_PRICING,
    Feature,
    SubscriptionTier,
    get_tier_info,
    has_feature,
)

router = APIRouter(prefix="/tier", tags=["tier"])


@router.get("")
async def get_current_tier_info(
    current_user: User = Depends(get_current_user),
) -> dict:
    """Get the current tenant's tier with features and limits."""
    tier = get_current_tier()
    info = get_tier_info(tier)
    info["pricing"] = TIER_PRICING.get(tier, {})
    return info


@router.get("/features/{feature_name}")
async def check_feature_access(
    feature_name: str,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Check whether the current tier has access to a specific feature."""
    try:
        feature = Feature(feature_name)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown feature: {feature_name}",
        )

    tier = get_current_tier()
    return {
        "feature": feature.value,
        "tier": tier.value,
        "has_access": has_feature(tier, feature),
    }


@router.get("/catalog")
async def get_tier_catalog() -> dict:
    """Get the public catalog of all tiers, features, and pricing."""
    return {
        "tiers": [
            {
                **get_tier_info(tier),
                "pricing": TIER_PRICING.get(tier, {}),
            }
            for tier in SubscriptionTier
        ]
    }
