"""Baseline schema.

Revision ID: 0001_baseline
Revises: None
Create Date: 2026-09-02

The migration history that preceded this baseline was incomplete (revisions
referenced files that no longer exist), so the schema is re-based here on the
SQLAlchemy models as of this revision. Upgrading an empty database creates every
table from the model metadata; databases that were created directly from the
models before this file existed should be stamped at this revision instead
(scripts_db_prepare.py does that automatically).

Every migration after this one must be a normal Alembic diff.
"""

from collections import Counter

from alembic import op

import app.models  # noqa: F401  (registers every mapper on Base)
from app.db.base import Base

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def _dedupe_indexes() -> None:
    """Drop duplicate index objects that share a name within one table."""
    for table in Base.metadata.sorted_tables:
        seen: set[str] = set()
        for index in list(table.indexes):
            if index.name in seen:
                table.indexes.discard(index)
            else:
                seen.add(index.name or "")


def upgrade() -> None:
    """Create every model-backed table that does not exist yet."""
    _dedupe_indexes()
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """Drop every model-backed table."""
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
