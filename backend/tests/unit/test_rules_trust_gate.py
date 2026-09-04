# =============================================================================
# Stratum AI - Rules Engine Trust Gate
# =============================================================================
"""
Regression tests for the second, ungated automation path.

``evaluate_all_rules`` is beat-scheduled every 15 minutes and fans out to
``evaluate_rules``, whose ``_execute_action`` pauses campaigns, changes daily
budgets and sends WhatsApp messages. It had no signal-health check, no
enforcement-mode lookup and no audit record of any gate decision - so fixing
the worker's queue list (D1) would have switched on ungated automation, which
CLAUDE.md forbids outright ("Do NOT skip trust gate checks", "Do NOT execute
automations without audit logging").

The gate reuses the same pure evaluator as the async path, so the thresholds
cannot drift between them. The documented bands apply:

* PASS  -> the action executes
* HOLD  -> "alert only": labels and alerts still go out, platform mutations are
  held and recorded with the reason
* BLOCK -> nothing runs at all
"""

import re
from datetime import UTC, datetime

import pytest

from app.models import RuleExecution
from app.stratum.core.trust_gate import GateDecision
from app.tasks.apply_actions_queue import SignalHealthGateResult
from app.workers.tasks import rules as rules_module

TENANT_ID = 1
RULE_ID = 7


class FakeRule:
    """Minimal active rule."""

    def __init__(self, action_type="pause_campaign", conditions=None):
        self.id = RULE_ID
        self.tenant_id = TENANT_ID
        self.name = "Pause the losers"
        self.action_type = action_type
        self.action_config = {}
        self.conditions = (
            conditions
            if conditions is not None
            else [{"metric": "spend_cents", "operator": "greater_than", "value": "100"}]
        )


class FakeCampaign:
    """Minimal campaign the rule can match and mutate."""

    def __init__(self):
        self.id = 99
        self.tenant_id = TENANT_ID
        self.name = "Campaign A"
        self.status = "active"
        self.spend_cents = 5000
        self.daily_budget_cents = 10000
        self.labels = []
        self.is_deleted = False


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        """Return every row."""
        return list(self._rows)


class _FakeResult:
    def __init__(self, *, scalar=None, rows=()):
        self._scalar = scalar
        self._rows = rows

    def scalar_one_or_none(self):
        """Return the single object, or None."""
        return self._scalar

    def scalars(self):
        """Return the row collection."""
        return _FakeScalars(self._rows)


class FakeSession:
    """Sync session stand-in answering the rule lookup and campaign query."""

    def __init__(self, rule, campaigns):
        self.rule = rule
        self.campaigns = campaigns
        self.added: list[object] = []
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def execute(self, statement):
        """Answer whichever of the two queries this is.

        Dispatches on the FROM clause: the rule row carries a ``campaigns``
        column, so a bare substring test would misroute the rule lookup.
        """
        sql = re.sub(r"\s+", " ", str(statement)).lower()
        if "from rules" in sql:
            return _FakeResult(scalar=self.rule)
        if "from campaigns" in sql:
            return _FakeResult(rows=self.campaigns)
        raise AssertionError(f"unexpected query: {sql[:200]}")

    def add(self, obj):
        """Record an inserted RuleExecution."""
        self.added.append(obj)

    def commit(self):
        """Record a commit."""
        self.commits += 1


def gate_result(decision: GateDecision, score=None) -> SignalHealthGateResult:
    """Build a gate result standing in for a real evaluation."""
    return SignalHealthGateResult(
        decision=decision,
        reason=f"test gate decision {decision.value}",
        score=score,
        health_date=datetime.now(UTC).date(),
        enforcement_mode="advisory",
        channels={"facebook": {"score": score, "decision": decision.value}},
    )


@pytest.fixture
def wire(monkeypatch):
    """Patch the rules module's session, gate and event publisher."""

    def _wire(decision, *, action_type="pause_campaign", conditions=None):
        rule = FakeRule(action_type=action_type, conditions=conditions)
        campaign = FakeCampaign()
        session = FakeSession(rule, [campaign])

        monkeypatch.setattr(rules_module, "SyncSessionLocal", lambda: session)
        monkeypatch.setattr(
            rules_module,
            "check_signal_health_sync",
            lambda db, tenant_id: gate_result(decision, score=55.0),
        )
        monkeypatch.setattr(rules_module, "publish_event", lambda *a, **k: None)
        return session, campaign, rule

    return _wire


class TestRulesRespectTheTrustGate:
    """The rules engine must not mutate campaigns on unverified signals."""

    def test_block_executes_nothing(self, wire):
        """A BLOCK stops the run before any campaign is touched."""
        session, campaign, _ = wire(GateDecision.BLOCK)

        result = rules_module.evaluate_rules.run(TENANT_ID, RULE_ID)

        assert result["status"] == "blocked"
        assert result["executions"] == 0
        assert campaign.status == "active"
        assert campaign.daily_budget_cents == 10000
        assert session.added == []

    def test_block_records_the_reason_for_audit(self, wire):
        """The decision and its inputs must be reportable."""
        wire(GateDecision.BLOCK)

        result = rules_module.evaluate_rules.run(TENANT_ID, RULE_ID)

        audit = result["trust_gate"]
        assert audit["decision"] == "block"
        assert audit["reason"]
        assert "healthy_threshold" in audit
        assert "channels" in audit

    def test_hold_does_not_mutate_the_campaign(self, wire):
        """40-69 is alert-only: a pause must not reach the platform."""
        _, campaign, _ = wire(GateDecision.HOLD)

        result = rules_module.evaluate_rules.run(TENANT_ID, RULE_ID)

        assert campaign.status == "active"
        assert result["executions"] == 0
        assert result["held"] == 1

    def test_hold_audit_logs_the_held_action(self, wire):
        """A held action is still recorded, with the gate's reason."""
        session, _, _ = wire(GateDecision.HOLD)

        rules_module.evaluate_rules.run(TENANT_ID, RULE_ID)

        assert len(session.added) == 1
        held = session.added[0].action_result
        assert held["held"] is True
        assert held["success"] is False
        assert held["trust_gate"]["decision"] == "hold"

    def test_hold_still_allows_alert_only_actions(self, wire):
        """The DEGRADED band is "alert only", not "do nothing"."""
        _, campaign, _ = wire(GateDecision.HOLD, action_type="apply_label")
        campaign.labels = []

        result = rules_module.evaluate_rules.run(TENANT_ID, RULE_ID)

        assert result["executions"] == 1
        assert result["held"] == 0

    def test_pass_executes_and_records_the_gate(self, wire):
        """A healthy tenant still gets its automation, with the gate attached."""
        session, campaign, _ = wire(GateDecision.PASS)

        result = rules_module.evaluate_rules.run(TENANT_ID, RULE_ID)

        assert result["executions"] == 1
        assert campaign.status == "paused"
        assert session.added[0].action_result["trust_gate"]["decision"] == "pass"

    def test_budget_change_is_held_below_the_threshold(self, wire):
        """Budget moves are platform mutations, not alerts."""
        _, campaign, _ = wire(GateDecision.HOLD, action_type="adjust_budget")

        rules_module.evaluate_rules.run(TENANT_ID, RULE_ID)

        assert campaign.daily_budget_cents == 10000


class TestEmptyConditions:
    """An unconfigured rule must not fire against every campaign."""

    def test_rule_without_conditions_matches_nothing(self, wire):
        """``all_match`` started True and was returned unchanged."""
        _, campaign, _ = wire(GateDecision.PASS, conditions=[])

        result = rules_module.evaluate_rules.run(TENANT_ID, RULE_ID)

        assert result["matches"] == 0
        assert result["executions"] == 0
        assert campaign.status == "active"


class TestRuleExecutionUsesRealColumns:
    """The audit row must match the RuleExecution model.

    The task constructed ``RuleExecution(triggered_at=..., condition_values=...,
    action_taken=...)``, none of which are columns on that model, so the first
    matching campaign raised TypeError. The mutation had already been applied
    to the session and the WhatsApp alert already dispatched, but the commit
    never ran - so a matching rule looped on a crash every 15 minutes.
    """

    def test_executed_rows_are_constructible(self, wire):
        """A real RuleExecution is built, not a TypeError."""
        session, _, _ = wire(GateDecision.PASS)

        rules_module.evaluate_rules.run(TENANT_ID, RULE_ID)

        row = session.added[0]
        assert isinstance(row, RuleExecution)
        assert row.triggered is True
        assert row.condition_result is not None
        assert row.executed_at is not None

    def test_held_rows_are_constructible(self, wire):
        """The held path builds a valid row too."""
        session, _, _ = wire(GateDecision.HOLD)

        rules_module.evaluate_rules.run(TENANT_ID, RULE_ID)

        row = session.added[0]
        assert isinstance(row, RuleExecution)
        assert row.action_result["held"] is True
