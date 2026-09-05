"""Storage for the Meta App Review privacy callbacks.

Revision ID: 0003_meta_privacy_callbacks
Revises: 0002_widen_money_columns
Create Date: 2026-09-05

Meta App Review will not grant ``ads_read`` / ``ads_management`` without a
Deauthorize Callback and a Data Deletion Request Callback. Both are identified
by one thing only - the app-scoped Meta user id (ASID) inside the signed
request - so this revision adds the two pieces of state those callbacks need:

1. ``tenant_platform_connection.platform_user_id`` - the platform's own id for
   the person who authorised the connection, written at OAuth time from
   ``GET /me``. Without it a deauthorize callback has no way to know which
   connection to sever. Nullable and indexed, not unique: one Meta user can
   connect several tenants, and every connection that predates this column
   carries NULL.

   **Rows existing before this revision are not backfilled.** The ASID can only
   be read with that tenant's live token, which is a network call this migration
   must not make. Those connections keep working; they simply will not match a
   privacy callback until the tenant reconnects. Reconnecting is the operator
   remedy, and the callback answers 200 ``no_connection`` in the meantime rather
   than erroring.

2. ``meta_data_deletion_request`` - the durable record behind the confirmation
   code the deletion callback hands back to Meta, so the public status URL can
   keep reporting on it. It holds the ASID, the code, the outcome and
   timestamps; no name, email, phone or token.

Inspector-guarded and idempotent like 0002: every step checks what is already
present, so re-running on a database created from the model metadata (where
``scripts_db_prepare.py`` may already have made both) is a no-op.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0003_meta_privacy_callbacks"
down_revision = "0002_widen_money_columns"
branch_labels = None
depends_on = None


CONNECTION_TABLE = "tenant_platform_connection"
CONNECTION_COLUMN = "platform_user_id"
CONNECTION_INDEX = "ix_tenant_platform_connection_platform_user_id"

DELETION_TABLE = "meta_data_deletion_request"
DELETION_INDEXES: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("ix_meta_data_deletion_request_confirmation_code", ("confirmation_code",), True),
    ("ix_meta_data_deletion_request_meta_user_id", ("meta_user_id",), False),
    ("ix_meta_data_deletion_request_tenant_id", ("tenant_id",), False),
    (
        "ix_meta_data_deletion_request_meta_user",
        ("meta_user_id", "requested_at"),
        False,
    ),
)


def _tables(inspector: sa.Inspector) -> set[str]:
    """Return the set of table names currently present."""
    return set(inspector.get_table_names())


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    """Return the set of column names on ``table``."""
    return {column["name"] for column in inspector.get_columns(table)}


def _indexes(inspector: sa.Inspector, table: str) -> set[str]:
    """Return the set of index names on ``table``."""
    return {index["name"] for index in inspector.get_indexes(table)}


def upgrade() -> None:
    """Add the connection's platform user id and the deletion-request table."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = _tables(inspector)

    # --- 1. tenant_platform_connection.platform_user_id --------------------
    if CONNECTION_TABLE in tables:
        if CONNECTION_COLUMN not in _columns(inspector, CONNECTION_TABLE):
            op.add_column(
                CONNECTION_TABLE,
                sa.Column(CONNECTION_COLUMN, sa.String(length=64), nullable=True),
            )
        if CONNECTION_INDEX not in _indexes(inspector, CONNECTION_TABLE):
            op.create_index(CONNECTION_INDEX, CONNECTION_TABLE, [CONNECTION_COLUMN])

    # --- 2. meta_data_deletion_request -------------------------------------
    if DELETION_TABLE not in tables:
        op.create_table(
            DELETION_TABLE,
            sa.Column(
                "id",
                sa.dialects.postgresql.UUID(as_uuid=True),
                primary_key=True,
                nullable=False,
            ),
            sa.Column("confirmation_code", sa.String(length=64), nullable=False),
            sa.Column("meta_user_id", sa.String(length=64), nullable=False),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column(
                "connections_cleared", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        inspector = sa.inspect(bind)

    existing_indexes = _indexes(inspector, DELETION_TABLE)
    for name, columns, unique in DELETION_INDEXES:
        if name not in existing_indexes:
            op.create_index(name, DELETION_TABLE, list(columns), unique=unique)


def downgrade() -> None:
    """Drop the deletion-request table and the connection's platform user id."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = _tables(inspector)

    if DELETION_TABLE in tables:
        existing_indexes = _indexes(inspector, DELETION_TABLE)
        for name, _columns_, _unique in DELETION_INDEXES:
            if name in existing_indexes:
                op.drop_index(name, table_name=DELETION_TABLE)
        op.drop_table(DELETION_TABLE)

    if CONNECTION_TABLE in tables:
        if CONNECTION_INDEX in _indexes(inspector, CONNECTION_TABLE):
            op.drop_index(CONNECTION_INDEX, table_name=CONNECTION_TABLE)
        if CONNECTION_COLUMN in _columns(inspector, CONNECTION_TABLE):
            op.drop_column(CONNECTION_TABLE, CONNECTION_COLUMN)
