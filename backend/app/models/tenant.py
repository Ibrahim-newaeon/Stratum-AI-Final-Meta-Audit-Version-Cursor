# =============================================================================
# Stratum AI - Tenant Model Re-export
# =============================================================================
"""
Tenant model access point.

The canonical Tenant model lives in ``app.base_models``; this module keeps
the conventional ``app.models.tenant`` import path working for tasks and
services that expect it.
"""

from app.base_models import Tenant, User

__all__ = ["Tenant", "User"]
