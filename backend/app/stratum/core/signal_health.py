# =============================================================================
# Stratum AI - Signal Health Calculator (Trust Engine)
# =============================================================================
"""
Signal health scoring for the trust-gated autopilot.

Computes a composite 0-100 signal health score from four components:

- EMQ (Event Match Quality): quality of conversion signal matching
- Freshness: how recently metrics data was synced
- Variance: stability of key metrics over the recent window
- Anomaly: absence of sudden spikes/drops in spend and conversions

Thresholds come from configuration (never hardcoded at call sites):
scores >= healthy_threshold are HEALTHY (autopilot enabled), scores >=
degraded_threshold are DEGRADED (alert + hold), and anything lower is
CRITICAL (manual action required).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Optional, Sequence

from app.core.config import settings
from app.core.logging import get_logger
from app.stratum.models import SignalHealth

logger = get_logger(__name__)

__all__ = ["SignalHealthCalculator", "SignalHealthConfig", "SignalHealthResult"]


@dataclass
class SignalHealthConfig:
    """Configurable thresholds and weights for signal health scoring."""

    # Status thresholds (sourced from app settings when defined there)
    healthy_threshold: float = float(getattr(settings, "signal_health_healthy_threshold", 70.0))
    degraded_threshold: float = float(getattr(settings, "signal_health_degraded_threshold", 40.0))

    # Component weights (must sum to 1.0)
    emq_weight: float = 0.40
    freshness_weight: float = 0.25
    variance_weight: float = 0.20
    anomaly_weight: float = 0.15

    # Freshness scoring: full score under fresh_minutes, zero past stale_minutes
    fresh_minutes: float = float(getattr(settings, "signal_health_fresh_minutes", 60.0))
    stale_minutes: float = float(getattr(settings, "signal_health_stale_minutes", 24 * 60.0))


class SignalHealthResult(SignalHealth):
    """
    SignalHealth extended with convenience accessors used by workers.

    Adds recommendations plus derived flags (is_healthy/is_degraded/
    is_critical/autopilot_allowed) and a ``score`` alias for overall_score.
    """

    recommendations: list[str] = []
    healthy_threshold: float = 70.0
    degraded_threshold: float = 40.0

    @property
    def score(self) -> float:
        """Alias for overall_score."""
        return self.overall_score

    @property
    def is_healthy(self) -> bool:
        """Signal health is in the green zone (autopilot enabled)."""
        return self.overall_score >= self.healthy_threshold

    @property
    def is_degraded(self) -> bool:
        """Signal health is in the yellow zone (alert + hold)."""
        return self.degraded_threshold <= self.overall_score < self.healthy_threshold

    @property
    def is_critical(self) -> bool:
        """Signal health is in the red zone (manual action required)."""
        return self.overall_score < self.degraded_threshold

    @property
    def autopilot_allowed(self) -> bool:
        """Whether autopilot may execute actions at this health level."""
        return self.is_autopilot_safe(threshold=self.healthy_threshold)


def _get(obj: Any, attr: str, default: Any = None) -> Any:
    """Read an attribute from an object or key from a dict."""
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


class SignalHealthCalculator:
    """Computes composite signal health from EMQ scores and recent metrics."""

    def __init__(self, config: Optional[SignalHealthConfig] = None):
        """Initialize with optional custom configuration."""
        self.config = config or SignalHealthConfig()

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def calculate(
        self,
        platform: Any,
        account_id: str,
        emq_scores: Sequence[Any],
        recent_metrics: Sequence[Any],
        cdp_emq_score: Optional[float] = None,
    ) -> SignalHealthResult:
        """
        Calculate signal health for an account.

        Args:
            platform: Platform enum or string (informational).
            account_id: Ad account identifier (informational).
            emq_scores: EMQScore models or dicts with a 0-10 ``score``.
            recent_metrics: PerformanceMetrics models or dicts.
            cdp_emq_score: Optional CDP EMQ score (0-100) to blend in.

        Returns:
            SignalHealthResult with component scores, status, issues and
            recommendations.
        """
        issues: list[str] = []
        recommendations: list[str] = []

        emq = self._emq_score(emq_scores, issues, recommendations)
        freshness = self._freshness_score(recent_metrics, issues, recommendations)
        variance = self._variance_score(recent_metrics, issues, recommendations)
        anomaly = self._anomaly_score(recent_metrics, issues, recommendations)

        cfg = self.config
        overall = (
            emq * cfg.emq_weight
            + freshness * cfg.freshness_weight
            + variance * cfg.variance_weight
            + anomaly * cfg.anomaly_weight
        )
        overall = round(min(max(overall, 0.0), 100.0), 1)

        if overall >= cfg.healthy_threshold:
            status = "healthy"
        elif overall >= cfg.degraded_threshold:
            status = "degraded"
            recommendations.append(
                "Signal health is degraded - autopilot actions are held for review."
            )
        else:
            status = "critical"
            recommendations.append(
                "Signal health is critical - manual review required before any "
                "automated changes."
            )

        result = SignalHealthResult(
            overall_score=overall,
            emq_score=round(emq, 1),
            freshness_score=round(freshness, 1),
            variance_score=round(variance, 1),
            anomaly_score=round(anomaly, 1),
            cdp_emq_score=cdp_emq_score,
            status=status,
            issues=issues,
            last_updated=datetime.now(UTC),
            recommendations=recommendations,
            healthy_threshold=cfg.healthy_threshold,
            degraded_threshold=cfg.degraded_threshold,
        )

        logger.debug(
            "signal_health_calculated",
            platform=str(platform),
            account_id=account_id,
            score=overall,
            status=status,
        )
        return result

    # -------------------------------------------------------------------
    # Component scores
    # -------------------------------------------------------------------

    def _emq_score(
        self,
        emq_scores: Sequence[Any],
        issues: list[str],
        recommendations: list[str],
    ) -> float:
        """Average EMQ (0-10 scale) converted to 0-100."""
        if not emq_scores:
            issues.append("No EMQ data available")
            recommendations.append(
                "Enable the Conversions API to improve event match quality visibility."
            )
            return 50.0  # Neutral: absence of data is not proof of failure

        values = []
        for entry in emq_scores:
            raw = _get(entry, "score", None)
            if raw is None:
                continue
            raw = float(raw)
            # Accept either 0-10 EMQ or 0-100 percentage inputs
            values.append(raw * 10 if raw <= 10 else raw)

        if not values:
            issues.append("EMQ entries contained no scores")
            return 50.0

        score = min(sum(values) / len(values), 100.0)
        if score < 70:
            issues.append(f"Low average EMQ ({score:.0f}/100)")
            recommendations.append(
                "Send more customer identifiers (email, phone) to raise match quality."
            )
        return score

    def _freshness_score(
        self,
        recent_metrics: Sequence[Any],
        issues: list[str],
        recommendations: list[str],
    ) -> float:
        """Score based on the age of the most recent metrics data point."""
        if not recent_metrics:
            issues.append("No recent metrics data")
            recommendations.append("Trigger a data sync to refresh account metrics.")
            return 0.0

        newest: Optional[datetime] = None
        for m in recent_metrics:
            for attr in ("date_end", "date_start"):
                value = _get(m, attr)
                if isinstance(value, str):
                    try:
                        value = datetime.fromisoformat(value)
                    except ValueError:
                        value = None
                if isinstance(value, datetime):
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=UTC)
                    if newest is None or value > newest:
                        newest = value

        if newest is None:
            # Metrics exist but carry no timestamps: assume acceptably fresh
            return 80.0

        age_minutes = max((datetime.now(UTC) - newest).total_seconds() / 60.0, 0.0)
        cfg = self.config
        if age_minutes <= cfg.fresh_minutes:
            return 100.0
        if age_minutes >= cfg.stale_minutes:
            issues.append(f"Metrics are stale ({age_minutes / 60:.1f}h old)")
            recommendations.append("Investigate the sync pipeline - data is stale.")
            return 0.0

        span = cfg.stale_minutes - cfg.fresh_minutes
        return 100.0 * (1.0 - (age_minutes - cfg.fresh_minutes) / span)

    def _variance_score(
        self,
        recent_metrics: Sequence[Any],
        issues: list[str],
        recommendations: list[str],
    ) -> float:
        """Score metric stability via the coefficient of variation of spend."""
        spends = [
            float(_get(m, "spend", 0.0) or 0.0)
            for m in recent_metrics
            if _get(m, "spend", None) is not None
        ]
        spends = [s for s in spends if s > 0]

        if len(spends) < 3:
            return 75.0  # Not enough points to judge variance; mildly positive

        mean = statistics.mean(spends)
        if mean <= 0:
            return 75.0
        cv = statistics.stdev(spends) / mean  # coefficient of variation

        # cv 0 -> 100, cv >= 1.0 -> 0
        score = max(0.0, 100.0 * (1.0 - min(cv, 1.0)))
        if score < 40:
            issues.append("High variance in recent spend data")
            recommendations.append(
                "Review recent campaign changes - spend is fluctuating heavily."
            )
        return score

    def _anomaly_score(
        self,
        recent_metrics: Sequence[Any],
        issues: list[str],
        recommendations: list[str],
    ) -> float:
        """Score based on absence of sudden spikes/drops in conversions."""
        conversions = [
            float(_get(m, "conversions", 0) or 0)
            for m in recent_metrics
            if _get(m, "conversions", None) is not None
        ]

        if len(conversions) < 3:
            return 85.0  # Not enough data to detect anomalies

        historical, current = conversions[:-1], conversions[-1]
        mean = statistics.mean(historical)
        stdev = statistics.stdev(historical) if len(historical) > 1 else 0.0

        if stdev <= 1e-9:
            return 100.0 if abs(current - mean) < max(mean * 0.5, 1.0) else 50.0

        zscore = abs(current - mean) / stdev
        if zscore >= 4.0:
            issues.append(f"Severe conversion anomaly detected (z={zscore:.1f})")
            recommendations.append(
                "Check tracking configuration - conversion volume shifted sharply."
            )
            return 0.0
        if zscore >= 2.5:
            issues.append(f"Conversion anomaly detected (z={zscore:.1f})")
            return 40.0
        if zscore >= 1.5:
            return 75.0
        return 100.0
