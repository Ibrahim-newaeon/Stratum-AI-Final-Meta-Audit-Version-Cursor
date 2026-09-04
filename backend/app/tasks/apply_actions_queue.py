# =============================================================================
# Stratum AI - Apply Actions Queue Task
# =============================================================================
"""
Celery task for processing and applying approved autopilot actions.
Handles safe execution of budget changes, pauses, and other campaign modifications.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Optional

from celery import shared_task
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

from app.autopilot.service import ActionStatus, ActionType
from app.core.config import settings
from app.core.websocket import publish_action_status_update
# app.db.session exports the sessionmaker as AsyncSessionLocal; the old
# `async_session_factory` name never existed there, so this module raised
# ImportError and none of its tasks could be registered or run.
from app.db.session import AsyncSessionLocal as async_session_factory
from app.features.flags import get_autopilot_caps
from app.models.autopilot import EnforcementMode, TenantEnforcementSettings
from app.models.trust_layer import (
    FactActionsQueue,
    FactSignalHealthDaily,
    SignalHealthStatus,
)
from app.stratum.core.signal_health import SignalHealthConfig
from app.stratum.core.trust_gate import GateDecision

logger = logging.getLogger(__name__)


# =============================================================================
# Platform Executors
# =============================================================================


class PlatformExecutor:
    """
    Base class for platform-specific action execution.

    This is an abstract base class - concrete implementations exist in:
    - MetaExecutor
    """

    async def execute_action(
        self,
        action_type: str,
        entity_type: str,
        entity_id: str,
        action_details: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Execute an action on the platform.

        This is an abstract method that must be overridden by subclasses.

        Returns:
            Dict with keys: success, before_value, after_value, platform_response, error

        Raises:
            NotImplementedError: This base class method must be overridden.
        """
        raise NotImplementedError("Subclass must implement execute_action()")


class MetaExecutor(PlatformExecutor):
    """Executor for Meta (Facebook/Instagram) platform actions."""

    async def execute_action(
        self,
        action_type: str,
        entity_type: str,
        entity_id: str,
        action_details: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute action on Meta platform."""
        # In production, this would use the Meta Marketing API
        # For now, simulate successful execution

        logger.info(f"[META] Executing {action_type} on {entity_type} {entity_id}")

        # Simulate API call
        before_value = {"status": "ACTIVE", "daily_budget": 10000}
        after_value = before_value.copy()

        if action_type == ActionType.BUDGET_INCREASE.value:
            amount = action_details.get("amount", 0)
            after_value["daily_budget"] = before_value["daily_budget"] + amount

        elif action_type == ActionType.BUDGET_DECREASE.value:
            amount = action_details.get("amount", 0)
            after_value["daily_budget"] = max(0, before_value["daily_budget"] - amount)

        elif action_type in [
            ActionType.PAUSE_CAMPAIGN.value,
            ActionType.PAUSE_ADSET.value,
            ActionType.PAUSE_CREATIVE.value,
        ]:
            after_value["status"] = "PAUSED"

        elif action_type in [
            ActionType.ENABLE_CAMPAIGN.value,
            ActionType.ENABLE_ADSET.value,
            ActionType.ENABLE_CREATIVE.value,
        ]:
            after_value["status"] = "ACTIVE"

        return {
            "success": True,
            "before_value": before_value,
            "after_value": after_value,
            "platform_response": {"request_id": "meta_123", "status": "success"},
            "error": None,
        }


# Platform executor registry
PLATFORM_EXECUTORS = {
    "meta": MetaExecutor(),
}


# =============================================================================
# Helper Functions
# =============================================================================


# =============================================================================
# Trust Gate
# =============================================================================
#
# The gate between the approved-actions queue and the platform. It FAILS
# CLOSED: absent, stale or unhealthy signal health means the action is not
# executed. See docs/architecture/trust-engine.md - "Never auto-execute when
# signal_health < 70".
#
# Ordering of decisions, worst wins: BLOCK > HOLD > PASS.
_DECISION_SEVERITY: dict[GateDecision, int] = {
    GateDecision.PASS: 0,
    GateDecision.HOLD: 1,
    GateDecision.BLOCK: 2,
}

# Signal health status enum -> gate decision. Independent of the numeric
# score: a row the rollup already flagged CRITICAL can never be a PASS, no
# matter what the component columns add up to.
_STATUS_DECISIONS: dict[SignalHealthStatus, GateDecision] = {
    SignalHealthStatus.OK: GateDecision.PASS,
    SignalHealthStatus.RISK: GateDecision.PASS,
    SignalHealthStatus.DEGRADED: GateDecision.HOLD,
    SignalHealthStatus.CRITICAL: GateDecision.BLOCK,
}


@dataclass(frozen=True)
class SignalHealthGateResult:
    """
    Outcome of a trust gate evaluation, in a form the callers can audit.

    Carries the decision, the human-readable reason and the component inputs
    that produced it, so a held or blocked action can be audit-logged with its
    full explanation as required by CLAUDE.md.
    """

    decision: GateDecision
    reason: str
    score: float | None = None
    health_date: date | None = None
    enforcement_mode: str = EnforcementMode.ADVISORY.value
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def may_execute(self) -> bool:
        """Whether autopilot is allowed to apply the action right now."""
        return self.decision is GateDecision.PASS

    @property
    def should_alert(self) -> bool:
        """Whether the decision warrants an operator alert (HOLD and BLOCK)."""
        return self.decision is not GateDecision.PASS

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialise the decision and its inputs for the audit trail."""
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "signal_health_score": self.score,
            "signal_health_date": self.health_date.isoformat() if self.health_date else None,
            "enforcement_mode": self.enforcement_mode,
            "healthy_threshold": settings.signal_health_healthy_threshold,
            "degraded_threshold": settings.signal_health_degraded_threshold,
            "max_health_age_days": settings.trust_gate_max_health_age_days,
            "channels": self.channels,
        }


def _freshness_component(
    freshness_minutes: int | None, config: SignalHealthConfig
) -> float | None:
    """
    Convert a data-age reading in minutes into a 0-100 freshness component.

    Full marks under ``signal_health_fresh_minutes``, zero at or beyond
    ``signal_health_stale_minutes``, linear in between.

    Args:
        freshness_minutes: Age of the newest data point, or None when unknown
        config: Signal health configuration carrying the fresh/stale bounds

    Returns:
        A 0-100 score, or None when there is no reading to score
    """
    if freshness_minutes is None:
        return None
    age = max(float(freshness_minutes), 0.0)
    if age <= config.fresh_minutes:
        return 100.0
    if age >= config.stale_minutes:
        return 0.0
    span = config.stale_minutes - config.fresh_minutes
    return 100.0 * (1.0 - (age - config.fresh_minutes) / span)


def _score_record(
    record: FactSignalHealthDaily, config: SignalHealthConfig
) -> tuple[float | None, dict[str, float | None]]:
    """
    Compute the 0-100 composite signal health score for one channel row.

    Uses the component weights documented in docs/architecture/trust-engine.md
    (EMQ 40%, freshness 25%, variance 20%, anomalies 15%) over the columns
    ``fact_signal_health_daily`` actually stores. Components that are NULL are
    dropped and the remaining weights renormalised, so a partially populated
    row is scored on what it does contain rather than being penalised for
    columns the rollup never filled.

    Renormalising is only safe while enough of the row is populated to be
    evidence. Every metric column is nullable and ``status`` defaults to OK, so
    without a floor a row carrying nothing but ``api_error_rate=0`` (15% of the
    weight) would renormalise to a perfect 100 and PASS the gate - the same
    "absence of data is health" inversion D3 exists to remove, one level down.
    A row populating less than ``signal_health_min_component_weight`` of the
    total weight is therefore unscorable (None), which BLOCKs.

    Args:
        record: One fact_signal_health_daily row
        config: Signal health configuration (weights and freshness bounds)

    Returns:
        Tuple of (score, component map). The score is None when too little of
        the row is populated to be trusted, which the caller maps to BLOCK.
    """
    components: dict[str, float | None] = {
        "emq": float(record.emq_score) if record.emq_score is not None else None,
        "freshness": _freshness_component(record.freshness_minutes, config),
        "delivery": (
            100.0 - float(record.event_loss_pct) if record.event_loss_pct is not None else None
        ),
        "reliability": (
            100.0 - float(record.api_error_rate) if record.api_error_rate is not None else None
        ),
    }
    weights = {
        "emq": config.emq_weight,
        "freshness": config.freshness_weight,
        "delivery": config.variance_weight,
        "reliability": config.anomaly_weight,
    }

    total_weight = sum(weights[name] for name, value in components.items() if value is not None)
    # Scale the floor by the weights actually in play, so a config whose
    # weights do not sum to exactly 1.0 still means "half the evidence".
    available_weight = sum(weights.values())
    required_weight = settings.signal_health_min_component_weight * available_weight
    if total_weight <= 0 or total_weight < required_weight:
        return None, components

    score = sum(
        min(max(value, 0.0), 100.0) * weights[name]
        for name, value in components.items()
        if value is not None
    )
    return round(score / total_weight, 1), components


def _decision_for_score(score: float | None) -> GateDecision:
    """
    Map a composite signal health score onto a gate decision.

    Thresholds come from configuration, never from literals at the call site:
    >= signal_health_healthy_threshold PASSes, >= signal_health_degraded_threshold
    HOLDs, anything lower BLOCKs. A missing score is not evidence of health, so
    it BLOCKs.

    Args:
        score: Composite 0-100 signal health, or None when it could not be computed

    Returns:
        The corresponding gate decision
    """
    if score is None:
        return GateDecision.BLOCK
    if score >= settings.signal_health_healthy_threshold:
        return GateDecision.PASS
    if score >= settings.signal_health_degraded_threshold:
        return GateDecision.HOLD
    return GateDecision.BLOCK


def _worst(*decisions: GateDecision) -> GateDecision:
    """Return the most restrictive of the given decisions."""
    return max(decisions, key=lambda decision: _DECISION_SEVERITY[decision])


def _apply_enforcement_mode(
    decision: GateDecision, mode: str
) -> tuple[GateDecision, str | None]:
    """
    Let the tenant's enforcement mode tighten - never loosen - the decision.

    Advisory and soft-block keep the documented outcome: autopilot has no human
    at the point of execution, so a soft-blocked action simply stays held.
    Hard-block escalates a HOLD to a BLOCK so nothing below the healthy
    threshold reaches the platform. No mode can turn a HOLD or BLOCK into a
    PASS - that is the fail-open behaviour this gate exists to prevent.

    Args:
        decision: The decision derived from signal health alone
        mode: The tenant's EnforcementMode value

    Returns:
        Tuple of (possibly escalated decision, note explaining the escalation)
    """
    if mode == EnforcementMode.HARD_BLOCK.value and decision is GateDecision.HOLD:
        return GateDecision.BLOCK, "escalated to block by hard_block enforcement mode"
    return decision, None


async def get_tenant_enforcement_mode(db: AsyncSession, tenant_id: int) -> str:
    """
    Read the tenant's configured autopilot enforcement mode.

    Note that ``TenantEnforcementSettings.enforcement_enabled`` is deliberately
    not consulted here: that kill switch governs the budget/ROAS enforcement
    rules, and must not be a bypass for the trust gate.

    Args:
        db: Async database session
        tenant_id: Tenant to look up

    Returns:
        The EnforcementMode value, defaulting to "advisory" when unconfigured
    """
    result = await db.execute(
        select(TenantEnforcementSettings).where(
            TenantEnforcementSettings.tenant_id == tenant_id
        )
    )
    record = result.scalar_one_or_none()
    if record is None or record.default_mode is None:
        return EnforcementMode.ADVISORY.value
    mode = record.default_mode
    return mode.value if isinstance(mode, EnforcementMode) else str(mode)


def evaluate_signal_health(
    records: list[FactSignalHealthDaily],
    health_date: date | None,
    today: date,
    enforcement_mode: str,
    config: SignalHealthConfig | None = None,
) -> SignalHealthGateResult:
    """
    Turn the newest signal health snapshot into a trust gate decision.

    Pure function - no database access - so the decision table is directly
    testable. The rules, in order:

    1. No snapshot at all -> BLOCK. Absence of data is not health.
    2. Snapshot older than ``trust_gate_max_health_age_days`` -> the configured
       stale decision (HOLD by default, never PASS).
    3. Otherwise score every channel row, take the worst decision and the
       lowest score across channels, and let the row's own status enum floor
       the outcome.
    4. Apply the tenant enforcement mode, which may only tighten the result.

    Args:
        records: fact_signal_health_daily rows for the newest available date
        health_date: The date those rows carry, or None when there are none
        today: The current UTC date, used for the staleness check
        enforcement_mode: The tenant's EnforcementMode value
        config: Optional signal health configuration override (weights/bounds)

    Returns:
        A SignalHealthGateResult carrying the decision, reason and inputs
    """
    config = config or SignalHealthConfig()
    max_age = settings.trust_gate_max_health_age_days

    if health_date is None:
        return SignalHealthGateResult(
            decision=GateDecision.BLOCK,
            reason=(
                "No signal health data for this tenant - the trust gate cannot "
                "verify the signals this action would be based on, so it fails closed."
            ),
            enforcement_mode=enforcement_mode,
        )

    age_days = (today - health_date).days
    # A negative age means the row is dated in the future - clock skew or a
    # rollup invoked with a bad target_date. Treating it as "fresher than
    # fresh" would keep the gate open until the calendar caught up, so it is
    # invalid rather than current.
    if age_days < 0 or age_days > max_age:
        stale_decision = (
            GateDecision.BLOCK
            if settings.trust_gate_stale_health_decision == "block"
            else GateDecision.HOLD
        )
        decision, note = _apply_enforcement_mode(stale_decision, enforcement_mode)
        if age_days < 0:
            reason = (
                f"Signal health snapshot is dated {health_date.isoformat()}, "
                f"{-age_days} day(s) in the future; a row that cannot exist yet "
                "does not count as health."
            )
        else:
            reason = (
                f"Signal health snapshot is {age_days} day(s) old "
                f"(max {max_age}); a stale row does not count as health."
            )
        if note:
            reason = f"{reason} Decision {note}."
        return SignalHealthGateResult(
            decision=decision,
            reason=reason,
            health_date=health_date,
            enforcement_mode=enforcement_mode,
        )

    if not records:
        return SignalHealthGateResult(
            decision=GateDecision.BLOCK,
            reason=(
                f"Signal health snapshot dated {health_date.isoformat()} has no "
                "channel rows to evaluate; the gate fails closed."
            ),
            health_date=health_date,
            enforcement_mode=enforcement_mode,
        )

    channels: dict[str, dict[str, Any]] = {}
    decisions: list[GateDecision] = []
    scores: list[float] = []

    for record in records:
        score, components = _score_record(record, config)
        status = record.status
        if isinstance(status, str):
            status = SignalHealthStatus(status)
        status_decision = _STATUS_DECISIONS.get(status, GateDecision.BLOCK)
        channel_decision = _worst(_decision_for_score(score), status_decision)

        decisions.append(channel_decision)
        if score is not None:
            scores.append(score)
        channels[record.platform] = {
            "score": score,
            "status": status.value if isinstance(status, SignalHealthStatus) else str(status),
            "decision": channel_decision.value,
            "components": components,
        }

    base_decision = _worst(*decisions)
    decision, note = _apply_enforcement_mode(base_decision, enforcement_mode)
    lowest = min(scores) if scores else None

    worst_channels = sorted(
        name for name, detail in channels.items() if detail["decision"] != GateDecision.PASS.value
    )
    if decision is GateDecision.PASS:
        reason = (
            f"Signal health {lowest} >= {settings.signal_health_healthy_threshold} "
            f"on every channel ({health_date.isoformat()})."
        )
    else:
        reason = (
            f"Signal health {lowest} below the healthy threshold "
            f"{settings.signal_health_healthy_threshold} on "
            f"{', '.join(worst_channels) or 'one or more channels'} "
            f"({health_date.isoformat()})."
        )
    if note:
        reason = f"{reason} Decision {note}."

    return SignalHealthGateResult(
        decision=decision,
        reason=reason,
        score=lowest,
        health_date=health_date,
        enforcement_mode=enforcement_mode,
        channels=channels,
    )


async def check_signal_health(db: AsyncSession, tenant_id: int) -> SignalHealthGateResult:
    """
    Evaluate the trust gate for a tenant.

    Loads the newest fact_signal_health_daily snapshot for the tenant and
    evaluates it against the documented thresholds and the tenant's
    enforcement mode.

    FAILS CLOSED. The previous implementation returned True ("no data means we
    proceed cautiously") whenever the table held no row for today, which - with
    the table permanently empty - left the gate permanently open. There is no
    input for which this function now returns a PASS without a fresh, healthy
    snapshot behind it.

    Args:
        db: Async database session
        tenant_id: Tenant whose signal health should be evaluated

    Returns:
        A SignalHealthGateResult; callers must check ``may_execute``
    """
    today = datetime.now(UTC).date()

    # Ignore rows dated in the future when picking the newest snapshot: one bad
    # write (clock skew, or a rollup invoked with a future target_date) would
    # otherwise be the max() forever and hide every real current row behind it.
    newest_result = await db.execute(
        select(func.max(FactSignalHealthDaily.date)).where(
            and_(
                FactSignalHealthDaily.tenant_id == tenant_id,
                FactSignalHealthDaily.date <= today,
            )
        )
    )
    health_date = newest_result.scalar_one_or_none()

    # Only the newest snapshot can be current, so load its rows and let the
    # evaluator decide; an older one is handled by the staleness branch without
    # reading rows at all.
    records: list[FactSignalHealthDaily] = []
    if health_date is not None and 0 <= (today - health_date).days <= (
        settings.trust_gate_max_health_age_days
    ):
        rows_result = await db.execute(
            select(FactSignalHealthDaily).where(
                and_(
                    FactSignalHealthDaily.tenant_id == tenant_id,
                    FactSignalHealthDaily.date == health_date,
                )
            )
        )
        records = list(rows_result.scalars().all())

    enforcement_mode = await get_tenant_enforcement_mode(db, tenant_id)

    return evaluate_signal_health(
        records=records,
        health_date=health_date,
        today=today,
        enforcement_mode=enforcement_mode,
    )


def check_signal_health_sync(db: SyncSession, tenant_id: int) -> SignalHealthGateResult:
    """
    Synchronous trust gate, for Celery tasks that use a sync Session.

    Same decision table as :func:`check_signal_health` - it loads the same rows
    and calls the same pure :func:`evaluate_signal_health` - so the thresholds,
    the staleness rule and the enforcement mode cannot drift between the async
    and sync callers. The rules engine (``app.workers.tasks.rules``) runs on a
    sync session and needs the gate before it mutates a campaign.

    Args:
        db: Synchronous database session
        tenant_id: Tenant whose signal health should be evaluated

    Returns:
        A SignalHealthGateResult; callers must check ``may_execute``
    """
    today = datetime.now(UTC).date()

    health_date = db.execute(
        select(func.max(FactSignalHealthDaily.date)).where(
            and_(
                FactSignalHealthDaily.tenant_id == tenant_id,
                FactSignalHealthDaily.date <= today,
            )
        )
    ).scalar_one_or_none()

    records: list[FactSignalHealthDaily] = []
    if health_date is not None and 0 <= (today - health_date).days <= (
        settings.trust_gate_max_health_age_days
    ):
        records = list(
            db.execute(
                select(FactSignalHealthDaily).where(
                    and_(
                        FactSignalHealthDaily.tenant_id == tenant_id,
                        FactSignalHealthDaily.date == health_date,
                    )
                )
            )
            .scalars()
            .all()
        )

    record = db.execute(
        select(TenantEnforcementSettings).where(
            TenantEnforcementSettings.tenant_id == tenant_id
        )
    ).scalar_one_or_none()
    if record is None or record.default_mode is None:
        enforcement_mode = EnforcementMode.ADVISORY.value
    else:
        mode = record.default_mode
        enforcement_mode = mode.value if isinstance(mode, EnforcementMode) else str(mode)

    return evaluate_signal_health(
        records=records,
        health_date=health_date,
        today=today,
        enforcement_mode=enforcement_mode,
    )


async def get_tenant_autopilot_level(db: AsyncSession, tenant_id: int) -> int:
    """Get the autopilot level for a tenant."""
    from app.features.service import get_tenant_features

    features = await get_tenant_features(db, tenant_id)
    return features.get("autopilot_level", 0)


def validate_action_caps(
    action_type: str, action_details: dict[str, Any]
) -> tuple[bool, Optional[str]]:
    """
    Validate that an action doesn't exceed caps.

    Returns:
        Tuple of (is_valid, error_message)
    """
    caps = get_autopilot_caps()

    if action_type in [ActionType.BUDGET_INCREASE.value, ActionType.BUDGET_DECREASE.value]:
        amount = action_details.get("amount", 0)
        percentage = action_details.get("percentage", 0)

        if abs(amount) > caps["max_daily_budget_change"]:
            return False, f"Budget change ${amount} exceeds max ${caps['max_daily_budget_change']}"

        if abs(percentage) > caps["max_budget_pct_change"]:
            return (
                False,
                f"Budget change {percentage}% exceeds max {caps['max_budget_pct_change']}%",
            )

    return True, None


# =============================================================================
# Main Task
# =============================================================================


@shared_task(
    name="tasks.apply_actions_queue",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def apply_actions_queue(self, tenant_id: Optional[int] = None):
    """
    Process and apply approved actions from the queue.

    This task:
    1. Fetches approved actions that haven't been applied yet
    2. Validates signal health before execution
    3. Applies actions to platforms
    4. Records results and audit logs

    Args:
        tenant_id: Optional tenant ID to process (None = all tenants)
    """
    import asyncio

    async def run_apply():
        async with async_session_factory() as db:
            try:
                logger.info(f"Starting action queue processing for tenant_id={tenant_id}")

                # Build query for approved actions
                query = select(FactActionsQueue).where(
                    FactActionsQueue.status == ActionStatus.APPROVED.value
                )

                if tenant_id:
                    query = query.where(FactActionsQueue.tenant_id == tenant_id)

                # Order by creation time
                query = query.order_by(FactActionsQueue.created_at)

                result = await db.execute(query)
                actions = result.scalars().all()

                if not actions:
                    logger.info("No approved actions to process")
                    return {"status": "success", "processed": 0, "failed": 0}

                processed = 0
                failed = 0
                held = 0
                blocked = 0
                gate_cache: dict[int, SignalHealthGateResult] = {}

                for action in actions:
                    try:
                        # Trust gate: fails closed, so an action only proceeds
                        # when a fresh, healthy signal health snapshot says so.
                        if action.tenant_id not in gate_cache:
                            gate_cache[action.tenant_id] = await check_signal_health(
                                db, action.tenant_id
                            )
                        gate = gate_cache[action.tenant_id]

                        if not gate.may_execute:
                            logger.warning(
                                "Trust gate %s for action %s (tenant %s): %s",
                                gate.decision.value,
                                action.id,
                                action.tenant_id,
                                gate.reason,
                            )
                            action.error = f"Trust gate {gate.decision.value}: {gate.reason}"
                            await log_gate_decision_audit(action=action, gate=gate)
                            await publish_action_status_update(
                                tenant_id=action.tenant_id,
                                action_id=str(action.id),
                                status=gate.decision.value,
                            )
                            if gate.decision is GateDecision.BLOCK:
                                blocked += 1
                            else:
                                held += 1
                            continue

                        # Parse action details
                        action_details = (
                            json.loads(action.action_json) if action.action_json else {}
                        )

                        # Validate against caps
                        is_valid, cap_error = validate_action_caps(
                            action.action_type, action_details
                        )
                        if not is_valid:
                            logger.warning(f"Action {action.id} exceeds caps: {cap_error}")
                            action.status = ActionStatus.FAILED.value
                            action.error = cap_error
                            failed += 1
                            continue

                        # Get platform executor
                        executor = PLATFORM_EXECUTORS.get(action.platform)
                        if not executor:
                            logger.error(f"No executor for platform: {action.platform}")
                            action.status = ActionStatus.FAILED.value
                            action.error = f"Unsupported platform: {action.platform}"
                            failed += 1
                            continue

                        # Execute the action
                        exec_result = await executor.execute_action(
                            action_type=action.action_type,
                            entity_type=action.entity_type,
                            entity_id=action.entity_id,
                            action_details=action_details,
                        )

                        if exec_result["success"]:
                            action.status = ActionStatus.APPLIED.value
                            action.applied_at = datetime.now(UTC)
                            action.after_value = json.dumps(exec_result["after_value"])
                            action.platform_response = json.dumps(exec_result["platform_response"])
                            processed += 1

                            logger.info(f"Successfully applied action {action.id}")

                            # Log to audit (in production, write to audit_log table)
                            await log_action_audit(
                                db=db,
                                action=action,
                                result=exec_result,
                                gate=gate,
                            )

                            # Publish WebSocket notification
                            await publish_action_status_update(
                                tenant_id=action.tenant_id,
                                action_id=str(action.id),
                                status="applied",
                                before_value=exec_result.get("before_value"),
                                after_value=exec_result.get("after_value"),
                            )
                        else:
                            action.status = ActionStatus.FAILED.value
                            action.error = exec_result.get("error", "Unknown error")
                            action.platform_response = json.dumps(
                                exec_result.get("platform_response")
                            )
                            failed += 1

                            logger.error(
                                f"Failed to apply action {action.id}: {exec_result.get('error')}"
                            )

                            # Publish WebSocket notification for failure
                            await publish_action_status_update(
                                tenant_id=action.tenant_id,
                                action_id=str(action.id),
                                status="failed",
                            )

                    except Exception as e:
                        logger.error(f"Error processing action {action.id}: {e!s}")
                        action.status = ActionStatus.FAILED.value
                        action.error = str(e)
                        failed += 1

                await db.commit()

                logger.info(
                    "Action queue processing complete: %s applied, %s failed, "
                    "%s held by the trust gate, %s blocked by the trust gate",
                    processed,
                    failed,
                    held,
                    blocked,
                )

                return {
                    "status": "success",
                    "processed": processed,
                    "failed": failed,
                    "held": held,
                    "blocked": blocked,
                }

            except Exception as e:
                logger.error(f"Action queue processing failed: {e!s}")
                await db.rollback()
                raise self.retry(exc=e)

    return asyncio.get_event_loop().run_until_complete(run_apply())


async def log_gate_decision_audit(
    action: FactActionsQueue, gate: SignalHealthGateResult
) -> None:
    """
    Record a held or blocked action in the audit trail.

    Every automation decision has to be explainable, so the entry carries the
    component inputs that produced the decision (per-channel scores, statuses
    and component breakdown), the thresholds they were compared against and
    the tenant's enforcement mode - not just the outcome.

    Args:
        action: The queued action the gate refused to execute
        gate: The trust gate result explaining why
    """
    audit_entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": "trust_gate_decision",
        "tenant_id": action.tenant_id,
        "action_id": str(action.id),
        "action_type": action.action_type,
        "entity_type": action.entity_type,
        "entity_id": action.entity_id,
        "entity_name": action.entity_name,
        "platform": action.platform,
        "approved_by": action.approved_by_user_id,
        "executed": False,
        "trust_gate": gate.to_audit_dict(),
    }

    logger.info(f"AUDIT: {json.dumps(audit_entry, default=str)}")


async def log_action_audit(
    db: AsyncSession,
    action: FactActionsQueue,
    result: dict[str, Any],
    gate: SignalHealthGateResult | None = None,
) -> None:
    """
    Log action execution to audit trail.

    In production, this would write to a dedicated audit_log table.

    Args:
        db: Async database session
        action: The action that was applied
        result: The platform executor result (before/after values, response)
        gate: The trust gate result that permitted the execution, recorded so
            every executed automation carries the signal health it relied on
    """
    audit_entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": "action_applied",
        "executed": True,
        "trust_gate": gate.to_audit_dict() if gate else None,
        "tenant_id": action.tenant_id,
        "action_id": str(action.id),
        "action_type": action.action_type,
        "entity_type": action.entity_type,
        "entity_id": action.entity_id,
        "entity_name": action.entity_name,
        "platform": action.platform,
        "before_value": result.get("before_value"),
        "after_value": result.get("after_value"),
        "approved_by": action.approved_by_user_id,
        "applied_at": action.applied_at.isoformat() if action.applied_at else None,
        "platform_response": result.get("platform_response"),
    }

    logger.info(f"AUDIT: {json.dumps(audit_entry, default=str)}")


# =============================================================================
# Scheduled Task
# =============================================================================


@shared_task(name="tasks.schedule_apply_actions_queue")
def schedule_apply_actions_queue():
    """
    Scheduled task to process action queue.
    Should run every 5 minutes to pick up newly approved actions.
    """
    return apply_actions_queue.delay()


# =============================================================================
# Single Action Execution
# =============================================================================


@shared_task(
    name="tasks.apply_single_action",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def apply_single_action(self, action_id: str, user_id: Optional[int] = None):
    """
    Apply a single action immediately.

    Used for immediate execution when approval is granted.

    Args:
        action_id: UUID of the action to apply
        user_id: ID of user triggering the execution
    """
    import asyncio
    from uuid import UUID

    async def run_single():
        async with async_session_factory() as db:
            try:
                uuid_id = UUID(action_id)

                result = await db.execute(
                    select(FactActionsQueue).where(FactActionsQueue.id == uuid_id)
                )
                action = result.scalar_one_or_none()

                if not action:
                    return {"status": "error", "error": "Action not found"}

                if action.status != ActionStatus.APPROVED.value:
                    return {
                        "status": "error",
                        "error": f"Action status is {action.status}, expected approved",
                    }

                # Trust gate: fails closed, exactly as on the batch path.
                gate = await check_signal_health(db, action.tenant_id)
                if not gate.may_execute:
                    logger.warning(
                        "Trust gate %s for action %s (tenant %s): %s",
                        gate.decision.value,
                        action_id,
                        action.tenant_id,
                        gate.reason,
                    )
                    await log_gate_decision_audit(action=action, gate=gate)
                    await publish_action_status_update(
                        tenant_id=action.tenant_id,
                        action_id=action_id,
                        status=gate.decision.value,
                    )
                    return {
                        "status": "error",
                        "error": f"Trust gate {gate.decision.value}: {gate.reason}",
                        "trust_gate": gate.to_audit_dict(),
                    }

                # Parse and validate
                action_details = json.loads(action.action_json) if action.action_json else {}
                is_valid, cap_error = validate_action_caps(action.action_type, action_details)

                if not is_valid:
                    action.status = ActionStatus.FAILED.value
                    action.error = cap_error
                    await db.commit()
                    return {"status": "error", "error": cap_error}

                # Execute
                executor = PLATFORM_EXECUTORS.get(action.platform)
                if not executor:
                    action.status = ActionStatus.FAILED.value
                    action.error = f"Unsupported platform: {action.platform}"
                    await db.commit()
                    return {"status": "error", "error": action.error}

                exec_result = await executor.execute_action(
                    action_type=action.action_type,
                    entity_type=action.entity_type,
                    entity_id=action.entity_id,
                    action_details=action_details,
                )

                if exec_result["success"]:
                    action.status = ActionStatus.APPLIED.value
                    action.applied_at = datetime.now(UTC)
                    action.applied_by_user_id = user_id
                    action.after_value = json.dumps(exec_result["after_value"])
                    action.platform_response = json.dumps(exec_result["platform_response"])

                    await log_action_audit(db, action, exec_result, gate=gate)
                    await db.commit()

                    # Publish WebSocket notification
                    await publish_action_status_update(
                        tenant_id=action.tenant_id,
                        action_id=action_id,
                        status="applied",
                        before_value=exec_result.get("before_value"),
                        after_value=exec_result.get("after_value"),
                    )

                    return {"status": "success", "action_id": action_id}
                else:
                    action.status = ActionStatus.FAILED.value
                    action.error = exec_result.get("error", "Unknown error")
                    action.platform_response = json.dumps(exec_result.get("platform_response"))
                    await db.commit()

                    # Publish WebSocket notification for failure
                    await publish_action_status_update(
                        tenant_id=action.tenant_id,
                        action_id=action_id,
                        status="failed",
                    )

                    return {"status": "error", "error": action.error}

            except Exception as e:
                logger.error(f"Single action execution failed: {e!s}")
                await db.rollback()
                raise self.retry(exc=e)

    return asyncio.get_event_loop().run_until_complete(run_single())
