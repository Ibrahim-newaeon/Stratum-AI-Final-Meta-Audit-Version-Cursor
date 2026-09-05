"""Create the CAPI delivery telemetry tables that were never built.

Revision ID: 0004_capi_delivery_tables
Revises: 0003_meta_privacy_callbacks
Create Date: 2026-09-05

``app/models/capi_delivery.py`` declared four tables but was never imported by
``app.models``, so its metadata never reached the baseline's ``create_all`` and
the tables were never created in any deployed database. Nothing noticed while
the CAPI path appended to an in-memory list instead. The moment signal health
began reading ``capi_delivery_logs`` for real, every read raised
``UndefinedTableError`` and the dashboard endpoint returned 500.

The module is now registered in ``app.models``, which fixes fresh databases.
This revision fixes the ones that already ran the baseline.

Created from the model metadata with ``checkfirst=True``, so a database that
already has any of these tables is left alone and the revision is safe to
re-run.
"""

from alembic import op

import app.models  # noqa: F401  (registers every mapper on Base)
from app.db.base import Base

# revision identifiers, used by Alembic.
revision = "0004_capi_delivery_tables"
down_revision = "0003_meta_privacy_callbacks"
branch_labels = None
depends_on = None

TABLES: tuple[str, ...] = (
    "capi_delivery_logs",
    "capi_dead_letter_queue",
    "capi_event_dedupe",
    "capi_delivery_daily_stats",
)


def _tables() -> list:
    """Return the metadata objects for the tables this revision owns."""
    return [Base.metadata.tables[name] for name in TABLES]


def upgrade() -> None:
    """Create any of the four tables that does not exist yet."""
    Base.metadata.create_all(bind=op.get_bind(), tables=_tables(), checkfirst=True)


def downgrade() -> None:
    """Drop the four tables."""
    Base.metadata.drop_all(bind=op.get_bind(), tables=_tables(), checkfirst=True)
