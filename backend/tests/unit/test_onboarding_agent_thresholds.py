# =============================================================================
# Stratum AI - Onboarding Chat Threshold Persistence Tests
# =============================================================================
"""
Regression tests for the trust threshold the onboarding chat used to drop.

``RootAgent._handle_thresholds`` collected the tenant's answer onto
``ConversationContext.onboarding_data`` and the conversation ended with a
``complete_onboarding`` action - but the agent has no database access and
nothing else wrote the number down. Meanwhile
``TenantOnboarding.trust_threshold_autopilot`` / ``trust_threshold_alert``
became live configuration: ``thresholds_for_tenant`` resolves them for the
dashboard summary, for the status stamped on each daily rollup row and for the
trust gate in ``apply_actions_queue``. A tenant who answered "90" in the chat
was therefore still graded at the deployment default of 70.

Pinned here: a threshold answered in the chat reaches those columns and comes
back out of ``thresholds_for_tenant``; the completing turn of the API
conversation is what writes it; and the pair written stays ordered, so
``SignalHealthThresholds.resolve`` honours it instead of discarding it for the
defaults.
"""

from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import onboarding_agent as onboarding_agent_module
from app.core.config import settings
from app.models.onboarding import OnboardingStatus, OnboardingStep, TenantOnboarding
from app.services.agents import ConversationState, UserContext, root_agent
from app.services.signal_health import thresholds_for_tenant
from app.services.tenant.onboarding import persist_chat_onboarding

TENANT = 1
OTHER_TENANT = 2


# =============================================================================
# Test doubles
# =============================================================================


class _Result:
    """The slice of ``Result`` the onboarding queries consume."""

    def __init__(self, *, scalar=None, row=None) -> None:
        """Store what this result should hand back."""
        self._scalar = scalar
        self._row = row

    def scalar_one_or_none(self):
        """Return the single ORM entity, or None."""
        return self._scalar

    def first(self):
        """Return the single row of column values, or None."""
        return self._row


class FakeSession:
    """
    An ``AsyncSession`` stand-in holding real ``TenantOnboarding`` objects.

    It answers both onboarding queries from the same store, so the write and
    the read-back in these tests go through the production statements rather
    than through a stubbed return value. ``flush`` applies the table's scalar
    column defaults the way an INSERT would, which is what gives a freshly
    created record its default alert threshold.
    """

    def __init__(self, records=()) -> None:
        """Seed the store with records belonging to several tenants."""
        self.records: dict[int, TenantOnboarding] = {
            record.tenant_id: record for record in records
        }
        self.committed = False

    async def execute(self, statement):
        """Answer either the entity select or the two-column threshold read."""
        sql = str(statement)
        params = dict(statement.compile().params)
        record = self.records.get(params.get("tenant_id_1"))

        if "tenant_onboarding.id" in sql:
            return _Result(scalar=record)

        if "tenant_onboarding.trust_threshold_autopilot" in sql:
            if record is None:
                return _Result(row=None)
            return _Result(
                row=(record.trust_threshold_autopilot, record.trust_threshold_alert)
            )

        raise AssertionError(f"unexpected query: {sql}")

    def add(self, record) -> None:
        """Stage a new record the way ``Session.add`` would."""
        self.records[record.tenant_id] = record

    async def flush(self) -> None:
        """Apply scalar column defaults, as an INSERT would."""
        for record in self.records.values():
            for column in TenantOnboarding.__table__.columns:
                default = column.default
                if default is None or not default.is_scalar:
                    continue
                if getattr(record, column.key, None) is None:
                    setattr(record, column.key, default.arg)

    async def commit(self) -> None:
        """Mark the session committed."""
        self.committed = True


class FakeRedis:
    """An in-memory stand-in for the session store the chat endpoints use."""

    def __init__(self) -> None:
        """Start with no sessions."""
        self.store: dict[str, str] = {}
        self.closed = False

    async def get(self, key):
        """Read a stored session payload."""
        return self.store.get(key)

    async def set(self, key, value, ex=None) -> None:
        """Write a session payload."""
        self.store[key] = value

    async def delete(self, key) -> None:
        """Drop a session payload."""
        self.store.pop(key, None)

    async def aclose(self) -> None:
        """Record that the client was closed."""
        self.closed = True


def onboarding_record(tenant_id: int, autopilot: int, alert: int) -> TenantOnboarding:
    """Build an existing onboarding record with a stored threshold pair."""
    return TenantOnboarding(
        tenant_id=tenant_id,
        status=OnboardingStatus.IN_PROGRESS.value,
        current_step=OnboardingStep.TRUST_GATE_CONFIG.value,
        completed_steps=[],
        trust_threshold_autopilot=autopilot,
        trust_threshold_alert=alert,
    )


# =============================================================================
# Helpers
# =============================================================================

# The quick replies that walk the state machine from the greeting to the point
# where it asks for a threshold.
_UP_TO_THRESHOLD = ["Get Started", "Acme Retail", "meta", "Done", "Connect Now", "Yes"]


async def converse(threshold_answer: str):
    """
    Drive a full onboarding conversation, answering the threshold question.

    Args:
        threshold_answer: What the user types at the trust threshold step.

    Returns:
        ``(response, context)`` for the turn that completes the conversation.
    """
    response, context = await root_agent.start_conversation(
        user_context=UserContext(name="Ada", language="en"),
        session_id="session-under-test",
    )
    for message in [*_UP_TO_THRESHOLD, threshold_answer, "Skip for now", "Launch"]:
        response = await root_agent.process_message(message, context)
    return response, context


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.unit
async def test_threshold_answered_in_chat_reaches_tenant_onboarding():
    """
    A threshold typed into the chat must grade the tenant.

    The agent used to collect "90" onto the conversation context and stop
    there, so the trust engine kept reading the configured 70. The completion
    step now writes it to ``TenantOnboarding``, and the same value comes back
    out of ``thresholds_for_tenant`` - the function the dashboard, the daily
    rollup and the trust gate all resolve their band edges through.
    """
    response, context = await converse("90")

    assert response.state is ConversationState.COMPLETED
    assert response.action_type == "complete_onboarding"
    assert context.onboarding_data.trust_threshold == 90

    db = FakeSession()
    record = await persist_chat_onboarding(db, TENANT, context.onboarding_data)

    assert record.trust_threshold_autopilot == 90

    thresholds = await thresholds_for_tenant(db, TENANT)
    assert thresholds.healthy == 90.0
    assert thresholds.degraded == float(settings.signal_health_degraded_threshold)
    # The tenant asked to be graded harder than the deployment default.
    assert thresholds.healthy > settings.signal_health_healthy_threshold


@pytest.mark.unit
async def test_chat_reply_promises_only_what_is_persisted():
    """
    The confirmation must match what the completion step actually stores.

    While the number was dropped the reply pointed at the onboarding summary
    and the wizard's trust gate step. Now that the conversation writes the
    column the gate reads, the reply says the threshold is set - so the two
    must not drift apart again.
    """
    _, context = await converse("90")

    reply = next(
        entry["content"]
        for entry in context.history
        if entry["role"] == "assistant" and "Trust threshold set to" in entry["content"]
    )
    assert "90%" in reply
    assert "summary" not in reply

    db = FakeSession()
    record = await persist_chat_onboarding(db, TENANT, context.onboarding_data)
    assert record.trust_threshold_autopilot == 90


@pytest.mark.unit
async def test_lowered_threshold_keeps_the_stored_pair_ordered():
    """
    Lowering autopilot below a stored alert edge must not fall back silently.

    The chat only asks for the autopilot edge. ``SignalHealthThresholds``
    discards a pair where the alert edge sits above it, so writing 45 under a
    stored alert of 50 would have handed the tenant the deployment defaults
    back - the exact silent substitution this change exists to remove.
    """
    _, context = await converse("45")
    assert context.onboarding_data.trust_threshold == 45

    db = FakeSession([onboarding_record(TENANT, autopilot=70, alert=50)])
    record = await persist_chat_onboarding(db, TENANT, context.onboarding_data)

    assert record.trust_threshold_autopilot == 45
    assert record.trust_threshold_alert == 45

    thresholds = await thresholds_for_tenant(db, TENANT)
    assert thresholds.healthy == 45.0
    assert thresholds.degraded == 45.0


@pytest.mark.unit
async def test_persistence_is_scoped_to_the_conversations_tenant():
    """One tenant's chat answer must not move another tenant's band edges."""
    _, context = await converse("90")

    other = onboarding_record(OTHER_TENANT, autopilot=70, alert=40)
    db = FakeSession([other])
    await persist_chat_onboarding(db, TENANT, context.onboarding_data)

    assert other.trust_threshold_autopilot == 70
    assert (await thresholds_for_tenant(db, OTHER_TENANT)).healthy == 70.0
    assert (await thresholds_for_tenant(db, TENANT)).healthy == 90.0


@pytest.mark.unit
async def test_completing_turn_of_the_api_conversation_persists(monkeypatch):
    """
    The write has to happen on the turn the agent declares completion.

    The chat client never calls ``POST /onboarding-agent/complete``; it just
    keeps posting messages. If persistence hung off that unused endpoint the
    threshold would still be lost in practice, so this drives the endpoint the
    client actually uses and asserts the tenant's record moved.
    """
    _, context = await converse("90")
    # Rewind to the turn before "Launch": that is the message under test.
    context.state = ConversationState.REVIEWING

    redis = FakeRedis()
    session_id = context.session_id
    redis.store[f"onboarding_session:{session_id}"] = context.model_dump_json()

    async def fake_redis_client():
        return redis

    monkeypatch.setattr(onboarding_agent_module, "get_redis_client", fake_redis_client)

    db = FakeSession()
    response = await onboarding_agent_module.send_message(
        onboarding_agent_module.SendMessageRequest(
            session_id=session_id, message="Launch"
        ),
        current_user=SimpleNamespace(id=7, tenant_id=TENANT),
        db=db,
    )

    assert response.action_type == "complete_onboarding"
    assert db.committed is True
    assert db.records[TENANT].trust_threshold_autopilot == 90
    assert (await thresholds_for_tenant(db, TENANT)).healthy == 90.0
