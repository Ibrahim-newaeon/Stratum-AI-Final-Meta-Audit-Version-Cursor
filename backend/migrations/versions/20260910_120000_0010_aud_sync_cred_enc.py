"""Encrypt audience_sync_credentials.access_token at rest.

Revision ID: 0010_aud_sync_cred_enc
Revises: 0009_tenant_capi_credentials
Create Date: 2026-09-10

Audience sync stored Meta System User tokens in plaintext
(``audience_sync_credentials.access_token``). This revision:

1. Adds ``access_token_encrypted``
2. Makes the legacy ``access_token`` column nullable
3. Copies existing plaintext through ``encrypt_token`` and clears it
4. Adds a unique index on (tenant_id, platform, ad_account_id) for upserts

Inspector-guarded for idempotent re-runs.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0010_aud_sync_cred_enc"
down_revision = "0009_tenant_capi_credentials"
branch_labels = None
depends_on = None

TABLE = "audience_sync_credentials"
ENCRYPTED_COL = "access_token_encrypted"
UNIQUE_INDEX = "ix_audience_sync_credentials_tenant_platform_account"


def upgrade() -> None:
    """Add encrypted column, migrate plaintext, clear legacy token."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in set(inspector.get_table_names()):
        return

    columns = {c["name"] for c in inspector.get_columns(TABLE)}
    if ENCRYPTED_COL not in columns:
        op.add_column(TABLE, sa.Column(ENCRYPTED_COL, sa.Text(), nullable=True))

    # Legacy plaintext becomes optional once ciphertext exists.
    op.alter_column(TABLE, "access_token", existing_type=sa.Text(), nullable=True)

    # Re-inspect indexes after possible column add.
    inspector = sa.inspect(bind)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes(TABLE)}
    if UNIQUE_INDEX not in existing_indexes:
        op.create_index(
            UNIQUE_INDEX,
            TABLE,
            ["tenant_id", "platform", "ad_account_id"],
            unique=True,
        )

    # Encrypt any remaining plaintext rows using the same Fernet helpers as the app.
    from app.services.encryption import encrypt_token

    rows = bind.execute(
        sa.text(
            f"SELECT id, access_token FROM {TABLE} "
            f"WHERE access_token IS NOT NULL "
            f"AND (access_token_encrypted IS NULL OR access_token_encrypted = '')"
        )
    ).fetchall()
    for row in rows:
        token = row[1]
        if not token:
            continue
        enc = encrypt_token(token)
        bind.execute(
            sa.text(
                f"UPDATE {TABLE} SET access_token_encrypted = :enc, access_token = NULL "
                f"WHERE id = :id"
            ),
            {"enc": enc, "id": row[0]},
        )


def downgrade() -> None:
    """Drop encrypted column and unique index (cannot restore plaintext)."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if TABLE not in set(inspector.get_table_names()):
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes(TABLE)}
    if UNIQUE_INDEX in existing_indexes:
        op.drop_index(UNIQUE_INDEX, table_name=TABLE)

    columns = {c["name"] for c in inspector.get_columns(TABLE)}
    if ENCRYPTED_COL in columns:
        op.drop_column(TABLE, ENCRYPTED_COL)

    # Leave access_token nullable — restoring NOT NULL would fail on cleared rows.
