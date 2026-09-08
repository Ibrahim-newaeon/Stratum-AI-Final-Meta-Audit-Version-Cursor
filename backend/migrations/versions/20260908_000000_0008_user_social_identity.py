"""Facebook Login identity links.

Revision ID: 0008_user_social_identity
Revises: 0007_embed_widgets
Create Date: 2026-09-08

Adds the two pieces of state behind "Log in with Facebook":

1. ``user_social_identity`` - one row links a Stratum login to the Meta
   **app-scoped user id (ASID)** for that person, so a second sign-in resolves
   to the account that already exists instead of provisioning another tenant.
   It is also the row Meta's Data Deletion callback deletes, since the ASID is
   the only identifier that callback carries.

2. ``users.has_usable_password`` - False only for an account that social
   sign-in provisioned, which carries a random password nobody holds. Existing
   rows are backfilled to True, which is correct: every account that predates
   this revision was created by ``POST /auth/signup`` or ``POST /users/invite``
   with a password its owner chose. Without the flag, unlinking Facebook from
   such an account would silently remove its only way in.

Both directions are written out explicitly rather than driven from
``Base.metadata``. The baseline revision can use ``create_all``/``drop_all``
because it owns the whole schema; a single-table revision cannot, because
``drop_all`` reaches past the table it was handed and tries to drop enum types
other tables still use - ``downgrade()`` failed on ``DROP TYPE userrole`` until
this was spelled out. Writing the DDL here also pins the enum type name, so a
later rename of the Python class cannot silently orphan the type in Postgres.

Every step is inspector-guarded, so the revision is a no-op on a database where
``scripts_db_prepare.py`` already created the table or the column, and it is
safe to re-run.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0008_user_social_identity"
down_revision = "0007_embed_widgets"
branch_labels = None
depends_on = None


IDENTITY_TABLE = "user_social_identity"
USERS_TABLE = "users"
PASSWORD_FLAG_COLUMN = "has_usable_password"

#: Postgres enum backing ``UserSocialIdentity.provider``. Meta-only by design;
#: see the model for why this must not grow a non-Meta provider.
PROVIDER_ENUM_NAME = "socialprovider"
PROVIDER_VALUES = ("facebook",)

IDENTITY_INDEXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ix_user_social_identity_user_id", ("user_id",)),
    ("ix_user_social_identity_tenant_id", ("tenant_id",)),
)


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    """Return the set of column names on ``table``."""
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    """Create ``user_social_identity`` and add the usable-password flag."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if IDENTITY_TABLE not in tables:
        # create_type=False plus an explicit CREATE TYPE keeps the enum
        # idempotent: create_table would otherwise re-issue CREATE TYPE and
        # fail on a database where the type already exists.
        provider_enum = postgresql.ENUM(
            *PROVIDER_VALUES, name=PROVIDER_ENUM_NAME, create_type=False
        )
        provider_enum.create(bind, checkfirst=True)

        op.create_table(
            IDENTITY_TABLE,
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("tenant_id", sa.Integer(), nullable=False),
            sa.Column("provider", provider_enum, nullable=False),
            sa.Column("provider_user_id", sa.String(length=64), nullable=False),
            sa.Column(
                "granted_scopes",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
            ),
            sa.Column(
                "linked_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["user_id"],
                ["users.id"],
                name=op.f("fk_user_social_identity_user_id_users"),
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id"],
                ["tenants.id"],
                name=op.f("fk_user_social_identity_tenant_id_tenants"),
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id", name=op.f("pk_user_social_identity")),
            # One Facebook account -> one Stratum login, platform-wide. The
            # ASID is global to the app, so this key is deliberately not
            # tenant-scoped: the sign-in lookup runs before any tenant context
            # exists and could not use a tenant-scoped key to answer.
            sa.UniqueConstraint(
                "provider",
                "provider_user_id",
                name="uq_social_identity_provider_user",
            ),
            # One Stratum login -> at most one identity per provider.
            sa.UniqueConstraint(
                "provider", "user_id", name="uq_social_identity_provider_account"
            ),
        )
        for index_name, index_columns in IDENTITY_INDEXES:
            op.create_index(index_name, IDENTITY_TABLE, list(index_columns))

    if USERS_TABLE in tables and PASSWORD_FLAG_COLUMN not in _columns(
        inspector, USERS_TABLE
    ):
        # server_default backfills every existing row to True in the same
        # statement; the model carries the same default, so a row inserted by
        # the ORM and a row inserted by raw SQL agree.
        op.add_column(
            USERS_TABLE,
            sa.Column(
                PASSWORD_FLAG_COLUMN,
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )


def downgrade() -> None:
    """Drop the flag and the identity table, unlinking every Facebook login."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if USERS_TABLE in tables and PASSWORD_FLAG_COLUMN in _columns(
        inspector, USERS_TABLE
    ):
        op.drop_column(USERS_TABLE, PASSWORD_FLAG_COLUMN)

    if IDENTITY_TABLE in tables:
        existing_indexes = {
            index["name"] for index in inspector.get_indexes(IDENTITY_TABLE)
        }
        for index_name, _ in IDENTITY_INDEXES:
            if index_name in existing_indexes:
                op.drop_index(index_name, table_name=IDENTITY_TABLE)
        op.drop_table(IDENTITY_TABLE)

    # Dropped only after its one consumer is gone, and only this type - never
    # via metadata drop_all, which reaches enums other tables still depend on.
    postgresql.ENUM(*PROVIDER_VALUES, name=PROVIDER_ENUM_NAME, create_type=False).drop(
        bind, checkfirst=True
    )
