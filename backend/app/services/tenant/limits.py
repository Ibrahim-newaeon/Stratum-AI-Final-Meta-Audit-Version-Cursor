# =============================================================================
# Stratum AI - Tenant Limits Service
# =============================================================================
"""
Tenant resource usage vs. plan limits.

Provides a summary of current resource usage (users, campaigns) against the
limits stored on the Tenant record.
"""

from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.base_models import Campaign, Tenant, User
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = ["TenantLimitService"]


class TenantLimitService:
    """Reports tenant resource usage against plan limits."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _count(self, model: Any, tenant_id: int) -> int:
        """Count non-deleted rows of a tenant-scoped model."""
        stmt = select(func.count(model.id)).where(model.tenant_id == tenant_id)
        if hasattr(model, "is_deleted"):
            stmt = stmt.where(model.is_deleted == False)  # noqa: E712
        result = await self.db.execute(stmt)
        return int(result.scalar() or 0)

    async def get_usage_summary(self, tenant_id: int) -> dict[str, Any]:
        """
        Get usage vs. limits for a tenant.

        Returns a dict of resource -> {used, limit, pct_used}.
        """
        result = await self.db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant: Optional[Tenant] = result.scalar_one_or_none()

        if tenant is None:
            logger.warning("tenant_limits_tenant_not_found", tenant_id=tenant_id)
            return {}

        user_count = await self._count(User, tenant_id)
        campaign_count = await self._count(Campaign, tenant_id)

        def _entry(used: int, limit: Optional[int]) -> dict[str, Any]:
            pct = round(used / limit * 100, 1) if limit else None
            return {"used": used, "limit": limit, "pct_used": pct}

        return {
            "users": _entry(user_count, tenant.max_users),
            "campaigns": _entry(campaign_count, tenant.max_campaigns),
        }
