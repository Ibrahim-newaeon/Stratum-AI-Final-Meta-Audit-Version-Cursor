# =============================================================================
# Stratum AI - Measurement & Verification Schemas (GA4 + GTM)
# =============================================================================
"""
Pydantic schemas for the Measurement & Verification integrations.

Positioning (see shared contracts):

- Google Analytics 4 is a **read-only, independent revenue/conversion baseline**
  pulled through the GA4 Data API with a service account
  (scope ``https://www.googleapis.com/auth/analytics.readonly``). It feeds
  attribution variance, EMQ, signal health and the Trust Gate.
- Google Tag Manager is a **tag deployment** integration: a web container for
  the Meta Pixel + Stratum snippet and a server-side tagging endpoint for the
  Meta Conversions API + the CDP ``sgtm`` source.

Neither is an ad platform or channel. Stratum AI only acts on Meta channels.

Security: response models in this module never expose the service-account
JSON or the GTM preview header. Those secrets are stored encrypted and only
surfaced as ``has_credentials`` / ``has_preview_header`` booleans plus a short
non-reversible fingerprint.
"""

import json
import re
from datetime import datetime
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# =============================================================================
# Constants & validation helpers
# =============================================================================

GA4_READONLY_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GA4_ACCESS: Literal["read_only"] = "read_only"
GTM_ROLE: Literal["tag_deployment"] = "tag_deployment"

PROPERTY_ID_RE = re.compile(r"^[0-9]{1,64}$")
MEASUREMENT_ID_RE = re.compile(r"^G-[A-Z0-9]{4,12}$")
GTM_CONTAINER_ID_RE = re.compile(r"^GTM-[A-Z0-9]{4,10}$")
META_PIXEL_ID_RE = re.compile(r"^[0-9]{1,64}$")

MAX_CONVERSION_EVENTS = 20
MAX_CONVERSION_EVENT_NAME_LENGTH = 255
MAX_SERVER_CONTAINER_URL_LENGTH = 2048
MAX_PREVIEW_HEADER_LENGTH = 512

MeasurementConnectionStatus = Literal["connected", "error", "disconnected"]


def validate_property_id(value: str) -> str:
    """Validate a GA4 numeric property ID (digits only, 1-64 chars)."""
    candidate = (value or "").strip()
    if not PROPERTY_ID_RE.match(candidate):
        raise ValueError("property_id must be the numeric GA4 property ID (digits only)")
    return candidate


def validate_measurement_id(value: str) -> str:
    """Validate a GA4 web stream measurement ID (``G-XXXXXXXX``)."""
    candidate = (value or "").strip().upper()
    if not MEASUREMENT_ID_RE.match(candidate):
        raise ValueError("measurement_id must look like G-XXXXXXXX")
    return candidate


def validate_gtm_container_id(value: str) -> str:
    """Validate a GTM container ID (``GTM-XXXXXXX``, upper-case)."""
    candidate = (value or "").strip().upper()
    if not GTM_CONTAINER_ID_RE.match(candidate):
        raise ValueError("Container ID must look like GTM-XXXXXXX")
    return candidate


def validate_https_url(value: str) -> str:
    """Validate an https-only URL and strip any trailing slash."""
    candidate = (value or "").strip()
    if len(candidate) > MAX_SERVER_CONTAINER_URL_LENGTH:
        raise ValueError("URL is too long")
    parsed = urlparse(candidate)
    if parsed.scheme != "https":
        raise ValueError("URL must use https://")
    if not parsed.netloc:
        raise ValueError("URL must include a host")
    if parsed.query or parsed.fragment:
        raise ValueError("URL must not contain a query string or fragment")
    return candidate.rstrip("/")


def validate_service_account_json(value: str) -> str:
    """
    Validate that a service-account JSON blob is structurally a service account.

    Only the structure is validated here; credentials are never logged and are
    parsed into a client by ``GA4DataClient.from_service_account_json``.
    """
    candidate = (value or "").strip()
    if not candidate:
        raise ValueError("service_account_json must not be empty")
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError("service_account_json must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("service_account_json must be a JSON object")
    if not parsed.get("client_email") or not parsed.get("private_key"):
        raise ValueError("service_account_json must contain client_email and private_key")
    return candidate


def _normalize_conversion_events(values: list[str]) -> list[str]:
    """Trim, de-duplicate (order-preserving) and bound conversion event names."""
    cleaned: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError("conversion_event_names must be strings")
        name = raw.strip()
        if not name:
            raise ValueError("conversion_event_names must not contain empty strings")
        if len(name) > MAX_CONVERSION_EVENT_NAME_LENGTH:
            raise ValueError("conversion event names must be at most 255 characters")
        if name not in cleaned:
            cleaned.append(name)
    if not cleaned:
        raise ValueError("at least one conversion event name is required")
    if len(cleaned) > MAX_CONVERSION_EVENTS:
        raise ValueError(f"at most {MAX_CONVERSION_EVENTS} conversion event names are allowed")
    return cleaned


def _blank_to_none(value: Optional[str]) -> Optional[str]:
    """Treat empty / whitespace-only strings as ``None``."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class MeasurementSchema(BaseModel):
    """Base schema for measurement models."""

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# GA4 - Google Analytics 4 (read-only baseline)
# =============================================================================


class GA4ConfigRequest(MeasurementSchema):
    """Create or update the tenant's GA4 read-only baseline configuration."""

    property_id: str = Field(..., description="Numeric GA4 property ID")
    measurement_id: Optional[str] = Field(
        default=None, description="GA4 web stream measurement ID (G-XXXXXXXX)"
    )
    service_account_json: Optional[str] = Field(
        default=None,
        description=(
            "Service account key JSON with Viewer access on the property. "
            "Omit to keep the stored credentials."
        ),
    )
    conversion_event_names: list[str] = Field(
        default_factory=lambda: ["purchase"],
        description="GA4 event names counted as conversions",
    )
    is_active: bool = Field(default=True)

    @field_validator("property_id")
    @classmethod
    def _validate_property_id(cls, value: str) -> str:
        return validate_property_id(value)

    @field_validator("measurement_id")
    @classmethod
    def _validate_measurement_id(cls, value: Optional[str]) -> Optional[str]:
        value = _blank_to_none(value)
        return validate_measurement_id(value) if value else None

    @field_validator("service_account_json")
    @classmethod
    def _validate_service_account_json(cls, value: Optional[str]) -> Optional[str]:
        value = _blank_to_none(value)
        return validate_service_account_json(value) if value else None

    @field_validator("conversion_event_names")
    @classmethod
    def _validate_conversion_event_names(cls, value: list[str]) -> list[str]:
        return _normalize_conversion_events(value)


class GA4ConfigResponse(MeasurementSchema):
    """Stored GA4 configuration. Never includes the service-account JSON."""

    configured: Literal[True] = True
    property_id: str
    measurement_id: Optional[str] = None
    service_account_email: Optional[str] = None
    service_account_fingerprint: Optional[str] = None
    has_credentials: bool = False
    conversion_event_names: list[str] = Field(default_factory=lambda: ["purchase"])
    status: MeasurementConnectionStatus = "disconnected"
    is_active: bool = True
    last_verified_at: Optional[datetime] = None
    last_verify_success: Optional[bool] = None
    last_verify_message: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    last_sync_rows: Optional[int] = None
    last_error: Optional[str] = None
    scope: str = GA4_READONLY_SCOPE
    access: Literal["read_only"] = GA4_ACCESS
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class GA4StatusResponse(MeasurementSchema):
    """Compact GA4 status used by the combined measurement status endpoint."""

    configured: bool = False
    status: MeasurementConnectionStatus = "disconnected"
    property_id: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    last_verified_at: Optional[datetime] = None
    last_error: Optional[str] = None
    access: Literal["read_only"] = GA4_ACCESS


class GA4TestRequest(MeasurementSchema):
    """Optional overrides for a GA4 connection test (falls back to stored config)."""

    property_id: Optional[str] = None
    service_account_json: Optional[str] = None

    @field_validator("property_id")
    @classmethod
    def _validate_property_id(cls, value: Optional[str]) -> Optional[str]:
        value = _blank_to_none(value)
        return validate_property_id(value) if value else None

    @field_validator("service_account_json")
    @classmethod
    def _validate_service_account_json(cls, value: Optional[str]) -> Optional[str]:
        value = _blank_to_none(value)
        return validate_service_account_json(value) if value else None


class GA4TestResponse(MeasurementSchema):
    """Result of a GA4 Data API read-only connection test."""

    success: bool
    message: str
    property_id: Optional[str] = None
    sessions_last_7d: Optional[int] = None
    conversions_last_7d: Optional[int] = None
    revenue_last_7d: Optional[float] = None


class GA4SyncRequest(MeasurementSchema):
    """Manual GA4 baseline sync options."""

    lookback_days: Optional[int] = Field(default=None, ge=1, le=90)
    backfill: bool = Field(default=False, description="Backfill the configured backfill window")


class GA4SyncResponse(MeasurementSchema):
    """Outcome of a GA4 daily baseline sync."""

    configured: bool
    success: bool
    rows_upserted: int = 0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    message: str = ""


class GA4DailyPoint(MeasurementSchema):
    """One day of GA4 baseline data."""

    date: str
    sessions: int = 0
    conversions: int = 0
    revenue: float = 0.0


class GA4BaselineResponse(MeasurementSchema):
    """GA4 baseline summary over a date range (independent verification data)."""

    start_date: str
    end_date: str
    meta_only: bool = False
    sessions: int = 0
    conversions: int = 0
    revenue: float = 0.0
    total_revenue: float = 0.0
    days_with_data: int = 0
    last_date: Optional[str] = None
    daily: list[GA4DailyPoint] = Field(default_factory=list)


# =============================================================================
# GTM - Google Tag Manager (tag deployment)
# =============================================================================


class GTMConfigRequest(MeasurementSchema):
    """Create or update the tenant's GTM tag-deployment configuration."""

    web_container_id: Optional[str] = Field(default=None, description="GTM-XXXXXXX")
    server_container_url: Optional[str] = Field(
        default=None, description="Server-side tagging endpoint (https://...)"
    )
    server_container_id: Optional[str] = Field(default=None, description="GTM-XXXXXXX")
    preview_header: Optional[str] = Field(
        default=None,
        description="Optional X-Gtm-Server-Preview header. Omit to keep the stored value.",
    )
    meta_pixel_id: Optional[str] = Field(default=None, description="Meta Pixel ID")
    deploy_meta_pixel: bool = Field(default=True)
    deploy_meta_capi: bool = Field(default=True)
    deploy_stratum_snippet: bool = Field(default=True)
    is_active: bool = Field(default=True)

    @field_validator("web_container_id", "server_container_id")
    @classmethod
    def _validate_container_id(cls, value: Optional[str]) -> Optional[str]:
        value = _blank_to_none(value)
        return validate_gtm_container_id(value) if value else None

    @field_validator("server_container_url")
    @classmethod
    def _validate_server_container_url(cls, value: Optional[str]) -> Optional[str]:
        value = _blank_to_none(value)
        return validate_https_url(value) if value else None

    @field_validator("preview_header")
    @classmethod
    def _validate_preview_header(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        if len(stripped) > MAX_PREVIEW_HEADER_LENGTH:
            raise ValueError("preview_header is too long")
        # Empty string is meaningful: it clears the stored header.
        return stripped

    @field_validator("meta_pixel_id")
    @classmethod
    def _validate_meta_pixel_id(cls, value: Optional[str]) -> Optional[str]:
        value = _blank_to_none(value)
        if value and not META_PIXEL_ID_RE.match(value):
            raise ValueError("meta_pixel_id must be numeric")
        return value

    @model_validator(mode="after")
    def _require_a_container(self) -> "GTMConfigRequest":
        if not self.web_container_id and not self.server_container_url:
            raise ValueError(
                "Provide a web container ID and/or a server-side tagging endpoint"
            )
        return self


class GTMConfigResponse(MeasurementSchema):
    """Stored GTM configuration. Never includes the preview header value."""

    configured: Literal[True] = True
    web_container_id: Optional[str] = None
    server_container_url: Optional[str] = None
    server_container_id: Optional[str] = None
    has_preview_header: bool = False
    meta_pixel_id: Optional[str] = None
    deploy_meta_pixel: bool = True
    deploy_meta_capi: bool = True
    deploy_stratum_snippet: bool = True
    status: MeasurementConnectionStatus = "disconnected"
    is_active: bool = True
    cdp_source_id: Optional[str] = None
    cdp_source_key: Optional[str] = None
    last_verified_at: Optional[datetime] = None
    last_verify_success: Optional[bool] = None
    last_verify_message: Optional[str] = None
    last_error: Optional[str] = None
    role: Literal["tag_deployment"] = GTM_ROLE
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class GTMStatusResponse(MeasurementSchema):
    """Compact GTM status used by the combined measurement status endpoint."""

    configured: bool = False
    status: MeasurementConnectionStatus = "disconnected"
    web_container_id: Optional[str] = None
    server_container_url: Optional[str] = None
    last_verified_at: Optional[datetime] = None
    role: Literal["tag_deployment"] = GTM_ROLE


class GTMVerifyResponse(MeasurementSchema):
    """Result of verifying that the configured containers are reachable."""

    success: bool
    message: str
    web_container_ok: Optional[bool] = None
    server_container_ok: Optional[bool] = None
    checked_at: str


class GTMSnippetsResponse(MeasurementSchema):
    """Ready-to-paste GTM snippets plus the server-side tagging configuration."""

    head_snippet: str
    body_snippet: str
    stratum_snippet: str
    sgtm_config: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Combined status
# =============================================================================


class MeasurementStatusResponse(MeasurementSchema):
    """Status of both Measurement & Verification integrations."""

    ga4: GA4StatusResponse = Field(default_factory=GA4StatusResponse)
    gtm: GTMStatusResponse = Field(default_factory=GTMStatusResponse)


__all__ = [
    "GA4_ACCESS",
    "GA4_READONLY_SCOPE",
    "GTM_CONTAINER_ID_RE",
    "GTM_ROLE",
    "MEASUREMENT_ID_RE",
    "PROPERTY_ID_RE",
    "GA4BaselineResponse",
    "GA4ConfigRequest",
    "GA4ConfigResponse",
    "GA4DailyPoint",
    "GA4StatusResponse",
    "GA4SyncRequest",
    "GA4SyncResponse",
    "GA4TestRequest",
    "GA4TestResponse",
    "GTMConfigRequest",
    "GTMConfigResponse",
    "GTMSnippetsResponse",
    "GTMStatusResponse",
    "GTMVerifyResponse",
    "MeasurementConnectionStatus",
    "MeasurementStatusResponse",
    "validate_gtm_container_id",
    "validate_https_url",
    "validate_measurement_id",
    "validate_property_id",
    "validate_service_account_json",
]
