# =============================================================================
# Stratum AI - Meta Autopilot Write Executor Tests
# =============================================================================
"""
Unit tests for the real Meta write path: ``app.services.meta.write_client``,
``app.services.meta.action_executor`` and the ``MetaExecutor`` that
``app.tasks.apply_actions_queue`` drives.

No network and no database: HTTP goes through ``httpx.MockTransport`` and the
session is an in-memory fake that answers the handful of queries the executor
issues, so every assertion is about behaviour rather than mocking.

These tests exist because the code they cover replaced a **simulator**. That
simulator never contacted Meta: it returned a hardcoded
``before_value = {"status": "ACTIVE", "daily_budget": 10000}``, derived an
``after_value`` arithmetically from that invention, reported
``{"success": True, "platform_response": {"request_id": "meta_123"}}`` and had
those numbers written into ``fact_actions_queue`` and the audit log. Several
tests below name that constant explicitly so the simulator cannot come back.

What is pinned here:

* ``before_value`` is read from the API, never assumed, and an unreadable
  entity refuses instead of writing,
* budgets convert to Meta's API units correctly, including for a zero-decimal
  currency where this schema's ``*_cents`` convention differs by 100x,
* every guard rail refuses with a recorded reason and writes nothing,
* a held or blocked trust gate prevents any HTTP call at all,
* soft-block refuses without a confirmation token and proceeds with one,
* a re-read that does not match the intent marks the action failed,
* an ambiguous network failure is reconciled by re-reading and never retried,
* the same action cannot be applied twice, by row status or by observed state,
* revert restores the before-value and refuses when the entity has drifted,
* dry run and the master switch both perform no write,
* the access token never reaches a log record or an exception message.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest
import structlog
from sqlalchemy import Update

import app.models  # noqa: F401  (registers every mapper so ORM statements compile)
from app.autopilot.service import SAFE_ACTIONS, ActionStatus, ActionType
from app.core.config import settings
from app.models import ConnectionStatus, TenantAdAccount, TenantPlatformConnection
from app.models.autopilot import (
    EnforcementMode,
    PendingConfirmationToken,
    TenantEnforcementSettings,
)
from app.models.trust_layer import FactActionsQueue
from app.services.encryption import encrypt_token
from app.services.meta.action_executor import (
    ActionOutcome,
    ExecutionStatus,
    RefusalCode,
    revert_meta_action,
)
from app.services.meta.write_client import (
    META_CURRENCY_OFFSET,
    META_ZERO_DECIMAL_CURRENCIES,
    WRITABLE_FIELDS,
    WRITABLE_STATUSES,
    MetaEntityType,
    MetaRateLimitError,
    MetaTokenError,
    MetaWriteAmbiguousError,
    MetaWriteClient,
    MetaWriteValidationError,
    UnsupportedCurrencyError,
    hundredths_to_meta_minor,
    major_to_meta_minor,
    meta_minor_to_major,
)
from app.tasks.apply_actions_queue import PLATFORM_EXECUTORS, record_outcome

pytestmark = pytest.mark.unit

TOKEN = "EAAG_super_secret_meta_system_user_token_do_not_leak"
TENANT_ID = 7
AD_ACCOUNT = "act_1234567890"
ADSET_ID = "23847562910380456"
CAMPAIGN_ID = "23847562910380123"
AD_ID = "23847562910380789"

#: The exact value the simulator invented for every entity it never read.
SIMULATOR_BEFORE_VALUE = {"status": "ACTIVE", "daily_budget": 10000}


# =============================================================================
# Fake async session
# =============================================================================


class _ScalarResult:
    """Stand-in for a SQLAlchemy ``ScalarResult``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        """Return every row."""
        return list(self._rows)

    def first(self) -> Any | None:
        """Return the first row, or None."""
        return self._rows[0] if self._rows else None


class _UpdateResult:
    """Stand-in for the result of a DML ``UPDATE``."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _Result:
    """Stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalar_one_or_none(self) -> Any | None:
        """Return the single row, or None."""
        if len(self._rows) > 1:
            raise AssertionError("scalar_one_or_none() got more than one row")
        return self._rows[0] if self._rows else None

    def scalar_one(self) -> Any:
        """Return the single scalar value."""
        return self._rows[0]

    def scalars(self) -> _ScalarResult:
        """Return a scalar result view."""
        return _ScalarResult(self._rows)


def _selected_entity(statement: Any) -> type | None:
    """
    Return the ORM entity a ``select()`` targets, or None for an aggregate.

    ``select(func.count())`` reports no entity even with ``select_from()``, so
    "no entity" is how the fake recognises the daily action count.
    """
    for description in statement.column_descriptions:
        entity = description.get("entity")
        if entity is not None and description.get("expr") is entity:
            return entity
    return None


class FakeAsyncSession:
    """
    In-memory stand-in for ``AsyncSessionLocal()``.

    Answers exactly the queries the executor issues: the platform connection,
    the tenant's enabled ad accounts, its enforcement settings, a pending
    confirmation token, the count of actions applied today, and the applied
    actions for one entity today. Confirmation tokens are really deleted, so
    "a token authorises exactly one write" is exercised rather than mimed.

    The history queries **honour the predicates the executor actually sends** -
    tenant, status and the ``applied_at >= midnight`` bound. A fake that
    ignored them would answer "yes, there is history" for a row applied last
    week, and the two day-scoped guard rails would look like they worked while
    counting rows that no longer belong to today.
    """

    def __init__(
        self,
        *,
        connection: TenantPlatformConnection | None = None,
        ad_account: TenantAdAccount | None = None,
        ad_accounts: list[TenantAdAccount] | None = None,
        enforcement: TenantEnforcementSettings | None = None,
        tokens: list[PendingConfirmationToken] | None = None,
        applied_actions: list[FactActionsQueue] | None = None,
    ) -> None:
        self.connection = connection
        if ad_accounts is not None:
            self.ad_accounts = list(ad_accounts)
        else:
            self.ad_accounts = [ad_account] if ad_account is not None else []
        self.enforcement = enforcement
        self.tokens = list(tokens or [])
        self.applied_actions = list(applied_actions or [])
        self.deleted: list[Any] = []
        self.commits = 0
        self.flushes = 0
        #: Queue rows this session knows about, for the conditional pre-write
        #: claim. Empty for the single-action tests, which fall back to
        #: ``claim_rowcount``.
        self.rows: list[FactActionsQueue] = []
        #: What the conditional claim's UPDATE affects when ``rows`` is empty.
        #: Set to 0 to simulate another worker having claimed the row first.
        self.claim_rowcount = 1
        self.claims: list[Any] = []
        #: Ordered log of session and HTTP events. A ``Recorder`` handed this
        #: list appends its requests to it, which is how a test can assert that
        #: the pre-write claim was committed *before* the POST left.
        self.journal: list[str] = []

    def _history(self, params: dict[str, Any], *, by_entity: bool) -> list[Any]:
        """
        Applied rows matching the binds the executor actually sent.

        Both the ``applied_at >=`` bound and a ``date ==`` bound are honoured,
        so a test can tell the two apart. That is the whole point: the rails
        must scope by when an action *executed*, and a fake that quietly
        ignored whichever predicate it was given would pass either way.
        """
        tenant_id = params.get("tenant_id_1")
        status = params.get("status_1")
        since = params.get("applied_at_1")
        queued_on = params.get("date_1")
        entity_id = params.get("entity_id_1") if by_entity else None
        rows = []
        for row in self.applied_actions:
            if tenant_id is not None and row.tenant_id != tenant_id:
                continue
            if status is not None and row.status != status:
                continue
            if entity_id is not None and row.entity_id != entity_id:
                continue
            if queued_on is not None and row.date != queued_on:
                continue
            if since is not None:
                applied_at = row.applied_at
                if applied_at is None:
                    continue
                if applied_at.tzinfo is None:
                    applied_at = applied_at.replace(tzinfo=UTC)
                if applied_at < since:
                    continue
            rows.append(row)
        return rows

    async def execute(self, statement: Any) -> Any:
        """Answer one of the executor's queries, or apply its claim UPDATE."""
        if isinstance(statement, Update):
            return self._claim(statement)

        entity = _selected_entity(statement)
        params = statement.compile().params

        if entity is TenantPlatformConnection:
            return _Result([self.connection] if self.connection is not None else [])
        if entity is TenantAdAccount:
            return _Result(list(self.ad_accounts))
        if entity is TenantEnforcementSettings:
            return _Result([self.enforcement] if self.enforcement is not None else [])
        if entity is PendingConfirmationToken:
            matches = [
                token
                for token in self.tokens
                if token.token == params.get("token_1")
                and token.tenant_id == params.get("tenant_id_1")
                and token.action_type == params.get("action_type_1")
                and token.entity_id == params.get("entity_id_1")
            ]
            return _Result(matches)
        if entity is FactActionsQueue:
            return _Result(self._history(params, by_entity=True))
        # No entity: the count of actions applied for the tenant today.
        return _Result([len(self._history(params, by_entity=False))])

    def _claim(self, statement: Any) -> "_UpdateResult":
        """
        Apply the executor's conditional pre-write claim.

        The real statement is ``UPDATE ... SET status='applying' WHERE id=:id
        AND status='approved'``, and the executor requires it to affect exactly
        one row. Modelled honestly, so a row another worker already moved on
        answers 0 and no write follows.
        """
        self.claims.append(statement)
        params = statement.compile().params
        required = params.get("status_1")
        for row in self.rows:
            if str(row.id) != str(params.get("id_1")):
                continue
            if row.status != required:
                return _UpdateResult(0)
            row.status = params["status"]
            row.before_value = params["before_value"]
            row.platform_response = params["platform_response"]
            return _UpdateResult(1)
        return _UpdateResult(self.claim_rowcount)

    async def delete(self, obj: Any) -> None:
        """Really remove a row, so a consumed token cannot be reused."""
        self.deleted.append(obj)
        if isinstance(obj, PendingConfirmationToken) and obj in self.tokens:
            self.tokens.remove(obj)

    async def flush(self) -> None:
        """Count flushes."""
        self.flushes += 1
        self.journal.append("flush")

    async def commit(self) -> None:
        """Count commits."""
        self.commits += 1
        self.journal.append("commit")


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def write_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Turn execution on with dry-run off, and pin every guard rail.

    Both master switches default to "no write"; a test that wants a write has
    to say so, which is the same thing an operator has to do.
    """
    monkeypatch.setattr(settings, "meta_graph_api_version", "v23.0")
    monkeypatch.setattr(settings, "meta_write_request_timeout_seconds", 5.0)
    monkeypatch.setattr(settings, "autopilot_execution_enabled", True)
    monkeypatch.setattr(settings, "autopilot_execution_dry_run", False)
    monkeypatch.setattr(settings, "autopilot_max_budget_change_pct", 20.0)
    monkeypatch.setattr(settings, "autopilot_max_cumulative_budget_change_pct", 50.0)
    monkeypatch.setattr(settings, "autopilot_min_daily_budget_major", 5.0)
    monkeypatch.setattr(settings, "autopilot_max_daily_budget_major", 1000.0)
    monkeypatch.setattr(settings, "autopilot_daily_budget_limits_by_currency", "")
    monkeypatch.setattr(
        settings, "autopilot_max_executed_actions_per_tenant_per_day", 10
    )
    monkeypatch.setattr(
        settings,
        "autopilot_executable_action_types",
        "budget_decrease,budget_increase,pause_adset,pause_creative,bid_decrease",
    )


def make_connection(**overrides: Any) -> TenantPlatformConnection:
    """A connected Meta platform connection holding an encrypted token."""
    values: dict[str, Any] = {
        "id": uuid4(),
        "tenant_id": TENANT_ID,
        "platform": "meta",
        "status": ConnectionStatus.CONNECTED.value,
        "access_token_encrypted": encrypt_token(TOKEN),
        "token_expires_at": datetime.now(UTC) + timedelta(days=30),
    }
    values.update(overrides)
    return TenantPlatformConnection(**values)


def make_ad_account(
    currency: str = "USD", platform_account_id: str = AD_ACCOUNT
) -> TenantAdAccount:
    """An enabled Meta ad account in the given currency."""
    return TenantAdAccount(
        id=uuid4(),
        tenant_id=TENANT_ID,
        connection_id=uuid4(),
        platform="meta",
        platform_account_id=platform_account_id,
        name="Stratum Test Account",
        currency=currency,
        is_enabled=True,
    )


def make_action(
    *,
    action_type: str = ActionType.PAUSE_ADSET.value,
    entity_type: str = "adset",
    entity_id: str = ADSET_ID,
    status: str = ActionStatus.APPROVED.value,
    before_value: dict[str, Any] | None = None,
    after_value: dict[str, Any] | None = None,
    action_json: dict[str, Any] | None = None,
    queued_on: Any = None,
    applied_at: datetime | None = None,
) -> FactActionsQueue:
    """
    An approved queue row for the executor to act on.

    ``queued_on`` and ``applied_at`` are separate on purpose: ``date`` is
    stamped when the row is queued and never updated, while ``applied_at``
    records when it executed. The day-scoped guard rails must key on the
    second, and a test can only show that by making them disagree.
    """
    return FactActionsQueue(
        id=uuid4(),
        tenant_id=TENANT_ID,
        date=queued_on or datetime.now(UTC).date(),
        action_type=action_type,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_name="Prospecting | Broad | US",
        platform="meta",
        action_json=json.dumps(action_json or {}),
        before_value=json.dumps(before_value) if before_value else None,
        after_value=json.dumps(after_value) if after_value else None,
        status=status,
        created_at=datetime.now(UTC),
        applied_at=(
            applied_at
            if applied_at is not None
            else (
                datetime.now(UTC) if status == ActionStatus.APPLIED.value else None
            )
        ),
    )


class PassingGate:
    """A trust gate result that permits execution."""

    reason = "Signal health 91.0 >= 70 on every channel."
    may_execute = True

    def to_audit_dict(self) -> dict[str, Any]:
        """The gate payload the audit record carries."""
        return {"decision": "pass", "reason": self.reason}


class BlockingGate(PassingGate):
    """A trust gate result that refuses execution."""

    reason = "No signal health data for this tenant; the gate fails closed."
    may_execute = False


def session(**kwargs: Any) -> FakeAsyncSession:
    """Build a fake session with a connected Meta account by default."""
    kwargs.setdefault("connection", make_connection())
    kwargs.setdefault("ad_account", make_ad_account())
    return FakeAsyncSession(**kwargs)


class Recorder:
    """
    A scripted ``httpx.MockTransport`` that remembers every request.

    ``responses`` maps ``"<METHOD> <path>"`` to either a response or a list of
    responses consumed in order, so a test can make the second read of the
    same entity differ from the first.
    """

    def __init__(
        self, responses: dict[str, Any], journal: list[str] | None = None
    ) -> None:
        self.responses = {
            key: value if isinstance(value, list) else [value]
            for key, value in responses.items()
        }
        self.requests: list[httpx.Request] = []
        #: When a session's journal is handed in, every request is appended to
        #: it so session events and HTTP calls can be ordered against each
        #: other.
        self.journal = journal if journal is not None else []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Serve the next scripted response for this method and path."""
        self.requests.append(request)
        self.journal.append(request.method)
        key = f"{request.method} {request.url.path.rsplit('/', 1)[-1]}"
        queue = self.responses.get(key)
        if not queue:
            raise AssertionError(f"unexpected request: {key}")
        return queue.pop(0) if len(queue) > 1 else queue[0]

    @property
    def methods(self) -> list[str]:
        """Every HTTP method issued, in order."""
        return [request.method for request in self.requests]

    @property
    def writes(self) -> list[httpx.Request]:
        """Every POST issued."""
        return [request for request in self.requests if request.method == "POST"]

    def factory(self, access_token: str) -> MetaWriteClient:
        """Build a client bound to this transport, for ``client_factory``."""
        return MetaWriteClient(
            access_token, transport=httpx.MockTransport(self.__call__)
        )


def ok(payload: dict[str, Any]) -> httpx.Response:
    """A 200 JSON response."""
    return httpx.Response(200, json=payload)


def adset(
    *,
    status: str = "ACTIVE",
    daily_budget: str | None = "50000",
    bid_amount: str | None = None,
    campaign_id: str = CAMPAIGN_ID,
) -> dict[str, Any]:
    """An ad set payload as Meta returns one (every number a string)."""
    payload: dict[str, Any] = {
        "id": ADSET_ID,
        "name": "Broad | US | 25-54",
        "status": status,
        "effective_status": status,
        "campaign_id": campaign_id,
        "account_id": AD_ACCOUNT.removeprefix("act_"),
    }
    if daily_budget is not None:
        payload["daily_budget"] = daily_budget
    if bid_amount is not None:
        payload["bid_amount"] = bid_amount
    return payload


def non_cbo_campaign() -> dict[str, Any]:
    """A campaign whose ad sets hold the budgets (Advantage budget off)."""
    return {"id": CAMPAIGN_ID}


def account(currency: str = "USD") -> dict[str, Any]:
    """An ad account payload carrying its currency."""
    return {"id": AD_ACCOUNT, "currency": currency}


def budget_script(
    *,
    before: dict[str, Any],
    after: dict[str, Any] | None = None,
    currency: str = "USD",
    ad_account: str = AD_ACCOUNT,
    journal: list[str] | None = None,
) -> Recorder:
    """Script the three reads and one write a budget change performs."""
    reads = [ok(before)] if after is None else [ok(before), ok(after)]
    return Recorder(
        {
            f"GET {ADSET_ID}": reads,
            f"GET {CAMPAIGN_ID}": ok(non_cbo_campaign()),
            f"GET {ad_account}": ok(account(currency)),
            f"POST {ADSET_ID}": ok({"success": True}),
        },
        journal,
    )


async def run(
    action: FactActionsQueue,
    recorder: Recorder,
    *,
    db: FakeAsyncSession | None = None,
    details: dict[str, Any] | None = None,
    gate: Any = None,
    dry_run: bool | None = None,
) -> ActionOutcome:
    """
    Execute an action through the registered ``MetaExecutor``.

    Deliberately routed through ``PLATFORM_EXECUTORS`` rather than calling the
    service function directly: that is the seam the simulator occupied, so
    these tests fail if it is ever restored.
    """
    executor = PLATFORM_EXECUTORS["meta"]
    return await executor.execute_action(
        db=db or session(),
        action=action,
        action_details=details or {},
        gate=gate or PassingGate(),
        dry_run=dry_run,
        client_factory=recorder.factory,
    )


# =============================================================================
# Money: the boundary conversion
# =============================================================================


class TestMoneyConversion:
    """Meta API units are not this schema's ``*_cents`` units."""

    def test_two_decimal_currency_uses_hundredths(self):
        """A USD 100.00 daily budget is 10000 to Meta."""
        assert major_to_meta_minor(Decimal("100.00"), "USD") == 10000
        assert meta_minor_to_major(10000, "USD") == Decimal(100)

    def test_zero_decimal_currency_uses_the_basic_unit(self):
        """JPY has no minor unit: 1000 yen is 1000, not 100000."""
        assert major_to_meta_minor(Decimal(1000), "JPY") == 1000
        assert meta_minor_to_major(1000, "JPY") == Decimal(1000)

    def test_cents_columns_differ_from_meta_units_by_100x_for_jpy(self):
        """
        The trap this conversion exists for.

        ``*_cents`` holds hundredths of the MAJOR unit for every currency, so
        100000 means 1000.00 of it. USD wants 100000 at Meta and JPY wants
        1000 - exactly two orders of magnitude apart for the same column.
        """
        assert hundredths_to_meta_minor(100_000, "USD") == 100_000
        assert hundredths_to_meta_minor(100_000, "JPY") == 1_000

    def test_zero_decimal_set_is_metas_not_iso_4217(self):
        """
        HUF, IDR and TWD are two-decimal in ISO 4217 but offset 1 at Meta.

        Using the ISO table on this path would send Meta a number 100x too
        large; using it for BHD/JOD (three-decimal in ISO, offset 100 at Meta)
        would send one 10x too large.
        """
        for code in ("HUF", "IDR", "TWD", "COP", "CRC", "JPY", "KRW"):
            assert code in META_ZERO_DECIMAL_CURRENCIES
        assert META_CURRENCY_OFFSET["BHD"] == 100
        assert META_CURRENCY_OFFSET["JOD"] == 100
        assert 1000 not in set(META_CURRENCY_OFFSET.values())

    def test_unknown_currency_refuses_rather_than_guessing(self):
        """An unknown offset is refused; guessing 100 could be a 100x error."""
        with pytest.raises(UnsupportedCurrencyError):
            major_to_meta_minor(Decimal(10), "ZZZ")
        with pytest.raises(UnsupportedCurrencyError):
            major_to_meta_minor(Decimal(10), None)

    def test_float_is_rejected(self):
        """Money never travels as a float."""
        with pytest.raises(TypeError):
            major_to_meta_minor(10.0, "USD")  # type: ignore[arg-type]

    async def test_budget_change_posts_meta_units_for_a_zero_decimal_currency(
        self, write_settings, monkeypatch
    ):
        """
        The same major-unit decrease produces different wire values per currency.

        A JPY ad set on 10000 yen dropping 2000 yen must POST ``8000``. A USD
        ad set on 10000 dollars dropping 2000 dollars must POST ``800000``.
        """
        # The floor and ceiling are absolute amounts in the account's major
        # unit, so a JPY account needs yen-scale values and a USD account
        # dollar-scale ones. Configured per currency: raising one global
        # ceiling to cover the yen account would raise it for every dollar
        # account on the deployment at the same time.
        monkeypatch.setattr(
            settings,
            "autopilot_daily_budget_limits_by_currency",
            "JPY:500:50000,USD:5:20000",
        )
        jpy = Recorder(
            {
                f"GET {ADSET_ID}": [
                    ok(adset(daily_budget="10000")),
                    ok(adset(daily_budget="8000")),
                ],
                f"GET {CAMPAIGN_ID}": ok(non_cbo_campaign()),
                f"GET {AD_ACCOUNT}": ok(account("JPY")),
                f"POST {ADSET_ID}": ok({"success": True}),
            }
        )
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            jpy,
            db=session(ad_account=make_ad_account("JPY")),
            details={"amount_major": 2000},
        )
        assert outcome.status is ExecutionStatus.APPLIED
        assert jpy.writes[0].content.decode() == "daily_budget=8000"

        usd = Recorder(
            {
                f"GET {ADSET_ID}": [
                    ok(adset(daily_budget="1000000")),
                    ok(adset(daily_budget="800000")),
                ],
                f"GET {CAMPAIGN_ID}": ok(non_cbo_campaign()),
                f"GET {AD_ACCOUNT}": ok(account("USD")),
                f"POST {ADSET_ID}": ok({"success": True}),
            }
        )
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            usd,
            details={"amount_major": 2000},
        )
        assert outcome.status is ExecutionStatus.APPLIED
        assert usd.writes[0].content.decode() == "daily_budget=800000"


# =============================================================================
# before_value is measured
# =============================================================================


class TestMeasuredBeforeValue:
    """The before-value comes from Meta, never from a constant."""

    async def test_before_value_is_read_from_the_api(self, write_settings):
        """
        The recorded before-value is what Meta returned.

        The simulator this replaced returned ``{"status": "ACTIVE",
        "daily_budget": 10000}`` for every entity it never read.
        """
        recorder = Recorder(
            {
                f"GET {ADSET_ID}": [
                    ok(adset(status="ACTIVE", daily_budget="73500")),
                    ok(adset(status="PAUSED", daily_budget="73500")),
                ],
                f"POST {ADSET_ID}": ok({"success": True}),
            }
        )
        outcome = await run(make_action(), recorder)

        assert outcome.status is ExecutionStatus.APPLIED
        assert recorder.methods[0] == "GET", "the entity must be read before it is written"
        assert outcome.before_value["daily_budget"] == "73500"
        assert outcome.before_value["status"] == "ACTIVE"
        assert outcome.before_value != SIMULATOR_BEFORE_VALUE
        assert outcome.after_value["status"] == "PAUSED"

    async def test_unreadable_entity_refuses_and_writes_nothing(self, write_settings):
        """No measured before-value means no action, not an assumed one."""
        recorder = Recorder(
            {
                f"GET {ADSET_ID}": httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": "Unsupported get request",
                            "code": 100,
                            "error_subcode": 33,
                        }
                    },
                )
            }
        )
        outcome = await run(make_action(), recorder)

        assert outcome.status is ExecutionStatus.REFUSED
        assert outcome.code is RefusalCode.READ_FAILED
        assert recorder.writes == []

    async def test_platform_response_is_metas_not_a_fake_request_id(
        self, write_settings
    ):
        """The simulator returned ``{"request_id": "meta_123"}`` for every call."""
        recorder = Recorder(
            {
                f"GET {ADSET_ID}": [ok(adset()), ok(adset(status="PAUSED"))],
                f"POST {ADSET_ID}": ok({"success": True}),
            }
        )
        outcome = await run(make_action(), recorder)
        assert outcome.platform_response["response"] == {"success": True}
        assert "meta_123" not in json.dumps(outcome.platform_response)


# =============================================================================
# Guard rails
# =============================================================================


class TestGuardRails:
    """Every rail refuses with a reason and writes nothing."""

    async def test_action_type_outside_the_allowlist_refuses(
        self, write_settings, monkeypatch
    ):
        """The allowlist starts at SAFE_ACTIONS; pause_campaign is not in it."""
        monkeypatch.setattr(
            settings,
            "autopilot_executable_action_types",
            ",".join(sorted(a.value for a in SAFE_ACTIONS)),
        )
        recorder = Recorder({})
        outcome = await run(
            make_action(
                action_type=ActionType.PAUSE_CAMPAIGN.value,
                entity_type="campaign",
                entity_id=CAMPAIGN_ID,
            ),
            recorder,
        )
        assert outcome.code is RefusalCode.ACTION_TYPE_NOT_ALLOWED
        assert recorder.requests == []

    async def test_single_action_percentage_cap_refuses(self, write_settings):
        """A 50% cut exceeds the 20% single-action limit."""
        recorder = budget_script(before=adset(daily_budget="50000"))
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            recorder,
            details={"percentage": 50},
        )
        assert outcome.code is RefusalCode.GUARD_RAIL_SINGLE_CHANGE_PCT
        assert recorder.writes == []
        assert "20.0%" in outcome.reason
        rail = next(
            r for r in outcome.guard_rails if r["guard_rail"] == "single_change_pct"
        )
        assert rail["passed"] is False
        assert rail["change_pct"] == 50.0

    async def test_cumulative_daily_percentage_cap_refuses(self, write_settings):
        """
        Small steps cannot walk a budget past the daily limit.

        The entity started the day at 50000 (recorded on an action already
        applied today) and is now at 30000. Another 20% cut is within the
        single-action rail but 44% away from where the day started - and the
        cumulative rail is 50%, so a further cut to 20000 (60%) refuses.
        """
        earlier = make_action(
            action_type=ActionType.BUDGET_DECREASE.value,
            status=ActionStatus.APPLIED.value,
            before_value={"daily_budget": "50000"},
            after_value={"daily_budget": "30000"},
        )
        recorder = budget_script(before=adset(daily_budget="24000"))
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            recorder,
            db=session(applied_actions=[earlier]),
            details={"percentage": 15},
        )
        assert outcome.code is RefusalCode.GUARD_RAIL_CUMULATIVE_CHANGE_PCT
        assert recorder.writes == []
        rail = next(
            r for r in outcome.guard_rails if r["guard_rail"] == "cumulative_change_pct"
        )
        assert rail["day_start"] == 50000

    async def test_absolute_floor_refuses(self, write_settings, monkeypatch):
        """A daily budget may not be cut below the configured floor."""
        monkeypatch.setattr(settings, "autopilot_min_daily_budget_major", 100.0)
        recorder = budget_script(before=adset(daily_budget="11000"))
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            recorder,
            details={"percentage": 10},
        )
        assert outcome.code is RefusalCode.GUARD_RAIL_BUDGET_FLOOR
        assert recorder.writes == []
        assert "99 USD" in outcome.reason

    async def test_absolute_ceiling_refuses(self, write_settings, monkeypatch):
        """A daily budget may not be raised above the configured ceiling."""
        monkeypatch.setattr(settings, "autopilot_max_daily_budget_major", 500.0)
        recorder = budget_script(before=adset(daily_budget="45000"))
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_INCREASE.value),
            recorder,
            details={"percentage": 15},
        )
        assert outcome.code is RefusalCode.GUARD_RAIL_BUDGET_CEILING
        assert recorder.writes == []

    async def test_per_tenant_daily_action_cap_refuses(self, write_settings, monkeypatch):
        """The cap counts actions already applied today for the tenant."""
        monkeypatch.setattr(
            settings, "autopilot_max_executed_actions_per_tenant_per_day", 1
        )
        already = make_action(status=ActionStatus.APPLIED.value, entity_id="other")
        recorder = Recorder({f"GET {ADSET_ID}": ok(adset())})
        outcome = await run(
            make_action(), recorder, db=session(applied_actions=[already])
        )
        assert outcome.code is RefusalCode.GUARD_RAIL_DAILY_ACTION_CAP
        assert recorder.writes == []

    async def test_a_violated_rail_is_never_clamped(self, write_settings):
        """A refusal is a refusal; nothing smaller is applied instead."""
        recorder = budget_script(before=adset(daily_budget="50000"))
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            recorder,
            details={"percentage": 90},
        )
        assert outcome.status is ExecutionStatus.REFUSED
        assert recorder.writes == []
        assert outcome.after_value is None

    async def test_ambiguous_amount_is_refused_not_guessed(self, write_settings):
        """A bare ``amount`` means major units to one reader and minor to another."""
        recorder = budget_script(before=adset(daily_budget="50000"))
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            recorder,
            details={"amount": 100},
        )
        assert outcome.code is RefusalCode.AMBIGUOUS_AMOUNT
        assert recorder.writes == []

    async def test_cbo_campaign_budget_blocks_an_adset_budget_change(
        self, write_settings
    ):
        """
        Meta answers 1885621 for this; refuse before sending it.

        With Advantage campaign budget on, the ad set has no budget of its own,
        so changing one would move a number nothing spends against.
        """
        recorder = Recorder(
            {
                f"GET {ADSET_ID}": ok(adset(daily_budget="50000")),
                f"GET {CAMPAIGN_ID}": ok({"id": CAMPAIGN_ID, "daily_budget": "200000"}),
            }
        )
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            recorder,
            details={"percentage": 10},
        )
        assert outcome.code is RefusalCode.CBO_BUDGET_ON_CAMPAIGN
        assert recorder.writes == []

    async def test_currency_mismatch_refuses(self, write_settings):
        """A budget is not converted while its unit is in doubt."""
        recorder = budget_script(before=adset(daily_budget="50000"), currency="JPY")
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            recorder,
            db=session(ad_account=make_ad_account("USD")),
            details={"percentage": 10},
        )
        assert outcome.code is RefusalCode.CURRENCY_MISMATCH
        assert recorder.writes == []

    async def test_the_daily_cap_counts_by_execution_time_not_queue_date(
        self, write_settings, monkeypatch
    ):
        """
        An action queued yesterday and executed today still counts.

        ``fact_actions_queue.date`` is stamped once, when the row is queued,
        and never updated; the batch task selects every approved row with no
        date filter at all. Approve-today/apply-tomorrow and a backlog drained
        after midnight UTC are both ordinary, so a cap keyed on ``date`` would
        simply stop counting for them - the tenant's whole backlog would
        execute while the counter read zero.
        """
        monkeypatch.setattr(
            settings, "autopilot_max_executed_actions_per_tenant_per_day", 1
        )
        yesterdays_row_applied_today = make_action(
            status=ActionStatus.APPLIED.value,
            entity_id="other",
            queued_on=(datetime.now(UTC) - timedelta(days=1)).date(),
            applied_at=datetime.now(UTC),
        )
        recorder = Recorder({f"GET {ADSET_ID}": ok(adset())})
        outcome = await run(
            make_action(),
            recorder,
            db=session(applied_actions=[yesterdays_row_applied_today]),
        )
        assert outcome.code is RefusalCode.GUARD_RAIL_DAILY_ACTION_CAP
        assert recorder.writes == []

    async def test_the_daily_cap_does_not_count_yesterdays_executions(
        self, write_settings, monkeypatch
    ):
        """The cap is a daily one, so it resets at midnight UTC."""
        monkeypatch.setattr(
            settings, "autopilot_max_executed_actions_per_tenant_per_day", 1
        )
        executed_yesterday = make_action(
            status=ActionStatus.APPLIED.value,
            entity_id="other",
            applied_at=datetime.now(UTC) - timedelta(days=1, hours=2),
        )
        recorder = Recorder(
            {
                f"GET {ADSET_ID}": [ok(adset()), ok(adset(status="PAUSED"))],
                f"POST {ADSET_ID}": ok({"success": True}),
            }
        )
        outcome = await run(
            make_action(), recorder, db=session(applied_actions=[executed_yesterday])
        )
        assert outcome.status is ExecutionStatus.APPLIED
        rail = next(
            r for r in outcome.guard_rails if r["guard_rail"] == "daily_action_cap"
        )
        assert rail["executed_today"] == 0

    async def test_cumulative_rail_sees_an_action_queued_on_an_earlier_day(
        self, write_settings
    ):
        """
        The day's opening value comes from what executed today, whenever queued.

        Same scenario as the cumulative test above, except the earlier action
        was queued yesterday and applied this morning. Keyed on ``date`` the
        rail would find no history, fall back to the value live now, and
        collapse into the 20% single-action rail - the exact compounding it
        exists to stop.
        """
        earlier = make_action(
            action_type=ActionType.BUDGET_DECREASE.value,
            status=ActionStatus.APPLIED.value,
            before_value={"daily_budget": "50000"},
            after_value={"daily_budget": "30000"},
            queued_on=(datetime.now(UTC) - timedelta(days=1)).date(),
            applied_at=datetime.now(UTC),
        )
        recorder = budget_script(before=adset(daily_budget="24000"))
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            recorder,
            db=session(applied_actions=[earlier]),
            details={"percentage": 15},
        )
        assert outcome.code is RefusalCode.GUARD_RAIL_CUMULATIVE_CHANGE_PCT
        assert recorder.writes == []
        rail = next(
            r for r in outcome.guard_rails if r["guard_rail"] == "cumulative_change_pct"
        )
        assert rail["day_start"] == 50000

    async def test_a_zero_decimal_currency_needs_its_own_limits(self, write_settings):
        """
        The floor and ceiling are scale-sensitive, so JPY refuses until set.

        50,000 yen (about USD 330) is an ordinary daily budget and a five-figure
        number of major units. Compared against the shipped ceiling of 1000 -
        sized for dollars - every JPY action would be refused as "above the
        ceiling", and the floor of 5 would never bind. Rather than let one
        scalar be silently wrong for a whole class of accounts, the executor
        names the setting an operator has to supply.
        """
        recorder = budget_script(before=adset(daily_budget="50000"), currency="JPY")
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            recorder,
            db=session(ad_account=make_ad_account("JPY")),
            details={"percentage": 10},
        )
        assert outcome.code is RefusalCode.BUDGET_LIMITS_UNCONFIGURED
        assert recorder.writes == []
        assert "AUTOPILOT_DAILY_BUDGET_LIMITS_BY_CURRENCY" in outcome.reason
        rail = next(
            r
            for r in outcome.guard_rails
            if r["guard_rail"] == "daily_budget_limits_configured"
        )
        assert rail["passed"] is False
        assert rail["meta_offset"] == 1

    async def test_a_per_currency_limit_does_not_move_another_currency(
        self, write_settings, monkeypatch
    ):
        """Raising the yen ceiling must not raise the dollar one."""
        monkeypatch.setattr(
            settings, "autopilot_daily_budget_limits_by_currency", "JPY:500:150000"
        )
        recorder = budget_script(before=adset(daily_budget="200000"))
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_INCREASE.value),
            recorder,
            details={"percentage": 10},
        )
        assert outcome.code is RefusalCode.GUARD_RAIL_BUDGET_CEILING
        assert recorder.writes == []
        rail = next(
            r for r in outcome.guard_rails if r["guard_rail"] == "daily_budget_ceiling"
        )
        assert rail["currency"] == "USD"
        assert rail["ceiling_major"] == "1000.0"

    def test_malformed_currency_limits_are_dropped_not_half_applied(self):
        """A limit nobody can parse is not a limit."""
        from app.core.config import Settings

        parsed = Settings(
            autopilot_daily_budget_limits_by_currency=(
                "JPY:500:150000,KRW:oops:5,VND:9:1,CLP:-5:10,BAD,COP:1"
            )
        ).autopilot_daily_budget_limits_map
        assert parsed == {"JPY": (500.0, 150000.0)}


# =============================================================================
# The entity's own ad account decides the currency
# =============================================================================

OTHER_ACCOUNT = "act_9876543210"


class TestAdAccountScope:
    """
    A tenant with several ad accounts must not have one account's currency
    applied to another account's entity.

    The credential is resolved per *connection*, and the account on it is only
    the tenant's oldest enabled one - nothing identifies the right account
    until the entity has been read. For an agency with a USD account and a JPY
    account that provisional pick decides a 100x conversion factor and the
    scale of the floor and ceiling, so the entity's own ``account_id`` has to
    override it before any money is converted.
    """

    async def test_currency_comes_from_the_entitys_own_account(
        self, write_settings, monkeypatch
    ):
        """A JPY ad set converts with JPY's offset even when USD is account #1."""
        monkeypatch.setattr(
            settings, "autopilot_daily_budget_limits_by_currency", "JPY:500:50000"
        )
        usd_first = make_ad_account("USD", AD_ACCOUNT)
        jpy_second = make_ad_account("JPY", OTHER_ACCOUNT)
        payload = adset(daily_budget="10000")
        payload["account_id"] = OTHER_ACCOUNT.removeprefix("act_")
        after = adset(daily_budget="8000")
        after["account_id"] = OTHER_ACCOUNT.removeprefix("act_")
        recorder = budget_script(
            before=payload, after=after, currency="JPY", ad_account=OTHER_ACCOUNT
        )
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            recorder,
            db=session(ad_accounts=[usd_first, jpy_second]),
            details={"amount_major": 2000},
        )
        # 2000 yen off 10000 yen is 8000 on the wire. Converted with the USD
        # account's offset it would have been 10000 - 200000, i.e. negative.
        assert outcome.status is ExecutionStatus.APPLIED
        assert recorder.writes[0].content.decode() == "daily_budget=8000"
        # The currency was read from the entity's account, not the first one.
        assert any(
            OTHER_ACCOUNT in str(request.url) for request in recorder.requests
        )
        assert not any(AD_ACCOUNT in str(request.url) for request in recorder.requests)

    async def test_an_entity_in_an_unowned_account_refuses(self, write_settings):
        """An entity in an account this tenant has no record of is refused."""
        payload = adset(daily_budget="50000")
        payload["account_id"] = "5555555555"
        recorder = Recorder({f"GET {ADSET_ID}": ok(payload)})
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            recorder,
            details={"percentage": 10},
        )
        assert outcome.code is RefusalCode.ACCOUNT_MISMATCH
        assert recorder.writes == []
        assert "act_5555555555" in outcome.reason

    async def test_an_entity_without_an_account_id_refuses(self, write_settings):
        """No account_id means no established unit for the numbers in it."""
        payload = adset(daily_budget="50000")
        payload.pop("account_id")
        recorder = Recorder({f"GET {ADSET_ID}": ok(payload)})
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            recorder,
            details={"percentage": 10},
        )
        assert outcome.code is RefusalCode.ACCOUNT_MISMATCH
        assert recorder.writes == []

    async def test_a_status_action_is_scoped_too(self, write_settings):
        """Pausing something in an account the tenant does not own is refused."""
        payload = adset()
        payload["account_id"] = "5555555555"
        recorder = Recorder({f"GET {ADSET_ID}": ok(payload)})
        outcome = await run(make_action(), recorder)
        assert outcome.code is RefusalCode.ACCOUNT_MISMATCH
        assert recorder.writes == []


# =============================================================================
# Trust gate and enforcement mode
# =============================================================================


class TestGateAndEnforcement:
    """Nothing reaches Meta without a passing gate and a permitting mode."""

    async def test_blocked_gate_prevents_every_request(self, write_settings):
        """A blocked gate stops the action before a single HTTP call."""
        recorder = Recorder({})
        outcome = await run(make_action(), recorder, gate=BlockingGate())
        assert outcome.code is RefusalCode.TRUST_GATE
        assert recorder.requests == []

    async def test_missing_gate_is_not_permission(self, write_settings):
        """A caller that forgets the gate gets a refusal, not a write."""
        recorder = Recorder({})
        executor = PLATFORM_EXECUTORS["meta"]
        outcome = await executor.execute_action(
            db=session(),
            action=make_action(),
            action_details={},
            gate=None,
            client_factory=recorder.factory,
        )
        assert outcome.code is RefusalCode.TRUST_GATE
        assert recorder.requests == []

    async def test_hard_block_refuses(self, write_settings):
        """hard_block prevents automated changes at the API."""
        recorder = Recorder({})
        db = session(
            enforcement=TenantEnforcementSettings(
                id=uuid4(),
                tenant_id=TENANT_ID,
                default_mode=EnforcementMode.HARD_BLOCK,
            )
        )
        outcome = await run(make_action(), recorder, db=db)
        assert outcome.code is RefusalCode.ENFORCEMENT_HARD_BLOCK
        assert recorder.requests == []

    async def test_soft_block_without_a_token_refuses(self, write_settings):
        """soft_block needs a confirmation, and autopilot has no human."""
        recorder = Recorder({})
        db = session(
            enforcement=TenantEnforcementSettings(
                id=uuid4(),
                tenant_id=TENANT_ID,
                default_mode=EnforcementMode.SOFT_BLOCK,
            )
        )
        outcome = await run(make_action(), recorder, db=db)
        assert outcome.code is RefusalCode.ENFORCEMENT_UNCONFIRMED
        assert recorder.requests == []

    async def test_soft_block_with_a_valid_token_proceeds_and_consumes_it(
        self, write_settings
    ):
        """One confirmation authorises exactly one write."""
        token = PendingConfirmationToken(
            id=uuid4(),
            tenant_id=TENANT_ID,
            token="confirm-abc123",
            action_type=ActionType.PAUSE_ADSET.value,
            entity_id=ADSET_ID,
            violations={},
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        db = session(
            enforcement=TenantEnforcementSettings(
                id=uuid4(),
                tenant_id=TENANT_ID,
                default_mode=EnforcementMode.SOFT_BLOCK,
            ),
            tokens=[token],
        )
        recorder = Recorder(
            {
                f"GET {ADSET_ID}": [ok(adset()), ok(adset(status="PAUSED"))],
                f"POST {ADSET_ID}": ok({"success": True}),
            },
            db.journal,
        )
        outcome = await run(
            make_action(), recorder, db=db, details={"confirmation_token": token.token}
        )
        assert outcome.status is ExecutionStatus.APPLIED
        assert db.tokens == [], "the token must be consumed, not reusable"
        # Flushed at once. The session runs with autoflush=False, so an
        # unflushed delete is invisible to the next SELECT in the same
        # transaction - two queued rows for the same tenant, action type and
        # entity would both find this token and both write on one confirmation.
        assert "flush" in db.journal
        assert db.journal.index("flush") < db.journal.index("POST")

    async def test_a_guard_rail_refusal_does_not_burn_the_confirmation(
        self, write_settings
    ):
        """
        A confirmation authorises a change, so a refused change must not spend it.

        The operator confirmed a budget cut; a guard rail then refuses it and
        nothing is written. Consuming the token there would make them go and
        confirm again for a change that never happened - and the refusal text
        would not even say so.
        """
        token = PendingConfirmationToken(
            id=uuid4(),
            tenant_id=TENANT_ID,
            token="confirm-refused",
            action_type=ActionType.BUDGET_DECREASE.value,
            entity_id=ADSET_ID,
            violations={},
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        db = session(
            enforcement=TenantEnforcementSettings(
                id=uuid4(),
                tenant_id=TENANT_ID,
                default_mode=EnforcementMode.SOFT_BLOCK,
            ),
            tokens=[token],
        )
        recorder = budget_script(before=adset(daily_budget="50000"))
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            recorder,
            db=db,
            details={"confirmation_token": token.token, "percentage": 90},
        )
        assert outcome.code is RefusalCode.GUARD_RAIL_SINGLE_CHANGE_PCT
        assert recorder.writes == []
        assert db.tokens == [token], "a refused action must not spend a confirmation"

    async def test_a_dry_run_does_not_burn_the_confirmation(
        self, write_settings, monkeypatch
    ):
        """Dry run writes nothing, so it authorises nothing either."""
        monkeypatch.setattr(settings, "autopilot_execution_dry_run", True)
        token = PendingConfirmationToken(
            id=uuid4(),
            tenant_id=TENANT_ID,
            token="confirm-dry",
            action_type=ActionType.PAUSE_ADSET.value,
            entity_id=ADSET_ID,
            violations={},
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
        )
        db = session(
            enforcement=TenantEnforcementSettings(
                id=uuid4(),
                tenant_id=TENANT_ID,
                default_mode=EnforcementMode.SOFT_BLOCK,
            ),
            tokens=[token],
        )
        recorder = Recorder({f"GET {ADSET_ID}": ok(adset())})
        outcome = await run(
            make_action(), recorder, db=db, details={"confirmation_token": token.token}
        )
        assert outcome.status is ExecutionStatus.DRY_RUN
        assert db.tokens == [token]

    async def test_soft_block_with_an_expired_token_refuses(self, write_settings):
        """An expired confirmation is not a confirmation."""
        token = PendingConfirmationToken(
            id=uuid4(),
            tenant_id=TENANT_ID,
            token="confirm-stale",
            action_type=ActionType.PAUSE_ADSET.value,
            entity_id=ADSET_ID,
            violations={},
            created_at=datetime.now(UTC) - timedelta(hours=2),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        db = session(
            enforcement=TenantEnforcementSettings(
                id=uuid4(),
                tenant_id=TENANT_ID,
                default_mode=EnforcementMode.SOFT_BLOCK,
            ),
            tokens=[token],
        )
        recorder = Recorder({})
        outcome = await run(
            make_action(), recorder, db=db, details={"confirmation_token": token.token}
        )
        assert outcome.code is RefusalCode.ENFORCEMENT_UNCONFIRMED
        assert recorder.requests == []


# =============================================================================
# Verification, ambiguity and idempotency
# =============================================================================


class TestVerificationAndIdempotency:
    """A write is only applied once it has been read back."""

    async def test_failed_reread_marks_the_action_failed(self, write_settings):
        """Meta saying ``success`` is not proof the change took effect."""
        recorder = Recorder(
            {
                f"GET {ADSET_ID}": [ok(adset(status="ACTIVE")), ok(adset(status="ACTIVE"))],
                f"POST {ADSET_ID}": ok({"success": True}),
            }
        )
        outcome = await run(make_action(), recorder)
        assert outcome.status is ExecutionStatus.FAILED
        assert "does not carry the intended values" in outcome.reason
        assert outcome.after_value["status"] == "ACTIVE"

    async def test_ambiguous_write_is_reconciled_never_retried(self, write_settings):
        """A timeout during the POST re-reads once; it does not write again."""

        seen: list[httpx.Request] = []

        def counting(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.method == "POST":
                raise httpx.ReadTimeout("timed out", request=request)
            # ACTIVE on the first read (so there is a real change to make),
            # PAUSED on the reconciling read (so the write is shown to have
            # landed despite the timeout).
            reads = [r for r in seen if r.method == "GET"]
            return ok(adset(status="ACTIVE" if len(reads) == 1 else "PAUSED"))

        def factory(access_token: str) -> MetaWriteClient:
            return MetaWriteClient(
                access_token, transport=httpx.MockTransport(counting)
            )

        executor = PLATFORM_EXECUTORS["meta"]
        outcome = await executor.execute_action(
            db=session(),
            action=make_action(),
            action_details={},
            gate=PassingGate(),
            client_factory=factory,
        )
        posts = [r for r in seen if r.method == "POST"]
        assert len(posts) == 1, "an ambiguous write must never be repeated"
        # The reconciling read shows the change did land.
        assert outcome.status is ExecutionStatus.APPLIED
        assert outcome.platform_response["reconciled"] is True

    async def test_ambiguous_write_that_did_not_land_is_unknown(self, write_settings):
        """When the re-read disagrees, the outcome is UNKNOWN, not a retry."""

        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.method == "POST":
                raise httpx.ConnectError("connection reset", request=request)
            return ok(adset(status="ACTIVE"))

        def factory(access_token: str) -> MetaWriteClient:
            return MetaWriteClient(access_token, transport=httpx.MockTransport(handler))

        executor = PLATFORM_EXECUTORS["meta"]
        outcome = await executor.execute_action(
            db=session(),
            action=make_action(),
            action_details={},
            gate=PassingGate(),
            client_factory=factory,
        )
        assert outcome.status is ExecutionStatus.UNKNOWN
        assert len([r for r in seen if r.method == "POST"]) == 1

    async def test_an_applied_row_is_not_applied_again(self, write_settings):
        """The durable row status stops a re-delivered task."""
        recorder = Recorder({})
        outcome = await run(
            make_action(status=ActionStatus.APPLIED.value), recorder
        )
        assert outcome.status is ExecutionStatus.ALREADY_APPLIED
        assert recorder.requests == []

    async def test_an_entity_already_in_the_intended_state_is_not_written(
        self, write_settings
    ):
        """The observed state closes the window the row status cannot."""
        recorder = Recorder({f"GET {ADSET_ID}": ok(adset(status="PAUSED"))})
        outcome = await run(make_action(), recorder)
        assert outcome.status is ExecutionStatus.APPLIED
        assert outcome.idempotent_no_op is True
        assert recorder.writes == []
        assert outcome.before_value == outcome.after_value

    async def test_unapproved_rows_are_refused(self, write_settings):
        """Only an approved action may execute."""
        recorder = Recorder({})
        outcome = await run(
            make_action(status=ActionStatus.QUEUED.value), recorder
        )
        assert outcome.code is RefusalCode.ACTION_NOT_APPROVED
        assert recorder.requests == []

    async def test_the_row_is_claimed_and_committed_before_the_write(
        self, write_settings
    ):
        """
        The resolved target is durable before the request leaves.

        Without this, a crash between the POST and the outcome commit leaves an
        ``approved`` row whose next run resolves "-20%" against the value this
        write just set.
        """
        db = session()
        action = make_action(action_type=ActionType.BUDGET_DECREASE.value)
        recorder = budget_script(
            before=adset(daily_budget="50000"),
            after=adset(daily_budget="40000"),
            journal=db.journal,
        )
        outcome = await run(action, recorder, db=db, details={"percentage": 20})
        assert outcome.status is ExecutionStatus.APPLIED
        assert db.journal.index("commit") < db.journal.index("POST")
        assert action.status == ActionStatus.APPLYING.value
        claim = json.loads(action.platform_response)["pre_write_claim"]
        assert claim["changes"] == {"daily_budget": 40000}
        assert claim["before_value"]["daily_budget"] == "50000"

    async def test_a_row_another_worker_claimed_first_is_not_written(
        self, write_settings
    ):
        """
        The claim is conditional, and losing it means no write.

        ``FOR UPDATE SKIP LOCKED`` on the batch query is not enough on its own:
        the task now commits after each action, which releases those locks, so
        a second run started mid-batch can legitimately hold the remaining
        rows. Only an ``UPDATE ... WHERE status = 'approved'`` that must affect
        exactly one row makes "I am the one executing this" a fact.
        """
        db = session()
        db.claim_rowcount = 0  # another worker moved the row first
        recorder = Recorder(
            {
                f"GET {ADSET_ID}": ok(adset()),
                f"POST {ADSET_ID}": ok({"success": True}),
            }
        )
        outcome = await run(make_action(), recorder, db=db)
        assert outcome.code is RefusalCode.ACTION_NOT_APPROVED
        assert recorder.writes == [], "losing the claim must not write"
        assert db.claims, "the claim was attempted"

    async def test_a_relative_change_is_not_re_derived_after_a_lost_commit(
        self, write_settings
    ):
        """
        The compounding this whole claim mechanism exists to stop.

        A ``budget_decrease`` of 20% resolves against whatever is live, so a
        second attempt after the transaction failed to commit would resolve
        against the already-reduced value: 50000 -> 40000 -> 32000, both
        recorded as verified successes. Only comparing the entity against the
        *recorded absolute target* can notice, because an intent freshly
        derived from the entity can never match it.

        ``run`` never calls ``record_outcome``, so after the first pass the row
        is left exactly as a crashed worker would leave it: ``applying``, with
        the claim, and no committed outcome.
        """
        action = make_action(action_type=ActionType.BUDGET_DECREASE.value)
        first = await run(
            action,
            budget_script(
                before=adset(daily_budget="50000"), after=adset(daily_budget="40000")
            ),
            details={"percentage": 20},
        )
        assert first.status is ExecutionStatus.APPLIED
        assert action.status == ActionStatus.APPLYING.value

        # The task is redelivered. Meta is now serving the reduced budget.
        second_recorder = Recorder({f"GET {ADSET_ID}": ok(adset(daily_budget="40000"))})
        second = await run(action, second_recorder, details={"percentage": 20})
        assert second_recorder.writes == [], "the write must not be repeated"
        assert second.status is ExecutionStatus.APPLIED
        assert second.idempotent_no_op is True
        assert second.intended_changes == {"daily_budget": 40000}, (
            "reconciled against the recorded target, not a freshly derived 32000"
        )

    async def test_a_claimed_row_whose_target_is_absent_is_unknown_not_rewritten(
        self, write_settings
    ):
        """
        "The target is not there" does not prove the write never left.

        It cannot distinguish that from "it landed and somebody changed it
        back", and re-issuing on that guess is how a budget gets cut twice. So
        the outcome is UNKNOWN and an operator decides.
        """
        action = make_action(action_type=ActionType.BUDGET_DECREASE.value)
        await run(
            action,
            budget_script(
                before=adset(daily_budget="50000"), after=adset(daily_budget="40000")
            ),
            details={"percentage": 20},
        )
        recorder = Recorder({f"GET {ADSET_ID}": ok(adset(daily_budget="50000"))})
        outcome = await run(action, recorder, details={"percentage": 20})
        assert outcome.status is ExecutionStatus.UNKNOWN
        assert outcome.code is RefusalCode.IN_FLIGHT_UNRESOLVED
        assert recorder.writes == []
        assert record_outcome(action, outcome) == "unknown"
        assert action.status == ActionStatus.FAILED.value

    async def test_a_claimed_row_with_no_readable_claim_is_unknown(self, write_settings):
        """A mid-write row nobody can read is reconciled by a human, not guessed."""
        action = make_action(status=ActionStatus.APPLYING.value)
        action.platform_response = "not json"
        recorder = Recorder({})
        outcome = await run(action, recorder)
        assert outcome.status is ExecutionStatus.UNKNOWN
        assert outcome.code is RefusalCode.IN_FLIGHT_UNRESOLVED
        assert recorder.requests == []

    async def test_a_claimed_row_is_reconciled_even_when_the_gate_has_since_blocked(
        self, write_settings
    ):
        """
        Reconciling reads; it does not act, so the gate must not strand it.

        A row whose write may already have landed has to be resolvable even
        after signal health degrades - refusing to look is how a row sits in
        ``applying`` for ever.
        """
        action = make_action(action_type=ActionType.BUDGET_DECREASE.value)
        await run(
            action,
            budget_script(
                before=adset(daily_budget="50000"), after=adset(daily_budget="40000")
            ),
            details={"percentage": 20},
        )
        recorder = Recorder({f"GET {ADSET_ID}": ok(adset(daily_budget="40000"))})
        outcome = await run(
            action, recorder, gate=BlockingGate(), details={"percentage": 20}
        )
        assert outcome.status is ExecutionStatus.APPLIED
        assert outcome.idempotent_no_op is True
        assert recorder.writes == []

    async def test_a_transient_5xx_write_is_reconciled_not_recorded_as_failed(
        self, write_settings
    ):
        """
        A 500 can happen either side of the mutation, so it is not a non-event.

        Recording ``failed`` with no after-value would put "the pause did not
        happen" in the audit trail while the ad set may in fact be paused.
        """
        recorder = Recorder(
            {
                f"GET {ADSET_ID}": [ok(adset()), ok(adset(status="PAUSED"))],
                f"POST {ADSET_ID}": httpx.Response(
                    500, json={"error": {"code": 2, "is_transient": True}}
                ),
            }
        )
        outcome = await run(make_action(), recorder)
        assert len(recorder.writes) == 1, "an ambiguous write is never repeated"
        assert outcome.status is ExecutionStatus.APPLIED
        assert outcome.platform_response["reconciled"] is True
        assert outcome.after_value["status"] == "PAUSED"

    async def test_a_permanent_5xx_write_stays_a_definite_failure(self, write_settings):
        """Meta saying ``is_transient: false`` is Meta saying it did not apply."""
        recorder = Recorder(
            {
                f"GET {ADSET_ID}": ok(adset()),
                f"POST {ADSET_ID}": httpx.Response(
                    500,
                    json={
                        "error": {
                            "code": 100,
                            "message": "Invalid parameter",
                            "is_transient": False,
                        }
                    },
                ),
            }
        )
        outcome = await run(make_action(), recorder)
        assert outcome.status is ExecutionStatus.FAILED
        assert outcome.after_value is None

    async def test_a_2xx_write_with_an_unparseable_body_is_reconciled(
        self, write_settings
    ):
        """Meta accepted it and then said something unreadable; that is not a no-op."""
        recorder = Recorder(
            {
                f"GET {ADSET_ID}": [ok(adset()), ok(adset(status="ACTIVE"))],
                f"POST {ADSET_ID}": httpx.Response(200, text="<html>oops</html>"),
            }
        )
        outcome = await run(make_action(), recorder)
        assert len(recorder.writes) == 1
        assert outcome.status is ExecutionStatus.UNKNOWN
        assert outcome.platform_response["reconciled"] is True

    def test_a_refusal_leaves_a_mid_write_row_in_applying(self):
        """
        A refused attempt must not demote a row whose write may have landed.

        The executor refuses to reconcile while execution is disabled; putting
        that row back to ``approved`` would hand an unresolved in-flight change
        straight to the next queue run.
        """
        action = make_action(status=ActionStatus.APPLYING.value)
        outcome = ActionOutcome(
            status=ExecutionStatus.REFUSED,
            reason="Autopilot execution is disabled",
            code=RefusalCode.EXECUTION_DISABLED,
        )
        assert record_outcome(action, outcome) == "refused"
        assert action.status == ActionStatus.APPLYING.value


# =============================================================================
# Off by default
# =============================================================================


class TestOffByDefault:
    """Merging this cannot start spending money."""

    def test_both_switches_default_to_no_write(self):
        """The shipped defaults are execution off and dry-run on."""
        from app.core.config import Settings

        defaults = Settings.model_fields
        assert defaults["autopilot_execution_enabled"].default is False
        assert defaults["autopilot_execution_dry_run"].default is True
        assert defaults["rules_local_campaign_mutations_enabled"].default is False

    def test_default_allowlist_is_never_wider_than_safe_actions(self):
        """The allowlist starts from SAFE_ACTIONS and nothing wider."""
        from app.core.config import Settings

        default = Settings.model_fields["autopilot_executable_action_types"].default
        assert set(default.split(",")) <= {a.value for a in SAFE_ACTIONS}

    def test_pause_creative_is_not_executable_by_default(self):
        """
        Its Meta node is an assumption, so it is not shipped enabled.

        The executor maps entity_type "creative" to the Meta **ad** - an
        AdCreative has no status of its own - but the only in-repo producer
        (app/analytics/logic/recommend.py, from fatigue detection) emits
        ``fact_creative.creative_id``, which is nowhere established to be an ad
        id. A money-affecting action whose target node rests on an assumption
        does not belong in a default allowlist, so it is opt-in until the
        producer is shown to emit an ad id.
        """
        from app.core.config import Settings

        default = Settings.model_fields["autopilot_executable_action_types"].default
        assert ActionType.PAUSE_CREATIVE.value not in default.split(",")
        assert ActionType.PAUSE_CREATIVE in SAFE_ACTIONS, (
            "still a safe action for the approval workflow; only auto-execution "
            "is withheld"
        )

    async def test_feature_flag_off_writes_nothing(self, write_settings, monkeypatch):
        """With the master switch off, not even a read is issued."""
        monkeypatch.setattr(settings, "autopilot_execution_enabled", False)
        recorder = Recorder({})
        outcome = await run(make_action(), recorder)
        assert outcome.code is RefusalCode.EXECUTION_DISABLED
        assert recorder.requests == []

    async def test_dry_run_runs_every_check_and_writes_nothing(
        self, write_settings, monkeypatch
    ):
        """Dry run reports the intended change without calling a write endpoint."""
        monkeypatch.setattr(settings, "autopilot_execution_dry_run", True)
        recorder = budget_script(before=adset(daily_budget="50000"))
        outcome = await run(
            make_action(action_type=ActionType.BUDGET_DECREASE.value),
            recorder,
            details={"percentage": 10},
        )
        assert outcome.status is ExecutionStatus.DRY_RUN
        assert recorder.writes == []
        assert outcome.intended_changes == {"daily_budget": 45000}
        assert outcome.before_value["daily_budget"] == "50000"
        assert outcome.guard_rails, "every guard rail still runs in dry run"
        assert all(rail["passed"] for rail in outcome.guard_rails)

    async def test_dry_run_leaves_the_row_approved(self, write_settings, monkeypatch):
        """A dry run must not mark anything applied or failed."""
        monkeypatch.setattr(settings, "autopilot_execution_dry_run", True)
        recorder = budget_script(before=adset(daily_budget="50000"))
        action = make_action(action_type=ActionType.BUDGET_DECREASE.value)
        outcome = await run(action, recorder, details={"percentage": 10})
        bucket = record_outcome(action, outcome)
        assert bucket == "dry_run"
        assert action.status == ActionStatus.APPROVED.value
        assert action.applied_at is None

    async def test_dry_run_on_an_entity_already_in_state_stays_a_dry_run(
        self, write_settings, monkeypatch
    ):
        """
        A mode that must change nothing must not mark a row applied.

        The observed-state check answers "already in the intended state" with
        an applied outcome, which is right for a real run and wrong for a dry
        one: it would stamp ``applied_at`` on the row and have it count against
        the tenant's daily cap for the rest of the day, all from a mode whose
        entire promise is that it reports.
        """
        monkeypatch.setattr(settings, "autopilot_execution_dry_run", True)
        recorder = Recorder({f"GET {ADSET_ID}": ok(adset(status="PAUSED"))})
        action = make_action()
        outcome = await run(action, recorder)
        assert outcome.status is ExecutionStatus.DRY_RUN
        assert outcome.idempotent_no_op is True
        assert recorder.writes == []
        assert record_outcome(action, outcome) == "dry_run"
        assert action.status == ActionStatus.APPROVED.value
        assert action.applied_at is None

    def test_the_task_is_scheduled_and_its_queue_is_consumed(self):
        """
        The write path is wired into beat, and onto a queue a worker drains.

        A beat entry whose queue no worker consumes is worse than no entry at
        all: the run looks scheduled and the messages pile up unread.
        """
        from app.workers.celery_app import BEAT_SCHEDULE, CELERY_QUEUES, celery_app

        assert "app.tasks.apply_actions_queue" in celery_app.conf.include

        entry = BEAT_SCHEDULE["autopilot-apply-actions-queue"]
        assert entry["task"] == "tasks.apply_actions_queue"
        assert entry["options"]["queue"] in CELERY_QUEUES

    def test_the_fan_out_wrapper_is_not_also_scheduled(self):
        """
        Only one of the two entry points may be on the schedule.

        ``tasks.schedule_apply_actions_queue`` exists only to re-enqueue
        ``tasks.apply_actions_queue``. Scheduling both would run the batch twice
        per tick against the same day-scoped guard rails.
        """
        from app.workers.celery_app import BEAT_SCHEDULE

        scheduled = {entry.get("task") for entry in BEAT_SCHEDULE.values()}
        assert "tasks.apply_actions_queue" in scheduled
        assert "tasks.schedule_apply_actions_queue" not in scheduled

    def test_being_scheduled_does_not_enable_writes(self):
        """
        Scheduling is not the switch. The defaults still refuse every write.

        This is the whole safety argument for wiring beat up separately from
        turning execution on, so it is asserted rather than assumed.
        """
        from app.core.config import Settings

        defaults = Settings.model_fields
        assert defaults["autopilot_execution_enabled"].default is False
        assert defaults["autopilot_execution_dry_run"].default is True
        assert defaults["rules_local_campaign_mutations_enabled"].default is False

    def test_the_task_starts_its_own_event_loop(self):
        """
        The task must not call ``asyncio.get_event_loop()``.

        On Python 3.12 - the version in `.github/workflows/ci.yml` and in
        `python:3.12-slim-bookworm` - that raises
        ``RuntimeError('There is no current event loop in thread ...')`` in a
        thread that has none, which is precisely a Celery prefork worker. On
        3.11 it still auto-creates a loop, so a developer running the suite
        locally on 3.11 would not see it.

        This mattered only once the task was scheduled: until then nothing ever
        called it, so the fault was unreachable. A beat entry firing every five
        minutes would have raised on every tick.
        """
        import inspect

        from app.tasks import apply_actions_queue as task_module

        source = inspect.getsource(task_module)
        assert "asyncio.get_event_loop()" not in source
        assert "asyncio.run(" in source

    def test_a_disabled_run_reads_no_rows_and_writes_no_audit(self, monkeypatch):
        """
        The batch returns before it queries, so a quiet deployment stays quiet.

        `execute_action` refusing is correct but not sufficient on its own: a
        refused row deliberately keeps its APPROVED status, so on the
        five-minute beat the same rows would be re-read and one refusal audit
        row written per action per tick - 288 a day, describing a decision that
        has not changed since the row was queued.

        Asserting on the session factory rather than on a row count is the
        point: with execution off the task must not open a transaction at all.

        Synchronous on purpose: the task drives its own event loop, which
        cannot be done from inside pytest-asyncio's already-running one.
        """
        from app.tasks import apply_actions_queue as task_module

        monkeypatch.setattr(settings, "autopilot_execution_enabled", False)

        def _no_session(*args, **kwargs):
            raise AssertionError(
                "apply_actions_queue opened a database session while execution "
                "was disabled; it must return before querying the batch"
            )

        monkeypatch.setattr(task_module, "async_session_factory", _no_session)

        result = task_module.apply_actions_queue(None)

        assert result["status"] == "skipped"
        assert result["reason"] == "execution_disabled"
        assert result["processed"] == 0
        assert result["failed"] == 0

    async def test_a_scheduled_run_writes_nothing_while_execution_is_disabled(
        self, monkeypatch
    ):
        """
        With the master switch off, an approved row is refused, not written.

        The executor checks the switch before it decrypts a token or consults
        the trust gate, so a default deployment running this on a five-minute
        beat issues no Meta request at all.
        """
        monkeypatch.setattr(settings, "autopilot_execution_enabled", False)
        recorder = Recorder({})
        action = make_action()

        outcome = await run(action, recorder)

        assert outcome.status is ExecutionStatus.REFUSED
        assert outcome.code is RefusalCode.EXECUTION_DISABLED
        # Not just no POST: no request of any kind, because the switch is
        # checked before the entity is even read.
        assert recorder.requests == []
        assert action.status == ActionStatus.APPROVED.value
        assert action.applied_at is None


# =============================================================================
# Reversibility
# =============================================================================


class TestRevert:
    """Every executed change can be undone, unless a human changed it first."""

    async def test_revert_restores_the_recorded_before_value(self, write_settings):
        """The one-click override writes the before-value back and verifies it."""
        action = make_action(
            action_type=ActionType.BUDGET_DECREASE.value,
            status=ActionStatus.APPLIED.value,
            before_value=adset(daily_budget="50000"),
            after_value=adset(daily_budget="45000"),
        )
        recorder = Recorder(
            {
                f"GET {ADSET_ID}": [
                    ok(adset(daily_budget="45000")),
                    ok(adset(daily_budget="50000")),
                ],
                f"POST {ADSET_ID}": ok({"success": True}),
            }
        )
        outcome = await revert_meta_action(
            session(), action, client_factory=recorder.factory
        )
        assert outcome.status is ExecutionStatus.APPLIED
        assert recorder.writes[0].content.decode() == "daily_budget=50000"
        assert outcome.after_value["daily_budget"] == "50000"

    async def test_revert_refuses_when_the_entity_has_drifted(self, write_settings):
        """Somebody else changed it since; their change is not overwritten."""
        action = make_action(
            action_type=ActionType.BUDGET_DECREASE.value,
            status=ActionStatus.APPLIED.value,
            before_value=adset(daily_budget="50000"),
            after_value=adset(daily_budget="45000"),
        )
        recorder = Recorder({f"GET {ADSET_ID}": ok(adset(daily_budget="90000"))})
        outcome = await revert_meta_action(
            session(), action, client_factory=recorder.factory
        )
        assert outcome.status is ExecutionStatus.REFUSED
        assert outcome.code is RefusalCode.ENTITY_DRIFTED
        assert recorder.writes == []
        assert "90000" in outcome.reason

    async def test_revert_of_a_pause_restores_the_status(self, write_settings):
        """A paused ad set is re-enabled to exactly the status it had."""
        action = make_action(
            status=ActionStatus.APPLIED.value,
            before_value=adset(status="ACTIVE"),
            after_value=adset(status="PAUSED"),
        )
        recorder = Recorder(
            {
                f"GET {ADSET_ID}": [ok(adset(status="PAUSED")), ok(adset(status="ACTIVE"))],
                f"POST {ADSET_ID}": ok({"success": True}),
            }
        )
        outcome = await revert_meta_action(
            session(), action, client_factory=recorder.factory
        )
        assert outcome.status is ExecutionStatus.APPLIED
        assert recorder.writes[0].content.decode() == "status=ACTIVE"

    async def test_revert_is_a_no_op_before_execution(self, write_settings):
        """Only an applied action can be reverted."""
        recorder = Recorder({})
        outcome = await revert_meta_action(
            session(), make_action(), client_factory=recorder.factory
        )
        assert outcome.code is RefusalCode.NOT_APPLIED
        assert recorder.requests == []

    async def test_revert_honours_dry_run(self, write_settings, monkeypatch):
        """Dry run checks the drift and reports the restore without writing."""
        monkeypatch.setattr(settings, "autopilot_execution_dry_run", True)
        action = make_action(
            status=ActionStatus.APPLIED.value,
            before_value=adset(status="ACTIVE"),
            after_value=adset(status="PAUSED"),
        )
        recorder = Recorder({f"GET {ADSET_ID}": ok(adset(status="PAUSED"))})
        outcome = await revert_meta_action(
            session(), action, client_factory=recorder.factory
        )
        assert outcome.status is ExecutionStatus.DRY_RUN
        assert recorder.writes == []
        assert outcome.intended_changes == {"status": "ACTIVE"}


# =============================================================================
# Client contract
# =============================================================================


class TestWriteClientContract:
    """The client implements the documented Marketing API write contract."""

    async def test_update_is_a_post_to_the_node(self, write_settings):
        """POST /{entity_id}, form-encoded, on the configured API version."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return ok({"success": True})

        async with MetaWriteClient(
            TOKEN, transport=httpx.MockTransport(handler)
        ) as client:
            await client.update_entity(
                MetaEntityType.ADSET, ADSET_ID, {"status": "PAUSED"}
            )

        assert seen[0].method == "POST"
        assert seen[0].url.path == f"/v23.0/{ADSET_ID}"
        assert seen[0].content.decode() == "status=PAUSED"

    async def test_read_requests_the_documented_fields(self, write_settings):
        """GET /{entity_id}?fields=... with no token in the query string."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return ok(adset())

        async with MetaWriteClient(
            TOKEN, transport=httpx.MockTransport(handler)
        ) as client:
            await client.get_entity(MetaEntityType.ADSET, ADSET_ID)

        fields = seen[0].url.params["fields"].split(",")
        assert "status" in fields
        assert "daily_budget" in fields
        assert "campaign_id" in fields
        assert TOKEN not in str(seen[0].url)
        assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"

    @pytest.mark.parametrize(
        ("entity_type", "field"),
        [
            (MetaEntityType.AD, "daily_budget"),
            (MetaEntityType.AD, "bid_amount"),
            (MetaEntityType.CAMPAIGN, "bid_amount"),
            (MetaEntityType.ADSET, "name"),
        ],
    )
    async def test_unwritable_fields_are_rejected_locally(
        self, write_settings, entity_type, field
    ):
        """
        An ad has no budget and Meta refuses bid_amount on one.

        The rejection happens before any request, so a bug elsewhere cannot
        become an unexpected mutation.
        """
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return ok({"success": True})

        async with MetaWriteClient(
            TOKEN, transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(ValueError, match="not writable"):
                await client.update_entity(entity_type, "123", {field: 1000})
        assert seen == []

    @pytest.mark.parametrize("status", ["DELETED", "ARCHIVED", "active "])
    async def test_destructive_statuses_are_rejected(self, write_settings, status):
        """Autopilot may only write ACTIVE or PAUSED."""
        async with MetaWriteClient(
            TOKEN, transport=httpx.MockTransport(lambda r: ok({}))
        ) as client:
            with pytest.raises(ValueError):
                await client.update_entity(
                    MetaEntityType.ADSET, ADSET_ID, {"status": status}
                )

    def test_writable_fields_match_the_documented_contract(self):
        """Campaign and ad set carry budgets; an ad carries only status."""
        assert WRITABLE_FIELDS[MetaEntityType.AD] == frozenset({"status"})
        assert "bid_amount" in WRITABLE_FIELDS[MetaEntityType.ADSET]
        assert "bid_amount" not in WRITABLE_FIELDS[MetaEntityType.CAMPAIGN]
        assert WRITABLE_STATUSES == frozenset({"ACTIVE", "PAUSED"})

    async def test_money_must_arrive_already_converted(self, write_settings):
        """A Decimal or a float in a money field is a programming error."""
        async with MetaWriteClient(
            TOKEN, transport=httpx.MockTransport(lambda r: ok({}))
        ) as client:
            with pytest.raises(ValueError, match="Meta API units"):
                await client.update_entity(
                    MetaEntityType.ADSET, ADSET_ID, {"daily_budget": Decimal("100.00")}
                )

    @pytest.mark.parametrize(
        ("code", "subcode", "expected"),
        [
            (190, 463, MetaTokenError),
            (102, None, MetaTokenError),
            (4, None, MetaRateLimitError),
            (80004, None, MetaRateLimitError),
            (100, 1487901, MetaWriteValidationError),
            (200, None, MetaWriteValidationError),
        ],
    )
    async def test_error_codes_map_to_distinct_exception_types(
        self, write_settings, code, subcode, expected
    ):
        """Token, throttle and non-retryable validation are told apart."""
        body: dict[str, Any] = {"error": {"message": "nope", "code": code}}
        if subcode is not None:
            body["error"]["error_subcode"] = subcode

        async with MetaWriteClient(
            TOKEN, transport=httpx.MockTransport(lambda r: httpx.Response(400, json=body))
        ) as client:
            with pytest.raises(expected):
                await client.update_entity(
                    MetaEntityType.ADSET, ADSET_ID, {"status": "PAUSED"}
                )

    async def test_transport_failure_on_a_write_is_ambiguous_not_an_api_error(
        self, write_settings
    ):
        """
        The distinction that stops a double-apply.

        ``MetaWriteAmbiguousError`` is deliberately not a ``MetaAPIError``, so
        a handler that retries "Meta errors" cannot catch it.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        async with MetaWriteClient(
            TOKEN, transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(MetaWriteAmbiguousError):
                await client.update_entity(
                    MetaEntityType.ADSET, ADSET_ID, {"status": "PAUSED"}
                )

        from app.services.meta.insights_client import MetaAPIError

        assert not issubclass(MetaWriteAmbiguousError, MetaAPIError)

    async def test_transport_failure_on_a_read_is_an_ordinary_error(
        self, write_settings
    ):
        """A read has no side effect, so it is not ambiguous."""
        from app.services.meta.insights_client import MetaAPIError

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=request)

        async with MetaWriteClient(
            TOKEN, transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(MetaAPIError):
                await client.get_entity(MetaEntityType.ADSET, ADSET_ID)


# =============================================================================
# Token safety
# =============================================================================


class TestTokenSafety:
    """The access token must not reach a log record or an exception."""

    async def test_token_absent_from_logs_and_errors_on_every_path(
        self, write_settings, caplog
    ):
        """Drive a success, a refusal and a failure, then search everything."""
        structlog.configure(
            processors=[structlog.stdlib.render_to_log_kwargs],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=False,
        )
        messages: list[str] = []

        with caplog.at_level(logging.DEBUG):
            recorder = Recorder(
                {
                    f"GET {ADSET_ID}": [ok(adset()), ok(adset(status="PAUSED"))],
                    f"POST {ADSET_ID}": ok({"success": True}),
                }
            )
            outcome = await run(make_action(), recorder)
            messages.append(json.dumps(outcome.to_audit_dict(), default=str))

            # A Meta error that echoes the token back in its message: the
            # client must redact it rather than pass it through.
            echoing = Recorder(
                {
                    f"GET {ADSET_ID}": httpx.Response(
                        400,
                        json={
                            "error": {
                                "message": f"Invalid OAuth access token {TOKEN}",
                                "code": 190,
                            }
                        },
                    )
                }
            )
            refusal = await run(make_action(), echoing)
            messages.append(refusal.reason)

        for record in caplog.records:
            messages.append(record.getMessage())
            messages.append(str(getattr(record, "args", "")))
            messages.append(json.dumps(record.__dict__, default=str))

        blob = "\n".join(messages)
        assert TOKEN not in blob
        assert "***REDACTED***" in refusal.reason

    async def test_credentials_never_appear_in_an_outcome(self, write_settings):
        """The audit payload carries no credential material at all."""
        recorder = Recorder(
            {
                f"GET {ADSET_ID}": [ok(adset()), ok(adset(status="PAUSED"))],
                f"POST {ADSET_ID}": ok({"success": True}),
            }
        )
        outcome = await run(make_action(), recorder)
        assert TOKEN not in json.dumps(outcome.to_audit_dict(), default=str)
        assert "access_token" not in json.dumps(outcome.to_audit_dict(), default=str)


# =============================================================================
# The batch task: history has to be visible within one run
# =============================================================================


class CommitVisibleSession(FakeAsyncSession):
    """
    A session whose committed state is what the history queries can see.

    ``AsyncSessionLocal`` is built with ``autoflush=False``, so setting
    ``row.status = "applied"`` in memory changes nothing a later ``SELECT`` in
    the same transaction can observe. Both day-scoped guard rails answer from a
    ``SELECT``. A fake that answered from the in-memory objects would make the
    rails look like they bind inside one batch when in reality every action in
    the run sees the same pre-batch state.

    So this fake models the real boundary: ``applied_actions`` - the history the
    rails read - is only recomputed at ``commit``.
    """

    def __init__(self, rows: list[FactActionsQueue], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rows = list(rows)
        self.applied_actions = []

    async def commit(self) -> None:
        """Publish the rows' current state to the history queries."""
        await super().commit()
        self.applied_actions = [
            row for row in self.rows if row.status == ActionStatus.APPLIED.value
        ]

    async def execute(self, statement: Any) -> Any:
        """Serve the task's approved-rows query; delegate everything else."""
        if isinstance(statement, Update):
            return await super().execute(statement)
        entity = _selected_entity(statement)
        params = statement.compile().params
        if (
            entity is FactActionsQueue
            and params.get("status_1") == ActionStatus.APPROVED.value
        ):
            return _Result(
                [
                    row
                    for row in self.rows
                    if row.status == ActionStatus.APPROVED.value
                ]
            )
        return await super().execute(statement)


class TestBatchRunAccounting:
    """A guard rail that cannot see this run's own writes is not a guard rail."""

    def test_the_daily_cap_binds_within_a_single_batch_run(
        self, write_settings, monkeypatch
    ):
        """
        Three approved rows, a cap of two: the third must be refused.

        Before the per-action commit, every action in a run read the same
        pre-batch history, so a cap of ten did not stop two hundred rows from
        executing in one pass - each of them recording ``daily_action_cap
        passed`` on the way through.
        """
        from app.services.meta import action_executor as executor_module
        from app.tasks import apply_actions_queue as task_module

        monkeypatch.setattr(
            settings, "autopilot_max_executed_actions_per_tenant_per_day", 2
        )
        rows = [
            make_action(entity_id=f"2384756291038{index:04d}") for index in range(3)
        ]
        db = CommitVisibleSession(
            rows, connection=make_connection(), ad_account=make_ad_account()
        )

        def handler(request: httpx.Request) -> httpx.Response:
            entity_id = request.url.path.rsplit("/", 1)[-1]
            payload = adset(status="ACTIVE" if request.method == "GET" else "PAUSED")
            payload["id"] = entity_id
            if request.method == "POST":
                return ok({"success": True})
            # First read ACTIVE, verifying read PAUSED.
            reads = [
                seen
                for seen in recorder_requests
                if seen.method == "GET" and seen.url.path == request.url.path
            ]
            payload["status"] = "ACTIVE" if len(reads) == 1 else "PAUSED"
            payload["effective_status"] = payload["status"]
            return ok(payload)

        recorder_requests: list[httpx.Request] = []

        def counting(request: httpx.Request) -> httpx.Response:
            recorder_requests.append(request)
            return handler(request)

        monkeypatch.setattr(
            executor_module,
            "_default_client_factory",
            lambda token: MetaWriteClient(
                token, transport=httpx.MockTransport(counting)
            ),
        )
        monkeypatch.setattr(task_module, "async_session_factory", lambda: _Session(db))

        async def _gate(_db: Any, _tenant_id: int) -> Any:
            return PassingGate()

        async def _publish(**_kwargs: Any) -> None:
            return None

        monkeypatch.setattr(task_module, "check_signal_health", _gate)
        monkeypatch.setattr(task_module, "publish_action_status_update", _publish)

        # The task drives its own loop via asyncio.get_event_loop(), which is
        # unset once another test in the session has closed one. Give it a
        # fresh loop rather than depending on test ordering.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = task_module.apply_actions_queue(tenant_id=TENANT_ID)
        finally:
            asyncio.set_event_loop(None)
            loop.close()

        assert result["processed"] == 2
        assert result["outcomes"].get("refused") == 1
        assert [row.status for row in rows] == [
            ActionStatus.APPLIED.value,
            ActionStatus.APPLIED.value,
            ActionStatus.APPROVED.value,
        ]
        assert rows[2].error is not None
        assert "cap is 2" in rows[2].error
        assert len([r for r in recorder_requests if r.method == "POST"]) == 2


class _Session:
    """Async context manager returning a prepared fake session."""

    def __init__(self, db: FakeAsyncSession) -> None:
        self._db = db

    async def __aenter__(self) -> FakeAsyncSession:
        """Hand out the session."""
        return self._db

    async def __aexit__(self, *exc_info: object) -> bool:
        """Never suppress."""
        return False
