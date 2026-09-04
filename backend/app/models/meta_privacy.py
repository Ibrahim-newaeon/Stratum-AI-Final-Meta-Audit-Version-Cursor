# =============================================================================
# Stratum AI - Meta privacy callback models
# =============================================================================
"""
Persistence for Meta's Data Deletion Request Callback.

Meta requires the callback to answer with a ``confirmation_code`` and a status
``url``, and requires that URL to keep reporting on the request afterwards. So
the request has to be durable: :class:`MetaDataDeletionRequest` is that record.

Privacy shape of this table: it stores the *app-scoped* Meta user id (ASID) -
an identifier that is meaningless outside this one app - the confirmation code,
and the outcome. It stores no name, email, phone or token. The confirmation
code is the only key the public status endpoint accepts, and it is generated
from ``secrets`` so it cannot be guessed or enumerated.
"""

import enum
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.db.base_class import Base

__all__ = [
    "DataDeletionStatus",
    "MetaDataDeletionRequest",
]


class DataDeletionStatus(str, enum.Enum):
    """Lifecycle of one Meta data deletion request."""

    #: Accepted and recorded, erasure not finished (a retry will finish it).
    PENDING = "pending"
    #: Erasure ran to completion. Also used when nothing was held for the user:
    #: there is nothing left to delete either way, and saying so is honest.
    COMPLETED = "completed"
    #: Erasure was attempted and failed; an operator must finish it.
    FAILED = "failed"


class MetaDataDeletionRequest(Base):
    """
    One data deletion request received from Meta for one Meta user.

    Rows are never deleted: the confirmation code has to keep resolving on the
    public status page for as long as the person may check it.
    """

    __tablename__ = "meta_data_deletion_request"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    #: Unguessable code handed back to Meta and shown to the person. The only
    #: credential the public status endpoint accepts.
    confirmation_code = Column(String(64), nullable=False, unique=True, index=True)

    #: Meta's app-scoped user id (ASID) from the verified signed_request.
    meta_user_id = Column(String(64), nullable=False, index=True)

    #: Tenant whose connection matched, when one did. Null means the Meta user
    #: matched no connection - which is not an error, only nothing to erase.
    tenant_id = Column(
        Integer,
        ForeignKey("tenants.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    status = Column(
        String(32), nullable=False, default=DataDeletionStatus.PENDING.value
    )

    #: How many platform connections this request severed. Operator counter,
    #: and the one thing the public status page consults - a request that
    #: matched nothing must not be described as having erased something.
    connections_cleared = Column(Integer, nullable=False, default=0)

    #: Failure cause for operators. Never contains a token or the app secret.
    last_error = Column(Text, nullable=True)

    requested_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_meta_data_deletion_request_meta_user", "meta_user_id", "requested_at"
        ),
    )
