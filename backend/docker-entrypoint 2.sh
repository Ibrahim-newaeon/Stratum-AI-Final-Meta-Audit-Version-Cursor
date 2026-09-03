#!/bin/sh
# Container entrypoint: prepare the database, optionally seed demo data, then serve.
#
# - Schema is created from the SQLAlchemy models (the Alembic history is incomplete).
# - Demo tenant/users and demo data are seeded only when SEED_DEMO=true (idempotent).
# - serve.py binds one dual-stack socket (IPv4 for Railway's public proxy, IPv6 for
#   its private network) and honours $PORT when the platform injects one.
set -e

python scripts_create_schema.py

if [ "${SEED_DEMO:-false}" = "true" ]; then
  python scripts_seed_demo.py
  python scripts_seed_demo_data.py
fi

exec python serve.py
