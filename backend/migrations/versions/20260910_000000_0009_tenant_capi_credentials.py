"""Create ``tenant_capi_credentials`` for durable encrypted CAPI tokens.

Revision ID: 0009_tenant_capi_credentials
Revises: 0008_user_social_identity
Create Date: 2026-09-10

Per-tenant Meta / WhatsApp Conversions API credentials used to live only in
process memory (``CAPIService.connectors`` / ``_capi_services``). A restart
dropped every connection and EMQ / signal health starved for attributed
``capi_delivery_logs``.

This revision adds one row per ``(tenant_id, platform)`` with Fernet-encrypted
token columns. Non-secret identifiers (pixel_id, phone ids) stay plaintext for
status UI. Inspector-guarded so ``scripts_db_prepare`` / re-runs are safe.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0009_tenant_capi_credentials"
down_revision = "0008_user_social_identity"
branch_labels = None
depends_on = None

TABLE = "tenant_capi_credentials"
UNIQUE = "uq_tenant_capi_credentials_tenant_platform"
INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ix_tenant_capi_credentials_tenant_id", ("tenant_id",)),
    ("ix_tenant_capi_credentials_platform", ("platform",)),
)


def upgrade() -> None:
    """Create the encrypted CAPI credentials table when missing."""
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
            sa.Column("platform", sa.String(length=50), nullable=False),
            sa.Column("pixel_id", sa.String(length=64), nullable=True),
            sa.Column("phone_number_id", sa.String(length=64), nullable=True),
            sa.Column("business_account_id", sa.String(length=64), nullable=True),
            sa.Column("access_token_encrypted", sa.Text(), nullable=True),
            sa.Column("webhook_verify_token_encrypted", sa.Text(), nullable=True),
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
            sa.UniqueConstraint("tenant_id", "platform", name=UNIQUE),
        )

    # Re-inspect after possible create so indexes are idempotent.
    inspector = sa.inspect(bind)
    if TABLE in set(inspector.get_table_names()):
        existing = {idx["name"] for idx in inspector.get_indexes(TABLE)}
        for name, columns in INDEXES:
            if name not in existing:
                op.create_index(name, TABLE, list(columns))


def downgrade() -> None:
    """Drop ``tenant_capi_credentials`` when present."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in set(inspector.get_table_names()):
        return

    existing = {idx["name"] for idx in inspector.get_indexes(TABLE)}
    for name, _ in INDEXES:
        if name in existing:
            op.drop_index(name, table_name=TABLE)

    op.drop_table(TABLE)
