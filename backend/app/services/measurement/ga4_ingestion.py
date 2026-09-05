# =============================================================================
# Stratum AI - GA4 Baseline Ingestion (read-only measurement)
# =============================================================================
"""
Ingest the daily GA4 baseline into ``fact_ga4_daily`` and expose aggregate
helpers for the Trust Layer.

GA4 is measurement-only: the rows written here are the *independent* revenue
and conversion baseline compared against Meta-reported numbers (attribution
variance), fed into EMQ diagnostics and the Trust Gate. Nothing here reads
or acts on non-Meta ad campaigns; ``classify_meta_traffic`` only tags which
GA4 sessions came from Facebook / Instagram / WhatsApp.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.measurement import FactGA4Daily, MeasurementStatus, TenantGA4Integration
from app.services.encryption import decrypt_token
from app.services.measurement.ga4_client import (
    GA4AuthError,
    GA4ClientError,
    GA4DailyRow,
    GA4DataClient,
)

logger = get_logger(__name__)

NOT_CONFIGURED_MESSAGE = "GA4 not configured"
UPSERT_BATCH_SIZE = 500

# =============================================================================
# Meta traffic classification
# =============================================================================

#: Exact (normalised) GA4 sessionSource tokens -> Meta channel.
META_SOURCE_TOKENS: dict[str, str] = {
    # Facebook
    "facebook": "facebook",
    "fb": "facebook",
    "meta": "facebook",
    "facebook.com": "facebook",
    "l.facebook.com": "facebook",
    "m.facebook.com": "facebook",
    "lm.facebook.com": "facebook",
    "business.facebook.com": "facebook",
    "facebook_ads": "facebook",
    "facebook-ads": "facebook",
    "meta_ads": "facebook",
    "meta-ads": "facebook",
    "fbclid": "facebook",
    # Instagram
    "instagram": "instagram",
    "ig": "instagram",
    "instagram.com": "instagram",
    "l.instagram.com": "instagram",
    "instagram_ads": "instagram",
    # WhatsApp
    "whatsapp": "whatsapp",
    "wa": "whatsapp",
    "whatsapp.com": "whatsapp",
    "web.whatsapp.com": "whatsapp",
    "api.whatsapp.com": "whatsapp",
    "wa.me": "whatsapp",
}

#: Mediums that indicate paid Meta traffic (informational - any Meta source
#: counts as Meta traffic regardless of medium so organic Meta is included).
META_PAID_MEDIUMS: frozenset[str] = frozenset(
    {"paid_social", "cpc", "paid", "social", "paidsocial", "ppc"}
)

#: Sources that must never be classified as Meta, whatever the medium.
NON_META_SOURCE_MARKERS: tuple[str, ...] = ("google", "gclid", "doubleclick", "youtube")

META_CHANNELS: tuple[str, ...] = ("facebook", "instagram", "whatsapp")


def _normalise_token(value: str | None) -> str:
    """Lower-case, trim and strip protocol / ``www.`` from a source token."""
    token = (value or "").strip().lower()
    for prefix in ("https://", "http://"):
        token = token.removeprefix(prefix)
    token = token.removeprefix("www.")
    return token.rstrip("/")


def is_paid_medium(utm_medium: str | None) -> bool:
    """Whether a GA4 sessionMedium looks like paid traffic."""
    return _normalise_token(utm_medium) in META_PAID_MEDIUMS


def classify_meta_traffic(utm_source: str, utm_medium: str) -> tuple[bool, str | None]:
    """
    Classify a GA4 source/medium pair as Meta traffic.

    Returns ``(is_meta, channel)`` where channel is one of
    ``facebook`` | ``instagram`` | ``whatsapp`` or ``None``. Google / gclid
    sources are never classified as Meta.
    """
    source = _normalise_token(utm_source)
    medium = _normalise_token(utm_medium)

    if not source:
        return False, None
    if any(marker in source for marker in NON_META_SOURCE_MARKERS):
        return False, None
    if any(marker in medium for marker in ("gclid",)):
        return False, None

    channel = META_SOURCE_TOKENS.get(source)
    if channel is None:
        # Fuzzy fallback for variants like "facebook_paid", "ig_stories", "meta-cbo"
        if "instagram" in source or source.startswith(("ig_", "ig-")):
            channel = "instagram"
        elif "facebook" in source or source.startswith(("fb_", "fb-", "meta_", "meta-")):
            channel = "facebook"
        elif "whatsapp" in source or source.startswith(("wa_", "wa-")):
            channel = "whatsapp"

    if channel is None:
        return False, None
    return True, channel


# =============================================================================
# Result data classes
# =============================================================================


@dataclass
class GA4SyncResult:
    """Outcome of a GA4 baseline sync for one tenant."""

    tenant_id: int
    configured: bool
    success: bool
    rows_upserted: int
    start_date: date | None
    end_date: date | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation (dates as ISO strings)."""
        return {
            "tenant_id": self.tenant_id,
            "configured": self.configured,
            "success": self.success,
            "rows_upserted": self.rows_upserted,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "message": self.message,
        }


@dataclass
class GA4BaselineSummary:
    """Aggregated GA4 baseline for a date window."""

    start_date: date
    end_date: date
    meta_only: bool
    sessions: int
    conversions: int
    revenue: float
    total_revenue: float
    days_with_data: int
    last_date: date | None
    daily: list[dict]


# =============================================================================
# Integration lookup
# =============================================================================


async def get_active_ga4_integration(
    db: AsyncSession, tenant_id: int
) -> TenantGA4Integration | None:
    """Return the tenant's active GA4 integration, if any."""
    result = await db.execute(
        select(TenantGA4Integration).where(
            TenantGA4Integration.tenant_id == tenant_id,
            TenantGA4Integration.is_active.is_(True),
        )
    )
    return result.scalar_one_or_none()


def build_client_from_integration(
    integration: TenantGA4Integration | None,
) -> GA4DataClient | None:
    """
    Build a read-only client from a stored integration.

    Returns ``None`` when the integration is missing, inactive or has no
    credentials. The decrypted JSON is never logged.
    """
    if integration is None or not integration.is_active:
        return None
    if not integration.service_account_json_encrypted or not integration.property_id:
        return None
    try:
        service_account_json = decrypt_token(integration.service_account_json_encrypted)
    except Exception as exc:  # noqa: BLE001 - decryption failure (key rotation etc.)
        logger.warning(
            "ga4_credentials_decrypt_failed",
            tenant_id=integration.tenant_id,
            error=type(exc).__name__,
        )
        return None
    try:
        return GA4DataClient.from_service_account_json(
            integration.property_id,
            service_account_json,
            timeout_seconds=settings.ga4_request_timeout_seconds,
        )
    except GA4AuthError as exc:
        logger.warning(
            "ga4_credentials_invalid",
            tenant_id=integration.tenant_id,
            error=str(exc),
        )
        return None


async def load_ga4_client_for_tenant(db: AsyncSession, tenant_id: int) -> GA4DataClient | None:
    """
    Load a read-only GA4 client for a tenant.

    Returns ``None`` when there is no active ``TenantGA4Integration`` or it has
    no stored credentials.
    """
    integration = await get_active_ga4_integration(db, tenant_id)
    return build_client_from_integration(integration)


async def list_tenants_with_ga4(db: AsyncSession) -> list[int]:
    """Tenant ids with an active GA4 integration that has credentials."""
    result = await db.execute(
        select(TenantGA4Integration.tenant_id)
        .where(
            TenantGA4Integration.is_active.is_(True),
            TenantGA4Integration.service_account_json_encrypted.isnot(None),
        )
        .order_by(TenantGA4Integration.tenant_id)
    )
    return [int(tid) for tid in result.scalars().all()]


# =============================================================================
# Sync
# =============================================================================


async def _has_fact_rows(db: AsyncSession, tenant_id: int, property_id: str) -> bool:
    """Whether any ``fact_ga4_daily`` rows exist for the tenant/property."""
    result = await db.execute(
        select(func.count(FactGA4Daily.id)).where(
            FactGA4Daily.tenant_id == tenant_id,
            FactGA4Daily.property_id == property_id,
        )
    )
    return bool(result.scalar() or 0)


def _resolve_window(
    lookback_days: int | None, backfill: bool, has_rows: bool, today: date
) -> tuple[date, date]:
    """Compute the [start, end] window for a sync (end is always yesterday)."""
    if backfill or not has_rows:
        days = max(int(lookback_days or 0), int(settings.ga4_backfill_days))
    else:
        days = int(lookback_days or settings.ga4_lookback_days)
    days = max(1, days)
    end = today - timedelta(days=1)
    start = today - timedelta(days=days)
    return start, end


def rows_to_fact_values(
    rows: list[GA4DailyRow], tenant_id: int, property_id: str
) -> list[dict[str, Any]]:
    """
    Convert client rows into ``fact_ga4_daily`` insert values with Meta
    classification, merging duplicates on the unique dimension key.
    """
    now = datetime.now(UTC)
    merged: dict[tuple[date, str, str, str], dict[str, Any]] = {}
    for row in rows:
        is_meta, channel = classify_meta_traffic(row.utm_source, row.utm_medium)
        key = (row.date, row.utm_source[:255], row.utm_medium[:255], row.utm_campaign[:255])
        existing = merged.get(key)
        if existing is None:
            merged[key] = {
                "tenant_id": tenant_id,
                "date": row.date,
                "property_id": property_id,
                "utm_source": key[1],
                "utm_medium": key[2],
                "utm_campaign": key[3],
                "is_meta_traffic": is_meta,
                "meta_channel": channel,
                "sessions": int(row.sessions),
                "conversions": int(row.conversions),
                "revenue": float(row.revenue),
                "total_revenue": float(row.total_revenue),
                "currency": row.currency,
                "ingested_at": now,
            }
        else:
            existing["sessions"] += int(row.sessions)
            existing["conversions"] += int(row.conversions)
            existing["revenue"] += float(row.revenue)
            existing["total_revenue"] += float(row.total_revenue)
    return list(merged.values())


async def upsert_fact_rows(db: AsyncSession, values: list[dict[str, Any]]) -> int:
    """Upsert fact rows on ``uq_fact_ga4_daily_dim`` in batches. Returns rows written."""
    written = 0
    for i in range(0, len(values), UPSERT_BATCH_SIZE):
        batch = values[i : i + UPSERT_BATCH_SIZE]
        if not batch:
            continue
        stmt = pg_insert(FactGA4Daily).values(batch)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_fact_ga4_daily_dim",
            set_={
                "is_meta_traffic": stmt.excluded.is_meta_traffic,
                "meta_channel": stmt.excluded.meta_channel,
                "sessions": stmt.excluded.sessions,
                "conversions": stmt.excluded.conversions,
                "revenue": stmt.excluded.revenue,
                "total_revenue": stmt.excluded.total_revenue,
                "currency": stmt.excluded.currency,
                "ingested_at": stmt.excluded.ingested_at,
            },
        )
        await db.execute(stmt)
        written += len(batch)
    return written


async def sync_ga4_for_tenant(
    db: AsyncSession,
    tenant_id: int,
    lookback_days: int | None = None,
    backfill: bool = False,
) -> GA4SyncResult:
    """
    Pull the GA4 daily baseline for a tenant and upsert ``fact_ga4_daily``.

    Never raises for a missing or invalid configuration - returns
    ``configured=False`` instead. API failures mark the integration
    ``status='error'`` and are returned in ``message``.
    """
    integration = await get_active_ga4_integration(db, tenant_id)
    client = build_client_from_integration(integration)
    if integration is None or client is None:
        return GA4SyncResult(
            tenant_id=tenant_id,
            configured=False,
            success=False,
            rows_upserted=0,
            start_date=None,
            end_date=None,
            message=NOT_CONFIGURED_MESSAGE,
        )

    property_id = client.property_id
    today = datetime.now(UTC).date()
    has_rows = await _has_fact_rows(db, tenant_id, property_id)
    start, end = _resolve_window(lookback_days, backfill, has_rows, today)

    conversion_events = [
        str(e) for e in (integration.conversion_event_names or []) if str(e).strip()
    ] or [settings.ga4_default_conversion_event]

    now = datetime.now(UTC)
    try:
        rows = await client.run_daily_report(start, end, conversion_events=conversion_events)
    except GA4ClientError as exc:
        integration.status = MeasurementStatus.ERROR.value
        integration.last_error = str(exc)[:2000]
        await db.commit()
        logger.warning(
            "ga4_sync_failed",
            tenant_id=tenant_id,
            property_id=property_id,
            error=str(exc),
        )
        return GA4SyncResult(
            tenant_id=tenant_id,
            configured=True,
            success=False,
            rows_upserted=0,
            start_date=start,
            end_date=end,
            message=str(exc),
        )

    values = rows_to_fact_values(rows, tenant_id, property_id)
    try:
        written = await upsert_fact_rows(db, values)
        integration.status = MeasurementStatus.CONNECTED.value
        integration.last_sync_at = now
        integration.last_sync_rows = written
        integration.last_error = None
        await db.commit()
    except Exception as exc:  # noqa: BLE001 - any storage failure must mark status=error
        await db.rollback()
        integration.status = MeasurementStatus.ERROR.value
        integration.last_error = f"Failed to store GA4 rows: {type(exc).__name__}"[:2000]
        await db.commit()
        logger.error(
            "ga4_sync_store_failed",
            tenant_id=tenant_id,
            property_id=property_id,
            error=str(exc),
        )
        return GA4SyncResult(
            tenant_id=tenant_id,
            configured=True,
            success=False,
            rows_upserted=0,
            start_date=start,
            end_date=end,
            message=f"Failed to store GA4 rows: {exc}",
        )


    logger.info(
        "ga4_sync_complete",
        tenant_id=tenant_id,
        property_id=property_id,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        rows=written,
        meta_rows=sum(1 for v in values if v["is_meta_traffic"]),
    )
    return GA4SyncResult(
        tenant_id=tenant_id,
        configured=True,
        success=True,
        rows_upserted=written,
        start_date=start,
        end_date=end,
        message=f"Synced {written} GA4 rows for {start.isoformat()}..{end.isoformat()}",
    )


# =============================================================================
# Baseline aggregation
# =============================================================================


async def get_ga4_baseline(
    db: AsyncSession,
    tenant_id: int,
    start_date: date,
    end_date: date,
    meta_only: bool = False,
) -> GA4BaselineSummary:
    """
    Aggregate the stored GA4 baseline for a window (totals + daily series).

    ``meta_only`` restricts to rows classified as Meta traffic.
    """
    stmt = (
        select(
            FactGA4Daily.date,
            func.coalesce(func.sum(FactGA4Daily.sessions), 0),
            func.coalesce(func.sum(FactGA4Daily.conversions), 0),
            func.coalesce(func.sum(FactGA4Daily.revenue), 0.0),
            func.coalesce(func.sum(FactGA4Daily.total_revenue), 0.0),
        )
        .where(
            FactGA4Daily.tenant_id == tenant_id,
            FactGA4Daily.date >= start_date,
            FactGA4Daily.date <= end_date,
        )
        .group_by(FactGA4Daily.date)
        .order_by(FactGA4Daily.date)
    )
    if meta_only:
        stmt = stmt.where(FactGA4Daily.is_meta_traffic.is_(True))

    result = await db.execute(stmt)
    daily: list[dict] = []
    sessions = conversions = 0
    revenue = total_revenue = 0.0
    last_date: date | None = None
    for row_date, row_sessions, row_conversions, row_revenue, row_total in result.all():
        row_sessions = int(row_sessions or 0)
        row_conversions = int(row_conversions or 0)
        row_revenue = round(float(row_revenue or 0.0), 2)
        row_total = float(row_total or 0.0)
        daily.append(
            {
                "date": row_date.isoformat(),
                "sessions": row_sessions,
                "conversions": row_conversions,
                "revenue": row_revenue,
            }
        )
        sessions += row_sessions
        conversions += row_conversions
        revenue += row_revenue
        total_revenue += row_total
        if last_date is None or row_date > last_date:
            last_date = row_date

    return GA4BaselineSummary(
        start_date=start_date,
        end_date=end_date,
        meta_only=meta_only,
        sessions=sessions,
        conversions=conversions,
        revenue=round(revenue, 2),
        total_revenue=round(total_revenue, 2),
        days_with_data=len(daily),
        last_date=last_date,
        daily=daily,
    )


__all__ = [
    "META_CHANNELS",
    "META_PAID_MEDIUMS",
    "META_SOURCE_TOKENS",
    "NOT_CONFIGURED_MESSAGE",
    "GA4BaselineSummary",
    "GA4SyncResult",
    "build_client_from_integration",
    "classify_meta_traffic",
    "get_active_ga4_integration",
    "get_ga4_baseline",
    "is_paid_medium",
    "list_tenants_with_ga4",
    "load_ga4_client_for_tenant",
    "rows_to_fact_values",
    "sync_ga4_for_tenant",
    "upsert_fact_rows",
]
