# =============================================================================
# Stratum AI - Trust Gate Regression Tests
# =============================================================================
"""
Regression tests for the fail-open trust gate.

``check_signal_health`` used to query fact_signal_health_daily for TODAY and,
finding no rows, return True with the comment "No data means we proceed
cautiously" - so the automation executed. It was the only gate on both
execution paths, and because the rollup that fills that table never ran, the
table was permanently empty and the gate permanently open.

That inverts the contract in docs/architecture/trust-engine.md ("Never
auto-execute when signal_health < 70"; BLOCK below 40). These tests pin the
fail-closed behaviour: no data, stale data or unhealthy data must never
produce a PASS.
"""

from datetime import date, timedelta

import pytest

from app.core.config import settings
from app.models.autopilot import EnforcementMode, TenantEnforcementSettings
from app.models.trust_layer import FactSignalHealthDaily, SignalHealthStatus
from app.tasks.apply_actions_queue import (
    GateDecision,
    _decision_for_score,
    check_signal_health,
    evaluate_signal_health,
)

TENANT_ID = 1
TODAY = date(2026, 3, 10)
YESTERDAY = TODAY - timedelta(days=1)


# =============================================================================
# Test doubles
# =============================================================================


class _FakeScalars:
    """Stand-in for the object returned by ``Result.scalars()``."""

    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        """Return every row."""
        return list(self._rows)


class _FakeResult:
    """Stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, rows=(), scalar=None):
        self._rows = list(rows)
        self._scalar = scalar

    def scalars(self):
        """Return the row accessor."""
        return _FakeScalars(self._rows)

    def scalar_one_or_none(self):
        """Return the single scalar value, or None."""
        return self._scalar

    def first(self):
        """Return the first row, or None."""
        return self._rows[0] if self._rows else None


class FakeSession:
    """
    Minimal AsyncSession stand-in that answers the gate's four queries.

    Dispatches on the compiled SQL so the test does not depend on call order:
    the newest-snapshot aggregate, the rows for that snapshot, the tenant
    enforcement settings, and the tenant's own trust thresholds.
    """

    def __init__(self, *, newest_date=None, rows=(), enforcement=None, onboarding=None):
        self.newest_date = newest_date
        self.rows = list(rows)
        self.enforcement = enforcement
        # (trust_threshold_autopilot, trust_threshold_alert) or None when the
        # tenant has no onboarding record and takes the configured defaults.
        self.onboarding = onboarding
        self.queries: list[str] = []

    async def execute(self, statement):
        """Answer one of the gate's queries based on the statement's SQL."""
        sql = str(statement)
        self.queries.append(sql)
        if "tenant_onboarding" in sql:
            return _FakeResult(rows=[self.onboarding] if self.onboarding else [])
        if "tenant_enforcement_settings" in sql:
            return _FakeResult(scalar=self.enforcement)
        if "max(" in sql.lower():
            return _FakeResult(scalar=self.newest_date)
        if "fact_signal_health_daily" in sql:
            return _FakeResult(rows=self.rows)
        raise AssertionError(f"unexpected query: {sql}")


def health_row(
    platform: str = "facebook",
    *,
    emq_score: float | None = 95.0,
    event_loss_pct: float | None = 1.0,
    freshness_minutes: int | None = 30,
    api_error_rate: float | None = 0.2,
    status: SignalHealthStatus = SignalHealthStatus.OK,
    row_date: date = YESTERDAY,
) -> FactSignalHealthDaily:
    """Build an unsaved fact_signal_health_daily row for the gate to score."""
    return FactSignalHealthDaily(
        tenant_id=TENANT_ID,
        date=row_date,
        platform=platform,
        emq_score=emq_score,
        event_loss_pct=event_loss_pct,
        freshness_minutes=freshness_minutes,
        api_error_rate=api_error_rate,
        status=status,
    )


# Component sets chosen to land in each documented band. The tests assert the
# band, not the exact score, so the weights can move without breaking them.
HEALTHY_ROW: dict[str, float | int] = {}
DEGRADED_ROW: dict[str, float | int] = {
    "emq_score": 60.0,
    "event_loss_pct": 30.0,
    "freshness_minutes": 740,
    "api_error_rate": 20.0,
}
CRITICAL_ROW: dict[str, float | int] = {
    "emq_score": 20.0,
    "event_loss_pct": 80.0,
    "freshness_minutes": 1440,
    "api_error_rate": 90.0,
}


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    """Freeze the gate's notion of "today" so date maths is deterministic."""

    class _FrozenDatetime:
        @staticmethod
        def now(_tz=None):
            class _Now:
                @staticmethod
                def date():
                    return TODAY

            return _Now()

    monkeypatch.setattr("app.tasks.apply_actions_queue.datetime", _FrozenDatetime)


# =============================================================================
# The regression: no data must not mean "proceed"
# =============================================================================


class TestFailsClosedWithoutData:
    """Absence of signal health is not evidence of health."""

    async def test_no_signal_health_rows_blocks_execution(self):
        """
        THE D3 REGRESSION.

        With an empty fact_signal_health_daily the gate must BLOCK. The old
        implementation returned True here and the action executed.
        """
        db = FakeSession(newest_date=None, rows=[])

        result = await check_signal_health(db, TENANT_ID)

        assert result.decision is GateDecision.BLOCK
        assert result.may_execute is False
        assert "No signal health data" in result.reason

    async def test_snapshot_date_without_rows_blocks(self):
        """A snapshot with no channel rows carries no health either."""
        db = FakeSession(newest_date=YESTERDAY, rows=[])

        result = await check_signal_health(db, TENANT_ID)

        assert result.decision is GateDecision.BLOCK
        assert result.may_execute is False


# =============================================================================
# The documented decision table
# =============================================================================


class TestDocumentedThresholds:
    """HEALTHY >= 70 PASS, 40-69 HOLD, < 40 BLOCK - read from config."""

    async def test_healthy_health_executes(self):
        """A fresh, healthy snapshot is the only thing that opens the gate."""
        db = FakeSession(newest_date=YESTERDAY, rows=[health_row(**HEALTHY_ROW)])

        result = await check_signal_health(db, TENANT_ID)

        assert result.score >= settings.signal_health_healthy_threshold
        assert result.decision is GateDecision.PASS
        assert result.may_execute is True

    async def test_degraded_health_holds_and_alerts_without_executing(self):
        """40-69 is alert-only: the action is held, never applied."""
        db = FakeSession(newest_date=YESTERDAY, rows=[health_row(**DEGRADED_ROW)])

        result = await check_signal_health(db, TENANT_ID)

        assert (
            settings.signal_health_degraded_threshold
            <= result.score
            < settings.signal_health_healthy_threshold
        )
        assert result.decision is GateDecision.HOLD
        assert result.may_execute is False
        assert result.should_alert is True

    async def test_critical_health_blocks(self):
        """Below 40 the action is blocked for manual review."""
        db = FakeSession(newest_date=YESTERDAY, rows=[health_row(**CRITICAL_ROW)])

        result = await check_signal_health(db, TENANT_ID)

        assert result.score < settings.signal_health_degraded_threshold
        assert result.decision is GateDecision.BLOCK
        assert result.may_execute is False

    async def test_thresholds_are_read_from_config_not_hardcoded(self, monkeypatch):
        """Raising the healthy threshold must change the decision."""
        db = FakeSession(newest_date=YESTERDAY, rows=[health_row(**HEALTHY_ROW)])
        assert (await check_signal_health(db, TENANT_ID)).decision is GateDecision.PASS

        monkeypatch.setattr(settings, "signal_health_healthy_threshold", 99.9)
        monkeypatch.setattr(settings, "signal_health_degraded_threshold", 50.0)

        result = await check_signal_health(db, TENANT_ID)
        assert result.decision is GateDecision.HOLD
        assert result.may_execute is False

    async def test_worst_channel_decides_for_the_tenant(self):
        """One critical Meta channel holds back the whole tenant."""
        db = FakeSession(
            newest_date=YESTERDAY,
            rows=[
                health_row("facebook", **HEALTHY_ROW),
                health_row("instagram", **CRITICAL_ROW),
            ],
        )

        result = await check_signal_health(db, TENANT_ID)

        assert result.decision is GateDecision.BLOCK
        assert result.channels["facebook"]["decision"] == GateDecision.PASS.value
        assert result.channels["instagram"]["decision"] == GateDecision.BLOCK.value

    async def test_critical_status_blocks_despite_healthy_components(self):
        """The rollup's own status floors the outcome; it cannot be scored away."""
        db = FakeSession(
            newest_date=YESTERDAY,
            rows=[health_row(status=SignalHealthStatus.CRITICAL, **HEALTHY_ROW)],
        )

        result = await check_signal_health(db, TENANT_ID)

        assert result.score >= settings.signal_health_healthy_threshold
        assert result.decision is GateDecision.BLOCK

    async def test_row_without_any_components_blocks(self):
        """A row whose component columns are all NULL scores nothing, so it blocks."""
        db = FakeSession(
            newest_date=YESTERDAY,
            rows=[
                health_row(
                    emq_score=None,
                    event_loss_pct=None,
                    freshness_minutes=None,
                    api_error_rate=None,
                )
            ],
        )

        result = await check_signal_health(db, TENANT_ID)

        assert result.decision is GateDecision.BLOCK


# =============================================================================
# Freshness
# =============================================================================


class TestFreshness:
    """A row from an older date is not today's health."""

    async def test_stale_row_does_not_count_as_healthy(self):
        """A perfect snapshot from last month must not open the gate."""
        stale_date = TODAY - timedelta(days=30)
        db = FakeSession(
            newest_date=stale_date,
            rows=[health_row(row_date=stale_date, **HEALTHY_ROW)],
        )

        result = await check_signal_health(db, TENANT_ID)

        assert result.may_execute is False
        assert result.decision in (GateDecision.HOLD, GateDecision.BLOCK)
        assert "stale" in result.reason.lower() or "old" in result.reason.lower()

    async def test_stale_decision_is_configurable(self, monkeypatch):
        """Operators can escalate stale-health from hold to block."""
        stale_date = TODAY - timedelta(days=30)
        rows = [health_row(row_date=stale_date, **HEALTHY_ROW)]

        monkeypatch.setattr(settings, "trust_gate_stale_health_decision", "hold")
        held = evaluate_signal_health(rows, stale_date, TODAY, EnforcementMode.ADVISORY.value)
        assert held.decision is GateDecision.HOLD

        monkeypatch.setattr(settings, "trust_gate_stale_health_decision", "block")
        blocked = evaluate_signal_health(rows, stale_date, TODAY, EnforcementMode.ADVISORY.value)
        assert blocked.decision is GateDecision.BLOCK

    async def test_yesterdays_rollup_still_counts(self):
        """
        The rollup writes rows dated for the previous day, so yesterday's
        snapshot is the newest one that can exist and must remain usable.
        """
        db = FakeSession(newest_date=YESTERDAY, rows=[health_row(**HEALTHY_ROW)])

        assert (await check_signal_health(db, TENANT_ID)).decision is GateDecision.PASS

    async def test_max_age_is_configurable(self, monkeypatch):
        """Tightening the age window turns yesterday's snapshot stale."""
        rows = [health_row(**HEALTHY_ROW)]

        monkeypatch.setattr(settings, "trust_gate_max_health_age_days", 0)
        result = evaluate_signal_health(rows, YESTERDAY, TODAY, EnforcementMode.ADVISORY.value)

        assert result.decision is not GateDecision.PASS


# =============================================================================
# Tenant enforcement mode
# =============================================================================


class TestEnforcementMode:
    """The existing enforcement mode may tighten the gate, never loosen it."""

    async def test_hard_block_escalates_a_hold_to_a_block(self):
        """Hard-block tenants get nothing below the healthy threshold."""
        db = FakeSession(
            newest_date=YESTERDAY,
            rows=[health_row(**DEGRADED_ROW)],
            enforcement=TenantEnforcementSettings(
                tenant_id=TENANT_ID, default_mode=EnforcementMode.HARD_BLOCK
            ),
        )

        result = await check_signal_health(db, TENANT_ID)

        assert result.decision is GateDecision.BLOCK
        assert result.enforcement_mode == EnforcementMode.HARD_BLOCK.value

    @pytest.mark.parametrize(
        "mode",
        [
            EnforcementMode.ADVISORY.value,
            EnforcementMode.SOFT_BLOCK.value,
            EnforcementMode.HARD_BLOCK.value,
        ],
    )
    async def test_no_mode_can_open_a_closed_gate(self, mode):
        """No enforcement mode may turn a BLOCK into a PASS."""
        for rows, health_date in (
            ([], None),
            ([health_row(**CRITICAL_ROW)], YESTERDAY),
        ):
            result = evaluate_signal_health(rows, health_date, TODAY, mode)
            assert result.may_execute is False

    async def test_defaults_to_advisory_when_unconfigured(self):
        """A tenant with no enforcement row still gets the documented gate."""
        db = FakeSession(newest_date=YESTERDAY, rows=[health_row(**HEALTHY_ROW)])

        result = await check_signal_health(db, TENANT_ID)

        assert result.enforcement_mode == EnforcementMode.ADVISORY.value


# =============================================================================
# Auditability
# =============================================================================


class TestAuditPayload:
    """Held and blocked actions must be auditable with their inputs."""

    async def test_audit_dict_carries_reason_thresholds_and_components(self):
        """The audit entry explains the decision, not just its outcome."""
        db = FakeSession(newest_date=YESTERDAY, rows=[health_row(**DEGRADED_ROW)])

        payload = (await check_signal_health(db, TENANT_ID)).to_audit_dict()

        assert payload["decision"] == GateDecision.HOLD.value
        assert payload["reason"]
        assert payload["healthy_threshold"] == settings.signal_health_healthy_threshold
        assert payload["degraded_threshold"] == settings.signal_health_degraded_threshold
        assert payload["signal_health_date"] == YESTERDAY.isoformat()
        assert payload["enforcement_mode"] == EnforcementMode.ADVISORY.value

        channel = payload["channels"]["facebook"]
        assert set(channel["components"]) == {"emq", "freshness", "delivery", "reliability"}
        assert channel["status"] == SignalHealthStatus.OK.value


# =============================================================================
# Sparse rows: absence of data at column granularity
# =============================================================================


class TestSparseRowsAreNotHealth:
    """Renormalising over a nearly-empty row must not manufacture a PASS.

    Every metric column is nullable and ``status`` is NOT NULL defaulting to
    OK, so a row carrying one trivially-perfect component used to renormalise
    to a flat 100 and PASS - the D3 inversion again, one level down.
    """

    @pytest.mark.parametrize(
        "populated",
        [
            {"api_error_rate": 0.0},
            {"event_loss_pct": 0.0},
            {"freshness_minutes": 0},
            {"emq_score": 100.0},
        ],
        ids=["only-api-error-rate", "only-event-loss", "only-freshness", "only-emq"],
    )
    def test_single_perfect_component_does_not_pass(self, populated):
        """One populated column is not enough evidence to run automation."""
        empty = {
            "emq_score": None,
            "event_loss_pct": None,
            "freshness_minutes": None,
            "api_error_rate": None,
        }
        row = health_row(**{**empty, **populated})

        result = evaluate_signal_health(
            [row], YESTERDAY, TODAY, EnforcementMode.ADVISORY.value
        )

        assert result.decision is not GateDecision.PASS
        assert result.may_execute is False
        assert result.score is None

    def test_enough_populated_weight_still_scores(self):
        """The floor must not block rows that do carry real evidence."""
        row = health_row(event_loss_pct=None, api_error_rate=None)

        result = evaluate_signal_health(
            [row], YESTERDAY, TODAY, EnforcementMode.ADVISORY.value
        )

        assert result.decision is GateDecision.PASS
        assert result.score is not None

    def test_minimum_component_weight_is_configurable(self, monkeypatch):
        """The floor comes from config, not a literal at the call site."""
        row = health_row(
            emq_score=95.0,
            event_loss_pct=None,
            freshness_minutes=None,
            api_error_rate=None,
        )

        monkeypatch.setattr(settings, "signal_health_min_component_weight", 0.9)
        strict = evaluate_signal_health(
            [row], YESTERDAY, TODAY, EnforcementMode.ADVISORY.value
        )
        assert strict.score is None

        monkeypatch.setattr(settings, "signal_health_min_component_weight", 0.25)
        lenient = evaluate_signal_health(
            [row], YESTERDAY, TODAY, EnforcementMode.ADVISORY.value
        )
        assert lenient.score is not None


# =============================================================================
# Future-dated rows
# =============================================================================


class TestFutureDatedRows:
    """A row that cannot exist yet is invalid, not fresher than fresh."""

    def test_future_dated_row_does_not_pass(self):
        """Negative age used to skip the staleness branch and score as fresh."""
        future = TODAY + timedelta(days=88)

        result = evaluate_signal_health(
            [health_row(row_date=future)],
            future,
            TODAY,
            EnforcementMode.ADVISORY.value,
        )

        assert result.decision is not GateDecision.PASS
        assert result.may_execute is False
        assert "future" in result.reason.lower()

    @pytest.mark.asyncio
    async def test_newest_snapshot_query_excludes_future_rows(self):
        """max(date) must be bounded by today.

        Without the bound, one row written with a future date (clock skew, or
        a rollup invoked with a bad target_date) is the max() forever and hides
        every real current snapshot behind it.
        """
        session = FakeSession(
            newest_date=YESTERDAY,
            rows=[health_row(row_date=YESTERDAY, **CRITICAL_ROW)],
        )

        result = await check_signal_health(session, TENANT_ID)

        aggregate_sql = next(sql for sql in session.queries if "max(" in sql.lower())
        assert "fact_signal_health_daily.date <=" in aggregate_sql, (
            "the newest-snapshot query must exclude future-dated rows; "
            f"got: {aggregate_sql}"
        )
        assert result.health_date == YESTERDAY
        assert result.decision is GateDecision.BLOCK


class TestExactScoreBoundaries:
    """Pin 69 / 70 / 71 and 39 / 40 against the documented >= contract.

    Banded component fixtures in TestDocumentedThresholds prove the bands in
    general; these tests pin the integer edges the launch gate requires.
    """

    def test_score_none_blocks(self):
        assert _decision_for_score(None) is GateDecision.BLOCK

    def test_score_39_blocks(self):
        assert _decision_for_score(39.0) is GateDecision.BLOCK

    def test_score_40_holds(self):
        assert _decision_for_score(40.0) is GateDecision.HOLD

    def test_score_69_holds(self):
        assert _decision_for_score(69.0) is GateDecision.HOLD

    def test_score_70_passes(self):
        assert _decision_for_score(70.0) is GateDecision.PASS

    def test_score_71_passes(self):
        assert _decision_for_score(71.0) is GateDecision.PASS
