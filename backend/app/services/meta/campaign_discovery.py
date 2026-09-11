# =============================================================================
# Stratum AI - Meta Campaign Discovery (read-only upsert)
# =============================================================================
"""
Discover Meta campaigns for enabled ad accounts and upsert local ``Campaign``
rows so insights sync has something to attach metrics to.

This is the persistence half of campaign discovery: ``campaign_discovery_client``
does the (GET-only) network call; this module resolves the tenant's read
credential, maps Meta status/objective onto the unified campaign model, and
upserts by ``(tenant_id, platform=meta, external_id)``.

Invariants
----------
* Read-only toward Meta (``ads_read``). Never imports or calls ``write_client``.
* Does **not** advance ``Campaign.last_synced_at``. Freshness is owned by
  insights ingestion; discovery only creates/updates catalogue metadata.
* Does **not** write Meta budget amounts into ``daily_budget_cents`` /
  ``lifetime_budget_cents``. Meta API units are not this schema's
  hundredths-of-major; inventing a conversion here would lie to every
  dashboard that divides by 100. Budgets stay in ``raw_data`` only.
* Soft-deleted local rows are revived when Meta still lists the campaign.

Everything here runs against a **synchronous** SQLAlchemy ``Session`` because
its caller is the Celery task ``discover_tenant_campaigns``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models import (
    AdPlatform,
    Campaign,
    CampaignStatus,
    ConnectionStatus,
    TenantAdAccount,
    TenantPlatformConnection,
)
from app.services.encryption import decrypt_token
from app.services.meta.campaign_discovery_client import (
    MetaCampaignDiscoveryClient,
    MetaCampaignRow,
    MetaCampaignsTruncatedError,
)
from app.services.meta.insights_client import (
    MetaAPIError,
    MetaRateLimitError,
    MetaTokenError,
)
from app.services.meta.insights_ingestion import (
    META_PLATFORM,
    USABLE_CONNECTION_STATUSES,
    MetaCredentialsError,
    run_coroutine,
)

logger = get_logger(__name__)

DEFAULT_CURRENCY = "USD"

#: Meta ``effective_status`` / ``status`` → local ``CampaignStatus``.
#: Prefer ``effective_status`` when present (it reflects delivery reality).
_META_STATUS_MAP: dict[str, CampaignStatus] = {
    "ACTIVE": CampaignStatus.ACTIVE,
    "PAUSED": CampaignStatus.PAUSED,
    "CAMPAIGN_PAUSED": CampaignStatus.PAUSED,
    "DELETED": CampaignStatus.ARCHIVED,
    "ARCHIVED": CampaignStatus.ARCHIVED,
    "IN_PROCESS": CampaignStatus.DRAFT,
    "WITH_ISSUES": CampaignStatus.ACTIVE,
    "PENDING_REVIEW": CampaignStatus.DRAFT,
    "DISAPPROVED": CampaignStatus.PAUSED,
}


@dataclass(frozen=True)
class MetaReadConnection:
    """Decrypted Meta read credential plus enabled ad accounts."""

    access_token: str
    connection_id: Any
    ad_accounts: Sequence[TenantAdAccount]


@dataclass
class CampaignDiscoveryAccountResult:
    """Outcome for one enabled ad account."""

    ad_account_id: str
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    revived: int = 0
    status: str = "success"
    reason: Optional[str] = None


@dataclass
class CampaignDiscoveryResult:
    """Aggregate discovery outcome for one tenant."""

    tenant_id: int
    accounts: list[CampaignDiscoveryAccountResult] = field(default_factory=list)
    created_campaign_ids: list[int] = field(default_factory=list)

    @property
    def inserted(self) -> int:
        """Total campaigns inserted across accounts."""
        return sum(a.inserted for a in self.accounts)

    @property
    def updated(self) -> int:
        """Total campaigns updated across accounts."""
        return sum(a.updated for a in self.accounts)

    @property
    def fetched(self) -> int:
        """Total campaign nodes fetched across accounts."""
        return sum(a.fetched for a in self.accounts)


def _status_value(raw: Any) -> str:
    """Normalize an enum-or-string connection status to a lowercase string."""
    if raw is None:
        return ""
    value = getattr(raw, "value", raw)
    return str(value).strip().lower()


def map_meta_campaign_status(
    effective_status: Optional[str], status: Optional[str]
) -> CampaignStatus:
    """
    Map Meta campaign status fields onto ``CampaignStatus``.

    Prefers ``effective_status`` (delivery state) over configured ``status``.
    Unknown values default to ``ACTIVE`` so a newly discovered live campaign
    is not parked in draft by surprise.
    """
    for candidate in (effective_status, status):
        if not candidate:
            continue
        mapped = _META_STATUS_MAP.get(str(candidate).strip().upper())
        if mapped is not None:
            return mapped
    return CampaignStatus.ACTIVE


def parse_meta_date(value: Optional[str]) -> Optional[date]:
    """
    Parse a Meta ``start_time`` / ``stop_time`` into a UTC calendar date.

    Meta returns ISO-8601 with an offset (``2024-01-15T12:00:00-0800``) or a
    date-only string. Failures return ``None`` rather than raising - discovery
    must not abort the whole account over one bad timestamp.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            return date.fromisoformat(text)
        # Meta sometimes emits +0000 without a colon; fromisoformat wants +00:00.
        normalized = text
        if len(text) >= 5 and text[-5] in "+-" and text[-3] != ":":
            normalized = f"{text[:-2]}:{text[-2:]}"
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).date()
    except (TypeError, ValueError):
        logger.warning("meta_campaign_date_unparseable", value=text)
        return None


def normalize_act_id(account_id: str) -> str:
    """Ensure an ad account id carries the ``act_`` prefix."""
    account = str(account_id or "").strip()
    if not account:
        return account
    if not account.startswith("act_"):
        return f"act_{account}"
    return account


def resolve_meta_read_connection(db: Session, tenant_id: int) -> MetaReadConnection:
    """
    Resolve a usable Meta access token and the tenant's enabled ad accounts.

    Unlike ``resolve_meta_credentials``, this does not require a local
    ``Campaign`` row - discovery is what creates those rows.

    Raises:
        MetaCredentialsError: no usable connection or no enabled ad account.
    """
    connection = db.execute(
        select(TenantPlatformConnection).where(
            TenantPlatformConnection.tenant_id == tenant_id,
            TenantPlatformConnection.platform == META_PLATFORM,
        )
    ).scalar_one_or_none()

    if connection is None:
        raise MetaCredentialsError(
            "no_meta_connection",
            f"Tenant {tenant_id} has no Meta platform connection",
        )

    status = _status_value(getattr(connection, "status", None))
    if status not in USABLE_CONNECTION_STATUSES:
        raise MetaCredentialsError(
            "connection_not_connected",
            f"Meta connection for tenant {tenant_id} is '{status}', not connected",
        )

    expires_at = getattr(connection, "token_expires_at", None)
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise MetaCredentialsError(
                "token_expired",
                f"Meta access token for tenant {tenant_id} expired at {expires_at.isoformat()}",
            )

    if not getattr(connection, "access_token_encrypted", None):
        raise MetaCredentialsError(
            "token_missing",
            f"Meta connection for tenant {tenant_id} stores no access token",
        )

    try:
        access_token = decrypt_token(connection.access_token_encrypted)
    except Exception as exc:
        raise MetaCredentialsError(
            "token_undecryptable",
            f"Meta access token for tenant {tenant_id} could not be decrypted "
            f"({type(exc).__name__})",
        ) from exc

    ad_accounts = list(
        db.execute(
            select(TenantAdAccount)
            .where(
                TenantAdAccount.tenant_id == tenant_id,
                TenantAdAccount.platform == META_PLATFORM,
                TenantAdAccount.is_enabled.is_(True),
            )
            .order_by(TenantAdAccount.created_at)
        ).scalars().all()
    )

    if not ad_accounts:
        raise MetaCredentialsError(
            "no_ad_account",
            f"Tenant {tenant_id} has no enabled Meta ad account to discover from",
        )

    return MetaReadConnection(
        access_token=access_token,
        connection_id=getattr(connection, "id", None),
        ad_accounts=ad_accounts,
    )


def fetch_account_campaigns(
    access_token: str,
    ad_account_id: str,
    *,
    transport: Any = None,
) -> list[MetaCampaignRow]:
    """
    Fetch campaign nodes for one ad account (sync bridge over the async client).

    Args:
        access_token: Decrypted Meta token with ``ads_read``.
        ad_account_id: Platform ad account id.
        transport: Optional ``httpx`` transport for tests.
    """

    async def _run() -> list[MetaCampaignRow]:
        async with MetaCampaignDiscoveryClient(
            access_token, transport=transport
        ) as client:
            return await client.list_campaigns(ad_account_id)

    return run_coroutine(_run())


def upsert_discovered_campaign(
    db: Session,
    *,
    tenant_id: int,
    ad_account: TenantAdAccount,
    row: MetaCampaignRow,
) -> tuple[Campaign, str]:
    """
    Insert or update one local ``Campaign`` from a Meta discovery row.

    Returns:
        ``(campaign, action)`` where action is ``inserted``, ``updated``, or
        ``revived`` (soft-deleted row restored and updated).
    """
    platform_account_id = normalize_act_id(
        str(ad_account.platform_account_id or "")
    )
    currency = (
        str(getattr(ad_account, "currency", None) or "").strip() or DEFAULT_CURRENCY
    )
    status = map_meta_campaign_status(row.effective_status, row.status)
    objective = (row.objective or "")[:100] or None
    start_date = parse_meta_date(row.start_time)
    end_date = parse_meta_date(row.stop_time)

    existing = db.execute(
        select(Campaign).where(
            Campaign.tenant_id == tenant_id,
            Campaign.platform == AdPlatform.META,
            Campaign.external_id == row.external_id,
        )
    ).scalar_one_or_none()

    raw_data = {
        "source": "meta_campaign_discovery",
        "meta": row.raw,
        "discovered_ad_account_id": platform_account_id,
    }

    if existing is None:
        campaign = Campaign(
            tenant_id=tenant_id,
            platform=AdPlatform.META,
            external_id=row.external_id,
            account_id=platform_account_id,
            name=row.name[:500],
            status=status,
            objective=objective,
            currency=currency[:3],
            start_date=start_date,
            end_date=end_date,
            labels=[],
            raw_data=raw_data,
            # Budgets intentionally omitted - see module docstring.
            daily_budget_cents=None,
            lifetime_budget_cents=None,
        )
        db.add(campaign)
        db.flush()
        return campaign, "inserted"

    action = "updated"
    if getattr(existing, "is_deleted", False):
        existing.is_deleted = False
        existing.deleted_at = None
        action = "revived"

    existing.name = row.name[:500]
    existing.status = status
    existing.objective = objective
    existing.account_id = platform_account_id or existing.account_id
    existing.currency = currency[:3] or existing.currency
    if start_date is not None:
        existing.start_date = start_date
    if end_date is not None:
        existing.end_date = end_date
    existing.raw_data = raw_data
    # Never touch last_synced_at, spend, or metric aggregates here.
    return existing, action


def discover_tenant_campaigns(
    db: Session,
    tenant_id: int,
    *,
    transport: Any = None,
) -> CampaignDiscoveryResult:
    """
    Discover Meta campaigns for every enabled ad account of a tenant.

    Fetches the full catalogue per account before upserting. A truncated or
    failed fetch for one account leaves that account's local rows unchanged
    and continues with the next account.
    """
    result = CampaignDiscoveryResult(tenant_id=tenant_id)
    connection = resolve_meta_read_connection(db, tenant_id)

    for ad_account in connection.ad_accounts:
        account_id = normalize_act_id(str(ad_account.platform_account_id or ""))
        account_result = CampaignDiscoveryAccountResult(ad_account_id=account_id)

        if not account_id:
            account_result.status = "skipped"
            account_result.reason = "blank_ad_account_id"
            result.accounts.append(account_result)
            continue

        try:
            rows = fetch_account_campaigns(
                connection.access_token,
                account_id,
                transport=transport,
            )
        except MetaTokenError:
            # Dead token cannot succeed on any later account either.
            raise
        except MetaRateLimitError:
            # Let the Celery task back off; partial upserts for earlier
            # accounts are still in the session for the caller to commit.
            raise
        except MetaCampaignsTruncatedError as exc:
            account_result.status = "failed"
            account_result.reason = "campaigns_truncated"
            account_result.fetched = exc.rows
            ad_account.sync_error = str(exc)[:1000]
            result.accounts.append(account_result)
            logger.error(
                "Meta campaigns pull truncated for tenant %s account %s",
                tenant_id,
                account_id,
            )
            continue
        except MetaAPIError as exc:
            account_result.status = "failed"
            account_result.reason = str(exc)
            ad_account.sync_error = str(exc)[:1000]
            result.accounts.append(account_result)
            logger.warning(
                "Meta campaigns pull failed for tenant %s account %s: %s",
                tenant_id,
                account_id,
                exc,
            )
            continue

        account_result.fetched = len(rows)

        for row in rows:
            campaign, action = upsert_discovered_campaign(
                db,
                tenant_id=tenant_id,
                ad_account=ad_account,
                row=row,
            )
            if action == "inserted":
                account_result.inserted += 1
                if campaign.id is not None:
                    result.created_campaign_ids.append(int(campaign.id))
            elif action == "revived":
                account_result.revived += 1
                account_result.updated += 1
            else:
                account_result.updated += 1

        ad_account.last_synced_at = datetime.now(UTC)
        ad_account.sync_error = None
        result.accounts.append(account_result)

    return result
