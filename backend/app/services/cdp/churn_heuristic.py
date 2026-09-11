"""Heuristic churn scoring for CDP profiles (no ML, no Meta writes)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional


def score_churn_risk(
    *,
    last_seen_at: Optional[datetime],
    lifecycle_stage: Optional[str],
    total_purchases: Optional[int],
    now: Optional[datetime] = None,
) -> tuple[float, int, list[dict[str, Any]]]:
    """
    Return (probability 0.01-0.99, days_inactive, top_factors).

    Uses recency and lifecycle only. Safe for empty tenants (callers still
    return an empty list when no profiles exist).
    """
    now = now or datetime.now(UTC)
    last_seen = last_seen_at or now
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    ref = now if now.tzinfo else now.replace(tzinfo=UTC)
    days_inactive = max(0, (ref - last_seen).days)

    score = 0.15
    factors: list[dict[str, Any]] = []

    if days_inactive >= 90:
        score += 0.45
        factors.append(
            {
                "name": "inactivity",
                "impact": 45,
                "direction": "increases",
                "value": f"{days_inactive}d",
            }
        )
    elif days_inactive >= 45:
        score += 0.30
        factors.append(
            {
                "name": "inactivity",
                "impact": 30,
                "direction": "increases",
                "value": f"{days_inactive}d",
            }
        )
    elif days_inactive >= 21:
        score += 0.18
        factors.append(
            {
                "name": "inactivity",
                "impact": 18,
                "direction": "increases",
                "value": f"{days_inactive}d",
            }
        )
    else:
        factors.append(
            {
                "name": "inactivity",
                "impact": -10,
                "direction": "decreases",
                "value": f"{days_inactive}d",
            }
        )

    stage = (lifecycle_stage or "").lower()
    if stage in {"churned", "inactive", "dormant"}:
        score += 0.25
        factors.append(
            {"name": "lifecycle", "impact": 25, "direction": "increases", "value": stage}
        )
    elif stage in {"at_risk", "hibernating"}:
        score += 0.15
        factors.append(
            {"name": "lifecycle", "impact": 15, "direction": "increases", "value": stage}
        )
    elif stage in {"active", "loyal", "champion"}:
        score -= 0.10
        factors.append(
            {"name": "lifecycle", "impact": -10, "direction": "decreases", "value": stage}
        )

    purchases = int(total_purchases or 0)
    if purchases == 0:
        score += 0.10
        factors.append(
            {"name": "no_purchases", "impact": 10, "direction": "increases", "value": "0"}
        )
    elif purchases >= 3:
        score -= 0.08
        factors.append(
            {
                "name": "repeat_purchases",
                "impact": -8,
                "direction": "decreases",
                "value": str(purchases),
            }
        )

    score = max(0.01, min(0.99, score))
    return score, days_inactive, factors
