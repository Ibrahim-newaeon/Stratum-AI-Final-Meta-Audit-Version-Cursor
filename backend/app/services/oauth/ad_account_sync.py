"""Upsert Meta (and other platform) ad accounts onto a tenant connection.

Used by the OAuth callback (auto-sync after Connect) and by the campaign-builder
Sync Accounts action. Discovery / insights / autopilot only see rows with
``is_enabled=True``; a platform connection alone is not enough.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.campaign_builder import TenantAdAccount, TenantPlatformConnection
from app.services.oauth.base import AdAccountInfo

logger = get_logger(__name__)


def _platform_value(platform: object) -> str:
    return str(getattr(platform, "value", platform))


async def upsert_ad_accounts(
    db: AsyncSession,
    *,
    connection: TenantPlatformConnection,
    accounts: list[AdAccountInfo],
    enable: bool = True,
    account_ids: set[str] | None = None,
) -> list[TenantAdAccount]:
    """
    Create or update ``TenantAdAccount`` rows for ``connection``.

    Args:
        db: Active async session (caller commits).
        connection: Connected platform row that owns the accounts.
        accounts: Platform catalogue from ``fetch_ad_accounts``.
        enable: When True, mark upserted rows enabled for discovery/insights.
        account_ids: If set, only upsert these platform account ids.

    Returns:
        Upserted ``TenantAdAccount`` rows (enabled ones when ``enable`` is True).
    """
    platform = _platform_value(connection.platform)
    selected = [
        acc
        for acc in accounts
        if acc.account_id and (account_ids is None or acc.account_id in account_ids)
    ]
    if account_ids is not None:
        missing = account_ids - {acc.account_id for acc in selected}
        if missing:
            raise ValueError(f"Ad account(s) not found or not accessible: {sorted(missing)}")

    now = datetime.now(UTC)
    upserted: list[TenantAdAccount] = []

    for platform_acc in selected:
        result = await db.execute(
            select(TenantAdAccount).where(
                and_(
                    TenantAdAccount.tenant_id == connection.tenant_id,
                    TenantAdAccount.platform == platform,
                    TenantAdAccount.platform_account_id == platform_acc.account_id,
                )
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.connection_id = connection.id
            existing.name = platform_acc.name
            existing.business_name = platform_acc.business_name
            existing.currency = platform_acc.currency or existing.currency or "USD"
            existing.timezone = platform_acc.timezone or existing.timezone or "UTC"
            existing.account_status = platform_acc.status
            existing.last_synced_at = now
            existing.sync_error = None
            if enable:
                existing.is_enabled = True
            account = existing
        else:
            account = TenantAdAccount(
                tenant_id=connection.tenant_id,
                connection_id=connection.id,
                platform=platform,
                platform_account_id=platform_acc.account_id,
                name=platform_acc.name,
                business_name=platform_acc.business_name,
                currency=platform_acc.currency or "USD",
                timezone=platform_acc.timezone or "UTC",
                account_status=platform_acc.status,
                is_enabled=enable,
                last_synced_at=now,
            )
            db.add(account)

        await db.flush()
        upserted.append(account)

    logger.info(
        "ad_accounts_upserted",
        platform=platform,
        tenant_id=connection.tenant_id,
        count=len(upserted),
        enabled=enable,
    )
    return upserted


async def sync_connection_ad_accounts(
    db: AsyncSession,
    *,
    connection: TenantPlatformConnection,
    oauth_service,
    enable: bool = True,
) -> list[TenantAdAccount]:
    """
    Decrypt the connection token, fetch platform ad accounts, and upsert them.

    Raises on decrypt/fetch failure so callers can decide whether to fail the
    request or treat sync as best-effort.
    """
    access_token = oauth_service.decrypt_token(connection.access_token_encrypted)
    if not access_token:
        raise ValueError("Platform connection has no access token")
    platform_accounts = await oauth_service.fetch_ad_accounts(access_token)
    return await upsert_ad_accounts(
        db,
        connection=connection,
        accounts=platform_accounts,
        enable=enable,
    )
