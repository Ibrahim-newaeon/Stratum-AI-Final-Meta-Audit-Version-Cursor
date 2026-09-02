# =============================================================================
# Stratum AI - Tenant Services
# =============================================================================
"""
Tenant management services: licensing and plan limits.

Only re-export names that the submodules actually define. A dangling
re-export here breaks ``import app.services.tenant.limits`` for every caller
(e.g. ``GET /api/v1/subscription/usage-summary``), because importing a
submodule runs this package ``__init__`` first.
"""

from .licensing import LicenseValidationService, LicenseValidator
from .limits import TenantLimitService

__all__ = [
    "LicenseValidationService",
    "LicenseValidator",
    "TenantLimitService",
]
