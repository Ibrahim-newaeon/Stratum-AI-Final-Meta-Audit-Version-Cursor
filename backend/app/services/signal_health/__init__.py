# =============================================================================
# Stratum AI - Signal Health Service
# =============================================================================
"""
Tenant-scoped signal health, computed from real persisted data.

This package is the one place signal health is computed. The daily rollup
writes what it measures here into ``fact_signal_health_daily``, the dashboard
publishes it, and the trust gate grades the row it produced. When the inputs
are not there the answer is ``insufficient_data`` naming what is missing - not
a default score.

``app/stratum/core/signal_health.py`` is a different thing and stays: it is a
pure calculator over already-fetched in-memory sequences, used by the stratum
workers, with no tenant scope and no database access. This package owns the
tenant-scoped, database-backed path; both read their weights from the same
``signal_health_*_weight`` settings via ``SignalHealthConfig``.
"""

from app.services.signal_health.model import (
    MISSING_NO_CAMPAIGN_SYNC,
    MISSING_NO_DELIVERY_EVENTS,
    MISSING_NO_PLATFORM_CONNECTION,
    MISSING_TOO_FEW_DELIVERY_EVENTS,
    STATUS_CRITICAL,
    STATUS_DEGRADED,
    STATUS_HEALTHY,
    STATUS_INSUFFICIENT_DATA,
    ConnectionMeasurement,
    DeliveryMeasurement,
    FreshnessMeasurement,
    MissingInput,
    ScoredComponent,
    SignalHealthComputation,
    SignalHealthThresholds,
    SignalHealthWindow,
)
from app.services.signal_health.scoring import (
    COMPONENT_DELIVERY,
    COMPONENT_EMQ,
    COMPONENT_FRESHNESS,
    COMPONENT_RELIABILITY,
    component_weights,
    connection_component_score,
    emq_component_score,
    freshness_component_score,
    status_for_score,
    weighted_score,
)
from app.services.signal_health.service import (
    META_CHANNELS,
    compute_signal_health,
    compute_tenant_signal_health,
    daily_delivery_history,
    day_window,
    default_window,
    measure_connection,
    measure_delivery,
    measure_freshness,
    summarise_channels,
    thresholds_for_tenant,
)

__all__ = [
    "COMPONENT_DELIVERY",
    "COMPONENT_EMQ",
    "COMPONENT_FRESHNESS",
    "COMPONENT_RELIABILITY",
    "META_CHANNELS",
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
    "component_weights",
    "compute_signal_health",
    "compute_tenant_signal_health",
    "connection_component_score",
    "daily_delivery_history",
    "day_window",
    "default_window",
    "emq_component_score",
    "freshness_component_score",
    "measure_connection",
    "measure_delivery",
    "measure_freshness",
    "status_for_score",
    "summarise_channels",
    "thresholds_for_tenant",
    "weighted_score",
]
