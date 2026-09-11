#!/usr/bin/env bash
# =============================================================================
# Stratum AI - Cloud Agent start (per-boot service reconciliation)
# =============================================================================
# Starts the infrastructure daemons the application needs on every boot:
# PostgreSQL and Redis. Idempotent: safe when a service is already running.
# The application processes themselves (API, Celery worker, Vite) run as
# visible terminals defined in .cursor/environment.json.
set -euo pipefail

PG_VERSION=16

echo "==> Starting Redis"
if ! redis-cli ping >/dev/null 2>&1; then
  sudo redis-server /etc/redis/redis.conf --daemonize yes
fi

echo "==> Starting PostgreSQL ${PG_VERSION}"
sudo pg_ctlcluster "${PG_VERSION}" main start 2>/dev/null || true

echo "==> Waiting for services to accept connections"
for _ in $(seq 1 30); do
  if redis-cli ping >/dev/null 2>&1 && sudo -u postgres pg_isready -q; then
    echo "==> Redis and PostgreSQL are ready"
    exit 0
  fi
  sleep 1
done

echo "ERROR: Redis and/or PostgreSQL did not become ready in time" >&2
exit 1
