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

import warnings
from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PADDLE_SANDBOX_API_BASE_URL = "https://sandbox-api.paddle.com"
PADDLE_PRODUCTION_API_BASE_URL = "https://api.paddle.com"


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
    # Ad Platform Configuration
    # -------------------------------------------------------------------------
    use_mock_ad_data: bool = Field(
        default=True, description="Use mock data instead of real ad platform APIs"
    )

    # OAuth callback base URL (for constructing redirect URIs)
    oauth_redirect_base_url: str = Field(
        default="http://localhost:8000", description="Base URL for OAuth callbacks"
    )

    # Meta/Facebook OAuth
    meta_app_id: Optional[str] = Field(default=None, description="Meta/Facebook App ID")
    meta_app_secret: Optional[str] = Field(default=None, description="Meta/Facebook App Secret")
    meta_access_token: Optional[str] = Field(default=None)
    meta_api_version: str = Field(default="v19.0", description="Meta Graph API version")

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
    """
    import os

    # Check if we're in production before creating settings
    app_env = os.getenv("APP_ENV", "development").lower()

    if app_env != "production":
        # For development, set fallback values if not provided
        for key, fallback in _DEV_FALLBACK_KEYS.items():
            env_key = key.upper()
            if not os.getenv(env_key):
                os.environ[env_key] = fallback

    return Settings()


# Global settings instance
settings = get_settings()
