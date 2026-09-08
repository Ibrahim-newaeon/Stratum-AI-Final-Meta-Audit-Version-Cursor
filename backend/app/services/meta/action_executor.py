# =============================================================================
# Stratum AI - Meta Autopilot Action Executor
# =============================================================================
"""
The real Meta write path: turn an approved ``fact_actions_queue`` row into a
verified change on a live ad account, or into a recorded refusal.

This module replaces a simulator. The code it supersedes logged a line, made
up ``before_value = {"status": "ACTIVE", "daily_budget": 10000}``, derived an
``after_value`` arithmetically from that invention and returned
``{"success": True, "platform_response": {"request_id": "meta_123"}}`` without
ever contacting Meta - and those invented numbers were written into
``fact_actions_queue`` and the audit log as if they were measurements.

Order of operations for :func:`execute_meta_action`, every step of which can
only *refuse*, never soften an earlier decision:

1. **Feature flag.** ``autopilot_execution_enabled`` is False by default, so
   merging this cannot start spending money. See "Turning it on" below.
2. **Resume.** A row left in ``applying`` by an earlier attempt is reconciled
   rather than re-decided. See "Idempotency" below.
3. **Trust gate.** The caller's ``SignalHealthGateResult`` must permit
   execution. Re-checked here rather than trusted, so no future caller can
   reach the write path around the gate.
4. **Idempotency**, first check: the durable row status.
5. **Enforcement mode.** ``advisory`` proceeds; ``soft_block`` requires a live
   :class:`PendingConfirmationToken` for this tenant, action type and entity;
   ``hard_block`` refuses. The token is *validated* here and *spent* at step
   9, so a confirmation is not burned by an action a guard rail then refuses.
6. **Measured before-state.** The entity is read from Meta. An action whose
   current state cannot be read is refused - never executed against an assumed
   value. The entity's own ``account_id`` then decides which ad account, and
   therefore which currency, its numbers are in.
7. **Guard rails.** Evaluated against the state just read, before any write.
   A violation is a refusal with a reason, never a clamp-and-proceed: quietly
   applying a smaller change than the one that was approved is still applying
   something nobody approved.
8. **Dry run** stops here, having run every check and written nothing.
9. **The write**, preceded by the durable pre-write claim.
10. **Measured after-state.** The entity is read again and compared against the
    intent. A mismatch marks the action failed, with both values recorded.

## Idempotency

Three mechanisms, because no one of them covers the whole window:

* **The queue row's status.** Only ``approved`` proceeds. A row already
  ``applied`` returns :data:`ExecutionStatus.ALREADY_APPLIED` without touching
  Meta. Durable, and the primary defence against a re-delivered Celery
  message - but only once it has been committed, which is why the claim below
  exists.
* **The pre-write claim.** Immediately before the request leaves, the row is
  moved to ``applying`` and the **resolved absolute target** is written to it
  and committed (:func:`_claim_for_write`). This is what makes a *relative*
  change safe. "Cut the budget 20%" resolves against whatever is live, so a
  second attempt after a crash resolves against the already-reduced value and
  the two compound; comparing the entity against a freshly derived intent can
  never notice, because the intent came from the entity. A row found in
  ``applying`` is reconciled against the recorded target
  (:func:`_resume_claimed_attempt`) and the write is never repeated.
* **The observed current state.** If the entity already carries the intent
  before any write, none is issued. This covers the case where somebody or
  something else got there first, and it is recorded as applied with
  ``before == after`` and ``idempotent_no_op`` set, so the audit trail says
  "already in the intended state" rather than claiming a change was made. It
  is sufficient on its own only for status actions, whose target is absolute.

## Ambiguous failures are never retried

A write whose response never arrives may or may not have been applied. The
client raises :class:`MetaWriteAmbiguousError`; this module reconciles by
**re-reading** the entity. If the entity matches the intent, the write landed
and the action is applied. If it does not, the outcome is
:data:`ExecutionStatus.UNKNOWN` and the action is left for an operator. The
write is not repeated in either case.

## Turning it on

Off by default. ``tasks.apply_actions_queue`` is on the beat schedule every 5
minutes, but the master switch below is checked before this module decrypts a
token or consults the trust gate, so a scheduled run on a default deployment
refuses every row with ``EXECUTION_DISABLED`` and issues no Meta request.

To enable, an operator must, in order:

1. Complete Meta App Review for **``ads_management``** and reconnect the
   tenant, so the stored token carries the scope. ``ads_read`` cannot write.
2. Set ``AUTOPILOT_EXECUTION_ENABLED=true``.
3. Leave ``AUTOPILOT_EXECUTION_DRY_RUN=true`` and watch the recorded intended
   changes for a full day.
4. Only then set ``AUTOPILOT_EXECUTION_DRY_RUN=false``.

Every one of those steps is independent, so no single mistake starts spending.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.autopilot.service import ActionStatus, ActionType
from app.core.config import settings
from app.core.logging import get_logger
from app.models.autopilot import (
    EnforcementMode,
    PendingConfirmationToken,
    TenantEnforcementSettings,
)
from app.models.campaign_builder import TenantAdAccount, TenantPlatformConnection
from app.models.trust_layer import FactActionsQueue
from app.services.encryption import decrypt_token
from app.services.meta.insights_client import (
    MetaAPIError,
    MetaRateLimitError,
    MetaTokenError,
)
from app.services.meta.insights_ingestion import (
    META_PLATFORM,
    USABLE_CONNECTION_STATUSES,
    MetaCredentialsError,
    _status_value,
)
from app.services.meta.write_client import (
    MONEY_FIELDS,
    MetaEntityType,
    MetaWriteAmbiguousError,
    MetaWriteClient,
    MetaWriteValidationError,
    UnsupportedCurrencyError,
    major_to_meta_minor,
    meta_currency_offset,
    meta_minor_to_major,
)

logger = get_logger(__name__)


# =============================================================================
# Outcomes
# =============================================================================


class ExecutionStatus(str, Enum):
    """What happened to an action."""

    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    DRY_RUN = "dry_run"
    REFUSED = "refused"
    FAILED = "failed"
    #: The write left this process and its outcome could not be established.
    UNKNOWN = "unknown"


class RefusalCode(str, Enum):
    """
    Machine-readable reasons an action was refused.

    Every value here means "nothing was written to Meta".
    """

    EXECUTION_DISABLED = "execution_disabled"
    TRUST_GATE = "trust_gate"
    ENFORCEMENT_HARD_BLOCK = "enforcement_hard_block"
    ENFORCEMENT_UNCONFIRMED = "enforcement_soft_block_unconfirmed"
    ACTION_NOT_APPROVED = "action_not_approved"
    ACTION_TYPE_NOT_ALLOWED = "action_type_not_allowed"
    ACTION_TYPE_UNKNOWN = "action_type_unknown"
    ENTITY_TYPE_MISMATCH = "entity_type_mismatch"
    NO_CREDENTIALS = "no_credentials"
    READ_FAILED = "read_before_value_failed"
    ACCOUNT_MISMATCH = "entity_in_unknown_ad_account"
    CURRENCY_UNKNOWN = "currency_unknown"
    CURRENCY_MISMATCH = "currency_mismatch"
    BUDGET_LIMITS_UNCONFIGURED = "budget_limits_unconfigured_for_currency"
    IN_FLIGHT_UNRESOLVED = "in_flight_attempt_unresolved"
    AMBIGUOUS_AMOUNT = "ambiguous_amount"
    NO_CHANGE_SPECIFIED = "no_change_specified"
    NO_BUDGET_ON_ENTITY = "no_budget_on_entity"
    LIFETIME_BUDGET_UNSUPPORTED = "lifetime_budget_unsupported"
    CBO_BUDGET_ON_CAMPAIGN = "cbo_budget_on_campaign"
    CAMPAIGN_HAS_NO_BUDGET = "campaign_has_no_budget"
    GUARD_RAIL_SINGLE_CHANGE_PCT = "guard_rail_single_change_pct"
    GUARD_RAIL_CUMULATIVE_CHANGE_PCT = "guard_rail_cumulative_change_pct"
    GUARD_RAIL_BUDGET_FLOOR = "guard_rail_budget_floor"
    GUARD_RAIL_BUDGET_CEILING = "guard_rail_budget_ceiling"
    GUARD_RAIL_DAILY_ACTION_CAP = "guard_rail_daily_action_cap"
    NOT_APPLIED = "not_applied"
    ENTITY_DRIFTED = "entity_drifted"
    NOTHING_TO_REVERT = "nothing_to_revert"


@dataclass(frozen=True)
class ActionOutcome:
    """
    The result of attempting one action, in a form the caller can audit.

    Attributes:
        status: What happened.
        reason: Human-readable explanation. Always populated for anything
            other than a plain success.
        code: Machine-readable refusal code, when the action was refused.
        before_value: The entity's state as *read from Meta* before the
            change, or None when it was never read.
        after_value: The entity's state as read back afterwards, or None.
        intended_changes: The fields and values the action meant to write, in
            Meta API units. Populated even for a refusal or a dry run, so an
            operator can see what would have happened.
        platform_response: Meta's response body plus request metadata.
        idempotent_no_op: True when the entity was already in the intended
            state and no write was issued.
        guard_rails: Every guard rail evaluated, with its inputs and verdict.
    """

    status: ExecutionStatus
    reason: str
    code: RefusalCode | None = None
    before_value: dict[str, Any] | None = None
    after_value: dict[str, Any] | None = None
    intended_changes: dict[str, Any] = field(default_factory=dict)
    platform_response: dict[str, Any] = field(default_factory=dict)
    idempotent_no_op: bool = False
    guard_rails: list[dict[str, Any]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """Whether the queue row should be marked applied."""
        return self.status in (ExecutionStatus.APPLIED, ExecutionStatus.ALREADY_APPLIED)

    @property
    def wrote_to_meta(self) -> bool:
        """Whether a request that could change the account was actually sent."""
        return self.status in (ExecutionStatus.APPLIED, ExecutionStatus.UNKNOWN) and not (
            self.idempotent_no_op
        )

    def to_audit_dict(self) -> dict[str, Any]:
        """Serialise the outcome and its inputs for the audit trail."""
        return {
            "status": self.status.value,
            "reason": self.reason,
            "code": self.code.value if self.code else None,
            "before_value": self.before_value,
            "after_value": self.after_value,
            "intended_changes": self.intended_changes,
            "idempotent_no_op": self.idempotent_no_op,
            "guard_rails": self.guard_rails,
            "platform_response": self.platform_response,
        }


@runtime_checkable
class TrustGateDecision(Protocol):
    """
    The shape of a trust gate result, without importing the task module.

    ``app.tasks.apply_actions_queue`` imports this module, so this module must
    not import it back. The gate result is duck-typed instead.
    """

    @property
    def may_execute(self) -> bool:
        """Whether the gate permits execution right now."""
        ...

    def to_audit_dict(self) -> dict[str, Any]:
        """The gate's decision and inputs, for the audit record."""
        ...


# =============================================================================
# Entity and action mapping
# =============================================================================

#: ``fact_actions_queue.entity_type`` -> Meta node. "creative" is this
#: schema's name for the thing whose delivery is paused, which on Meta is the
#: **ad** (an AdCreative has no status of its own).
ENTITY_TYPES: dict[str, MetaEntityType] = {
    "campaign": MetaEntityType.CAMPAIGN,
    "adset": MetaEntityType.ADSET,
    "ad_set": MetaEntityType.ADSET,
    "creative": MetaEntityType.AD,
    "ad": MetaEntityType.AD,
}

#: Action type -> the Meta node it must target. An action whose queue row names
#: a different entity type is refused rather than reinterpreted.
ACTION_ENTITY: dict[str, MetaEntityType] = {
    ActionType.PAUSE_CAMPAIGN.value: MetaEntityType.CAMPAIGN,
    ActionType.ENABLE_CAMPAIGN.value: MetaEntityType.CAMPAIGN,
    ActionType.PAUSE_ADSET.value: MetaEntityType.ADSET,
    ActionType.ENABLE_ADSET.value: MetaEntityType.ADSET,
    ActionType.PAUSE_CREATIVE.value: MetaEntityType.AD,
    ActionType.ENABLE_CREATIVE.value: MetaEntityType.AD,
    ActionType.BID_INCREASE.value: MetaEntityType.ADSET,
    ActionType.BID_DECREASE.value: MetaEntityType.ADSET,
}

#: Status-setting actions and the status they set.
STATUS_ACTIONS: dict[str, str] = {
    ActionType.PAUSE_CAMPAIGN.value: "PAUSED",
    ActionType.PAUSE_ADSET.value: "PAUSED",
    ActionType.PAUSE_CREATIVE.value: "PAUSED",
    ActionType.ENABLE_CAMPAIGN.value: "ACTIVE",
    ActionType.ENABLE_ADSET.value: "ACTIVE",
    ActionType.ENABLE_CREATIVE.value: "ACTIVE",
}

#: Actions that change a numeric money field, and its direction.
NUMERIC_ACTIONS: dict[str, tuple[str, int]] = {
    ActionType.BUDGET_INCREASE.value: ("daily_budget", 1),
    ActionType.BUDGET_DECREASE.value: ("daily_budget", -1),
    ActionType.BID_INCREASE.value: ("bid_amount", 1),
    ActionType.BID_DECREASE.value: ("bid_amount", -1),
}


# =============================================================================
# Credentials
# =============================================================================


@dataclass(frozen=True)
class MetaWriteCredentials:
    """
    A tenant's Meta credential for the **write** path.

    Distinct from ``insights_ingestion.MetaCredentials``: that one is resolved
    per campaign for an ``ads_read`` pull, this one is resolved per tenant and
    the token it carries has to hold ``ads_management``. Whether it really
    does can only be discovered from Meta - a token without the scope fails
    the write with a permission error, which the client classifies as a
    non-retryable validation error.

    Attributes:
        access_token: Decrypted token. Never logged, never persisted, never
            placed in an outcome or an audit record.
        ad_account_id: Ad account id including the ``act_`` prefix. As
            returned by :func:`resolve_meta_write_credentials` this is only a
            provisional pick - the tenant's oldest enabled account - because
            nothing identifies the right account until the entity has been
            read. :func:`_scope_credentials_to_entity` replaces it with the
            account the entity itself reports before any money is converted.
        stored_currency: The currency recorded on ``tenant_ad_account``, used
            only to cross-check what Meta reports.
        connection_id: ``tenant_platform_connection.id`` the token came from.
    """

    access_token: str
    ad_account_id: str
    stored_currency: str
    connection_id: Any


async def _enabled_ad_accounts(
    db: AsyncSession, tenant_id: int
) -> list[TenantAdAccount]:
    """
    Every enabled Meta ad account this tenant owns, oldest first.

    Args:
        db: Async database session.
        tenant_id: Tenant to look up.

    Returns:
        The enabled accounts, in ``created_at`` order.
    """
    return list(
        (
            await db.execute(
                select(TenantAdAccount)
                .where(
                    TenantAdAccount.tenant_id == tenant_id,
                    TenantAdAccount.platform == META_PLATFORM,
                    TenantAdAccount.is_enabled.is_(True),
                )
                .order_by(TenantAdAccount.created_at)
            )
        )
        .scalars()
        .all()
    )


async def _scope_credentials_to_entity(
    db: AsyncSession,
    tenant_id: int,
    before_payload: dict[str, Any],
    credentials: MetaWriteCredentials,
) -> tuple[MetaWriteCredentials | None, tuple[RefusalCode, str] | None]:
    """
    Re-point a credential at the ad account the entity actually lives in.

    :func:`resolve_meta_write_credentials` picks the tenant's *first* enabled
    ad account, because the access token is stored per connection and there is
    nothing to key an account on before the entity has been read. That is fine
    for a tenant with one account and wrong for an agency with several: the
    currency of the first account would decide the money conversion and the
    floor/ceiling comparison for an entity in a different one. A JPY ad set
    converted with a USD account's offset is out by 100x - the exact class of
    error this module exists to prevent.

    So once the entity has been read, its own reported ``account_id`` decides.
    It must name an enabled ad account this tenant owns; anything else is a
    refusal, not a fallback:

    * no ``account_id`` in the payload - the account cannot be established, so
      neither can the unit of the numbers in it;
    * an ``account_id`` this tenant has no enabled record for - either the
      entity is not theirs or Stratum does not know that account's currency.

    Args:
        db: Async database session.
        tenant_id: Tenant the action belongs to.
        before_payload: The entity as just read from Meta.
        credentials: The tenant-level credential holding the access token.

    Returns:
        Tuple of (credential scoped to the entity's account, refusal) -
        exactly one is set.
    """
    reported = _normalise_account_id(before_payload.get("account_id"))
    if not reported:
        return None, (
            RefusalCode.ACCOUNT_MISMATCH,
            (
                f"{before_payload.get('id') or 'The entity'} reported no account_id, "
                "so which ad account - and therefore which currency - its numbers "
                "are in cannot be established."
            ),
        )

    for account in await _enabled_ad_accounts(db, tenant_id):
        if _normalise_account_id(getattr(account, "platform_account_id", None)) != reported:
            continue
        return (
            MetaWriteCredentials(
                access_token=credentials.access_token,
                ad_account_id=reported,
                stored_currency=str(getattr(account, "currency", "") or "")
                .strip()
                .upper(),
                connection_id=credentials.connection_id,
            ),
            None,
        )

    return None, (
        RefusalCode.ACCOUNT_MISMATCH,
        (
            f"Meta reports {before_payload.get('id') or 'the entity'} belongs to "
            f"{reported}, which is not an enabled Meta ad account of tenant "
            f"{tenant_id}. Refusing to act on an entity whose account - and so "
            "whose currency - Stratum does not know."
        ),
    )


async def resolve_meta_write_credentials(
    db: AsyncSession, tenant_id: int
) -> MetaWriteCredentials:
    """
    Load the tenant's Meta credential for writing.

    The ad account on the returned credential is provisional: it is the
    tenant's oldest enabled Meta account, chosen only so there is something to
    read the currency from if the entity turns out to live there. Every caller
    must narrow it with :func:`_scope_credentials_to_entity` once the entity
    has been read, before converting any money.

    Args:
        db: Async database session.
        tenant_id: Tenant whose connection should be used.

    Returns:
        The resolved credential.

    Raises:
        MetaCredentialsError: no Meta connection, the connection is not
            connected, the token is missing/expired/undecryptable, or no
            enabled ad account is known. ``reason`` names which.
    """
    connection = (
        await db.execute(
            select(TenantPlatformConnection).where(
                TenantPlatformConnection.tenant_id == tenant_id,
                TenantPlatformConnection.platform == META_PLATFORM,
            )
        )
    ).scalar_one_or_none()

    if connection is None:
        raise MetaCredentialsError(
            "no_meta_connection", f"Tenant {tenant_id} has no Meta platform connection"
        )

    status = _status_value(getattr(connection, "status", None))
    if status not in USABLE_CONNECTION_STATUSES:
        raise MetaCredentialsError(
            "connection_not_connected",
            f"Meta connection for tenant {tenant_id} is '{status}', not connected",
        )

    expires_at = getattr(connection, "token_expires_at", None)
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise MetaCredentialsError(
                "token_expired",
                f"Meta access token for tenant {tenant_id} expired at "
                f"{expires_at.isoformat()}",
            )

    if not getattr(connection, "access_token_encrypted", None):
        raise MetaCredentialsError(
            "token_missing",
            f"Meta connection for tenant {tenant_id} stores no access token",
        )

    try:
        access_token = decrypt_token(connection.access_token_encrypted)
    except Exception as exc:
        raise MetaCredentialsError(
            "token_undecryptable",
            f"Meta access token for tenant {tenant_id} could not be decrypted "
            f"({type(exc).__name__})",
        ) from exc

    accounts = await _enabled_ad_accounts(db, tenant_id)
    ad_account = accounts[0] if accounts else None
    if ad_account is None:
        raise MetaCredentialsError(
            "no_ad_account",
            f"Tenant {tenant_id} has no enabled Meta ad account",
        )

    account_id = str(getattr(ad_account, "platform_account_id", "") or "").strip()
    if not account_id:
        raise MetaCredentialsError(
            "no_ad_account", f"Tenant {tenant_id} ad account has no platform id"
        )
    if not account_id.startswith("act_"):
        account_id = f"act_{account_id}"

    return MetaWriteCredentials(
        access_token=access_token,
        ad_account_id=account_id,
        stored_currency=str(getattr(ad_account, "currency", "") or "").strip().upper(),
        connection_id=getattr(connection, "id", None),
    )


# =============================================================================
# Small helpers
# =============================================================================


def _decimal_or_none(value: Any) -> Decimal | None:
    """
    Parse a value into a ``Decimal``, or None when it is not a number.

    ``float`` is accepted only via ``str`` so a JSON-decoded 0.1 does not
    become 0.1000000000000000055511151231257827.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    """Parse a Meta money/bid string into an int, or None when absent."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _guard(name: str, passed: bool, detail: dict[str, Any]) -> dict[str, Any]:
    """Build one guard-rail evaluation record for the audit trail."""
    return {"guard_rail": name, "passed": passed, **detail}


def _utc_day_start() -> datetime:
    """
    Midnight UTC at the start of the current day.

    Both day-scoped guard rails count from here against ``applied_at``, the
    timestamp an action *executed*. They deliberately do not use
    ``fact_actions_queue.date``, which is stamped once when the row is queued
    (``AutopilotService.queue_action``) and never updated: an action approved
    on Monday and executed on Tuesday carries Monday's date, so a date-keyed
    rail would count neither it nor anything else in a backlog drained after
    midnight - and the batch task selects every approved row regardless of
    age. A cap that stops counting is not a cap.
    """
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _normalise_account_id(value: Any) -> str:
    """
    Render an ad account id in Meta's ``act_<id>`` form.

    Args:
        value: An account id with or without the prefix.

    Returns:
        The prefixed form, or an empty string when there was no id.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    return text if text.startswith("act_") else f"act_{text}"


def _refuse(
    code: RefusalCode,
    reason: str,
    *,
    before_value: dict[str, Any] | None = None,
    intended_changes: dict[str, Any] | None = None,
    guard_rails: list[dict[str, Any]] | None = None,
) -> ActionOutcome:
    """Build a refusal outcome. Nothing has been written to Meta."""
    return ActionOutcome(
        status=ExecutionStatus.REFUSED,
        reason=reason,
        code=code,
        before_value=before_value,
        intended_changes=intended_changes or {},
        guard_rails=guard_rails or [],
    )


def _entity_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Reduce a Meta entity payload to the fields worth recording.

    Keeps the flat shape the queue's ``before_value`` / ``after_value``
    columns and the WebSocket consumers already expect.
    """
    keep = (
        "id",
        "name",
        "status",
        "effective_status",
        "daily_budget",
        "lifetime_budget",
        "bid_amount",
        "campaign_id",
        "adset_id",
        "account_id",
    )
    return {key: payload[key] for key in keep if key in payload}


# =============================================================================
# Enforcement mode
# =============================================================================


async def _enforcement_mode(db: AsyncSession, tenant_id: int) -> str:
    """
    Read the tenant's enforcement mode, defaulting to advisory.

    ``TenantEnforcementSettings.enforcement_enabled`` is deliberately not
    consulted: that kill switch governs the budget/ROAS enforcement rules and
    must not become a bypass for this check.

    Args:
        db: Async database session.
        tenant_id: Tenant to look up.

    Returns:
        The ``EnforcementMode`` value.
    """
    record = (
        await db.execute(
            select(TenantEnforcementSettings).where(
                TenantEnforcementSettings.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if record is None or record.default_mode is None:
        return EnforcementMode.ADVISORY.value
    mode = record.default_mode
    return mode.value if isinstance(mode, EnforcementMode) else str(mode)


async def _find_confirmation_token(
    db: AsyncSession,
    tenant_id: int,
    action_type: str,
    entity_id: str,
    token: str | None,
) -> PendingConfirmationToken | None:
    """
    Find a live soft-block confirmation token for this action.

    The token must exist, belong to this tenant, name this action type and
    entity, and not have expired. Finding it does **not** consume it: see
    :func:`_consume_confirmation_token` for why the two are separate.

    Args:
        db: Async database session.
        tenant_id: Tenant the action belongs to.
        action_type: The action being confirmed.
        entity_id: The entity being changed.
        token: The token supplied in the action payload, if any.

    Returns:
        The matching record, or None when there is no live confirmation.
    """
    if not token:
        return None
    record = (
        await db.execute(
            select(PendingConfirmationToken).where(
                PendingConfirmationToken.token == str(token),
                PendingConfirmationToken.tenant_id == tenant_id,
                PendingConfirmationToken.action_type == action_type,
                PendingConfirmationToken.entity_id == str(entity_id),
            )
        )
    ).scalar_one_or_none()
    if record is None:
        return None
    expires_at = record.expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            return None
    return record


async def _consume_confirmation_token(
    db: AsyncSession, record: PendingConfirmationToken
) -> None:
    """
    Spend a confirmation token so it can authorise exactly one write.

    Called immediately before the write, not when the token is validated. A
    confirmation is a human authorising *this change*; if the action is then
    refused by a guard rail, a structural check or the dry-run switch, nothing
    was authorised and the operator should not have to go and confirm again.

    The delete is flushed at once. The session runs with ``autoflush=False``,
    so an unflushed delete is invisible to the next ``SELECT`` in the same
    transaction - two queued rows for the same tenant, action type and entity
    would both find the same token and both write on one confirmation.

    Args:
        db: Async database session.
        record: The token being spent.
    """
    await db.delete(record)
    await db.flush()


# =============================================================================
# Guard rails
# =============================================================================


async def _executed_today(db: AsyncSession, tenant_id: int) -> int:
    """
    Count the actions autopilot has already applied for a tenant today (UTC).

    Counted by ``applied_at`` - when the action executed - not by the row's
    ``date``, which records when it was queued. See :func:`_utc_day_start`.

    Args:
        db: Async database session.
        tenant_id: Tenant to count for.

    Returns:
        The number of rows this tenant applied since midnight UTC.
    """
    result = await db.execute(
        select(func.count())
        .select_from(FactActionsQueue)
        .where(
            FactActionsQueue.tenant_id == tenant_id,
            FactActionsQueue.status == ActionStatus.APPLIED.value,
            FactActionsQueue.applied_at >= _utc_day_start(),
        )
    )
    return int(result.scalar_one() or 0)


async def _day_start_value(
    db: AsyncSession,
    tenant_id: int,
    entity_id: str,
    field_name: str,
    current: int,
) -> int:
    """
    The value this entity's field started the UTC day on.

    Read from the ``before_value`` of the earliest action autopilot applied to
    this entity today. With no such action, the day started on the value that
    is live now. This is what the cumulative guard rail measures against, so
    ten "within 20%" steps cannot walk a budget to triple its morning value.

    Scoped by ``applied_at``, not by the row's queue ``date``: an action
    approved yesterday and executed this morning is part of today's movement.
    See :func:`_utc_day_start`.

    Args:
        db: Async database session.
        tenant_id: Tenant that owns the entity.
        entity_id: Meta entity id.
        field_name: The Meta field being changed.
        current: The value live on Meta right now, used when nothing was
            applied today.

    Returns:
        The day's starting value in Meta API units.
    """
    rows = (
        (
            await db.execute(
                select(FactActionsQueue)
                .where(
                    FactActionsQueue.tenant_id == tenant_id,
                    FactActionsQueue.entity_id == str(entity_id),
                    FactActionsQueue.status == ActionStatus.APPLIED.value,
                    FactActionsQueue.applied_at >= _utc_day_start(),
                )
                .order_by(FactActionsQueue.applied_at, FactActionsQueue.created_at)
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        if not row.before_value:
            continue
        try:
            before = json.loads(row.before_value)
        except (TypeError, ValueError):
            continue
        value = _int_or_none((before or {}).get(field_name))
        if value is not None:
            return value
    return current


def _daily_budget_limits(
    currency: str,
) -> tuple[tuple[Decimal, Decimal] | None, tuple[RefusalCode, str] | None]:
    """
    The absolute daily-budget floor and ceiling for one account currency.

    ``autopilot_min_daily_budget_major`` and ``autopilot_max_daily_budget_major``
    are a single pair of numbers compared against an amount in whatever major
    unit the account uses, so they only travel between currencies of similar
    magnitude. Meta's own offset table says which currencies break that
    assumption: an offset of 1 means the currency has no minor unit at all, so
    an ordinary daily budget is a five-figure number of major units and the
    shipped ceiling of 1000 would refuse every action on the account while the
    floor of 5 would never bind.

    Rather than compare a yen budget against a dollar-shaped limit, a currency
    Meta gives an offset of 1 must have an explicit pair configured in
    ``autopilot_daily_budget_limits_by_currency``; without one, budget actions
    on that account are refused with a reason that names the setting.

    Args:
        currency: Ad account currency as Meta reports it.

    Returns:
        Tuple of (``(floor, ceiling)`` in major units, refusal) - exactly one
        is set.
    """
    override = settings.autopilot_daily_budget_limits_map.get(currency.upper())
    if override is not None:
        return (Decimal(str(override[0])), Decimal(str(override[1]))), None

    if meta_currency_offset(currency) == 1:
        return None, (
            RefusalCode.BUDGET_LIMITS_UNCONFIGURED,
            (
                f"{currency} has no minor unit at Meta, so an ordinary daily "
                f"budget is a far larger number of major units than the default "
                f"floor/ceiling of {settings.autopilot_min_daily_budget_major}/"
                f"{settings.autopilot_max_daily_budget_major} describe. Set a "
                f"'{currency}:<floor>:<ceiling>' entry in "
                "AUTOPILOT_DAILY_BUDGET_LIMITS_BY_CURRENCY before autopilot may "
                "change a budget on this account."
            ),
        )

    return (
        Decimal(str(settings.autopilot_min_daily_budget_major)),
        Decimal(str(settings.autopilot_max_daily_budget_major)),
    ), None


async def _evaluate_guard_rails(
    db: AsyncSession,
    *,
    tenant_id: int,
    entity_id: str,
    field_name: str,
    current: int,
    proposed: int,
    currency: str,
) -> tuple[list[dict[str, Any]], tuple[RefusalCode, str] | None]:
    """
    Evaluate every guard rail against a proposed numeric change.

    All limits come from configuration; none is a literal here. The rails, in
    the order they are recorded:

    * per-tenant daily cap on the number of executed actions
    * maximum percentage change in a single action
    * maximum cumulative percentage change for this entity today
    * absolute floor for a daily budget, in the account's major unit
    * absolute ceiling for a daily budget, in the account's major unit

    The floor and ceiling apply to ``daily_budget`` only: they are expressed
    as a daily amount, and applying a daily floor to a ``bid_amount`` would
    compare two different kinds of number. They are also scale-sensitive, so
    they are preceded by a rail that refuses outright when the account's
    currency has no configured limits it can be compared against - see
    :func:`_daily_budget_limits`.

    Args:
        db: Async database session.
        tenant_id: Tenant the action belongs to.
        entity_id: Meta entity id being changed.
        field_name: The Meta field being changed.
        current: The value live on Meta now, in Meta API units.
        proposed: The value the action would set, in Meta API units.
        currency: Ad account currency, for the major-unit comparisons.

    Returns:
        Tuple of (every evaluation, first violation as (code, reason) or None).
    """
    rails: list[dict[str, Any]] = []
    violation: tuple[RefusalCode, str] | None = None

    def record(
        name: str,
        passed: bool,
        detail: dict[str, Any],
        code: RefusalCode,
        reason: str,
    ) -> None:
        nonlocal violation
        rails.append(_guard(name, passed, detail))
        if not passed and violation is None:
            violation = (code, reason)

    # --- per-tenant daily action cap -------------------------------------
    cap = settings.autopilot_max_executed_actions_per_tenant_per_day
    executed = await _executed_today(db, tenant_id)
    record(
        "daily_action_cap",
        executed < cap,
        {"executed_today": executed, "limit": cap},
        RefusalCode.GUARD_RAIL_DAILY_ACTION_CAP,
        f"Tenant has already executed {executed} action(s) today; the cap is {cap}.",
    )

    # --- single-action percentage change ----------------------------------
    single_limit = settings.autopilot_max_budget_change_pct
    if current > 0:
        single_pct = abs(proposed - current) / current * 100.0
    else:
        # A zero current value has no percentage to measure against, so the
        # rail cannot be satisfied - refuse rather than divide by zero or
        # treat "unmeasurable" as "within limits".
        single_pct = float("inf")
    record(
        "single_change_pct",
        single_pct <= single_limit,
        {
            "current": current,
            "proposed": proposed,
            "change_pct": None if single_pct == float("inf") else round(single_pct, 2),
            "limit_pct": single_limit,
        },
        RefusalCode.GUARD_RAIL_SINGLE_CHANGE_PCT,
        (
            f"Changing {field_name} from {current} to {proposed} is a "
            f"{'unmeasurable' if single_pct == float('inf') else f'{single_pct:.2f}%'} "
            f"change; the limit for one action is {single_limit}%."
        ),
    )

    # --- cumulative percentage change for this entity today ---------------
    cumulative_limit = settings.autopilot_max_cumulative_budget_change_pct
    day_start = await _day_start_value(db, tenant_id, entity_id, field_name, current)
    if day_start > 0:
        cumulative_pct = abs(proposed - day_start) / day_start * 100.0
    else:
        cumulative_pct = float("inf")
    record(
        "cumulative_change_pct",
        cumulative_pct <= cumulative_limit,
        {
            "day_start": day_start,
            "proposed": proposed,
            "change_pct": (
                None if cumulative_pct == float("inf") else round(cumulative_pct, 2)
            ),
            "limit_pct": cumulative_limit,
        },
        RefusalCode.GUARD_RAIL_CUMULATIVE_CHANGE_PCT,
        (
            f"{field_name} started the day at {day_start}; moving it to {proposed} "
            f"is a cumulative "
            f"{'unmeasurable' if cumulative_pct == float('inf') else f'{cumulative_pct:.2f}%'} "
            f"change and the daily limit is {cumulative_limit}%."
        ),
    )

    # --- absolute floor and ceiling (daily budgets only) ------------------
    if field_name == "daily_budget":
        proposed_major = meta_minor_to_major(proposed, currency)
        limits, limits_refusal = _daily_budget_limits(currency)
        if limits_refusal is not None:
            code, reason = limits_refusal
            record(
                "daily_budget_limits_configured",
                False,
                {
                    "currency": currency,
                    "meta_offset": meta_currency_offset(currency),
                    "proposed_major": str(proposed_major),
                },
                code,
                reason,
            )
            return rails, violation
        assert limits is not None
        floor_major, ceiling_major = limits
        record(
            "daily_budget_floor",
            proposed_major >= floor_major,
            {
                "proposed_major": str(proposed_major),
                "floor_major": str(floor_major),
                "currency": currency,
            },
            RefusalCode.GUARD_RAIL_BUDGET_FLOOR,
            (
                f"A daily budget of {proposed_major} {currency} is below the "
                f"configured floor of {floor_major} {currency}."
            ),
        )
        record(
            "daily_budget_ceiling",
            proposed_major <= ceiling_major,
            {
                "proposed_major": str(proposed_major),
                "ceiling_major": str(ceiling_major),
                "currency": currency,
            },
            RefusalCode.GUARD_RAIL_BUDGET_CEILING,
            (
                f"A daily budget of {proposed_major} {currency} is above the "
                f"configured ceiling of {ceiling_major} {currency}."
            ),
        )

    return rails, violation


# =============================================================================
# Intent
# =============================================================================


@dataclass(frozen=True)
class WriteIntent:
    """
    The change an action means to make, resolved against live Meta state.

    Attributes:
        entity_type: The node to update.
        entity_id: Its Meta id.
        changes: Fields to write, already in Meta API units.
        field_name: The numeric field being changed, when there is one.
        current_value: That field's live value, in Meta API units.
        currency: The ad account currency the conversion used.
    """

    entity_type: MetaEntityType
    entity_id: str
    changes: dict[str, Any]
    field_name: str | None = None
    current_value: int | None = None
    currency: str | None = None


def _matches_intent(payload: dict[str, Any], changes: dict[str, Any]) -> bool:
    """
    Whether an entity payload already carries every intended value.

    Args:
        payload: An entity as read from Meta.
        changes: The intended field values, in Meta API units.

    Returns:
        True when every intended field already holds its intended value.
    """
    for name, value in changes.items():
        if name == "status":
            if str(payload.get("status") or "").upper() != str(value).upper():
                return False
        elif name in MONEY_FIELDS:
            if _int_or_none(payload.get(name)) != int(value):
                return False
        elif str(payload.get(name)) != str(value):
            return False
    return True


def _resolve_delta_major(
    action_details: dict[str, Any], current_major: Decimal, currency: str
) -> tuple[Decimal | None, tuple[RefusalCode, str] | None]:
    """
    Work out how much an action wants to move a value, in major units.

    The accepted spellings are deliberately explicit, because this is money:

    * ``percentage``: a percent of the value currently live on Meta.
    * ``amount_major``: an absolute amount in the account's major unit.
    * ``amount`` **with** ``amount_unit`` of ``"major"`` or ``"minor"``.

    A bare ``amount`` is **refused**. That key has no single meaning in this
    codebase - ``validate_action_caps`` compares it against a dollar cap and
    formats it with a ``$``, while the simulator this module replaces added it
    directly to a minor-unit budget - so the two readings differ by 100x for a
    two-decimal currency. Producers must say which they mean.

    The sign is taken from the action type, not from this value, so a
    ``budget_decrease`` carrying a negative amount cannot become an increase.

    Args:
        action_details: The parsed ``action_json`` payload.
        current_major: The live value in major units.
        currency: Ad account currency.

    Returns:
        Tuple of (magnitude in major units, refusal) - exactly one is set.
    """
    percentage = _decimal_or_none(action_details.get("percentage"))
    if percentage is not None:
        return (current_major * abs(percentage) / Decimal(100)), None

    amount_major = _decimal_or_none(action_details.get("amount_major"))
    if amount_major is not None:
        return abs(amount_major), None

    amount = _decimal_or_none(action_details.get("amount"))
    if amount is not None:
        unit = str(action_details.get("amount_unit") or "").strip().lower()
        if unit == "major":
            return abs(amount), None
        if unit == "minor":
            return abs(meta_minor_to_major(int(amount), currency)), None
        return None, (
            RefusalCode.AMBIGUOUS_AMOUNT,
            (
                "The action supplies 'amount' with no 'amount_unit'. That key means "
                "major units to the cap validator and minor units to the executor "
                "this replaced - a 100x difference - so it is refused rather than "
                "guessed. Send 'percentage', 'amount_major', or 'amount' together "
                "with 'amount_unit' of 'major' or 'minor'."
            ),
        )

    return None, (
        RefusalCode.NO_CHANGE_SPECIFIED,
        (
            "The action specifies no change: none of 'percentage', 'amount_major' "
            "or 'amount' is present in its payload."
        ),
    )


async def _account_currency(
    client: MetaWriteClient, credentials: MetaWriteCredentials
) -> tuple[str | None, tuple[RefusalCode, str] | None]:
    """
    Read the ad account currency from Meta and cross-check the stored one.

    The currency decides the money conversion, so it is read from Meta rather
    than trusted from ``tenant_ad_account``, and a disagreement between the
    two is an unresolved ambiguity about a monetary unit - refused, not picked.

    Args:
        client: An open write client.
        credentials: The tenant's resolved credential.

    Returns:
        Tuple of (currency, refusal) - exactly one is set.
    """
    payload = await client.get_entity(
        MetaEntityType.AD_ACCOUNT, credentials.ad_account_id, ["currency"]
    )
    reported = str(payload.get("currency") or "").strip().upper()
    if not reported:
        return None, (
            RefusalCode.CURRENCY_UNKNOWN,
            (
                f"Meta reported no currency for {credentials.ad_account_id}; a "
                "budget cannot be converted without it."
            ),
        )
    stored = credentials.stored_currency
    if stored and stored != reported:
        return None, (
            RefusalCode.CURRENCY_MISMATCH,
            (
                f"Meta reports {credentials.ad_account_id} is in {reported} but "
                f"Stratum has it recorded as {stored}. Refusing to convert a "
                "budget while the unit is in doubt."
            ),
        )
    return reported, None


async def _build_numeric_intent(
    client: MetaWriteClient,
    credentials: MetaWriteCredentials,
    *,
    entity_type: MetaEntityType,
    entity_id: str,
    before: dict[str, Any],
    action_type: str,
    action_details: dict[str, Any],
) -> tuple[WriteIntent | None, tuple[RefusalCode, str] | None]:
    """
    Build the intent for a budget or bid change.

    Also enforces the two structural rules that decide *where* a budget lives:

    * An ad set inside an Advantage campaign budget (CBO) campaign has no
      effective budget of its own - Meta answers ``error_subcode 1885621``,
      "You can only set an ad set budget or a campaign budget". Changing it
      would either fail or move a number nothing spends against, so it is
      refused with the campaign named.
    * A campaign that carries no budget of its own is a non-CBO campaign whose
      budgets live on its ad sets. Setting ``daily_budget`` on it would switch
      the campaign into CBO and override every ad set's budget at once, which
      is far more than the approved action asked for. Refused.

    Only ``daily_budget`` is supported. A ``lifetime_budget`` entity is
    refused: the floor and ceiling guard rails are expressed as daily amounts,
    and comparing a lifetime budget against a daily floor compares two
    different kinds of number.

    Args:
        client: An open write client.
        credentials: The tenant's resolved credential.
        entity_type: The node being changed.
        entity_id: Its Meta id.
        before: The entity as just read from Meta.
        action_type: The queued action type.
        action_details: The parsed ``action_json`` payload.

    Returns:
        Tuple of (intent, refusal) - exactly one is set.
    """
    field_name, direction = NUMERIC_ACTIONS[action_type]

    if field_name == "daily_budget":
        if entity_type is MetaEntityType.ADSET:
            campaign_id = str(before.get("campaign_id") or "").strip()
            if not campaign_id:
                return None, (
                    RefusalCode.NO_BUDGET_ON_ENTITY,
                    (
                        f"Ad set {entity_id} reported no campaign_id, so whether its "
                        "budget is the effective one cannot be established."
                    ),
                )
            campaign = await client.get_entity(
                MetaEntityType.CAMPAIGN,
                campaign_id,
                ["id", "daily_budget", "lifetime_budget"],
            )
            if campaign.get("daily_budget") or campaign.get("lifetime_budget"):
                return None, (
                    RefusalCode.CBO_BUDGET_ON_CAMPAIGN,
                    (
                        f"Campaign {campaign_id} carries the budget (Advantage "
                        f"campaign budget), so ad set {entity_id} has no budget of "
                        "its own to change."
                    ),
                )
        elif entity_type is MetaEntityType.CAMPAIGN and not before.get("daily_budget"):
            if before.get("lifetime_budget"):
                return None, (
                    RefusalCode.LIFETIME_BUDGET_UNSUPPORTED,
                    (
                        f"Campaign {entity_id} is on a lifetime budget; autopilot "
                        "only changes daily budgets, whose floor and ceiling guard "
                        "rails are daily amounts."
                    ),
                )
            return None, (
                RefusalCode.CAMPAIGN_HAS_NO_BUDGET,
                (
                    f"Campaign {entity_id} carries no budget of its own, so its "
                    "budgets live on its ad sets. Setting a campaign daily_budget "
                    "would switch it into Advantage campaign budget and override "
                    "every ad set at once - far more than this action approved."
                ),
            )
        if entity_type is MetaEntityType.ADSET and not before.get("daily_budget"):
            if before.get("lifetime_budget"):
                return None, (
                    RefusalCode.LIFETIME_BUDGET_UNSUPPORTED,
                    (
                        f"Ad set {entity_id} is on a lifetime budget; autopilot only "
                        "changes daily budgets."
                    ),
                )
            return None, (
                RefusalCode.NO_BUDGET_ON_ENTITY,
                f"Ad set {entity_id} reports no daily_budget to change.",
            )

    current = _int_or_none(before.get(field_name))
    if current is None:
        return None, (
            RefusalCode.NO_BUDGET_ON_ENTITY,
            f"{entity_type.value} {entity_id} reports no {field_name} to change.",
        )

    currency, refusal = await _account_currency(client, credentials)
    if refusal is not None:
        return None, refusal
    assert currency is not None

    current_major = meta_minor_to_major(current, currency)
    delta_major, refusal = _resolve_delta_major(action_details, current_major, currency)
    if refusal is not None:
        return None, refusal
    assert delta_major is not None

    proposed_major = current_major + (Decimal(direction) * delta_major)
    if proposed_major <= 0:
        return None, (
            RefusalCode.GUARD_RAIL_BUDGET_FLOOR,
            (
                f"The change would take {field_name} from {current_major} to "
                f"{proposed_major} {currency}, at or below zero."
            ),
        )

    proposed = major_to_meta_minor(proposed_major, currency)
    return (
        WriteIntent(
            entity_type=entity_type,
            entity_id=entity_id,
            changes={field_name: proposed},
            field_name=field_name,
            current_value=current,
            currency=currency,
        ),
        None,
    )


# =============================================================================
# Execution
# =============================================================================


def _default_client_factory(access_token: str) -> MetaWriteClient:
    """Build a production write client for a token."""
    return MetaWriteClient(access_token)


def _is_dry_run(dry_run: bool | None) -> bool:
    """Resolve the effective dry-run mode (explicit argument wins over config)."""
    return settings.autopilot_execution_dry_run if dry_run is None else bool(dry_run)


async def execute_meta_action(
    db: AsyncSession,
    action: FactActionsQueue,
    action_details: dict[str, Any],
    gate: TrustGateDecision | None = None,
    *,
    dry_run: bool | None = None,
    client_factory: Any = None,
) -> ActionOutcome:
    """
    Execute one approved action against Meta, or refuse it with a reason.

    See the module docstring for the full order of operations and the
    idempotency strategy. Nothing in this function can turn a refusal into an
    execution; each step may only stop the action.

    Args:
        db: Async database session (used for enforcement, credentials and the
            guard rails that count history).
        action: The ``fact_actions_queue`` row being executed.
        action_details: The parsed ``action_json`` payload.
        gate: The trust gate result that permitted the action. Re-checked
            here; a missing gate is treated as no permission.
        dry_run: Force dry-run on or off. Defaults to
            ``settings.autopilot_execution_dry_run``.
        client_factory: Callable taking an access token and returning a
            :class:`MetaWriteClient`. Tests inject an ``httpx.MockTransport``
            through this.

    Returns:
        An :class:`ActionOutcome`. The caller writes it to the queue row and
        the audit log. This function commits exactly once, and only when it is
        about to write: the pre-write claim described above. Everything else
        it leaves for the caller to commit.
    """
    factory = client_factory or _default_client_factory
    tenant_id = int(action.tenant_id)
    action_type = str(action.action_type)
    entity_id = str(action.entity_id or "").strip()

    # 1. Master switch. Checked before anything else, so a deployment with
    #    execution off never even decrypts a token.
    if not settings.autopilot_execution_enabled:
        return _refuse(
            RefusalCode.EXECUTION_DISABLED,
            "Autopilot execution is disabled (AUTOPILOT_EXECUTION_ENABLED is "
            "false); no Meta write was attempted.",
        )

    # 2. A row left mid-write by an earlier attempt is reconciled, never
    #    re-decided. Deliberately ahead of the trust gate: this path only
    #    reads, and a gate that has since degraded must not strand a row whose
    #    write may already have landed.
    if str(action.status or "") == ActionStatus.APPLYING.value:
        return await _resume_claimed_attempt(db, action, factory)

    # 3. Trust gate, re-checked rather than trusted.
    if gate is None or not gate.may_execute:
        reason = getattr(gate, "reason", None) if gate is not None else None
        return _refuse(
            RefusalCode.TRUST_GATE,
            f"The trust gate does not permit execution: {reason or 'no gate decision'}",
        )

    # 4. Idempotency, first check: the durable queue row status.
    status = str(action.status or "")
    if status == ActionStatus.APPLIED.value:
        return ActionOutcome(
            status=ExecutionStatus.ALREADY_APPLIED,
            reason=f"Action {action.id} is already marked applied; not applying twice.",
            idempotent_no_op=True,
        )
    if status != ActionStatus.APPROVED.value:
        return _refuse(
            RefusalCode.ACTION_NOT_APPROVED,
            f"Action {action.id} has status {status!r}, not "
            f"{ActionStatus.APPROVED.value!r}.",
        )

    # 5. Action-type allowlist.
    allowed = settings.autopilot_executable_action_types_set
    if action_type not in allowed:
        return _refuse(
            RefusalCode.ACTION_TYPE_NOT_ALLOWED,
            f"{action_type!r} is not in the configured auto-execute allowlist "
            f"({sorted(allowed)}).",
        )
    if action_type not in STATUS_ACTIONS and action_type not in NUMERIC_ACTIONS:
        return _refuse(
            RefusalCode.ACTION_TYPE_UNKNOWN,
            f"{action_type!r} has no Meta write mapping.",
        )

    # 6. The entity the row names must be the one the action type acts on.
    row_entity = ENTITY_TYPES.get(str(action.entity_type or "").strip().lower())
    expected_entity = ACTION_ENTITY.get(action_type)
    if action_type in NUMERIC_ACTIONS and expected_entity is None:
        # budget_* may target a campaign or an ad set; the row decides.
        expected_entity = row_entity
    if row_entity is None or expected_entity is None or row_entity is not expected_entity:
        return _refuse(
            RefusalCode.ENTITY_TYPE_MISMATCH,
            f"Action {action_type!r} targets {getattr(expected_entity, 'value', None)!r} "
            f"but the queue row names entity_type {action.entity_type!r}.",
        )
    if not entity_id:
        return _refuse(
            RefusalCode.ENTITY_TYPE_MISMATCH, "The queue row carries no entity_id."
        )

    # 7. Enforcement mode. A soft-block confirmation is only *validated* here;
    #    it is spent at step 14, immediately before the write, so an action
    #    that a guard rail or a structural check then refuses does not burn
    #    the operator's one-shot confirmation on a change that never happened.
    mode = await _enforcement_mode(db, tenant_id)
    confirmation: PendingConfirmationToken | None = None
    if mode == EnforcementMode.HARD_BLOCK.value:
        return _refuse(
            RefusalCode.ENFORCEMENT_HARD_BLOCK,
            f"Tenant {tenant_id} is in hard_block enforcement mode; automated "
            "changes are prevented at the API.",
        )
    if mode == EnforcementMode.SOFT_BLOCK.value:
        confirmation = await _find_confirmation_token(
            db,
            tenant_id,
            action_type,
            entity_id,
            action_details.get("confirmation_token"),
        )
        if confirmation is None:
            return _refuse(
                RefusalCode.ENFORCEMENT_UNCONFIRMED,
                f"Tenant {tenant_id} is in soft_block enforcement mode and this "
                "action carries no live PendingConfirmationToken for this "
                "action type and entity.",
            )

    # 8. Credentials.
    try:
        credentials = await resolve_meta_write_credentials(db, tenant_id)
    except MetaCredentialsError as exc:
        return _refuse(
            RefusalCode.NO_CREDENTIALS,
            f"No usable Meta credential for tenant {tenant_id}: {exc.message} "
            f"({exc.reason}).",
        )

    client = factory(credentials.access_token)
    try:
        # 9. Measured before-state. An unreadable entity is a refusal.
        try:
            before_payload = await client.get_entity(row_entity, entity_id)
        except (MetaAPIError, ValueError) as exc:
            return _refuse(
                RefusalCode.READ_FAILED,
                f"Could not read {row_entity.value} {entity_id} from Meta, so "
                f"there is no measured before-value to act on: {exc}",
            )
        before = _entity_snapshot(before_payload)

        # 10. The entity's own account decides the currency, not the tenant's
        #     first one. See _scope_credentials_to_entity.
        credentials, account_refusal = await _scope_credentials_to_entity(
            db, tenant_id, before_payload, credentials
        )
        if account_refusal is not None:
            code, reason = account_refusal
            return _refuse(code, reason, before_value=before)
        assert credentials is not None

        # 11. Intent.
        intent: WriteIntent | None
        if action_type in STATUS_ACTIONS:
            intent = WriteIntent(
                entity_type=row_entity,
                entity_id=entity_id,
                changes={"status": STATUS_ACTIONS[action_type]},
            )
        else:
            try:
                intent, refusal = await _build_numeric_intent(
                    client,
                    credentials,
                    entity_type=row_entity,
                    entity_id=entity_id,
                    before=before_payload,
                    action_type=action_type,
                    action_details=action_details,
                )
            except UnsupportedCurrencyError as exc:
                return _refuse(
                    RefusalCode.CURRENCY_UNKNOWN, str(exc), before_value=before
                )
            except (MetaAPIError, ValueError) as exc:
                return _refuse(
                    RefusalCode.READ_FAILED,
                    f"Could not establish the budget context for {entity_id}: {exc}",
                    before_value=before,
                )
            if refusal is not None:
                code, reason = refusal
                return _refuse(code, reason, before_value=before)
        if intent is None:
            # _build_numeric_intent returns exactly one of (intent, refusal);
            # this branch is unreachable and exists so the write path cannot
            # continue on a None intent if that contract is ever broken.
            return _refuse(
                RefusalCode.NO_CHANGE_SPECIFIED,
                f"No change could be resolved for {action_type} on {entity_id}.",
                before_value=before,
            )

        # 12. Guard rails, before any write.
        rails: list[dict[str, Any]] = []
        if intent.field_name is not None and intent.current_value is not None:
            rails, violation = await _evaluate_guard_rails(
                db,
                tenant_id=tenant_id,
                entity_id=entity_id,
                field_name=intent.field_name,
                current=intent.current_value,
                proposed=int(intent.changes[intent.field_name]),
                currency=intent.currency or credentials.stored_currency,
            )
        else:
            # Status changes still honour the per-tenant daily action cap.
            cap = settings.autopilot_max_executed_actions_per_tenant_per_day
            executed = await _executed_today(db, tenant_id)
            rails = [
                _guard(
                    "daily_action_cap",
                    executed < cap,
                    {"executed_today": executed, "limit": cap},
                )
            ]
            violation = (
                None
                if executed < cap
                else (
                    RefusalCode.GUARD_RAIL_DAILY_ACTION_CAP,
                    (
                        f"Tenant has already executed {executed} action(s) today; "
                        f"the cap is {cap}."
                    ),
                )
            )
        if violation is not None:
            code, reason = violation
            return _refuse(
                code,
                reason,
                before_value=before,
                intended_changes=dict(intent.changes),
                guard_rails=rails,
            )

        already_in_state = _matches_intent(before_payload, intent.changes)

        # 13. Dry run: every check has run, nothing is written and - crucially
        #     - nothing is recorded as applied. Evaluated before the
        #     observed-state check below, because a dry run against an entity
        #     that already matches must still report a dry run: marking the
        #     row applied would be a mode that changes state.
        if _is_dry_run(dry_run):
            return ActionOutcome(
                status=ExecutionStatus.DRY_RUN,
                reason=(
                    "Dry run (AUTOPILOT_EXECUTION_DRY_RUN): every check passed and "
                    + (
                        f"{row_entity.value} {entity_id} is already in the intended "
                        f"state {intent.changes}, so nothing would be written."
                        if already_in_state
                        else f"{row_entity.value} {entity_id} would be set to "
                        f"{intent.changes}, but no write endpoint was called."
                    )
                ),
                before_value=before,
                intended_changes=dict(intent.changes),
                idempotent_no_op=already_in_state,
                guard_rails=rails,
            )

        # 14. Idempotency, second check: the observed state already matches.
        if already_in_state:
            return ActionOutcome(
                status=ExecutionStatus.APPLIED,
                reason=(
                    f"{row_entity.value} {entity_id} is already in the intended "
                    "state; no write was issued."
                ),
                before_value=before,
                after_value=before,
                intended_changes=dict(intent.changes),
                idempotent_no_op=True,
                guard_rails=rails,
            )

        # 15. The write.
        request_meta = {
            "entity_type": row_entity.value,
            "entity_id": entity_id,
            "changes": dict(intent.changes),
            "currency": intent.currency,
            "api_version": client.api_version,
        }
        # The durable pre-write claim. Committed before the request leaves, so
        # a crash between the write and the outcome commit finds an `applying`
        # row carrying the absolute target rather than an `approved` row that
        # would recompute the delta against the value this write just set.
        if not await _claim_for_write(db, action, before, intent, request_meta):
            return _refuse(
                RefusalCode.ACTION_NOT_APPROVED,
                f"Action {action.id} is no longer 'approved' - another worker "
                "claimed it between this run reading the row and reaching the "
                "write. Nothing was written.",
                before_value=before,
                intended_changes=dict(intent.changes),
                guard_rails=rails,
            )
        if confirmation is not None:
            # Spent here and nowhere earlier: one human confirmation, one
            # write. After the claim, so a worker that lost the race refuses
            # without spending a confirmation the winner is about to use.
            await _consume_confirmation_token(db, confirmation)
        try:
            response = await client.update_entity(row_entity, entity_id, intent.changes)
        except MetaWriteAmbiguousError as exc:
            # The request left this process. Reconcile by re-reading; never
            # repeat the write.
            return await _reconcile_ambiguous_write(
                client,
                row_entity,
                entity_id,
                intent,
                before,
                rails,
                request_meta,
                str(exc),
            )
        except (MetaWriteValidationError, MetaTokenError, MetaRateLimitError) as exc:
            return ActionOutcome(
                status=ExecutionStatus.FAILED,
                reason=f"Meta rejected the update: {exc}",
                before_value=before,
                intended_changes=dict(intent.changes),
                platform_response={**request_meta, "error": str(exc)},
                guard_rails=rails,
            )
        except MetaAPIError as exc:
            return ActionOutcome(
                status=ExecutionStatus.FAILED,
                reason=f"Meta update failed: {exc}",
                before_value=before,
                intended_changes=dict(intent.changes),
                platform_response={**request_meta, "error": str(exc)},
                guard_rails=rails,
            )

        # 16. Measured after-state.
        return await _verify_after_write(
            client,
            row_entity,
            entity_id,
            intent,
            before,
            rails,
            {**request_meta, "response": response},
        )
    finally:
        await client.aclose()


#: Key under which the pre-write claim is stored in the queue row's
#: ``platform_response`` column. A row in ``applying`` without one cannot be
#: reconciled and is failed rather than guessed at.
CLAIM_KEY = "pre_write_claim"


async def _claim_for_write(
    db: AsyncSession,
    action: FactActionsQueue,
    before: dict[str, Any],
    intent: WriteIntent,
    request_meta: dict[str, Any],
) -> bool:
    """
    Durably record what is about to be written, then commit.

    This is the only commit the executor performs, and it happens for two
    reasons.

    **A relative change cannot be made idempotent by observation.** "Cut the
    budget by 20%" resolves to an absolute target against whatever is live at
    the time, so a second attempt after a crash resolves to a *different*
    target and the two compound - 50000 becomes 40000 becomes 32000, each
    recorded as a verified success. Comparing the entity against a freshly
    derived intent can never detect that, because the intent was derived from
    the entity. Writing the resolved absolute target down before the request
    leaves turns the question from "what should this action do?" into "did
    this exact change land?", which a re-read can answer. The row moves to
    ``ActionStatus.APPLYING``, which the batch task's ``approved``-only query
    does not select, so a stranded row is never re-fired automatically.

    **Two workers must not both write.** The transition is a conditional
    ``UPDATE ... WHERE status = 'approved'`` and must affect exactly one row.
    The batch task takes ``FOR UPDATE SKIP LOCKED``, but it also commits after
    each action - which releases those locks - so a second run started
    mid-batch can legitimately hold the remaining rows. Only this conditional
    transition, committed before any request leaves, makes "I am the one
    executing this row" a fact rather than a hope.

    Args:
        db: Async database session.
        action: The queue row being claimed.
        before: The measured before-state.
        intent: The resolved change, in Meta API units.
        request_meta: Request metadata recorded alongside the claim.

    Returns:
        True when this caller claimed the row. False means somebody else got
        there first and **no write may be issued**.
    """
    claim_payload = json.dumps(
        {
            **request_meta,
            CLAIM_KEY: {
                "claimed_at": datetime.now(UTC).isoformat(),
                "entity_type": intent.entity_type.value,
                "entity_id": intent.entity_id,
                "changes": dict(intent.changes),
                "before_value": before,
            },
        },
        default=str,
    )
    before_payload = json.dumps(before)
    result = await db.execute(
        update(FactActionsQueue)
        .where(
            FactActionsQueue.id == action.id,
            FactActionsQueue.status == ActionStatus.APPROVED.value,
        )
        .values(
            status=ActionStatus.APPLYING.value,
            before_value=before_payload,
            platform_response=claim_payload,
        )
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if (getattr(result, "rowcount", 0) or 0) != 1:
        return False
    # Mirror the committed values onto the in-memory row so the caller's
    # audit record and outcome recording see what the database holds.
    action.status = ActionStatus.APPLYING.value
    action.before_value = before_payload
    action.platform_response = claim_payload
    return True


def _recorded_claim(action: FactActionsQueue) -> dict[str, Any] | None:
    """
    Read back the pre-write claim a previous attempt committed.

    Args:
        action: A queue row in ``applying``.

    Returns:
        The claim, or None when the row carries none that can be parsed.
    """
    try:
        payload = json.loads(action.platform_response) if action.platform_response else {}
    except (TypeError, ValueError):
        return None
    claim = (payload or {}).get(CLAIM_KEY)
    if not isinstance(claim, dict) or not isinstance(claim.get("changes"), dict):
        return None
    return claim


async def _resume_claimed_attempt(
    db: AsyncSession, action: FactActionsQueue, factory: Any
) -> ActionOutcome:
    """
    Establish what happened to an action that claimed the row and then stopped.

    Reads the entity once and compares it against the **recorded absolute
    target**, never against a freshly derived change. Three outcomes:

    * the entity carries the target - the earlier write landed, so the action
      is applied, as a no-op: this attempt wrote nothing;
    * it does not - the write is not repeated. The outcome is UNKNOWN and an
      operator decides, because "the target is not there" cannot distinguish
      "the write never left" from "it landed and somebody has since changed
      it back", and re-issuing on that guess is how a budget gets cut twice;
    * the claim or the entity cannot be read - also UNKNOWN.

    Args:
        db: Async database session.
        action: The queue row found in ``applying``.
        factory: Client factory taking an access token.

    Returns:
        An applied or UNKNOWN outcome. Never a write.
    """
    claim = _recorded_claim(action)
    if claim is None:
        return ActionOutcome(
            status=ExecutionStatus.UNKNOWN,
            code=RefusalCode.IN_FLIGHT_UNRESOLVED,
            reason=(
                f"Action {action.id} is mid-write but carries no readable record "
                "of what it was setting, so whether the change landed cannot be "
                "established. Nothing was written; an operator must reconcile."
            ),
        )

    entity_type = ENTITY_TYPES.get(str(action.entity_type or "").strip().lower())
    entity_id = str(claim.get("entity_id") or action.entity_id or "").strip()
    changes = dict(claim["changes"])
    before = claim.get("before_value") if isinstance(claim.get("before_value"), dict) else None
    if entity_type is None or not entity_id:
        return ActionOutcome(
            status=ExecutionStatus.UNKNOWN,
            code=RefusalCode.IN_FLIGHT_UNRESOLVED,
            reason=(
                f"Action {action.id} is mid-write but names entity_type "
                f"{action.entity_type!r} and entity_id {entity_id!r}, which is "
                "not a Meta node. Nothing was written."
            ),
            before_value=before,
            intended_changes=changes,
        )

    try:
        credentials = await resolve_meta_write_credentials(db, int(action.tenant_id))
    except MetaCredentialsError as exc:
        return ActionOutcome(
            status=ExecutionStatus.UNKNOWN,
            code=RefusalCode.IN_FLIGHT_UNRESOLVED,
            reason=(
                f"Action {action.id} is mid-write and cannot be reconciled: no "
                f"usable Meta credential for tenant {action.tenant_id} "
                f"({exc.reason}). Nothing was written."
            ),
            before_value=before,
            intended_changes=changes,
        )

    client = factory(credentials.access_token)
    try:
        try:
            current_payload = await client.get_entity(entity_type, entity_id)
        except (MetaAPIError, ValueError) as exc:
            return ActionOutcome(
                status=ExecutionStatus.UNKNOWN,
                code=RefusalCode.IN_FLIGHT_UNRESOLVED,
                reason=(
                    f"Action {action.id} is mid-write and {entity_type.value} "
                    f"{entity_id} could not be read back to establish whether the "
                    f"change landed: {exc}. The write was not repeated."
                ),
                before_value=before,
                intended_changes=changes,
            )

        current = _entity_snapshot(current_payload)
        if _matches_intent(current_payload, changes):
            return ActionOutcome(
                status=ExecutionStatus.APPLIED,
                reason=(
                    f"An earlier attempt on action {action.id} reached Meta before "
                    f"it could record the outcome. {entity_type.value} {entity_id} "
                    f"now carries {changes}, so the change landed; this attempt "
                    "wrote nothing."
                ),
                before_value=before,
                after_value=current,
                intended_changes=changes,
                idempotent_no_op=True,
                platform_response={"reconciled_claim": claim},
            )

        return ActionOutcome(
            status=ExecutionStatus.UNKNOWN,
            code=RefusalCode.IN_FLIGHT_UNRESOLVED,
            reason=(
                f"An earlier attempt on action {action.id} claimed the row and "
                f"then stopped. {entity_type.value} {entity_id} shows {current}, "
                f"which does not carry the claimed {changes}. The write was not "
                "repeated - re-deriving it would risk applying the change twice - "
                "so an operator must decide whether to re-approve it."
            ),
            before_value=before,
            after_value=current,
            intended_changes=changes,
            platform_response={"reconciled_claim": claim},
        )
    finally:
        await client.aclose()


async def _verify_after_write(
    client: MetaWriteClient,
    entity_type: MetaEntityType,
    entity_id: str,
    intent: WriteIntent,
    before: dict[str, Any],
    rails: list[dict[str, Any]],
    platform_response: dict[str, Any],
) -> ActionOutcome:
    """
    Re-read the entity and confirm it really carries the intended values.

    Meta answering ``{"success": true}`` is not proof: the after-value written
    to the queue row and the audit log has to be a measurement, and a change
    that did not take effect must be recorded as a failure rather than as a
    success nobody checked.

    Args:
        client: The open write client.
        entity_type: The node that was updated.
        entity_id: Its Meta id.
        intent: What was written.
        before: The measured before-state.
        rails: Guard-rail evaluations, for the audit record.
        platform_response: Request metadata plus Meta's response body.

    Returns:
        An applied outcome when the re-read matches, otherwise a failed one.
    """
    try:
        after_payload = await client.get_entity(entity_type, entity_id)
    except (MetaAPIError, ValueError) as exc:
        return ActionOutcome(
            status=ExecutionStatus.UNKNOWN,
            reason=(
                f"The update to {entity_type.value} {entity_id} was accepted but "
                f"the entity could not be read back to confirm it: {exc}. The "
                "action needs manual reconciliation; the write was not repeated."
            ),
            before_value=before,
            intended_changes=dict(intent.changes),
            platform_response=platform_response,
            guard_rails=rails,
        )

    after = _entity_snapshot(after_payload)
    if not _matches_intent(after_payload, intent.changes):
        return ActionOutcome(
            status=ExecutionStatus.FAILED,
            reason=(
                f"Meta accepted the update but {entity_type.value} {entity_id} "
                f"does not carry the intended values on re-read: wanted "
                f"{intent.changes}, found {after}."
            ),
            before_value=before,
            after_value=after,
            intended_changes=dict(intent.changes),
            platform_response=platform_response,
            guard_rails=rails,
        )

    return ActionOutcome(
        status=ExecutionStatus.APPLIED,
        reason=(
            f"{entity_type.value} {entity_id} updated and verified: "
            f"{intent.changes}."
        ),
        before_value=before,
        after_value=after,
        intended_changes=dict(intent.changes),
        platform_response=platform_response,
        guard_rails=rails,
    )


async def _reconcile_ambiguous_write(
    client: MetaWriteClient,
    entity_type: MetaEntityType,
    entity_id: str,
    intent: WriteIntent,
    before: dict[str, Any],
    rails: list[dict[str, Any]],
    request_meta: dict[str, Any],
    detail: str,
) -> ActionOutcome:
    """
    Establish what happened after a write whose response never arrived.

    Re-reads the entity exactly once. If it carries the intended values the
    write landed and the action is applied; otherwise the outcome is UNKNOWN
    and an operator decides. The write is not repeated in either case - that
    is the whole point of treating this failure mode separately from an
    ordinary error.

    Args:
        client: The open write client.
        entity_type: The node whose state is unknown.
        entity_id: Its Meta id.
        intent: What was being written.
        before: The measured before-state.
        rails: Guard-rail evaluations, for the audit record.
        request_meta: Request metadata for the audit record.
        detail: The ambiguous failure's description.

    Returns:
        An applied or UNKNOWN outcome; never a retry.
    """
    payload = {**request_meta, "ambiguous_failure": detail, "reconciled": True}
    try:
        current = await client.get_entity(entity_type, entity_id)
    except (MetaAPIError, ValueError) as exc:
        return ActionOutcome(
            status=ExecutionStatus.UNKNOWN,
            reason=(
                f"{detail} Re-reading {entity_type.value} {entity_id} to reconcile "
                f"also failed ({exc}), so whether the change landed is unknown. "
                "The write was not repeated."
            ),
            before_value=before,
            intended_changes=dict(intent.changes),
            platform_response=payload,
            guard_rails=rails,
        )

    snapshot = _entity_snapshot(current)
    if _matches_intent(current, intent.changes):
        return ActionOutcome(
            status=ExecutionStatus.APPLIED,
            reason=(
                f"{detail} The re-read shows the change did land, so the action "
                "is applied without repeating the write."
            ),
            before_value=before,
            after_value=snapshot,
            intended_changes=dict(intent.changes),
            platform_response=payload,
            guard_rails=rails,
        )
    return ActionOutcome(
        status=ExecutionStatus.UNKNOWN,
        reason=(
            f"{detail} The re-read shows {snapshot}, which does not carry the "
            f"intended {intent.changes}. The write was not repeated; an "
            "operator must decide whether to re-approve it."
        ),
        before_value=before,
        after_value=snapshot,
        intended_changes=dict(intent.changes),
        platform_response=payload,
        guard_rails=rails,
    )


# =============================================================================
# Reversibility
# =============================================================================


def _revert_changes(
    entity_type: MetaEntityType,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """
    Work out what to write to put an entity back the way it was.

    The fields to restore are derived by diffing the recorded before- and
    after-values, so nothing has to be stored beyond the two snapshots the
    execution already writes. Only fields this client is allowed to write are
    considered; ``effective_status`` and the like are read-only context.

    Args:
        entity_type: The node being reverted.
        before: The recorded pre-change snapshot.
        after: The recorded post-change snapshot.

    Returns:
        The field values to write, in Meta API units.
    """
    from app.services.meta.write_client import WRITABLE_FIELDS

    writable = WRITABLE_FIELDS.get(entity_type, frozenset())
    changes: dict[str, Any] = {}
    for name in writable:
        if name not in before or name not in after:
            continue
        if name in MONEY_FIELDS:
            old, new = _int_or_none(before[name]), _int_or_none(after[name])
            if old is None or new is None or old == new:
                continue
            changes[name] = old
        else:
            old_s, new_s = str(before[name] or ""), str(after[name] or "")
            if not old_s or old_s.upper() == new_s.upper():
                continue
            changes[name] = old_s.upper() if name == "status" else old_s
    return changes


async def revert_meta_action(
    db: AsyncSession,
    action: FactActionsQueue,
    *,
    dry_run: bool | None = None,
    client_factory: Any = None,
) -> ActionOutcome:
    """
    Restore an executed action's recorded ``before_value`` on Meta.

    This is the one-click override the product promises: every automated
    change is reversible. It goes through the same write client, records its
    own audit entry, and refuses in exactly one case that matters -

    **The entity no longer matches the recorded after-value.** Somebody (a
    human in Ads Manager, another tool, Meta itself) has changed it since. The
    recorded before-value is then no longer "what it was before us"; writing
    it would silently discard that person's change. The revert reports the
    drift instead, naming the fields and both values, and leaves the entity
    alone.

    Unlike execution this does **not** consult the trust gate or the budget
    guard rails: undoing an automated change must stay available when signal
    health has degraded - that is often exactly when somebody wants it - and
    restoring a previous value cannot breach a limit the previous value
    already satisfied. It does honour the master switch and dry-run, because
    those govern whether this deployment may talk to Meta at all.

    Args:
        db: Async database session.
        action: The applied ``fact_actions_queue`` row to reverse.
        dry_run: Force dry-run on or off. Defaults to
            ``settings.autopilot_execution_dry_run``.
        client_factory: Callable taking an access token and returning a
            :class:`MetaWriteClient`.

    Returns:
        An :class:`ActionOutcome`. ``before_value`` carries the entity's state
        at the moment of the revert and ``after_value`` its restored state.
    """
    factory = client_factory or _default_client_factory
    tenant_id = int(action.tenant_id)
    entity_id = str(action.entity_id or "").strip()

    if not settings.autopilot_execution_enabled:
        return _refuse(
            RefusalCode.EXECUTION_DISABLED,
            "Autopilot execution is disabled (AUTOPILOT_EXECUTION_ENABLED is "
            "false); no Meta write was attempted.",
        )

    if str(action.status or "") != ActionStatus.APPLIED.value:
        return _refuse(
            RefusalCode.NOT_APPLIED,
            f"Action {action.id} has status {action.status!r}; only an applied "
            "action can be reverted.",
        )

    try:
        recorded_before = json.loads(action.before_value) if action.before_value else None
        recorded_after = json.loads(action.after_value) if action.after_value else None
    except (TypeError, ValueError):
        recorded_before = recorded_after = None
    if not isinstance(recorded_before, dict) or not isinstance(recorded_after, dict):
        return _refuse(
            RefusalCode.NOTHING_TO_REVERT,
            f"Action {action.id} does not carry both a recorded before_value and "
            "after_value, so there is nothing that can be restored.",
        )

    entity_type = ENTITY_TYPES.get(str(action.entity_type or "").strip().lower())
    if entity_type is None or not entity_id:
        return _refuse(
            RefusalCode.ENTITY_TYPE_MISMATCH,
            f"Action {action.id} names entity_type {action.entity_type!r} and "
            f"entity_id {action.entity_id!r}, which is not a Meta node.",
        )

    changes = _revert_changes(entity_type, recorded_before, recorded_after)
    if not changes:
        return _refuse(
            RefusalCode.NOTHING_TO_REVERT,
            f"Action {action.id} recorded no writable difference between its "
            "before and after values.",
        )

    try:
        credentials = await resolve_meta_write_credentials(db, tenant_id)
    except MetaCredentialsError as exc:
        return _refuse(
            RefusalCode.NO_CREDENTIALS,
            f"No usable Meta credential for tenant {tenant_id}: {exc.message} "
            f"({exc.reason}).",
        )

    client = factory(credentials.access_token)
    try:
        try:
            current_payload = await client.get_entity(entity_type, entity_id)
        except (MetaAPIError, ValueError) as exc:
            return _refuse(
                RefusalCode.READ_FAILED,
                f"Could not read {entity_type.value} {entity_id} from Meta, so "
                f"whether it still matches the recorded after-value is unknown: {exc}",
            )
        current = _entity_snapshot(current_payload)

        # The entity must still belong to an ad account this tenant owns. A
        # revert writes recorded absolute values, so no currency conversion is
        # at stake here - but writing to an account the tenant does not own
        # would be, and the check is one comparison.
        _, account_refusal = await _scope_credentials_to_entity(
            db, tenant_id, current_payload, credentials
        )
        if account_refusal is not None:
            code, reason = account_refusal
            return _refuse(code, reason, before_value=current)

        drifted: dict[str, dict[str, Any]] = {}
        for name in changes:
            expected = recorded_after.get(name)
            if name in MONEY_FIELDS:
                same = _int_or_none(current_payload.get(name)) == _int_or_none(expected)
            else:
                same = str(current_payload.get(name) or "").upper() == str(
                    expected or ""
                ).upper()
            if not same:
                drifted[name] = {
                    "expected": expected,
                    "found": current_payload.get(name),
                }
        if drifted:
            return _refuse(
                RefusalCode.ENTITY_DRIFTED,
                f"{entity_type.value} {entity_id} no longer matches the value "
                f"this action left it at ({drifted}). Someone has changed it "
                "since, so restoring the recorded before-value would discard "
                "their change. Nothing was written.",
                before_value=current,
                intended_changes=dict(changes),
            )

        request_meta = {
            "entity_type": entity_type.value,
            "entity_id": entity_id,
            "changes": dict(changes),
            "revert_of_action_id": str(action.id),
            "api_version": client.api_version,
        }

        if _is_dry_run(dry_run):
            return ActionOutcome(
                status=ExecutionStatus.DRY_RUN,
                reason=(
                    "Dry run (AUTOPILOT_EXECUTION_DRY_RUN): the entity still "
                    f"matches its recorded after-value and would be restored to "
                    f"{changes}, but no write endpoint was called."
                ),
                before_value=current,
                intended_changes=dict(changes),
            )

        intent = WriteIntent(
            entity_type=entity_type, entity_id=entity_id, changes=dict(changes)
        )
        try:
            response = await client.update_entity(entity_type, entity_id, changes)
        except MetaWriteAmbiguousError as exc:
            return await _reconcile_ambiguous_write(
                client, entity_type, entity_id, intent, current, [], request_meta, str(exc)
            )
        except (MetaWriteValidationError, MetaTokenError, MetaRateLimitError) as exc:
            return ActionOutcome(
                status=ExecutionStatus.FAILED,
                reason=f"Meta rejected the revert: {exc}",
                before_value=current,
                intended_changes=dict(changes),
                platform_response={**request_meta, "error": str(exc)},
            )
        except MetaAPIError as exc:
            return ActionOutcome(
                status=ExecutionStatus.FAILED,
                reason=f"Revert failed: {exc}",
                before_value=current,
                intended_changes=dict(changes),
                platform_response={**request_meta, "error": str(exc)},
            )

        outcome = await _verify_after_write(
            client,
            entity_type,
            entity_id,
            intent,
            current,
            [],
            {**request_meta, "response": response},
        )
        if outcome.status is ExecutionStatus.APPLIED:
            logger.info(
                "meta_action_reverted",
                tenant_id=tenant_id,
                action_id=str(action.id),
                entity_type=entity_type.value,
                entity_id=entity_id,
                restored=sorted(changes),
            )
        return outcome
    finally:
        await client.aclose()


def revert_audit_entry(
    action: FactActionsQueue, outcome: ActionOutcome, actor_user_id: int | None = None
) -> dict[str, Any]:
    """
    Build the audit record for a revert attempt.

    A revert is an automated change to a live ad account in its own right, so
    it is audited exactly like an execution: what was restored, from what, by
    whom, and whether it succeeded.

    Args:
        action: The action that was reverted.
        outcome: The result of :func:`revert_meta_action`.
        actor_user_id: The user who requested the revert, when there is one.

    Returns:
        A JSON-serialisable audit entry.
    """
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "event": "action_reverted",
        "tenant_id": action.tenant_id,
        "action_id": str(action.id),
        "action_type": action.action_type,
        "entity_type": action.entity_type,
        "entity_id": action.entity_id,
        "entity_name": action.entity_name,
        "platform": action.platform,
        "reverted_by": actor_user_id,
        "executed": outcome.wrote_to_meta,
        "outcome": outcome.to_audit_dict(),
    }
