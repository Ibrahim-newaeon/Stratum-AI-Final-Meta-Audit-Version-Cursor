"""Widen the campaign money columns from int4 to int8.

Revision ID: 0002_widen_money_columns
Revises: 0001_baseline
Create Date: 2026-09-04

Why this migration exists (the baseline says every revision after it must be a
normal Alembic diff, and this is that diff):

``campaigns.*_cents`` and ``campaign_metrics.*_cents`` do not hold ISO-4217
minor units. They hold *hundredths of the ad account's major unit* for every
currency, because every reader in the codebase converts them with a bare
``/ 100`` that knows nothing about the currency
(``app/ml/forecaster.py``, ``app/api/v1/endpoints/tenant_dashboard.py``,
``Campaign.calculate_metrics``). That x100 is correct for display but it cuts
the int4 headroom by 100x for zero-decimal currencies:

    int4 max 2,147,483,647 hundredths = 21,474,836 major units
        USD  ~$21,474,836
        KRW  W21,474,836   ~US$15,900
        VND  d21,474,836   ~US$860

A Vietnamese or Korean advertiser therefore hits ``integer out of range`` at
an ordinary campaign spend, which aborts the whole ingestion transaction and
makes the Meta insights sync permanently fail for that campaign. int8 raises
the ceiling to ~9.2e16 major units, which is unreachable in any currency.

The cost-per-* columns are derived from ``total_spend_cents`` by
``Campaign.calculate_metrics()``, so they are widened with it.

Operationally this is an ``ALTER COLUMN ... TYPE bigint``, which rewrites both
tables on PostgreSQL. Run it during a maintenance window on a database that
already carries a lot of history.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002_widen_money_columns"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


#: (table, column, nullable) for every money column that scales by x100.
MONEY_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("campaigns", "daily_budget_cents", True),
    ("campaigns", "lifetime_budget_cents", True),
    ("campaigns", "total_spend_cents", False),
    ("campaigns", "revenue_cents", False),
    ("campaigns", "cpc_cents", True),
    ("campaigns", "cpm_cents", True),
    ("campaigns", "cpa_cents", True),
    ("campaign_metrics", "spend_cents", False),
    ("campaign_metrics", "revenue_cents", False),
)


def _alter(to_bigint: bool) -> None:
    """
    Retype every money column, skipping tables/columns that do not exist yet.

    Args:
        to_bigint: True to widen to 64-bit, False to narrow back to 32-bit.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())
    new_type = sa.BigInteger() if to_bigint else sa.Integer()

    for table, column, nullable in MONEY_COLUMNS:
        if table not in existing:
            continue
        if column not in {col["name"] for col in inspector.get_columns(table)}:
            continue
        op.alter_column(table, column, type_=new_type, existing_nullable=nullable)


def upgrade() -> None:
    """Widen the money columns to 64-bit."""
    _alter(True)


def downgrade() -> None:
    """Narrow the money columns back to 32-bit (may fail on large values)."""
    _alter(False)
