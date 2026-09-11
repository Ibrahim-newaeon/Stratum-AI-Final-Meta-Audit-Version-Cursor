"""Unit tests for Custom Autopilot engine.

Verifies SAFE-only enqueue, Trust Gate fail-closed behavior, and that this
module never imports Meta write clients.
"""

from __future__ import annotations

import ast
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.autopilot.service import SAFE_ACTIONS, ActionStatus, ActionType
from app.schemas.custom_autopilot import ALLOWED_ACTION_TYPES
from app.services.custom_autopilot_engine import (
    MAX_DECREASE_PERCENT,
    CustomAutopilotEngine,
)
from app.stratum.core.trust_gate import GateDecision

ENGINE_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "services" / "custom_autopilot_engine.py"
)


def test_engine_module_does_not_import_write_clients():
    source = ENGINE_PATH.read_text()
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imported.append(mod)
            imported.extend(f"{mod}.{alias.name}" for alias in node.names)
    joined = " ".join(imported)
    assert "write_client" not in joined
    assert "action_executor" not in joined
    # Docstrings may name write_client / action_executor to explain the boundary;
    # the AST import check above is the load-bearing guard.


def test_allowed_actions_are_subset_of_safe_actions():
    safe_values = {a.value for a in SAFE_ACTIONS}
    assert set(ALLOWED_ACTION_TYPES) <= safe_values
    assert "budget_increase" not in ALLOWED_ACTION_TYPES
    assert "pause_campaign" not in ALLOWED_ACTION_TYPES


def _gate(decision: GateDecision, score: float = 80.0):
    return SimpleNamespace(
        decision=decision,
        reason=f"gate={decision.value}",
        score=score,
        may_execute=decision is GateDecision.PASS,
    )


def _rule(**overrides):
    base = dict(
        id=uuid.uuid4(),
        tenant_id=1,
        name="Cut waste",
        actions=[{"type": "budget_decrease", "config": {"percentage": 15}}],
        conditions=[{"field": "roas", "operator": "lt", "value": 1.0}],
        require_approval=True,
        cooldown_hours=0,
        max_executions_per_day=10,
        last_triggered_at=None,
        last_evaluated_at=None,
        trigger_count=0,
        status="active",
        is_deleted=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _campaign(**overrides):
    base = dict(
        id=42,
        tenant_id=1,
        name="Prospecting",
        external_id="120001",
        platform="meta",
        status="active",
        is_deleted=False,
        roas=0.5,
        ctr=1.0,
        cpc_cents=100,
        cpa_cents=5000,
        total_spend_cents=10000,
        impressions=1000,
        clicks=10,
        conversions=1,
        daily_budget_cents=5000,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_block_gate_does_not_enqueue():
    db = AsyncMock()
    engine = CustomAutopilotEngine(db)
    rule = _rule()
    campaign = _campaign()

    with (
        patch.object(engine, "_within_rate_limits", AsyncMock(return_value=True)),
        patch.object(engine, "_candidate_campaigns", AsyncMock(return_value=[campaign])),
        patch(
            "app.services.custom_autopilot_engine.check_signal_health",
            AsyncMock(return_value=_gate(GateDecision.BLOCK, 10)),
        ),
        patch.object(engine, "_record_execution", AsyncMock()) as record,
        patch.object(engine, "_enqueue_action", AsyncMock()) as enqueue,
    ):
        outcome = await engine.evaluate_rule(rule)

    assert outcome["blocked"] == 1
    assert outcome["enqueued"] == 0
    enqueue.assert_not_called()
    assert record.await_args.kwargs["enqueued"] is False
    assert record.await_args.kwargs["gate_decision"] == "block"


@pytest.mark.asyncio
async def test_hold_gate_matches_but_does_not_enqueue():
    db = AsyncMock()
    engine = CustomAutopilotEngine(db)
    rule = _rule()
    campaign = _campaign()

    with (
        patch.object(engine, "_within_rate_limits", AsyncMock(return_value=True)),
        patch.object(engine, "_candidate_campaigns", AsyncMock(return_value=[campaign])),
        patch(
            "app.services.custom_autopilot_engine.check_signal_health",
            AsyncMock(return_value=_gate(GateDecision.HOLD, 55)),
        ),
        patch.object(engine, "_record_execution", AsyncMock()) as record,
        patch.object(engine, "_enqueue_action", AsyncMock()) as enqueue,
    ):
        outcome = await engine.evaluate_rule(rule)

    assert outcome["matches"] == 1
    assert outcome["held"] == 1
    assert outcome["enqueued"] == 0
    enqueue.assert_not_called()
    assert record.await_args.kwargs["matched"] is True
    assert record.await_args.kwargs["enqueued"] is False


@pytest.mark.asyncio
async def test_pass_gate_enqueues_safe_action():
    db = AsyncMock()
    engine = CustomAutopilotEngine(db)
    rule = _rule(require_approval=False)
    campaign = _campaign()
    queued = SimpleNamespace(id=uuid.uuid4())

    with (
        patch.object(engine, "_within_rate_limits", AsyncMock(return_value=True)),
        patch.object(engine, "_candidate_campaigns", AsyncMock(return_value=[campaign])),
        patch(
            "app.services.custom_autopilot_engine.check_signal_health",
            AsyncMock(return_value=_gate(GateDecision.PASS, 90)),
        ),
        patch.object(engine, "_record_execution", AsyncMock()) as record,
        patch.object(
            engine, "_enqueue_action", AsyncMock(return_value=(queued, None))
        ) as enqueue,
    ):
        outcome = await engine.evaluate_rule(rule)

    assert outcome["enqueued"] == 1
    enqueue.assert_awaited_once()
    assert record.await_args.kwargs["enqueued"] is True
    assert record.await_args.kwargs["queued_action_id"] == queued.id


@pytest.mark.asyncio
async def test_unsafe_action_is_skipped():
    db = AsyncMock()
    engine = CustomAutopilotEngine(db)
    rule = _rule(actions=[{"type": "budget_increase", "config": {"percentage": 10}}])

    with patch.object(engine, "_record_execution", AsyncMock()) as record:
        outcome = await engine.evaluate_rule(rule)

    assert outcome["skipped"] == 1
    assert record.await_args.kwargs["gate_decision"] == "skipped"


@pytest.mark.asyncio
async def test_enqueue_caps_percentage_and_sets_queued_status():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    engine = CustomAutopilotEngine(db)
    rule = _rule(require_approval=True)
    campaign = _campaign()

    row, err = await engine._enqueue_action(
        rule, campaign, "budget_decrease", {"percentage": 99}, 88.0
    )
    assert err is None
    assert row is not None
    assert row.status == ActionStatus.QUEUED.value
    assert row.action_type == ActionType.BUDGET_DECREASE.value
    payload = json.loads(row.action_json)
    assert payload["percentage"] == MAX_DECREASE_PERCENT
    assert payload["source"] == "custom_autopilot"


@pytest.mark.asyncio
async def test_pause_adset_requires_adset_id():
    db = AsyncMock()
    engine = CustomAutopilotEngine(db)
    rule = _rule(actions=[{"type": "pause_adset", "config": {}}])
    campaign = _campaign()

    row, err = await engine._enqueue_action(rule, campaign, "pause_adset", {}, 90.0)
    assert row is None
    assert "adset_id" in (err or "")


@pytest.mark.asyncio
async def test_auto_approve_sets_approved_status():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    engine = CustomAutopilotEngine(db)
    rule = _rule(require_approval=False)
    campaign = _campaign()

    row, err = await engine._enqueue_action(
        rule, campaign, "bid_decrease", {"percentage": 5}, 91.0
    )
    assert err is None
    assert row is not None
    assert row.status == ActionStatus.APPROVED.value
    assert row.approved_at is not None
