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

import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

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

    async def execute(self, statement):
        """Return the tenant row, or the seat count."""
        sql = str(statement)
        if "FROM tenants" in sql:
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
