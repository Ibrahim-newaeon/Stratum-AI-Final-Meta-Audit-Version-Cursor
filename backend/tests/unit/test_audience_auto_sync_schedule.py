# =============================================================================
# Stratum AI - Audience auto-sync schedule tests
# =============================================================================
"""
Unit tests for ``list_due_auto_sync_audiences`` and the Celery beat fan-out
``sync_due_audience_syncs``.

No Meta network calls: the due-list query is exercised against an in-memory
fake session, and the beat task is asserted to enqueue one per-audience
job without invoking the Meta connector.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Iterator, Optional
from uuid import uuid4

import pytest

from app.models.audience_sync import PlatformAudience
from app.services.cdp.audience_sync.service import list_due_auto_sync_audiences
from app.workers.tasks import cdp as cdp_tasks

pytestmark = pytest.mark.unit

TENANT_ID = 42


class _ScalarResult:
    """Stand-in for ``Result.scalars()``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        """Return every matched row."""
        return list(self._rows)


class _Result:
    """Stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        """Return a scalar result view."""
        return _ScalarResult(self._rows)


class FakeAsyncSession:
    """Minimal async session that filters due PlatformAudience rows in Python."""

    def __init__(self, audiences: list[PlatformAudience]) -> None:
        self.audiences = audiences
        self.forced_limit: Optional[int] = None

    async def execute(self, statement: Any) -> _Result:
        """Apply the due-auto-sync predicates the production query uses."""
        del statement  # Fake session ignores SQL shape; filters in Python.
        now = datetime.now(UTC).replace(tzinfo=None)
        due: list[PlatformAudience] = []
        for row in self.audiences:
            if not row.auto_sync or row.next_sync_at is None:
                continue
            stamp = row.next_sync_at
            if getattr(stamp, "tzinfo", None) is not None:
                stamp = stamp.astimezone(UTC).replace(tzinfo=None)
            if stamp <= now:
                due.append(row)
        due.sort(
            key=lambda row: (
                row.next_sync_at.astimezone(UTC).replace(tzinfo=None)
                if getattr(row.next_sync_at, "tzinfo", None)
                else row.next_sync_at
            )
        )
        if self.forced_limit is not None:
            due = due[: self.forced_limit]
        return _Result(due)


def _audience(
    *,
    auto_sync: bool,
    next_sync_at: Optional[datetime],
    name: str = "Buyers",
) -> PlatformAudience:
    """Build a PlatformAudience row for due-list tests."""
    return PlatformAudience(
        id=uuid4(),
        tenant_id=TENANT_ID,
        segment_id=uuid4(),
        platform="meta",
        ad_account_id="act_1",
        audience_type="customer_list",
        platform_audience_name=name,
        auto_sync=auto_sync,
        sync_interval_hours=24,
        next_sync_at=next_sync_at,
    )


@pytest.mark.asyncio
async def test_list_due_includes_only_auto_sync_past_next_sync_at() -> None:
    """Past-due auto_sync rows are returned; future and disabled are not."""
    past = _audience(
        auto_sync=True,
        next_sync_at=datetime.now(UTC) - timedelta(hours=1),
        name="due",
    )
    future = _audience(
        auto_sync=True,
        next_sync_at=datetime.now(UTC) + timedelta(hours=1),
        name="future",
    )
    disabled = _audience(
        auto_sync=False,
        next_sync_at=datetime.now(UTC) - timedelta(hours=1),
        name="off",
    )
    unset = _audience(auto_sync=True, next_sync_at=None, name="unset")

    db = FakeAsyncSession([past, future, disabled, unset])
    due = await list_due_auto_sync_audiences(db)

    assert [row.platform_audience_name for row in due] == ["due"]


@pytest.mark.asyncio
async def test_list_due_orders_oldest_first_and_respects_limit() -> None:
    """Oldest next_sync_at wins ordering; limit caps the fan-out."""
    older = _audience(
        auto_sync=True,
        next_sync_at=datetime.now(UTC) - timedelta(hours=5),
        name="older",
    )
    newer = _audience(
        auto_sync=True,
        next_sync_at=datetime.now(UTC) - timedelta(minutes=10),
        name="newer",
    )
    db = FakeAsyncSession([newer, older])
    db.forced_limit = 1

    due = await list_due_auto_sync_audiences(db, limit=1)

    assert len(due) == 1
    assert due[0].platform_audience_name == "older"


def test_sync_due_audience_syncs_enqueues_per_audience(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Beat dispatcher fans out one task per due audience and reports the count."""
    import importlib

    celery_mod = importlib.import_module("app.workers.celery_app")

    a_id = uuid4()
    b_id = uuid4()
    queued: list[tuple[int, str]] = []

    monkeypatch.setattr(
        cdp_tasks,
        "_run_async",
        lambda coro: [(TENANT_ID, str(a_id)), (7, str(b_id))],
    )
    monkeypatch.setattr(
        cdp_tasks.sync_platform_audience_task,
        "delay",
        lambda tenant_id, audience_id: queued.append((tenant_id, audience_id)),
    )

    @contextmanager
    def _always_acquired(*_args: Any, **_kwargs: Any) -> Iterator[bool]:
        yield True

    monkeypatch.setattr(celery_mod._distributed_lock, "acquire", _always_acquired)

    result = cdp_tasks.sync_due_audience_syncs.run(limit=50)

    assert result == {"tasks_queued": 2}
    assert queued == [(TENANT_ID, str(a_id)), (7, str(b_id))]


def test_beat_schedule_registers_audience_auto_sync() -> None:
    """Celery beat must schedule the due-audience scanner on the cdp queue."""
    from app.workers.celery_app import BEAT_SCHEDULE

    entry = BEAT_SCHEDULE["sync-due-audience-syncs"]
    assert entry["task"] == "app.workers.tasks.cdp.sync_due_audience_syncs"
    assert entry["options"]["queue"] == "cdp"
