"""Seed rich, Meta-only demo data for the demo tenant (slug "demo", id=1).

What gets seeded (all scoped to the demo tenant, platform = Meta only:
Facebook / Instagram / WhatsApp channels):

- Tenant setup: completed onboarding (autopilot mode), a connected Meta platform
  connection and an enabled ad account (needed for the dashboard trust gate).
- 12 campaigns across Facebook / Instagram / WhatsApp with mixed statuses and
  ~30 days of daily CampaignMetric rows (ROAS-consistent numbers).
- Creative assets, automation rules (+ executions) and competitor benchmarks.
- Trust layer: FactSignalHealthDaily (per channel, per day), SignalHealthHistory
  rollups (score >= 70 => Trust Gate PASS), FactAttributionVarianceDaily.
- Autopilot: FactActionsQueue actions (queued / approved / applied / failed=rolled
  back / dismissed) and TrustGateAuditLog decisions.
- Notifications, audit-log activity, WhatsApp contacts / templates / messages.
- CDP: sources, ~200 profiles with identifiers, consents, ~2k events over 30 days,
  identity links / canonical identities / merge history, 3 segments with
  memberships, computed traits, a purchase funnel, and a Meta audience-sync
  credential + 2 platform audiences with sync history.

Idempotent: entity sections are skipped when data already exists; time-series
sections (campaign metrics, signal health, attribution variance) only insert
missing dates, so re-running on a later day extends the series without
duplicates. Every pseudo-random value comes from an RNG seeded on a stable key,
so re-runs produce identical rows.

Usage (from backend/), with the SAME security env as the running API server
(SECRET_KEY salts the PII encryption key used for user email / full_name):
    SECRET_KEY=<api server key> APP_ENV=<api server env> \
    DATABASE_URL=postgresql+asyncpg://stratum:...@127.0.0.1:5432/stratum_ai \
        .venv/bin/python scripts_seed_demo_data.py [--reencrypt-pii]
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (registers all mappers)
from app.base_models import (
    AdPlatform,
    AssetType,
    AuditAction,
    AuditLog,
    Campaign,
    CampaignMetric,
    CampaignStatus,
    CompetitorBenchmark,
    CreativeAsset,
    Rule,
    RuleAction,
    RuleExecution,
    RuleOperator,
    RuleStatus,
    Tenant,
    User,
    WhatsAppContact,
    WhatsAppConversation,
    WhatsAppMessage,
    WhatsAppMessageDirection,
    WhatsAppMessageStatus,
    WhatsAppOptInStatus,
    WhatsAppTemplate,
    WhatsAppTemplateCategory,
    WhatsAppTemplateStatus,
)
from app.core.config import settings
from app.core.security import decrypt_pii, encrypt_pii, hash_pii_for_lookup
from app.models.audience_sync import (
    AudienceSyncCredential,
    AudienceSyncJob,
    PlatformAudience,
)
from app.models.campaign_builder import (
    ConnectionStatus,
    TenantAdAccount,
    TenantPlatformConnection,
)
from app.models.cdp import (
    CDPCanonicalIdentity,
    CDPComputedTrait,
    CDPConsent,
    CDPEvent,
    CDPFunnel,
    CDPFunnelEntry,
    CDPIdentityLink,
    CDPProfile,
    CDPProfileIdentifier,
    CDPProfileMerge,
    CDPSegment,
    CDPSegmentMembership,
    CDPSource,
)
from app.models.onboarding import OnboardingStatus, OnboardingStep, TenantOnboarding
from app.models.settings import Notification, NotificationCategory, NotificationType
from app.models.trust_layer import (
    AttributionVarianceStatus,
    FactActionsQueue,
    FactAttributionVarianceDaily,
    FactSignalHealthDaily,
    SignalHealthHistory,
    SignalHealthStatus,
    TrustGateAuditLog,
)

# =============================================================================
# Constants
# =============================================================================

TENANT_SLUG = "demo"
DEMO_ADMIN_EMAIL = "demo@stratum.ai"
DAYS = 30
AD_ACCOUNT_ID = "act_1234567890"
AD_ACCOUNT_NAME = "Stratum Demo Store"
CHANNELS = ("facebook", "instagram", "whatsapp")
SEED_MARKER = "seed:demo"

NOW = datetime.now(UTC).replace(microsecond=0)
TODAY = NOW.date()
DATES = [TODAY - timedelta(days=i) for i in range(DAYS - 1, -1, -1)]  # oldest -> today


# =============================================================================
# Helpers
# =============================================================================


def rng(*parts: Any) -> random.Random:
    """Deterministic RNG keyed on stable identifiers."""
    return random.Random("stratum-demo|" + "|".join(str(p) for p in parts))


def uid(*parts: Any) -> uuid.UUID:
    """Deterministic UUID (uuid5) keyed on stable identifiers."""
    return uuid.uuid5(
        uuid.NAMESPACE_URL, "stratum-demo/" + "/".join(str(p) for p in parts)
    )


def sha(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def at(d: date, hour: int = 12, minute: int = 0, second: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, second, tzinfo=UTC)


def ago(days: float = 0, hours: float = 0, minutes: float = 0) -> datetime:
    return NOW - timedelta(days=days, hours=hours, minutes=minutes)


def naive(dt: datetime) -> datetime:
    """UTC datetime without tzinfo (for the few naive TIMESTAMP columns)."""
    return dt.astimezone(UTC).replace(tzinfo=None)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def cents(dollars: float) -> int:
    return int(round(dollars * 100))


async def count(db: AsyncSession, model: Any, *where: Any) -> int:
    result = await db.execute(select(func.count()).select_from(model).where(*where))
    return int(result.scalar() or 0)


# =============================================================================
# Campaign catalogue (Meta channels only)
# =============================================================================

# ROAS = (1000 / cpm) * ctr * cvr * aov  -> chosen to give a scale / watch / fix mix
CAMPAIGN_SPECS: list[dict[str, Any]] = [
    dict(
        key="fb-prospecting-lal",
        channel="facebook",
        name="[FB] Prospecting - Lookalike 1% Purchasers",
        objective="OUTCOME_SALES",
        status=CampaignStatus.ACTIVE,
        daily_budget=320.0,
        spend=290.0,
        cpm=9.8,
        ctr=0.0135,
        cvr=0.031,
        aov=86.0,
        trend=0.18,
        start_days_ago=74,
        interests=["Online shopping", "Fashion", "Sustainable products"],
        age=(25, 54),
        genders=["all"],
        placements=["feed", "marketplace", "video_feeds"],
        optimization="OFFSITE_CONVERSIONS",
    ),
    dict(
        key="fb-retargeting-dpa",
        channel="facebook",
        name="[FB] Retargeting - Dynamic Product Ads",
        objective="OUTCOME_SALES",
        status=CampaignStatus.ACTIVE,
        daily_budget=180.0,
        spend=165.0,
        cpm=14.5,
        ctr=0.021,
        cvr=0.045,
        aov=78.0,
        trend=0.06,
        start_days_ago=120,
        interests=[],
        age=(18, 65),
        genders=["all"],
        placements=["feed", "right_column", "marketplace"],
        optimization="OFFSITE_CONVERSIONS",
    ),
    dict(
        key="ig-reels-launch",
        channel="instagram",
        name="[IG] Reels - Spring Collection Launch",
        objective="OUTCOME_SALES",
        status=CampaignStatus.ACTIVE,
        daily_budget=240.0,
        spend=210.0,
        cpm=7.5,
        ctr=0.011,
        cvr=0.024,
        aov=74.0,
        trend=0.25,
        start_days_ago=26,
        interests=["Streetwear", "Fashion influencers", "Spring fashion"],
        age=(18, 34),
        genders=["female", "male"],
        placements=["reels", "explore"],
        optimization="OFFSITE_CONVERSIONS",
        video=True,
    ),
    dict(
        key="ig-stories-ugc",
        channel="instagram",
        name="[IG] Stories - UGC Engagement",
        objective="OUTCOME_ENGAGEMENT",
        status=CampaignStatus.ACTIVE,
        daily_budget=110.0,
        spend=95.0,
        cpm=5.2,
        ctr=0.009,
        cvr=0.012,
        aov=60.0,
        trend=-0.08,
        start_days_ago=48,
        interests=["Lifestyle", "Beauty", "Wellness"],
        age=(18, 44),
        genders=["female"],
        placements=["stories"],
        optimization="POST_ENGAGEMENT",
        video=True,
    ),
    dict(
        key="ig-shopping-catalog",
        channel="instagram",
        name="[IG] Shopping - Catalog Sales",
        objective="OUTCOME_SALES",
        status=CampaignStatus.PAUSED,
        daily_budget=150.0,
        spend=140.0,
        cpm=11.0,
        ctr=0.016,
        cvr=0.02,
        aov=70.0,
        trend=-0.15,
        start_days_ago=63,
        paused_days_ago=6,
        interests=["Online shopping", "Home decor"],
        age=(25, 54),
        genders=["all"],
        placements=["shop", "feed"],
        optimization="OFFSITE_CONVERSIONS",
    ),
    dict(
        key="wa-ctwa-leads",
        channel="whatsapp",
        name="[WA] Click-to-WhatsApp - Lead Gen",
        objective="OUTCOME_LEADS",
        status=CampaignStatus.ACTIVE,
        daily_budget=140.0,
        spend=120.0,
        cpm=8.4,
        ctr=0.018,
        cvr=0.09,
        aov=18.0,
        trend=0.12,
        start_days_ago=60,
        interests=["Small business", "Home services", "Interior design"],
        age=(25, 65),
        genders=["all"],
        placements=["feed", "stories", "reels"],
        optimization="CONVERSATIONS",
    ),
    dict(
        key="wa-abandoned-cart",
        channel="whatsapp",
        name="[WA] Abandoned Cart Recovery",
        objective="OUTCOME_SALES",
        status=CampaignStatus.ACTIVE,
        daily_budget=100.0,
        spend=85.0,
        cpm=12.0,
        ctr=0.022,
        cvr=0.04,
        aov=64.0,
        trend=0.1,
        start_days_ago=40,
        interests=[],
        age=(18, 65),
        genders=["all"],
        placements=["feed", "stories"],
        optimization="CONVERSATIONS",
    ),
    dict(
        key="fb-awareness-video",
        channel="facebook",
        name="[FB] Brand Awareness - Video Views",
        objective="OUTCOME_AWARENESS",
        status=CampaignStatus.PAUSED,
        daily_budget=120.0,
        spend=110.0,
        cpm=4.2,
        ctr=0.006,
        cvr=0.006,
        aov=55.0,
        trend=-0.2,
        start_days_ago=55,
        paused_days_ago=12,
        interests=["Fashion", "Lifestyle"],
        age=(18, 65),
        genders=["all"],
        placements=["video_feeds", "in_stream", "reels"],
        optimization="THRUPLAY",
        video=True,
    ),
    dict(
        key="fb-ramadan-sale",
        channel="facebook",
        name="[FB] Ramadan Mega Sale",
        objective="OUTCOME_SALES",
        status=CampaignStatus.COMPLETED,
        daily_budget=420.0,
        spend=400.0,
        cpm=10.5,
        ctr=0.019,
        cvr=0.035,
        aov=80.0,
        trend=0.3,
        start_days_ago=30,
        ended_days_ago=9,
        interests=["Ramadan", "Gifts", "Online shopping"],
        age=(18, 54),
        genders=["all"],
        placements=["feed", "stories", "reels", "marketplace"],
        optimization="OFFSITE_CONVERSIONS",
    ),
    dict(
        key="ig-influencer-collab",
        channel="instagram",
        name="[IG] Influencer Collab - Q4 Teaser",
        objective="OUTCOME_TRAFFIC",
        status=CampaignStatus.DRAFT,
        daily_budget=200.0,
        spend=0.0,
        cpm=6.0,
        ctr=0.01,
        cvr=0.01,
        aov=70.0,
        trend=0.0,
        start_days_ago=-14,  # scheduled to start in two weeks
        interests=["Fashion influencers", "Beauty"],
        age=(18, 34),
        genders=["all"],
        placements=["reels", "stories"],
        optimization="LINK_CLICKS",
        no_metrics=True,
    ),
    dict(
        key="fb-app-installs",
        channel="facebook",
        name="[FB] App Installs - iOS",
        objective="OUTCOME_APP_PROMOTION",
        status=CampaignStatus.ACTIVE,
        daily_budget=150.0,
        spend=130.0,
        cpm=6.8,
        ctr=0.012,
        cvr=0.15,
        aov=3.2,
        trend=-0.05,
        start_days_ago=35,
        interests=["Mobile shopping", "Fashion apps"],
        age=(18, 44),
        genders=["all"],
        placements=["feed", "reels", "audience_network"],
        optimization="APP_INSTALLS",
    ),
    dict(
        key="wa-loyalty-restock",
        channel="whatsapp",
        name="[WA] Loyalty Members - Restock Alerts",
        objective="OUTCOME_ENGAGEMENT",
        status=CampaignStatus.ACTIVE,
        daily_budget=70.0,
        spend=60.0,
        cpm=9.0,
        ctr=0.024,
        cvr=0.03,
        aov=50.0,
        trend=0.04,
        start_days_ago=90,
        interests=[],
        age=(18, 65),
        genders=["all"],
        placements=["feed", "stories"],
        optimization="CONVERSATIONS",
    ),
]

AGE_SPLIT = {
    "18-24": 0.14,
    "25-34": 0.36,
    "35-44": 0.26,
    "45-54": 0.14,
    "55-64": 0.07,
    "65+": 0.03,
}
GENDER_SPLIT = {"female": 0.56, "male": 0.41, "unknown": 0.03}
LOCATION_SPLIT = {
    "US": 0.34,
    "UK": 0.16,
    "CA": 0.09,
    "DE": 0.08,
    "FR": 0.07,
    "AU": 0.07,
    "ES": 0.05,
    "IT": 0.05,
    "IN": 0.05,
    "BR": 0.04,
}


def campaign_active_dates(spec: dict[str, Any]) -> list[date]:
    """Dates (within the seeded window) on which a campaign spent money."""
    if spec.get("no_metrics"):
        return []
    start = TODAY - timedelta(days=spec["start_days_ago"])
    end = TODAY
    if spec.get("ended_days_ago") is not None:
        end = TODAY - timedelta(days=spec["ended_days_ago"])
    if spec.get("paused_days_ago") is not None:
        end = TODAY - timedelta(days=spec["paused_days_ago"])
    return [d for d in DATES if start <= d <= end]


def daily_metric(spec: dict[str, Any], d: date) -> dict[str, Any]:
    """Deterministic daily metrics for a campaign on a date."""
    r = rng("metric", spec["key"], d.isoformat())
    weekday = d.weekday()
    weekday_factor = 1.0 + (
        0.12 if weekday in (4, 5) else -0.05 if weekday == 0 else 0.0
    )
    # position within the 30-day window drives the trend
    position = (d - DATES[0]).days / max(1, DAYS - 1)
    trend_factor = 1.0 + spec["trend"] * (position - 0.5)
    spend = spec["spend"] * weekday_factor * trend_factor * r.uniform(0.86, 1.14)
    impressions = int(spend / spec["cpm"] * 1000 * r.uniform(0.92, 1.08))
    clicks = int(impressions * spec["ctr"] * r.uniform(0.85, 1.15))
    conversions = int(round(clicks * spec["cvr"] * r.uniform(0.8, 1.2)))
    revenue = conversions * spec["aov"] * r.uniform(0.9, 1.1)
    row: dict[str, Any] = {
        "date": d,
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions,
        "spend_cents": cents(spend),
        "revenue_cents": cents(revenue),
        "shares": int(clicks * r.uniform(0.02, 0.06)),
        "comments": int(clicks * r.uniform(0.01, 0.04)),
        "saves": int(clicks * r.uniform(0.03, 0.09)),
    }
    if spec.get("video"):
        views = int(impressions * r.uniform(0.28, 0.42))
        row["video_views"] = views
        row["video_completions"] = int(views * r.uniform(0.18, 0.33))
    return row


def split_metrics(
    spec: dict[str, Any], totals: dict[str, int], weights: dict[str, float], kind: str
):
    """Split campaign totals across a dimension with per-campaign jitter."""
    r = rng("split", spec["key"], kind)
    jittered = {k: w * r.uniform(0.8, 1.2) for k, w in weights.items()}
    norm = sum(jittered.values())
    out: dict[str, dict[str, int]] = {}
    for k, w in jittered.items():
        share = w / norm
        out[k] = {
            "impressions": int(totals["impressions"] * share),
            "clicks": int(totals["clicks"] * share),
            "conversions": int(totals["conversions"] * share),
            "spend_cents": int(totals["spend_cents"] * share),
        }
    return out


# =============================================================================
# Section: schema compatibility + user PII
# =============================================================================


async def ensure_audience_sync_schema(engine) -> list[str]:
    """Audience-sync tables were created with a UUID tenant_id while every other table
    (and the service layer) uses the integer tenants.id. Convert the column when the
    table is still empty so the models, the service and the DB agree."""
    fixed: list[str] = []
    async with engine.begin() as conn:
        for table in (
            "audience_sync_credentials",
            "platform_audiences",
            "audience_sync_jobs",
        ):
            dtype = (
                await conn.execute(
                    text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_name = :t AND column_name = 'tenant_id'"
                    ),
                    {"t": table},
                )
            ).scalar()
            if dtype != "uuid":
                continue
            rows = (
                await conn.execute(text(f'SELECT count(*) FROM "{table}"'))
            ).scalar() or 0
            if rows:
                print(
                    f"  ! {table}.tenant_id is uuid but table has rows; leaving untouched"
                )
                continue
            await conn.execute(
                text(
                    f'ALTER TABLE "{table}" ALTER COLUMN tenant_id TYPE INTEGER '
                    "USING (tenant_id::text::integer)"
                )
            )
            fixed.append(table)
    return fixed


KNOWN_DEMO_USERS = {
    hash_pii_for_lookup("demo@stratum.ai"): {
        "email": "demo@stratum.ai",
        "full_name": "Demo Admin",
    },
    hash_pii_for_lookup("superadmin@stratum.ai"): {
        "email": "superadmin@stratum.ai",
        "full_name": "Super Admin",
    },
}


def _looks_encrypted(value: str) -> bool:
    # encrypt_pii() base64-encodes a Fernet token ("gAAAAA...") -> "Z0FBQUFB..."
    return value.startswith("Z0FBQUFB") and len(value) > 60


async def fix_user_pii(
    db: AsyncSession, tenant_id: int, reencrypt: bool = False
) -> int:
    """Users created by scripts_seed_demo.py store email/full_name in plaintext, which
    makes every endpoint that decrypts PII (get_current_user -> every dashboard and
    CDP route, /users/me) fail. Encrypt any plaintext PII field in place.

    NOTE: the Fernet key is salted with SECRET_KEY, so run this script with the same
    SECRET_KEY / PII_ENCRYPTION_KEY / PII_ENCRYPTION_SALT as the API server. A value
    that is already encrypted but does not decrypt with the current key is left alone
    (it was written with a different key); pass --reencrypt-pii to rewrite the known
    demo users from their well-known plaintext with the current key."""
    result = await db.execute(select(User).where(User.tenant_id == tenant_id))
    fixed = 0
    for user in result.scalars().all():
        known = KNOWN_DEMO_USERS.get(user.email_hash, {})
        for field in ("email", "full_name", "phone"):
            value = getattr(user, field)
            if not value:
                continue
            try:
                decrypt_pii(value)
                continue
            except Exception:
                pass
            if _looks_encrypted(value):
                plain = known.get(field) if reencrypt else None
                if plain is None:
                    print(
                        f"  ! user {user.id}: {field} is encrypted with a different key "
                        "(run with the API server's SECRET_KEY, or --reencrypt-pii)"
                    )
                    continue
            else:
                plain = value
            setattr(user, field, encrypt_pii(plain))
            fixed += 1
    await db.commit()
    return fixed


# =============================================================================
# Section: tenant setup (onboarding, platform connection, ad account)
# =============================================================================


async def seed_tenant_setup(
    db: AsyncSession, tenant: Tenant, user_id: int
) -> dict[str, int]:
    counts = {
        "tenant_onboarding": 0,
        "tenant_platform_connection": 0,
        "tenant_ad_account": 0,
    }

    onboarding = (
        await db.execute(
            select(TenantOnboarding).where(TenantOnboarding.tenant_id == tenant.id)
        )
    ).scalar_one_or_none()
    if onboarding is None:
        onboarding = TenantOnboarding(
            tenant_id=tenant.id,
            status=OnboardingStatus.COMPLETED.value,
            current_step=OnboardingStep.TRUST_GATE_CONFIG.value,
            completed_steps=[s.value for s in OnboardingStep],
            industry="ecommerce",
            monthly_ad_spend="50k_100k",
            team_size="6_15",
            company_website="https://demo-store.stratum.ai",
            target_markets=["US", "GB", "AE", "SA", "DE"],
            selected_platforms=["meta"],
            primary_kpi="roas",
            target_roas=3.0,
            target_cpa_cents=3500,
            monthly_budget_cents=6_500_000,
            currency="USD",
            timezone="UTC",
            automation_mode="autopilot",
            auto_pause_enabled=True,
            auto_scale_enabled=True,
            notification_email=True,
            notification_slack=True,
            notification_whatsapp=True,
            trust_threshold_autopilot=70,
            trust_threshold_alert=40,
            require_approval_above=50_000,
            max_daily_actions=10,
            additional_preferences={"seed": SEED_MARKER, "channels": list(CHANNELS)},
            started_at=ago(days=45, hours=3),
            completed_at=ago(days=45, hours=1),
            created_at=ago(days=45, hours=3),
            updated_at=ago(days=45, hours=1),
            completed_by_user_id=user_id,
        )
        db.add(onboarding)
        counts["tenant_onboarding"] = 1

    connection = (
        await db.execute(
            select(TenantPlatformConnection).where(
                TenantPlatformConnection.tenant_id == tenant.id,
                TenantPlatformConnection.platform == AdPlatform.META.value,
            )
        )
    ).scalar_one_or_none()
    if connection is None:
        connection = TenantPlatformConnection(
            id=uid("connection", "meta"),
            tenant_id=tenant.id,
            platform=AdPlatform.META.value,
            status=ConnectionStatus.CONNECTED.value,
            token_ref="secrets://demo/meta/system-user-token",
            token_expires_at=ago(days=-58),
            scopes=[
                "ads_management",
                "ads_read",
                "business_management",
                "pages_read_engagement",
                "instagram_basic",
                "whatsapp_business_messaging",
            ],
            granted_by_user_id=user_id,
            connected_at=ago(days=45),
            last_refreshed_at=ago(minutes=7),
            created_at=ago(days=45),
            updated_at=ago(minutes=7),
            error_count=0,
        )
        db.add(connection)
        counts["tenant_platform_connection"] = 1
    elif connection.status != ConnectionStatus.CONNECTED.value:
        connection.status = ConnectionStatus.CONNECTED.value
        connection.last_refreshed_at = ago(minutes=7)

    account = (
        await db.execute(
            select(TenantAdAccount).where(
                TenantAdAccount.tenant_id == tenant.id,
                TenantAdAccount.platform == AdPlatform.META.value,
                TenantAdAccount.platform_account_id == AD_ACCOUNT_ID,
            )
        )
    ).scalar_one_or_none()
    if account is None:
        db.add(
            TenantAdAccount(
                id=uid("ad_account", AD_ACCOUNT_ID),
                tenant_id=tenant.id,
                connection_id=connection.id,
                platform=AdPlatform.META.value,
                platform_account_id=AD_ACCOUNT_ID,
                name=AD_ACCOUNT_NAME,
                business_name="Stratum Demo Retail LLC",
                currency="USD",
                timezone="UTC",
                is_enabled=True,
                daily_budget_cap=Decimal("2500.00"),
                monthly_budget_cap=Decimal("65000.00"),
                permissions_json={
                    "roles": ["ADVERTISE", "ANALYZE"],
                    "tasks": ["MANAGE"],
                },
                account_status="active",
                last_synced_at=ago(minutes=7),
                created_at=ago(days=45),
                updated_at=ago(minutes=7),
            )
        )
        counts["tenant_ad_account"] = 1

    await db.commit()
    return counts


# =============================================================================
# Section: campaigns + daily metrics
# =============================================================================


async def seed_campaigns(
    db: AsyncSession, tenant_id: int
) -> tuple[dict[str, Campaign], dict[str, int]]:
    counts = {"campaigns": 0, "campaign_metrics": 0}

    existing = {
        c.external_id: c
        for c in (
            await db.execute(
                select(Campaign).where(
                    Campaign.tenant_id == tenant_id,
                    Campaign.platform == AdPlatform.META,
                )
            )
        ).scalars()
    }

    campaigns: dict[str, Campaign] = {}
    for idx, spec in enumerate(CAMPAIGN_SPECS, start=1):
        external_id = f"1207{idx:05d}00{rng('ext', spec['key']).randint(1000, 9999)}"
        start_date = TODAY - timedelta(days=spec["start_days_ago"])
        end_date = None
        if spec.get("ended_days_ago") is not None:
            end_date = TODAY - timedelta(days=spec["ended_days_ago"])

        campaign = existing.get(external_id)
        if campaign is None:
            r = rng("campaign", spec["key"])
            campaign = Campaign(
                tenant_id=tenant_id,
                platform=AdPlatform.META,
                external_id=external_id,
                account_id=AD_ACCOUNT_ID,
                name=spec["name"],
                status=spec["status"],
                objective=spec["objective"],
                daily_budget_cents=cents(spec["daily_budget"]),
                lifetime_budget_cents=cents(spec["daily_budget"] * 21)
                if spec["status"] == CampaignStatus.COMPLETED
                else None,
                currency="USD",
                targeting_age_min=spec["age"][0],
                targeting_age_max=spec["age"][1],
                targeting_genders=spec["genders"],
                targeting_locations=[
                    {"country": c, "type": "country"} for c in list(LOCATION_SPLIT)[:6]
                ],
                targeting_interests=spec["interests"],
                start_date=start_date,
                end_date=end_date,
                labels=[
                    spec["channel"],
                    "meta",
                    spec["objective"].replace("OUTCOME_", "").lower(),
                ],
                raw_data={
                    "seed": SEED_MARKER,
                    "channel": spec["channel"],
                    "placements": spec["placements"],
                    "optimization_goal": spec["optimization"],
                    "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                    "adset_count": r.randint(2, 6),
                    "ad_count": r.randint(4, 14),
                    "special_ad_categories": [],
                },
                last_synced_at=ago(minutes=r.randint(4, 40)),
                created_at=at(min(start_date, TODAY), 9, r.randint(0, 59)),
                updated_at=ago(minutes=r.randint(4, 40)),
            )
            db.add(campaign)
            counts["campaigns"] += 1
        campaigns[spec["key"]] = campaign

    await db.flush()  # campaign ids

    for spec in CAMPAIGN_SPECS:
        campaign = campaigns[spec["key"]]
        have = {
            d
            for (d,) in (
                await db.execute(
                    select(CampaignMetric.date).where(
                        CampaignMetric.campaign_id == campaign.id
                    )
                )
            ).all()
        }
        for d in campaign_active_dates(spec):
            if d in have:
                continue
            row = daily_metric(spec, d)
            db.add(CampaignMetric(tenant_id=tenant_id, campaign_id=campaign.id, **row))
            counts["campaign_metrics"] += 1

    await db.flush()

    # Recompute aggregates from the stored time series so they always agree.
    for spec in CAMPAIGN_SPECS:
        campaign = campaigns[spec["key"]]
        agg = (
            await db.execute(
                select(
                    func.coalesce(func.sum(CampaignMetric.impressions), 0),
                    func.coalesce(func.sum(CampaignMetric.clicks), 0),
                    func.coalesce(func.sum(CampaignMetric.conversions), 0),
                    func.coalesce(func.sum(CampaignMetric.spend_cents), 0),
                    func.coalesce(func.sum(CampaignMetric.revenue_cents), 0),
                ).where(CampaignMetric.campaign_id == campaign.id)
            )
        ).one()
        campaign.impressions = int(agg[0])
        campaign.clicks = int(agg[1])
        campaign.conversions = int(agg[2])
        campaign.total_spend_cents = int(agg[3])
        campaign.revenue_cents = int(agg[4])
        campaign.calculate_metrics()
        totals = {
            "impressions": campaign.impressions,
            "clicks": campaign.clicks,
            "conversions": campaign.conversions,
            "spend_cents": campaign.total_spend_cents,
        }
        campaign.demographics_age = split_metrics(spec, totals, AGE_SPLIT, "age")
        campaign.demographics_gender = split_metrics(
            spec, totals, GENDER_SPLIT, "gender"
        )
        campaign.demographics_location = split_metrics(
            spec, totals, LOCATION_SPLIT, "location"
        )

    await db.commit()
    return campaigns, counts


# =============================================================================
# Section: creative assets, rules, competitors
# =============================================================================

ASSET_SPECS = [
    (
        "Spring Reel v2 - Linen Dress",
        AssetType.VIDEO,
        "ig-reels-launch",
        "Spring 2026",
        78.0,
        True,
    ),
    (
        "Spring Reel v1 - Sneakers",
        AssetType.VIDEO,
        "ig-reels-launch",
        "Spring 2026",
        41.0,
        True,
    ),
    (
        "Lookalike Static - Crossbody Bag",
        AssetType.IMAGE,
        "fb-prospecting-lal",
        "Evergreen",
        33.0,
        False,
    ),
    (
        "Lookalike Carousel - Bestsellers",
        AssetType.CAROUSEL,
        "fb-prospecting-lal",
        "Evergreen",
        22.0,
        False,
    ),
    (
        "DPA Template - Product Grid",
        AssetType.CAROUSEL,
        "fb-retargeting-dpa",
        "Evergreen",
        58.0,
        False,
    ),
    (
        "UGC Story - Morning Routine",
        AssetType.STORY,
        "ig-stories-ugc",
        "UGC",
        84.0,
        True,
    ),
    ("UGC Story - Unboxing", AssetType.STORY, "ig-stories-ugc", "UGC", 47.0, True),
    (
        "CTWA Banner - Free Consultation",
        AssetType.IMAGE,
        "wa-ctwa-leads",
        "WhatsApp",
        19.0,
        False,
    ),
    (
        "Cart Recovery - 10% Off",
        AssetType.IMAGE,
        "wa-abandoned-cart",
        "WhatsApp",
        36.0,
        False,
    ),
    ("Brand Film 30s", AssetType.VIDEO, "fb-awareness-video", "Brand", 72.0, True),
    (
        "Ramadan Hero - Gift Sets",
        AssetType.IMAGE,
        "fb-ramadan-sale",
        "Seasonal",
        61.0,
        False,
    ),
    ("App Install Playable", AssetType.HTML5, "fb-app-installs", "App", 28.0, False),
]


async def seed_creative_assets(
    db: AsyncSession, tenant_id: int, campaigns: dict[str, Campaign]
) -> int:
    if await count(db, CreativeAsset, CreativeAsset.tenant_id == tenant_id):
        return 0
    created = 0
    for idx, (name, asset_type, ckey, folder, fatigue, is_video) in enumerate(
        ASSET_SPECS, start=1
    ):
        r = rng("asset", name)
        campaign = campaigns[ckey]
        impressions = int(campaign.impressions * r.uniform(0.12, 0.45))
        clicks = int(impressions * r.uniform(0.006, 0.024))
        ext = (
            "mp4" if is_video else ("html" if asset_type == AssetType.HTML5 else "jpg")
        )
        slug = name.lower().replace(" ", "-").replace("---", "-")
        db.add(
            CreativeAsset(
                tenant_id=tenant_id,
                campaign_id=campaign.id,
                name=name,
                asset_type=asset_type,
                file_url=f"https://cdn.stratum-demo.ai/creatives/{idx:02d}-{slug}.{ext}",
                thumbnail_url=f"https://cdn.stratum-demo.ai/creatives/{idx:02d}-{slug}-thumb.jpg",
                file_size_bytes=r.randint(1_200_000, 48_000_000)
                if is_video
                else r.randint(120_000, 2_400_000),
                file_format=ext,
                width=1080,
                height=1920
                if asset_type in (AssetType.STORY, AssetType.VIDEO)
                else 1080,
                duration_seconds=round(r.uniform(6, 30), 1) if is_video else None,
                tags=[campaign.raw_data["channel"], folder.lower(), asset_type.value],
                folder=folder,
                impressions=impressions,
                clicks=clicks,
                ctr=round(clicks / impressions * 100, 3) if impressions else None,
                fatigue_score=fatigue,
                first_used_at=ago(days=r.randint(8, 60)),
                times_used=r.randint(1, 6),
                ai_description=f"{asset_type.value.title()} creative for {campaign.name} featuring {name.split(' - ')[-1].lower()}.",
                ai_tags=["product", "lifestyle", campaign.raw_data["channel"]],
                brand_safety_score=round(r.uniform(0.9, 0.99), 2),
                created_at=ago(days=r.randint(8, 60)),
                updated_at=ago(hours=r.randint(1, 72)),
            )
        )
        created += 1
    await db.commit()
    return created


async def seed_rules(
    db: AsyncSession, tenant_id: int, campaigns: dict[str, Campaign]
) -> dict[str, int]:
    if await count(db, Rule, Rule.tenant_id == tenant_id):
        return {"rules": 0, "rule_executions": 0}

    specs = [
        dict(
            name="Pause campaigns below breakeven",
            description="Pause any Meta campaign whose 48h ROAS stays below 1.0x.",
            status=RuleStatus.ACTIVE,
            field="roas",
            op=RuleOperator.LESS_THAN,
            value="1.0",
            hours=48,
            action=RuleAction.PAUSE_CAMPAIGN,
            config={"notify": True, "require_trust_gate": True},
            triggers=3,
        ),
        dict(
            name="Alert when CPA exceeds $45",
            description="Send a Slack + WhatsApp alert when 24h CPA is above target.",
            status=RuleStatus.ACTIVE,
            field="cpa",
            op=RuleOperator.GREATER_THAN,
            value="45",
            hours=24,
            action=RuleAction.SEND_ALERT,
            config={"channels": ["slack", "whatsapp"], "severity": "warning"},
            triggers=7,
        ),
        dict(
            name="Scale winners (+20% budget)",
            description="Increase daily budget by 20% when 72h ROAS is above 3.5x (trust-gated).",
            status=RuleStatus.ACTIVE,
            field="roas",
            op=RuleOperator.GREATER_THAN,
            value="3.5",
            hours=72,
            action=RuleAction.ADJUST_BUDGET,
            config={
                "change_pct": 20,
                "max_daily_budget": 500,
                "require_trust_gate": True,
            },
            triggers=5,
        ),
        dict(
            name="Label fatigued creatives",
            description="Apply the 'refresh-creative' label when CTR drops 30% week over week.",
            status=RuleStatus.DRAFT,
            field="ctr",
            op=RuleOperator.LESS_THAN,
            value="0.8",
            hours=168,
            action=RuleAction.APPLY_LABEL,
            config={"label": "refresh-creative"},
            triggers=0,
        ),
    ]
    rules: list[Rule] = []
    for spec in specs:
        r = rng("rule", spec["name"])
        rule = Rule(
            tenant_id=tenant_id,
            name=spec["name"],
            description=spec["description"],
            status=spec["status"],
            condition_field=spec["field"],
            condition_operator=spec["op"],
            condition_value=spec["value"],
            condition_duration_hours=spec["hours"],
            action_type=spec["action"],
            action_config=spec["config"],
            applies_to_campaigns=None,
            applies_to_platforms=["meta"],
            last_evaluated_at=ago(minutes=r.randint(5, 55))
            if spec["status"] == RuleStatus.ACTIVE
            else None,
            last_triggered_at=ago(days=r.randint(1, 6)) if spec["triggers"] else None,
            trigger_count=spec["triggers"],
            cooldown_hours=24,
            created_at=ago(days=r.randint(20, 44)),
            updated_at=ago(days=r.randint(1, 6)),
        )
        db.add(rule)
        rules.append(rule)
    await db.flush()

    executions = [
        (
            rules[0],
            campaigns["fb-awareness-video"],
            12,
            True,
            {"field": "roas", "value": 0.47, "threshold": 1.0},
            {"action": "pause_campaign", "result": "paused"},
        ),
        (
            rules[0],
            campaigns["fb-app-installs"],
            2,
            False,
            {
                "field": "roas",
                "value": 0.85,
                "threshold": 1.0,
                "note": "held: trust gate hold on adset",
            },
            None,
        ),
        (
            rules[1],
            campaigns["fb-app-installs"],
            1,
            True,
            {"field": "cpa", "value": 52.4, "threshold": 45},
            {"action": "send_alert", "channels": ["slack", "whatsapp"]},
        ),
        (
            rules[1],
            campaigns["ig-stories-ugc"],
            3,
            True,
            {"field": "cpa", "value": 48.1, "threshold": 45},
            {"action": "send_alert", "channels": ["slack"]},
        ),
        (
            rules[2],
            campaigns["fb-retargeting-dpa"],
            1,
            True,
            {"field": "roas", "value": 5.1, "threshold": 3.5},
            {
                "action": "adjust_budget",
                "from": 180,
                "to": 216,
                "trust_gate": "execute",
            },
        ),
        (
            rules[2],
            campaigns["wa-abandoned-cart"],
            4,
            True,
            {"field": "roas", "value": 4.6, "threshold": 3.5},
            {
                "action": "adjust_budget",
                "from": 100,
                "to": 120,
                "trust_gate": "execute",
            },
        ),
    ]
    for rule, campaign, days, triggered, cond, action in executions:
        db.add(
            RuleExecution(
                tenant_id=tenant_id,
                rule_id=rule.id,
                campaign_id=campaign.id,
                executed_at=ago(days=days, hours=6),
                triggered=triggered,
                condition_result={**cond, "campaign": campaign.name, "met": triggered},
                action_result=action,
            )
        )
    await db.commit()
    return {"rules": len(rules), "rule_executions": len(executions)}


async def seed_competitors(db: AsyncSession, tenant_id: int) -> int:
    if await count(db, CompetitorBenchmark, CompetitorBenchmark.tenant_id == tenant_id):
        return 0
    specs = [
        ("rivalbrand.com", "RivalBrand", True, 1_250_000, "up", 34.5, 1, 185_000, 142),
        ("shopnova.com", "ShopNova", False, 820_000, "stable", 22.1, 2, 96_000, 88),
        ("urbanfit.co", "UrbanFit", False, 410_000, "down", 12.4, 4, 41_000, 37),
    ]
    for domain, name, primary, traffic, trend, sov, rank, spend, creatives in specs:
        r = rng("competitor", domain)
        history = []
        for m in range(6, 0, -1):
            day = TODAY - timedelta(days=30 * m)
            history.append(
                {
                    "date": day.isoformat(),
                    "estimated_traffic": int(traffic * r.uniform(0.82, 1.05)),
                    "share_of_voice": round(sov * r.uniform(0.85, 1.1), 1),
                    "ad_creatives_count": int(creatives * r.uniform(0.7, 1.0)),
                }
            )
        db.add(
            CompetitorBenchmark(
                tenant_id=tenant_id,
                domain=domain,
                name=name,
                is_primary=primary,
                meta_title=f"{name} - Everyday essentials, delivered",
                meta_description=f"Shop {name} for fashion, home and lifestyle products with free shipping.",
                meta_keywords=["fashion", "home", "lifestyle", name.lower()],
                social_links={
                    "facebook": f"https://facebook.com/{name.lower()}",
                    "instagram": f"https://instagram.com/{name.lower()}",
                    "whatsapp": f"https://wa.me/1555{r.randint(1000000, 9999999)}",
                },
                estimated_traffic=traffic,
                traffic_trend=trend,
                top_keywords=[
                    {
                        "keyword": kw,
                        "volume": r.randint(5_000, 60_000),
                        "position": r.randint(1, 12),
                    }
                    for kw in (
                        "summer dresses",
                        "eco sneakers",
                        "linen shirts",
                        "gift sets",
                        "yoga mat",
                    )
                ],
                paid_keywords_count=r.randint(120, 900),
                organic_keywords_count=r.randint(2_000, 15_000),
                share_of_voice=sov,
                category_rank=rank,
                estimated_ad_spend_cents=spend * 100,
                detected_ad_platforms=["facebook", "instagram"]
                + (["whatsapp"] if primary else []),
                ad_creatives_count=creatives,
                metrics_history=history,
                data_source="scraper",
                last_fetched_at=ago(hours=r.randint(2, 30)),
                created_at=ago(days=40),
                updated_at=ago(hours=r.randint(2, 30)),
            )
        )
    await db.commit()
    return len(specs)


# =============================================================================
# Section: trust layer (signal health, history, attribution variance)
# =============================================================================

CHANNEL_HEALTH = {
    "facebook": {"emq": 94.0, "loss": 2.2, "fresh": 14, "err": 0.6},
    "instagram": {"emq": 92.0, "loss": 2.9, "fresh": 22, "err": 0.9},
    "whatsapp": {"emq": 96.0, "loss": 1.1, "fresh": 9, "err": 0.3},
}
RISK_DAY = TODAY - timedelta(days=9)  # one Instagram "risk" day for a realistic history


def classify_health(
    emq: float, loss: float, fresh: int, err: float
) -> SignalHealthStatus:
    if emq < 70 or loss > 20 or err > 10:
        return SignalHealthStatus.CRITICAL
    if emq < 80 or loss > 10 or fresh > 180 or err > 5:
        return SignalHealthStatus.DEGRADED
    if emq < 90 or loss > 5 or fresh > 60 or err > 2:
        return SignalHealthStatus.RISK
    return SignalHealthStatus.OK


def channel_health(channel: str, d: date) -> dict[str, Any]:
    base = CHANNEL_HEALTH[channel]
    r = rng("health", channel, d.isoformat())
    if channel == "instagram" and d == RISK_DAY:
        emq, loss, fresh, err = 85.0, 6.5, 150, 2.4
        issues = [
            "Instagram CAPI events delayed ~150 min",
            "Event loss 6.5% above 5% threshold",
        ]
        actions = ["Check CAPI gateway health", "Verify pixel/CAPI deduplication keys"]
    else:
        emq = round(clamp(base["emq"] + r.uniform(-2.0, 2.0), 90.5, 99.0), 1)
        loss = round(clamp(base["loss"] + r.uniform(-0.9, 0.9), 0.2, 4.8), 1)
        fresh = int(clamp(base["fresh"] + r.randint(-6, 12), 3, 58))
        err = round(clamp(base["err"] + r.uniform(-0.3, 0.5), 0.05, 1.9), 2)
        issues, actions = [], []
    status = classify_health(emq, loss, fresh, err)
    return {
        "emq": emq,
        "loss": loss,
        "fresh": fresh,
        "err": err,
        "status": status,
        "issues": issues,
        "actions": actions,
    }


def overall_score(rows: list[dict[str, Any]]) -> float:
    emq = sum(r["emq"] for r in rows) / len(rows)
    loss = sum(r["loss"] for r in rows) / len(rows)
    fresh = sum(r["fresh"] for r in rows) / len(rows)
    err = sum(r["err"] for r in rows) / len(rows)
    score = (
        0.55 * emq
        + 0.20 * clamp(100 - 4 * loss, 0, 100)
        + 0.15 * clamp(100 - fresh / 1.8, 0, 100)
        + 0.10 * clamp(100 - 10 * err, 0, 100)
    )
    return round(clamp(score, 0, 100), 1)


async def seed_signal_health(db: AsyncSession, tenant_id: int) -> dict[str, int]:
    counts = {"fact_signal_health_daily": 0, "signal_health_history": 0}
    have_daily = {
        (d, p)
        for d, p in (
            await db.execute(
                select(
                    FactSignalHealthDaily.date, FactSignalHealthDaily.platform
                ).where(FactSignalHealthDaily.tenant_id == tenant_id)
            )
        ).all()
    }
    have_history = {
        d
        for (d,) in (
            await db.execute(
                select(SignalHealthHistory.date).where(
                    SignalHealthHistory.tenant_id == tenant_id
                )
            )
        ).all()
    }

    priority = {
        SignalHealthStatus.OK: 0,
        SignalHealthStatus.RISK: 1,
        SignalHealthStatus.DEGRADED: 2,
        SignalHealthStatus.CRITICAL: 3,
    }
    for d in DATES:
        rows = []
        for channel in CHANNELS:
            h = channel_health(channel, d)
            rows.append(h)
            if (d, channel) in have_daily:
                continue
            db.add(
                FactSignalHealthDaily(
                    id=uid("signal_health", channel, d.isoformat()),
                    tenant_id=tenant_id,
                    date=d,
                    platform=channel,
                    account_id=AD_ACCOUNT_ID,
                    emq_score=h["emq"],
                    event_loss_pct=h["loss"],
                    freshness_minutes=h["fresh"],
                    api_error_rate=h["err"],
                    status=h["status"],
                    notes=f"Meta {channel} signal health rollup ({SEED_MARKER})",
                    issues=json.dumps(h["issues"]),
                    actions=json.dumps(h["actions"]),
                    created_at=at(d, 6, 5),
                    updated_at=at(d, 6, 5),
                )
            )
            counts["fact_signal_health_daily"] += 1

        if d in have_history:
            continue
        worst = max((r["status"] for r in rows), key=lambda s: priority[s])
        score = overall_score(rows)
        db.add(
            SignalHealthHistory(
                id=uid("signal_health_history", d.isoformat()),
                tenant_id=tenant_id,
                date=d,
                overall_score=score,
                emq_score_avg=round(sum(r["emq"] for r in rows) / len(rows), 1),
                event_loss_pct_avg=round(sum(r["loss"] for r in rows) / len(rows), 2),
                freshness_minutes_avg=int(sum(r["fresh"] for r in rows) / len(rows)),
                api_error_rate_avg=round(sum(r["err"] for r in rows) / len(rows), 2),
                platforms_ok=sum(
                    1 for r in rows if r["status"] == SignalHealthStatus.OK
                ),
                platforms_risk=sum(
                    1 for r in rows if r["status"] == SignalHealthStatus.RISK
                ),
                platforms_degraded=sum(
                    1 for r in rows if r["status"] == SignalHealthStatus.DEGRADED
                ),
                platforms_critical=sum(
                    1 for r in rows if r["status"] == SignalHealthStatus.CRITICAL
                ),
                status=worst,
                automation_blocked=1
                if worst in (SignalHealthStatus.DEGRADED, SignalHealthStatus.CRITICAL)
                else 0,
                created_at=at(d, 6, 10),
            )
        )
        counts["signal_health_history"] += 1

    await db.commit()
    return counts


async def seed_attribution_variance(
    db: AsyncSession, tenant_id: int, campaigns: dict[str, Campaign]
) -> int:
    have = {
        (d, p)
        for d, p in (
            await db.execute(
                select(
                    FactAttributionVarianceDaily.date,
                    FactAttributionVarianceDaily.platform,
                ).where(FactAttributionVarianceDaily.tenant_id == tenant_id)
            )
        ).all()
    }
    channel_of = {
        c.id: c.raw_data.get("channel", "facebook") for c in campaigns.values()
    }
    rows = (
        await db.execute(
            select(
                CampaignMetric.campaign_id,
                CampaignMetric.date,
                CampaignMetric.revenue_cents,
                CampaignMetric.conversions,
            ).where(
                CampaignMetric.tenant_id == tenant_id,
                CampaignMetric.campaign_id.in_(list(channel_of)),
                CampaignMetric.date >= DATES[0],
            )
        )
    ).all()
    daily: dict[tuple[date, str], dict[str, float]] = {}
    for campaign_id, d, revenue_cents, conversions in rows:
        key = (d, channel_of[campaign_id])
        agg = daily.setdefault(key, {"revenue": 0.0, "conversions": 0})
        agg["revenue"] += revenue_cents / 100
        agg["conversions"] += conversions

    created = 0
    for d in DATES:
        for channel in CHANNELS:
            if (d, channel) in have:
                continue
            agg = daily.get((d, channel), {"revenue": 0.0, "conversions": 0})
            r = rng("variance", channel, d.isoformat())
            factor = r.uniform(0.86, 0.97)  # platforms over-attribute vs. analytics
            platform_rev = round(agg["revenue"], 2)
            ga4_rev = round(platform_rev * factor, 2)
            platform_conv = int(agg["conversions"])
            ga4_conv = int(round(platform_conv * r.uniform(0.88, 0.98)))
            rev_delta = round(platform_rev - ga4_rev, 2)
            rev_pct = round(rev_delta / ga4_rev * 100, 2) if ga4_rev else 0.0
            conv_delta = platform_conv - ga4_conv
            conv_pct = round(conv_delta / ga4_conv * 100, 2) if ga4_conv else 0.0
            worst = max(abs(rev_pct), abs(conv_pct))
            if worst < 10:
                status = AttributionVarianceStatus.HEALTHY
            elif worst < 20:
                status = AttributionVarianceStatus.MINOR_VARIANCE
            elif worst < 30:
                status = AttributionVarianceStatus.MODERATE_VARIANCE
            else:
                status = AttributionVarianceStatus.HIGH_VARIANCE
            db.add(
                FactAttributionVarianceDaily(
                    id=uid("attribution_variance", channel, d.isoformat()),
                    tenant_id=tenant_id,
                    date=d,
                    platform=channel,
                    ga4_revenue=ga4_rev,
                    platform_revenue=platform_rev,
                    revenue_delta_abs=rev_delta,
                    revenue_delta_pct=rev_pct,
                    ga4_conversions=ga4_conv,
                    platform_conversions=platform_conv,
                    conversion_delta_abs=conv_delta,
                    conversion_delta_pct=conv_pct,
                    confidence=round(r.uniform(0.82, 0.95), 2),
                    status=status,
                    notes=f"Meta {channel} vs GA4 (7-day click / 1-day view)",
                    created_at=at(d, 6, 20),
                    updated_at=at(d, 6, 20),
                )
            )
            created += 1
    await db.commit()
    return created


# =============================================================================
# Section: autopilot actions + trust gate audit log
# =============================================================================

# (days_ago, hour, action_type, campaign_key, status, change_pct, reason)
ACTION_SPECS = [
    (
        0,
        8,
        "budget_increase",
        "fb-prospecting-lal",
        "queued",
        20,
        "ROAS 3.7x above 3.0x target for 5 consecutive days",
    ),
    (
        0,
        8,
        "budget_increase",
        "wa-abandoned-cart",
        "queued",
        15,
        "ROAS 4.7x with stable CPA; headroom in daily cap",
    ),
    (
        0,
        9,
        "budget_decrease",
        "ig-stories-ugc",
        "queued",
        -25,
        "ROAS 1.2x below 1.5x watch threshold for 72h",
    ),
    (
        1,
        7,
        "budget_increase",
        "fb-retargeting-dpa",
        "applied",
        20,
        "Rule 'Scale winners' matched: 72h ROAS 5.1x",
    ),
    (
        1,
        7,
        "budget_increase",
        "wa-ctwa-leads",
        "applied",
        15,
        "Cost per lead 28% under target",
    ),
    (
        2,
        7,
        "pause_campaign",
        "fb-awareness-video",
        "applied",
        0,
        "Rule 'Pause campaigns below breakeven': 48h ROAS 0.47x",
    ),
    (
        2,
        10,
        "budget_decrease",
        "fb-app-installs",
        "approved",
        -20,
        "ROAS 0.85x below breakeven; awaiting execution window",
    ),
    (
        3,
        7,
        "budget_increase",
        "wa-loyalty-restock",
        "applied",
        10,
        "ROAS 4.0x; frequency under 2.0",
    ),
    (
        4,
        7,
        "budget_increase",
        "ig-reels-launch",
        "failed",
        25,
        "Launch momentum: ROAS 2.9x trending up",
    ),
    (
        5,
        7,
        "pause_campaign",
        "ig-shopping-catalog",
        "applied",
        0,
        "Catalog feed errors > 8% for 24h",
    ),
    (
        5,
        11,
        "budget_increase",
        "fb-prospecting-lal",
        "dismissed",
        30,
        "Change exceeds 30% guardrail; dismissed by analyst",
    ),
    (
        6,
        7,
        "enable_campaign",
        "wa-abandoned-cart",
        "applied",
        0,
        "Re-enabled after WhatsApp template re-approval",
    ),
]


async def seed_autopilot(
    db: AsyncSession, tenant_id: int, user_id: int, campaigns: dict[str, Campaign]
) -> dict[str, int]:
    counts = {"fact_actions_queue": 0, "trust_gate_audit_log": 0}
    if await count(db, FactActionsQueue, FactActionsQueue.tenant_id == tenant_id) == 0:
        for idx, (days, hour, action_type, ckey, status, pct, reason) in enumerate(
            ACTION_SPECS, start=1
        ):
            campaign = campaigns[ckey]
            r = rng("action", idx)
            current_budget = (campaign.daily_budget_cents or 0) / 100
            proposed = (
                round(current_budget * (1 + pct / 100), 2) if pct else current_budget
            )
            created_at = at(TODAY - timedelta(days=days), hour, r.randint(0, 59))
            score = round(r.uniform(91.5, 95.5), 1)
            action_json = {
                "action": action_type,
                "entity": campaign.name,
                "channel": campaign.raw_data.get("channel"),
                "current_daily_budget": current_budget,
                "proposed_daily_budget": proposed,
                "change_pct": pct,
                "reason": reason,
                "trust_gate": {
                    "signal_health_score": score,
                    "signal_health_status": "ok",
                    "decision": "execute"
                    if status in ("applied", "failed")
                    else "hold",
                },
                "guardrails": {
                    "max_budget_pct_change": 30,
                    "max_daily_budget_change": 500,
                },
                "generated_by": "autopilot",
            }
            before = {
                "daily_budget": current_budget,
                "status": "active" if "pause" in action_type else campaign.status.value,
            }
            if action_type == "pause_campaign":
                after: dict[str, Any] | None = {
                    "daily_budget": current_budget,
                    "status": "paused",
                }
            elif action_type == "enable_campaign":
                before = {"daily_budget": current_budget, "status": "paused"}
                after = {"daily_budget": current_budget, "status": "active"}
            else:
                after = {"daily_budget": proposed, "status": campaign.status.value}

            action = FactActionsQueue(
                id=uid("action", idx),
                tenant_id=tenant_id,
                date=created_at.date(),
                action_type=action_type,
                entity_type="campaign",
                entity_id=campaign.external_id,
                entity_name=campaign.name,
                platform=AdPlatform.META.value,
                action_json=json.dumps(action_json),
                before_value=json.dumps(before),
                after_value=json.dumps(after)
                if status in ("applied", "failed")
                else None,
                status=status,
                created_by_user_id=None if status != "dismissed" else user_id,
                created_at=created_at,
            )
            if status in ("approved", "applied", "failed"):
                action.approved_by_user_id = user_id if status == "approved" else None
                action.approved_at = created_at + timedelta(minutes=r.randint(2, 40))
            if status in ("applied", "failed"):
                action.applied_by_user_id = None
                action.applied_at = action.approved_at + timedelta(
                    minutes=r.randint(1, 5)
                )
                action.platform_response = json.dumps(
                    {"success": True, "campaign_id": campaign.external_id}
                )
            if status == "failed":
                action.error = (
                    "Rolled back after 6h: ROAS fell 31% below the 7-day baseline "
                    f"(guardrail breach). Daily budget restored to ${current_budget:,.2f}."
                )
                action.platform_response = json.dumps(
                    {
                        "success": True,
                        "rolled_back": True,
                        "restored_daily_budget": current_budget,
                    }
                )
            db.add(action)
            counts["fact_actions_queue"] += 1

    if (
        await count(db, TrustGateAuditLog, TrustGateAuditLog.tenant_id == tenant_id)
        == 0
    ):
        decision_for = {
            "applied": "execute",
            "failed": "execute",
            "approved": "hold",
            "queued": "hold",
            "dismissed": "hold",
        }
        for idx, (days, hour, action_type, ckey, status, pct, reason) in enumerate(
            ACTION_SPECS, start=1
        ):
            campaign = campaigns[ckey]
            r = rng("gate", idx)
            decision = decision_for[status]
            score = round(r.uniform(91.5, 95.5), 1)
            reasons = [f"Signal health {score}% >= healthy threshold (70%)"]
            if decision == "hold":
                reasons.append(
                    "Autopilot level 1 (guarded): budget change requires approval"
                )
            if abs(pct) > 30:
                reasons.append(f"Change of {pct}% exceeds 30% guardrail")
            db.add(
                TrustGateAuditLog(
                    id=uid("gate", idx),
                    tenant_id=tenant_id,
                    decision_type=decision,
                    action_type=action_type,
                    entity_type="campaign",
                    entity_id=campaign.external_id,
                    entity_name=campaign.name,
                    platform=AdPlatform.META.value,
                    signal_health_score=score,
                    signal_health_status="ok",
                    gate_passed=1,
                    gate_reason=json.dumps(reasons),
                    healthy_threshold=70.0,
                    degraded_threshold=40.0,
                    is_dry_run=0,
                    action_payload=json.dumps(
                        {"action": action_type, "change_pct": pct, "reason": reason}
                    ),
                    action_result=json.dumps({"status": status}),
                    triggered_by_user_id=user_id if status == "dismissed" else None,
                    triggered_by_system=0 if status == "dismissed" else 1,
                    created_at=at(TODAY - timedelta(days=days), hour, r.randint(0, 59))
                    - timedelta(seconds=30),
                )
            )
            counts["trust_gate_audit_log"] += 1
        # Two dry-run simulations from the UI
        for n, (days, ckey, decision, reasons) in enumerate(
            [
                (
                    0,
                    "ig-reels-launch",
                    "execute",
                    ["Signal health 93.8% >= healthy threshold (70%)"],
                ),
                (
                    3,
                    "fb-app-installs",
                    "hold",
                    [
                        "Signal health 92.4% >= healthy threshold (70%)",
                        "Autopilot check: pause requires approval at level 1",
                    ],
                ),
            ],
            start=1,
        ):
            campaign = campaigns[ckey]
            db.add(
                TrustGateAuditLog(
                    id=uid("gate-dry", n),
                    tenant_id=tenant_id,
                    decision_type=decision,
                    action_type="budget_increase"
                    if decision == "execute"
                    else "pause_campaign",
                    entity_type="campaign",
                    entity_id=campaign.external_id,
                    entity_name=campaign.name,
                    platform=AdPlatform.META.value,
                    signal_health_score=93.8 if decision == "execute" else 92.4,
                    signal_health_status="ok",
                    gate_passed=1,
                    gate_reason=json.dumps(reasons),
                    healthy_threshold=70.0,
                    degraded_threshold=40.0,
                    is_dry_run=1,
                    action_payload=json.dumps({"change_pct": 20, "simulated": True}),
                    triggered_by_user_id=user_id,
                    triggered_by_system=0,
                    created_at=at(TODAY - timedelta(days=days), 14, 30),
                )
            )
            counts["trust_gate_audit_log"] += 1

    await db.commit()
    return counts


# =============================================================================
# Section: notifications + audit log activity
# =============================================================================


async def seed_notifications(db: AsyncSession, tenant_id: int, user_id: int) -> int:
    if await count(db, Notification, Notification.tenant_id == tenant_id):
        return 0
    specs = [
        (
            NotificationType.SUCCESS,
            NotificationCategory.TRUST_GATE,
            "Trust Gate: PASS - autopilot enabled",
            "Signal health is 93.6 (EMQ 94%, event loss 2.1%). Automations execute automatically.",
            0,
            2,
            False,
            "/dashboard/data-quality",
            "View signal health",
            None,
        ),
        (
            NotificationType.INFO,
            NotificationCategory.INTEGRATION,
            "Audience synced to Meta",
            "'High-Value Customers' pushed to Meta Custom Audiences - 71% match rate (1,240 matched).",
            0,
            6,
            False,
            "/dashboard/cdp/audience-sync",
            "Open audience sync",
            None,
        ),
        (
            NotificationType.WARNING,
            NotificationCategory.CAMPAIGN,
            "Creative fatigue detected",
            "'UGC Story - Morning Routine' reached fatigue score 84. Consider rotating creatives.",
            0,
            11,
            False,
            "/dashboard/assets",
            "Review creatives",
            user_id,
        ),
        (
            NotificationType.INFO,
            NotificationCategory.CAMPAIGN,
            "Budget increased: [FB] Retargeting - DPA",
            "Autopilot raised the daily budget by 20% (ROAS 5.1x over 72h).",
            1,
            7,
            True,
            "/dashboard/campaigns",
            "View campaign",
            None,
        ),
        (
            NotificationType.SUCCESS,
            NotificationCategory.CAMPAIGN,
            "[WA] Abandoned Cart Recovery hit 4.7x ROAS",
            "7-day ROAS is 4.7x with 138 recovered orders.",
            2,
            9,
            True,
            "/dashboard/campaigns",
            "View campaign",
            None,
        ),
        (
            NotificationType.ERROR,
            NotificationCategory.TRUST_GATE,
            "Budget change rolled back",
            "The +25% change on [IG] Reels - Spring Collection Launch was rolled back after a guardrail breach.",
            4,
            13,
            True,
            "/dashboard/autopilot",
            "Open autopilot",
            None,
        ),
        (
            NotificationType.WARNING,
            NotificationCategory.SYSTEM,
            "Weekly signal health report ready",
            "Average signal health 92.8 over the last 7 days; one Instagram risk day.",
            6,
            8,
            True,
            "/dashboard/data-quality",
            "Open report",
            None,
        ),
        (
            NotificationType.ALERT,
            NotificationCategory.TRUST_GATE,
            "Instagram CAPI freshness degraded",
            "Instagram events were 150 min stale; automation was held for the day.",
            9,
            10,
            True,
            "/dashboard/data-quality",
            "View details",
            None,
        ),
    ]
    for ntype, category, title, message, days, hour, is_read, url, label, uid_ in specs:
        created_at = at(TODAY - timedelta(days=days), hour, 15)
        db.add(
            Notification(
                tenant_id=tenant_id,
                user_id=uid_,
                title=title,
                message=message,
                type=ntype,
                category=category,
                is_read=is_read,
                read_at=created_at + timedelta(hours=3) if is_read else None,
                action_url=url,
                action_label=label,
                extra_data={"seed": SEED_MARKER, "platform": "meta"},
                created_at=created_at,
                updated_at=created_at,
            )
        )
    await db.commit()
    return len(specs)


async def seed_audit_logs(
    db: AsyncSession, tenant_id: int, user_id: int, campaigns: dict[str, Campaign]
) -> int:
    if await count(
        db,
        AuditLog,
        AuditLog.tenant_id == tenant_id,
        AuditLog.request_id.like("seed-%"),
    ):
        return 0
    specs = [
        (
            0,
            8,
            AuditAction.UPDATE,
            "campaign",
            campaigns["fb-prospecting-lal"],
            {"daily_budget_cents": 32000},
            {"daily_budget_cents": 38400},
            "POST",
            "/api/v1/autopilot/tenant/1/autopilot/actions",
            None,
        ),
        (
            0,
            7,
            AuditAction.CREATE,
            "audience_sync",
            None,
            None,
            {"audience": "High-Value Customers", "platform": "meta"},
            "POST",
            "/api/v1/cdp/audience-sync/audiences",
            user_id,
        ),
        (
            1,
            7,
            AuditAction.UPDATE,
            "campaign",
            campaigns["fb-retargeting-dpa"],
            {"daily_budget_cents": 18000},
            {"daily_budget_cents": 21600},
            "POST",
            "/api/v1/autopilot/tenant/1/autopilot/actions",
            None,
        ),
        (
            1,
            10,
            AuditAction.EXPORT,
            "dashboard",
            None,
            None,
            {"format": "csv", "period": "30d"},
            "POST",
            "/api/v1/dashboard/export",
            user_id,
        ),
        (
            2,
            7,
            AuditAction.UPDATE,
            "campaign",
            campaigns["fb-awareness-video"],
            {"status": "active"},
            {"status": "paused"},
            "POST",
            "/api/v1/rules/1/execute",
            None,
        ),
        (
            2,
            12,
            AuditAction.CREATE,
            "segment",
            None,
            None,
            {"name": "Cart Abandoners (30d)"},
            "POST",
            "/api/v1/cdp/segments",
            user_id,
        ),
        (
            3,
            9,
            AuditAction.UPDATE,
            "rule",
            None,
            {"status": "draft"},
            {"status": "active"},
            "PATCH",
            "/api/v1/rules/3",
            user_id,
        ),
        (
            3,
            15,
            AuditAction.CREATE,
            "creative_asset",
            None,
            None,
            {"name": "Spring Reel v2 - Linen Dress"},
            "POST",
            "/api/v1/assets/upload",
            user_id,
        ),
        (
            4,
            7,
            AuditAction.UPDATE,
            "campaign",
            campaigns["ig-reels-launch"],
            {"daily_budget_cents": 24000},
            {"daily_budget_cents": 30000},
            "POST",
            "/api/v1/autopilot/tenant/1/autopilot/actions",
            None,
        ),
        (
            4,
            13,
            AuditAction.UPDATE,
            "campaign",
            campaigns["ig-reels-launch"],
            {"daily_budget_cents": 30000},
            {"daily_budget_cents": 24000, "rollback": True},
            "POST",
            "/api/v1/autopilot/tenant/1/autopilot/actions/rollback",
            None,
        ),
        (
            5,
            7,
            AuditAction.UPDATE,
            "campaign",
            campaigns["ig-shopping-catalog"],
            {"status": "active"},
            {"status": "paused"},
            "POST",
            "/api/v1/autopilot/tenant/1/autopilot/actions",
            None,
        ),
        (
            5,
            16,
            AuditAction.CREATE,
            "whatsapp_template",
            None,
            None,
            {"name": "spring_sale_promo"},
            "POST",
            "/api/v1/whatsapp/templates",
            user_id,
        ),
        (
            6,
            7,
            AuditAction.UPDATE,
            "campaign",
            campaigns["wa-abandoned-cart"],
            {"status": "paused"},
            {"status": "active"},
            "POST",
            "/api/v1/autopilot/tenant/1/autopilot/actions",
            None,
        ),
        (
            6,
            11,
            AuditAction.CREATE,
            "rule",
            None,
            None,
            {"name": "Scale winners (+20% budget)"},
            "POST",
            "/api/v1/rules",
            user_id,
        ),
        (
            7,
            9,
            AuditAction.CREATE,
            "campaign",
            campaigns["ig-influencer-collab"],
            None,
            {"name": campaigns["ig-influencer-collab"].name, "status": "draft"},
            "POST",
            "/api/v1/campaigns",
            user_id,
        ),
        (
            8,
            14,
            AuditAction.EXPORT,
            "cdp_audience",
            None,
            None,
            {"format": "csv", "segment": "WhatsApp Engaged Leads"},
            "POST",
            "/api/v1/cdp/audiences/export",
            user_id,
        ),
    ]
    for idx, (
        days,
        hour,
        action,
        rtype,
        campaign,
        old,
        new,
        method,
        endpoint,
        uid_,
    ) in enumerate(specs, start=1):
        db.add(
            AuditLog(
                tenant_id=tenant_id,
                user_id=uid_,
                action=action,
                resource_type=rtype,
                resource_id=str(campaign.id) if campaign is not None else None,
                old_value=old,
                new_value=new,
                changed_fields=sorted(set((old or {}) | (new or {})))
                if (old or new)
                else None,
                ip_address="10.20.0.14" if uid_ else None,
                user_agent="Mozilla/5.0 (Macintosh) StratumApp/1.0"
                if uid_
                else "stratum-autopilot/1.0",
                request_id=f"seed-{idx:03d}",
                endpoint=endpoint,
                http_method=method,
                created_at=at(
                    TODAY - timedelta(days=days), hour, rng("audit", idx).randint(0, 59)
                ),
            )
        )
    await db.commit()
    return len(specs)


# =============================================================================
# Section: WhatsApp (Meta channel)
# =============================================================================

WA_CONTACTS = [
    ("+971501234567", "971", "Sara Haddad", WhatsAppOptInStatus.OPTED_IN, "web_form"),
    ("+971559876543", "971", "Omar Mansour", WhatsAppOptInStatus.OPTED_IN, "qr_code"),
    (
        "+966541112233",
        "966",
        "Layla Al Farsi",
        WhatsAppOptInStatus.OPTED_IN,
        "web_form",
    ),
    ("+966505556677", "966", "Yousef Nasser", WhatsAppOptInStatus.OPTED_IN, "ctwa_ad"),
    ("+201001234567", "20", "Noor Ibrahim", WhatsAppOptInStatus.OPTED_IN, "ctwa_ad"),
    ("+447700900123", "44", "Emma Brown", WhatsAppOptInStatus.OPTED_IN, "web_form"),
    ("+447700900456", "44", "Liam Smith", WhatsAppOptInStatus.OPTED_IN, "checkout"),
    ("+12025550143", "1", "Olivia Johnson", WhatsAppOptInStatus.OPTED_IN, "checkout"),
    ("+12025550178", "1", "Noah Garcia", WhatsAppOptInStatus.OPTED_IN, "ctwa_ad"),
    ("+4915112345678", "49", "Mia Mueller", WhatsAppOptInStatus.PENDING, "web_form"),
    ("+971502223344", "971", "Khalid Saleh", WhatsAppOptInStatus.PENDING, "qr_code"),
    ("+962790001122", "962", "Dana Karim", WhatsAppOptInStatus.OPTED_OUT, "web_form"),
]

WA_TEMPLATES = [
    (
        "order_confirmation",
        WhatsAppTemplateCategory.UTILITY,
        WhatsAppTemplateStatus.APPROVED,
        "Hi {{1}}, your order {{2}} is confirmed! Estimated delivery: {{3}}. Track it here: {{4}}",
        412,
    ),
    (
        "restock_alert",
        WhatsAppTemplateCategory.MARKETING,
        WhatsAppTemplateStatus.APPROVED,
        "Good news {{1}} - {{2}} is back in stock. Loyalty members get 10% off for 48h with code {{3}}.",
        186,
    ),
    (
        "cart_recovery",
        WhatsAppTemplateCategory.MARKETING,
        WhatsAppTemplateStatus.APPROVED,
        "{{1}}, you left {{2}} in your cart. Complete your order in the next 24h and shipping is on us: {{3}}",
        264,
    ),
    (
        "login_otp",
        WhatsAppTemplateCategory.AUTHENTICATION,
        WhatsAppTemplateStatus.APPROVED,
        "{{1}} is your Stratum Demo Store verification code. It expires in 10 minutes.",
        933,
    ),
    (
        "spring_sale_promo",
        WhatsAppTemplateCategory.MARKETING,
        WhatsAppTemplateStatus.PENDING,
        "Spring is here, {{1}}! Enjoy up to 30% off the new collection until {{2}}.",
        0,
    ),
]


async def seed_whatsapp(
    db: AsyncSession, tenant_id: int, user_id: int
) -> dict[str, int]:
    counts = {
        "whatsapp_templates": 0,
        "whatsapp_contacts": 0,
        "whatsapp_messages": 0,
        "whatsapp_conversations": 0,
    }
    if await count(db, WhatsAppContact, WhatsAppContact.tenant_id == tenant_id):
        return counts

    templates: dict[str, WhatsAppTemplate] = {}
    for name, category, status, body, usage in WA_TEMPLATES:
        r = rng("wa_template", name)
        tpl = WhatsAppTemplate(
            tenant_id=tenant_id,
            name=name,
            language="en",
            category=category,
            header_type="TEXT"
            if category != WhatsAppTemplateCategory.AUTHENTICATION
            else None,
            header_content="Stratum Demo Store"
            if category != WhatsAppTemplateCategory.AUTHENTICATION
            else None,
            body_text=body,
            footer_text="Reply STOP to opt out"
            if category == WhatsAppTemplateCategory.MARKETING
            else None,
            body_variables=[f"{{{{{i}}}}}" for i in range(1, body.count("{{") + 1)],
            buttons=[
                {"type": "URL", "text": "Open", "url": "https://demo-store.stratum.ai"}
            ]
            if category == WhatsAppTemplateCategory.MARKETING
            else [],
            meta_template_id=str(r.randint(10**14, 10**15 - 1))
            if status == WhatsAppTemplateStatus.APPROVED
            else None,
            status=status,
            usage_count=usage,
            last_used_at=ago(hours=r.randint(1, 48)) if usage else None,
            created_at=ago(days=r.randint(20, 60)),
            updated_at=ago(days=r.randint(1, 5)),
        )
        db.add(tpl)
        templates[name] = tpl
        counts["whatsapp_templates"] += 1

    contacts: list[WhatsAppContact] = []
    for phone, cc, name, opt_status, method in WA_CONTACTS:
        r = rng("wa_contact", phone)
        opted_in = opt_status == WhatsAppOptInStatus.OPTED_IN
        contact = WhatsAppContact(
            tenant_id=tenant_id,
            user_id=None,
            phone_number=phone,
            country_code=cc,
            display_name=name,
            is_verified=opted_in,
            verified_at=ago(days=r.randint(5, 40)) if opted_in else None,
            opt_in_status=opt_status,
            opt_in_at=ago(days=r.randint(5, 40)) if opted_in else None,
            opt_out_at=ago(days=3)
            if opt_status == WhatsAppOptInStatus.OPTED_OUT
            else None,
            opt_in_method=method,
            wa_id=phone.lstrip("+"),
            profile_name=name.split()[0],
            notification_types=["alerts", "reports", "digests"],
            timezone="Asia/Dubai" if cc in ("971", "966", "962") else "UTC",
            language="en",
            is_active=True,
            last_message_at=ago(hours=r.randint(1, 96)) if opted_in else None,
            message_count=0,
            created_at=ago(days=r.randint(5, 45)),
            updated_at=ago(hours=r.randint(1, 96)),
        )
        db.add(contact)
        contacts.append(contact)
        counts["whatsapp_contacts"] += 1
    await db.flush()

    opted = [c for c in contacts if c.opt_in_status == WhatsAppOptInStatus.OPTED_IN]
    inbound_texts = [
        "Hi! Is the linen dress available in M?",
        "Thanks, order received.",
        "Can I change my delivery address?",
        "What is the return policy?",
        "Please stop the restock alerts for shoes.",
        "Love the new collection!",
    ]
    idx = 0
    for contact in opted:
        r = rng("wa_messages", contact.phone_number)
        for n in range(r.randint(3, 5)):
            idx += 1
            created = ago(days=r.uniform(0.2, 12))
            inbound = n % 3 == 2
            if inbound:
                msg = WhatsAppMessage(
                    tenant_id=tenant_id,
                    contact_id=contact.id,
                    direction=WhatsAppMessageDirection.INBOUND,
                    message_type="text",
                    content=r.choice(inbound_texts),
                    wamid=f"wamid.HBgN{uid('wamid', idx).hex[:22]}",
                    recipient_wa_id=None,
                    status=WhatsAppMessageStatus.READ,
                    status_history=[{"status": "read", "at": created.isoformat()}],
                    created_at=created,
                )
            else:
                tpl_name = r.choice(
                    ["order_confirmation", "restock_alert", "cart_recovery"]
                )
                tpl = templates[tpl_name]
                failed = idx % 11 == 0
                status = (
                    WhatsAppMessageStatus.FAILED
                    if failed
                    else r.choice(
                        [
                            WhatsAppMessageStatus.READ,
                            WhatsAppMessageStatus.READ,
                            WhatsAppMessageStatus.DELIVERED,
                            WhatsAppMessageStatus.SENT,
                        ]
                    )
                )
                sent_at = created + timedelta(seconds=2)
                delivered_at = (
                    sent_at + timedelta(seconds=r.randint(3, 60))
                    if status
                    in (WhatsAppMessageStatus.DELIVERED, WhatsAppMessageStatus.READ)
                    else None
                )
                read_at = (
                    delivered_at + timedelta(minutes=r.randint(1, 240))
                    if status == WhatsAppMessageStatus.READ and delivered_at
                    else None
                )
                history = [{"status": "sent", "at": sent_at.isoformat()}]
                if delivered_at:
                    history.append(
                        {"status": "delivered", "at": delivered_at.isoformat()}
                    )
                if read_at:
                    history.append({"status": "read", "at": read_at.isoformat()})
                if failed:
                    history.append(
                        {
                            "status": "failed",
                            "at": sent_at.isoformat(),
                            "code": "131047",
                        }
                    )
                msg = WhatsAppMessage(
                    tenant_id=tenant_id,
                    contact_id=contact.id,
                    template_id=tpl.id,
                    direction=WhatsAppMessageDirection.OUTBOUND,
                    message_type="template",
                    template_name=tpl_name,
                    template_variables={
                        "1": contact.profile_name,
                        "2": r.choice(
                            ["ORD-48213", "Linen Summer Dress", "Everyday Sneakers"]
                        ),
                    },
                    content=None,
                    wamid=f"wamid.HBgN{uid('wamid', idx).hex[:22]}",
                    recipient_wa_id=contact.wa_id,
                    status=status,
                    status_history=history,
                    error_code="131047" if failed else None,
                    error_message="Re-engagement message: 24h customer service window expired"
                    if failed
                    else None,
                    sent_at=None if failed else sent_at,
                    delivered_at=delivered_at,
                    read_at=read_at,
                    created_at=created,
                )
            db.add(msg)
            contact.message_count += 1
            counts["whatsapp_messages"] += 1

    for n, contact in enumerate(opted[:6]):
        r = rng("wa_conv", contact.phone_number)
        started = ago(days=r.uniform(0.1, 6))
        db.add(
            WhatsAppConversation(
                tenant_id=tenant_id,
                contact_id=contact.id,
                conversation_id=f"conv_{uid('conv', contact.phone_number).hex[:16]}",
                origin_type="user_initiated" if n % 2 else "business_initiated",
                pricing_category="service"
                if n % 2
                else r.choice(["marketing", "utility"]),
                started_at=started,
                expires_at=started + timedelta(hours=24),
                message_count=r.randint(2, 6),
                is_active=started + timedelta(hours=24) > NOW,
                created_at=started,
            )
        )
        counts["whatsapp_conversations"] += 1

    await db.commit()
    return counts


# =============================================================================
# Section: CDP
# =============================================================================

FIRST_NAMES = [
    "Sara",
    "Omar",
    "Layla",
    "Yousef",
    "Noor",
    "Ahmed",
    "Mariam",
    "Khalid",
    "Hana",
    "Ali",
    "Emma",
    "Liam",
    "Olivia",
    "Noah",
    "Ava",
    "James",
    "Sophia",
    "Lucas",
    "Mia",
    "Ethan",
    "Zainab",
    "Faisal",
    "Dana",
    "Rashid",
    "Aisha",
]
LAST_NAMES = [
    "Haddad",
    "Al Farsi",
    "Khan",
    "Mansour",
    "Saleh",
    "Nasser",
    "Smith",
    "Johnson",
    "Brown",
    "Garcia",
    "Martin",
    "Rossi",
    "Mueller",
    "Silva",
    "Patel",
    "Rahman",
    "Hassan",
    "Ibrahim",
    "Youssef",
    "Karim",
]
CITIES = [
    ("Dubai", "AE"),
    ("Abu Dhabi", "AE"),
    ("Riyadh", "SA"),
    ("Jeddah", "SA"),
    ("Cairo", "EG"),
    ("London", "GB"),
    ("Manchester", "GB"),
    ("New York", "US"),
    ("Austin", "US"),
    ("Berlin", "DE"),
    ("Toronto", "CA"),
    ("Amman", "JO"),
]
PRODUCTS = [
    ("SKU-1001", "Linen Summer Dress", 79.0),
    ("SKU-1002", "Everyday Sneakers", 95.0),
    ("SKU-1003", "Organic Cotton Tee", 29.0),
    ("SKU-1004", "Leather Crossbody Bag", 149.0),
    ("SKU-1005", "Wireless Earbuds Pro", 129.0),
    ("SKU-1006", "Smart Water Bottle", 39.0),
    ("SKU-1007", "Yoga Mat Eco", 49.0),
    ("SKU-1008", "Denim Jacket", 119.0),
    ("SKU-1009", "Scented Candle Set", 35.0),
    ("SKU-1010", "Running Shorts", 42.0),
]
ACQ_CHANNELS = ["facebook", "instagram", "whatsapp", "organic"]
RFM_SEGMENTS = [
    "Champions",
    "Loyal Customers",
    "Potential Loyalists",
    "New Customers",
    "At Risk",
    "Hibernating",
    "Need Attention",
]
PROFILE_COUNT = 200


def lifecycle_for(i: int) -> str:
    if i < 60:
        return "anonymous"
    if i < 130:
        return "known"
    if i < 190:
        return "customer"
    return "churned"


def build_profile_plan(i: int, sources: dict[str, uuid.UUID]) -> dict[str, Any]:
    """Plan a profile, its identifiers and its events deterministically."""
    r = rng("profile", i)
    stage = lifecycle_for(i)
    first = r.choice(FIRST_NAMES)
    last = r.choice(LAST_NAMES)
    city, country = r.choice(CITIES)
    channel = r.choices(ACQ_CHANNELS, weights=[0.38, 0.3, 0.2, 0.12])[0]
    device = r.choices(["mobile", "desktop", "tablet"], weights=[0.68, 0.26, 0.06])[0]
    profile_id = uid("profile", i)

    identifiers: list[dict[str, Any]] = []
    if stage == "anonymous" or r.random() < 0.85:
        identifiers.append(
            {
                "type": "anonymous_id",
                "value": f"anon_{uid('anon', i).hex[:20]}",
                "primary": stage == "anonymous",
            }
        )
    if r.random() < 0.45:
        identifiers.append(
            {
                "type": "device_id",
                "value": f"idfv-{uid('device', i).hex[:16].upper()}",
                "primary": False,
            }
        )
    email = phone = None
    if stage != "anonymous":
        email = f"{first}.{last.replace(' ', '')}{i}@example.com".lower()
        identifiers.append({"type": "email", "value": email, "primary": True})
        if r.random() < 0.55:
            cc = {
                "AE": "+9715",
                "SA": "+9665",
                "EG": "+2010",
                "GB": "+4477",
                "US": "+1202",
                "DE": "+4915",
                "CA": "+1416",
                "JO": "+9627",
            }[country]
            phone = f"{cc}{1000000 + (i * 7919) % 8999999:07d}"  # unique per profile
            identifiers.append({"type": "phone", "value": phone, "primary": False})
    external_id = f"cust_{10000 + i}" if stage in ("customer", "churned") else None
    if external_id:
        identifiers.append(
            {"type": "external_id", "value": external_id, "primary": False}
        )

    # sessions / events
    if stage == "anonymous":
        n_sessions = r.randint(1, 2)
    elif stage == "known":
        n_sessions = r.randint(2, 4)
    elif stage == "customer":
        n_sessions = r.randint(3, 6)
    else:
        n_sessions = r.randint(2, 3)

    events: list[dict[str, Any]] = []
    purchases_needed = (
        r.randint(1, 4)
        if stage == "customer"
        else (r.randint(1, 2) if stage == "churned" else 0)
    )
    window_start = NOW - timedelta(days=90 if stage == "churned" else DAYS - 0.5)
    window_end = NOW - timedelta(days=45 if stage == "churned" else 0.05)
    session_times = sorted(
        window_start + (window_end - window_start) * r.random()
        for _ in range(n_sessions)
    )
    has_pii = email is not None
    campaign_name = {
        "facebook": "[FB] Prospecting - Lookalike 1% Purchasers",
        "instagram": "[IG] Reels - Spring Collection Launch",
        "whatsapp": "[WA] Click-to-WhatsApp - Lead Gen",
        "organic": None,
    }[channel]
    for s_idx, s_time in enumerate(session_times):
        t = s_time
        session_id = uid("session", i, s_idx).hex[:16]

        def add(
            name: str, props: dict[str, Any], source: str = "web", pii: bool = has_pii
        ) -> None:
            nonlocal t
            t = t + timedelta(seconds=r.randint(20, 240))
            emq = r.uniform(84, 98) if pii else r.uniform(52, 74)
            events.append(
                {
                    "name": name,
                    "time": t,
                    "props": props,
                    "source": sources[source],
                    "session_id": session_id,
                    "emq": round(emq, 2),
                }
            )

        add(
            "PageView",
            {
                "url": "/",
                "title": "Home",
                "referrer": f"https://{channel}.com" if channel != "organic" else None,
            },
        )
        for _ in range(r.randint(0, 2)):
            sku, pname, price = r.choice(PRODUCTS)
            add(
                "ViewContent",
                {
                    "product_id": sku,
                    "product_name": pname,
                    "price": price,
                    "currency": "USD",
                    "content_type": "product",
                },
            )
        if r.random() < 0.3:
            add(
                "Search",
                {
                    "query": r.choice(
                        ["linen dress", "sneakers", "gift set", "yoga mat", "earbuds"]
                    ),
                    "results": r.randint(3, 40),
                },
            )
        want_purchase = purchases_needed > 0 and (
            s_idx >= n_sessions - purchases_needed
        )
        if want_purchase or r.random() < (0.45 if stage != "anonymous" else 0.2):
            sku, pname, price = r.choice(PRODUCTS)
            qty = r.randint(1, 3)
            add(
                "AddToCart",
                {
                    "product_id": sku,
                    "product_name": pname,
                    "quantity": qty,
                    "value": round(price * qty, 2),
                    "currency": "USD",
                },
            )
            if want_purchase or r.random() < 0.5:
                add(
                    "InitiateCheckout",
                    {
                        "value": round(price * qty, 2),
                        "num_items": qty,
                        "currency": "USD",
                    },
                )
                if want_purchase:
                    total = round(price * qty + r.choice([0, 0, 5.99]), 2)
                    add(
                        "Purchase",
                        {
                            "order_id": f"ORD-{40000 + i * 7 + s_idx}",
                            "total": total,
                            "currency": "USD",
                            "num_items": qty,
                            "product_ids": [sku],
                            "payment_method": r.choice(["card", "apple_pay", "cod"]),
                            "coupon": r.choice([None, None, "SPRING10"]),
                        },
                        source="server",
                        pii=True,
                    )
                    purchases_needed -= 1
        if channel == "whatsapp" and stage != "anonymous" and s_idx == 0:
            add(
                "Lead",
                {
                    "form": "click_to_whatsapp",
                    "channel": "whatsapp",
                    "campaign": campaign_name,
                },
                source="server",
                pii=True,
            )
        if stage != "anonymous" and s_idx == 0 and r.random() < 0.5:
            add(
                "CompleteRegistration",
                {"method": r.choice(["email", "whatsapp", "apple"])},
                source="server",
                pii=True,
            )

    events.sort(key=lambda e: e["time"])
    purchases = [e for e in events if e["name"] == "Purchase"]
    revenue = round(sum(e["props"]["total"] for e in purchases), 2)
    first_seen = (
        min(events[0]["time"], NOW - timedelta(days=r.randint(0, 120)))
        if stage != "anonymous"
        else events[0]["time"]
    )
    last_seen = events[-1]["time"]

    traits: dict[str, Any] = {
        "acquisition_channel": channel,
        "device_preference": device,
        "engagement_score": round(
            clamp(len(events) * 4.5 + r.uniform(-5, 5), 5, 100), 1
        ),
    }
    if purchases:
        last_purchase_days = (NOW - purchases[-1]["time"]).days
        recency = (
            5
            if last_purchase_days <= 7
            else 4
            if last_purchase_days <= 14
            else 3
            if last_purchase_days <= 30
            else 2
            if last_purchase_days <= 60
            else 1
        )
        frequency = min(5, len(purchases) + 1)
        monetary = (
            5
            if revenue >= 300
            else 4
            if revenue >= 200
            else 3
            if revenue >= 120
            else 2
            if revenue >= 60
            else 1
        )
        if recency >= 4 and frequency >= 4 and monetary >= 4:
            seg = "Champions"
        elif frequency >= 3 and monetary >= 3:
            seg = "Loyal Customers"
        elif recency >= 4 and frequency <= 2:
            seg = "New Customers" if len(purchases) == 1 else "Potential Loyalists"
        elif recency <= 2 and monetary >= 3:
            seg = "At Risk"
        elif recency <= 1:
            seg = "Hibernating"
        else:
            seg = "Need Attention"
        traits.update(
            {
                "rfm_segment": seg,
                "rfm_recency_score": recency,
                "rfm_frequency_score": frequency,
                "rfm_monetary_score": monetary,
                "rfm_score": f"{recency}{frequency}{monetary}",
                "last_purchase_days_ago": last_purchase_days,
                "average_order_value": round(revenue / len(purchases), 2),
                "predicted_ltv": round(revenue * r.uniform(1.6, 3.4), 2),
                "total_purchases": len(purchases),
            }
        )
    profile_data: dict[str, Any] = {
        "city": city,
        "country": country,
        "device": device,
        "acquisition_channel": channel,
        "language": "ar"
        if country in ("AE", "SA", "EG", "JO") and r.random() < 0.5
        else "en",
    }
    if stage != "anonymous":
        profile_data.update(
            {
                "first_name": first,
                "last_name": last,
                "email": email,
                "phone": phone,
                "loyalty_tier": r.choice(["bronze", "silver", "gold"])
                if stage == "customer"
                else None,
                "marketing_opt_in": True,
            }
        )
    return {
        "i": i,
        "id": profile_id,
        "stage": stage,
        "external_id": external_id,
        "identifiers": identifiers,
        "events": events,
        "sessions": n_sessions,
        "revenue": revenue,
        "purchases": len(purchases),
        "first_seen": first_seen,
        "last_seen": last_seen,
        "traits": traits,
        "profile_data": profile_data,
        "email": email,
        "phone": phone,
        "channel": channel,
        "campaign_name": campaign_name,
        "rng": r,
    }


SEGMENT_SPECS = [
    dict(
        key="high-value-customers",
        name="High-Value Customers",
        description="Customers with more than $200 lifetime revenue - prime for lookalike seeds and loyalty offers.",
        rules={
            "logic": "and",
            "conditions": [
                {
                    "field": "profile.lifecycle_stage",
                    "operator": "equals",
                    "value": "customer",
                },
                {
                    "field": "profile.total_revenue",
                    "operator": "greater_than",
                    "value": 200,
                },
            ],
        },
        tags=["revenue", "lookalike-seed", "meta"],
        match=lambda p: p["stage"] == "customer" and p["revenue"] > 200,
    ),
    dict(
        key="cart-abandoners-30d",
        name="Cart Abandoners (30d)",
        description="Added to cart in the last 30 days without purchasing - targeted by [WA] Abandoned Cart Recovery.",
        rules={
            "logic": "and",
            "conditions": [
                {
                    "field": "event.AddToCart.count",
                    "operator": "greater_or_equal",
                    "value": 1,
                },
                {"field": "event.Purchase.count", "operator": "equals", "value": 0},
            ],
        },
        tags=["retargeting", "whatsapp", "cart"],
        match=lambda p: (
            any(e["name"] == "AddToCart" for e in p["events"]) and p["purchases"] == 0
        ),
    ),
    dict(
        key="whatsapp-engaged-leads",
        name="WhatsApp Engaged Leads",
        description="Known or customer profiles acquired through Click-to-WhatsApp ads.",
        rules={
            "logic": "and",
            "conditions": [
                {
                    "field": "data.acquisition_channel",
                    "operator": "equals",
                    "value": "whatsapp",
                },
                {
                    "field": "profile.lifecycle_stage",
                    "operator": "in",
                    "value": ["known", "customer"],
                },
            ],
        },
        tags=["whatsapp", "leads"],
        match=lambda p: (
            p["channel"] == "whatsapp" and p["stage"] in ("known", "customer")
        ),
    ),
]

FUNNEL_STEPS = [
    {
        "step": 1,
        "step_name": "View Product",
        "event_name": "ViewContent",
        "conditions": [],
    },
    {
        "step": 2,
        "step_name": "Add to Cart",
        "event_name": "AddToCart",
        "conditions": [],
    },
    {
        "step": 3,
        "step_name": "Start Checkout",
        "event_name": "InitiateCheckout",
        "conditions": [],
    },
    {"step": 4, "step_name": "Purchase", "event_name": "Purchase", "conditions": []},
]


async def seed_cdp(db: AsyncSession, tenant_id: int, user_id: int) -> dict[str, int]:
    counts: dict[str, int] = {}
    if await count(db, CDPProfile, CDPProfile.tenant_id == tenant_id):
        return counts

    # --- sources ------------------------------------------------------------
    source_specs = [
        (
            "web",
            "Website Pixel (demo-store.stratum.ai)",
            "website",
            {"domain": "demo-store.stratum.ai", "sdk": "stratum.js 2.4"},
        ),
        (
            "server",
            "Server-side CAPI Gateway",
            "server",
            {"runtime": "node", "capi_dedup": True},
        ),
        (
            "import",
            "Shopify Customer Import",
            "import",
            {"format": "csv", "schedule": "weekly"},
        ),
    ]
    sources: dict[str, uuid.UUID] = {}
    source_rows: dict[str, CDPSource] = {}
    for key, name, stype, config in source_specs:
        src = CDPSource(
            id=uid("source", key),
            tenant_id=tenant_id,
            name=name,
            source_type=stype,
            source_key=f"src_{uid('source_key', key).hex}",
            config=config,
            is_active=True,
            event_count=0,
            last_event_at=None,
            created_at=ago(days=45),
            updated_at=ago(days=45),
        )
        db.add(src)
        sources[key] = src.id
        source_rows[key] = src
    counts["cdp_sources"] = len(source_specs)

    # --- profiles, identifiers, consents ------------------------------------
    plans = [build_profile_plan(i, sources) for i in range(PROFILE_COUNT)]
    identifier_ids: dict[tuple[int, str], uuid.UUID] = {}
    counts["cdp_profiles"] = counts["cdp_profile_identifiers"] = counts[
        "cdp_consents"
    ] = 0
    for p in plans:
        profile = CDPProfile(
            id=p["id"],
            tenant_id=tenant_id,
            external_id=p["external_id"],
            first_seen_at=p["first_seen"],
            last_seen_at=p["last_seen"],
            profile_data=p["profile_data"],
            computed_traits=p["traits"],
            lifecycle_stage=p["stage"],
            total_events=len(p["events"]),
            total_sessions=p["sessions"],
            total_purchases=p["purchases"],
            total_revenue=Decimal(str(p["revenue"])),
            created_at=p["first_seen"],
            updated_at=p["last_seen"],
        )
        db.add(profile)
        counts["cdp_profiles"] += 1
        for ident in p["identifiers"]:
            ident_id = uid("identifier", p["i"], ident["type"])
            identifier_ids[(p["i"], ident["type"])] = ident_id
            db.add(
                CDPProfileIdentifier(
                    id=ident_id,
                    tenant_id=tenant_id,
                    profile_id=p["id"],
                    identifier_type=ident["type"],
                    identifier_value=ident["value"],
                    identifier_hash=sha(ident["value"]),
                    is_primary=ident["primary"],
                    confidence_score=Decimal("1.00")
                    if ident["type"] in ("email", "phone", "external_id")
                    else Decimal("0.90"),
                    verified_at=p["first_seen"]
                    if ident["type"] in ("email", "external_id")
                    else None,
                    first_seen_at=p["first_seen"],
                    last_seen_at=p["last_seen"],
                    created_at=p["first_seen"],
                )
            )
            counts["cdp_profile_identifiers"] += 1
        if p["stage"] != "anonymous":
            r = p["rng"]
            for ctype, prob in (
                ("analytics", 0.95),
                ("ads", 0.8),
                ("email", 0.7),
                ("sms", 0.45 if p["phone"] else 0.0),
            ):
                if prob == 0.0:
                    continue
                granted = r.random() < prob
                db.add(
                    CDPConsent(
                        id=uid("consent", p["i"], ctype),
                        tenant_id=tenant_id,
                        profile_id=p["id"],
                        consent_type=ctype,
                        granted=granted,
                        granted_at=p["first_seen"] if granted else None,
                        revoked_at=None
                        if granted
                        else p["first_seen"] + timedelta(days=1),
                        source=r.choice(["web_banner", "checkout", "whatsapp_optin"]),
                        consent_text="I agree to receive personalised offers from Stratum Demo Store.",
                        consent_version="v2.1",
                        created_at=p["first_seen"],
                        updated_at=p["first_seen"],
                    )
                )
                counts["cdp_consents"] += 1
    await db.commit()

    # --- events -------------------------------------------------------------
    counts["cdp_events"] = 0
    source_stats: dict[uuid.UUID, dict[str, Any]] = {}
    for p in plans:
        for n, e in enumerate(p["events"]):
            r = rng("event", p["i"], n)
            received = e["time"] + timedelta(seconds=r.randint(1, 90))
            ident_payload = [
                {"type": i["type"], "value": sha(i["value"])}
                for i in p["identifiers"]
                if i["type"] != "external_id"
            ]
            context: dict[str, Any] = {
                "session_id": e["session_id"],
                "device": p["profile_data"]["device"],
                "locale": p["profile_data"]["language"],
                "country": p["profile_data"]["country"],
                "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X)"
                if p["profile_data"]["device"] == "mobile"
                else "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4)",
                "campaign": {
                    "source": p["channel"],
                    "medium": "paid_social" if p["channel"] != "organic" else "organic",
                    "name": p["campaign_name"],
                },
            }
            if p["channel"] in ("facebook", "instagram"):
                context["fbp"] = (
                    f"fb.1.{int(p['first_seen'].timestamp())}.{r.randint(10**9, 10**10 - 1)}"
                )
                context["fbc"] = (
                    f"fb.1.{int(e['time'].timestamp())}.{uid('fbclid', p['i']).hex[:24]}"
                )
            db.add(
                CDPEvent(
                    id=uid("event", p["i"], n),
                    tenant_id=tenant_id,
                    profile_id=p["id"],
                    source_id=e["source"],
                    event_name=e["name"],
                    event_time=e["time"],
                    received_at=received,
                    idempotency_key=f"evt_{uid('event', p['i'], n).hex[:24]}",
                    properties=e["props"],
                    context=context,
                    identifiers=ident_payload,
                    processed=True,
                    processing_errors=[],
                    emq_score=Decimal(str(e["emq"])),
                    created_at=received,
                )
            )
            stats = source_stats.setdefault(e["source"], {"count": 0, "last": received})
            stats["count"] += 1
            stats["last"] = max(stats["last"], received)
            counts["cdp_events"] += 1
    for src in source_rows.values():
        stats = source_stats.get(src.id)
        if stats:
            src.event_count = stats["count"]
            src.last_event_at = stats["last"]
    await db.commit()

    # --- identity links, canonical identities, merges -------------------------
    counts["cdp_identity_links"] = counts["cdp_canonical_identities"] = counts[
        "cdp_profile_merges"
    ] = 0
    for p in plans:
        r = rng("links", p["i"])
        email_id = identifier_ids.get((p["i"], "email"))
        if email_id:
            for other_type, link_type in (
                ("anonymous_id", r.choice(["login", "form_submit"])),
                ("device_id", "same_session"),
                ("phone", "purchase"),
            ):
                other = identifier_ids.get((p["i"], other_type))
                if not other:
                    continue
                db.add(
                    CDPIdentityLink(
                        id=uid("link", p["i"], other_type),
                        tenant_id=tenant_id,
                        source_identifier_id=other,
                        target_identifier_id=email_id,
                        link_type=link_type,
                        confidence_score=Decimal("1.00")
                        if link_type in ("login", "purchase")
                        else Decimal("0.85"),
                        evidence={
                            "session_id": p["events"][0]["session_id"],
                            "event": "CompleteRegistration"
                            if link_type == "login"
                            else "Purchase"
                            if link_type == "purchase"
                            else "PageView",
                        },
                        is_active=True,
                        verified_at=p["first_seen"]
                        if link_type in ("login", "purchase")
                        else None,
                        created_at=p["first_seen"],
                    )
                )
                counts["cdp_identity_links"] += 1
            db.add(
                CDPCanonicalIdentity(
                    id=uid("canonical", p["i"]),
                    tenant_id=tenant_id,
                    profile_id=p["id"],
                    canonical_identifier_id=email_id,
                    canonical_type="email",
                    canonical_value_hash=sha(p["email"]),
                    priority_score=100,
                    is_verified=p["stage"] == "customer",
                    verified_at=p["first_seen"] if p["stage"] == "customer" else None,
                    verification_method="purchase"
                    if p["stage"] == "customer"
                    else None,
                    created_at=p["first_seen"],
                    updated_at=p["last_seen"],
                )
            )
            counts["cdp_canonical_identities"] += 1

    merge_reasons = [
        "identity_match",
        "login_event",
        "cross_device",
        "identity_match",
        "manual_merge",
        "login_event",
    ]
    for n, reason in enumerate(merge_reasons):
        surviving = plans[130 + n * 7]  # customers
        r = rng("merge", n)
        merged_id = uid("merged-profile", n)
        db.add(
            CDPProfileMerge(
                id=uid("merge", n),
                tenant_id=tenant_id,
                surviving_profile_id=surviving["id"],
                merged_profile_id=merged_id,
                merge_reason=reason,
                merged_profile_snapshot={
                    "id": str(merged_id),
                    "lifecycle_stage": "anonymous",
                    "total_events": r.randint(2, 9),
                    "identifiers": [
                        {"type": "anonymous_id", "hash": sha(str(merged_id))[:16]}
                    ],
                    "first_seen_at": (
                        surviving["first_seen"] - timedelta(days=r.randint(1, 20))
                    ).isoformat(),
                },
                triggering_identifier_type="email"
                if reason != "cross_device"
                else "device_id",
                triggering_identifier_hash=sha(surviving["email"])
                if surviving["email"]
                else None,
                merged_event_count=r.randint(2, 9),
                merged_identifier_count=r.randint(1, 3),
                merged_by_user_id=user_id if reason == "manual_merge" else None,
                merge_metadata={"strategy": "keep_oldest", "conflicts": 0},
                is_rolled_back=False,
                created_at=ago(days=r.randint(1, 20), hours=r.randint(0, 23)),
            )
        )
        counts["cdp_profile_merges"] += 1
    await db.commit()

    # --- segments + memberships ------------------------------------------------
    counts["cdp_segments"] = counts["cdp_segment_memberships"] = 0
    segments: dict[str, CDPSegment] = {}
    for spec in SEGMENT_SPECS:
        members = [p for p in plans if spec["match"](p)]
        seg = CDPSegment(
            id=uid("segment", spec["key"]),
            tenant_id=tenant_id,
            name=spec["name"],
            description=spec["description"],
            slug=spec["key"],
            segment_type="dynamic",
            status="active",
            rules=spec["rules"],
            profile_count=len(members),
            last_computed_at=ago(hours=1, minutes=12),
            computation_duration_ms=rng("segment", spec["key"]).randint(220, 1400),
            auto_refresh=True,
            refresh_interval_hours=24,
            next_refresh_at=ago(hours=-22),
            tags=spec["tags"],
            created_by_user_id=user_id,
            created_at=ago(days=18),
            updated_at=ago(hours=1, minutes=12),
        )
        db.add(seg)
        segments[spec["key"]] = seg
        counts["cdp_segments"] += 1
        for p in members:
            db.add(
                CDPSegmentMembership(
                    id=uid("membership", spec["key"], p["i"]),
                    tenant_id=tenant_id,
                    segment_id=seg.id,
                    profile_id=p["id"],
                    added_at=ago(hours=1, minutes=12),
                    is_active=True,
                    match_score=Decimal("1.00"),
                )
            )
            counts["cdp_segment_memberships"] += 1

    # --- computed traits ---------------------------------------------------------
    trait_specs = [
        (
            "total_purchases",
            "Total Purchases",
            "count",
            {"event_name": "Purchase", "time_window_days": 365},
            "number",
            "0",
        ),
        (
            "average_order_value",
            "Average Order Value",
            "average",
            {"event_name": "Purchase", "property": "total", "time_window_days": 365},
            "number",
            "0",
        ),
        (
            "last_purchase_days_ago",
            "Days Since Last Purchase",
            "last",
            {"event_name": "Purchase", "output": "days_since"},
            "number",
            None,
        ),
        (
            "predicted_ltv",
            "Predicted LTV (12m)",
            "formula",
            {"formula": "average_order_value * expected_orders_12m", "model": "ltv_v2"},
            "number",
            "0",
        ),
    ]
    for name, display, ttype, config, otype, default in trait_specs:
        db.add(
            CDPComputedTrait(
                id=uid("trait", name),
                tenant_id=tenant_id,
                name=name,
                display_name=display,
                description=f"{display} computed from CDP events.",
                trait_type=ttype,
                source_config=config,
                output_type=otype,
                default_value=default,
                is_active=True,
                last_computed_at=ago(hours=1, minutes=5),
                created_at=ago(days=18),
                updated_at=ago(hours=1, minutes=5),
            )
        )
    counts["cdp_computed_traits"] = len(trait_specs)

    # --- funnel + entries ----------------------------------------------------------
    counts["cdp_funnel_entries"] = 0
    funnel_id = uid("funnel", "purchase-funnel")
    step_counts = [0, 0, 0, 0]
    entries = []
    for p in plans:
        names_in_order = [e["name"] for e in p["events"]]
        if "ViewContent" not in names_in_order:
            continue
        completed = 0
        stamps: dict[str, str] = {}
        cursor = 0
        for step in FUNNEL_STEPS:
            found = None
            for idx in range(cursor, len(p["events"])):
                if p["events"][idx]["name"] == step["event_name"]:
                    found = idx
                    break
            if found is None:
                break
            completed += 1
            cursor = found + 1
            stamps[str(step["step"])] = p["events"][found]["time"].isoformat()
        for s in range(completed):
            step_counts[s] += 1
        converted = completed == len(FUNNEL_STEPS)
        entered_at = datetime.fromisoformat(stamps["1"])
        converted_at = datetime.fromisoformat(stamps["4"]) if converted else None
        entries.append(
            CDPFunnelEntry(
                id=uid("funnel_entry", p["i"]),
                tenant_id=tenant_id,
                funnel_id=funnel_id,
                profile_id=p["id"],
                entered_at=entered_at,
                converted_at=converted_at,
                is_converted=converted,
                current_step=min(completed + 1, len(FUNNEL_STEPS))
                if not converted
                else len(FUNNEL_STEPS),
                completed_steps=completed,
                step_timestamps=stamps,
                total_duration_seconds=int((converted_at - entered_at).total_seconds())
                if converted_at
                else None,
                created_at=entered_at,
                updated_at=converted_at or entered_at,
            )
        )
    step_metrics = []
    for idx, step in enumerate(FUNNEL_STEPS):
        prev = step_counts[idx - 1] if idx else step_counts[0]
        step_metrics.append(
            {
                "step": step["step"],
                "name": step["step_name"],
                "event_name": step["event_name"],
                "count": step_counts[idx],
                "conversion_rate": round(step_counts[idx] / step_counts[0] * 100, 1)
                if step_counts[0]
                else 0,
                "step_conversion_rate": round(step_counts[idx] / prev * 100, 1)
                if prev
                else 0,
                "drop_off_rate": round((1 - step_counts[idx] / prev) * 100, 1)
                if prev
                else 0,
            }
        )
    db.add(
        CDPFunnel(
            id=funnel_id,
            tenant_id=tenant_id,
            name="Purchase Funnel",
            description="Product view -> add to cart -> checkout -> purchase across Meta-acquired traffic.",
            slug="purchase-funnel",
            status="active",
            steps=FUNNEL_STEPS,
            conversion_window_days=30,
            step_timeout_hours=None,
            total_entered=step_counts[0],
            total_converted=step_counts[3],
            overall_conversion_rate=Decimal(
                str(round(step_counts[3] / step_counts[0] * 100, 2))
            )
            if step_counts[0]
            else None,
            step_metrics=step_metrics,
            last_computed_at=ago(hours=1),
            computation_duration_ms=860,
            auto_refresh=True,
            refresh_interval_hours=24,
            next_refresh_at=ago(hours=-23),
            tags=["ecommerce", "meta"],
            created_by_user_id=user_id,
            created_at=ago(days=15),
            updated_at=ago(hours=1),
        )
    )
    db.add_all(entries)
    counts["cdp_funnels"] = 1
    counts["cdp_funnel_entries"] = len(entries)
    await db.commit()

    counts["_segments"] = {k: v.id for k, v in segments.items()}  # type: ignore[assignment]
    counts["_segment_sizes"] = {k: v.profile_count for k, v in segments.items()}  # type: ignore[assignment]
    return counts


# =============================================================================
# Section: Meta audience sync
# =============================================================================


async def seed_audience_sync(
    db: AsyncSession,
    tenant_id: int,
    segments: dict[str, uuid.UUID],
    sizes: dict[str, int],
) -> dict[str, int]:
    counts = {
        "audience_sync_credentials": 0,
        "platform_audiences": 0,
        "audience_sync_jobs": 0,
    }
    if await count(db, PlatformAudience, PlatformAudience.tenant_id == tenant_id):
        return counts

    if not await count(
        db, AudienceSyncCredential, AudienceSyncCredential.tenant_id == tenant_id
    ):
        from app.services.encryption import encrypt_token

        db.add(
            AudienceSyncCredential(
                id=uid("audience_credential", "meta"),
                tenant_id=tenant_id,
                platform="meta",
                ad_account_id=AD_ACCOUNT_ID,
                access_token=None,
                access_token_encrypted=encrypt_token("demo-system-user-token-redacted"),
                config={
                    "ad_account_name": AD_ACCOUNT_NAME,
                    "business_id": "1023456789012",
                    "seed": SEED_MARKER,
                },
                is_active=True,
                created_at=ago(days=30),
                updated_at=ago(days=30),
            )
        )
        counts["audience_sync_credentials"] = 1

    specs = [
        (
            "high-value-customers",
            "Stratum - High-Value Customers",
            "Lookalike seed: customers > $200 LTV",
            0.71,
            24,
        ),
        (
            "whatsapp-engaged-leads",
            "Stratum - WhatsApp Engaged Leads",
            "Retarget CTWA leads across FB/IG",
            0.64,
            12,
        ),
    ]
    for n, (seg_key, name, description, match_rate, interval) in enumerate(
        specs, start=1
    ):
        r = rng("audience", seg_key)
        size = sizes.get(seg_key, 0)
        matched = int(round(size * match_rate))
        last_sync = ago(hours=4 + n)
        audience = PlatformAudience(
            id=uid("platform_audience", seg_key),
            tenant_id=tenant_id,
            segment_id=segments[seg_key],
            platform="meta",
            ad_account_id=AD_ACCOUNT_ID,
            audience_type="customer_list",
            platform_audience_id=f"2384500123456{n:03d}",
            platform_audience_name=name,
            description=description,
            auto_sync=True,
            sync_interval_hours=interval,
            next_sync_at=naive(last_sync + timedelta(hours=interval)),
            last_sync_at=naive(last_sync),
            last_sync_status="completed",
            last_sync_error=None,
            platform_size=matched,
            matched_size=matched,
            match_rate=match_rate,
            created_at=ago(days=6 + n),
            updated_at=last_sync,
        )
        db.add(audience)
        counts["platform_audiences"] += 1

        history = [
            ("create", "completed", 6 + n),
            ("update", "completed", 4 + n),
            ("update", "partial", 3),
            ("replace", "completed", 2),
            ("update", "completed", 1),
            ("update", "completed", 0),
        ]
        for j, (operation, status, days_ago) in enumerate(history):
            started = ago(days=days_ago, hours=4 + n) if days_ago else last_sync
            duration = r.randint(1800, 9500)
            sent = (
                size
                if operation in ("create", "replace")
                else max(1, int(size * r.uniform(0.05, 0.2)))
            )
            failed = int(sent * 0.06) if status == "partial" else 0
            db.add(
                AudienceSyncJob(
                    id=uid("sync_job", seg_key, j),
                    tenant_id=tenant_id,
                    platform_audience_id=audience.id,
                    operation=operation,
                    status=status,
                    started_at=naive(started),
                    completed_at=naive(started + timedelta(milliseconds=duration)),
                    duration_ms=duration,
                    profiles_total=size,
                    profiles_sent=sent,
                    profiles_added=int(round((sent - failed) * match_rate)),
                    profiles_removed=int(size * 0.02) if operation == "replace" else 0,
                    profiles_failed=failed,
                    platform_response={
                        "audience_id": audience.platform_audience_id,
                        "session_id": r.randint(10**12, 10**13),
                        "num_received": sent,
                        "num_invalid_entries": failed,
                    },
                    error_message="6% of entries rejected: invalid phone format (normalised on next sync)"
                    if status == "partial"
                    else None,
                    error_details={"invalid_entries": failed, "reason": "phone_format"}
                    if status == "partial"
                    else None,
                    created_at=started,
                    updated_at=started + timedelta(milliseconds=duration),
                )
            )
            counts["audience_sync_jobs"] += 1

    await db.commit()
    return counts


# =============================================================================
# Main
# =============================================================================


async def main() -> None:
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    summary: dict[str, int] = {}

    print("== Stratum demo data seed (Meta only) ==")
    fixed_tables = await ensure_audience_sync_schema(engine)
    if fixed_tables:
        print(
            f"  schema: converted tenant_id uuid->integer on {', '.join(fixed_tables)}"
        )

    async with session_factory() as db:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.slug == TENANT_SLUG))
        ).scalar_one_or_none()
        if tenant is None:
            raise SystemExit("demo tenant not found - run scripts_seed_demo.py first")
        admin = (
            await db.execute(
                select(User).where(
                    User.tenant_id == tenant.id,
                    User.email_hash == hash_pii_for_lookup(DEMO_ADMIN_EMAIL),
                )
            )
        ).scalar_one_or_none()
        if admin is None:
            admin = (
                (
                    await db.execute(
                        select(User)
                        .where(User.tenant_id == tenant.id)
                        .order_by(User.id)
                    )
                )
                .scalars()
                .first()
            )
        if admin is None:
            raise SystemExit(
                "no user found for demo tenant - run scripts_seed_demo.py first"
            )
        tenant_id, user_id = tenant.id, admin.id
        print(
            f"  tenant={tenant.slug} (id={tenant_id}) user_id={user_id} today={TODAY}"
        )

        fixed = await fix_user_pii(
            db, tenant_id, reencrypt="--reencrypt-pii" in sys.argv
        )
        if fixed:
            print(f"  users: encrypted {fixed} plaintext PII field(s)")

        summary.update(await seed_tenant_setup(db, tenant, user_id))
        campaigns, c = await seed_campaigns(db, tenant_id)
        summary.update(c)
        summary["creative_assets"] = await seed_creative_assets(
            db, tenant_id, campaigns
        )
        summary.update(await seed_rules(db, tenant_id, campaigns))
        summary["competitor_benchmarks"] = await seed_competitors(db, tenant_id)
        summary.update(await seed_signal_health(db, tenant_id))
        summary["fact_attribution_variance_daily"] = await seed_attribution_variance(
            db, tenant_id, campaigns
        )
        summary.update(await seed_autopilot(db, tenant_id, user_id, campaigns))
        summary["notifications"] = await seed_notifications(db, tenant_id, user_id)
        summary["audit_logs"] = await seed_audit_logs(db, tenant_id, user_id, campaigns)
        summary.update(await seed_whatsapp(db, tenant_id, user_id))

        cdp = await seed_cdp(db, tenant_id, user_id)
        segments = cdp.pop("_segments", None)
        sizes = cdp.pop("_segment_sizes", None)
        summary.update(cdp)
        if segments is None:
            rows = (
                (
                    await db.execute(
                        select(CDPSegment).where(CDPSegment.tenant_id == tenant_id)
                    )
                )
                .scalars()
                .all()
            )
            segments = {s.slug: s.id for s in rows}
            sizes = {s.slug: s.profile_count for s in rows}
        if segments:
            summary.update(
                await seed_audience_sync(db, tenant_id, segments, sizes or {})
            )

    await engine.dispose()

    print("\nInserted rows this run:")
    for table, n in summary.items():
        if isinstance(n, int):
            print(f"  {table:36s} {n:>6d}")
    if all(v == 0 for v in summary.values() if isinstance(v, int)):
        print("  (nothing to do - demo data already present)")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:  # pragma: no cover
        print(f"seed failed: {exc!r}", file=sys.stderr)
        raise
