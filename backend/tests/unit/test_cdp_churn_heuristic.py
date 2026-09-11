"""Unit tests for CDP heuristic churn scoring."""

from datetime import UTC, datetime, timedelta

from app.services.cdp.churn_heuristic import score_churn_risk


def test_recent_active_customer_is_low_risk():
    now = datetime(2026, 3, 15, tzinfo=UTC)
    score, days, factors = score_churn_risk(
        last_seen_at=now - timedelta(days=2),
        lifecycle_stage="active",
        total_purchases=5,
        now=now,
    )
    assert days == 2
    assert score < 0.4
    assert any(f["name"] == "lifecycle" for f in factors)


def test_long_inactive_churned_is_high_risk():
    now = datetime(2026, 3, 15, tzinfo=UTC)
    score, days, factors = score_churn_risk(
        last_seen_at=now - timedelta(days=120),
        lifecycle_stage="churned",
        total_purchases=0,
        now=now,
    )
    assert days == 120
    assert score >= 0.7
    assert any(f["name"] == "inactivity" for f in factors)
    assert any(f["name"] == "no_purchases" for f in factors)


def test_score_is_clamped():
    now = datetime(2026, 3, 15, tzinfo=UTC)
    score, _, _ = score_churn_risk(
        last_seen_at=now - timedelta(days=400),
        lifecycle_stage="churned",
        total_purchases=0,
        now=now,
    )
    assert 0.01 <= score <= 0.99
