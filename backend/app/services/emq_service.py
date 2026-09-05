# =============================================================================
# Stratum AI - EMQ Service
# =============================================================================
"""
EMQ (Event Measurement Quality) Service.

Reads a tenant's persisted signal health and reports it. The one invariant, the
same one ``app.services.signal_health`` exists to protect: **a value that was
not measured is absent, never a default**. Every method here returns ``None``
(or an empty list) where it used to return a plausible number:

- ``_get_default_emq_response`` returned score 75.0 / previousScore 73.0 /
  ``directional`` with a full set of synthesised drivers, so a tenant the
  rollup had deliberately written no row for - the normal state of a tenant
  whose signal cannot be substantiated - was shown a mid-band score and the
  autopilot mode derived from it.
- ``_calculate_emq_from_records`` invented the driver breakdown by multiplying
  the score by fixed coefficients (1.05, 1.10, 0.85, 0.95, 1.15) and fell back
  to ``score - 2.0`` for the previous day.
- ``_calculate_emq_from_variance`` extrapolated a whole EMQ from attribution
  accuracy alone and gave it a "slight boost since this is partial data".
- ``_get_default_volatility`` generated eight weeks of points from a formula,
  ``_get_default_impact`` published $24,350, ``_get_default_benchmarks``
  published a **LinkedIn** row (not a Meta channel at all), and
  ``_get_default_portfolio`` published 156 tenants and $2,450,000 at risk.
- ``get_autopilot_state`` sized budget at risk as ``queued_count * 5000.0``.

The composite is computed with ``app.services.signal_health.weighted_score``
over the four components ``fact_signal_health_daily`` actually carries, so the
number this endpoint publishes is the number the daily rollup wrote and the
trust gate grades - not a second, differently-defined "EMQ". That function
drops unmeasured components, renormalises the rest, and returns ``None`` when
less than ``signal_health_min_component_weight`` of the evidence is present.
"""

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.logic.emq_calculation import (
    determine_autopilot_mode,
)
from app.core.config import settings
from app.models.trust_layer import (
    FactActionsQueue,
    FactAttributionVarianceDaily,
    FactSignalHealthDaily,
    SignalHealthStatus,
)
from app.services.signal_health import (
    COMPONENT_DELIVERY,
    COMPONENT_EMQ,
    COMPONENT_FRESHNESS,
    COMPONENT_RELIABILITY,
    STATUS_CRITICAL,
    STATUS_DEGRADED,
    STATUS_HEALTHY,
    component_weights,
    freshness_component_score,
    status_for_score,
    weighted_score,
)

# Display names for the four components, in the order the UI lists them. They
# are labels only: the values, weights and statuses all come from the shared
# scoring module, so nothing here can drift from what the trust gate enforces.
DRIVER_LABELS: dict[str, str] = {
    COMPONENT_EMQ: "Event Match Quality",
    COMPONENT_FRESHNESS: "Freshness",
    COMPONENT_DELIVERY: "Delivery",
    COMPONENT_RELIABILITY: "API Reliability",
}

# How a component's health band maps onto the three states the UI draws.
_DRIVER_STATUS = {
    STATUS_HEALTHY: "good",
    STATUS_DEGRADED: "warning",
    STATUS_CRITICAL: "critical",
}


def _mean(values: list[float]) -> float | None:
    """
    Average the readings that exist.

    Args:
        values: Measured values; may be empty.

    Returns:
        The mean, or None when nothing was measured.
    """
    present = [float(v) for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


def _clamp(value: float | None) -> float | None:
    """Hold a component inside 0-100, passing None through untouched."""
    if value is None:
        return None
    return min(max(value, 0.0), 100.0)


def confidence_band(score: float | None) -> str | None:
    """
    Grade a composite into the band the UI renders.

    The edges come from configuration rather than from literals here, because
    the frontend badge grades the same number: they used to disagree (the API
    called 80 "reliable" while the UI called it "directional"), which is the
    "two thresholds, one shown and one enforced" failure the trust engine
    documents.

    Args:
        score: The 0-100 composite, or None when it could not be computed.

    Returns:
        ``reliable``, ``directional``, ``unsafe``, or None for no score.
    """
    if score is None:
        return None
    if score >= settings.emq_confidence_reliable_threshold:
        return "reliable"
    if score >= settings.emq_confidence_directional_threshold:
        return "directional"
    return "unsafe"


class EmqService:
    """Service for EMQ operations."""

    SUPPORTED_PLATFORMS = ["meta"]

    def __init__(self, session: AsyncSession):
        self.session = session

    # -------------------------------------------------------------------------
    # Score
    # -------------------------------------------------------------------------
    async def get_emq_score(
        self,
        tenant_id: int,
        target_date: date | None = None,
    ) -> dict[str, Any]:
        """
        Get the EMQ score for a tenant from its persisted signal health.

        Args:
            tenant_id: Tenant ID.
            target_date: Target date (defaults to today).

        Returns:
            Dict with score, previousScore, confidenceBand, drivers and
            lastUpdated. ``score``, ``previousScore`` and ``confidenceBand``
            are None and ``drivers`` is empty for a tenant with no measured
            signal health - there is no fallback score.
        """
        if target_date is None:
            target_date = datetime.now(UTC).date()

        current_records = await self._records_for(tenant_id, target_date)
        previous_records = await self._records_for(tenant_id, target_date - timedelta(days=1))

        components = self._components(current_records)
        previous_components = self._components(previous_records)

        score, _, _ = weighted_score(components)
        previous_score, _, _ = weighted_score(previous_components)

        last_updated = (
            max(r.updated_at for r in current_records)
            if current_records
            else None
        )

        return {
            "score": score,
            "previousScore": previous_score,
            "confidenceBand": confidence_band(score),
            "drivers": self._drivers(components, previous_components),
            "lastUpdated": (
                last_updated.isoformat() if last_updated else datetime.now(UTC).isoformat()
            ),
        }

    async def _records_for(
        self, tenant_id: int, target_date: date
    ) -> list[FactSignalHealthDaily]:
        """Fetch one day's signal health rows for a tenant."""
        result = await self.session.execute(
            select(FactSignalHealthDaily).where(
                and_(
                    FactSignalHealthDaily.tenant_id == tenant_id,
                    FactSignalHealthDaily.date == target_date,
                )
            )
        )
        return list(result.scalars().all())

    @staticmethod
    def _components(
        records: list[FactSignalHealthDaily],
    ) -> dict[str, float | None]:
        """
        Turn a day's rows into the four scoring components.

        The mapping is the one the fact table's columns were built for:
        ``emq_score`` is already 0-100, ``event_loss_pct`` and
        ``api_error_rate`` are losses subtracted from full marks, and
        ``freshness_minutes`` is converted by the shared freshness curve so a
        deployment's fresh/stale bounds apply here too. A column that is NULL
        across every row yields None, which ``weighted_score`` drops rather
        than defaulting.

        Args:
            records: The rows measured for one day; may be empty.

        Returns:
            Component name to 0-100 value, or None where unmeasured.
        """
        emq = _mean([r.emq_score for r in records])
        loss = _mean([r.event_loss_pct for r in records])
        freshness_minutes = _mean([r.freshness_minutes for r in records])
        error_rate = _mean([r.api_error_rate for r in records])

        return {
            COMPONENT_EMQ: _clamp(emq),
            COMPONENT_DELIVERY: _clamp(None if loss is None else 100.0 - loss),
            COMPONENT_FRESHNESS: _clamp(freshness_component_score(freshness_minutes)),
            COMPONENT_RELIABILITY: _clamp(None if error_rate is None else 100.0 - error_rate),
        }

    @staticmethod
    def _drivers(
        components: dict[str, float | None],
        previous: dict[str, float | None],
    ) -> list[dict[str, Any]]:
        """
        Describe each measured component for the UI.

        Only measured components appear. ``trend`` is None unless the same
        component was also measured the day before - there is no direction to
        report against a day that was never recorded.

        Args:
            components: Today's component values.
            previous: The previous day's component values.

        Returns:
            One entry per measured component, in ``DRIVER_LABELS`` order.
        """
        weights = component_weights()
        drivers: list[dict[str, Any]] = []

        for name, label in DRIVER_LABELS.items():
            value = components.get(name)
            if value is None:
                continue

            was = previous.get(name)
            if was is None:
                trend = None
            elif value > was + 1:
                trend = "up"
            elif value < was - 1:
                trend = "down"
            else:
                trend = "flat"

            drivers.append(
                {
                    "name": label,
                    "value": round(value, 1),
                    "weight": weights.get(name, 0.0),
                    "status": _DRIVER_STATUS.get(status_for_score(value), "critical"),
                    "trend": trend,
                }
            )

        return drivers

    # -------------------------------------------------------------------------
    # Confidence
    # -------------------------------------------------------------------------
    async def get_confidence_data(
        self,
        tenant_id: int,
        target_date: date | None = None,
    ) -> dict[str, Any]:
        """
        Get confidence band details for a tenant.

        Args:
            tenant_id: Tenant ID.
            target_date: Target date (defaults to today).

        Returns:
            Dict with band, score, thresholds and per-driver factors. ``band``
            and ``score`` are None when nothing was measured, and ``factors``
            is then empty rather than a list of default contributions.
        """
        emq_data = await self.get_emq_score(tenant_id, target_date)

        factors = []
        for driver in emq_data["drivers"]:
            if driver["value"] >= settings.emq_confidence_reliable_threshold:
                status = "positive"
            elif driver["value"] >= settings.emq_confidence_directional_threshold:
                status = "neutral"
            else:
                status = "negative"

            factors.append(
                {
                    "name": driver["name"],
                    "contribution": round(driver["value"] * driver["weight"], 1),
                    "status": status,
                }
            )

        return {
            "band": emq_data["confidenceBand"],
            "score": emq_data["score"],
            "thresholds": {
                "reliable": float(settings.emq_confidence_reliable_threshold),
                "directional": float(settings.emq_confidence_directional_threshold),
            },
            "factors": factors,
        }

    # -------------------------------------------------------------------------
    # Incidents
    # -------------------------------------------------------------------------
    async def get_incidents(
        self,
        tenant_id: int,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """
        Get EMQ incidents within a date range.

        Args:
            tenant_id: Tenant ID.
            start_date: First day to report.
            end_date: Last day to report.

        Returns:
            One entry per non-OK signal health row. ``emqImpact`` is always
            None: a single row carries no baseline to measure an impact
            against, and the previous code reported the row's distance from a
            hardcoded 80 - or a flat -10 when the row had no score at all.
        """
        query = (
            select(FactSignalHealthDaily)
            .where(
                and_(
                    FactSignalHealthDaily.tenant_id == tenant_id,
                    FactSignalHealthDaily.date >= start_date,
                    FactSignalHealthDaily.date <= end_date,
                    FactSignalHealthDaily.status != SignalHealthStatus.OK,
                )
            )
            .order_by(FactSignalHealthDaily.date.desc())
        )

        result = await self.session.execute(query)
        records = result.scalars().all()

        incidents = []
        for record in records:
            if record.status == SignalHealthStatus.CRITICAL:
                incident_type = "incident_opened"
                severity = "critical"
            elif record.status == SignalHealthStatus.DEGRADED:
                incident_type = "degradation"
                severity = "high"
            else:  # RISK
                incident_type = "degradation"
                severity = "medium"

            issues = json.loads(record.issues) if record.issues else []
            title = issues[0] if issues else f"Signal health {record.status.value}"
            description = "; ".join(issues[1:]) if len(issues) > 1 else None

            incidents.append(
                {
                    "id": str(record.id),
                    "type": incident_type,
                    "title": title,
                    "description": description,
                    "timestamp": record.created_at.isoformat(),
                    "platform": record.platform,
                    "severity": severity,
                    "recoveryHours": None,
                    "emqImpact": None,
                }
            )

        return incidents

    # -------------------------------------------------------------------------
    # Volatility
    # -------------------------------------------------------------------------
    async def get_volatility(
        self,
        tenant_id: int,
        weeks: int = 8,
    ) -> dict[str, Any]:
        """
        Get the signal volatility index and its weekly series.

        Args:
            tenant_id: Tenant ID.
            weeks: How many weeks back to measure.

        Returns:
            Dict with svi, trend and weeklyData. All three are empty or None
            for a tenant with no history: the previous code generated eight
            weeks of points from ``12.5 + i * 0.8 - (i % 3) * 2.1`` and an SVI
            of 15.3.
        """
        end_date = datetime.now(UTC).date()
        start_date = end_date - timedelta(weeks=weeks)

        week_col = func.date_trunc("week", FactSignalHealthDaily.date).label("week")
        query = (
            select(
                week_col,
                func.avg(FactSignalHealthDaily.emq_score).label("avg_score"),
                func.stddev(FactSignalHealthDaily.emq_score).label("stddev"),
            )
            .where(
                and_(
                    FactSignalHealthDaily.tenant_id == tenant_id,
                    FactSignalHealthDaily.date >= start_date,
                    FactSignalHealthDaily.date <= end_date,
                )
            )
            .group_by(week_col)
            .order_by(week_col)
        )

        result = await self.session.execute(query)
        weekly_data = result.all()

        stddevs = [row.stddev for row in weekly_data if row.stddev is not None]
        svi = round(sum(stddevs) / len(stddevs), 1) if stddevs else None

        data_points = [
            {
                "date": row.week.strftime("%Y-%m-%d"),
                "value": round(row.stddev, 1),
            }
            for row in weekly_data
            if row.stddev is not None
        ]

        # A direction needs two ends to compare. One point is a reading, not a
        # trend, and no points is not "stable".
        if len(data_points) >= 4:
            recent = sum(d["value"] for d in data_points[-2:]) / 2
            older = sum(d["value"] for d in data_points[:2]) / 2
            if recent < older - 2:
                trend = "decreasing"
            elif recent > older + 2:
                trend = "increasing"
            else:
                trend = "stable"
        else:
            trend = None

        return {
            "svi": svi,
            "trend": trend,
            "weeklyData": data_points,
        }

    # -------------------------------------------------------------------------
    # Autopilot
    # -------------------------------------------------------------------------
    async def get_autopilot_state(
        self,
        tenant_id: int,
    ) -> dict[str, Any]:
        """
        Get the autopilot state implied by the tenant's measured signal health.

        Args:
            tenant_id: Tenant ID.

        Returns:
            Dict with mode, reason, budgetAtRisk and the allowed/restricted
            action lists. With no measured score the mode is ``frozen``: the
            trust gate fails closed on absent signal health, so reporting
            anything more permissive here would contradict what the gate will
            actually do. ``budgetAtRisk`` is None - ``fact_actions_queue``
            carries no budget column, and the previous code multiplied the
            queued row count by a flat $5,000.
        """
        emq_data = await self.get_emq_score(tenant_id)
        score = emq_data["score"]

        if score is None:
            mode = "frozen"
            reason = "No signal health has been measured; automation is held closed"
        else:
            mode, reason = determine_autopilot_mode(score)

        mode_config = {
            "normal": {
                "allowed": [
                    "pause_underperforming",
                    "reduce_budget",
                    "increase_budget",
                    "update_audiences",
                    "launch_new_campaigns",
                    "expand_targeting",
                ],
                "restricted": [],
            },
            "limited": {
                "allowed": [
                    "pause_underperforming",
                    "reduce_budget",
                    "update_audiences",
                ],
                "restricted": [
                    "increase_budget",
                    "launch_new_campaigns",
                    "expand_targeting",
                ],
            },
            "cuts_only": {
                "allowed": [
                    "pause_underperforming",
                    "reduce_budget",
                ],
                "restricted": [
                    "update_audiences",
                    "increase_budget",
                    "launch_new_campaigns",
                    "expand_targeting",
                ],
            },
            "frozen": {
                "allowed": [],
                "restricted": [
                    "pause_underperforming",
                    "reduce_budget",
                    "update_audiences",
                    "increase_budget",
                    "launch_new_campaigns",
                    "expand_targeting",
                ],
            },
        }

        config = mode_config[mode]

        return {
            "mode": mode,
            "reason": reason,
            "budgetAtRisk": None,
            "allowedActions": config["allowed"],
            "restrictedActions": config["restricted"],
        }

    async def count_queued_actions(self, tenant_id: int) -> int:
        """
        Count the actions currently waiting in the tenant's queue.

        A real count, kept because it is the honest part of what budget at risk
        used to be derived from.

        Args:
            tenant_id: Tenant ID.

        Returns:
            The number of queued rows.
        """
        result = await self.session.execute(
            select(func.count()).where(
                and_(
                    FactActionsQueue.tenant_id == tenant_id,
                    FactActionsQueue.status == "queued",
                )
            )
        )
        return result.scalar() or 0

    # -------------------------------------------------------------------------
    # Impact
    # -------------------------------------------------------------------------
    async def get_impact(
        self,
        tenant_id: int,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        """
        Report measured attribution variance per platform.

        Args:
            tenant_id: Tenant ID.
            start_date: First day to include.
            end_date: Last day to include.

        Returns:
            Dict with totalImpact, currency and a per-platform breakdown.
            ``actualRoas`` and ``confidence`` are measured from
            ``fact_attribution_variance_daily``. ``estimatedRoas``,
            ``revenueImpact`` and ``totalImpact`` are None: nothing in this
            system models what ROAS *would* be under perfect attribution, and
            the previous code simply asserted a 15% improvement and took a
            tenth of it as recovered revenue.
        """
        query = select(FactAttributionVarianceDaily).where(
            and_(
                FactAttributionVarianceDaily.tenant_id == tenant_id,
                FactAttributionVarianceDaily.date >= start_date,
                FactAttributionVarianceDaily.date <= end_date,
            )
        )
        result = await self.session.execute(query)
        records = result.scalars().all()

        platform_data: dict[str, dict] = {}
        for record in records:
            data = platform_data.setdefault(
                record.platform,
                {
                    "platform_revenue": 0.0,
                    "ga4_revenue": 0.0,
                    "confidence_sum": 0.0,
                    "count": 0,
                },
            )
            data["platform_revenue"] += record.platform_revenue
            data["ga4_revenue"] += record.ga4_revenue
            data["confidence_sum"] += record.confidence
            data["count"] += 1

        breakdown = []
        for platform, data in platform_data.items():
            if data["ga4_revenue"] <= 0 or data["count"] == 0:
                continue
            breakdown.append(
                {
                    "platform": platform.title(),
                    "actualRoas": round(data["platform_revenue"] / data["ga4_revenue"], 2),
                    "estimatedRoas": None,
                    "confidence": round(data["confidence_sum"] / data["count"], 2),
                    "revenueImpact": None,
                }
            )

        return {
            "totalImpact": None,
            "currency": "USD",
            "breakdown": breakdown,
        }


class EmqAdminService:
    """Service for super admin EMQ operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_benchmarks(
        self,
        target_date: date | None = None,
        platform: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Get EMQ percentile benchmarks across all tenants.

        Args:
            target_date: Day to measure (defaults to today).
            platform: Optional platform filter.

        Returns:
            One entry per platform with measured percentiles, or an empty list
            when no tenant has a score that day. ``tenantScore`` and
            ``percentile`` are None: this endpoint has no tenant scope, and the
            previous code filled them with the cross-tenant average and with
            that average restated as a percentile. The removed no-data fallback
            also published a **LinkedIn** benchmark, which is not a Meta
            channel and never had a source.
        """
        if target_date is None:
            target_date = datetime.now(UTC).date()

        query = select(
            FactSignalHealthDaily.platform,
            func.percentile_cont(0.25).within_group(FactSignalHealthDaily.emq_score).label("p25"),
            func.percentile_cont(0.50).within_group(FactSignalHealthDaily.emq_score).label("p50"),
            func.percentile_cont(0.75).within_group(FactSignalHealthDaily.emq_score).label("p75"),
        ).where(
            and_(
                FactSignalHealthDaily.date == target_date,
                FactSignalHealthDaily.emq_score.isnot(None),
            )
        )

        if platform:
            query = query.where(FactSignalHealthDaily.platform == platform.lower())

        query = query.group_by(FactSignalHealthDaily.platform)

        result = await self.session.execute(query)
        rows = result.all()

        return [
            {
                "platform": row.platform.title(),
                "p25": round(row.p25, 1) if row.p25 is not None else None,
                "p50": round(row.p50, 1) if row.p50 is not None else None,
                "p75": round(row.p75, 1) if row.p75 is not None else None,
                "tenantScore": None,
                "percentile": None,
            }
            for row in rows
        ]

    async def get_portfolio(
        self,
        target_date: date | None = None,
    ) -> dict[str, Any]:
        """
        Get the portfolio-wide EMQ overview.

        Args:
            target_date: Day to measure (defaults to today).

        Returns:
            Dict with the measured tenant counts by band and average score.
            ``atRiskBudget`` is None and ``topIssues`` is empty: there is no
            per-driver storage to rank issues from, and the previous code
            assigned five fixed issue names a share of the tenant count (50%,
            30%, 25%, 20%, 15%) and sized the budget as
            ``(directional + unsafe * 2) * 50000``. With no rows at all the
            counts are zero rather than the 156 tenants and $2,450,000 the
            removed fallback published.
        """
        if target_date is None:
            target_date = datetime.now(UTC).date()

        reliable_edge = float(settings.emq_confidence_reliable_threshold)
        directional_edge = float(settings.emq_confidence_directional_threshold)

        query = select(
            func.count(func.distinct(FactSignalHealthDaily.tenant_id)).label("total"),
            func.count(func.distinct(FactSignalHealthDaily.tenant_id))
            .filter(FactSignalHealthDaily.emq_score >= reliable_edge)
            .label("reliable"),
            func.count(func.distinct(FactSignalHealthDaily.tenant_id))
            .filter(
                and_(
                    FactSignalHealthDaily.emq_score >= directional_edge,
                    FactSignalHealthDaily.emq_score < reliable_edge,
                )
            )
            .label("directional"),
            func.count(func.distinct(FactSignalHealthDaily.tenant_id))
            .filter(FactSignalHealthDaily.emq_score < directional_edge)
            .label("unsafe"),
            func.avg(FactSignalHealthDaily.emq_score).label("avg_score"),
        ).where(
            and_(
                FactSignalHealthDaily.date == target_date,
                FactSignalHealthDaily.emq_score.isnot(None),
            )
        )

        result = await self.session.execute(query)
        row = result.one_or_none()

        if row is None or not row.total:
            return {
                "totalTenants": 0,
                "byBand": {"reliable": 0, "directional": 0, "unsafe": 0},
                "atRiskBudget": None,
                "avgScore": None,
                "topIssues": [],
            }

        return {
            "totalTenants": row.total,
            "byBand": {
                "reliable": row.reliable or 0,
                "directional": row.directional or 0,
                "unsafe": row.unsafe or 0,
            },
            "atRiskBudget": None,
            "avgScore": round(row.avg_score, 1) if row.avg_score is not None else None,
            "topIssues": [],
        }
