# =============================================================================
# Stratum AI - Durable Trust Gate Audit (Apply Path)
# =============================================================================
"""
DEF-002: worker apply audits must land in ``TrustGateAuditLog``, not stdout only.

Preview dry-runs already persist this table. The apply queue used to emit
``AUDIT:`` log lines and nothing else, so a worker restart erased the trail
and operators could not query held/blocked decisions.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.trust_layer import TrustGateAuditLog
from app.services.meta.action_executor import ActionOutcome, ExecutionStatus
from app.stratum.core.trust_gate import GateDecision
from app.tasks import apply_actions_queue as apply_module


class FakeAsyncSession:
    """Minimal async session that records ``add`` calls."""

    def __init__(self):
        self.added: list[object] = []

    def add(self, obj):
        """Record an inserted ORM object."""
        self.added.append(obj)


def _action(**overrides):
    """Build a FactActionsQueue-like stand-in."""
    base = {
        "id": uuid4(),
        "tenant_id": 3,
        "action_type": "pause",
        "entity_type": "campaign",
        "entity_id": "camp_42",
        "entity_name": "Prospecting",
        "platform": "facebook",
        "approved_by_user_id": 11,
        "applied_by_user_id": None,
        "action_json": '{"target": "PAUSED"}',
        "applied_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _gate(decision: GateDecision, score: float | None = 22.0) -> apply_module.SignalHealthGateResult:
    """Build a gate result for audit helpers."""
    return apply_module.SignalHealthGateResult(
        decision=decision,
        reason=f"test {decision.value}",
        score=score,
        health_date=date.today(),
        enforcement_mode="advisory",
        channels={"facebook": {"score": score, "decision": decision.value}},
    )


@pytest.mark.asyncio
async def test_log_gate_decision_audit_persists_trust_gate_audit_log():
    """A held/blocked gate decision writes a durable TrustGateAuditLog row."""
    db = FakeAsyncSession()
    action = _action()
    gate = _gate(GateDecision.BLOCK, score=18.0)

    await apply_module.log_gate_decision_audit(db=db, action=action, gate=gate)

    assert len(db.added) == 1
    row = db.added[0]
    assert isinstance(row, TrustGateAuditLog)
    assert row.tenant_id == 3
    assert row.decision_type == "block"
    assert row.gate_passed == 0
    assert row.action_type == "pause"
    assert row.entity_id == "camp_42"
    assert row.signal_health_score == 18.0
    assert row.healthy_threshold is not None
    assert row.degraded_threshold is not None
    assert row.triggered_by_user_id == 11
    assert row.triggered_by_system == 1
    assert row.is_dry_run == 0


@pytest.mark.asyncio
async def test_log_action_audit_persists_execute_decision_for_pass():
    """An execution attempt under PASS writes decision_type=execute."""
    db = FakeAsyncSession()
    action = _action(applied_by_user_id=22, applied_at=datetime.now(UTC))
    gate = _gate(GateDecision.PASS, score=88.0)
    outcome = ActionOutcome(
        status=ExecutionStatus.DRY_RUN,
        reason="dry run",
        before_value={"status": "ACTIVE"},
        after_value=None,
    )

    await apply_module.log_action_audit(db=db, action=action, outcome=outcome, gate=gate)

    assert len(db.added) == 1
    row = db.added[0]
    assert isinstance(row, TrustGateAuditLog)
    assert row.decision_type == "execute"
    assert row.gate_passed == 1
    assert row.is_dry_run == 1
    assert row.triggered_by_user_id == 22
