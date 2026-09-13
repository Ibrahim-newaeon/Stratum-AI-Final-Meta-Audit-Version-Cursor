"""Add country_code and tracked_platforms to competitor_benchmarks.

Revision ID: 0014_comp_country_platforms
Revises: 0013_custom_autopilot_rules
Create Date: 2026-09-13

The Add Competitor UI already collects country + platforms, but
``add_competitor`` only persisted name/domain. Persist the filter
inputs so Meta Ads Library links and cards use the saved country.

Revision id kept at <= 32 chars for ``alembic_version.version_num``.
Linearized under ``0013_custom_autopilot_rules`` (was a branched head
off ``0011_aud_sync_sched`` as ``0012_competitor_country_platforms``).
Inspector-guarded for idempotent re-runs.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_comp_country_platforms"
down_revision = "0013_custom_autopilot_rules"
branch_labels = None
depends_on = None

TABLE = "competitor_benchmarks"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if TABLE not in tables:
        return

    columns = {c["name"] for c in inspector.get_columns(TABLE)}
    if "country_code" not in columns:
        op.add_column(
            TABLE,
            sa.Column("country_code", sa.String(length=10), nullable=True),
        )
    if "tracked_platforms" not in columns:
        op.add_column(
            TABLE,
            sa.Column("tracked_platforms", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if TABLE not in tables:
        return

    columns = {c["name"] for c in inspector.get_columns(TABLE)}
    if "tracked_platforms" in columns:
        op.drop_column(TABLE, "tracked_platforms")
    if "country_code" in columns:
        op.drop_column(TABLE, "country_code")
