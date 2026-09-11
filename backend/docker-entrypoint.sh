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
    # Sample .pkl artifacts for local inference (idempotent; skip if present).
    # Opt in with BOOTSTRAP_ML_MODELS=true, or automatically with SEED_DEMO.
    if [ "${BOOTSTRAP_ML_MODELS:-false}" = "true" ] || [ "${SEED_DEMO:-false}" = "true" ]; then
      python scripts_bootstrap_ml_models.py || echo "warning: ML model bootstrap failed (predictions may be unavailable)"
    fi
    exec python serve.py
    ;;
  worker)
    # A worker only consumes the queues it is started with. Every task in this
    # app is routed to a named queue, so without -Q the worker would drain only
    # Celery's built-in "celery" queue and nothing scheduled would ever run.
    # The list is derived from the routing table and the beat schedule
    # (app.workers.celery_app.CELERY_QUEUES) so a new route cannot go
    # unconsumed; tests/unit/test_worker_queue_coverage.py guards it.
    #
    # The derivation lives in scripts_print_celery_queues.py because importing
    # the Celery app logs to stdout (structlog PrintLoggerFactory); inlining a
    # `python -c` here glued that log line onto the first queue name, Celery
    # split the result on commas and the "cdp" queue was never consumed. The
    # script keeps stdout to the queue list alone and fails loudly otherwise.
    QUEUES="$(python scripts_print_celery_queues.py)"
    # Defence in depth: never start a worker on a queue list that does not look
    # like one, however it came to be malformed.
    case "$QUEUES" in
      "" | *[!a-zA-Z0-9_,.-]*)
        echo "Refusing to start: derived queue list is not a queue list: [$QUEUES]" >&2
        exit 1
        ;;
    esac
    echo "Celery worker consuming queues: ${QUEUES}"
    # Beat persists its schedule with shelve; /app is not writable by appuser,
    # so keep the file in /tmp (it is rebuilt from beat_schedule on start).
    exec celery -A app.workers.celery_app worker --beat \
      --queues "${QUEUES}" \
      --schedule /tmp/celerybeat-schedule \
      --loglevel "${CELERY_LOG_LEVEL:-info}" \
      --concurrency "${CELERY_CONCURRENCY:-2}"
    ;;
  *)
    echo "Unknown SERVICE_ROLE '$ROLE' (expected api or worker)" >&2
    exit 1
    ;;
esac
