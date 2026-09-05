# =============================================================================
# Stratum AI - Cross-Tenant User Management Tests
# =============================================================================
"""
Tests for naming the tenant a user-management write acts on.

``invite_user``, ``update_user`` and ``delete_user`` used to read
``request.state.tenant_id`` and accept no target, so a platform-role operator
could administer nobody but their own colleagues, and the per-tenant team screen
had no way to say which tenant it meant. They now take an optional ``tenant_id``.

That widens what a SUPERADMIN token can write, so what is pinned here is the
narrowness of the rule rather than the feature:

* omitting the target, or naming your own tenant, behaves exactly as before;
* naming a *different* tenant is honoured only for SUPERADMIN;
* an ADMIN naming another tenant is refused - **not** silently redirected to
  their own, which is how a write aimed elsewhere lands at home unnoticed;
* the cross-tenant case leaves an audit row, against the tenant that was
  changed, carrying no PII.
"""

import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints.users import (
    record_cross_tenant_user_change,
    resolve_target_tenant,
)
from app.models import UserRole

OWN_TENANT = 1
OTHER_TENANT = 2


def _caller(role: str, tenant_id: int | None = OWN_TENANT):
    """A request whose identity TenantMiddleware would have set."""
    return SimpleNamespace(
        state=SimpleNamespace(role=role, tenant_id=tenant_id, user_id=99)
    )


# =============================================================================
# Who may name which tenant
# =============================================================================


def test_omitting_the_target_keeps_the_previous_behaviour():
    """No target named means the caller's own tenant, as before."""
    assert resolve_target_tenant(_caller(UserRole.ADMIN.value), None) == OWN_TENANT
    assert resolve_target_tenant(_caller(UserRole.SUPERADMIN.value), None) == OWN_TENANT


def test_naming_your_own_tenant_is_the_same_as_omitting_it():
    """Being explicit about your own tenant changes nothing."""
    assert (
        resolve_target_tenant(_caller(UserRole.ADMIN.value), OWN_TENANT) == OWN_TENANT
    )


def test_only_the_platform_role_may_name_another_tenant():
    """A SUPERADMIN administers any tenant; that is what the role is for."""
    resolved = resolve_target_tenant(_caller(UserRole.SUPERADMIN.value), OTHER_TENANT)

    assert resolved == OTHER_TENANT


def test_an_admin_naming_another_tenant_is_refused_not_redirected():
    """
    An ADMIN is an admin of one tenant, and gets a 403.

    Refused rather than quietly resolved to their own tenant: a silent fallback
    is how a write aimed at somebody else lands at home and nobody notices. The
    caller asked for something they may not have, and is told so.
    """
    with pytest.raises(HTTPException) as excinfo:
        resolve_target_tenant(_caller(UserRole.ADMIN.value), OTHER_TENANT)

    assert excinfo.value.status_code == 403


@pytest.mark.parametrize(
    "role", [UserRole.MANAGER.value, UserRole.ANALYST.value, UserRole.VIEWER.value]
)
def test_a_non_admin_may_not_manage_members_at_all(role: str):
    """
    The non-admin roles cannot administer members, own tenant included.

    The endpoints used to gate on ``role not in ["admin", "superadmin"]``; that
    check now lives in the resolver, so it is pinned here rather than lost.
    """
    with pytest.raises(HTTPException) as excinfo:
        resolve_target_tenant(_caller(role), None)

    assert excinfo.value.status_code == 403


def test_an_unauthenticated_caller_is_refused():
    """No identity, no write - 401 before any tenant question is asked."""
    anonymous = SimpleNamespace(
        state=SimpleNamespace(role=None, tenant_id=None, user_id=None)
    )

    with pytest.raises(HTTPException) as excinfo:
        resolve_target_tenant(anonymous, None)

    assert excinfo.value.status_code == 401


def test_a_caller_with_no_tenant_and_no_target_is_told_so():
    """
    A platform account with no tenant of its own must name one.

    Better a 400 naming the problem than a write that silently goes nowhere or,
    worse, to whichever tenant happens to be id 1.
    """
    with pytest.raises(HTTPException) as excinfo:
        resolve_target_tenant(_caller(UserRole.SUPERADMIN.value, tenant_id=None), None)

    assert excinfo.value.status_code == 400


def test_the_rule_is_the_one_every_other_tenant_write_uses():
    """
    The resolver defers to ``require_admin`` rather than restating the rule.

    Two copies of "who may administer this tenant" is how they drift apart, and
    the copy that drifts is usually the newer one.
    """
    source = inspect.getsource(resolve_target_tenant)

    assert "require_admin(request, requested)" in source


# =============================================================================
# The audit trail for a cross-tenant write
# =============================================================================


class _RecordingSession:
    """Captures what would have been persisted."""

    def __init__(self) -> None:
        """Start with nothing added."""
        self.added: list[object] = []

    def add(self, instance: object) -> None:
        """Record the row."""
        self.added.append(instance)


def _request(role: str, tenant_id: int | None):
    """A request with the client context the audit row reads."""
    return SimpleNamespace(
        state=SimpleNamespace(role=role, tenant_id=tenant_id, user_id=7),
        client=SimpleNamespace(host="203.0.113.7"),
        headers={"User-Agent": "pytest"},
        url=SimpleNamespace(path="/api/v1/users/invite"),
        method="POST",
    )


@pytest.mark.asyncio
async def test_a_same_tenant_change_writes_no_extra_audit_row():
    """
    A tenant administering its own members is ordinary, already-logged activity.

    Recording it here would bury the rows that matter under the rows that do
    not.
    """
    from app.models import AuditAction

    db = _RecordingSession()

    await record_cross_tenant_user_change(
        db=db,
        request=_request(UserRole.ADMIN.value, OWN_TENANT),
        action=AuditAction.CREATE,
        target_tenant_id=OWN_TENANT,
        caller_tenant_id=OWN_TENANT,
        subject_user_id=42,
    )

    assert db.added == []


@pytest.mark.asyncio
async def test_a_cross_tenant_change_is_recorded_against_the_tenant_changed():
    """
    The row belongs to the tenant whose member changed, not the operator's.

    The question it exists to answer is "who changed our people?", and that is
    asked from the affected tenant's side; a row filed under the operator's own
    tenant would be invisible to them.
    """
    from app.models import AuditAction

    db = _RecordingSession()

    await record_cross_tenant_user_change(
        db=db,
        request=_request(UserRole.SUPERADMIN.value, OWN_TENANT),
        action=AuditAction.DELETE,
        target_tenant_id=OTHER_TENANT,
        caller_tenant_id=OWN_TENANT,
        subject_user_id=42,
    )

    (row,) = db.added
    assert row.tenant_id == OTHER_TENANT
    assert row.user_id == 7
    assert row.resource_type == "user"
    assert row.resource_id == "42"
    assert row.new_value["acted_by_role"] == UserRole.SUPERADMIN.value
    assert row.new_value["acted_from_tenant_id"] == OWN_TENANT


@pytest.mark.asyncio
async def test_the_audit_row_carries_no_personal_data():
    """
    Only a domain, never an address.

    This row outlives the account it describes, and a GDPR erasure of that
    account should not have to chase the audit trail to stay complete.
    """
    from app.models import AuditAction

    db = _RecordingSession()

    await record_cross_tenant_user_change(
        db=db,
        request=_request(UserRole.SUPERADMIN.value, OWN_TENANT),
        action=AuditAction.CREATE,
        target_tenant_id=OTHER_TENANT,
        caller_tenant_id=OWN_TENANT,
        subject_user_id=42,
        detail={"email_domain": "example.com", "role": "viewer"},
    )

    (row,) = db.added
    serialised = str(row.new_value)
    assert "example.com" in serialised
    assert "@" not in serialised


def test_every_member_write_records_the_cross_tenant_case():
    """
    All three writes audit, not just the one that was easiest to remember.

    A gap here is a cross-tenant change nobody can account for afterwards.
    """
    from app.api.v1.endpoints import users as users_module

    for name in ("invite_user", "update_user", "delete_user"):
        source = inspect.getsource(getattr(users_module, name))
        assert "record_cross_tenant_user_change(" in source, name
        assert "resolve_target_tenant(" in source, name
