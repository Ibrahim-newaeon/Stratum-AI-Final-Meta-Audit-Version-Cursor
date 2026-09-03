"""Bring the database to the current Alembic head (idempotent).

Three situations are handled:

* Empty database ......................... ``alembic upgrade head`` (creates everything)
* Tables exist but no ``alembic_version`` .. the schema was created from the models
  before migrations existed; it matches the baseline, so ``alembic stamp`` it and
  then upgrade to head
* Versioned database ..................... ``alembic upgrade head``

Run from ``backend/`` with DATABASE_URL_SYNC set (the container entrypoint does).
"""

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.core.config import settings

BASELINE_REVISION = "0001_baseline"


def main() -> int:
    """Stamp or upgrade the database; return 0 on success."""
    backend_dir = Path(__file__).resolve().parent
    alembic_cfg = Config(str(backend_dir / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "migrations"))

    engine = create_engine(settings.database_url_sync)
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
