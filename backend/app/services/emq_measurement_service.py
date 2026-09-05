# =============================================================================
# Stratum AI - EMQ Measurement Service
# =============================================================================
"""
EMQ (Event Match Quality) measured from persisted, tenant-scoped delivery data.

This module used to be a trap. ``calculate_real_emq_metrics`` looked like the
real thing - it read "event delivery logs", matched pixel events against CAPI
events, computed latency percentiles - but every one of those inputs was a
module-level Python list:

- ``_event_delivery_logs`` in ``app.services.capi.platform_connectors``, capped
  at 10000 entries, lost on restart, **not filtered by tenant at all**, and
  visible only to the process that appended to it (so the Celery worker
  computing signal health always saw it empty);
- ``_pixel_events`` and ``_conversion_events`` here, which nothing ever wrote to;
- ``_ga4_data`` here, fed in the API process and read nowhere that mattered.

``get_history`` was worse: it returned ``round(85 + (hash(f"{tenant_id}{i}") %
15), 1)`` and three siblings - numbers derived from a *string hash*, presented
to the tenant as their measurement history.

All of it is gone. The one genuine event-delivery source is the
``capi_delivery_logs`` table, written by
:class:`app.services.capi.delivery_logger.DeliveryLogger` from the CAPI send
path, and read here through :mod:`app.services.signal_health` so that EMQ means
exactly what it means inside the trust gate. Every function takes a
``tenant_id`` and every query is filtered by it.

When a tenant has not sent enough events, these functions say so
(``EMQMeasurementResult.insufficient_data``); they do not return a number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.services.signal_health import (
    COMPONENT_EMQ,
    compute_signal_health,
    daily_delivery_history,
)
from app.services.signal_health.service import CHANNEL_DELIVERY_PLATFORMS

logger = get_logger(__name__)

__all__ = [
    "EMQMeasurementResult",
    "delivery_platform_for",
    "get_emq_history",
    "measure_emq",
]


@dataclass
class EMQMeasurementResult:
    """
    The outcome of an EMQ measurement.

    ``insufficient_data`` is a first-class outcome: the tenant sent too few
    events for the numbers to mean anything, and every score is ``None``. That
    is different from a low score and must be presented differently.
    """

    platform: str
    insufficient_data: bool
    overall_score: float | None = None
    delivery_success_rate_pct: float | None = None
    identifier_coverage_pct: float | None = None
    events_measured: int = 0
    window_start: str | None = None
    window_end: str | None = None
    recommendations: list[str] = field(default_factory=list)
    missing_inputs: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serialise the result for API responses."""
        return {
            "platform": self.platform,
            "insufficient_data": self.insufficient_data,
            "overall_score": self.overall_score,
            "delivery_success_rate_pct": self.delivery_success_rate_pct,
            "identifier_coverage_pct": self.identifier_coverage_pct,
            "events_measured": self.events_measured,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "recommendations": self.recommendations,
            "missing_inputs": self.missing_inputs,
        }


def delivery_platform_for(channel: str) -> str:
    """
    Map a Meta channel onto the ``capi_delivery_logs.platform`` value it uses.

    Facebook and Instagram share the single Meta CAPI dataset; WhatsApp has its
    own connector and its own rows.

    Args:
        channel: Meta channel or platform name.

    Returns:
        The delivery-log platform value to query.
    """
    return CHANNEL_DELIVERY_PLATFORMS.get(channel, (channel,))[0]


async def measure_emq(
    db: AsyncSession,
    tenant_id: int,
    platform: str,
) -> EMQMeasurementResult:
    """
    Measure EMQ for one tenant and channel from persisted delivery data.

    Args:
        db: Async database session.
        tenant_id: Tenant to measure. The underlying query is filtered by it.
        platform: Meta channel (``facebook``, ``instagram`` or ``whatsapp``).

    Returns:
        The measurement, or an ``insufficient_data`` result naming what was
        missing when the tenant has not sent enough events to be scored.
    """
    computation = await compute_signal_health(
        db=db, tenant_id=tenant_id, channel=platform
    )
    delivery = computation.delivery
    components = computation.component_scores()
    emq = components.get(COMPONENT_EMQ)

    if emq is None:
        logger.info(
            "emq_insufficient_data",
            tenant_id=tenant_id,
            platform=platform,
            events=delivery.total_events if delivery else 0,
        )
        return EMQMeasurementResult(
            platform=platform,
            insufficient_data=True,
            events_measured=delivery.total_events if delivery else 0,
            window_start=computation.window.start.isoformat(),
            window_end=computation.window.end.isoformat(),
            missing_inputs=computation.missing_inputs,
            recommendations=[
                (
                    "Send conversion events through the Conversions API - there "
                    "is not yet enough delivery data to measure event match "
                    "quality."
                )
            ],
        )

    success_rate = delivery.success_rate_pct if delivery else None
    coverage = delivery.identifier_coverage_pct if delivery else None

    recommendations: list[str] = []
    if success_rate is not None and success_rate < 95:
        recommendations.append(
            f"CAPI delivery succeeded on {success_rate:.1f}% of attempts - "
            "check API credentials, rate limits and connector health."
        )
    if coverage is not None and coverage < 100:
        recommendations.append(
            f"{coverage:.1f}% of delivered events carried hashed customer "
            "identifiers - send email, phone or external_id with more events "
            "to raise match quality."
        )
    if not recommendations:
        recommendations.append(
            "Event delivery looks good. Keep monitoring for consistency."
        )

    return EMQMeasurementResult(
        platform=platform,
        insufficient_data=False,
        overall_score=emq,
        delivery_success_rate_pct=success_rate,
        identifier_coverage_pct=coverage,
        events_measured=delivery.total_events if delivery else 0,
        window_start=computation.window.start.isoformat(),
        window_end=computation.window.end.isoformat(),
        recommendations=recommendations,
    )


async def get_emq_history(
    db: AsyncSession,
    tenant_id: int,
    platform: str,
    days: int = 30,
) -> list[dict[str, Any]]:
    """
    Return this tenant's measured EMQ history, one entry per day with traffic.

    Days on which the tenant delivered no events are omitted rather than filled
    in with a zero or an interpolation: the caller should show a gap, because a
    gap is what happened.

    Args:
        db: Async database session.
        tenant_id: Tenant whose history to read.
        platform: Meta channel to read.
        days: How many days back to cover.

    Returns:
        Daily measurements, oldest first; empty when the tenant sent nothing.
    """
    return await daily_delivery_history(
        db=db,
        tenant_id=tenant_id,
        platform=delivery_platform_for(platform),
        days=days,
    )
