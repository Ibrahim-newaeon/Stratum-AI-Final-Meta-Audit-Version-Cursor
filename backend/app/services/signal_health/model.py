# =============================================================================
# Stratum AI - Signal Health Value Types
# =============================================================================
"""
Value types for the tenant-scoped signal health computation.

Everything here is a plain, immutable dataclass so the scoring rules can be
exercised without a database. The one invariant the whole module exists to
protect: a component that could not be measured is *absent*, never a default.
``SignalHealthComputation.score`` is ``None`` when too little evidence was
available, and ``missing`` names exactly which inputs were not there.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = [
    "MISSING_NO_CAMPAIGN_SYNC",
    "MISSING_NO_DELIVERY_EVENTS",
    "MISSING_NO_PLATFORM_CONNECTION",
    "MISSING_TOO_FEW_DELIVERY_EVENTS",
    "STATUS_CRITICAL",
    "STATUS_DEGRADED",
    "STATUS_HEALTHY",
    "STATUS_INSUFFICIENT_DATA",
    "ConnectionMeasurement",
    "DeliveryMeasurement",
    "FreshnessMeasurement",
    "MissingInput",
    "ScoredComponent",
    "SignalHealthComputation",
    "SignalHealthThresholds",
    "SignalHealthWindow",
]

# Status strings published by the API. ``insufficient_data`` is deliberately
# not one of the three health bands: it means "unknown", not "bad", and the UI
# must render it differently from a low score.
STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_CRITICAL = "critical"
STATUS_INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True)
class SignalHealthThresholds:
    """
    The two band edges a composite is graded against.

    Defaults come from ``Settings``; a tenant may raise or lower them during
    onboarding (``TenantOnboarding.trust_threshold_autopilot`` /
    ``trust_threshold_alert``). The same object is used by the API summary, the
    daily rollup's row status and the trust gate, so a tenant who asks for 90
    gets 90 in all three - the alternative was two thresholds, one shown and
    one enforced.
    """

    healthy: float
    degraded: float

    @classmethod
    def from_settings(cls) -> SignalHealthThresholds:
        """Build the deployment-wide defaults from configuration."""
        from app.core.config import settings

        return cls(
            healthy=float(settings.signal_health_healthy_threshold),
            degraded=float(settings.signal_health_degraded_threshold),
        )

    @classmethod
    def resolve(
        cls,
        healthy: float | None,
        degraded: float | None,
    ) -> SignalHealthThresholds:
        """
        Build thresholds from optional per-tenant overrides.

        An override is honoured only when it is inside 0-100 and keeps the
        bands ordered (``degraded <= healthy``). A malformed pair is ignored in
        favour of the configured defaults rather than silently producing a gate
        that can never pass - or one that always does.

        Args:
            healthy: The tenant's autopilot threshold, or None.
            degraded: The tenant's alert threshold, or None.

        Returns:
            The thresholds to grade with.
        """
        defaults = cls.from_settings()
        if healthy is None or degraded is None:
            return defaults
        high = float(healthy)
        low = float(degraded)
        if not (0.0 <= low <= high <= 100.0):
            return defaults
        return cls(healthy=high, degraded=low)


@dataclass(frozen=True)
class SignalHealthWindow:
    """
    The half-open time window a computation was measured over.

    Carried through to the caller (and into the API response) because a score
    without its window is not interpretable: "92" over the last hour and "92"
    over the last month are different claims.
    """

    start: datetime
    end: datetime

    def as_dict(self) -> dict[str, str]:
        """Serialise the window for API responses and audit payloads."""
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True)
class DeliveryMeasurement:
    """
    What ``capi_delivery_logs`` actually said for one tenant and window.

    Every field is counted from persisted rows. ``identifier_coverage_pct`` is
    the share of delivery attempts carrying a ``user_data_hash`` - the only
    match-quality signal that table genuinely holds - and is ``None`` when
    there were no rows to compute it from.
    """

    total_events: int
    successful_events: int
    failed_events: int
    identifier_events: int

    @property
    def success_rate_pct(self) -> float | None:
        """Share of delivery attempts the platform accepted, or None if empty."""
        if self.total_events <= 0:
            return None
        return round(self.successful_events / self.total_events * 100.0, 2)

    @property
    def failure_rate_pct(self) -> float | None:
        """Share of delivery attempts that failed, or None if empty."""
        rate = self.success_rate_pct
        return None if rate is None else round(100.0 - rate, 2)

    @property
    def identifier_coverage_pct(self) -> float | None:
        """Share of attempts that carried hashed identifiers, or None if empty."""
        if self.total_events <= 0:
            return None
        return round(self.identifier_events / self.total_events * 100.0, 2)


@dataclass(frozen=True)
class FreshnessMeasurement:
    """
    Age of the newest genuine Meta insights pull for the tenant.

    ``last_synced_at`` is only advanced when Meta actually returned rows (see
    ``app.workers.tasks.sync``), so this is a trustworthy freshness input
    rather than a "we tried" timestamp.
    """

    last_synced_at: datetime
    age_minutes: int


@dataclass(frozen=True)
class ConnectionMeasurement:
    """State of the tenant's Meta platform connection."""

    status: str
    error_count: int
    has_last_error: bool
    last_error: str | None = None

    @property
    def is_connected(self) -> bool:
        """Whether the connection is in the connected state."""
        return self.status == "connected"


@dataclass(frozen=True)
class ScoredComponent:
    """One 0-100 component that contributed to the composite."""

    name: str
    score: float
    weight: float
    detail: dict[str, Any] = field(default_factory=dict)


# Stable identifiers for the reasons a component could not be measured. The UI
# translates these; ``MissingInput.reason`` carries the English detail (counts,
# window) for logs, audit payloads and English readers.
MISSING_NO_DELIVERY_EVENTS = "no_delivery_events"
MISSING_TOO_FEW_DELIVERY_EVENTS = "too_few_delivery_events"
MISSING_NO_CAMPAIGN_SYNC = "no_campaign_sync"
MISSING_NO_PLATFORM_CONNECTION = "no_platform_connection"


@dataclass(frozen=True)
class MissingInput:
    """
    One component that could not be measured, and why.

    This is the payload that lets the product say "not enough data yet, here
    is what is missing" instead of showing a number nobody can defend.
    ``code`` is the stable, translatable identifier; ``reason`` is the English
    sentence with the specifics filled in.
    """

    name: str
    weight: float
    reason: str
    code: str = ""


@dataclass(frozen=True)
class SignalHealthComputation:
    """
    The outcome of computing signal health for one tenant and one channel.

    Either ``score`` is a 0-100 composite of the components in ``components``,
    or it is ``None`` - meaning insufficient data - and ``missing`` names the
    inputs that were not available. There is no third possibility and no
    default score.
    """

    tenant_id: int
    channel: str
    window: SignalHealthWindow
    score: float | None
    status: str
    components: tuple[ScoredComponent, ...] = ()
    missing: tuple[MissingInput, ...] = ()
    available_weight: float = 0.0
    required_weight: float = 0.0
    delivery: DeliveryMeasurement | None = None
    freshness: FreshnessMeasurement | None = None
    connection: ConnectionMeasurement | None = None
    thresholds: SignalHealthThresholds | None = None

    @property
    def insufficient_data(self) -> bool:
        """Whether there was too little evidence to publish a score."""
        return self.score is None

    @property
    def partial_evidence(self) -> bool:
        """
        Whether a score was published while some component went unmeasured.

        A composite over three of four components is still a real measurement,
        but it is a weaker claim than one over all four, and the trust layer
        surfaces that as ``risk`` rather than pretending the evidence was
        complete.
        """
        return self.score is not None and bool(self.missing)

    @property
    def band_thresholds(self) -> SignalHealthThresholds:
        """The thresholds this computation was graded against."""
        return self.thresholds or SignalHealthThresholds.from_settings()

    @property
    def missing_inputs(self) -> list[str]:
        """Human-readable reasons, one per component that could not be measured."""
        return [item.reason for item in self.missing]

    @property
    def missing_input_codes(self) -> list[str]:
        """Stable codes for the missing inputs, in the same order, deduplicated."""
        return list(dict.fromkeys(item.code for item in self.missing if item.code))

    def component_scores(self) -> dict[str, float]:
        """Component name to 0-100 score, for the components that were measured."""
        return {component.name: component.score for component in self.components}

    def as_dict(self) -> dict[str, Any]:
        """Serialise the computation for API responses and audit payloads."""
        return {
            "tenant_id": self.tenant_id,
            "channel": self.channel,
            "window": self.window.as_dict(),
            "score": self.score,
            "status": self.status,
            "components": self.component_scores(),
            "missing_inputs": self.missing_inputs,
            "missing_input_codes": self.missing_input_codes,
            "available_weight": round(self.available_weight, 4),
            "required_weight": round(self.required_weight, 4),
            "healthy_threshold": self.band_thresholds.healthy,
            "degraded_threshold": self.band_thresholds.degraded,
        }
