#!/bin/sh
# Container entrypoint for every backend process. SERVICE_ROLE selects what runs:
#
#   api    (default) prepare the database, optionally seed demo data, serve the API
#   worker           Celery worker with the embedded beat scheduler
#
# - The database is prepared with Alembic (scripts_db_prepare.py): databases that
#   were created from the models before migrations existed are stamped at the
#   baseline; everything else is upgraded to head.
# - Demo tenant/users and demo data are seeded only when SEED_DEMO=true (idempotent).
# - serve.py binds one dual-stack socket (IPv4 for Railway's public proxy, IPv6 for
#   its private network) and honours $PORT when the platform injects one.
# - Run exactly one worker with --beat; scale worker throughput with
#   CELERY_CONCURRENCY rather than extra beat-enabled replicas.
set -e

ROLE="${SERVICE_ROLE:-api}"

case "$ROLE" in
  api)
    python scripts_db_prepare.py
    if [ "${SEED_DEMO:-false}" = "true" ]; then
      python scripts_seed_demo.py
      python scripts_seed_demo_data.py
    fi
    exec python serve.py
    ;;
  worker)
    exec celery -A app.workers.celery_app worker --beat \
      --loglevel "${CELERY_LOG_LEVEL:-info}" \
      --concurrency "${CELERY_CONCURRENCY:-2}"
    ;;
  *)
    echo "Unknown SERVICE_ROLE '$ROLE' (expected api or worker)" >&2
    exit 1
    ;;
esac
