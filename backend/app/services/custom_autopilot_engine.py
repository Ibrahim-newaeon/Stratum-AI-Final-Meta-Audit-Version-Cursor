"""
Custom Autopilot evaluation engine.

Evaluates tenant-defined rules against campaign metrics, applies the Trust Gate,
and — on PASS — enqueues SAFE Autopilot actions into ``fact_actions_queue``.

This module never imports Meta write clients. Meta API mutation remains the
sole responsibility of ``action_executor`` / ``write_client``, which stay
disabled by default via ``autopilot_execution_enabled``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.autopilot.service import SAFE_ACTIONS, ActionStatus, ActionType
from app.base_models import Campaign, CampaignStatus
from app.core.logging import get_logger
from app.models.custom_autopilot import (
    CustomAutopilotRule,
    CustomAutopilotRuleExecution,
    CustomAutopilotRuleStatus,
)
from app.models.trust_layer import FactActionsQueue
from app.stratum.core.trust_gate import GateDecision
from app.tasks.apply_actions_queue import check_signal_health

logger = get_logger(__name__)

METRICS = {
    "spend": lambda c: float(Decimal(c.total_spend_cents or 0) / 100),
    "roas": lambda c: float(c.roas) if c.roas is not None else None,
    "ctr": lambda c: float(c.ctr) if c.ctr is not None else None,
    "cpc": (
        lambda c: float(Decimal(c.cpc_cents or 0) / 100) if c.cpc_cents is not None else None
    ),
    "cpa": (
        lambda c: float(Decimal(c.cpa_cents or 0) / 100) if c.cpa_cents is not None else None
    ),
    "impressions": lambda c: float(c.impressions or 0),
    "clicks": lambda c: float(c.clicks or 0),
    "conversions": lambda c: float(c.conversions or 0),
}

OPS = {
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "eq": lambda a, b: a == b,
}

# Cap percentage decreases so a bad rule cannot slash budgets/bids aggressively.
MAX_DECREASE_PERCENT = 20.0


class CustomAutopilotEngine:
    """Evaluate Custom Autopilot rules and enqueue Autopilot queue rows."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def evaluate_tenant_rules(self, tenant_id: int) -> dict[str, Any]:
        result = await self.db.execute(
            select(CustomAutopilotRule).where(
                and_(
                    CustomAutopilotRule.tenant_id == tenant_id,
                    CustomAutopilotRule.status == CustomAutopilotRuleStatus.ACTIVE.value,
                    CustomAutopilotRule.is_deleted.is_(False),
                )
            )
        )
        rules = list(result.scalars().all())
        summary: dict[str, Any] = {
            "tenant_id": tenant_id,
            "rules_evaluated": 0,
            "matches": 0,
            "enqueued": 0,
            "held": 0,
            "blocked": 0,
            "skipped": 0,
        }
        for rule in rules:
            outcome = await self.evaluate_rule(rule)
            summary["rules_evaluated"] += 1
            summary["matches"] += outcome.get("matches", 0)
            summary["enqueued"] += outcome.get("enqueued", 0)
            summary["held"] += outcome.get("held", 0)
            summary["blocked"] += outcome.get("blocked", 0)
            summary["skipped"] += outcome.get("skipped", 0)
        return summary

    async def evaluate_rule(self, rule: CustomAutopilotRule) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        outcome = {"matches": 0, "enqueued": 0, "held": 0, "blocked": 0, "skipped": 0}

        action_spec = self._primary_action(rule)
        if action_spec is None:
            await self._record_execution(
                rule,
                matched=False,
                enqueued=False,
                gate_decision="skipped",
                gate_reason="no SAFE Autopilot action configured",
                detail={"actions": rule.actions},
            )
            outcome["skipped"] += 1
            rule.last_evaluated_at = now
            return outcome

        action_type = action_spec["type"]
        action_config = action_spec["config"]

        if not await self._within_rate_limits(rule, now):
            await self._record_execution(
                rule,
                matched=False,
                enqueued=False,
                gate_decision="skipped",
                gate_reason="cooldown or daily execution limit",
                action_type=action_type,
                detail={
                    "cooldown_hours": rule.cooldown_hours,
                    "max_executions_per_day": rule.max_executions_per_day,
                },
            )
            outcome["skipped"] += 1
            rule.last_evaluated_at = now
            return outcome

        campaigns = await self._candidate_campaigns(rule)
        if not campaigns:
            rule.last_evaluated_at = now
            return outcome

        gate = await check_signal_health(self.db, rule.tenant_id)
        if gate.decision is GateDecision.BLOCK:
            await self._record_execution(
                rule,
                matched=False,
                enqueued=False,
                gate_decision=gate.decision.value,
                gate_reason=gate.reason,
                signal_health_score=gate.score,
                action_type=action_type,
                detail={"campaigns_checked": len(campaigns)},
            )
            outcome["blocked"] += 1
            rule.last_evaluated_at = now
            return outcome

        matched_campaigns = [c for c in campaigns if self._conditions_match(rule, c)]
        if not matched_campaigns:
            rule.last_evaluated_at = now
            return outcome

        outcome["matches"] = len(matched_campaigns)

        if gate.decision is GateDecision.HOLD or not gate.may_execute:
            await self._record_execution(
                rule,
                matched=True,
                enqueued=False,
                gate_decision=gate.decision.value,
                gate_reason=gate.reason,
                signal_health_score=gate.score,
                action_type=action_type,
                campaign_id=matched_campaigns[0].id,
                detail={"matched_campaign_ids": [c.id for c in matched_campaigns]},
            )
            outcome["held"] += 1
            rule.last_evaluated_at = now
            rule.last_triggered_at = now
            rule.trigger_count = int(rule.trigger_count or 0) + 1
            return outcome

        # Gate PASS — enqueue at most one action per rule evaluation (first match).
        campaign = matched_campaigns[0]
        queued, skip_reason = await self._enqueue_action(
            rule, campaign, action_type, action_config, gate.score
        )
        await self._record_execution(
            rule,
            matched=True,
            enqueued=queued is not None,
            gate_decision=gate.decision.value,
            gate_reason=gate.reason if queued is not None else (skip_reason or gate.reason),
            signal_health_score=gate.score,
            queued_action_id=queued.id if queued else None,
            campaign_id=campaign.id,
            action_type=action_type,
            detail={
                "campaign_id": campaign.id,
                "campaign_external_id": campaign.external_id,
                "action_type": action_type,
                "require_approval": rule.require_approval,
                "skip_reason": skip_reason,
            },
        )
        if queued is not None:
            outcome["enqueued"] = 1
            rule.trigger_count = int(rule.trigger_count or 0) + 1
            rule.last_triggered_at = now
        elif skip_reason:
            outcome["skipped"] += 1
        rule.last_evaluated_at = now
        return outcome

    def _primary_action(self, rule: CustomAutopilotRule) -> Optional[dict[str, Any]]:
        for raw in rule.actions or []:
            if not isinstance(raw, dict):
                continue
            action_type = str(raw.get("type") or "")
            try:
                enum_type = ActionType(action_type)
            except ValueError:
                continue
            if enum_type not in SAFE_ACTIONS:
                continue
            if enum_type is ActionType.PAUSE_CREATIVE:
                continue
            config = raw.get("config") if isinstance(raw.get("config"), dict) else {}
            return {"type": enum_type.value, "config": dict(config)}
        return None

    async def _within_rate_limits(self, rule: CustomAutopilotRule, now: datetime) -> bool:
        if rule.last_triggered_at and rule.cooldown_hours:
            last = rule.last_triggered_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if now - last < timedelta(hours=int(rule.cooldown_hours)):
                return False

        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        result = await self.db.execute(
            select(func.count())
            .select_from(CustomAutopilotRuleExecution)
            .where(
                and_(
                    CustomAutopilotRuleExecution.rule_id == rule.id,
                    CustomAutopilotRuleExecution.enqueued.is_(True),
                    CustomAutopilotRuleExecution.created_at >= day_start,
                )
            )
        )
        today_count = int(result.scalar() or 0)
        return today_count < int(rule.max_executions_per_day or 1)

    async def _candidate_campaigns(self, rule: CustomAutopilotRule) -> list[Campaign]:
        stmt = select(Campaign).where(
            and_(
                Campaign.tenant_id == rule.tenant_id,
                Campaign.is_deleted.is_(False),
                Campaign.status == CampaignStatus.ACTIVE,
            )
        )
        result = await self.db.execute(stmt.limit(100))
        return list(result.scalars().all())

    def _conditions_match(self, rule: CustomAutopilotRule, campaign: Campaign) -> bool:
        conditions = rule.conditions or []
        if not conditions:
            return False
        results: list[bool] = []
        for condition in conditions:
            if not isinstance(condition, dict):
                results.append(False)
                continue
            field = condition.get("field") or condition.get("metric")
            operator = condition.get("operator")
            threshold = condition.get("value")
            getter = METRICS.get(str(field or ""))
            op_fn = OPS.get(str(operator or ""))
            if getter is None or op_fn is None or threshold is None:
                results.append(False)
                continue
            actual = getter(campaign)
            if actual is None:
                results.append(False)
                continue
            try:
                results.append(bool(op_fn(float(actual), float(threshold))))
            except (TypeError, ValueError):
                results.append(False)
        return bool(results) and all(results)

    async def _enqueue_action(
        self,
        rule: CustomAutopilotRule,
        campaign: Campaign,
        action_type: str,
        action_config: dict[str, Any],
        signal_health_score: Optional[float],
    ) -> tuple[Optional[FactActionsQueue], Optional[str]]:
        try:
            enum_type = ActionType(action_type)
        except ValueError:
            return None, f"unknown action_type {action_type}"

        if enum_type not in SAFE_ACTIONS:
            return None, f"action_type {action_type} is not SAFE"

        platform = getattr(campaign.platform, "value", None) or str(campaign.platform or "meta")
        if platform in ("facebook", "instagram", "whatsapp"):
            platform = "meta"

        entity_type: str
        entity_id: str
        payload: dict[str, Any] = {
            "source": "custom_autopilot",
            "custom_rule_id": str(rule.id),
            "custom_rule_name": rule.name,
            "signal_health_score": signal_health_score,
            "reason": f"Custom Autopilot rule '{rule.name}' matched",
        }
        before_value: Optional[dict[str, Any]] = None

        if enum_type in (ActionType.BUDGET_DECREASE, ActionType.BID_DECREASE):
            if not campaign.external_id:
                return None, "campaign missing external_id"
            percent = float(action_config.get("percentage", 10))
            if percent <= 0:
                percent = 10.0
            if percent > MAX_DECREASE_PERCENT:
                percent = MAX_DECREASE_PERCENT
            entity_type = "campaign"
            entity_id = str(campaign.external_id)
            payload["percentage"] = percent
            if (
                enum_type is ActionType.BUDGET_DECREASE
                and campaign.daily_budget_cents is not None
            ):
                before_value = {"daily_budget_cents": campaign.daily_budget_cents}
        elif enum_type is ActionType.PAUSE_ADSET:
            adset_id = action_config.get("adset_id") or action_config.get("entity_id")
            if not adset_id:
                return None, "pause_adset requires config.adset_id"
            entity_type = "adset"
            entity_id = str(adset_id)
            payload["status"] = "PAUSED"
            before_value = {"status": "ACTIVE"}
        else:
            return None, f"action_type {action_type} not supported by Custom Autopilot"

        now = datetime.now(timezone.utc)
        status = (
            ActionStatus.QUEUED.value
            if rule.require_approval
            else ActionStatus.APPROVED.value
        )
        row = FactActionsQueue(
            tenant_id=rule.tenant_id,
            date=now.date(),
            action_type=enum_type.value,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=campaign.name,
            platform=platform,
            action_json=json.dumps(payload),
            before_value=json.dumps(before_value) if before_value else None,
            status=status,
            created_by_user_id=None,
            approved_by_user_id=None,
            approved_at=None if rule.require_approval else now,
        )
        self.db.add(row)
        await self.db.flush()
        logger.info(
            "custom_autopilot_enqueued rule_id=%s action_id=%s action_type=%s status=%s campaign_id=%s",
            rule.id,
            row.id,
            enum_type.value,
            status,
            campaign.id,
        )
        return row, None

    async def _record_execution(
        self,
        rule: CustomAutopilotRule,
        *,
        matched: bool,
        enqueued: bool,
        gate_decision: str,
        gate_reason: Optional[str],
        signal_health_score: Optional[float] = None,
        queued_action_id: Optional[uuid.UUID] = None,
        campaign_id: Optional[int] = None,
        action_type: Optional[str] = None,
        detail: Optional[dict[str, Any]] = None,
    ) -> CustomAutopilotRuleExecution:
        row = CustomAutopilotRuleExecution(
            tenant_id=rule.tenant_id,
            rule_id=rule.id,
            campaign_id=campaign_id,
            matched=matched,
            enqueued=enqueued,
            gate_decision=gate_decision,
            gate_reason=gate_reason,
            signal_health_score=signal_health_score,
            queued_action_id=queued_action_id,
            action_type=action_type,
            detail=detail or {},
        )
        self.db.add(row)
        await self.db.flush()
        return row
