# =============================================================================
# Stratum AI - Social login identity models
# =============================================================================
"""
Persistence for "Log in with Facebook".

One row links a Stratum :class:`~app.base_models.User` to the identity Meta
returns for that person, so a later sign-in resolves to the same account
instead of minting a second one.

What the identifier is
----------------------
``provider_user_id`` is Meta's **app-scoped user id (ASID)** - the value
``GET /me`` returns for this app, and the same value the Deauthorize and Data
Deletion callbacks carry inside their ``signed_request``. It is meaningless
outside this one app, which is what makes it safe to store and what lets
``app.api.v1.endpoints.meta_callbacks`` sever the link when Meta asks.

Uniqueness and why it is global
-------------------------------
The ASID is global to the app, not to a tenant, so ``(provider,
provider_user_id)`` is globally unique: one Facebook account resolves to
exactly one Stratum login. The reverse constraint ``(provider, user_id)`` is
unique too - an account carries at most one Facebook identity. ``tenant_id`` is
carried for tenant-scoped cleanup and reporting, and is deliberately *not* part
of either key: the sign-in lookup happens before any tenant context exists, so
a tenant-scoped key could not be used to answer it.

Privacy shape
-------------
This table stores the ASID, the granted scopes and timestamps. It stores **no**
name, email, phone, picture or access token. The person's email and name live
on ``users`` where they are already encrypted at rest; the Facebook access
token is used once, server-side, to verify the sign-in and is never persisted.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base, TimestampMixin

if TYPE_CHECKING:
    from app.base_models import User

__all__ = [
    "SocialProvider",
    "UserSocialIdentity",
]


class SocialProvider(str, enum.Enum):
    """
    Identity providers accepted for social sign-in.

    Meta-only by design, matching the activation boundary in CLAUDE.md: this
    enum must not grow a non-Meta provider (Google sign-in in particular is
    explicitly out of scope).
    """

    FACEBOOK = "facebook"


class UserSocialIdentity(Base, TimestampMixin):
    """
    A Stratum login's link to one Meta app-scoped identity.

    Created either by a first Facebook sign-in that provisioned the account, or
    by an authenticated user linking Facebook from their settings. Deleted when
    the person unlinks, when Meta's Deauthorize callback fires for the ASID, or
    when Meta's Data Deletion callback asks for erasure.
    """

    __tablename__ = "user_social_identity"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: Owning login. CASCADE: the link is meaningless without the account.
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    #: Denormalised from ``users.tenant_id`` for tenant-scoped cleanup. Not part
    #: of any uniqueness key - the sign-in lookup runs with no tenant context.
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )

    provider: Mapped[SocialProvider] = mapped_column(
        Enum(SocialProvider, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )

    #: Meta's app-scoped user id (ASID). Never a phone, email or raw Facebook id.
    provider_user_id: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Scopes Meta reported as granted at the last sign-in, for support triage.
    #: A list of strings; never a token.
    granted_scopes: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship("User")

    __table_args__ = (
        # One Facebook account -> one Stratum login, platform-wide.
        UniqueConstraint(
            "provider", "provider_user_id", name="uq_social_identity_provider_user"
        ),
        # One Stratum login -> at most one identity per provider.
        UniqueConstraint(
            "provider", "user_id", name="uq_social_identity_provider_account"
        ),
        Index("ix_user_social_identity_user_id", "user_id"),
        Index("ix_user_social_identity_tenant_id", "tenant_id"),
    )
