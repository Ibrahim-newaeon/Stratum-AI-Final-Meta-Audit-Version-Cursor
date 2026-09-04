# =============================================================================
# Stratum AI - Data Synchronization Tasks
# =============================================================================
"""
Background tasks for syncing campaign data from ad platforms.

The real path is a READ-ONLY Meta Marketing API pull: ``GET
/act_<id>/insights`` at campaign level with ``time_increment=1``, mapped onto
``CampaignMetric`` rows (see ``app.services.meta``). Nothing in this module
mutates anything on Meta - the autopilot write path is separate and
deliberately unwired.

``USE_MOCK_AD_DATA`` still switches in the local mock generator; it is
rejected outright when ``APP_ENV=production``.

Security: Beat-scheduled tasks use distributed locks to prevent
duplicate execution across multiple Celery workers.
"""

from datetime import UTC, datetime, timedelta

from celery import shared_task
from celery.utils.log import get_task_logger
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SyncSessionLocal
from app.models import (
    Campaign,
    CampaignMetric,
    ConnectionStatus,
    Tenant,
    TenantPlatformConnection,
)
from app.services.meta.insights_client import (
    MetaAPIError,
    MetaInsightsTruncatedError,
    MetaRateLimitError,
    MetaTokenError,
)
from app.services.meta.insights_ingestion import (
    META_PLATFORM,
    MetaCredentialsError,
    fetch_campaign_insight_rows,
    ingest_campaign_insights,
    insights_window,
    resolve_meta_credentials,
)
from app.workers.celery_app import with_distributed_lock
from app.workers.tasks.helpers import publish_event

logger = get_task_logger(__name__)


def _mark_connection_disconnected(db, tenant_id: int, message: str) -> None:
    """
    Flag the tenant's Meta connection as disconnected after a rejected token.

    A token Meta refuses (code 190) cannot succeed on a retry, so the
    connection is taken out of service and the operator-facing error recorded.

    Args:
        db: Synchronous SQLAlchemy session.
        tenant_id: Tenant whose Meta connection was rejected.
        message: Error text to store (never contains the token).
    """
    connection = db.execute(
        select(TenantPlatformConnection).where(
            TenantPlatformConnection.tenant_id == tenant_id,
            TenantPlatformConnection.platform == META_PLATFORM,
        )
    ).scalar_one_or_none()

    if connection is None:
        return

    connection.status = ConnectionStatus.DISCONNECTED.value
    connection.last_error = message[:1000]
    connection.error_count = (connection.error_count or 0) + 1


def _sync_mock_campaign_data(db, tenant_id: int, campaign: Campaign) -> dict:
    """
    Fill CampaignMetric from the local mock generator (development only).

    Args:
        db: Synchronous SQLAlchemy session.
        tenant_id: Owning tenant.
        campaign: Campaign to populate.

    Returns:
        The task result dict.
    """
    from app.services.mock_client import MockAdNetwork

    network = MockAdNetwork(seed=tenant_id)

    end_date = datetime.now(UTC).date()
    start_date = campaign.start_date or (end_date - timedelta(days=30))

    time_series = network.generate_time_series(
        campaign.external_id,
        start_date,
        end_date,
        campaign.__dict__,
    )

    for day_data in time_series:
        existing = db.execute(
            select(CampaignMetric).where(
                CampaignMetric.campaign_id == campaign.id,
                CampaignMetric.date == day_data["date"],
            )
        ).scalar_one_or_none()

        if existing:
            for key, value in day_data.items():
                if key != "date" and hasattr(existing, key):
                    setattr(existing, key, value)
        else:
            metric = CampaignMetric(
                tenant_id=tenant_id,
                campaign_id=campaign.id,
                date=day_data["date"],
                impressions=day_data["impressions"],
                clicks=day_data["clicks"],
                conversions=day_data["conversions"],
                spend_cents=day_data["spend_cents"],
                revenue_cents=day_data["revenue_cents"],
                video_views=day_data.get("video_views"),
                video_completions=day_data.get("video_completions"),
            )
            db.add(metric)

    campaign.calculate_metrics()
    campaign.last_synced_at = datetime.now(UTC)
    campaign.sync_error = None

    db.commit()

    publish_event(
        tenant_id,
        "sync_complete",
        {"campaign_id": campaign.id, "campaign_name": campaign.name},
    )

    logger.info("Campaign %s synced from mock data", campaign.id)
    return {"status": "success", "campaign_id": campaign.id, "source": "mock"}


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=3,
)
def sync_campaign_data(self, tenant_id: int, campaign_id: int):
    """
    Sync one campaign's daily metrics from the Meta Marketing API (read-only).

    Pulls the last ``META_INSIGHTS_LOOKBACK_DAYS`` days of campaign-level
    insights and upserts them by ``(campaign_id, date)``. The window is
    re-pulled every run because Meta restates conversions after the fact, and
    the upsert makes that idempotent.

    Failure behaviour is deliberately honest. ``last_synced_at`` is advanced
    only when Meta actually returned rows for this campaign, so a missing
    credential, a rejected token, a truncated page-capped pull or an empty
    response all leave freshness degrading and say why in the result. Nothing
    is ever written that Stratum did not read from Meta.

    Args:
        tenant_id: Tenant that owns the campaign.
        campaign_id: Local ``Campaign.id``.

    Returns:
        ``{"status": ...}`` describing the outcome.
    """
    logger.info("Syncing campaign %s for tenant %s", campaign_id, tenant_id)

    with SyncSessionLocal() as db:
        campaign = db.execute(
            select(Campaign).where(
                Campaign.id == campaign_id,
                Campaign.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()

        if not campaign:
            logger.warning("Campaign %s not found", campaign_id)
            return {"status": "not_found"}

        if settings.use_mock_ad_data:
            # Mock generator - development and demo only (rejected outright
            # when APP_ENV=production).
            try:
                return _sync_mock_campaign_data(db, tenant_id, campaign)
            except Exception as exc:
                campaign.sync_error = str(exc)
                db.commit()
                raise

        # ------------------------------------------------------- real path
        try:
            credentials = resolve_meta_credentials(db, tenant_id, campaign)
        except MetaCredentialsError as exc:
            # No credential means we cannot know this campaign's numbers.
            # Record why, write nothing, and leave last_synced_at untouched so
            # freshness (and therefore signal health) degrades honestly.
            logger.warning(
                "Cannot sync campaign %s for tenant %s: %s",
                campaign_id,
                tenant_id,
                exc.message,
            )
            campaign.sync_error = exc.message
            db.commit()
            return {
                "status": "skipped",
                "reason": exc.reason,
                "campaign_id": campaign_id,
            }

        since, until = insights_window()

        try:
            rows = fetch_campaign_insight_rows(
                credentials,
                since,
                until,
                external_campaign_id=campaign.external_id,
            )
        except MetaTokenError as exc:
            # The token is dead; retrying with it can only fail again.
            message = f"Meta rejected the access token: {exc.message}"
            logger.error(
                "Meta token rejected for tenant %s (campaign %s): %s",
                tenant_id,
                campaign_id,
                exc.message,
            )
            _mark_connection_disconnected(db, tenant_id, message)
            campaign.sync_error = message
            db.commit()
            return {
                "status": "failed",
                "reason": "token_rejected",
                "campaign_id": campaign_id,
            }
        except MetaRateLimitError as exc:
            # Back off for as long as Meta asked rather than hammering.
            logger.warning(
                "Meta throttled tenant %s (campaign %s), retrying in %ss",
                tenant_id,
                campaign_id,
                exc.retry_after_seconds,
            )
            campaign.sync_error = f"Meta rate limit: {exc.message}"
            db.commit()
            raise self.retry(exc=exc, countdown=exc.retry_after_seconds)
        except MetaInsightsTruncatedError as exc:
            # The page cap stopped the pull while Meta still had pages, so the
            # window is incomplete. Retrying would truncate identically, so
            # record it, write nothing and leave last_synced_at alone rather
            # than putting a partial number in front of the trust gate.
            logger.error(
                "Meta insights pull truncated for tenant %s (campaign %s): %s",
                tenant_id,
                campaign_id,
                exc,
            )
            campaign.sync_error = str(exc)
            db.commit()
            return {
                "status": "failed",
                "reason": "insights_truncated",
                "campaign_id": campaign_id,
            }
        except MetaAPIError as exc:
            # Transient/unknown API failure: record it and let Celery's
            # exponential backoff retry. Nothing partial was written.
            campaign.sync_error = str(exc)
            db.commit()
            raise

        try:
            result = ingest_campaign_insights(
                db,
                tenant_id,
                campaign,
                rows,
                since,
                until,
                fallback_currency=credentials.currency,
                ad_account_id=credentials.ad_account_id,
            )
            db.commit()
        except Exception as exc:
            db.rollback()
            campaign.sync_error = str(exc)
            db.commit()
            raise

        if not result.marked_fresh:
            # Meta answered, but with nothing for this campaign. That is not a
            # successful sync: it is indistinguishable from a stale or wrong
            # external_id, so freshness stays degraded and the status says so.
            logger.warning(
                "Meta returned no insight rows for campaign %s (external id %s, "
                "account %s) between %s and %s - either no delivery in the "
                "window, or the campaign does not belong to this ad account",
                campaign_id,
                campaign.external_id,
                credentials.ad_account_id,
                since,
                until,
            )
            return {
                "status": "no_rows",
                "reason": "no_insight_rows",
                "campaign_id": campaign_id,
                "source": "meta_insights",
                "rows_fetched": 0,
                "since": since.isoformat(),
                "until": until.isoformat(),
            }

        publish_event(
            tenant_id,
            "sync_complete",
            {"campaign_id": campaign_id, "campaign_name": campaign.name},
        )

        logger.info(
            "Campaign %s synced from Meta insights (%s rows, +%s/~%s)",
            campaign_id,
            result.rows_fetched,
            result.inserted,
            result.updated,
        )
        return {
            "status": "success",
            "campaign_id": campaign_id,
            "source": "meta_insights",
            "rows_fetched": result.rows_fetched,
            "inserted": result.inserted,
            "updated": result.updated,
            "since": since.isoformat(),
            "until": until.isoformat(),
        }


@shared_task
@with_distributed_lock(timeout=3600)  # 1 hour lock timeout
def sync_all_campaigns():
    """
    Sync all active campaigns across all tenants.
    Scheduled hourly by Celery beat.

    Uses distributed lock to prevent duplicate execution across workers.
    """
    logger.info("Starting sync for all campaigns")

    with SyncSessionLocal() as db:
        # Select only IDs to avoid loading full ORM objects into memory
        tenant_ids = db.execute(
            select(Tenant.id).where(Tenant.is_deleted == False)
        ).scalars().all()

        task_count = 0
        for tid in tenant_ids:
            campaign_ids = db.execute(
                select(Campaign.id).where(
                    Campaign.tenant_id == tid,
                    Campaign.is_deleted == False,
                )
            ).scalars().all()

            for cid in campaign_ids:
                sync_campaign_data.delay(tid, cid)
                task_count += 1

            db.expire_all()

    logger.info(f"Queued {task_count} campaign sync tasks")
    return {"tasks_queued": task_count}
