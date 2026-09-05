# =============================================================================
# Stratum AI - Tenant Member Listing Tests
# =============================================================================
"""
Regression tests for the team-management screen showing the wrong tenant.

``GET /tenants/{tenant_id}/users`` documented itself as returning the "users
count and list" and returned only counts. With no way to read a named tenant's
members, ``frontend/src/views/tenant/TeamManagement.tsx`` - which renders at
``/app/:tenantId/team`` - fell back to ``GET /users``, and that endpoint derives
the tenant from the caller's token rather than from any argument
(``list_users`` reads ``request.state.tenant_id``).

So a platform-role user opening *another* tenant's team page was shown **their
own** tenant's members, under the other tenant's name. Nothing errored and
nothing looked wrong; the page simply answered a different question than the one
its URL asked.

What is pinned here: the endpoint answers about the tenant in the path, it is
still authorised exactly as before, and it decrypts the PII columns the way the
other member listing does.
"""

import inspect

from app.api.v1.endpoints import tenants as tenants_module
from app.api.v1.endpoints import users as users_module


def test_the_endpoint_returns_the_members_of_the_tenant_in_the_path():
    """
    The listing is keyed on the path parameter, not on the caller's token.

    This is the whole defect: the screen asks about one tenant and used to be
    answered about another.
    """
    source = inspect.getsource(tenants_module.get_tenant_users)

    assert "select(User)" in source
    assert "User.tenant_id == tenant_id" in source
    assert '"users":' in source

    # The caller's own identity is read only to answer whether they may change
    # this tenant's members, never to choose which members are listed. Using it
    # for the listing is the original defect.
    assert "User.tenant_id == caller_tenant_id" not in source
    assert "can_manage_members" in source


def test_the_members_are_serialised_and_decrypted_like_the_other_listing():
    """
    Same response model and same PII decryption as ``GET /users``.

    ``email`` and ``full_name`` are encrypted at rest; a listing that forgot to
    decrypt them would render ciphertext, and one that invented its own shape
    would drift from the endpoint the rest of the app reads.
    """
    tenant_source = inspect.getsource(tenants_module.get_tenant_users)
    users_source = inspect.getsource(users_module.list_users)

    for fragment in (
        "UserResponse(",
        "decrypt_pii(u.email)",
        "decrypt_pii(u.full_name)",
    ):
        assert fragment in tenant_source, fragment
        assert fragment in users_source, fragment


def test_reading_the_members_is_still_behind_the_tenant_access_check():
    """
    Authorisation is unchanged: own tenant for a member, any tenant for the
    platform role.

    Adding the member rows widened what this endpoint *returns*, so the check
    that decides who may call it is worth pinning alongside. It is the same
    exposure as ``GET /users`` already grants a member of the tenant.
    """
    source = inspect.getsource(tenants_module.get_tenant_users)

    assert "require_tenant_access(request, tenant_id)" in source


def test_soft_deleted_members_are_excluded():
    """A removed member is not on the team, and does not consume a seat."""
    source = inspect.getsource(tenants_module.get_tenant_users)

    assert "User.is_deleted == False" in source


def test_the_seat_counts_are_derived_from_the_rows_that_were_returned():
    """
    ``user_count`` counts the listed members rather than being queried apart.

    Two queries could disagree - the count says four, the list shows three -
    and the screen would render a seat usage that contradicts the table beside
    it.
    """
    source = inspect.getsource(tenants_module.get_tenant_users)

    assert '"user_count": len(users)' in source
    assert '"slots_available": tenant.max_users - len(users)' in source


def test_the_user_endpoints_authorise_the_tenant_they_are_aimed_at():
    """
    The write endpoints now take a target tenant, and authorise it.

    They used to resolve the tenant from ``request.state`` and accept no
    argument, which is why this screen could only ever be read-only for anybody
    but the tenant's own admin. They now defer to ``resolve_target_tenant``,
    which honours another tenant only for the cross-tenant platform role - so
    what replaced the restriction is a check, not the absence of one.
    """
    for name in ("invite_user", "update_user", "delete_user"):
        source = inspect.getsource(getattr(users_module, name))
        assert "resolve_target_tenant(" in source, name
        # The old ad-hoc role gate is gone rather than left beside it.
        assert 'role not in ["admin", "superadmin"]' not in source, name


def test_the_response_says_whether_writes_would_land_on_this_tenant():
    """
    ``can_manage_members`` is answered by the server, not guessed in the browser.

    The member-management endpoints resolve their tenant from the caller's token
    and take no target, so on any tenant but the caller's own they would change
    the wrong one. Only the server knows both halves, and a client that guessed
    wrong would offer controls that quietly edit somebody else's team - so the
    screen asks rather than comparing an identity it may not have loaded.
    """
    source = inspect.getsource(tenants_module.get_tenant_users)

    assert '"can_manage_members": can_manage_members' in source
    # The same predicate the write endpoints enforce: the platform role
    # anywhere, an ADMIN on its own tenant, nobody else. A flag looser than the
    # endpoints offers controls that 403; tighter hides ones that would work.
    assert "UserRole.SUPERADMIN.value" in source
    assert (
        "caller_role == UserRole.ADMIN.value and caller_tenant_id == tenant_id" in source
    )
