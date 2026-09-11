#!/usr/bin/env bash
# =============================================================================
# Stratum AI - Cloud Agent install (idempotent repository bootstrap)
# =============================================================================
# Runs after the repository is checked out. Prepares durable, source-derived
# state: system packages, the backend virtualenv + Python deps, frontend node
# modules, local .env files, and the PostgreSQL role/database/schema.
#
# Per-boot service startup lives in .cursor/start.sh, not here.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PG_VERSION=16
PG_SUPER="stratum"
PG_PASS="stratum_secure_password_2024"
PG_DB="stratum_ai"

echo "==> [1/6] System packages (PostgreSQL ${PG_VERSION}, Redis, Python venv)"
export DEBIAN_FRONTEND=noninteractive
if ! command -v psql >/dev/null 2>&1 || ! command -v redis-server >/dev/null 2>&1 \
   || ! dpkg -s python3.12-venv >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq \
    postgresql postgresql-contrib redis-server \
    python3.12-venv python3-dev
fi

echo "==> [2/6] Ensure PostgreSQL cluster is running (needed for schema setup)"
sudo pg_ctlcluster "${PG_VERSION}" main start 2>/dev/null || true
# Wait for the socket to accept connections.
for _ in $(seq 1 30); do
  if sudo -u postgres pg_isready -q; then break; fi
  sleep 1
done

echo "==> [3/6] Ensure PostgreSQL role and database exist"
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${PG_SUPER}') THEN
      CREATE ROLE ${PG_SUPER} LOGIN SUPERUSER PASSWORD '${PG_PASS}';
   END IF;
END
\$\$;
SQL
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${PG_DB}'" \
  | grep -q 1 || sudo -u postgres createdb -O "${PG_SUPER}" "${PG_DB}"

echo "==> [4/6] Backend virtualenv + Python dependencies"
cd "$REPO_ROOT/backend"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q

# Create backend/.env for local development if it is not already present.
if [ ! -f .env ]; then
  echo "    creating backend/.env with generated dev secrets"
  SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
  JWT_SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
  PII_ENCRYPTION_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')"
  cat > .env <<ENV
APP_ENV=development
DEBUG=true
LOG_LEVEL=INFO
LOG_FORMAT=console
SECRET_KEY=${SECRET_KEY}
JWT_SECRET_KEY=${JWT_SECRET_KEY}
PII_ENCRYPTION_KEY=${PII_ENCRYPTION_KEY}
DATABASE_URL=postgresql+asyncpg://${PG_SUPER}:${PG_PASS}@localhost:5432/${PG_DB}
DATABASE_URL_SYNC=postgresql://${PG_SUPER}:${PG_PASS}@localhost:5432/${PG_DB}
POSTGRES_USER=${PG_SUPER}
POSTGRES_PASSWORD=${PG_PASS}
POSTGRES_DB=${PG_DB}
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2
ML_PROVIDER=local
ML_MODELS_PATH=./ml_models
USE_MOCK_AD_DATA=true
MARKET_INTEL_PROVIDER=mock
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
ENV
fi

echo "==> [5/6] Apply database migrations (idempotent, brings DB to head)"
python scripts_db_prepare.py

echo "==> [6/6] Frontend dependencies"
cd "$REPO_ROOT/frontend"
npm install --no-audit --no-fund

echo "==> install complete"
