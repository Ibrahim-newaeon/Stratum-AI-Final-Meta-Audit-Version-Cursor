# =============================================================================
# Stratum AI - User role assignment regression tests
# =============================================================================
"""
Regression tests for role assignment on the user management endpoints.

``app.api.v1.endpoints.users`` built its role lookup from ``UserRole.USER``,
a member that does not exist on the enum (SUPERADMIN, ADMIN, MANAGER, ANALYST,
VIEWER). Both dicts were built inside the request handlers, so the module
imported cleanly and the break only showed up at call time: every
``POST /users/invite`` and every ``PATCH /users/{id}`` carrying a role raised
``AttributeError`` and answered 500. Nothing covered either path.

The lookup is now a single module-level table. What is pinned here:

* the table only names roles the enum actually has, so it cannot regress to an
  attribute that does not exist;
* SUPERADMIN is absent. It is the cross-tenant platform role, and these are
  tenant-scoped endpoints, so granting it here would be privilege escalation;
* an unrecognised role is rejected rather than silently defaulted. A quiet
  downgrade hides the caller's mistake and a quiet upgrade is a security hole;
* an invite that omits ``role`` gets the least-privileged one.
"""

import pytest

from app.api.v1.endpoints import users
from app.models import UserRole

ASSIGNABLE_ROLES = users.ASSIGNABLE_ROLES


class TestAssignableRoles:
    """The role lookup table itself."""

    @pytest.mark.parametrize("name,role", sorted(ASSIGNABLE_ROLES.items()))
    def test_every_entry_is_a_real_enum_member(self, name: str, role: UserRole):
        # The original bug in one assertion: UserRole.USER was not a member.
        assert role in set(UserRole)
        assert role.value == name

    def test_superadmin_is_not_assignable(self):
        assert UserRole.SUPERADMIN not in ASSIGNABLE_ROLES.values()
        assert "superadmin" not in ASSIGNABLE_ROLES

    def test_covers_every_tenant_scoped_role(self):
        assert set(ASSIGNABLE_ROLES) == {
            role.value for role in UserRole if role is not UserRole.SUPERADMIN
        }

    def test_keys_are_lowercase(self):
        # Both call sites look up `role.lower()`.
        assert all(name == name.lower() for name in ASSIGNABLE_ROLES)

    @pytest.mark.parametrize("unknown", ["user", "superadmin", "owner", "", "Admin "])
    def test_unknown_role_has_no_entry(self, unknown: str):
        # "user" is the specific string the old default used.
        assert ASSIGNABLE_ROLES.get(unknown.lower()) is None

    def test_rejection_message_lists_the_assignable_roles(self):
        for name in ASSIGNABLE_ROLES:
            assert name in users.INVALID_ROLE_DETAIL
        assert "superadmin" not in users.INVALID_ROLE_DETAIL


class TestInviteDefaultRole:
    """An invite that omits `role` must not hand out access nobody asked for."""

    def test_default_role_is_assignable(self):
        default = users.InviteUserRequest(email="someone@example.com").role
        assert default in ASSIGNABLE_ROLES

    def test_default_role_is_least_privileged(self):
        assert users.InviteUserRequest(email="someone@example.com").role == "viewer"
        assert ASSIGNABLE_ROLES["viewer"] is UserRole.VIEWER
