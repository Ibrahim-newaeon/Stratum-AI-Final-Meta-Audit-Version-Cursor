#!/bin/sh
# Container entrypoint: prepare the database, optionally seed demo data, then serve.
#
# - Schema is created from the SQLAlchemy models (the Alembic history is incomplete).
# - Demo tenant/users and demo data are seeded only when SEED_DEMO=true (idempotent).
# - Binds on "::" (dual-stack) so Railway's IPv6 private network can reach the API,
#   and honours $PORT when the platform injects one.
set -e

python scripts_create_schema.py

if [ "${SEED_DEMO:-false}" = "true" ]; then
  python scripts_seed_demo.py
  python scripts_seed_demo_data.py
fi

exec uvicorn app.main:app --host "${UVICORN_HOST:-::}" --port "${PORT:-8000}" --workers "${WEB_CONCURRENCY:-1}"
