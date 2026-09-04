# =============================================================================
# Stratum AI - Meta Insights Ingestion (read-only)
# =============================================================================
"""
Map read-only Meta Ads Insights rows onto ``CampaignMetric`` and persist them.

This is the persistence half of the Meta ingestion: ``insights_client`` does
the (GET-only) network call, this module resolves the tenant's credentials,
converts a ``MetaInsightRow`` into column values and upserts it by
``(campaign_id, date)`` so re-pulling a day is idempotent.

Everything here runs against a **synchronous** SQLAlchemy ``Session`` because
its only caller is the Celery task ``app.workers.tasks.sync.sync_campaign_data``,
which uses ``SyncSessionLocal``.

Money
-----
``CampaignMetric.spend_cents`` / ``revenue_cents`` are integers, and every
reader in this repository converts them with a plain ``/ 100`` that knows
nothing about the currency (see ``app/ml/forecaster.py``,
``app/api/v1/endpoints/tenant_dashboard.py``, ``Campaign.calculate_metrics``).
The column's real contract is therefore *hundredths of the ad account's major
unit*, not ISO-4217 minor units. Writing true minor units for a zero-decimal
currency (JPY, KRW, ...) would make every dashboard show yen amounts 100x too
small.

So ``to_hundredths`` does two things, in this order:

1. Quantises the ``Decimal`` at the currency's **real** precision
   (``CURRENCY_MINOR_UNIT_EXPONENT``: 0 for JPY/KRW/..., 3 for BHD/KWD/...,
   2 otherwise), so a sub-unit that the currency does not have is never
   invented or carried through.
2. Scales that to hundredths of the major unit and rounds ``ROUND_HALF_UP``.

Result: ``JPY "1234"`` -> ``123400`` (renders as 1234 yen), ``USD "12.345"``
-> ``1235`` (12.35), ``BHD "1.234"`` -> ``123`` (1.23, the precision the
column can hold). ``float`` is never used anywhere on this path.

That deliberate x100 is also why the money columns are ``BigInteger``. int4
would cap at 2,147,483,647 hundredths = 21,474,836 major units, which is
~US$21M in USD but only ~US$16k in KRW and ~US$860 in VND - a zero-decimal
advertiser would hit ``integer out of range`` at ordinary spend and the whole
window would abort. Migration ``0002_widen_money_columns`` widens the existing
``campaigns`` / ``campaign_metrics`` money columns to int8, whose ceiling
(~9.2e16 major units) is unreachable in any currency.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models import (
    AdPlatform,
    Campaign,
    CampaignMetric,
    ConnectionStatus,
    TenantAdAccount,
    TenantPlatformConnection,
)
from app.services.encryption import decrypt_token
from app.services.meta.insights_client import (
    MetaInsightRow,
    MetaInsightsClient,
)

logger = get_logger(__name__)

T = TypeVar("T")

#: Platform key stored in ``tenant_platform_connection.platform``.
META_PLATFORM = AdPlatform.META.value

#: Connection statuses whose token is worth trying.
USABLE_CONNECTION_STATUSES: frozenset[str] = frozenset({ConnectionStatus.CONNECTED.value})

#: ISO 4217 currencies whose minor unit exponent is not 2. Anything absent
#: from this map is treated as a normal 2-decimal currency.
CURRENCY_MINOR_UNIT_EXPONENT: dict[str, int] = {
    # Zero-decimal (no sub-unit at all)
    "BIF": 0,
    "CLP": 0,
    "DJF": 0,
    "GNF": 0,
    "ISK": 0,
    "JPY": 0,
    "KMF": 0,
    "KRW": 0,
    "PYG": 0,
    "RWF": 0,
    "UGX": 0,
    "UYI": 0,
    "VND": 0,
    "VUV": 0,
    "XAF": 0,
    "XOF": 0,
    "XPF": 0,
    # Three-decimal
    "BHD": 3,
    "IQD": 3,
    "JOD": 3,
    "KWD": 3,
    "LYD": 3,
    "OMR": 3,
    "TND": 3,
}

#: Convenience view of the zero-decimal set (used by tests and callers).
ZERO_DECIMAL_CURRENCIES: frozenset[str] = frozenset(
    code for code, exponent in CURRENCY_MINOR_UNIT_EXPONENT.items() if exponent == 0
)

DEFAULT_CURRENCY = "USD"


# =============================================================================
# Results and errors
# =============================================================================


class MetaCredentialsError(Exception):
    """
    Raised when a tenant has no usable read-only Meta credential.

    This is never an error the caller should retry or paper over: it means we
    genuinely cannot know the campaign's numbers, so nothing must be written
    and no success may be reported.

    Attributes:
        reason: Machine-readable reason code, surfaced in the task result.
        message: Operator-facing explanation (never contains a token).
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class MetaCredentials:
    """
    Resolved read-only Meta credential for one tenant/campaign.

    Attributes:
        access_token: Decrypted ``ads_read`` token. Never logged or persisted.
        ad_account_id: Ad account id including the ``act_`` prefix.
        currency: Ad account currency (ISO 4217), best known value.
        connection_id: ``tenant_platform_connection.id`` the token came from.
    """

    access_token: str
    ad_account_id: str
    currency: str
    connection_id: Any

    def __repr__(self) -> str:
        """Render without the token so it cannot reach a log or a traceback."""
        return (
            f"MetaCredentials(ad_account_id={self.ad_account_id!r}, "
            f"currency={self.currency!r}, access_token=***)"
        )


@dataclass(frozen=True)
class MetaIngestionResult:
    """
    Outcome of ingesting one campaign's insights window.

    Attributes:
        campaign_id: Local ``Campaign.id``.
        since: First day pulled (inclusive).
        until: Last day pulled (inclusive).
        rows_fetched: Insight rows Meta returned for this campaign.
        inserted: CampaignMetric rows created.
        updated: CampaignMetric rows updated in place (restatements).
        marked_fresh: Whether ``campaign.last_synced_at`` was advanced. False
            when Meta returned nothing for this campaign, because "no rows"
            is indistinguishable from a stale or wrong ``external_id`` and
            must not be reported to the Trust Engine as fresh data.
    """

    campaign_id: int
    since: date
    until: date
    rows_fetched: int
    inserted: int
    updated: int
    marked_fresh: bool = True

    @property
    def status(self) -> str:
        """``"success"`` when rows were ingested, otherwise ``"no_rows"``."""
        return "success" if self.marked_fresh else "no_rows"


# =============================================================================
# Money
# =============================================================================


def minor_unit_exponent(currency: Optional[str]) -> int:
    """
    Return the ISO 4217 minor-unit exponent for a currency code.

    Args:
        currency: ISO 4217 code (case-insensitive); None falls back to 2.

    Returns:
        0 for zero-decimal currencies (JPY, KRW, ...), 3 for the
        three-decimal ones (BHD, KWD, ...), 2 for everything else.
    """
    if not currency:
        return 2
    return CURRENCY_MINOR_UNIT_EXPONENT.get(currency.strip().upper(), 2)


def to_hundredths(amount: Optional[Decimal], currency: Optional[str]) -> int:
    """
    Convert a major-unit ``Decimal`` into hundredths of the major unit.

    See the module docstring: ``*_cents`` columns in this schema mean
    "major unit x 100" for every currency, because every reader divides by
    100 with no currency awareness. The currency's true precision is still
    honoured - the amount is first quantised at its real exponent so a
    zero-decimal currency never acquires a fraction.

    Args:
        amount: Money in the ad account's major unit (e.g. ``Decimal("12.34")``).
        currency: ISO 4217 code of that amount.

    Returns:
        The amount as an integer number of hundredths, rounded ROUND_HALF_UP.
    """
    if amount is None:
        return 0
    exponent = minor_unit_exponent(currency)
    at_currency_precision = amount.quantize(
        Decimal(1).scaleb(-exponent), rounding=ROUND_HALF_UP
    )
    return int(
        (at_currency_precision * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )


# =============================================================================
# Credential resolution
# =============================================================================


def _status_value(status: Any) -> str:
    """
    Normalise a connection status to its plain string value.

    ``app/api/v1/endpoints/oauth.py`` assigns the ``ConnectionStatus`` member
    itself (not ``.value``) to a ``String`` column, so an object still live in
    the session carries the enum while one read back from the database carries
    ``"connected"``. ``str(ConnectionStatus.CONNECTED)`` is
    ``"ConnectionStatus.CONNECTED"``, which would fail a naive comparison and
    reject a connection that had just completed OAuth.

    Args:
        status: Enum member, string or None.

    Returns:
        The lower-level string value, or "" when unset.
    """
    return str(getattr(status, "value", status) or "")


def resolve_meta_credentials(
    db: Session, tenant_id: int, campaign: Campaign
) -> MetaCredentials:
    """
    Load the tenant's read-only Meta credential for a campaign.

    Args:
        db: Synchronous SQLAlchemy session.
        tenant_id: Tenant that owns the campaign.
        campaign: The campaign being synced (its ``account_id`` is preferred
            over the tenant's default ad account).

    Returns:
        The resolved credential.

    Raises:
        MetaCredentialsError: no Meta connection, the connection is not in a
            usable state, the token is missing/expired/undecryptable, no ad
            account is known, or the campaign carries no ``external_id`` to
            identify its rows with. The ``reason`` attribute names which.
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

    ad_account = db.execute(
        select(TenantAdAccount)
        .where(
            TenantAdAccount.tenant_id == tenant_id,
            TenantAdAccount.platform == META_PLATFORM,
            TenantAdAccount.is_enabled.is_(True),
        )
        .order_by(TenantAdAccount.created_at)
    ).scalars().first()

    account_id = str(getattr(campaign, "account_id", "") or "").strip()
    currency = str(getattr(campaign, "currency", "") or "").strip()

    if ad_account is not None:
        # The campaign's own account_id wins when set - a tenant may have more
        # than one enabled ad account.
        account_id = account_id or str(ad_account.platform_account_id or "").strip()
        currency = currency or str(ad_account.currency or "").strip()

    if not account_id:
        raise MetaCredentialsError(
            "no_ad_account",
            f"Tenant {tenant_id} has no enabled Meta ad account and campaign "
            f"{getattr(campaign, 'id', None)} carries no account_id",
        )

    if not account_id.startswith("act_"):
        account_id = f"act_{account_id}"

    # A blank external_id disables BOTH the server-side campaign filter and
    # the local re-filter, which would attribute every campaign's rows in the
    # ad account to this one campaign and call it a success. `external_id` is
    # NOT NULL but empty strings get through, so refuse here.
    if not str(getattr(campaign, "external_id", "") or "").strip():
        raise MetaCredentialsError(
            "no_external_id",
            f"Campaign {getattr(campaign, 'id', None)} has no Meta external_id, "
            f"so its insight rows cannot be identified in {account_id}",
        )

    return MetaCredentials(
        access_token=access_token,
        ad_account_id=account_id,
        currency=currency or DEFAULT_CURRENCY,
        connection_id=getattr(connection, "id", None),
    )


# =============================================================================
# Fetching (sync bridge)
# =============================================================================


def run_coroutine(coro: Coroutine[Any, Any, T]) -> T:
    """
    Run an awaitable from synchronous code.

    Why this exists: ``MetaInsightsClient`` is async (the house pattern for
    third-party HTTP, matching ``PaddleClient``), while ``sync_campaign_data``
    is a synchronous Celery task on ``SyncSessionLocal``. A sync ``httpx``
    client would have avoided the bridge but would have forked the client from
    every other integration in the codebase, so the bridge is here instead and
    kept to four lines.

    ``asyncio.run`` is used when no loop is running - the normal prefork
    worker case. It builds and tears down a loop (and therefore an httpx
    connection pool) per task, which is the right trade here: one task pulls
    one ad account in a handful of requests, and the pool *is* reused across
    the pagination loop inside a single call. If a loop is already running
    (gevent/eventlet pools, or a caller inside async code), ``asyncio.run``
    would raise, so the coroutine is handed to a private loop on a worker
    thread instead.

    Args:
        coro: The coroutine to run to completion.

    Returns:
        Whatever the coroutine returns.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def insights_window(
    today: Optional[date] = None, lookback_days: Optional[int] = None
) -> tuple[date, date]:
    """
    Compute the ``(since, until)`` window to re-pull.

    Meta restates conversions for days after the fact (attribution windows
    close late), so the last N days are pulled again on every sync and
    upserted rather than assumed final.

    Args:
        today: Day to anchor on (defaults to today's UTC date).
        lookback_days: Window size (defaults to
            ``settings.meta_insights_lookback_days``).

    Returns:
        ``(since, until)``, both inclusive, ending yesterday-inclusive-of-today.
    """
    until = today or datetime.now(UTC).date()
    days = (
        lookback_days
        if lookback_days is not None
        else settings.meta_insights_lookback_days
    )
    return until - timedelta(days=max(days, 1) - 1), until


def fetch_campaign_insight_rows(
    credentials: MetaCredentials,
    since: date,
    until: date,
    *,
    external_campaign_id: Optional[str] = None,
    transport: Any = None,
) -> list[MetaInsightRow]:
    """
    Fetch insight rows for an ad account and optionally narrow to one campaign.

    Args:
        credentials: Resolved read-only Meta credential.
        since: First day of the window (inclusive).
        until: Last day of the window (inclusive).
        external_campaign_id: When given, Meta filters server-side to this
            campaign id and the result is re-checked locally, so a per-campaign
            sync never downloads (or re-parses) the whole ad account.
        transport: Optional ``httpx`` transport, for tests.

    Returns:
        The parsed rows.

    Raises:
        MetaCredentialsError: ``external_campaign_id`` was supplied but blank.
        MetaTokenError, MetaRateLimitError, MetaInsightsTruncatedError,
            MetaAPIError: propagated from the client so the caller can act on
            each case distinctly.
    """

    # Distinguish "no filter wanted" (None) from "filter wanted but blank"
    # (""). Treating a blank id as "no filter" would hand back every campaign
    # in the account under one campaign's name.
    wanted: Optional[str] = None
    if external_campaign_id is not None:
        wanted = str(external_campaign_id).strip()
        if not wanted:
            raise MetaCredentialsError(
                "no_external_id",
                "A blank external campaign id cannot be used to filter "
                "insight rows",
            )

    async def _run() -> list[MetaInsightRow]:
        """Open the client, pull the window, and always close the pool."""
        client = MetaInsightsClient(
            credentials.access_token, transport=transport
        )
        try:
            return await client.get_campaign_insights(
                credentials.ad_account_id,
                since,
                until,
                campaign_ids=[wanted] if wanted else None,
            )
        finally:
            await client.aclose()

    rows = run_coroutine(_run())
    if wanted:
        rows = [row for row in rows if row.campaign_id == wanted]
    return rows


# =============================================================================
# Mapping and persistence
# =============================================================================


def metric_values_from_row(
    row: MetaInsightRow, fallback_currency: Optional[str] = None
) -> dict[str, Any]:
    """
    Convert a ``MetaInsightRow`` into ``CampaignMetric`` column values.

    Args:
        row: Parsed insight row.
        fallback_currency: Currency to use when Meta did not return
            ``account_currency`` on the row.

    Returns:
        A dict of CampaignMetric column names to values (no ids - the caller
        supplies ``tenant_id`` / ``campaign_id``).
    """
    currency = row.currency or fallback_currency or DEFAULT_CURRENCY
    return {
        "date": row.date,
        "impressions": row.impressions,
        "clicks": row.clicks,
        "conversions": row.conversions,
        "spend_cents": to_hundredths(row.spend, currency),
        "revenue_cents": to_hundredths(row.conversion_value, currency),
        "video_views": row.video_views,
        "video_completions": row.video_completions,
    }


def upsert_campaign_metric(
    db: Session, tenant_id: int, campaign_id: int, values: dict[str, Any]
) -> bool:
    """
    Insert or update the ``CampaignMetric`` row for ``(campaign_id, date)``.

    Implemented as select-then-update/insert rather than a Postgres
    ``ON CONFLICT``: ``CampaignMetric`` already declares
    ``UniqueConstraint("campaign_id", "date", name="uq_campaign_metric_date")``,
    so a genuinely concurrent double-insert fails loudly on that constraint
    instead of duplicating, and this form keeps the module dialect-agnostic
    and free of a new Alembic migration (the project ships a single baseline).

    Args:
        db: Synchronous session.
        tenant_id: Owning tenant.
        campaign_id: Local campaign id.
        values: Output of ``metric_values_from_row``.

    Returns:
        True when a new row was inserted, False when an existing row was
        updated in place (a Meta restatement of an already-ingested day).
    """
    row_date = values["date"]
    existing = db.execute(
        select(CampaignMetric).where(
            CampaignMetric.campaign_id == campaign_id,
            CampaignMetric.date == row_date,
        )
    ).scalar_one_or_none()

    if existing is not None:
        for key, value in values.items():
            if key != "date":
                setattr(existing, key, value)
        return False

    db.add(
        CampaignMetric(
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            **values,
        )
    )
    return True


def recompute_campaign_aggregates(db: Session, campaign: Campaign) -> None:
    """
    Refresh the campaign's stored aggregates from its daily metric rows.

    The insights window only covers the last few days, so the aggregates are
    recomputed from every ``CampaignMetric`` row rather than from the window,
    then ``Campaign.calculate_metrics()`` derives ctr/cpc/cpm/cpa/roas.

    Flushes first: the session has ``autoflush=False``, so without an explicit
    flush the SUM runs against the database as it was *before* this window was
    upserted.

    Args:
        db: Synchronous session.
        campaign: The campaign to update in place (not committed here).
    """
    # SyncSessionLocal is built with autoflush=False (app/db/session.py), so
    # the rows just added/updated by upsert_campaign_metric are still pending
    # in the session and this SUM would silently miss them - every aggregate
    # would be one sync behind, and 0 on a first sync.
    db.flush()

    totals = db.execute(
        select(
            func.coalesce(func.sum(CampaignMetric.impressions), 0),
            func.coalesce(func.sum(CampaignMetric.clicks), 0),
            func.coalesce(func.sum(CampaignMetric.conversions), 0),
            func.coalesce(func.sum(CampaignMetric.spend_cents), 0),
            func.coalesce(func.sum(CampaignMetric.revenue_cents), 0),
        ).where(CampaignMetric.campaign_id == campaign.id)
    ).one_or_none()

    if totals is None:
        return

    impressions, clicks, conversions, spend_cents, revenue_cents = totals
    campaign.impressions = int(impressions or 0)
    campaign.clicks = int(clicks or 0)
    campaign.conversions = int(conversions or 0)
    campaign.total_spend_cents = int(spend_cents or 0)
    campaign.revenue_cents = int(revenue_cents or 0)
    campaign.calculate_metrics()


def ingest_campaign_insights(
    db: Session,
    tenant_id: int,
    campaign: Campaign,
    rows: Sequence[MetaInsightRow],
    since: date,
    until: date,
    fallback_currency: Optional[str] = None,
    ad_account_id: Optional[str] = None,
) -> MetaIngestionResult:
    """
    Persist a window of insight rows for one campaign.

    Upserts every row by ``(campaign_id, date)`` and refreshes the campaign
    aggregates. Does **not** commit - the caller owns the transaction so a
    failure rolls the whole window back rather than leaving partial data.

    Freshness is only claimed when Meta actually returned rows. An empty
    response looks identical whether the campaign genuinely had no delivery or
    its ``external_id`` does not exist in this ad account, so it does not
    advance ``last_synced_at`` and does not clear a standing ``sync_error``:
    freshness is 25% of signal health (docs/architecture/trust-engine.md) and
    must never read HEALTHY for data nobody has seen.

    Args:
        db: Synchronous session.
        tenant_id: Owning tenant.
        campaign: The campaign these rows belong to.
        rows: Rows already filtered to this campaign.
        since: First day of the window (inclusive).
        until: Last day of the window (inclusive).
        fallback_currency: Currency used when a row carries no
            ``account_currency``.
        ad_account_id: Account the window was pulled from, used only in the
            ``sync_error`` text when nothing came back.

    Returns:
        A ``MetaIngestionResult`` describing what changed.
    """
    inserted = 0
    updated = 0

    for row in rows:
        values = metric_values_from_row(row, fallback_currency)
        if upsert_campaign_metric(db, tenant_id, campaign.id, values):
            inserted += 1
        else:
            updated += 1

    recompute_campaign_aggregates(db, campaign)

    marked_fresh = bool(rows)
    if marked_fresh:
        campaign.last_synced_at = datetime.now(UTC)
        campaign.sync_error = None
        logger.info(
            "meta_insights_ingested",
            tenant_id=tenant_id,
            campaign_id=campaign.id,
            since=since.isoformat(),
            until=until.isoformat(),
            rows=len(rows),
            inserted=inserted,
            updated=updated,
        )
    else:
        # Deliberately leave last_synced_at alone and record why, so a stale
        # or wrong external_id degrades freshness instead of looking healthy.
        campaign.sync_error = (
            f"Meta returned no insight rows for external_id "
            f"{campaign.external_id} in {ad_account_id or 'the ad account'} "
            f"between {since.isoformat()} and {until.isoformat()} - either no "
            f"delivery in the window, or the campaign is not in this account"
        )
        logger.warning(
            "meta_insights_no_rows",
            tenant_id=tenant_id,
            campaign_id=campaign.id,
            external_id=campaign.external_id,
            ad_account_id=ad_account_id,
            since=since.isoformat(),
            until=until.isoformat(),
        )

    return MetaIngestionResult(
        campaign_id=campaign.id,
        since=since,
        until=until,
        rows_fetched=len(rows),
        inserted=inserted,
        updated=updated,
        marked_fresh=marked_fresh,
    )
