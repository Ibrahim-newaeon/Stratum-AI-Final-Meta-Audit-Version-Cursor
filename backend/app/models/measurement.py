# =============================================================================
# Stratum AI - Measurement & Verification Models (GA4 read-only + GTM tagging)
# =============================================================================
"""
Database models for the Measurement & Verification integrations.

These integrations are NOT ad channels. Stratum AI acts only on Meta channels
(Facebook, Instagram, WhatsApp); GA4 and GTM exist purely for measurement:

- TenantGA4Integration: per-tenant Google Analytics 4 property + service
  account (read-only GA4 Data API, scope analytics.readonly). Provides the
  independent revenue/conversion baseline used for attribution variance,
  EMQ and the Trust Gate.
- TenantGTMIntegration: per-tenant Google Tag Manager web container and
  server-side tagging endpoint used to deploy Meta Pixel / Conversions API
  and the Stratum tracking snippet. Linked to the CDP 'sgtm' source.
- FactGA4Daily: daily GA4 sessions / conversions / purchase revenue by
  date x source/medium/campaign, with Meta-traffic classification.

Tables are created by ``backend/scripts_create_measurement_tables.py``
(no Alembic migration). Secrets are stored encrypted via
``app.services.encryption.encrypt_token`` and never returned by the API.
"""

from datetime import UTC, date, datetime
from enum import Enum
from uuid import UUID as PyUUID
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base, TimestampMixin

# =============================================================================
# Enums
# =============================================================================


class MeasurementProvider(str, Enum):
    """Measurement-only providers (never ad platforms)."""

    GA4 = "ga4"
    GTM = "gtm"


class MeasurementStatus(str, Enum):
    """Connection status of a measurement integration."""

    CONNECTED = "connected"
    ERROR = "error"
    DISCONNECTED = "disconnected"


def _default_conversion_events() -> list[str]:
    """Default GA4 conversion event names."""
    return ["purchase"]


# =============================================================================
# GA4 (read-only baseline)
# =============================================================================


class TenantGA4Integration(Base, TimestampMixin):
    """
    Per-tenant Google Analytics 4 configuration (read-only GA4 Data API).

    One record per tenant. The service-account JSON is stored Fernet-encrypted
    (``service_account_json_encrypted``) and identified by a short fingerprint.
    """

    __tablename__ = "tenant_ga4_integrations"

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    # GA4 property
    property_id: Mapped[str] = mapped_column(String(64), nullable=False)
    measurement_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # Service account (read-only scope) - JSON encrypted at rest
    service_account_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    service_account_json_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_account_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Which GA4 events count as conversions for the baseline
    conversion_event_names: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=_default_conversion_events,
        server_default=text("'[\"purchase\"]'::jsonb"),
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=MeasurementStatus.DISCONNECTED.value,
        server_default=text("'disconnected'"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true")
    )

    # Verification (test connection)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_verify_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_verify_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Sync bookkeeping
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", name="uq_tenant_ga4_integrations_tenant"),)

    @property
    def has_credentials(self) -> bool:
        """Whether an encrypted service-account JSON is stored."""
        return bool(self.service_account_json_encrypted)

    def __repr__(self) -> str:
        return f"<TenantGA4Integration tenant={self.tenant_id} property={self.property_id}>"


# =============================================================================
# GTM (tag deployment)
# =============================================================================


class TenantGTMIntegration(Base, TimestampMixin):
    """
    Per-tenant Google Tag Manager configuration (tag deployment only).

    Holds the web container ID (Meta Pixel + Stratum snippet) and the
    server-side tagging endpoint (Meta Conversions API + CDP 'sgtm' source).
    """

    __tablename__ = "tenant_gtm_integrations"

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    # Containers
    web_container_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    server_container_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    server_container_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    preview_header_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # What the containers deploy
    meta_pixel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deploy_meta_pixel: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true")
    )
    deploy_meta_capi: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true")
    )
    deploy_stratum_snippet: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true")
    )

    # Linked CDP source (source_type='sgtm')
    cdp_source_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cdp_sources.id", ondelete="SET NULL"), nullable=True
    )

    # Status
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=MeasurementStatus.DISCONNECTED.value,
        server_default=text("'disconnected'"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, server_default=text("true")
    )

    # Verification
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_verify_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_verify_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("tenant_id", name="uq_tenant_gtm_integrations_tenant"),)

    @property
    def has_preview_header(self) -> bool:
        """Whether an encrypted preview header is stored."""
        return bool(self.preview_header_encrypted)

    def __repr__(self) -> str:
        return f"<TenantGTMIntegration tenant={self.tenant_id} web={self.web_container_id}>"


# =============================================================================
# GA4 daily fact table
# =============================================================================


class FactGA4Daily(Base):
    """
    Daily GA4 baseline: sessions, conversions and purchase revenue per
    date x sessionSource x sessionMedium x sessionCampaignName.

    ``revenue`` holds GA4 purchaseRevenue so the analytics query
    ``BLENDED_ROAS_BY_DAY`` (``fg.revenue``) keeps working; ``total_revenue``
    holds GA4 totalRevenue. ``is_meta_traffic`` / ``meta_channel`` are set by
    ``app.services.measurement.ga4_ingestion.classify_meta_traffic``.
    """

    __tablename__ = "fact_ga4_daily"

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    property_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Traffic dimensions (GA4 session-scoped)
    utm_source: Mapped[str] = mapped_column(
        String(255), nullable=False, default="(not set)", server_default=text("'(not set)'")
    )
    utm_medium: Mapped[str] = mapped_column(
        String(255), nullable=False, default="(not set)", server_default=text("'(not set)'")
    )
    utm_campaign: Mapped[str] = mapped_column(
        String(255), nullable=False, default="(not set)", server_default=text("'(not set)'")
    )

    # Meta classification (facebook | instagram | whatsapp)
    is_meta_traffic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    meta_channel: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Metrics
    sessions: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    conversions: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    revenue: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"))
    total_revenue: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0"))
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)

    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        server_default=text("now()"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "date",
            "property_id",
            "utm_source",
            "utm_medium",
            "utm_campaign",
            name="uq_fact_ga4_daily_dim",
        ),
        Index("ix_fact_ga4_daily_tenant_date", "tenant_id", "date"),
    )

    def __repr__(self) -> str:
        return (
            f"<FactGA4Daily tenant={self.tenant_id} date={self.date} "
            f"{self.utm_source}/{self.utm_medium}/{self.utm_campaign}>"
        )
