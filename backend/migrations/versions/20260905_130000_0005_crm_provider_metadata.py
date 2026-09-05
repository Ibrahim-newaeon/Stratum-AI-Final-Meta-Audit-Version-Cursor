"""Provider-specific connection metadata for CRM connections.

Revision ID: 0005_crm_provider_metadata
Revises: 0004_capi_delivery_tables
Create Date: 2026-09-05

Two CRM clients cannot address a tenant's account without a per-connection
value the provider only hands out at OAuth time:

* Salesforce returns an ``instance_url`` with the token exchange. Every REST
  call is built as ``{instance_url}/services/data/{version}{endpoint}``, so
  without it ``SalesforceClient.api_request`` logs ``salesforce_no_instance_url``
  and returns None for every request.
* Pipedrive returns an ``api_domain`` per account.

Both clients already read and write that value on the connection - Salesforce
under ``raw_properties``, Pipedrive under ``provider_metadata`` - but the column
never existed on ``crm_connections``, so both raised ``AttributeError``. This
revision adds it once, as ``provider_metadata``, and the Salesforce client was
moved onto that name in the same change. ``raw_properties`` stays what it has
always been on ``crm_contacts`` / ``crm_deals``: the raw CRM record.

Nullable JSONB with no backfill. The values can only be obtained from a live
token exchange, which this migration must not make. Connections that predate it
carry NULL and keep the behaviour they have today - Salesforce and Pipedrive
calls fail until the tenant reconnects, which is the operator remedy.

Inspector-guarded and idempotent like 0002 and 0003, so re-running against a
database created straight from the model metadata (where
``scripts_db_prepare.py`` may already have added the column) is a no-op.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0005_crm_provider_metadata"
down_revision = "0004_capi_delivery_tables"
branch_labels = None
depends_on = None


TABLE = "crm_connections"
COLUMN = "provider_metadata"


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    """Return the set of column names on ``table``."""
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    """Add ``crm_connections.provider_metadata``."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if TABLE not in set(inspector.get_table_names()):
        return

    if COLUMN not in _columns(inspector, TABLE):
        op.add_column(TABLE, sa.Column(COLUMN, postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    """Drop ``crm_connections.provider_metadata``."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if TABLE not in set(inspector.get_table_names()):
        return

    if COLUMN in _columns(inspector, TABLE):
        op.drop_column(TABLE, COLUMN)
