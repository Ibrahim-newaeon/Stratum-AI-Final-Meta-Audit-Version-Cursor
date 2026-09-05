# =============================================================================
# Stratum AI - GDPR erasure service
# =============================================================================
"""
The single implementation of "erase this person's data".

This module holds the erasure that ``POST /api/v1/gdpr/anonymize`` has always
performed; the endpoint now calls it instead of inlining it. Anything that ever
needs to erase a person's account must call this function rather than write a
second version: two erasure paths drift, and a privacy promise kept on only one
of them is not kept.

Note what is **not** a caller. Meta's Data Deletion Request Callback
(``app.api.v1.endpoints.meta_callbacks``) deletes only what Meta gave us - the
platform connection, its tokens and the app-scoped Meta user id - and does not
come here. Anonymising a user deactivates the login irreversibly, and the
Stratum AI account is an email/password account of the tenant's own, not
Meta-derived data; an unauthenticated third-party callback must not be able to
lock a tenant out of its workspace. Account erasure stays behind the
authenticated endpoint, where the person asking has proved who they are.

What :func:`anonymize_user` covers, table by table:

===========================  ==============================================
``users``                    email, email_hash, full_name, phone and
                             avatar_url replaced with anonymised values;
                             ``is_active`` cleared, ``preferences`` emptied,
                             ``gdpr_anonymized_at`` stamped. The row itself is
                             kept so foreign keys and non-personal history stay
                             intact.
``notification_preferences`` deleted
``api_keys``                 deleted
``audit_logs``               ``ip_address`` and ``user_agent`` nulled; the
                             action history itself is retained, because it is
                             the compliance record of the erasure
===========================  ==============================================

What it deliberately does **not** touch: campaign, metric and billing history
(no personal data, and required for accounting), and the tenant's platform
credentials. A caller that also needs to sever a connection handles that
itself.

The function never commits. The caller owns the transaction, so an erasure and
whatever else it belongs with (an audit row, a deletion-request record, a
cleared token) land atomically or not at all.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.security import anonymize_pii
from app.models import APIKey, AuditAction, AuditLog, NotificationPreference, User

logger = get_logger(__name__)

__all__ = ["ErasureResult", "anonymize_user"]


@dataclass
class ErasureResult:
    """Outcome of one user erasure."""

    user_id: int
    anonymized_at: datetime
    tables_affected: list[str] = field(default_factory=list)
    records_modified: int = 0
    already_anonymized: bool = False


async def anonymize_user(
    db: AsyncSession,
    user: User,
    *,
    actor_user_id: int | None = None,
    reason: str = "gdpr_request",
    audit_context: dict[str, Any] | None = None,
) -> ErasureResult:
    """
    Anonymise one user's personal data and record the erasure in the audit log.

    Idempotent: a user who already carries ``gdpr_anonymized_at`` is returned
    unchanged with ``already_anonymized=True`` and no second audit row, so a
    retried callback cannot double-count or re-anonymise.

    Args:
        db: Open async session. **Not** committed by this function.
        user: The loaded user row to anonymise.
        actor_user_id: The user who requested the erasure, or ``None`` when the
            request came from outside the product (a Meta privacy callback).
        reason: Short machine-readable cause, stored on the audit row.
        audit_context: Extra non-personal fields to merge into the audit row's
            ``new_value`` (for example a deletion-request confirmation code).

    Returns:
        An :class:`ErasureResult` describing what was touched.
    """
    if user.gdpr_anonymized_at is not None:
        logger.info("gdpr_erasure_already_done", user_id=user.id, reason=reason)
        return ErasureResult(
            user_id=user.id,
            anonymized_at=user.gdpr_anonymized_at,
            already_anonymized=True,
        )

    tables_affected: list[str] = []
    records_modified = 0
    now = datetime.now(UTC)

    # --- users -------------------------------------------------------------
    user.email = anonymize_pii("email")
    user.email_hash = f"ANON_{user.id}"
    user.full_name = anonymize_pii("name")
    user.phone = None
    user.avatar_url = None
    user.is_active = False
    user.gdpr_anonymized_at = now
    user.preferences = {}

    tables_affected.append("users")
    records_modified += 1

    # --- notification_preferences -----------------------------------------
    result = await db.execute(
        delete(NotificationPreference).where(NotificationPreference.user_id == user.id)
    )
    if result.rowcount and result.rowcount > 0:
        tables_affected.append("notification_preferences")
        records_modified += result.rowcount

    # --- api_keys ----------------------------------------------------------
    result = await db.execute(delete(APIKey).where(APIKey.user_id == user.id))
    if result.rowcount and result.rowcount > 0:
        tables_affected.append("api_keys")
        records_modified += result.rowcount

    # --- audit_logs (keep the history, drop the identifying request context)
    result = await db.execute(
        update(AuditLog)
        .where(AuditLog.user_id == user.id)
        .values(ip_address=None, user_agent=None)
    )
    if result.rowcount and result.rowcount > 0:
        tables_affected.append("audit_logs")
        records_modified += result.rowcount

    # --- the erasure is itself an audited event ----------------------------
    new_value: dict[str, Any] = {
        "tables_affected": tables_affected,
        "records_modified": records_modified,
        "reason": reason,
    }
    if audit_context:
        new_value.update(audit_context)

    db.add(
        AuditLog(
            tenant_id=user.tenant_id,
            user_id=actor_user_id,
            action=AuditAction.ANONYMIZE,
            resource_type="user",
            resource_id=str(user.id),
            new_value=new_value,
        )
    )

    logger.info(
        "gdpr_user_anonymized",
        user_id=user.id,
        requested_by=actor_user_id,
        reason=reason,
        tables_affected=tables_affected,
        records_modified=records_modified,
    )

    return ErasureResult(
        user_id=user.id,
        anonymized_at=now,
        tables_affected=tables_affected,
        records_modified=records_modified,
    )
