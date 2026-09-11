# =============================================================================
# Stratum AI - Free workspace (no payment gateway)
# =============================================================================
"""Self-serve accounts on this portal are unpaid full-access workspaces."""

import inspect

import pytest

from app.api.v1.endpoints import auth, auth_facebook
from app.core.subscription import _tier_for_plan, free_workspace_tenant_kwargs
from app.core.tiers import SubscriptionTier, TIER_FEATURES, Feature
from app.features.flags import get_default_features

pytestmark = pytest.mark.unit


def test_free_plan_maps_to_enterprise_tier() -> None:
    assert _tier_for_plan("free") is SubscriptionTier.ENTERPRISE
    assert _tier_for_plan("") is SubscriptionTier.ENTERPRISE
    assert _tier_for_plan("enterprise") is SubscriptionTier.ENTERPRISE
    assert _tier_for_plan("starter") is SubscriptionTier.STARTER
    assert _tier_for_plan("unknown-plan") is SubscriptionTier.STARTER


def test_free_plan_gets_enterprise_feature_defaults() -> None:
    features = get_default_features("free")
    assert features["signal_health"] is True
    assert features["max_users"] == -1
    enterprise = get_default_features("enterprise")
    assert features == enterprise


def test_signup_tenant_kwargs_are_unpaid_enterprise() -> None:
    fields = free_workspace_tenant_kwargs()
    assert fields["plan"] == "enterprise"
    assert fields["plan_expires_at"] is None
    assert fields["max_users"] >= 999999
    assert fields["max_campaigns"] >= 999999
    assert Feature.AD_ACCOUNTS_UNLIMITED in TIER_FEATURES[SubscriptionTier.ENTERPRISE]


def test_password_and_facebook_signup_provision_a_free_workspace() -> None:
    assert "free_workspace_tenant_kwargs" in inspect.getsource(auth)
    assert "free_workspace_tenant_kwargs" in inspect.getsource(auth_facebook)
    assert 'plan="free"' not in inspect.getsource(auth)
    assert 'plan="free"' not in inspect.getsource(auth_facebook)
