# =============================================================================
# Stratum AI - EMQ v2 Schemas
# =============================================================================
"""
Pydantic models for EMQ (Event Measurement Quality) v2 API endpoints.
These schemas match the frontend types defined in api/emqV2.ts.

Every measured field is Optional and None means "not measured", not zero. The
service behind these endpoints used to substitute a default wherever a tenant
had no persisted signal health - a score of 75, an SVI of 15.3, $24,350 of
recovered revenue - and these types were what made that expressible: a
required ``score: float`` leaves no way to say "nothing was measured".
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Base Schema
# =============================================================================
class EMQBaseSchema(BaseModel):
    """Base schema with common configuration."""

    model_config = ConfigDict(
        from_attributes=True,
        str_strip_whitespace=True,
    )


# =============================================================================
# EMQ Score Schemas
# =============================================================================
class EmqDriver(EMQBaseSchema):
    """EMQ driver component."""

    name: str
    value: float
    weight: float
    status: Literal["good", "warning", "critical"]
    # None when the same component was not measured the day before: there is
    # no direction to report against a day that was never recorded.
    trend: Literal["up", "down", "flat"] | None = None


class EmqScoreResponse(EMQBaseSchema):
    """EMQ score response with drivers."""

    score: float | None = Field(
        default=None, ge=0, le=100, description="Overall EMQ score; None when not measured"
    )
    previousScore: float | None = Field(
        default=None, ge=0, le=100, description="Previous period score; None when not measured"
    )
    confidenceBand: Literal["reliable", "directional", "unsafe"] | None = None
    drivers: list[EmqDriver]
    lastUpdated: str = Field(..., description="ISO timestamp of last update")


# =============================================================================
# Confidence Schemas
# =============================================================================
class ConfidenceFactor(EMQBaseSchema):
    """Factor contributing to confidence score."""

    name: str
    contribution: float
    status: Literal["positive", "negative", "neutral"]


class ConfidenceThresholds(EMQBaseSchema):
    """Threshold values for confidence bands."""

    reliable: float
    directional: float


class ConfidenceDataResponse(EMQBaseSchema):
    """Confidence band details response."""

    band: Literal["reliable", "directional", "unsafe"] | None = None
    score: float | None = None
    thresholds: ConfidenceThresholds
    factors: list[ConfidenceFactor]


# =============================================================================
# Playbook Schemas
# =============================================================================
class PlaybookItemResponse(EMQBaseSchema):
    """Playbook item for EMQ fixes."""

    id: str
    title: str
    description: str
    priority: Literal["critical", "high", "medium", "low"]
    owner: str | None = None
    estimatedImpact: float = Field(..., description="Estimated EMQ score improvement")
    estimatedTime: str | None = None
    platform: str | None = None
    status: Literal["pending", "in_progress", "completed"]
    actionUrl: str | None = None


class PlaybookItemUpdate(EMQBaseSchema):
    """Playbook item update request."""

    status: Literal["pending", "in_progress", "completed"] | None = None
    owner: str | None = None


# =============================================================================
# Incident Schemas
# =============================================================================
class EmqIncidentResponse(EMQBaseSchema):
    """EMQ incident event."""

    id: str
    type: Literal["incident_opened", "incident_closed", "degradation", "recovery"]
    title: str
    description: str | None = None
    timestamp: str = Field(..., description="ISO timestamp")
    platform: str | None = None
    severity: Literal["critical", "high", "medium", "low"]
    recoveryHours: float | None = None
    emqImpact: float | None = None


# =============================================================================
# Impact Schemas
# =============================================================================
class ImpactBreakdown(EMQBaseSchema):
    """Per-platform impact breakdown."""

    platform: str
    actualRoas: float
    # Nothing models the counterfactual "ROAS under perfect attribution", so
    # these stay None rather than asserting a 15% improvement.
    estimatedRoas: float | None = None
    confidence: float
    revenueImpact: float | None = None


class EmqImpactResponse(EMQBaseSchema):
    """ROAS impact estimate response."""

    totalImpact: float | None = None
    currency: str = "USD"
    breakdown: list[ImpactBreakdown]


# =============================================================================
# Volatility Schemas
# =============================================================================
class VolatilityDataPoint(EMQBaseSchema):
    """Weekly volatility data point."""

    date: str
    value: float


class EmqVolatilityResponse(EMQBaseSchema):
    """Signal Volatility Index response."""

    svi: float | None = Field(default=None, description="Signal Volatility Index")
    # A direction needs two ends to compare; None when there is too little
    # history to state one.
    trend: Literal["increasing", "decreasing", "stable"] | None = None
    weeklyData: list[VolatilityDataPoint]


# =============================================================================
# Autopilot Schemas
# =============================================================================
class AutopilotStateResponse(EMQBaseSchema):
    """Autopilot state response."""

    mode: Literal["normal", "limited", "cuts_only", "frozen"]
    reason: str | None = None
    # fact_actions_queue carries no budget column, so this is None until a
    # source exists. It was the queued row count multiplied by a flat $5,000.
    budgetAtRisk: float | None = None
    allowedActions: list[str]
    restrictedActions: list[str]


class AutopilotModeUpdate(EMQBaseSchema):
    """Autopilot mode update request."""

    mode: Literal["normal", "limited", "cuts_only", "frozen"]
    reason: str | None = None


# =============================================================================
# Benchmark Schemas (Super Admin)
# =============================================================================
class EmqBenchmarkResponse(EMQBaseSchema):
    """Platform benchmark data."""

    platform: str
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None
    # This endpoint is not tenant-scoped, so it cannot report a tenant's own
    # score or where that score falls in the distribution.
    tenantScore: float | None = None
    percentile: float | None = None


# =============================================================================
# Portfolio Schemas (Super Admin)
# =============================================================================
class TopIssue(EMQBaseSchema):
    """Top issue affecting tenants."""

    driver: str
    affectedTenants: int


class BandDistribution(EMQBaseSchema):
    """Distribution of tenants by confidence band."""

    reliable: int
    directional: int
    unsafe: int


class EmqPortfolioResponse(EMQBaseSchema):
    """Portfolio overview for super admin."""

    totalTenants: int
    byBand: BandDistribution
    atRiskBudget: float | None = None
    avgScore: float | None = None
    topIssues: list[TopIssue]
