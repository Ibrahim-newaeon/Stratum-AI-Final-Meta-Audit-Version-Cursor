# =============================================================================
# Stratum AI - Tenant Services
# =============================================================================
"""
Tenant management services: licensing, plan limits and onboarding state.

Only re-export names that the submodules actually define. A dangling
re-export here breaks ``import app.services.tenant.limits`` for every caller
(e.g. ``GET /api/v1/subscription/usage-summary``), because importing a
submodule runs this package ``__init__`` first.
"""

from .licensing import LicenseValidationService, LicenseValidator
from .limits import TenantLimitService
from .onboarding import get_or_create_onboarding, persist_chat_onboarding

__all__ = [
    "LicenseValidationService",
    "LicenseValidator",
    "TenantLimitService",
    "get_or_create_onboarding",
    "persist_chat_onboarding",
]
