# =============================================================================
# Stratum AI - Scheduled Dispatch Backlog Guard
# =============================================================================
"""
Regression tests for the first-worker-start backlog burst.

``process-scheduled-whatsapp`` and ``publish-scheduled-cms-posts`` both run
every minute and select everything whose ``scheduled_at`` has passed, with no
lower bound. The worker consumed no queues until the queue-list fix, so a real
tenant's pending backlog stretches back to deployment: the first worker start
would have sent every overdue WhatsApp message and published every overdue post
in one burst.

The WhatsApp task was worse than a one-off burst. It dispatches by
tenant/template/number rather than by message id and never updated the row it
read, so the same message was re-queued on *every* beat tick - an unbounded
duplicate-send loop for as long as the worker ran.
"""

import re
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.models import WhatsAppMessageStatus
from app.workers.tasks import cms as cms_module
from app.workers.tasks import whatsapp as whatsapp_module

NOW = datetime.now(UTC)


class FakeMessage:
    """Minimal scheduled WhatsApp message."""

    def __init__(self, scheduled_at):
        self.tenant_id = 1
        self.scheduled_at = scheduled_at
        self.status = WhatsAppMessageStatus.PENDING
        self.sent_at = None
        self.template = None
        self.contact = None
        self.variables = {}
        self.media_url = None


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        """Return every row."""
        return list(self._rows)


class _FakeResult:
    def __init__(self, *, rows=(), scalar=None):
        self._rows = rows
        self._scalar = scalar

    def scalars(self):
        """Return the row collection."""
        return _FakeScalars(self._rows)

    def scalar(self):
        """Return the aggregate value."""
        return self._scalar


class FakeSession:
    """Applies the query's own date filters to an in-memory row set."""

    def __init__(self, rows):
        self.rows = rows
        self.commits = 0
        self.count_queries = 0
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, statement):
        """Return either the filtered rows or the stale-row count."""
        sql = re.sub(r"\s+", " ", str(statement)).lower()
        self.queries.append(sql)
        if "count(" in sql:
            self.count_queries += 1
            cutoff = NOW - timedelta(hours=settings.scheduled_dispatch_max_age_hours)
            return _FakeResult(
                scalar=len([r for r in self.rows if r.scheduled_at < cutoff])
            )
        # The task's own cut-off is what we are testing, so mirror it here.
        cutoff = NOW - timedelta(hours=settings.scheduled_dispatch_max_age_hours)
        return _FakeResult(
            rows=[r for r in self.rows if cutoff <= r.scheduled_at <= NOW]
        )

    def commit(self):
        """Record a commit."""
        self.commits += 1


@pytest.fixture
def dispatched(monkeypatch):
    """Capture what the WhatsApp task hands to Celery."""
    sent: list[dict] = []

    class _Delay:
        @staticmethod
        def delay(**kwargs):
            sent.append(kwargs)

    monkeypatch.setattr(whatsapp_module, "send_whatsapp_message", _Delay)
    return sent


class TestWhatsAppBacklog:
    """A stale backlog must not be flushed on the first worker start."""

    def test_overdue_messages_are_not_sent(self, monkeypatch, dispatched):
        """Anything older than the cut-off is left for manual review."""
        ancient = FakeMessage(NOW - timedelta(days=120))
        session = FakeSession([ancient])
        monkeypatch.setattr(whatsapp_module, "SyncSessionLocal", lambda: session)

        result = whatsapp_module.process_scheduled_whatsapp_messages()

        select_sql = next(q for q in session.queries if "count(" not in q)
        assert "whatsapp_messages.scheduled_at >=" in select_sql, (
            "the scheduled-message query must carry a lower bound, or the whole "
            f"backlog is dispatched on the first worker start; got: {select_sql}"
        )
        assert dispatched == []
        assert result["queued"] == 0
        assert result["skipped_stale"] == 1
        assert ancient.status is WhatsAppMessageStatus.PENDING

    def test_recent_messages_are_still_sent(self, monkeypatch, dispatched):
        """The cut-off must not break normal scheduling."""
        due = FakeMessage(NOW - timedelta(minutes=5))
        session = FakeSession([due])
        monkeypatch.setattr(whatsapp_module, "SyncSessionLocal", lambda: session)

        result = whatsapp_module.process_scheduled_whatsapp_messages()

        assert len(dispatched) == 1
        assert result["queued"] == 1

    def test_dispatched_message_leaves_the_pending_set(self, monkeypatch, dispatched):
        """Otherwise the same message is re-queued on every beat tick."""
        due = FakeMessage(NOW - timedelta(minutes=5))
        session = FakeSession([due])
        monkeypatch.setattr(whatsapp_module, "SyncSessionLocal", lambda: session)

        whatsapp_module.process_scheduled_whatsapp_messages()

        assert due.status is not WhatsAppMessageStatus.PENDING
        assert due.sent_at is not None
        assert session.commits == 1

    def test_cut_off_is_configurable(self, monkeypatch, dispatched):
        """The window comes from config, not a literal in the task."""
        older = FakeMessage(NOW - timedelta(hours=48))
        session = FakeSession([older])
        monkeypatch.setattr(whatsapp_module, "SyncSessionLocal", lambda: session)
        monkeypatch.setattr(settings, "scheduled_dispatch_max_age_hours", 72)

        result = whatsapp_module.process_scheduled_whatsapp_messages()

        assert result["queued"] == 1


class TestCMSBacklog:
    """Publishing public content in a burst is equally unwanted."""

    def test_overdue_posts_are_not_published(self, monkeypatch):
        """Posts past the cut-off wait for a human."""
        published: list[str] = []

        class _FakePost:
            def __init__(self, scheduled_at):
                self.id = "post-1"
                self.scheduled_at = scheduled_at

        class _Delay:
            @staticmethod
            def delay(post_id):
                published.append(post_id)

        session = FakeSession([_FakePost(NOW - timedelta(days=90))])
        monkeypatch.setattr(cms_module, "SyncSessionLocal", lambda: session)
        monkeypatch.setattr(cms_module, "publish_cms_post", _Delay)

        result = cms_module.publish_scheduled_cms_posts()

        select_sql = next(q for q in session.queries if "count(" not in q)
        assert "cms_posts.scheduled_at >=" in select_sql, (
            "the scheduled-post query must carry a lower bound; "
            f"got: {select_sql}"
        )
        assert published == []
        assert result["queued"] == 0
        assert result["skipped_stale"] == 1
