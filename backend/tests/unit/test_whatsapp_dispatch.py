# =============================================================================
# Stratum AI - WhatsApp Outbound Dispatch Guard
# =============================================================================
"""
Regression tests for the broken WhatsApp outbound path.

Every outbound send was dead on arrival:

* ``POST /whatsapp/messages/broadcast`` imported ``send_whatsapp_broadcast``,
  which existed nowhere in the codebase - an ImportError raised *after* the
  message rows were committed, so each attempt returned 500 and left one
  PENDING row per recipient that nothing would ever dispatch.
* ``POST /whatsapp/messages/send`` called ``send_whatsapp_message.delay()``
  with keyword arguments the task did not accept, which Celery rejects with a
  TypeError before the message reaches the broker.
* The task itself then built a *second* message row from fields the model does
  not have (``variables``), read a template attribute that does not exist
  (``body``), and never set the NOT NULL ``message_type``.

The path now starts from the row the API layer already committed and the task
is handed only its id. ``TestTasksAcceptTheirCallers`` is the guard that would
have caught the first two on its own: it binds each call site's real keyword
arguments against the real task signature.
"""

import ast
import inspect
import pathlib
import re

import pytest

from app.models import (
    WhatsAppMessageStatus,
    WhatsAppOptInStatus,
)
from app.workers import tasks as tasks_pkg
from app.workers.tasks import whatsapp as whatsapp_module

REPO = pathlib.Path(__file__).resolve().parents[2]


# =============================================================================
# Fakes
# =============================================================================
class FakeContact:
    """A WhatsApp contact, opted in unless told otherwise."""

    def __init__(self, opt_in_status=WhatsAppOptInStatus.OPTED_IN):
        self.id = 5
        self.phone_number = "+15551234567"
        self.opt_in_status = opt_in_status


class FakeTemplate:
    """An approved template."""

    def __init__(self, header_type=None):
        self.id = 3
        self.name = "order_update"
        self.language = "en"
        self.header_type = header_type


class FakeMessage:
    """A committed outbound message row."""

    def __init__(self, *, contact, template=None, message_type="template", wamid=None):
        self.id = 42
        self.tenant_id = 1
        self.contact = contact
        self.template = template
        self.template_name = "order_update"
        self.template_variables = {"1": "Ada"}
        self.content = None
        self.media_url = None
        self.message_type = message_type
        self.wamid = wamid
        self.status = WhatsAppMessageStatus.PENDING
        self.sent_at = None
        self.error_message = None
        self.retry_count = 0


class _FakeResult:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        """Return the single ORM object, or None."""
        return self._obj


class FakeSession:
    """Resolves selects by the entity they target and records every write."""

    def __init__(self, message=None, template=None):
        self.message = message
        self.template = template
        self.added: list[object] = []
        self.commits = 0
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    @staticmethod
    def _entity_of(statement):
        """Name the entity a select targets, so the fake can answer it."""
        for description in statement.column_descriptions:
            entity = description.get("entity")
            if entity is not None and description.get("expr") is entity:
                return getattr(entity, "__name__", "")
        return ""

    def execute(self, statement):
        """Return the message row, the template row, or nothing."""
        self.queries.append(re.sub(r"\s+", " ", str(statement)))
        entity = self._entity_of(statement)
        if entity == "WhatsAppMessage":
            return _FakeResult(self.message)
        if entity == "WhatsAppTemplate":
            return _FakeResult(self.template)
        return _FakeResult(None)

    def add(self, obj):
        """Record an attempted insert."""
        self.added.append(obj)

    def commit(self):
        """Record a commit."""
        self.commits += 1


class FakeClient:
    """Stand-in for the synchronous Graph API client."""

    def __init__(self, tenant_id=None, phone_number_id=None, access_token=None, **kwargs):
        self.tenant_id = tenant_id
        self.phone_number_id = phone_number_id
        self.access_token = access_token
        FakeClient.calls = getattr(FakeClient, "calls", [])

    def send_template_message(self, **kwargs):
        """Record a template send and hand back a Meta message id."""
        FakeClient.calls.append(("template", kwargs))
        return {"message_id": "wamid.TEST"}

    def send_text_message(self, **kwargs):
        """Record a text send and hand back a Meta message id."""
        FakeClient.calls.append(("text", kwargs))
        return {"message_id": "wamid.TEXT"}


@pytest.fixture
def client_calls(monkeypatch):
    """Capture what the task sends to Meta, sending nothing."""
    from app.services.whatsapp.credentials_store import ResolvedWhatsAppCredentials

    FakeClient.calls = []
    monkeypatch.setattr(
        "app.services.whatsapp.client.WhatsAppClient", FakeClient, raising=True
    )
    monkeypatch.setattr(
        "app.services.whatsapp.credentials_store.resolve_credentials_sync",
        lambda db, tenant_id: ResolvedWhatsAppCredentials(
            phone_number_id="pn-test",
            access_token="tok-test",
            business_account_id="ba-test",
            source="tenant",
        ),
        raising=True,
    )
    return FakeClient.calls


def _run(monkeypatch, session):
    """Run the send task against a recording session."""
    monkeypatch.setattr(whatsapp_module, "SyncSessionLocal", lambda: session)
    return whatsapp_module.send_whatsapp_message(message_id=42, tenant_id=1)


# =============================================================================
# The defect that shipped
# =============================================================================
SOURCES = [
    REPO / "app" / "api" / "v1" / "endpoints" / "whatsapp.py",
    REPO / "app" / "workers" / "tasks" / "whatsapp.py",
]


def _delay_call_sites():
    """
    Find every ``send_whatsapp_*.delay(...)`` in the source, as it is written.

    Reading the real call sites rather than restating them here is the whole
    point: a hand-copied argument list would have gone on passing while the
    endpoints were broken.

    Returns:
        ``(task_name, keyword names, "path:line")`` per call site.
    """
    sites = []
    for path in SOURCES:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            func = getattr(node, "func", None)
            if not isinstance(func, ast.Attribute) or func.attr != "delay":
                continue
            if not isinstance(func.value, ast.Name):
                continue
            name = func.value.id
            if not name.startswith("send_whatsapp"):
                continue
            keywords = sorted(kw.arg for kw in node.keywords if kw.arg)
            sites.append((name, keywords, f"{path.name}:{node.lineno}"))
    return sites


class TestTasksAcceptTheirCallers:
    """Every call site's keyword arguments must bind to the real task.

    Celery checks arguments in ``apply_async`` before the message reaches the
    broker, so a signature mismatch is a hard TypeError at request time, not a
    worker-side problem. Both endpoints had one.
    """

    def test_broadcast_task_is_exported(self):
        """The broadcast endpoint's import must resolve; it did not exist."""
        assert hasattr(tasks_pkg, "send_whatsapp_broadcast")

    def test_both_tasks_are_actually_called(self):
        """Guards the test below from passing because it found nothing."""
        called = {name for name, _, _ in _delay_call_sites()}
        assert called == {"send_whatsapp_message", "send_whatsapp_broadcast"}

    @pytest.mark.parametrize(
        ("task_name", "keywords", "where"),
        _delay_call_sites(),
        ids=lambda value: value if isinstance(value, str) else "",
    )
    def test_call_site_kwargs_bind(self, task_name, keywords, where):
        """What each caller passes is what the task takes."""
        task = getattr(tasks_pkg, task_name)
        try:
            inspect.signature(task.run).bind(**dict.fromkeys(keywords))
        except TypeError as e:
            pytest.fail(f"{where} cannot call {task_name}: {e}")


class TestBroadcastFansOut:
    """A broadcast queues one send per row the endpoint committed."""

    def test_one_send_per_message(self, monkeypatch):
        """The rows already exist, so the task only queues them."""
        queued: list[dict] = []

        class _Delay:
            @staticmethod
            def delay(**kwargs):
                queued.append(kwargs)

        monkeypatch.setattr(whatsapp_module, "send_whatsapp_message", _Delay)

        result = whatsapp_module.send_whatsapp_broadcast(
            message_ids=[42, 43, 44], tenant_id=1
        )

        assert queued == [
            {"message_id": 42, "tenant_id": 1},
            {"message_id": 43, "tenant_id": 1},
            {"message_id": 44, "tenant_id": 1},
        ]
        assert result == {"queued": 3}

    def test_empty_broadcast_queues_nothing(self, monkeypatch):
        """No recipients opted in, so there is nothing to send."""
        monkeypatch.setattr(
            whatsapp_module,
            "send_whatsapp_message",
            pytest.fail,  # calling it at all is the failure
        )

        assert whatsapp_module.send_whatsapp_broadcast(message_ids=[], tenant_id=1) == {
            "queued": 0
        }


class TestSendUsesTheCommittedRow:
    """The task sends the row it is given and never creates another."""

    def test_sends_the_template_to_the_contact(self, monkeypatch, client_calls):
        """Everything the send needs comes off the row."""
        template = FakeTemplate()
        session = FakeSession(
            message=FakeMessage(contact=FakeContact(), template=template)
        )

        result = _run(monkeypatch, session)

        assert result["status"] == "sent"
        assert len(client_calls) == 1
        kind, kwargs = client_calls[0]
        assert kind == "template"
        assert kwargs["to"] == "+15551234567"
        assert kwargs["template"] == "order_update"

    def test_writes_no_second_message_row(self, monkeypatch, client_calls):
        """The endpoint already committed the row; a duplicate is a bug."""
        session = FakeSession(
            message=FakeMessage(contact=FakeContact(), template=FakeTemplate())
        )

        _run(monkeypatch, session)

        assert session.added == []

    def test_records_the_outcome_on_the_row(self, monkeypatch, client_calls):
        """A row that says SENT must carry the id Meta gave back."""
        message = FakeMessage(contact=FakeContact(), template=FakeTemplate())
        session = FakeSession(message=message)

        _run(monkeypatch, session)

        assert message.wamid == "wamid.TEST"
        assert message.status is WhatsAppMessageStatus.SENT
        assert message.sent_at is not None

    def test_is_scoped_to_the_tenant(self, monkeypatch, client_calls):
        """The lookup must filter on tenant_id, not just the message id."""
        session = FakeSession(
            message=FakeMessage(contact=FakeContact(), template=FakeTemplate())
        )

        _run(monkeypatch, session)

        select_sql = session.queries[0].lower()
        assert "whatsapp_messages.tenant_id" in select_sql, (
            "a message id alone must not be enough to send a row; got: "
            f"{select_sql}"
        )


class TestSendRefuses:
    """Anything that would send the wrong message sends nothing."""

    def test_a_row_meta_already_accepted(self, monkeypatch, client_calls):
        """wamid is the idempotency key: beat re-dispatch must not resend."""
        message = FakeMessage(
            contact=FakeContact(), template=FakeTemplate(), wamid="wamid.ALREADY"
        )
        session = FakeSession(message=message)

        result = _run(monkeypatch, session)

        assert result["status"] == "already_sent"
        assert client_calls == []
        assert session.commits == 0

    def test_a_contact_that_opted_out_after_queueing(self, monkeypatch, client_calls):
        """A broadcast can sit in the queue past the contact's consent."""
        message = FakeMessage(
            contact=FakeContact(opt_in_status=WhatsAppOptInStatus.OPTED_OUT),
            template=FakeTemplate(),
        )
        session = FakeSession(message=message)

        result = _run(monkeypatch, session)

        assert result["status"] == "failed"
        assert client_calls == []
        assert message.status is WhatsAppMessageStatus.FAILED
        assert "opted in" in message.error_message

    def test_a_template_that_is_no_longer_approved(self, monkeypatch, client_calls):
        """No template row resolves, so there is nothing safe to send."""
        message = FakeMessage(contact=FakeContact(), template=None)
        session = FakeSession(message=message, template=None)

        result = _run(monkeypatch, session)

        assert result["status"] == "failed"
        assert client_calls == []
        assert message.status is WhatsAppMessageStatus.FAILED
        assert "not approved" in message.error_message

    def test_a_message_id_outside_the_tenant(self, monkeypatch, client_calls):
        """The scoped lookup finds nothing, and nothing is sent."""
        session = FakeSession(message=None)

        result = _run(monkeypatch, session)

        assert result["status"] == "not_found"
        assert client_calls == []
        assert session.commits == 0

    def test_a_failed_send_is_recorded_and_raised(self, monkeypatch, client_calls):
        """The row must say why, and the exception must reach Celery's retry."""

        class _Failing(FakeClient):
            def send_template_message(self, **kwargs):
                """Fail the way the Graph API client does."""
                raise whatsapp_module.WhatsAppAPIError("rate limited")

        monkeypatch.setattr(
            "app.services.whatsapp.client.WhatsAppClient", _Failing, raising=True
        )
        message = FakeMessage(contact=FakeContact(), template=FakeTemplate())
        session = FakeSession(message=message)

        with pytest.raises(whatsapp_module.WhatsAppAPIError):
            _run(monkeypatch, session)

        assert message.status is WhatsAppMessageStatus.FAILED
        assert message.error_message == "rate limited"
        assert message.wamid is None, "a failed send must stay re-sendable"
        assert message.retry_count == 1
