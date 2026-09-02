"""Create the Measurement & Verification tables (GA4 read-only baseline + GTM tag deployment).

Run once: cd backend && .venv/bin/python scripts_create_measurement_tables.py

Creates (idempotently, ``checkfirst=True``; no Alembic migration):
- tenant_ga4_integrations  - per-tenant GA4 property + encrypted service account
- tenant_gtm_integrations  - per-tenant GTM web container / server-side tagging endpoint
- fact_ga4_daily           - daily GA4 sessions / conversions / purchase revenue baseline

Safe to re-run: existing tables are skipped. Requires the ``pgcrypto`` /
PostgreSQL 13+ ``gen_random_uuid()`` function (available by default on PG13+).
"""

import asyncio
import sys

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

import app.models  # noqa: F401  (registers all mappers / metadata)
from app.db.base_class import Base
from app.db.session import async_engine
from app.models.measurement import (
    FactGA4Daily,
    TenantGA4Integration,
    TenantGTMIntegration,
)

MEASUREMENT_TABLES = [
    TenantGA4Integration.__table__,
    TenantGTMIntegration.__table__,
    FactGA4Daily.__table__,
]


def _existing_table_names(conn: Connection) -> set[str]:
    """Return the set of table names currently present in the public schema."""
    return set(inspect(conn).get_table_names())


def _create_measurement_tables(conn: Connection) -> None:
    """Create only the measurement tables, skipping any that already exist."""
    Base.metadata.create_all(bind=conn, tables=MEASUREMENT_TABLES, checkfirst=True)


async def main() -> int:
    """Create the measurement tables and report what was created vs skipped."""
    async with async_engine.begin() as conn:
        before = await conn.run_sync(_existing_table_names)
        await conn.run_sync(_create_measurement_tables)
        after = await conn.run_sync(_existing_table_names)
    await async_engine.dispose()

    for table in MEASUREMENT_TABLES:
        name = table.name
        if name in before:
            print(f"skipped  {name} (already exists)")
        elif name in after:
            print(f"created  {name}")
        else:  # pragma: no cover - should not happen
            print(f"MISSING  {name} (create_all did not create it)")
            return 1
    print("Measurement tables are ready. Restart celery worker + beat to load the GA4 schedule.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
