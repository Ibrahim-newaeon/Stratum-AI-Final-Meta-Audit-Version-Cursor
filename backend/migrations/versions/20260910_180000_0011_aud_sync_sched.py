"""Add audience sync schedule trigger columns and due index.

Revision ID: 0011_aud_sync_sched
Revises: 0010_aud_sync_cred_enc
Create Date: 2026-09-10

``AudienceSyncService.sync_platform_audience`` already writes
``triggered_by`` / ``triggered_by_user_id`` on ``audience_sync_jobs``, but
those columns were missing from the table. Celery beat also needs a
composite index on ``platform_audiences (auto_sync, next_sync_at)`` so the
due-queue scan stays cheap.

Inspector-guarded for idempotent re-runs.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_aud_sync_sched"
down_revision = "0010_aud_sync_cred_enc"
branch_labels = None
depends_on = None

JOBS_TABLE = "audience_sync_jobs"
AUDIENCES_TABLE = "platform_audiences"
DUE_INDEX = "ix_platform_audiences_auto_sync_due"


def upgrade() -> None:
    """Add job trigger columns and the auto-sync due index."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if JOBS_TABLE in tables:
        columns = {c["name"] for c in inspector.get_columns(JOBS_TABLE)}
        if "triggered_by" not in columns:
            op.add_column(
                JOBS_TABLE,
                sa.Column("triggered_by", sa.String(length=50), nullable=True),
            )
        if "triggered_by_user_id" not in columns:
            op.add_column(
                JOBS_TABLE,
                sa.Column("triggered_by_user_id", sa.Integer(), nullable=True),
            )

    if AUDIENCES_TABLE in tables:
        existing_indexes = {
            idx["name"] for idx in inspector.get_indexes(AUDIENCES_TABLE)
        }
        if DUE_INDEX not in existing_indexes:
            op.create_index(
                DUE_INDEX,
                AUDIENCES_TABLE,
                ["auto_sync", "next_sync_at"],
                unique=False,
            )


def downgrade() -> None:
    """Drop the due index and job trigger columns."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if AUDIENCES_TABLE in tables:
        existing_indexes = {
            idx["name"] for idx in inspector.get_indexes(AUDIENCES_TABLE)
        }
        if DUE_INDEX in existing_indexes:
            op.drop_index(DUE_INDEX, table_name=AUDIENCES_TABLE)

    if JOBS_TABLE in tables:
        columns = {c["name"] for c in inspector.get_columns(JOBS_TABLE)}
        if "triggered_by_user_id" in columns:
            op.drop_column(JOBS_TABLE, "triggered_by_user_id")
        if "triggered_by" in columns:
            op.drop_column(JOBS_TABLE, "triggered_by")
