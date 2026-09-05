# =============================================================================
# Stratum AI - Real Signal Health Tests
# =============================================================================
"""
Regression tests for the fabricated signal health defect.

Signal health is the number the whole product is gated on, and it used to be
invented in three places:

S1 ``app/tasks/signal_health_rollup.py::fetch_platform_metrics`` returned
   ``emq_score`` 85 or 65, ``event_loss_pct`` 3.5 or 15, ``api_error_rate`` 0.5
   or 8 and a freshness defaulting to 30 minutes, chosen purely on whether the
   Meta connection had ever recorded an error - and wrote them into
   ``fact_signal_health_daily``, the table the trust gate grades on.
S2 ``app/api/v1/endpoints/dashboard.py`` published ``overall_score=85``,
   ``emq_score=0.92`` and ``data_freshness_minutes=5`` for any tenant that
   merely had campaigns or a connected platform.
S3 ``app/services/emq_measurement_service.py`` read a process-local, untenanted
   Python list and derived "history" from ``hash(f"{tenant_id}{i}")``.

What is pinned here: signal health is computed from real, tenant-scoped,
persisted data; a tenant without that data gets ``insufficient_data`` naming
what is missing rather than a plausible number; the rollup writes no row for
such a tenant so the trust gate holds instead of passing; thresholds and
weights come from configuration; and one tenant's delivery logs can never
reach another tenant's score.
"""

import pathlib
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import dashboard as dashboard_module
from app.core.config import settings
from app.models.trust_layer import FactSignalHealthDaily, SignalHealthStatus
from app.services.signal_health import (
    COMPONENT_DELIVERY,
    COMPONENT_EMQ,
    COMPONENT_FRESHNESS,
    COMPONENT_RELIABILITY,
    STATUS_INSUFFICIENT_DATA,
    component_weights,
    compute_signal_health,
    day_window,
    weighted_score,
)
from app.stratum.core.trust_gate import GateDecision
from app.tasks import signal_health_rollup as rollup_module
from app.tasks.apply_actions_queue import evaluate_signal_health

BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]

TENANT = 1
OTHER_TENANT = 2
NOW = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
ROLLUP_DATE = date(2026, 3, 9)


# =============================================================================
# Test doubles
# =============================================================================


class _DeliveryRow:
    """One persisted ``capi_delivery_logs`` row."""

    def __init__(
        self,
        tenant_id: int,
        platform: str,
        delivery_time: datetime,
        status: str = "success",
        user_data_hash: str | None = "abc",
    ) -> None:
        """Store the row's columns."""
        self.tenant_id = tenant_id
        self.platform = platform
        self.delivery_time = delivery_time
        self.status = status
        self.user_data_hash = user_data_hash


class _Result:
    """Stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, *, row=None, rows=(), scalar=None) -> None:
        """Store what this result should hand back."""
        self._row = row
        self._rows = list(rows)
        self._scalar = scalar

    def one(self):
        """Return the single aggregate row."""
        return self._row

    def all(self):
        """Return every row."""
        return list(self._rows)

    def scalars(self):
        """Return the row accessor."""
        return self

    def first(self):
        """Return the first row, or None."""
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        """Return the single scalar, or None."""
        return self._scalar


class FakeSession:
    """
    An AsyncSession stand-in backed by a multi-tenant in-memory store.

    It answers the delivery aggregate **using only the predicates the query
    actually carries**: the compiled statement's bind parameters are read back
    and applied to the store. A query that forgot its ``tenant_id`` filter
    therefore sees every tenant's rows, which is exactly what the tenant
    isolation test asserts must not happen - the filter is proven, not assumed.
    """

    def __init__(
        self,
        *,
        delivery_rows=(),
        last_synced_at: dict[int, datetime] | None = None,
        connections: dict[int, object] | None = None,
        onboarding=None,
        health_rows=(),
        health_date: date | None = None,
    ) -> None:
        """Seed the store with rows belonging to several tenants."""
        self.delivery_rows = list(delivery_rows)
        self.last_synced_at = last_synced_at or {}
        self.connections = connections or {}
        # (trust_threshold_autopilot, trust_threshold_alert), or None when the
        # tenant takes the configured defaults.
        self.onboarding = onboarding
        # What the trust gate would find: the newest fact_signal_health_daily
        # snapshot and its channel rows. Empty by default, which is the real
        # state of a tenant the rollup has never been able to substantiate.
        self.health_rows = list(health_rows)
        self.health_date = health_date
        self.added: list[object] = []
        self.deleted_statements: list[str] = []
        self.committed = False

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _params(statement) -> dict:
        """Read back the bind parameters the statement actually carries."""
        return dict(statement.compile().params)

    def _matching_delivery_rows(self, params: dict) -> list[_DeliveryRow]:
        """Apply only the filters present in the statement's parameters."""
        rows = list(self.delivery_rows)
        tenant = params.get("tenant_id_1")
        if tenant is not None:
            rows = [row for row in rows if row.tenant_id == tenant]
        platforms = params.get("platform_1")
        if platforms is not None:
            wanted = platforms if isinstance(platforms, (list, tuple)) else [platforms]
            rows = [row for row in rows if row.platform in wanted]
        start = params.get("delivery_time_1")
        if start is not None:
            rows = [row for row in rows if row.delivery_time >= start]
        end = params.get("delivery_time_2")
        if end is not None:
            rows = [row for row in rows if row.delivery_time < end]
        return rows

    # -- AsyncSession surface --------------------------------------------

    async def execute(self, statement):
        """Answer one of the queries the signal health service issues."""
        sql = str(statement)
        params = self._params(statement)

        if "DELETE FROM fact_signal_health_daily" in sql:
            self.deleted_statements.append(sql)
            return SimpleNamespace(rowcount=1)

        if "max(fact_signal_health_daily.date)" in sql:
            return _Result(scalar=self.health_date)

        if "fact_signal_health_daily" in sql:
            return _Result(rows=self.health_rows)

        if "tenant_enforcement_settings" in sql:
            return _Result(scalar=None)

        if "capi_delivery_logs" in sql:
            rows = self._matching_delivery_rows(params)
            return _Result(
                row=SimpleNamespace(
                    total=len(rows),
                    successful=sum(1 for row in rows if row.status == "success"),
                    identified=sum(1 for row in rows if row.user_data_hash is not None),
                )
            )

        if "max(campaigns.last_synced_at)" in sql:
            newest = self.last_synced_at.get(params.get("tenant_id_1"))
            # Honour the window bound the query carries: a sync recorded after
            # the window end is not evidence about that window.
            bound = params.get("last_synced_at_1")
            if newest is not None and bound is not None and newest > bound:
                newest = None
            return _Result(scalar=newest)

        if "tenant_platform_connection" in sql:
            connection = self.connections.get(params.get("tenant_id_1"))
            return _Result(rows=[connection] if connection else [])

        if "tenant_onboarding" in sql:
            return _Result(
                rows=[self.onboarding] if self.onboarding else [],
                scalar=self.onboarding,
            )

        raise AssertionError(f"unexpected query: {sql}")

    def add(self, record) -> None:
        """Record an added ORM object."""
        self.added.append(record)

    async def commit(self) -> None:
        """Mark the session committed."""
        self.committed = True

    async def rollback(self) -> None:
        """No-op rollback."""

    async def __aenter__(self):
        """Support ``async with`` so the rollup can use this as its session."""
        return self

    async def __aexit__(self, *_exc) -> bool:
        """Support ``async with``."""
        return False


def connection(status: str = "connected", error_count: int = 0, last_error=None):
    """Build a stand-in TenantPlatformConnection row."""
    return SimpleNamespace(
        status=status, error_count=error_count, last_error=last_error
    )


def delivery_rows(
    tenant_id: int,
    count: int,
    *,
    platform: str = "meta",
    failures: int = 0,
    with_identifiers: bool = True,
    when: datetime | None = None,
):
    """Build ``count`` persisted delivery rows for one tenant."""
    when = when or NOW - timedelta(hours=1)
    return [
        _DeliveryRow(
            tenant_id=tenant_id,
            platform=platform,
            delivery_time=when,
            status="failed" if index < failures else "success",
            user_data_hash="abc" if with_identifiers else None,
        )
        for index in range(count)
    ]


def window():
    """A window that contains the delivery rows the helpers build."""
    from app.services.signal_health import SignalHealthWindow

    return SignalHealthWindow(start=NOW - timedelta(hours=24), end=NOW)


def recent_rows(tenant_id: int, count: int, **kwargs):
    """
    Delivery rows inside the service's *default* window.

    The dashboard does not pass a window, so these have to sit near the real
    current time rather than the fixed NOW the windowed tests use.
    """
    return delivery_rows(
        tenant_id, count, when=datetime.now(UTC) - timedelta(minutes=30), **kwargs
    )


def _code_only(relative_path: str) -> str:
    """
    Return a module's source with comments and string literals stripped.

    The source guards below assert that specific fabricated expressions are
    gone from the *code*. The docstrings deliberately quote those expressions
    to explain what was removed, so a plain substring search over the raw file
    would match the explanation and never fail. Tokens are joined without
    separators so ``overall_score=85`` matches however it was spaced.
    """
    import tokenize

    path = BACKEND_ROOT / relative_path
    with path.open("rb") as handle:
        return "".join(
            token.string
            for token in tokenize.tokenize(handle.readline)
            if token.type not in (tokenize.COMMENT, tokenize.STRING)
        )


# =============================================================================
# 1. A tenant with genuine delivery logs is scored from them
# =============================================================================


@pytest.mark.asyncio
async def test_tenant_with_delivery_logs_scores_from_them():
    """A tenant that really delivered events is scored on what it delivered."""
    db = FakeSession(
        delivery_rows=delivery_rows(TENANT, 40, failures=4),
        last_synced_at={TENANT: NOW - timedelta(minutes=10)},
        connections={TENANT: connection()},
    )

    result = await compute_signal_health(db, TENANT, "facebook", window())

    assert not result.insufficient_data
    assert result.delivery.total_events == 40
    assert result.delivery.successful_events == 36
    # 90% success, 100% identifier coverage -> 0.7*90 + 0.3*100
    assert result.component_scores()[COMPONENT_EMQ] == pytest.approx(93.0)
    # Event loss is the measured failure rate, not a constant
    assert result.delivery.failure_rate_pct == pytest.approx(10.0)
    assert result.component_scores()[COMPONENT_DELIVERY] == pytest.approx(90.0)
    # The window the score was computed over travels with it
    assert result.window.start == NOW - timedelta(hours=24)
    assert result.window.end == NOW


@pytest.mark.asyncio
async def test_score_moves_with_the_measured_failure_rate():
    """A worse delivery record produces a worse score - the number is derived."""
    good = await compute_signal_health(
        FakeSession(
            delivery_rows=delivery_rows(TENANT, 40, failures=0),
            last_synced_at={TENANT: NOW - timedelta(minutes=5)},
            connections={TENANT: connection()},
        ),
        TENANT,
        "facebook",
        window(),
    )
    bad = await compute_signal_health(
        FakeSession(
            delivery_rows=delivery_rows(TENANT, 40, failures=30),
            last_synced_at={TENANT: NOW - timedelta(minutes=5)},
            connections={TENANT: connection()},
        ),
        TENANT,
        "facebook",
        window(),
    )

    assert good.score > bad.score
    assert good.score == pytest.approx(100.0)


# =============================================================================
# 2. A tenant with no delivery data is insufficient, not defaulted
# =============================================================================


@pytest.mark.asyncio
async def test_tenant_without_delivery_logs_is_insufficient_data():
    """No CAPI traffic means an explicit gap, never a plausible score."""
    db = FakeSession(
        delivery_rows=[],
        last_synced_at={TENANT: NOW - timedelta(minutes=10)},
        connections={TENANT: connection()},
    )

    result = await compute_signal_health(db, TENANT, "facebook", window())

    assert result.insufficient_data
    assert result.score is None
    assert result.status == STATUS_INSUFFICIENT_DATA
    # The gap names the inputs that were missing, per component
    missing = {item.name for item in result.missing}
    assert missing == {COMPONENT_EMQ, COMPONENT_DELIVERY}
    assert any(
        "No CAPI events were delivered" in reason for reason in result.missing_inputs
    )


@pytest.mark.asyncio
async def test_too_few_delivery_events_is_insufficient_not_scored():
    """A handful of events is noise; the sample floor comes from config."""
    below = settings.signal_health_min_delivery_events - 1
    db = FakeSession(
        delivery_rows=delivery_rows(TENANT, below),
        last_synced_at={TENANT: NOW - timedelta(minutes=10)},
        connections={TENANT: connection()},
    )

    result = await compute_signal_health(db, TENANT, "facebook", window())

    assert result.insufficient_data
    assert any(str(below) in reason for reason in result.missing_inputs)


@pytest.mark.asyncio
async def test_brand_new_tenant_has_nothing_at_all():
    """No connection, no campaigns, no events: every component is missing."""
    db = FakeSession(delivery_rows=[], last_synced_at={}, connections={})

    result = await compute_signal_health(db, TENANT, "facebook", window())

    assert result.insufficient_data
    assert result.score is None
    assert len(result.missing) == len(component_weights())
    assert result.component_scores() == {}


# =============================================================================
# 3. The rollup writes no usable row, so the trust gate holds
# =============================================================================


@pytest.mark.asyncio
async def test_rollup_returns_no_metrics_for_a_tenant_without_evidence():
    """fetch_platform_metrics returns None rather than the old 85/3.5/0.5/30."""
    db = FakeSession(
        delivery_rows=[],
        last_synced_at={TENANT: NOW - timedelta(minutes=10)},
        connections={TENANT: connection()},
    )

    metrics = await rollup_module.fetch_platform_metrics(
        db, TENANT, "facebook", ROLLUP_DATE
    )

    assert metrics is None


@pytest.mark.asyncio
async def test_rollup_metrics_are_measured_not_constant():
    """With real evidence the columns carry measurements, not fixed values."""
    db = FakeSession(
        delivery_rows=delivery_rows(
            TENANT, 40, failures=4, when=datetime(2026, 3, 9, 6, 0, tzinfo=UTC)
        ),
        last_synced_at={TENANT: datetime(2026, 3, 9, 23, 30, tzinfo=UTC)},
        connections={TENANT: connection()},
    )

    metrics = await rollup_module.fetch_platform_metrics(
        db, TENANT, "facebook", ROLLUP_DATE
    )

    assert metrics["event_loss_pct"] == pytest.approx(10.0)
    assert metrics["emq_score"] == pytest.approx(93.0)
    # 30 minutes before the end of the rollup day, measured - not the old
    # hardcoded 30-minute default that happened to look the same for everyone.
    assert metrics["freshness_minutes"] == 30
    assert metrics["api_error_rate"] == pytest.approx(0.0)
    # The window is the rollup day, so a re-run reproduces the same numbers
    assert day_window(ROLLUP_DATE).end == datetime(2026, 3, 10, tzinfo=UTC)


def test_rollup_writes_no_row_and_withdraws_stale_ones(monkeypatch):
    """
    With insufficient data the rollup writes nothing and withdraws old rows.

    Leaving a previously fabricated row in place would keep the trust gate
    passing on it long after the fabrication was removed.
    """
    db = FakeSession(delivery_rows=[], last_synced_at={}, connections={})
    inner_execute = db.execute

    async def execute(statement):
        # The rollup also asks for the list of tenants; everything else is
        # answered by the shared fake store.
        if "FROM tenants" in str(statement):
            return _Result(rows=[(TENANT,)])
        return await inner_execute(statement)

    db.execute = execute
    monkeypatch.setattr(rollup_module, "async_session_factory", lambda: db)

    result = rollup_module.signal_health_rollup(
        rollup_module.signal_health_rollup, target_date=ROLLUP_DATE.isoformat()
    )

    assert result["records_processed"] == 0
    assert result["insufficient_data"] == len(rollup_module.PLATFORMS)
    assert result["rows_withdrawn"] == len(rollup_module.PLATFORMS)
    # Nothing was added to fact_signal_health_daily
    assert db.added == []


def test_trust_gate_holds_or_blocks_without_a_signal_health_row():
    """
    With no row for the tenant the gate never passes.

    A brand-new tenant has never had a row, so the gate BLOCKs. A tenant whose
    last row has aged out gets the configured stale decision, HOLD by default.
    Neither is a PASS, which is what the old fabricated 85 produced.
    """
    today = date(2026, 3, 10)

    fresh_none = evaluate_signal_health(
        records=[], health_date=None, today=today, enforcement_mode="advisory"
    )
    assert fresh_none.decision is GateDecision.BLOCK

    aged_out = evaluate_signal_health(
        records=[],
        health_date=today - timedelta(days=settings.trust_gate_max_health_age_days + 1),
        today=today,
        enforcement_mode="advisory",
    )
    assert aged_out.decision is not GateDecision.PASS
    assert aged_out.decision is (
        GateDecision.BLOCK
        if settings.trust_gate_stale_health_decision == "block"
        else GateDecision.HOLD
    )


def test_trust_gate_cannot_pass_on_a_row_with_only_one_trivial_column():
    """A row carrying only a perfect reliability column is unscorable, not 100."""
    row = FactSignalHealthDaily(
        tenant_id=TENANT,
        date=date(2026, 3, 9),
        platform="facebook",
        emq_score=None,
        event_loss_pct=None,
        freshness_minutes=None,
        api_error_rate=0.0,
        status=SignalHealthStatus.OK,
    )

    result = evaluate_signal_health(
        records=[row],
        health_date=date(2026, 3, 9),
        today=date(2026, 3, 10),
        enforcement_mode="advisory",
    )

    assert result.decision is GateDecision.BLOCK


# =============================================================================
# 4. Freshness comes from a real last_synced_at, and staleness degrades it
# =============================================================================


@pytest.mark.asyncio
async def test_freshness_is_read_from_a_real_last_synced_at():
    """Freshness is the age of a genuine sync, measured against the window end."""
    db = FakeSession(
        delivery_rows=delivery_rows(TENANT, 40),
        last_synced_at={TENANT: NOW - timedelta(minutes=17)},
        connections={TENANT: connection()},
    )

    result = await compute_signal_health(db, TENANT, "facebook", window())

    assert result.freshness.age_minutes == 17
    assert result.component_scores()[COMPONENT_FRESHNESS] == 100.0


@pytest.mark.asyncio
async def test_a_stale_last_synced_at_degrades_the_score():
    """A stale sync lowers the composite; a fully stale one zeroes the component."""
    fresh = await compute_signal_health(
        FakeSession(
            delivery_rows=delivery_rows(TENANT, 40),
            last_synced_at={TENANT: NOW - timedelta(minutes=5)},
            connections={TENANT: connection()},
        ),
        TENANT,
        "facebook",
        window(),
    )
    stale = await compute_signal_health(
        FakeSession(
            delivery_rows=delivery_rows(TENANT, 40),
            last_synced_at={
                TENANT: NOW
                - timedelta(minutes=settings.signal_health_stale_minutes + 1)
            },
            connections={TENANT: connection()},
        ),
        TENANT,
        "facebook",
        window(),
    )

    assert stale.score < fresh.score
    assert stale.component_scores()[COMPONENT_FRESHNESS] == 0.0
    assert fresh.component_scores()[COMPONENT_FRESHNESS] == 100.0


@pytest.mark.asyncio
async def test_a_tenant_that_never_synced_has_no_freshness_reading():
    """A sync that never succeeded is a missing input, not a fresh reading."""
    db = FakeSession(
        delivery_rows=delivery_rows(TENANT, 40),
        last_synced_at={},
        connections={TENANT: connection()},
    )

    result = await compute_signal_health(db, TENANT, "facebook", window())

    assert COMPONENT_FRESHNESS not in result.component_scores()
    assert any("insights sync" in reason for reason in result.missing_inputs)


@pytest.mark.asyncio
async def test_connection_errors_lower_the_reliability_component():
    """Connection health is derived from the connection's own error state."""
    clean = await compute_signal_health(
        FakeSession(
            delivery_rows=delivery_rows(TENANT, 40),
            last_synced_at={TENANT: NOW - timedelta(minutes=5)},
            connections={TENANT: connection()},
        ),
        TENANT,
        "facebook",
        window(),
    )
    failing = await compute_signal_health(
        FakeSession(
            delivery_rows=delivery_rows(TENANT, 40),
            last_synced_at={TENANT: NOW - timedelta(minutes=5)},
            connections={TENANT: connection(error_count=2, last_error="token expired")},
        ),
        TENANT,
        "facebook",
        window(),
    )

    penalty = settings.signal_health_connection_error_penalty
    assert clean.component_scores()[COMPONENT_RELIABILITY] == 100.0
    assert failing.component_scores()[COMPONENT_RELIABILITY] == pytest.approx(
        100.0 - 2 * penalty
    )


# =============================================================================
# 5. Tenant isolation - deliberately written as a leak test
# =============================================================================


@pytest.mark.asyncio
async def test_a_tenant_cannot_see_another_tenants_delivery_logs():
    """
    One tenant's CAPI traffic must never reach another tenant's score.

    The fake session applies only the predicates the query actually carries, so
    a service that dropped its ``tenant_id`` filter would aggregate the other
    tenant's 200 rows here and this test would fail rather than pass silently.
    """
    db = FakeSession(
        # Tenant 1 has nothing; tenant 2 has plenty.
        delivery_rows=delivery_rows(OTHER_TENANT, 200),
        last_synced_at={TENANT: NOW - timedelta(minutes=5)},
        connections={TENANT: connection()},
    )

    result = await compute_signal_health(db, TENANT, "facebook", window())

    assert result.delivery.total_events == 0
    assert result.insufficient_data
    assert result.score is None


@pytest.mark.asyncio
async def test_each_tenant_is_scored_only_on_its_own_traffic():
    """Two tenants in one store get two different, correctly-attributed scores."""
    rows = delivery_rows(TENANT, 40, failures=20) + delivery_rows(OTHER_TENANT, 40)
    store = {
        "delivery_rows": rows,
        "last_synced_at": {
            TENANT: NOW - timedelta(minutes=5),
            OTHER_TENANT: NOW - timedelta(minutes=5),
        },
        "connections": {TENANT: connection(), OTHER_TENANT: connection()},
    }

    mine = await compute_signal_health(
        FakeSession(**store), TENANT, "facebook", window()
    )
    theirs = await compute_signal_health(
        FakeSession(**store), OTHER_TENANT, "facebook", window()
    )

    assert mine.delivery.total_events == 40
    assert mine.delivery.successful_events == 20
    assert theirs.delivery.successful_events == 40
    assert mine.score < theirs.score


@pytest.mark.asyncio
async def test_whatsapp_does_not_borrow_the_meta_datasets_events():
    """Channels read only the delivery-log platform that carries their events."""
    db = FakeSession(
        delivery_rows=delivery_rows(TENANT, 40, platform="meta"),
        last_synced_at={TENANT: NOW - timedelta(minutes=5)},
        connections={TENANT: connection()},
    )

    whatsapp = await compute_signal_health(db, TENANT, "whatsapp", window())
    facebook = await compute_signal_health(db, TENANT, "facebook", window())

    assert whatsapp.delivery.total_events == 0
    assert whatsapp.insufficient_data
    assert facebook.delivery.total_events == 40


# =============================================================================
# 6. Thresholds and weights come from configuration, not literals
# =============================================================================


def test_component_weights_come_from_settings(monkeypatch):
    """Changing the configured weights changes the composite."""
    components = {
        COMPONENT_EMQ: 100.0,
        COMPONENT_FRESHNESS: 0.0,
        COMPONENT_DELIVERY: 100.0,
        COMPONENT_RELIABILITY: 100.0,
    }

    baseline, _, _ = weighted_score(components)

    monkeypatch.setattr(settings, "signal_health_freshness_weight", 0.75)
    monkeypatch.setattr(settings, "signal_health_emq_weight", 0.10)
    monkeypatch.setattr(settings, "signal_health_variance_weight", 0.10)
    monkeypatch.setattr(settings, "signal_health_anomaly_weight", 0.05)

    reweighted, _, _ = weighted_score(components)

    assert baseline != reweighted
    assert reweighted == pytest.approx(25.0)


def test_minimum_evidence_floor_comes_from_settings(monkeypatch):
    """The refuse-to-score floor is configuration, not a literal."""
    # Freshness + reliability alone is 40% of the weight: under the default
    # 0.5 floor that is unscorable.
    partial = {
        COMPONENT_EMQ: None,
        COMPONENT_FRESHNESS: 100.0,
        COMPONENT_DELIVERY: None,
        COMPONENT_RELIABILITY: 100.0,
    }

    assert weighted_score(partial)[0] is None

    monkeypatch.setattr(settings, "signal_health_min_component_weight", 0.3)
    assert weighted_score(partial)[0] == pytest.approx(100.0)


def test_status_bands_come_from_settings(monkeypatch):
    """The healthy/degraded bands are read from configuration at every call."""
    from app.services.signal_health import status_for_score

    assert status_for_score(72.0) == "healthy"

    monkeypatch.setattr(settings, "signal_health_healthy_threshold", 90.0)
    assert status_for_score(72.0) == "degraded"

    monkeypatch.setattr(settings, "signal_health_degraded_threshold", 80.0)
    assert status_for_score(72.0) == "critical"


@pytest.mark.asyncio
async def test_delivery_sample_floor_comes_from_settings(monkeypatch):
    """The minimum delivery sample is configuration, not a literal."""
    monkeypatch.setattr(settings, "signal_health_min_delivery_events", 100)

    result = await compute_signal_health(
        FakeSession(
            delivery_rows=delivery_rows(TENANT, 50),
            last_synced_at={TENANT: NOW - timedelta(minutes=5)},
            connections={TENANT: connection()},
        ),
        TENANT,
        "facebook",
        window(),
    )

    assert result.insufficient_data


# =============================================================================
# 7. The dashboard reports the gap instead of a number
# =============================================================================


@pytest.mark.asyncio
async def test_dashboard_summary_reports_insufficient_data():
    """A tenant with no signal data gets an explicit gap, not a score."""
    db = FakeSession(delivery_rows=[], last_synced_at={}, connections={})

    summary = await dashboard_module.build_signal_health_summary(db, TENANT, None)

    assert summary.status == STATUS_INSUFFICIENT_DATA
    # Not 85, and not 0 either - 0 reads as "terrible", the truth is "unknown"
    assert summary.overall_score is None
    assert summary.emq_score is None
    assert summary.data_freshness_minutes is None
    assert summary.api_health is None
    assert summary.missing_inputs
    assert summary.autopilot_enabled is False


@pytest.mark.asyncio
async def test_dashboard_summary_reports_measured_values():
    """With real evidence the dashboard publishes the measured numbers."""
    now = datetime.now(UTC)
    db = FakeSession(
        delivery_rows=recent_rows(TENANT, 40, failures=4),
        last_synced_at={TENANT: now - timedelta(minutes=10)},
        connections={TENANT: connection()},
    )

    summary = await dashboard_module.build_signal_health_summary(db, TENANT, None)

    assert summary.status != STATUS_INSUFFICIENT_DATA
    assert summary.overall_score is not None
    # The old mock published emq_score=0.92 on a 0-100 scale; this is measured
    assert summary.emq_score == pytest.approx(93.0)
    assert summary.data_freshness_minutes == 10
    assert summary.api_health is True
    assert summary.window_start is not None and summary.window_end is not None


@pytest.mark.asyncio
async def test_signal_health_endpoint_returns_the_insufficient_state():
    """The /dashboard/signal-health endpoint no longer simulates health."""
    db = FakeSession(delivery_rows=[], last_synced_at={}, connections={})
    user = SimpleNamespace(tenant_id=TENANT)

    response = await dashboard_module.get_signal_health(current_user=user, db=db)

    assert response.success is True
    assert response.data.status == STATUS_INSUFFICIENT_DATA
    assert response.data.overall_score is None


@pytest.mark.asyncio
async def test_having_campaigns_is_not_evidence_of_signal_health():
    """
    The overview card used to publish 85 for any tenant that had campaigns.

    Campaigns say nothing about whether the signals behind them arrive, so the
    same tenant with campaigns but no delivered events must read as unknown.
    """
    db = FakeSession(
        delivery_rows=[],
        last_synced_at={TENANT: datetime.now(UTC) - timedelta(minutes=5)},
        connections={TENANT: connection()},
    )

    summary = await dashboard_module.build_signal_health_summary(
        db, TENANT, SimpleNamespace(automation_mode="autopilot")
    )

    assert summary.status == STATUS_INSUFFICIENT_DATA
    assert summary.overall_score is None
    # Autopilot is not "enabled" on the strength of a mode setting alone
    assert summary.autopilot_enabled is False


# =============================================================================
# 8. Source guards - the fabricated literals must not come back
# =============================================================================


def test_dashboard_module_has_no_hardcoded_signal_health():
    """The dashboard no longer contains the simulated trust gate values."""
    code = _code_only("app/api/v1/endpoints/dashboard.py")

    assert "overall_score=85" not in code
    assert "emq_score=0.92" not in code
    assert "data_freshness=5" not in code
    assert "overall_score=0" not in code


def test_rollup_has_no_hardcoded_metrics():
    """The rollup no longer returns connection-state-derived constants."""
    code = _code_only("app/tasks/signal_health_rollup.py")

    # Every fabricated value hung off this one flag.
    assert "is_healthy" not in code
    assert "85.0" not in code
    assert "65.0" not in code


def test_no_process_local_event_delivery_log_remains():
    """The untenanted in-memory delivery list and its readers are gone."""
    connectors = _code_only("app/services/capi/platform_connectors.py")
    emq = _code_only("app/services/emq_measurement_service.py")

    assert "_event_delivery_logs" not in connectors
    assert "defget_event_delivery_logs" not in connectors
    assert "calculate_real_emq_metrics" not in emq
    # The hash()-derived "measurement history" must not come back either.
    assert "hash(" not in emq


# =============================================================================
# 9. Review fixes - the second sweep
# =============================================================================
#
# Everything below pins a defect found *after* the first pass, when signal
# health had already stopped being fabricated at its source. They share one
# shape: a number that nobody measured still reached the tenant, or a measured
# number was graded against a scale it was never on.


def _tsx_code_only(relative_path: str) -> str:
    """
    Return a TypeScript/TSX file's source with its comments stripped.

    Same reason as :func:`_code_only`: the guards below assert that specific
    fabricated expressions are gone from the *code*, and the replacement
    comments quote those expressions to explain what was removed. A plain
    substring search over the raw file would match the explanation and could
    never fail.

    Args:
        relative_path: Path relative to the repository root.

    Returns:
        The source with block and line comments removed.
    """
    import re

    source = (BACKEND_ROOT.parent / relative_path).read_text()
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", source, flags=re.MULTILINE)


def _thresholds(healthy: float = 70.0, degraded: float = 40.0):
    """Build an explicit threshold pair."""
    from app.services.signal_health import SignalHealthThresholds

    return SignalHealthThresholds(healthy=healthy, degraded=degraded)


@pytest.mark.asyncio
async def test_freshness_ignores_a_sync_recorded_after_the_window():
    """
    A sync performed after the window is not evidence about that window.

    The query carried no upper bound and the age was clamped at zero, so a
    rollup for a historical date - or the normal 02:00 UTC run, whose window
    ended at midnight - reported ``freshness_minutes: 0``, a claim of perfect
    freshness resting on data that did not exist during the day being scored.
    """
    from app.services.signal_health import measure_freshness

    db = FakeSession(
        # The only sync happened 30 minutes *after* the rollup day ended.
        last_synced_at={TENANT: datetime(2026, 3, 10, 0, 30, tzinfo=UTC)},
    )

    measurement = await measure_freshness(db, TENANT, day_window(ROLLUP_DATE).end)

    assert measurement is None


@pytest.mark.asyncio
async def test_freshness_uses_the_newest_sync_inside_the_window():
    """A sync inside the window is still measured, and its real age is used."""
    from app.services.signal_health import measure_freshness

    db = FakeSession(last_synced_at={TENANT: datetime(2026, 3, 9, 23, 30, tzinfo=UTC)})

    measurement = await measure_freshness(db, TENANT, day_window(ROLLUP_DATE).end)

    assert measurement is not None
    assert measurement.age_minutes == 30


@pytest.mark.asyncio
async def test_a_single_connection_error_does_not_block_a_healthy_tenant():
    """
    One recorded connection error must not turn a 97 into a BLOCK.

    ``api_error_rate`` stores the connection-health deficit - 20 points per
    recorded error - but ``determine_status`` banded it as a *percentage* of
    failed API calls against a literal 10% "critical" threshold. A tenant with
    clean delivery, a fresh sync and one recorded error therefore scored 97 and
    was written CRITICAL, which floored the gate to BLOCK.
    """
    db = FakeSession(
        delivery_rows=delivery_rows(
            TENANT, 40, when=datetime(2026, 3, 9, 6, 0, tzinfo=UTC)
        ),
        last_synced_at={TENANT: datetime(2026, 3, 9, 23, 30, tzinfo=UTC)},
        connections={TENANT: connection(error_count=1, last_error="rate limited")},
    )

    metrics = await rollup_module.fetch_platform_metrics(
        db, TENANT, "facebook", ROLLUP_DATE
    )
    assert metrics is not None
    # The column still carries 20 - one error at the configured penalty.
    assert metrics["api_error_rate"] == pytest.approx(
        settings.signal_health_connection_error_penalty
    )
    assert metrics["composite_score"] >= settings.signal_health_healthy_threshold

    status = rollup_module.determine_status(
        metrics["composite_score"], metrics["partial_evidence"], _thresholds()
    )
    assert status is not SignalHealthStatus.CRITICAL

    record = FactSignalHealthDaily(
        tenant_id=TENANT,
        date=ROLLUP_DATE,
        platform="facebook",
        emq_score=metrics["emq_score"],
        event_loss_pct=metrics["event_loss_pct"],
        freshness_minutes=metrics["freshness_minutes"],
        api_error_rate=metrics["api_error_rate"],
        status=status,
    )
    result = evaluate_signal_health(
        records=[record],
        health_date=ROLLUP_DATE,
        today=ROLLUP_DATE,
        enforcement_mode="advisory",
    )
    assert result.decision is GateDecision.PASS


def test_row_status_comes_from_the_configured_bands_not_per_metric_literals():
    """
    ``determine_status`` bands the composite, with no literals of its own.

    It used to carry four unconfigured threshold tables. Freshness over 360
    minutes alone forced CRITICAL, so a tenant whose newest sync was seven
    hours old - which the configured ``signal_health_stale_minutes`` scores at
    roughly 71% fresh, for a composite in the nineties - was written CRITICAL
    and blocked.
    """
    source = _code_only("app/tasks/signal_health_rollup.py")
    for literal in ("emq_score_below", "event_loss_above", "freshness_above"):
        assert literal not in source

    high = settings.signal_health_healthy_threshold + 1
    assert (
        rollup_module.determine_status(93.5, False, _thresholds())
        is SignalHealthStatus.OK
    )
    assert rollup_module.determine_status(high, False, _thresholds(healthy=high + 5)) is (
        SignalHealthStatus.DEGRADED
    )
    # A healthy composite measured over an incomplete set of components is
    # reported as RISK - which still PASSes the gate - rather than as OK.
    assert (
        rollup_module.determine_status(95.0, True, _thresholds())
        is SignalHealthStatus.RISK
    )


def test_the_gate_scores_with_current_settings_not_import_time_weights(monkeypatch):
    """
    Changing a weight moves the gate's score, not just the service's.

    ``SignalHealthConfig``'s weight fields were plain dataclass defaults, so
    ``float(getattr(settings, ...))`` was evaluated once when the class was
    defined. ``evaluate_signal_health`` constructs one of those per call and
    hands it to the shared ``weighted_score``, so the gate kept scoring with
    import-time weights while the service scored with the current ones - the
    function was shared, the weights were not.
    """
    record = FactSignalHealthDaily(
        tenant_id=TENANT,
        date=ROLLUP_DATE,
        platform="facebook",
        emq_score=100.0,
        event_loss_pct=0.0,
        freshness_minutes=24 * 60,  # fully stale -> freshness component 0
        api_error_rate=0.0,
        status=SignalHealthStatus.OK,
    )

    def score_now() -> float:
        return evaluate_signal_health(
            records=[record],
            health_date=ROLLUP_DATE,
            today=ROLLUP_DATE,
            enforcement_mode="advisory",
        ).score

    baseline = score_now()

    # Push almost all the weight onto the one component that scores zero.
    monkeypatch.setattr(settings, "signal_health_freshness_weight", 0.75)
    monkeypatch.setattr(settings, "signal_health_emq_weight", 0.10)
    monkeypatch.setattr(settings, "signal_health_variance_weight", 0.10)
    monkeypatch.setattr(settings, "signal_health_anomaly_weight", 0.05)

    assert score_now() < baseline


@pytest.mark.asyncio
async def test_a_tenants_own_trust_thresholds_are_applied_by_service_and_gate():
    """
    ``trust_threshold_autopilot`` stops being dead configuration.

    Onboarding asks the tenant for its autopilot and alert thresholds and
    stores them, but nothing read them back: a tenant that asked for 90 was
    graded at the deployment default of 70 in the UI and in the gate. Both ends
    now resolve the same pair, so the band shown is the band enforced.
    """
    from app.services.signal_health import compute_signal_health, thresholds_for_tenant

    db = FakeSession(
        delivery_rows=delivery_rows(TENANT, 40, failures=12),
        last_synced_at={TENANT: NOW - timedelta(minutes=10)},
        connections={TENANT: connection()},
        onboarding=(90, 50),
    )

    thresholds = await thresholds_for_tenant(db, TENANT)
    assert thresholds.healthy == 90.0
    assert thresholds.degraded == 50.0

    computation = await compute_signal_health(db, TENANT, "facebook", window())
    assert computation.score is not None
    # Comfortably healthy at the configured 70, but not at the tenant's 90.
    assert settings.signal_health_healthy_threshold <= computation.score < 90.0
    assert computation.status == "degraded"

    record = FactSignalHealthDaily(
        tenant_id=TENANT,
        date=ROLLUP_DATE,
        platform="facebook",
        emq_score=computation.component_scores().get(COMPONENT_EMQ),
        event_loss_pct=computation.delivery.failure_rate_pct,
        freshness_minutes=computation.freshness.age_minutes,
        api_error_rate=0.0,
        status=SignalHealthStatus.OK,
    )
    result = evaluate_signal_health(
        records=[record],
        health_date=ROLLUP_DATE,
        today=ROLLUP_DATE,
        enforcement_mode="advisory",
        thresholds=thresholds,
    )
    assert result.decision is GateDecision.HOLD
    assert result.to_audit_dict()["healthy_threshold"] == 90.0


@pytest.mark.asyncio
async def test_dashboard_publishes_the_gates_decision_not_the_live_band():
    """
    A live-healthy tenant with no daily snapshot must not be shown PASS.

    ``build_signal_health_summary`` measures a live trailing window; the trust
    gate grades the newest ``fact_signal_health_daily`` row. A tenant that
    started delivering events this morning measures healthy while the gate
    still BLOCKs for want of a snapshot, and the UI - which renders gate verbs
    like "autopilot executes" - was reading the live band.
    """
    db = FakeSession(
        delivery_rows=recent_rows(TENANT, 40),
        last_synced_at={TENANT: datetime.now(UTC) - timedelta(minutes=5)},
        connections={TENANT: connection()},
        health_rows=[],
        health_date=None,
    )

    summary = await dashboard_module.build_signal_health_summary(db, TENANT, None)

    assert summary.status == "healthy"
    assert summary.overall_score is not None
    # ... and yet nothing will run.
    assert summary.gate_decision == "block"
    assert summary.gate_health_date is None
    assert summary.autopilot_enabled is False
    assert any("fails closed" in issue for issue in summary.issues)


def test_the_global_trust_gate_badge_reads_the_api_not_a_simulation():
    """
    The corner badge shown on every dashboard route is no longer simulated.

    It seeded ``useState(85)``, random-walked it every five seconds and mapped
    the result to PASS through its own copies of 70/40 - so a brand-new tenant
    saw a green "85 PASS" badge beside the card that correctly said there was
    not enough data to score.
    """
    source = _tsx_code_only("frontend/src/components/ui/TrustGateIndicator.tsx")

    assert "useState(85)" not in source
    assert "Math.random()" not in source
    assert "setInterval" not in source
    assert "HEALTHY_THRESHOLD" not in source
    # It takes the gate's decision rather than re-deriving one.
    assert "gate_decision" in source
    assert "useDashboardSignalHealth" in source


def test_no_frontend_view_invents_an_emq_score():
    """
    The remaining fabricated EMQ renders are gone.

    The account-manager portfolio attached ``Math.random()`` EMQ scores and
    statuses to the *real* tenant list, and the data quality dashboard seeded
    its state from mock per-platform and per-event EMQ tables, so a tenant with
    no CAPI traffic saw facebook 82 / instagram 74 / whatsapp 71 as their own.
    """
    portfolio = _tsx_code_only("frontend/src/views/am/Portfolio.tsx")
    assert "Math.random()" not in portfolio

    quality = _tsx_code_only("frontend/src/views/DataQualityDashboard.tsx")
    assert "mockPlatformEMQ" not in quality
    assert "mockEventEMQ" not in quality
    assert "mockKPIs" not in quality

    connect = _tsx_code_only("frontend/src/views/tenant/ConnectPlatforms.tsx")
    assert "platformHealthMock" not in connect


def test_no_endpoint_still_hardcodes_a_healthy_signal():
    """
    The three remaining hardcoded signal health sites are gone.

    The embeddable widgets published ``overall_score: 87`` with a
    ``last_updated`` stamped now - on the tenant's own public website - and the
    tenant dashboard overview returned ``signal_health_status="healthy"`` for
    every tenant, right next to an honest ``avg_emq_score=None``.
    """
    widgets = _code_only("app/api/v1/endpoints/embed_widgets.py")
    assert "overall_score:87" not in widgets
    assert "signal_health:87" not in widgets

    tenant_dashboard = _code_only("app/api/v1/endpoints/tenant_dashboard.py")
    assert "signal_health_status=healthy" not in tenant_dashboard
    assert 'signal_health_status:str="healthy"' not in tenant_dashboard

    # The autopilot dry-run preview no longer keeps its own thresholds, and no
    # longer starts from "the gate passed" and waits to be contradicted.
    autopilot = _code_only("app/api/v1/endpoints/autopilot.py")
    assert "healthy_threshold=70.0" not in autopilot
    assert "gate_passed=True" not in autopilot


def test_absent_signal_health_reports_automation_as_blocked():
    """
    "No data" must not be reported to the UI as "automation is not blocked".

    ``_empty_response`` returned ``automation_blocked: False`` on the exact
    path the trust gate treats as fail-closed, and the rollup now deliberately
    writes no row for an unsubstantiated tenant, so that path is the common one.
    """
    from app.quality.trust_layer_service import SignalHealthService

    empty = SignalHealthService.__dict__["_empty_response"](
        SignalHealthService(db=None), ROLLUP_DATE
    )

    assert empty["status"] == "no_data"
    assert empty["automation_blocked"] is True
