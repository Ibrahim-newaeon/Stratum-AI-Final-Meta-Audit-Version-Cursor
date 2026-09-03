"""Bring the database to the current Alembic head (idempotent).

Three situations are handled:

* Empty database ......................... ``alembic upgrade head`` (creates everything)
* Tables exist but no ``alembic_version`` .. the schema was created from the models
  before migrations existed; it matches the baseline, so ``alembic stamp`` it and
  then upgrade to head
* Versioned database ..................... ``alembic upgrade head``

Run from ``backend/`` with DATABASE_URL_SYNC set (the container entrypoint does).
"""

import os
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.config import settings

BASELINE_REVISION = "0001_baseline"


def _maybe_reset(engine) -> None:  # type: ignore[no-untyped-def]
    """Drop the public schema when DB_RESET_CONFIRM names this database.

    A deliberate, one-shot guard for turning a seeded/staging database into a
    clean one: set DB_RESET_CONFIRM to ``RESET-<database name>``, deploy once,
    then remove the variable. Anything else (unset, wrong name) is ignored.
    """
    confirm = os.environ.get("DB_RESET_CONFIRM", "")
    db_name = make_url(settings.database_url_sync).database or ""
    if not confirm:
        return
    if confirm != f"RESET-{db_name}":
        print(
            f"db-prepare: DB_RESET_CONFIRM does not match RESET-{db_name}; ignoring",
            file=sys.stderr,
            flush=True,
        )
        return
    print(f"db-prepare: RESETTING database {db_name} (DB_RESET_CONFIRM matched)", flush=True)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))


def main() -> int:
    """Stamp or upgrade the database; return 0 on success."""
    backend_dir = Path(__file__).resolve().parent
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))

    engine = create_engine(settings.database_url_sync)
    _maybe_reset(engine)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    engine.dispose()

    if tables and "alembic_version" not in tables:
        print(
            f"db-prepare: {len(tables)} tables without alembic_version; "
            f"stamping {BASELINE_REVISION}",
            flush=True,
        )
        command.stamp(alembic_cfg, BASELINE_REVISION)

    command.upgrade(alembic_cfg, "head")
    print("db-prepare: database at head", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
