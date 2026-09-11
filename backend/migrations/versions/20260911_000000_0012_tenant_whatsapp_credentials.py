"""Create ``tenant_whatsapp_credentials`` for Module G messaging tokens.

Revision ID: 0012_tenant_whatsapp_credentials
Revises: 0011_aud_sync_sched
Create Date: 2026-09-11

WhatsApp Cloud API messaging (Module G) previously read only global
``WHATSAPP_*`` env vars, so every tenant shared one phone number and token.
This revision adds one encrypted credential row per tenant. It is deliberately
separate from ``tenant_capi_credentials`` (Conversions API).

Inspector-guarded so ``scripts_db_prepare`` / re-runs are safe.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0012_tenant_whatsapp_credentials"
down_revision = "0011_aud_sync_sched"
branch_labels = None
depends_on = None

TABLE = "tenant_whatsapp_credentials"
UNIQUE = "uq_tenant_whatsapp_credentials_tenant"
INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ix_tenant_whatsapp_credentials_tenant_id", ("tenant_id",)),
)


def upgrade() -> None:
    """Create the encrypted WhatsApp messaging credentials table when missing."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if TABLE not in tables:
        op.create_table(
            TABLE,
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
                nullable=False,
            ),
            sa.Column(
                "tenant_id",
                sa.Integer(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("phone_number_id", sa.String(length=64), nullable=True),
            sa.Column("business_account_id", sa.String(length=64), nullable=True),
            sa.Column("display_phone_number", sa.String(length=32), nullable=True),
            sa.Column("access_token_encrypted", sa.Text(), nullable=True),
            sa.Column(
                "status",
                sa.String(length=50),
                nullable=False,
                server_default=sa.text("'disconnected'"),
            ),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_verify_message", sa.Text(), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.UniqueConstraint("tenant_id", name=UNIQUE),
        )

    inspector = sa.inspect(bind)
    if TABLE in set(inspector.get_table_names()):
        existing = {idx["name"] for idx in inspector.get_indexes(TABLE)}
        for name, columns in INDEXES:
            if name not in existing:
                op.create_index(name, TABLE, list(columns))


def downgrade() -> None:
    """Drop ``tenant_whatsapp_credentials`` when present."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in set(inspector.get_table_names()):
        return

    existing = {idx["name"] for idx in inspector.get_indexes(TABLE)}
    for name, _ in INDEXES:
        if name in existing:
            op.drop_index(name, table_name=TABLE)

    op.drop_table(TABLE)
