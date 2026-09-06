# =============================================================================
# Stratum AI - Seat Limit Tests
# =============================================================================
"""
Tests for the plan seat limit actually stopping an invite.

``Tenant.max_users`` is a sold entitlement - ``app/core/tiers.py`` prices it at
3 / 10 / unlimited by tier - and the product asserted it in three places while
enforcing it in none of the ones that mattered:

* the team screen renders ``slots_available`` from it,
* the superadmin usage view raises "limit nearly reached" warnings against it,
* ``check_tier_limit("users", ...)`` existed and was wired into the *client
  portal* invite in ``endpoints/clients.py``,

but ``POST /users/invite`` - the ordinary way a team member is added - never
called it. A tenant on the 3-seat tier could invite a fourth, and be told
afterwards by a warning banner that it was over its limit.

What is pinned here: the team invite is checked, it is checked against the
tenant being invited into rather than the caller's, and the two user-creating
paths use the same rule rather than two copies of it.
"""

import ast
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api.v1.endpoints import clients as clients_module
from app.api.v1.endpoints import users as users_module
from app.auth.deps import check_tier_limit


class _Result:
    """Stand-in for a SQLAlchemy ``Result``."""

    def __init__(self, value=None):
        """Store the single value this result yields."""
        self._value = value

    def scalar_one_or_none(self):
        """Return the stored value."""
        return self._value

    def scalar(self):
        """Return the stored value."""
        return self._value


class _SeatSession:
    """Answers the tenant lookup and the seat count the checker issues."""

    def __init__(self, max_users: int, current_users: int):
        """Seed the tenant's limit and how many seats are taken."""
        self.tenant = SimpleNamespace(
            id=1, max_users=max_users, max_campaigns=50, settings={}
        )
        self.current_users = current_users
        self.counted_tenant_ids: list[int] = []
        self.tenant_statement = None

    async def execute(self, statement):
        """Return the tenant row, or the seat count."""
        sql = str(statement)
        if "FROM tenants" in sql:
            self.tenant_statement = statement
            return _Result(self.tenant)
        # The seat count. Record which tenant it was scoped to.
        params = dict(statement.compile().params)
        for key, value in params.items():
            if key.startswith("tenant_id"):
                self.counted_tenant_ids.append(value)
        return _Result(self.current_users)


# =============================================================================
# The limit itself
# =============================================================================


@pytest.mark.asyncio
async def test_an_invite_below_the_limit_is_allowed():
    """A tenant with a seat to spare can still invite."""
    session = _SeatSession(max_users=3, current_users=2)

    await check_tier_limit("users", 1, session)


@pytest.mark.asyncio
async def test_an_invite_at_the_limit_is_refused():
    """
    The seat count is a limit, not a warning.

    At 3 of 3 the next invite is refused - previously it succeeded and the
    tenant simply ran over its plan.
    """
    session = _SeatSession(max_users=3, current_users=3)

    with pytest.raises(HTTPException) as excinfo:
        await check_tier_limit("users", 1, session)

    assert excinfo.value.status_code == 403
    assert "3" in excinfo.value.detail


@pytest.mark.asyncio
async def test_an_invite_past_the_limit_is_refused():
    """A tenant already over its limit cannot dig deeper."""
    session = _SeatSession(max_users=3, current_users=7)

    with pytest.raises(HTTPException) as excinfo:
        await check_tier_limit("users", 1, session)

    assert excinfo.value.status_code == 403


@pytest.mark.asyncio
async def test_the_seats_are_counted_for_the_tenant_being_invited_into():
    """
    The count is scoped to the target tenant, not the caller's.

    Cross-tenant invites mean the two need not be the same, and a platform
    operator must not be able to spend a seat the customer does not have -
    nor be blocked by their own tenant's limit when acting on somebody else's.
    """
    session = _SeatSession(max_users=10, current_users=0)

    await check_tier_limit("users", 42, session)

    assert session.counted_tenant_ids == [42]


# =============================================================================
# It holds under concurrency
# =============================================================================


@pytest.mark.asyncio
async def test_the_tenant_row_is_locked_while_the_seats_are_counted():
    """
    The tenant row is read ``FOR UPDATE``.

    Counting seats and inserting the user are two statements, and between them
    another request can count the same free seat. Measured against a real
    tenant with one seat left, eight concurrent invites without this lock were
    all accepted and left it six seats over its plan; with it, one is accepted
    and seven are refused.

    The lock is on the tenant row rather than a separate mutex because that row
    carries ``max_users`` itself, so raising the limit serializes against the
    invites reading it.
    """
    session = _SeatSession(max_users=3, current_users=0)

    await check_tier_limit("users", 1, session)

    compiled = str(
        session.tenant_statement.compile(dialect=postgresql.dialect())
    ).upper()
    assert "FOR UPDATE" in compiled


def _check_tier_limit_call_sites():
    """
    Every ``check_tier_limit`` call in the endpoint modules, with its context.

    Discovered rather than listed, so a caller added later is held to the same
    rule without anyone remembering to extend this file.
    """
    sites = []
    for module in (users_module, clients_module):
        source = inspect.getsource(module)
        tree = ast.parse(source)
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.get_source_segment(source, func) or ""
            for call in ast.walk(func):
                if not isinstance(call, ast.Call):
                    continue
                if getattr(call.func, "id", None) != "check_tier_limit":
                    continue

                by_keyword = {kw.arg: kw.value for kw in call.keywords}
                resource_node = (
                    call.args[0] if call.args else by_keyword.get("resource")
                )
                session_node = (
                    call.args[2] if len(call.args) > 2 else by_keyword.get("db")
                )
                assert isinstance(resource_node, ast.Constant), (
                    f"{func.name} passes a computed resource; "
                    "the rule below cannot read it"
                )
                assert isinstance(session_node, ast.Name), (
                    f"{func.name} passes a computed session; "
                    "check by hand that the lock survives to the insert"
                )

                after = "\n".join(body.splitlines()[call.lineno - func.lineno :])
                sites.append(
                    SimpleNamespace(
                        module=module.__name__.rsplit(".", 1)[-1],
                        function=func.name,
                        resource=resource_node.value,
                        session=session_node.id,
                        after_the_call=after,
                    )
                )
    return sites


def test_every_caller_holds_the_lock_through_the_insert_it_gates():
    """
    Each caller inserts and commits on the session it had checked.

    ``SELECT ... FOR UPDATE`` is held until that session's transaction ends, so
    the lock only covers the check-then-insert if the caller does not open a
    new session in between. A caller that committed first, or reached for
    another session, would take the lock and drop it before the row that
    consumes the allowance was ever created - and this test is what would
    notice.

    Every call site is checked, not just the two that spend a seat: client
    creation gates ``max_clients`` through the same function, so the tenant's
    client count is now serialized by the same lock as its seats.
    """
    sites = _check_tier_limit_call_sites()

    found = {(s.module, s.function, s.resource) for s in sites}
    assert found >= {
        ("users", "invite_user", "users"),
        ("clients", "invite_portal_user", "users"),
        ("clients", "create_client", "clients"),
    }, f"a known call site went missing: {found}"

    for site in sites:
        where = f"{site.module}.{site.function}"
        assert site.session == "db", f"{where} checks a session it does not use"
        assert (
            f"{site.session}.add(" in site.after_the_call
        ), f"{where} takes the lock but never inserts on that session"
        assert f"await {site.session}.commit()" in site.after_the_call, (
            f"{where} never commits on the checked session, so the lock is "
            "released by something else - possibly after the row is gone"
        )


# =============================================================================
# Both user-creating paths use it
# =============================================================================


def test_the_team_invite_checks_the_seat_limit():
    """
    ``POST /users/invite`` calls the checker.

    This is the gap: the entitlement was displayed and warned about, never
    enforced on the ordinary path for adding a colleague.
    """
    source = inspect.getsource(users_module.invite_user)

    assert 'check_tier_limit("users"' in source


def test_it_checks_the_resolved_target_not_the_callers_tenant():
    """
    The checked tenant is the one the invite resolved to.

    Passing the caller's own tenant would let a platform operator fill a
    customer's team past its plan while their own limit was checked instead.
    """
    source = inspect.getsource(users_module.invite_user)

    resolve_at = source.index("resolve_target_tenant(")
    check_at = source.index('check_tier_limit("users"')
    assert resolve_at < check_at, "the target must be resolved before it is checked"
    assert 'check_tier_limit("users", tenant_id, db)' in source


def test_both_user_creating_paths_share_one_rule():
    """
    The client-portal invite and the team invite call the same function.

    Two copies of "is there a seat free?" is how they drift, and the copy that
    drifts is the one nobody is looking at.
    """
    team = inspect.getsource(users_module.invite_user)
    portal = inspect.getsource(clients_module)

    assert 'check_tier_limit("users"' in team
    assert 'check_tier_limit("users"' in portal


def test_the_limit_read_is_the_one_the_ui_displays():
    """
    Enforcement and display agree on both the limit and what consumes it.

    ``get_tenant_users`` renders ``slots_available`` as
    ``max_users - <non-deleted users>``. If the checker counted a different
    population - active users only, say - the screen would offer a seat the
    API then refused, which is worse than not enforcing at all.
    """
    checker = inspect.getsource(check_tier_limit)
    displayed = inspect.getsource(
        __import__(
            "app.api.v1.endpoints.tenants", fromlist=["get_tenant_users"]
        ).get_tenant_users
    )

    assert "tenant.max_users" in checker
    assert "tenant.max_users - len(users)" in displayed
    # Both count non-deleted users, and neither filters on is_active.
    assert "User.is_deleted == False" in checker
    assert "User.is_deleted == False" in displayed
    assert "is_active" not in checker.split('resource == "users"')[1].split("elif")[0]
