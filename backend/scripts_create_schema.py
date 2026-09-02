"""Create the database schema from the SQLAlchemy models (idempotent).

Used for fresh environments (local, Railway) where the Alembic history is
incomplete. Tables are created one at a time with duplicate index names
de-duplicated, so the script can be re-run safely on an existing database.
"""

import sys

from sqlalchemy import create_engine, inspect

import app.models  # noqa: F401  (registers every mapper on Base)
from app.core.config import settings
from app.db.base import Base


def _dedupe_indexes() -> None:
    """Drop duplicate index definitions that share a name within one table."""
    for table in Base.metadata.sorted_tables:
        seen: set[str] = set()
        for idx in list(table.indexes):
            if idx.name in seen:
                table.indexes.discard(idx)
            else:
                seen.add(idx.name or "")


def main() -> int:
    """Create all missing tables; return 0 on success."""
    engine = create_engine(settings.database_url_sync)
    _dedupe_indexes()
    failures: list[str] = []
    for table in Base.metadata.sorted_tables:
        try:
            table.create(engine, checkfirst=True)
        except Exception as exc:  # noqa: BLE001 - report and continue
            message = str(exc).splitlines()[0]
            if "already exists" in message:
                continue
            failures.append(f"{table.name}: {message[:160]}")
    count = len(inspect(engine).get_table_names())
    print(f"schema ready: {count} tables present")
    for failure in failures:
        print(f"WARNING {failure}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
