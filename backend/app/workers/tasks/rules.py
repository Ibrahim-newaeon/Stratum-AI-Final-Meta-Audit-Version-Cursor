# =============================================================================
# Stratum AI - Rules Engine Tasks
# =============================================================================
"""
Background tasks for automation rules evaluation and execution.

Security: Beat-scheduled tasks use distributed locks to prevent
duplicate execution across multiple Celery workers.
"""

from datetime import UTC, datetime
from typing import Any

from celery import shared_task
from celery.utils.log import get_task_logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SyncSessionLocal
from app.models import (
    Campaign,
    Rule,
    RuleExecution,
    RuleStatus,
)
from app.stratum.core.trust_gate import GateDecision
from app.tasks.apply_actions_queue import check_signal_health_sync
from app.workers.celery_app import with_distributed_lock
from app.workers.tasks.helpers import publish_event

logger = get_task_logger(__name__)


@shared_task(bind=True)
def evaluate_rules(self, tenant_id: int, rule_id: int):
    """
    Evaluate a specific rule against matching campaigns.

    Args:
        tenant_id: Tenant ID for isolation
        rule_id: Rule ID to evaluate
    """
    logger.info(f"Evaluating rule {rule_id} for tenant {tenant_id}")

    with SyncSessionLocal() as db:
        rule = db.execute(
            select(Rule).where(
                Rule.id == rule_id,
                Rule.tenant_id == tenant_id,
                Rule.status == RuleStatus.ACTIVE,
            )
        ).scalar_one_or_none()

        if not rule:
            logger.warning(f"Rule {rule_id} not found or inactive")
            return {"status": "not_found"}

        # Get campaigns matching rule scope
        campaigns = (
            db.execute(
                select(Campaign).where(
                    Campaign.tenant_id == tenant_id,
                    Campaign.is_deleted == False,
                )
            )
            .scalars()
            .all()
        )

        # Trust gate. This is automation that pauses campaigns, moves budget
        # and sends messages, so it may not run on signals the Trust Engine
        # cannot vouch for (CLAUDE.md: never skip the trust gate; never
        # auto-execute when signal_health < 70). Evaluated once per rule run:
        # the snapshot is per tenant and per day, not per campaign.
        gate = check_signal_health_sync(db, tenant_id)
        gate_audit = gate.to_audit_dict()
        if gate.decision is GateDecision.BLOCK:
            logger.warning(
                f"Rule {rule_id} blocked by the trust gate: {gate.reason}"
            )
            return {
                "status": "blocked",
                "matches": 0,
                "executions": 0,
                "trust_gate": gate_audit,
            }

        matches = 0
        executions = 0
        held = 0

        for campaign in campaigns:
            # Evaluate rule conditions
            result = _evaluate_condition(rule, campaign)

            if result["matched"]:
                matches += 1

                # HOLD is the documented "alert only" band: notifications and
                # labels still go out, platform mutations wait for a PASS.
                if not gate.may_execute and rule.action_type not in ALERT_ONLY_ACTIONS:
                    held += 1
                    db.add(
                        RuleExecution(
                            tenant_id=tenant_id,
                            rule_id=rule.id,
                            campaign_id=campaign.id,
                            executed_at=datetime.now(UTC),
                            triggered=True,
                            condition_result=result["values"],
                            action_result={
                                "action": rule.action_type,
                                "success": False,
                                "held": True,
                                "reason": gate.reason,
                                "trust_gate": gate_audit,
                                "timestamp": datetime.now(UTC).isoformat(),
                            },
                        )
                    )
                    continue

                # Execute rule action
                action_result = _execute_action(rule, campaign, db)
                action_result["trust_gate"] = gate_audit

                # Log execution. Column names follow the RuleExecution model
                # (executed_at / triggered / condition_result); the previous
                # triggered_at / condition_values / action_taken keywords do
                # not exist on it, so this raised TypeError on the first
                # matching campaign - the rules engine could never have
                # completed a run even once the worker consumed its queue.
                execution = RuleExecution(
                    tenant_id=tenant_id,
                    rule_id=rule.id,
                    campaign_id=campaign.id,
                    executed_at=datetime.now(UTC),
                    triggered=True,
                    condition_result=result["values"],
                    action_result=action_result,
                )
                db.add(execution)
                executions += 1

        db.commit()

        if held:
            logger.warning(
                f"Rule {rule_id}: {held} action(s) held by the trust gate: {gate.reason}"
            )

        # Publish event if any actions taken
        if executions > 0:
            publish_event(
                tenant_id,
                "rule_triggered",
                {
                    "rule_id": rule_id,
                    "rule_name": rule.name,
                    "matches": matches,
                    "executions": executions,
                },
            )

        logger.info(
            f"Rule {rule_id}: {matches} matches, {executions} executions, {held} held"
        )
        return {
            "matches": matches,
            "executions": executions,
            "held": held,
            "trust_gate": gate_audit,
        }


@shared_task
@with_distributed_lock(timeout=900)  # 15 minute lock timeout
def evaluate_all_rules():
    """
    Evaluate all active rules across all tenants.
    Scheduled by Celery beat (typically every 15 minutes).

    Uses distributed lock to prevent duplicate execution across workers.
    """
    logger.info("Starting evaluation of all rules")

    with SyncSessionLocal() as db:
        # Get all active rules
        rules = (
            db.execute(
                select(Rule).where(
                    Rule.status == RuleStatus.ACTIVE,
                    Rule.is_deleted == False,
                )
            )
            .scalars()
            .all()
        )

        task_count = 0
        for rule in rules:
            evaluate_rules.delay(rule.tenant_id, rule.id)
            task_count += 1

    logger.info(f"Queued {task_count} rule evaluation tasks")
    return {"tasks_queued": task_count}


def _evaluate_condition(rule: Rule, campaign: Campaign) -> dict[str, Any]:
    """
    Evaluate rule conditions against a campaign.

    Returns:
        Dict with 'matched' bool and 'values' dict of evaluated metrics
    """
    conditions = rule.conditions or []
    values = {}

    # A rule with no conditions matches nothing. Starting from True would make
    # it fire against every campaign of the tenant, which is the opposite of
    # what an unconfigured rule should do.
    if not conditions:
        return {"matched": False, "values": values}

    all_match = True

    for condition in conditions:
        metric = condition.get("metric")
        operator = condition.get("operator")
        threshold = condition.get("value")

        # Get metric value from campaign
        actual = getattr(campaign, metric, None)
        if actual is None:
            all_match = False
            continue

        values[metric] = actual

        # Evaluate condition
        target = _parse_condition_value(threshold, type(actual))

        if operator == "greater_than":
            if not (actual > target):
                all_match = False
        elif operator == "less_than":
            if not (actual < target):
                all_match = False
        elif operator == "equals":
            if actual != target:
                all_match = False
        elif operator == "gte":
            if not (actual >= target):
                all_match = False
        elif operator == "lte" and not (actual <= target):
            all_match = False

    return {"matched": all_match, "values": values}


def _parse_condition_value(value: str, target_type: type) -> Any:
    """Parse condition value to match target type."""
    if target_type == float:
        return float(value)
    elif target_type == int:
        return int(value)
    elif target_type == bool:
        return value.lower() in ("true", "1", "yes")
    return value


# Actions that only annotate or notify. The Trust Engine's DEGRADED band is
# "alert only", so these stay available while the gate HOLDs; everything else
# mutates the campaign on the platform and needs a PASS.
ALERT_ONLY_ACTIONS = frozenset({"apply_label", "send_alert"})


def _execute_action(rule: Rule, campaign: Campaign, db: Session) -> dict[str, Any]:
    """
    Execute rule action on a campaign.

    Returns:
        Dict with action details and result
    """
    action_type = rule.action_type
    action_config = rule.action_config or {}

    result = {
        "action": action_type,
        "success": True,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    try:
        if action_type == "apply_label":
            labels = campaign.labels or []
            new_label = action_config.get("label")
            if new_label and new_label not in labels:
                campaign.labels = labels + [new_label]
                result["label_added"] = new_label

        elif action_type == "pause_campaign":
            campaign.status = "paused"
            result["previous_status"] = campaign.status

        elif action_type == "send_alert":
            # Queue alert notification
            from app.workers.tasks.whatsapp import send_whatsapp_message

            if action_config.get("whatsapp"):
                send_whatsapp_message.delay(
                    tenant_id=campaign.tenant_id,
                    template_name="rule_alert",
                    to_number=action_config.get("phone"),
                    variables={
                        "rule_name": rule.name,
                        "campaign_name": campaign.name,
                    },
                )
            result["alert_sent"] = True

        elif action_type == "adjust_budget":
            adjustment = action_config.get("adjustment_percent", 0)
            if campaign.daily_budget_cents:
                old_budget = campaign.daily_budget_cents
                campaign.daily_budget_cents = int(old_budget * (1 + adjustment / 100))
                result["budget_change"] = {
                    "old": old_budget,
                    "new": campaign.daily_budget_cents,
                }

    except Exception as e:
        result["success"] = False
        result["error"] = str(e)
        logger.error(f"Action execution failed: {e}")

    return result
