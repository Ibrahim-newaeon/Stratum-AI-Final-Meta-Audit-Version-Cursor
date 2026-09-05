# =============================================================================
# Stratum AI - Signal Health Scoring (pure)
# =============================================================================
"""
The one definition of how signal health components become a 0-100 composite.

Both ends of the trust engine use these functions, so the number cannot mean
one thing where it is written and another where it is enforced:

- ``app.services.signal_health.service`` computes the score from real,
  tenant-scoped measurements and the rollup writes the components it measured
  into ``fact_signal_health_daily``.
- ``app.tasks.apply_actions_queue`` scores that row again when the trust gate
  runs, through :func:`weighted_score`.

Weights come from ``Settings`` (``signal_health_*_weight``) via
``SignalHealthConfig``; the minimum evidence floor comes from
``signal_health_min_component_weight``. Nothing here carries a literal
threshold and nothing here substitutes a default for a missing input: a
component that was not measured is dropped, and if too much of the weight was
dropped the result is ``None`` - insufficient data - not a number.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.core.config import settings
from app.stratum.core.signal_health import SignalHealthConfig

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from app.services.signal_health.model import SignalHealthThresholds

__all__ = [
    "COMPONENT_DELIVERY",
    "COMPONENT_EMQ",
    "COMPONENT_FRESHNESS",
    "COMPONENT_RELIABILITY",
    "component_weights",
    "connection_component_score",
    "emq_component_score",
    "freshness_component_score",
    "status_for_score",
    "weighted_score",
]

# Component names. They line up one-to-one with the metric columns of
# fact_signal_health_daily: emq -> emq_score, freshness -> freshness_minutes,
# delivery -> event_loss_pct, reliability -> api_error_rate. The trust gate
# publishes these names in its audit payload, so they are a contract.
COMPONENT_EMQ = "emq"
COMPONENT_FRESHNESS = "freshness"
COMPONENT_DELIVERY = "delivery"
COMPONENT_RELIABILITY = "reliability"


def component_weights(config: SignalHealthConfig | None = None) -> dict[str, float]:
    """
    Return the configured weight of each signal health component.

    With no override the weights are read from ``Settings`` **at call time**,
    not captured at import. ``SignalHealthConfig`` is a dataclass whose field
    defaults are evaluated once when the class is defined, so reading them
    through it would freeze the weights for the life of the process and make
    "the weights come from configuration" true only by accident of import
    order.

    Args:
        config: Optional configuration override; when given, its weights win.

    Returns:
        Mapping of component name to weight.
    """
    if config is not None:
        return {
            COMPONENT_EMQ: config.emq_weight,
            COMPONENT_FRESHNESS: config.freshness_weight,
            COMPONENT_DELIVERY: config.variance_weight,
            COMPONENT_RELIABILITY: config.anomaly_weight,
        }
    return {
        COMPONENT_EMQ: float(settings.signal_health_emq_weight),
        COMPONENT_FRESHNESS: float(settings.signal_health_freshness_weight),
        COMPONENT_DELIVERY: float(settings.signal_health_variance_weight),
        COMPONENT_RELIABILITY: float(settings.signal_health_anomaly_weight),
    }


def weighted_score(
    components: Mapping[str, float | None],
    config: SignalHealthConfig | None = None,
) -> tuple[float | None, float, float]:
    """
    Combine the measured components into a 0-100 composite.

    Components whose value is ``None`` were not measured; they are dropped and
    the remaining weights renormalised, so a partially measured tenant is
    scored on what it does have rather than penalised for what it does not.

    Renormalising is only safe while enough of the evidence is present. Without
    a floor, a tenant carrying nothing but a trivially perfect component would
    renormalise to 100 and pass the trust gate - "absence of data is health",
    the exact inversion this module exists to remove. At least
    ``signal_health_min_component_weight`` of the total weight must therefore
    be measured; below that the composite is ``None``, which callers must treat
    as insufficient data (the gate BLOCKs on it, the API says so explicitly).

    Args:
        components: Component name to 0-100 value, or None when unmeasured.
        config: Optional configuration override carrying the weights.

    Returns:
        Tuple of (score or None, measured weight, weight required).
    """
    weights = component_weights(config)
    measured = {
        name: value
        for name, value in components.items()
        if value is not None and name in weights
    }

    available_weight = sum(weights[name] for name in measured)
    total_weight = sum(weights.values())
    # Scale the floor by the weights actually in play so a deployment whose
    # weights do not sum to exactly 1.0 still means "half the evidence".
    required_weight = settings.signal_health_min_component_weight * total_weight

    if available_weight <= 0 or available_weight < required_weight:
        return None, available_weight, required_weight

    score = sum(
        min(max(float(value), 0.0), 100.0) * weights[name]
        for name, value in measured.items()
    )
    return round(score / available_weight, 1), available_weight, required_weight


def status_for_score(
    score: float | None,
    thresholds: SignalHealthThresholds | None = None,
) -> str:
    """
    Map a composite onto the documented health band.

    Thresholds come from configuration (optionally overridden per tenant),
    never from literals at the call site. A missing score is
    ``insufficient_data`` - explicitly unknown, which is neither healthy nor
    critical.

    Args:
        score: The 0-100 composite, or None when it could not be computed.
        thresholds: Band edges to grade against; defaults to the configured
            deployment-wide pair.

    Returns:
        One of ``healthy``, ``degraded``, ``critical``, ``insufficient_data``.
    """
    from app.services.signal_health.model import (
        STATUS_CRITICAL,
        STATUS_DEGRADED,
        STATUS_HEALTHY,
        STATUS_INSUFFICIENT_DATA,
        SignalHealthThresholds,
    )

    bands = thresholds or SignalHealthThresholds.from_settings()
    if score is None:
        return STATUS_INSUFFICIENT_DATA
    if score >= bands.healthy:
        return STATUS_HEALTHY
    if score >= bands.degraded:
        return STATUS_DEGRADED
    return STATUS_CRITICAL


def emq_component_score(
    success_rate_pct: float,
    identifier_coverage_pct: float | None,
) -> float:
    """
    Score the EMQ component from measured CAPI delivery quality.

    Two drivers, both counted from ``capi_delivery_logs`` rows:

    - the delivery success rate, and
    - hashed-identifier coverage (``user_data_hash`` present), which is the
      only match-quality signal that table genuinely carries.

    Their split is ``signal_health_identifier_coverage_weight``. No other
    driver is invented: the Meta-reported match rate is not in this table, so
    it is not in this score. When coverage cannot be measured the component is
    the success rate alone.

    Args:
        success_rate_pct: Share of delivery attempts that succeeded (0-100).
        identifier_coverage_pct: Share carrying hashed identifiers, or None.

    Returns:
        A 0-100 component score.
    """
    identifier_weight = settings.signal_health_identifier_coverage_weight
    if identifier_coverage_pct is None or identifier_weight <= 0:
        return min(max(success_rate_pct, 0.0), 100.0)
    blended = (
        success_rate_pct * (1.0 - identifier_weight)
        + identifier_coverage_pct * identifier_weight
    )
    return round(min(max(blended, 0.0), 100.0), 2)


def freshness_component_score(
    age_minutes: float | None,
    config: SignalHealthConfig | None = None,
) -> float | None:
    """
    Convert a data age in minutes into a 0-100 freshness component.

    Full marks at or under ``signal_health_fresh_minutes``, zero at or beyond
    ``signal_health_stale_minutes``, linear between them.

    Args:
        age_minutes: Age of the newest genuine data point, or None if unknown.
        config: Optional configuration override carrying the fresh/stale bounds.

    Returns:
        A 0-100 score, or None when there is no reading to score.
    """
    if age_minutes is None:
        return None
    # Read at call time for the same reason as component_weights.
    fresh_minutes = (
        config.fresh_minutes
        if config is not None
        else float(settings.signal_health_fresh_minutes)
    )
    stale_minutes = (
        config.stale_minutes
        if config is not None
        else float(settings.signal_health_stale_minutes)
    )
    age = max(float(age_minutes), 0.0)
    if age <= fresh_minutes:
        return 100.0
    if age >= stale_minutes:
        return 0.0
    span = stale_minutes - fresh_minutes
    return round(100.0 * (1.0 - (age - fresh_minutes) / span), 2)


def connection_component_score(
    status: str, error_count: int, has_last_error: bool
) -> float:
    """
    Score the platform connection from its recorded state.

    A connection that is not in the connected state scores zero - it is not a
    degraded signal, it is no signal. A connected one starts at 100 and loses
    ``signal_health_connection_error_penalty`` points per recorded error. A
    stored ``last_error`` with a zero counter still counts as one error, so a
    connection that failed without incrementing its counter is not scored as
    perfect.

    Args:
        status: ``TenantPlatformConnection.status``.
        error_count: ``TenantPlatformConnection.error_count``.
        has_last_error: Whether ``last_error`` holds anything.

    Returns:
        A 0-100 component score.
    """
    if status != "connected":
        return 0.0
    errors = max(int(error_count or 0), 1 if has_last_error else 0)
    penalty = settings.signal_health_connection_error_penalty * errors
    return round(min(max(100.0 - penalty, 0.0), 100.0), 2)
