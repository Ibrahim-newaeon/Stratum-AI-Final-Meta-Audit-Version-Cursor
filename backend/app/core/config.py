# =============================================================================
# Stratum AI - Application Configuration
# =============================================================================
"""
Centralized configuration management using Pydantic Settings.
All environment variables are validated and typed.

SECURITY NOTE:
- In production, all sensitive values MUST be set via environment variables
- Never commit real credentials to the repository
- Use strong, randomly generated keys (32+ bytes) for encryption/signing
"""

import os
import warnings
from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PADDLE_SANDBOX_API_BASE_URL = "https://sandbox-api.paddle.com"
PADDLE_PRODUCTION_API_BASE_URL = "https://api.paddle.com"

# Opt-in escape hatch for the development conveniences below (fallback signing
# keys, the default tenant in TenantMiddleware). It exists so a container that
# deliberately runs without APP_ENV can still be told "yes, this is a dev box";
# it must never be set on a deployed environment.
DEV_DEFAULTS_OVERRIDE_ENV = "STRATUM_ALLOW_DEV_DEFAULTS"


def explicit_app_env() -> str | None:
    """
    Read the environment name the operator actually configured.

    Looks at the process environment first and then at the ``.env`` file that
    ``Settings`` itself loads, so a local checkout configured only through
    ``.env`` still counts as explicit. Returns None when nothing set it - which
    is different from ``Settings.app_env``, whose default silently reads
    "development".

    Returns:
        The lower-cased configured value, or None when APP_ENV is not set
    """
    raw = os.getenv("APP_ENV")
    if not raw:
        try:
            from dotenv import dotenv_values

            raw = dotenv_values(".env").get("APP_ENV")
        except (ImportError, OSError):  # pragma: no cover - dotenv is optional
            raw = None
    return raw.strip().lower() if raw else None


def dev_defaults_allowed() -> bool:
    """
    Report whether development conveniences may be used in this process.

    They are allowed only when ``APP_ENV`` is *explicitly* ``development`` or
    the operator opted in with ``STRATUM_ALLOW_DEV_DEFAULTS=1``. An unset or
    misspelled ``APP_ENV`` must never enable them: the Settings default would
    otherwise make an unconfigured container look like a development box and
    silently adopt the published fallback signing keys, which would let anyone
    forge an access token.

    Returns:
        True when development fallbacks are permitted, False otherwise
    """
    if os.getenv(DEV_DEFAULTS_OVERRIDE_ENV, "").strip() == "1":
        return True
    return explicit_app_env() == "development"


class Settings(BaseSettings):
    """Application settings with validation and type hints."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -------------------------------------------------------------------------
    # Application Settings
    # -------------------------------------------------------------------------
    app_name: str = Field(default="Stratum AI", description="Application name")
    app_env: Literal["development", "staging", "production"] = Field(default="development")
    debug: bool = Field(default=True)
    secret_key: str = Field(
        default="",
        min_length=32,
        description="Secret key for signing (REQUIRED: set via SECRET_KEY env var)",
    )
    api_v1_prefix: str = Field(default="/api/v1")

    # -------------------------------------------------------------------------
    # Subscription Tier
    # -------------------------------------------------------------------------
    subscription_tier: Literal["starter", "professional", "enterprise"] = Field(
        default="enterprise", description="Subscription tier (starter, professional, enterprise)"
    )

    # -------------------------------------------------------------------------
    # Database Configuration
    # -------------------------------------------------------------------------
    # NOTE: No default passwords - must be set via environment variables
    database_url: str = Field(
        default="postgresql+asyncpg://stratum:changeme@localhost:5432/stratum_ai",
        description="Database URL (set DATABASE_URL env var with real credentials)",
    )
    database_url_sync: str = Field(
        default="postgresql://stratum:changeme@localhost:5432/stratum_ai",
        description="Sync database URL (set DATABASE_URL_SYNC env var with real credentials)",
    )
    db_pool_size: int = Field(default=10)
    db_max_overflow: int = Field(default=20)
    db_pool_recycle: int = Field(default=3600)
    postgres_user: str = Field(default="postgres", description="PostgreSQL user (used by migrations/scripts)")
    postgres_password: str = Field(default="", description="PostgreSQL password (used by migrations/scripts)")
    postgres_db: str = Field(default="stratum_ai", description="PostgreSQL database name")

    # -------------------------------------------------------------------------
    # Redis Configuration
    # -------------------------------------------------------------------------
    redis_url: str = Field(default="redis://localhost:6379/0")
    redis_password: Optional[str] = Field(default=None, description="Redis password for authenticated connections")
    celery_broker_url: str = Field(default="redis://localhost:6379/1")
    celery_result_backend: str = Field(default="redis://localhost:6379/2")

    # -------------------------------------------------------------------------
    # ML Provider Configuration
    # -------------------------------------------------------------------------
    ml_provider: Literal["local"] = Field(
        default="local",
        description="ML inference provider: 'local' runs scikit-learn models loaded from disk",
    )
    ml_models_path: str = Field(default="./ml_models")
    mlflow_tracking_uri: Optional[str] = Field(default=None, description="MLflow tracking server URI")

    # -------------------------------------------------------------------------
    # Trust Engine (signal health -> trust gate)
    # -------------------------------------------------------------------------
    # The single source of truth for the thresholds documented in
    # docs/architecture/trust-engine.md. Call sites read them from here and
    # never hardcode 70/40: signal health >= healthy is PASS (autopilot
    # executes), healthy > score >= degraded is HOLD (alert only) and anything
    # lower is BLOCK (manual review). SignalHealthConfig and the trust gate in
    # app/tasks/apply_actions_queue.py both read these values.
    signal_health_healthy_threshold: float = Field(
        default=70.0,
        ge=0.0,
        le=100.0,
        description="Signal health at or above which the trust gate PASSes (autopilot executes)",
    )
    signal_health_degraded_threshold: float = Field(
        default=40.0,
        ge=0.0,
        le=100.0,
        description="Signal health at or above which the trust gate HOLDs; below it BLOCKs",
    )
    signal_health_fresh_minutes: float = Field(
        default=60.0, gt=0.0, description="Data age scoring full marks for the freshness component"
    )
    signal_health_stale_minutes: float = Field(
        default=24 * 60.0, gt=0.0, description="Data age scoring zero for the freshness component"
    )
    # A signal health row may have NULL metric columns. Scoring renormalises
    # the weights over the columns that are populated, so without a floor a row
    # carrying one trivially-perfect component (api_error_rate=0 is 15% of the
    # weight) would score 100 and PASS - "absence of data is health" again, one
    # level down. Require at least this much of the total weight before a score
    # is trusted; below it the gate treats the row as unscorable and BLOCKs.
    signal_health_min_component_weight: float = Field(
        default=0.5,
        gt=0.0,
        le=1.0,
        description=(
            "Fraction of the component weight that must be populated before a "
            "signal health row can be scored (below it the trust gate BLOCKs)"
        ),
    )
    # -------------------------------------------------------------------------
    # Signal health component weights
    # -------------------------------------------------------------------------
    # The single definition of the weights documented in
    # docs/architecture/trust-engine.md. SignalHealthConfig reads them, the
    # trust gate scores fact_signal_health_daily rows with them, and
    # app.services.signal_health computes the score the rollup and the
    # dashboard publish with them - so the composite cannot mean one thing at
    # the point it is written and another at the point it is enforced.
    # The names follow the fact table's columns: "variance" carries event loss
    # (event_loss_pct) and "anomaly" carries API/connection reliability
    # (api_error_rate).
    signal_health_emq_weight: float = Field(
        default=0.40, ge=0.0, le=1.0, description="Weight of the EMQ/delivery-quality component"
    )
    signal_health_freshness_weight: float = Field(
        default=0.25, ge=0.0, le=1.0, description="Weight of the data freshness component"
    )
    signal_health_variance_weight: float = Field(
        default=0.20, ge=0.0, le=1.0, description="Weight of the event-loss component"
    )
    signal_health_anomaly_weight: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Weight of the API/connection reliability component",
    )
    # How far back capi_delivery_logs is read for the delivery evidence when no
    # explicit window is given (the daily rollup passes the rollup day instead).
    signal_health_delivery_window_hours: int = Field(
        default=24,
        ge=1,
        description="Default width of the CAPI delivery window read for signal health",
    )
    # A success rate over two deliveries is noise, not evidence: one failure
    # would read as 50% loss. Below this many delivery attempts in the window
    # the delivery components are reported as unavailable rather than scored.
    signal_health_min_delivery_events: int = Field(
        default=10,
        ge=1,
        description=(
            "CAPI delivery attempts required in the window before the delivery "
            "components can be scored (below it they count as missing inputs)"
        ),
    )
    # capi_delivery_logs.user_data_hash is the only match-quality signal the
    # delivery table genuinely carries: it is set when the caller sent hashed
    # customer identifiers with the event. This is the share of the EMQ
    # component driven by that coverage; the rest is the delivery success rate.
    signal_health_identifier_coverage_weight: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Share of the EMQ component driven by hashed-identifier coverage",
    )
    # Points removed from the connection component per recorded error on
    # TenantPlatformConnection. Five errors zero it out.
    signal_health_connection_error_penalty: float = Field(
        default=20.0,
        ge=0.0,
        le=100.0,
        description="Points deducted from the connection component per recorded connection error",
    )
    # Freshness of the signal health snapshot itself. The rollup writes rows
    # dated for the previous day (02:00 UTC), so yesterday's row is the newest
    # one that can exist and 1 is the smallest workable value.
    trust_gate_max_health_age_days: int = Field(
        default=1,
        ge=0,
        description="How many days old a fact_signal_health_daily row may be and still count",
    )
    # The per-minute dispatchers (scheduled WhatsApp sends, scheduled CMS
    # publishes) select everything whose scheduled_at has passed, with no lower
    # bound. The worker consumed no queues until now, so whatever was scheduled
    # since deployment is still pending: without a cut-off the first worker
    # start would flush the entire backlog at once, sending months-old messages
    # and publishing stale posts. Anything older than this is left alone for an
    # operator to review rather than fired blind.
    scheduled_dispatch_max_age_hours: int = Field(
        default=24,
        ge=1,
        description=(
            "How overdue a scheduled WhatsApp message or CMS post may be and "
            "still be dispatched automatically"
        ),
    )
    trust_gate_stale_health_decision: Literal["hold", "block"] = Field(
        default="hold",
        description=(
            "Gate decision when the newest signal health row is older than "
            "trust_gate_max_health_age_days. Never 'pass' - a stale row is not health."
        ),
    )

    # -------------------------------------------------------------------------
    # Ad Platform Configuration
    # -------------------------------------------------------------------------
    # Fabricated ad metrics. When true, the sync tasks write
    # MockAdNetwork.generate_time_series() output into CampaignMetric instead
    # of calling a platform API - useful locally, catastrophic for a real
    # tenant, who would see invented spend/revenue as if it were their own.
    # Default off; validate_security_settings() refuses it in production.
    use_mock_ad_data: bool = Field(
        default=False,
        description=(
            "Write mock ad metrics instead of real platform data. "
            "Development only - rejected when APP_ENV=production."
        ),
    )

    # OAuth callback base URL (for constructing redirect URIs)
    oauth_redirect_base_url: str = Field(
        default="http://localhost:8000", description="Base URL for OAuth callbacks"
    )

    # Meta/Facebook OAuth
    meta_app_id: Optional[str] = Field(default=None, description="Meta/Facebook App ID")
    meta_app_secret: Optional[str] = Field(default=None, description="Meta/Facebook App Secret")
    meta_access_token: Optional[str] = Field(default=None)
    # Stratum's OWN marketing pixel, used only to send landing-page waitlist
    # leads back to Meta. Landing-page subscribers are first-party leads with
    # no tenant (see LandingPageSubscriber - a tenant only exists later, via
    # converted_to_tenant_id), so there is no tenant credential to scope this
    # to. Tenant Conversions API credentials are supplied per request and are
    # never read from the environment.
    meta_pixel_id: Optional[str] = Field(
        default=None,
        description=(
            "Meta Pixel ID for Stratum's own marketing site. Used with "
            "META_ACCESS_TOKEN to send landing-page Lead events to the "
            "Conversions API. Unset disables that send."
        ),
    )
    # DEPRECATED override kept so an existing META_API_VERSION env var is not
    # silently ignored. Leave it unset: the OAuth flow then follows
    # meta_graph_api_version like every other Meta caller. Its old default
    # (v19.0) expired on 2026-05-21.
    meta_api_version: Optional[str] = Field(
        default=None,
        description=(
            "DEPRECATED per-flow override for the Meta OAuth Graph version. "
            "Unset means 'use META_GRAPH_API_VERSION'."
        ),
    )

    # -------------------------------------------------------------------------
    # Meta Marketing API insights ingestion (READ-ONLY)
    # -------------------------------------------------------------------------
    # The nightly/hourly campaign sync pulls Ads Insights rows and writes them
    # into CampaignMetric. Only GET requests are ever issued; the scope needed
    # is ads_read. Per-tenant credentials live in tenant_platform_connection /
    # tenant_ad_account - only these global knobs come from the environment.
    meta_graph_api_version: str = Field(
        default="v23.0",
        description=(
            "Graph API version for every Meta caller: the read-only insights "
            "client, the Conversions API connector, the WhatsApp Cloud API "
            "connector, offline conversions, CDP audience sync and the OAuth "
            "flow (via meta_oauth_api_version). v23.0 is supported until "
            "2027-10-08; v26.0 is the current stable release."
        ),
    )
    meta_insights_lookback_days: int = Field(
        default=7,
        ge=1,
        le=90,
        description=(
            "Days re-pulled on every campaign sync. Meta restates conversions "
            "for days after the fact, so the window is re-fetched and upserted."
        ),
    )
    meta_insights_request_timeout_seconds: float = Field(
        default=30.0, gt=0, description="Per-request timeout for the Ads Insights API"
    )
    meta_insights_max_pages: int = Field(
        default=25,
        ge=1,
        description="Upper bound on insights pages followed (guards a runaway cursor loop)",
    )
    meta_conversion_action_types: str = Field(
        default="offsite_conversion.fb_pixel_purchase,omni_purchase,purchase",
        description=(
            "Comma-separated Meta action_type values counted as conversions and "
            "revenue. The first type present in a row wins, so order matters: "
            "the web-pixel purchase is preferred, then Meta's grouped "
            "'omni_purchase' (which also covers app, on-Facebook and offline "
            "purchases), then a bare 'purchase' as a belt-and-braces fallback. "
            "A tenant whose purchases are app-only or on-Facebook must have a "
            "type here that its account actually reports, or conversions and "
            "revenue are recorded as zero."
        ),
    )

    @property
    def meta_oauth_api_version(self) -> str:
        """
        Graph API version for the Meta OAuth flow.

        Follows ``meta_graph_api_version`` unless the deprecated
        ``META_API_VERSION`` override is set, so one knob moves every Meta
        caller.
        """
        return self.meta_api_version or self.meta_graph_api_version

    @property
    def meta_conversion_action_types_list(self) -> list[str]:
        """Get the configured Meta conversion action types as an ordered list."""
        return [
            action_type.strip()
            for action_type in self.meta_conversion_action_types.split(",")
            if action_type.strip()
        ]

    # -------------------------------------------------------------------------
    # Autopilot execution (Meta Marketing API WRITES) - OFF BY DEFAULT
    # -------------------------------------------------------------------------
    # These knobs govern the only code path that can change a live ad account:
    # app/services/meta/write_client.py, driven by
    # app/services/meta/action_executor.py. They are money, so every one of
    # them is conservative by default and every call site reads them from here
    # rather than carrying a literal.
    #
    # Turning execution on takes TWO deliberate steps - see
    # docs/architecture/trust-engine.md:
    #   1. AUTOPILOT_EXECUTION_ENABLED=true   (master switch, default false)
    #   2. AUTOPILOT_EXECUTION_DRY_RUN=false  (default true: every check runs,
    #                                          nothing is written)
    # Merging this feature therefore cannot start spending money on its own,
    # and neither can flipping a single flag by mistake.
    autopilot_execution_enabled: bool = Field(
        default=False,
        description=(
            "Master switch for Meta autopilot writes. False means no request "
            "that could change an ad account is ever issued."
        ),
    )
    autopilot_execution_dry_run: bool = Field(
        default=True,
        description=(
            "Run every gate, enforcement and guard-rail check and report the "
            "intended change without calling any Meta write endpoint."
        ),
    )
    meta_write_request_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Per-request timeout for Meta Marketing API reads and writes",
    )

    # Guard rails, all enforced BEFORE the write. A violated guard rail is a
    # refusal with a recorded reason - never a clamp-and-proceed, because
    # silently applying a smaller change than the one that was approved is
    # still applying something nobody approved.
    autopilot_max_budget_change_pct: float = Field(
        default=20.0,
        gt=0.0,
        le=100.0,
        description=(
            "Largest percentage change a single action may make to a budget, "
            "measured against the budget currently live on Meta"
        ),
    )
    autopilot_max_cumulative_budget_change_pct: float = Field(
        default=50.0,
        gt=0.0,
        description=(
            "Largest cumulative percentage change autopilot may make to one "
            "entity's budget in a UTC day, measured from the budget it started "
            "the day on"
        ),
    )
    # The absolute floor and ceiling below are ONE pair of numbers compared
    # against an amount in the ad account's major unit, so they only mean the
    # same thing across accounts whose currencies are of a similar magnitude.
    # They are sized for the two-decimal currencies Meta gives an offset of
    # 100 (USD, EUR, GBP, ...). For a currency Meta gives an offset of 1 -
    # JPY, KRW, VND, IDR, CLP, COP, CRC, HUF, ISK, PYG, TWD - a perfectly
    # ordinary daily budget is a five-figure number of major units, so these
    # defaults are meaningless there: the ceiling would refuse every action
    # and the floor would never bind. Rather than let one scalar be silently
    # wrong for those accounts, the executor REFUSES a budget action in any
    # such currency until an explicit per-currency pair is configured below.
    autopilot_min_daily_budget_major: float = Field(
        default=5.0,
        ge=0.0,
        description=(
            "Default absolute floor for a daily budget, in the ad account's "
            "major currency unit. A change that would land below it is "
            "refused. Sized for offset-100 currencies; offset-1 currencies "
            "must use autopilot_daily_budget_limits_by_currency."
        ),
    )
    autopilot_max_daily_budget_major: float = Field(
        default=1000.0,
        gt=0.0,
        description=(
            "Default absolute ceiling for a daily budget, in the ad account's "
            "major currency unit. A change that would land above it is "
            "refused. Sized for offset-100 currencies; offset-1 currencies "
            "must use autopilot_daily_budget_limits_by_currency."
        ),
    )
    autopilot_daily_budget_limits_by_currency: str = Field(
        default="",
        description=(
            "Per-currency overrides for the daily budget floor and ceiling, "
            "as comma-separated CURRENCY:floor:ceiling triples in that "
            "currency's major unit, e.g. 'JPY:750:150000,KRW:7000:1500000'. "
            "Required before autopilot may change a budget on an account "
            "whose currency has a Meta offset of 1; optional for every other."
        ),
    )

    @property
    def autopilot_daily_budget_limits_map(self) -> dict[str, tuple[float, float]]:
        """
        The configured per-currency budget limits, keyed by currency code.

        Returns:
            Currency code -> ``(floor, ceiling)`` in that currency's major
            unit. Malformed entries, a non-positive ceiling and a floor above
            its ceiling are dropped rather than half-applied: a limit nobody
            can read is not a limit, and the executor refuses when a currency
            that needs one has none.
        """
        limits: dict[str, tuple[float, float]] = {}
        for entry in self.autopilot_daily_budget_limits_by_currency.split(","):
            parts = [part.strip() for part in entry.split(":")]
            if len(parts) != 3 or not parts[0]:
                continue
            try:
                floor, ceiling = float(parts[1]), float(parts[2])
            except ValueError:
                continue
            if floor < 0 or ceiling <= 0 or floor > ceiling:
                continue
            limits[parts[0].upper()] = (floor, ceiling)
        return limits
    autopilot_max_executed_actions_per_tenant_per_day: int = Field(
        default=10,
        ge=0,
        description=(
            "How many actions autopilot may execute for one tenant in a UTC "
            "day. Zero means none."
        ),
    )
    # Starts from app.autopilot.service.SAFE_ACTIONS: the reversible, spend-
    # reducing actions. budget_increase, pause_campaign and every enable_* are
    # deliberately absent - raising spend or re-enabling delivery is not
    # something automation should do unsupervised by default.
    #
    # SAFE_ACTIONS minus pause_creative. That action's target node is not
    # established: the executor maps entity_type "creative" to the Meta **ad**
    # (an AdCreative carries no status of its own), but the only in-repo
    # producer - app/analytics/logic/recommend.py, via fatigue detection -
    # emits fact_creative.creative_id, which is nowhere shown to be an ad id.
    # If it is an AdCreative id the read fails closed with Graph #100 and
    # nothing is written, but a money-affecting action whose node identity
    # rests on an assumption does not belong in a default allowlist. Re-add it
    # once the producer is shown to emit an ad id.
    autopilot_executable_action_types: str = Field(
        default="budget_decrease,pause_adset,bid_decrease",
        description=(
            "Comma-separated allowlist of action types that may ever execute "
            "automatically against Meta. Anything absent is refused."
        ),
    )

    @property
    def autopilot_executable_action_types_set(self) -> frozenset[str]:
        """The configured auto-executable action types, as a set."""
        return frozenset(
            action_type.strip()
            for action_type in self.autopilot_executable_action_types.split(",")
            if action_type.strip()
        )

    # -------------------------------------------------------------------------
    # Measurement & Verification (GA4 read-only + GTM tag deployment)
    # -------------------------------------------------------------------------
    # GA4 and GTM are measurement-only integrations (never ad channels).
    # Property IDs, service-account JSON and container IDs live per tenant in
    # the database; only these global toggles are configured via environment.
    ga4_sync_enabled: bool = Field(
        default=True, description="Enable the nightly read-only GA4 baseline pull"
    )
    ga4_lookback_days: int = Field(
        default=3, description="Days re-pulled on every incremental GA4 sync"
    )
    ga4_backfill_days: int = Field(
        default=30, description="Days pulled on first sync / explicit backfill"
    )
    ga4_request_timeout_seconds: float = Field(
        default=30.0, description="Per-request timeout for the GA4 Data API"
    )
    ga4_default_conversion_event: str = Field(
        default="purchase", description="GA4 event counted as a conversion by default"
    )
    gtm_verify_timeout_seconds: float = Field(
        default=10.0, description="HTTP timeout when verifying GTM containers"
    )
    gtm_default_server_container_url: Optional[str] = Field(
        default=None,
        description="Optional default server-side tagging endpoint suggested to tenants",
    )

    # -------------------------------------------------------------------------
    # WhatsApp Business API Configuration
    # -------------------------------------------------------------------------
    whatsapp_phone_number_id: Optional[str] = Field(
        default=None, description="WhatsApp Business Phone Number ID"
    )
    whatsapp_access_token: Optional[str] = Field(
        default=None, description="WhatsApp Business API access token"
    )
    whatsapp_business_account_id: Optional[str] = Field(
        default=None, description="WhatsApp Business Account ID"
    )
    whatsapp_verify_token: str = Field(
        default="stratum-whatsapp-verify-token", description="Token for webhook verification"
    )
    whatsapp_app_secret: Optional[str] = Field(
        default=None, description="WhatsApp/Meta App Secret for webhook signature verification"
    )
    whatsapp_api_version: str = Field(
        default="v18.0", description="WhatsApp/Meta Graph API version"
    )

    # -------------------------------------------------------------------------
    # HubSpot CRM Configuration
    # -------------------------------------------------------------------------
    hubspot_client_id: Optional[str] = Field(
        default=None, description="HubSpot OAuth App Client ID"
    )
    hubspot_client_secret: Optional[str] = Field(
        default=None, description="HubSpot OAuth App Client Secret"
    )
    hubspot_api_key: Optional[str] = Field(
        default=None, description="HubSpot API Key (legacy, prefer OAuth)"
    )

    # -------------------------------------------------------------------------
    # Zoho CRM Configuration
    # -------------------------------------------------------------------------
    zoho_client_id: Optional[str] = Field(default=None, description="Zoho OAuth App Client ID")
    zoho_client_secret: Optional[str] = Field(
        default=None, description="Zoho OAuth App Client Secret"
    )
    zoho_region: str = Field(
        default="com", description="Zoho data center region (com, eu, in, com.au, jp, com.cn)"
    )

    # -------------------------------------------------------------------------
    # Market Intelligence Configuration
    # -------------------------------------------------------------------------
    market_intel_provider: Literal["mock", "serpapi", "dataforseo"] = Field(default="mock")
    serpapi_key: Optional[str] = Field(default=None)
    dataforseo_login: Optional[str] = Field(default=None)
    dataforseo_password: Optional[str] = Field(default=None)

    # -------------------------------------------------------------------------
    # Security Configuration
    # -------------------------------------------------------------------------
    # NOTE: All security keys MUST be set via environment variables in production
    jwt_secret_key: str = Field(
        default="",
        description="JWT signing key (REQUIRED: set via JWT_SECRET_KEY env var, min 32 chars)",
    )
    jwt_algorithm: str = Field(default="HS256")
    access_token_expire_minutes: int = Field(default=30)
    refresh_token_expire_days: int = Field(default=7)
    pii_encryption_key: str = Field(
        default="",
        description="AES encryption key for PII (REQUIRED: set via PII_ENCRYPTION_KEY env var, min 32 chars)",
    )

    # Email verification and password reset token expiry
    email_verification_expire_hours: int = Field(
        default=24, description="Hours until email verification token expires"
    )
    password_reset_expire_hours: int = Field(
        default=1, description="Hours until password reset token expires"
    )

    # -------------------------------------------------------------------------
    # Email Configuration
    # -------------------------------------------------------------------------
    smtp_host: str = Field(default="smtp.gmail.com", description="SMTP server host")
    smtp_port: int = Field(default=587, description="SMTP server port")
    smtp_user: Optional[str] = Field(default=None, description="SMTP username")
    smtp_password: Optional[str] = Field(default=None, description="SMTP password")
    smtp_tls: bool = Field(default=True, description="Use TLS for SMTP")
    smtp_ssl: bool = Field(default=False, description="Use SSL for SMTP")
    email_from_name: str = Field(default="Stratum AI", description="Email sender name")
    email_from_address: str = Field(
        default="cs@stratumai.app", description="Email sender address"
    )

    # Frontend URL for email links
    frontend_url: str = Field(
        default="http://localhost:3000", description="Frontend URL for email links"
    )

    # -------------------------------------------------------------------------
    # Slack Integration
    # -------------------------------------------------------------------------
    slack_webhook_url: Optional[str] = Field(default=None, description="Slack incoming webhook URL")
    slack_bot_token: Optional[str] = Field(default=None, description="Slack Bot OAuth token")

    # -------------------------------------------------------------------------
    # CORS Configuration
    # -------------------------------------------------------------------------
    cors_origins: str = Field(default="http://localhost:3000,http://localhost:5173")
    cors_allow_credentials: bool = Field(default=True)

    @property
    def cors_origins_list(self) -> list[str]:
        """Get CORS origins as a list."""
        return [origin.strip() for origin in self.cors_origins.split(",")]

    # -------------------------------------------------------------------------
    # Observability
    # -------------------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(default="INFO")
    log_format: Literal["json", "console"] = Field(default="json")
    sentry_dsn: Optional[str] = Field(default=None)
    prometheus_enabled: bool = Field(default=True, description="Enable Prometheus metrics endpoint")

    # -------------------------------------------------------------------------
    # Rate Limiting
    # -------------------------------------------------------------------------
    rate_limit_per_minute: int = Field(default=100)
    rate_limit_burst: int = Field(default=20)

    # -------------------------------------------------------------------------
    # Paddle Billing Configuration
    # -------------------------------------------------------------------------
    # Paddle is the merchant of record for subscriptions. Checkout runs
    # client-side (Paddle.js overlay, needs the client token); the backend uses
    # the API key for the REST API and the webhook secret to verify notifications.
    paddle_api_key: Optional[str] = Field(
        default=None, description="Paddle API key (server-side, Bearer auth for api.paddle.com)"
    )
    paddle_client_token: Optional[str] = Field(
        default=None, description="Paddle client-side token used by Paddle.js (test_... / live_...)"
    )
    paddle_webhook_secret: Optional[str] = Field(
        default=None,
        description="Paddle notification endpoint secret key (pdl_ntfset_...) for webhook signatures",
    )
    paddle_environment: Literal["sandbox", "production"] = Field(
        default="sandbox", description="Paddle environment: 'sandbox' or 'production'"
    )
    paddle_starter_price_id: Optional[str] = Field(
        default=None, description="Paddle Price ID for Starter tier (pri_...)"
    )
    paddle_professional_price_id: Optional[str] = Field(
        default=None, description="Paddle Price ID for Professional tier (pri_...)"
    )
    paddle_enterprise_price_id: Optional[str] = Field(
        default=None, description="Paddle Price ID for Enterprise tier (pri_...)"
    )

    @field_validator("paddle_environment", mode="before")
    @classmethod
    def _normalize_paddle_environment(cls, value: object) -> object:
        """Accept ``SANDBOX`` / `` production `` etc. by lower-casing and stripping."""
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized or "sandbox"
        return value

    # -------------------------------------------------------------------------
    # CDN Cache Invalidation
    # -------------------------------------------------------------------------
    cdn_provider: Optional[Literal["cloudflare", "cloudfront", "fastly"]] = Field(
        default=None, description="CDN provider for cache invalidation (cloudflare, cloudfront, fastly)"
    )
    cdn_api_key: Optional[str] = Field(
        default=None, description="CDN API token (Cloudflare API token or Fastly API token)"
    )
    cdn_zone_id: Optional[str] = Field(
        default=None,
        description="CDN zone/distribution ID (Cloudflare Zone ID or CloudFront Distribution ID)",
    )
    cdn_base_url: Optional[str] = Field(
        default=None, description="Public base URL for full purge URLs (e.g. https://blog.stratum.ai)"
    )

    # -------------------------------------------------------------------------
    # Feature Flags
    # -------------------------------------------------------------------------
    feature_competitor_intel: bool = Field(default=True)
    feature_what_if_simulator: bool = Field(default=True)
    feature_automation_rules: bool = Field(default=True)
    feature_gdpr_compliance: bool = Field(default=True)

    # -------------------------------------------------------------------------
    # File Storage (S3 / Local)
    # -------------------------------------------------------------------------
    storage_backend: str = Field(default="local", description="File storage backend (local, s3)")
    aws_access_key_id: Optional[str] = Field(default=None, description="AWS access key ID")
    aws_secret_access_key: Optional[str] = Field(default=None, description="AWS secret access key")
    aws_region: str = Field(default="us-east-1", description="AWS region")
    aws_s3_bucket: Optional[str] = Field(default=None, description="S3 bucket name for file storage")

    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.app_env == "development"

    @property
    def dev_defaults_enabled(self) -> bool:
        """
        Check whether development-only fallbacks may be applied.

        Stricter than :attr:`is_development`, which is true whenever ``app_env``
        holds its default - including when ``APP_ENV`` was never set or was
        misspelled. Security-relevant fallbacks (the tenant default in
        ``TenantMiddleware``, the fallback signing keys) key off this instead,
        so an unconfigured container fails closed.

        Returns:
            True only when APP_ENV is explicitly "development" (or the operator
            set STRATUM_ALLOW_DEV_DEFAULTS=1) and the app is not production
        """
        return self.is_development and dev_defaults_allowed()

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.app_env == "production"

    @property
    def paddle_enabled(self) -> bool:
        """Check if Paddle is configured (has an API key)."""
        return bool(self.paddle_api_key)

    @property
    def paddle_fully_configured(self) -> bool:
        """Check if all Paddle settings needed for checkout + webhooks are set."""
        return all(
            [
                self.paddle_api_key,
                self.paddle_client_token,
                self.paddle_webhook_secret,
                self.paddle_starter_price_id,
                self.paddle_professional_price_id,
                self.paddle_enterprise_price_id,
            ]
        )

    @property
    def paddle_api_base_url(self) -> str:
        """Paddle REST base URL for the configured environment."""
        if self.paddle_environment == "production":
            return PADDLE_PRODUCTION_API_BASE_URL
        return PADDLE_SANDBOX_API_BASE_URL

    @model_validator(mode="after")
    def validate_security_settings(self) -> "Settings":
        """
        Validate that security-critical settings are properly configured.

        In production:
        - Raises errors for missing/weak security keys
        - Raises errors for default/weak database passwords

        In development:
        - Issues warnings but allows startup with defaults for local testing
        """
        issues = []

        # Check for insecure database passwords
        insecure_passwords = ["changeme", "password", "123456", "admin", "root", ""]
        for url_field in ["database_url", "database_url_sync"]:
            url = getattr(self, url_field, "")
            for weak_pw in insecure_passwords:
                if f":{weak_pw}@" in url:
                    issues.append(f"{url_field} contains insecure password")
                    break

        # Check security keys
        if not self.secret_key or len(self.secret_key) < 32:
            issues.append("SECRET_KEY must be set and be at least 32 characters")

        if not self.jwt_secret_key or len(self.jwt_secret_key) < 32:
            issues.append("JWT_SECRET_KEY must be set and be at least 32 characters")

        if not self.pii_encryption_key or len(self.pii_encryption_key) < 32:
            issues.append("PII_ENCRYPTION_KEY must be set and be at least 32 characters")

        # Check for common weak keys and development fallback patterns
        weak_key_patterns = [
            "dev-secret",
            "jwt-secret-dev",
            "dev-encryption",
            "changeme",
            "secret",
            "password",
            "test",
            "demo",
            "dev-only",
            "do-not-use",
            "example",
            "placeholder",
            "your-key",
            "change-me",
            "default",
            "insecure",
        ]
        for key_field in ["secret_key", "jwt_secret_key", "pii_encryption_key"]:
            key_value = getattr(self, key_field, "").lower()
            for weak in weak_key_patterns:
                if weak in key_value:
                    issues.append(
                        f"{key_field.upper()} contains weak/default value (matched: '{weak}')"
                    )
                    break

        # Validate Paddle Billing configuration
        # If the Paddle API key is set, the companions needed for client-side
        # checkout (client token), webhooks (secret) and plan mapping (price ids)
        # must also be set. Production must additionally point at the live
        # Paddle environment (never the sandbox).
        if self.paddle_api_key:
            paddle_issues = []
            if not self.paddle_client_token:
                paddle_issues.append(
                    "PADDLE_CLIENT_TOKEN is required when PADDLE_API_KEY is set (Paddle.js checkout)"
                )
            if not self.paddle_webhook_secret:
                paddle_issues.append("PADDLE_WEBHOOK_SECRET is required for Paddle webhooks")
            if not self.paddle_starter_price_id:
                paddle_issues.append(
                    "PADDLE_STARTER_PRICE_ID is required for subscription checkout"
                )
            if not self.paddle_professional_price_id:
                paddle_issues.append(
                    "PADDLE_PROFESSIONAL_PRICE_ID is required for subscription checkout"
                )
            if not self.paddle_enterprise_price_id:
                paddle_issues.append(
                    "PADDLE_ENTERPRISE_PRICE_ID is required for subscription checkout"
                )

            # Sandbox billing must never be used in production
            if self.is_production and self.paddle_environment != "production":
                paddle_issues.append(
                    "PADDLE_ENVIRONMENT must be 'production' in production (sandbox configured)"
                )

            if paddle_issues:
                issues.extend(paddle_issues)

        # Fabricated ad metrics must never reach a paying tenant. This is a
        # production-only rejection: local development and the demo seed set
        # USE_MOCK_AD_DATA=true deliberately.
        if self.use_mock_ad_data and self.is_production:
            issues.append(
                "USE_MOCK_AD_DATA must be false in production "
                "(it writes fabricated campaign metrics into tenant data)"
            )

        if issues:
            if self.is_production:
                # In production, fail fast with clear error
                raise ValueError(
                    "SECURITY ERROR - Cannot start in production with insecure configuration:\n"
                    "  - " + "\n  - ".join(issues) + "\n\n"
                    "Please set these environment variables with secure values:\n"
                    "  - DATABASE_URL (with strong password)\n"
                    "  - SECRET_KEY (min 32 random characters)\n"
                    "  - JWT_SECRET_KEY (min 32 random characters)\n"
                    "  - PII_ENCRYPTION_KEY (min 32 random characters)\n\n"
                    'Generate secure keys with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
                )
            else:
                # In development, warn but allow startup
                warning_msg = (
                    f"\n{'='*60}\n"
                    f"SECURITY WARNING - Development mode with insecure defaults:\n"
                    f"  - " + "\n  - ".join(issues) + "\n"
                    f"{'='*60}\n"
                    f"This is OK for local development but NEVER use in production!\n"
                    f"Set APP_ENV=production to enforce security requirements.\n"
                    f"{'='*60}\n"
                )
                warnings.warn(warning_msg, UserWarning, stacklevel=2)

        return self


# Development-only fallback keys (only used when APP_ENV != production)
_DEV_FALLBACK_KEYS = {
    "secret_key": "dev-only-secret-key-do-not-use-in-production-32chars",
    "jwt_secret_key": "dev-only-jwt-secret-do-not-use-in-production-32",
    "pii_encryption_key": "dev-only-pii-key-do-not-use-in-prod-32ch",
}


@lru_cache
def get_settings() -> Settings:
    """
    Get cached settings instance.

    In development mode, provides fallback values for required keys
    to allow local testing without full configuration.

    The fallback keys are source-controlled constants: anyone can sign a JWT
    with them. They are therefore injected only when APP_ENV is *explicitly*
    "development" (or STRATUM_ALLOW_DEV_DEFAULTS=1 is set) - never for
    staging/production, and never when APP_ENV is missing or misspelled, which
    used to fall through to the development branch and hand out the published
    signing key.

    Returns:
        The cached Settings instance
    """
    configured_env = explicit_app_env()

    if dev_defaults_allowed():
        # For development, set fallback values if not provided
        for key, fallback in _DEV_FALLBACK_KEYS.items():
            env_key = key.upper()
            if not os.getenv(env_key):
                os.environ[env_key] = fallback
    elif configured_env is None:
        warnings.warn(
            "APP_ENV is not set. Refusing to inject development fallback keys: "
            "set APP_ENV=development for a local box (or "
            f"{DEV_DEFAULTS_OVERRIDE_ENV}=1), and SECRET_KEY / JWT_SECRET_KEY / "
            "PII_ENCRYPTION_KEY on every deployed environment.",
            UserWarning,
            stacklevel=2,
        )

    try:
        return Settings()
    except ValidationError as exc:
        if configured_env is not None:
            raise
        # Fail fast with an actionable message rather than the raw pydantic
        # error: this is the "APP_ENV was never set" path, which used to fall
        # through to the development branch and adopt the published signing
        # keys, so that anyone could forge an access token.
        raise RuntimeError(
            "APP_ENV is not set and the required secrets are missing.\n"
            "  - deployed environments: set APP_ENV (staging|production) plus "
            "SECRET_KEY, JWT_SECRET_KEY and PII_ENCRYPTION_KEY\n"
            "  - local development: set APP_ENV=development (or "
            f"{DEV_DEFAULTS_OVERRIDE_ENV}=1) to use the development fallbacks\n"
            f"Underlying error: {exc}"
        ) from exc


# Global settings instance
settings = get_settings()
