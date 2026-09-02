"""Migrate the ``tenants`` billing columns and the webhook ledger to Paddle Billing (idempotent).

Run once: cd backend && .venv/bin/python scripts_migrate_paddle_columns.py

The live schema was created from the SQLAlchemy models with ``create_all``
(no Alembic), so the Paddle changes are applied directly through
``settings.database_url_sync`` (``DATABASE_URL_SYNC``):

- ``tenants.paddle_customer_id VARCHAR(255)``: the legacy payment-gateway
  customer-id column (``<gateway>_customer_id``) is renamed when it still
  exists; when both columns exist the legacy values are copied into empty
  ``paddle_customer_id`` slots and the legacy column is dropped; when neither
  exists the column is added.
- ``tenants.paddle_subscription_id VARCHAR(255)``,
  ``tenants.subscription_status VARCHAR(32)``,
  ``tenants.current_period_end TIMESTAMPTZ``: added when missing.
- index ``ix_tenants_paddle_customer`` on ``tenants(paddle_customer_id)``.
- table ``paddle_webhook_events`` (webhook idempotency ledger) via
  ``Base.metadata.create_all(checkfirst=True)``.

Every step checks ``information_schema`` first, so re-running is a no-op.
Exit code 0 on success, 1 on failure (the whole run is one transaction).
"""

import re
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection

import app.models  # noqa: F401  (registers every mapper on Base)
from app.base_models import PaddleWebhookEvent
from app.core.config import settings
from app.db.base import Base

TENANTS = "tenants"
PADDLE_CUSTOMER_COLUMN = "paddle_customer_id"
PADDLE_CUSTOMER_INDEX = "ix_tenants_paddle_customer"
WEBHOOK_TABLE = PaddleWebhookEvent.__table__

# Columns added alongside paddle_customer_id (name -> SQL type).
NEW_TENANT_COLUMNS: dict[str, str] = {
    "paddle_subscription_id": "VARCHAR(255)",
    "subscription_status": "VARCHAR(32)",
    "current_period_end": "TIMESTAMPTZ",
}

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


def _quote(identifier: str) -> str:
    """Return a safely double-quoted SQL identifier (validated against a strict pattern)."""
    if not _IDENTIFIER.match(identifier):
        raise ValueError(f"unsafe SQL identifier: {identifier!r}")
    return f'"{identifier}"'


def _column_exists(conn: Connection, table: str, column: str) -> bool:
    """Return True when ``table.column`` exists in the current schema."""
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = :table AND column_name = :column"
        ),
        {"table": table, "column": column},
    ).first()
    return row is not None


def _table_exists(conn: Connection, table: str) -> bool:
    """Return True when ``table`` exists in the current schema."""
    row = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = :table"
        ),
        {"table": table},
    ).first()
    return row is not None


def _index_exists(conn: Connection, index: str) -> bool:
    """Return True when an index named ``index`` exists in the current schema."""
    row = conn.execute(
        text(
            "SELECT 1 FROM pg_indexes "
            "WHERE schemaname = current_schema() AND indexname = :index"
        ),
        {"index": index},
    ).first()
    return row is not None


def _legacy_customer_columns(conn: Connection) -> list[str]:
    """
    Return the legacy ``<gateway>_customer_id`` column(s) on ``tenants``.

    Any column ending in ``_customer_id`` other than ``paddle_customer_id`` is
    treated as the previous payment gateway's customer reference.
    """
    rows = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :table "
            "AND column_name ~ '^[a-z]+_customer_id$' "
            "AND column_name <> :paddle_column "
            "ORDER BY column_name"
        ),
        {"table": TENANTS, "paddle_column": PADDLE_CUSTOMER_COLUMN},
    ).scalars().all()
    return [str(name) for name in rows]


def migrate_customer_column(conn: Connection) -> None:
    """Ensure ``tenants.paddle_customer_id`` exists (rename / merge / add as appropriate)."""
    if not _table_exists(conn, TENANTS):
        raise RuntimeError("tenants table not found - create the schema first (scripts_create_schema.py)")

    has_paddle = _column_exists(conn, TENANTS, PADDLE_CUSTOMER_COLUMN)
    legacy_columns = _legacy_customer_columns(conn)
    legacy = legacy_columns[0] if len(legacy_columns) == 1 else None
    if len(legacy_columns) > 1:
        print(
            f"warning  several legacy customer-id columns on {TENANTS}: {legacy_columns} "
            "- not renaming/dropping any of them"
        )

    if legacy and not has_paddle:
        conn.execute(
            text(
                f"ALTER TABLE {_quote(TENANTS)} RENAME COLUMN {_quote(legacy)} "
                f"TO {_quote(PADDLE_CUSTOMER_COLUMN)}"
            )
        )
        print(f"renamed  {TENANTS}.{legacy} -> {PADDLE_CUSTOMER_COLUMN}")
        return

    if legacy and has_paddle:
        copied = conn.execute(
            text(
                f"UPDATE {_quote(TENANTS)} "
                f"SET {_quote(PADDLE_CUSTOMER_COLUMN)} = {_quote(legacy)} "
                f"WHERE {_quote(PADDLE_CUSTOMER_COLUMN)} IS NULL AND {_quote(legacy)} IS NOT NULL"
            )
        ).rowcount
        conn.execute(text(f"ALTER TABLE {_quote(TENANTS)} DROP COLUMN {_quote(legacy)}"))
        print(
            f"merged   {TENANTS}.{legacy} -> {PADDLE_CUSTOMER_COLUMN} "
            f"({copied} value(s) copied); dropped {legacy}"
        )
        return

    if has_paddle:
        print(f"skipped  {TENANTS}.{PADDLE_CUSTOMER_COLUMN} (already exists)")
        return

    conn.execute(
        text(
            f"ALTER TABLE {_quote(TENANTS)} "
            f"ADD COLUMN IF NOT EXISTS {_quote(PADDLE_CUSTOMER_COLUMN)} VARCHAR(255)"
        )
    )
    print(f"added    {TENANTS}.{PADDLE_CUSTOMER_COLUMN} VARCHAR(255)")


def add_new_tenant_columns(conn: Connection) -> None:
    """Add the subscription tracking columns to ``tenants`` when missing."""
    for column, sql_type in NEW_TENANT_COLUMNS.items():
        if _column_exists(conn, TENANTS, column):
            print(f"skipped  {TENANTS}.{column} (already exists)")
            continue
        conn.execute(
            text(
                f"ALTER TABLE {_quote(TENANTS)} "
                f"ADD COLUMN IF NOT EXISTS {_quote(column)} {sql_type}"
            )
        )
        print(f"added    {TENANTS}.{column} {sql_type}")


def create_customer_index(conn: Connection) -> None:
    """Create ``ix_tenants_paddle_customer`` when missing."""
    if _index_exists(conn, PADDLE_CUSTOMER_INDEX):
        print(f"skipped  index {PADDLE_CUSTOMER_INDEX} (already exists)")
        return
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS {_quote(PADDLE_CUSTOMER_INDEX)} "
            f"ON {_quote(TENANTS)} ({_quote(PADDLE_CUSTOMER_COLUMN)})"
        )
    )
    print(f"created  index {PADDLE_CUSTOMER_INDEX} on {TENANTS}({PADDLE_CUSTOMER_COLUMN})")


def create_webhook_table(conn: Connection) -> None:
    """Create ``paddle_webhook_events`` from the model when missing."""
    name = WEBHOOK_TABLE.name
    if name in set(inspect(conn).get_table_names()):
        print(f"skipped  {name} (already exists)")
        return
    Base.metadata.create_all(bind=conn, tables=[WEBHOOK_TABLE], checkfirst=True)
    if not _table_exists(conn, name):  # pragma: no cover - should not happen
        raise RuntimeError(f"{name} was not created")
    print(f"created  {name}")


def main() -> int:
    """Apply every Paddle schema step inside one transaction; return the exit code."""
    engine = create_engine(settings.database_url_sync)
    try:
        with engine.begin() as conn:
            migrate_customer_column(conn)
            add_new_tenant_columns(conn)
            create_customer_index(conn)
            create_webhook_table(conn)
    except Exception as exc:  # noqa: BLE001 - report and fail
        print(f"ERROR    paddle migration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    print("Paddle billing columns are ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
