# =============================================================================
# Stratum AI - CDP Pydantic Schemas
# =============================================================================
"""
Request/response schemas for the CDP (Customer Data Platform) API.

Covers event ingestion, profiles, identity resolution, sources, webhooks,
segments, computed traits, RFM analysis, and funnel/journey analytics.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# =============================================================================
# Shared base
# =============================================================================


class CDPSchema(BaseModel):
    """Base schema for CDP models."""

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Event Ingestion
# =============================================================================


class EventIdentifierInput(CDPSchema):
    """An identifier attached to an incoming event."""

    type: str = Field(..., max_length=50, description="Identifier type (email, phone, ...)")
    value: str = Field(..., max_length=1024, description="Raw identifier value")


class EventContextInput(CDPSchema):
    """Contextual metadata for an incoming event."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    ip: Optional[str] = None
    user_agent: Optional[str] = None
    page_url: Optional[str] = None
    referrer: Optional[str] = None
    campaign: Optional[str] = None
    source: Optional[str] = None
    medium: Optional[str] = None
    locale: Optional[str] = None


class EventConsentInput(CDPSchema):
    """Consent flags supplied with an event."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    analytics: Optional[bool] = None
    marketing: Optional[bool] = None
    advertising: Optional[bool] = None
    functional: Optional[bool] = None


class EventInput(CDPSchema):
    """A single event to ingest."""

    event_name: str = Field(..., min_length=1, max_length=255)
    event_time: datetime
    idempotency_key: Optional[str] = Field(None, max_length=255)
    identifiers: list[EventIdentifierInput] = Field(..., min_length=1)
    properties: Optional[dict[str, Any]] = None
    context: Optional[EventContextInput] = None
    consent: Optional[EventConsentInput] = None


class EventBatchInput(CDPSchema):
    """A batch of events to ingest."""

    events: list[EventInput] = Field(..., min_length=1, max_length=1000)


class EventIngestResult(CDPSchema):
    """Per-event ingestion result."""

    event_id: Optional[UUID] = None
    status: str = Field(..., description="accepted, rejected, or duplicate")
    profile_id: Optional[UUID] = None
    error: Optional[str] = None


class EventBatchResponse(CDPSchema):
    """Summary of a batch ingestion request."""

    accepted: int
    rejected: int
    duplicates: int
    results: list[EventIngestResult] = Field(default_factory=list)


# =============================================================================
# Profiles & Identifiers
# =============================================================================


class IdentifierResponse(CDPSchema):
    """A profile identifier."""

    id: UUID
    identifier_type: str
    identifier_value: Optional[str] = None
    identifier_hash: str
    is_primary: bool = False
    confidence_score: Optional[float] = None
    verified_at: Optional[datetime] = None
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None


class ProfileResponse(CDPSchema):
    """A unified customer profile."""

    id: UUID
    tenant_id: int
    external_id: Optional[str] = None
    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    profile_data: dict[str, Any] = Field(default_factory=dict)
    computed_traits: dict[str, Any] = Field(default_factory=dict)
    lifecycle_stage: str = "anonymous"
    total_events: int = 0
    total_sessions: int = 0
    total_purchases: int = 0
    total_revenue: float = 0.0
    identifiers: list[IdentifierResponse] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ProfileDeletionResponse(CDPSchema):
    """Result of a GDPR profile deletion."""

    profile_id: UUID
    deleted: bool
    events_deleted: int = 0
    identifiers_deleted: int = 0
    consents_deleted: int = 0
    segment_memberships_deleted: int = 0
    deletion_timestamp: datetime


# =============================================================================
# Identity Graph & Merges
# =============================================================================


class IdentityGraphNode(CDPSchema):
    """A node (identifier) in the identity graph."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    id: str
    type: Optional[str] = None
    hash: Optional[str] = None
    is_primary: bool = False
    priority: Optional[int] = None


class IdentityGraphEdge(CDPSchema):
    """An edge (link) in the identity graph."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    source: str
    target: str
    type: Optional[str] = None
    confidence: Optional[float] = None


class IdentityGraphResponse(CDPSchema):
    """Identity graph for a profile."""

    profile_id: UUID
    nodes: list[IdentityGraphNode] = Field(default_factory=list)
    edges: list[IdentityGraphEdge] = Field(default_factory=list)
    total_identifiers: int = 0
    total_links: int = 0


class CanonicalIdentityResponse(CDPSchema):
    """The canonical (strongest) identity for a profile."""

    id: UUID
    profile_id: UUID
    canonical_type: str
    canonical_value_hash: str
    priority_score: Optional[int] = None
    is_verified: bool = False
    verified_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ProfileMergeRequest(CDPSchema):
    """Request to manually merge two profiles."""

    source_profile_id: UUID = Field(..., description="Profile to merge (will be removed)")
    target_profile_id: UUID = Field(..., description="Surviving profile")


class ProfileMergeResponse(CDPSchema):
    """Record of a profile merge."""

    id: UUID
    surviving_profile_id: UUID
    merged_profile_id: UUID
    merge_reason: str
    merged_event_count: int = 0
    merged_identifier_count: int = 0
    is_rolled_back: bool = False
    created_at: Optional[datetime] = None


class ProfileMergeHistoryResponse(CDPSchema):
    """List of profile merges."""

    merges: list[ProfileMergeResponse] = Field(default_factory=list)
    total: int = 0


# =============================================================================
# Sources
# =============================================================================

# Allowed source types. Mirrors ``app.models.cdp.SourceType`` values (kept as
# plain strings here so schemas stay free of SQLAlchemy imports).
SOURCE_TYPE_VALUES: tuple[str, ...] = ("website", "server", "sgtm", "import", "crm")

# Human-readable labels (frontend mirror: CDP_SOURCE_TYPE_LABELS in src/api/cdp.ts).
SOURCE_TYPE_LABELS: dict[str, str] = {
    "website": "Website (JavaScript SDK)",
    "server": "Server-side API",
    "sgtm": "Server-side GTM",
    "import": "CSV / bulk import",
    "crm": "CRM sync",
}


class SourceCreate(CDPSchema):
    """Request to create a data source."""

    name: str = Field(..., min_length=1, max_length=255)
    source_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="One of: " + ", ".join(SOURCE_TYPE_VALUES),
    )
    config: Optional[dict[str, Any]] = None

    @field_validator("source_type")
    @classmethod
    def _validate_source_type(cls, value: str) -> str:
        """Accept only known ``SourceType`` values (case-insensitive, normalized)."""
        normalized = (value or "").strip().lower()
        if normalized not in SOURCE_TYPE_VALUES:
            raise ValueError(
                f"source_type must be one of: {', '.join(SOURCE_TYPE_VALUES)}"
            )
        return normalized


class SourceResponse(CDPSchema):
    """A configured data source."""

    id: UUID
    name: str
    source_type: str
    source_key: str
    config: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    event_count: int = 0
    last_event_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SourceListResponse(CDPSchema):
    """List of data sources."""

    sources: list[SourceResponse] = Field(default_factory=list)
    total: int = 0


# =============================================================================
# Webhooks
# =============================================================================


class WebhookCreate(CDPSchema):
    """Request to create a webhook destination."""

    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=1, max_length=2048)
    event_types: list[str] = Field(default_factory=list)
    max_retries: int = Field(default=3, ge=0, le=10)
    timeout_seconds: int = Field(default=10, ge=1, le=120)


class WebhookUpdate(CDPSchema):
    """Partial webhook update."""

    name: Optional[str] = Field(None, max_length=255)
    url: Optional[str] = Field(None, max_length=2048)
    event_types: Optional[list[str]] = None
    is_active: Optional[bool] = None
    max_retries: Optional[int] = Field(None, ge=0, le=10)
    timeout_seconds: Optional[int] = Field(None, ge=1, le=120)


class WebhookResponse(CDPSchema):
    """A webhook destination."""

    id: UUID
    name: str
    url: str
    event_types: list[str] = Field(default_factory=list)
    secret_key: Optional[str] = Field(None, description="Only returned on create/rotate")
    is_active: bool = True
    last_triggered_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    failure_count: int = 0
    max_retries: int = 3
    timeout_seconds: int = 10
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class WebhookListResponse(CDPSchema):
    """List of webhooks."""

    webhooks: list[WebhookResponse] = Field(default_factory=list)
    total: int = 0


class WebhookTestResult(CDPSchema):
    """Result of a webhook test delivery."""

    success: bool
    status_code: Optional[int] = None
    response_time_ms: float = 0.0
    error: Optional[str] = None


# =============================================================================
# Segments
# =============================================================================


class SegmentRules(CDPSchema):
    """Rule tree for dynamic segment evaluation."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    logic: str = Field(default="and", description="Top-level combinator: and/or")
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    groups: list[dict[str, Any]] = Field(default_factory=list)


class SegmentCreate(CDPSchema):
    """Request to create a segment."""

    name: str = Field(..., min_length=1, max_length=255)
    rules: SegmentRules
    segment_type: str = Field(default="dynamic", description="static, dynamic, or computed")
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    auto_refresh: bool = False
    refresh_interval_hours: Optional[int] = Field(default=24, ge=1, le=168)


class SegmentUpdate(CDPSchema):
    """Partial segment update."""

    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    rules: Optional[SegmentRules] = None
    tags: Optional[list[str]] = None
    auto_refresh: Optional[bool] = None
    refresh_interval_hours: Optional[int] = Field(None, ge=1, le=168)


class SegmentResponse(CDPSchema):
    """A customer segment."""

    id: UUID
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None
    segment_type: str = "dynamic"
    status: str = "draft"
    rules: dict[str, Any] = Field(default_factory=dict)
    profile_count: int = 0
    last_computed_at: Optional[datetime] = None
    computation_duration_ms: Optional[int] = None
    auto_refresh: bool = False
    refresh_interval_hours: Optional[int] = None
    next_refresh_at: Optional[datetime] = None
    tags: list[str] = Field(default_factory=list)
    created_by_user_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class SegmentListResponse(CDPSchema):
    """List of segments."""

    segments: list[SegmentResponse] = Field(default_factory=list)
    total: int = 0


class SegmentPreviewRequest(CDPSchema):
    """Request to preview segment membership without saving."""

    rules: SegmentRules
    limit: int = Field(default=10, ge=1, le=100)


class SegmentPreviewResponse(CDPSchema):
    """Preview of profiles matching segment rules."""

    estimated_count: int = 0
    sample_profiles: list[ProfileResponse] = Field(default_factory=list)


class SegmentProfilesResponse(CDPSchema):
    """Profiles belonging to a segment."""

    profiles: list[ProfileResponse] = Field(default_factory=list)
    total: int = 0


class ProfileSegmentsResponse(CDPSchema):
    """Segments a profile belongs to."""

    segments: list[SegmentResponse] = Field(default_factory=list)


# =============================================================================
# Computed Traits
# =============================================================================


class TraitSourceConfig(CDPSchema):
    """Configuration describing how a trait is computed."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    event_name: Optional[str] = None
    aggregation: Optional[str] = None
    property: Optional[str] = None
    window_days: Optional[int] = None


class ComputedTraitCreate(CDPSchema):
    """Request to create a computed trait definition."""

    name: str = Field(..., min_length=1, max_length=255)
    display_name: str = Field(..., min_length=1, max_length=255)
    trait_type: str = Field(..., description="e.g. aggregation, computed, sql")
    source_config: TraitSourceConfig
    description: Optional[str] = None
    output_type: str = Field(default="string")
    default_value: Optional[Any] = None


class ComputedTraitResponse(CDPSchema):
    """A computed trait definition."""

    id: UUID
    name: str
    display_name: str
    description: Optional[str] = None
    trait_type: str
    source_config: dict[str, Any] = Field(default_factory=dict)
    output_type: str = "string"
    default_value: Optional[Any] = None
    is_active: bool = True
    last_computed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ComputedTraitListResponse(CDPSchema):
    """List of computed trait definitions."""

    traits: list[ComputedTraitResponse] = Field(default_factory=list)
    total: int = 0


class ComputeTraitsResponse(CDPSchema):
    """Result of a batch trait computation."""

    profiles_processed: int = 0
    errors: int = 0


# =============================================================================
# RFM Analysis
# =============================================================================


class RFMConfig(CDPSchema):
    """Configuration for RFM computation."""

    purchase_event_name: str = "Purchase"
    revenue_property: str = "total"
    analysis_window_days: int = Field(default=365, ge=30, le=1095)


class RFMScores(CDPSchema):
    """RFM scores for a single profile."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    recency_days: Optional[int] = None
    frequency: int = 0
    monetary: float = 0.0
    recency_score: int = 0
    frequency_score: int = 0
    monetary_score: int = 0
    rfm_score: int = 0
    rfm_segment: Optional[str] = None


class RFMBatchResponse(CDPSchema):
    """Result of batch RFM computation."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    profiles_processed: int = 0
    segment_distribution: dict[str, int] = Field(default_factory=dict)
    analysis_window_days: Optional[int] = None
    calculated_at: Optional[str] = None


class RFMSummaryResponse(CDPSchema):
    """RFM segment distribution summary."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    total_profiles: int = 0
    profiles_with_rfm: int = 0
    segment_distribution: dict[str, int] = Field(default_factory=dict)
    coverage_pct: float = 0.0


# =============================================================================
# Funnels & Journeys
# =============================================================================


class FunnelStep(CDPSchema):
    """A single step in a conversion funnel."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    event_name: str
    step_name: Optional[str] = None
    property_filters: Optional[dict[str, Any]] = None


class FunnelCreate(CDPSchema):
    """Request to create a funnel."""

    name: str = Field(..., min_length=1, max_length=255)
    steps: list[FunnelStep] = Field(..., min_length=2, max_length=20)
    description: Optional[str] = None
    conversion_window_days: int = Field(default=30, ge=1, le=365)
    step_timeout_hours: Optional[int] = Field(None, ge=1)
    auto_refresh: bool = False
    refresh_interval_hours: Optional[int] = Field(default=24, ge=1, le=168)
    tags: list[str] = Field(default_factory=list)


class FunnelUpdate(CDPSchema):
    """Partial funnel update."""

    name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    steps: Optional[list[FunnelStep]] = None
    conversion_window_days: Optional[int] = Field(None, ge=1, le=365)
    step_timeout_hours: Optional[int] = Field(None, ge=1)
    auto_refresh: Optional[bool] = None
    refresh_interval_hours: Optional[int] = Field(None, ge=1, le=168)
    tags: Optional[list[str]] = None


class FunnelResponse(CDPSchema):
    """A conversion funnel."""

    id: UUID
    name: str
    slug: Optional[str] = None
    description: Optional[str] = None
    status: str = "draft"
    steps: list[dict[str, Any]] = Field(default_factory=list)
    conversion_window_days: int = 30
    step_timeout_hours: Optional[int] = None
    total_entered: int = 0
    total_converted: int = 0
    overall_conversion_rate: Optional[float] = None
    step_metrics: list[dict[str, Any]] = Field(default_factory=list)
    last_computed_at: Optional[datetime] = None
    computation_duration_ms: Optional[int] = None
    auto_refresh: bool = False
    refresh_interval_hours: Optional[int] = None
    next_refresh_at: Optional[datetime] = None
    tags: list[str] = Field(default_factory=list)
    created_by_user_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class FunnelListResponse(CDPSchema):
    """List of funnels."""

    funnels: list[FunnelResponse] = Field(default_factory=list)
    total: int = 0


class FunnelComputeResponse(CDPSchema):
    """Result of a funnel metrics computation."""

    funnel_id: str
    total_entered: int = 0
    total_converted: int = 0
    overall_conversion_rate: float = 0.0
    step_metrics: list[dict[str, Any]] = Field(default_factory=list)
    computation_duration_ms: Optional[int] = None


class FunnelAnalysisRequest(CDPSchema):
    """Optional date filters for funnel analysis."""

    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None


class FunnelAnalysisResponse(CDPSchema):
    """Detailed funnel analysis."""

    model_config = ConfigDict(from_attributes=True, extra="allow")

    funnel_id: str
    funnel_name: Optional[str] = None
    total_entered: int = 0
    total_converted: int = 0
    overall_conversion_rate: float = 0.0
    step_analysis: list[dict[str, Any]] = Field(default_factory=list)
    avg_conversion_time_seconds: Optional[float] = None
    analysis_period: dict[str, Any] = Field(default_factory=dict)


class FunnelDropOffResponse(CDPSchema):
    """Profiles that dropped off at a funnel step."""

    funnel_id: str
    step: int
    profiles: list[ProfileResponse] = Field(default_factory=list)
    total: int = 0


class ProfileFunnelJourneysResponse(CDPSchema):
    """A profile's journeys through funnels."""

    profile_id: str
    journeys: list[dict[str, Any]] = Field(default_factory=list)
