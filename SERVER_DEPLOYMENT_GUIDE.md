# Stratum AI - Server Deployment Guide

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Environment Configuration](#3-environment-configuration)
4. [Local Development Setup](#4-local-development-setup)
5. [Production Deployment](#5-production-deployment)
6. [Beta Deployment (DigitalOcean)](#6-beta-deployment-digitalocean)
7. [Database & Migrations](#7-database--migrations)
8. [Celery Workers & Scheduled Tasks](#8-celery-workers--scheduled-tasks)
9. [Monitoring Stack](#9-monitoring-stack)
10. [SSL & Security](#10-ssl--security)
11. [CI/CD Pipeline](#11-cicd-pipeline)
12. [Operations & Maintenance](#12-operations--maintenance)
13. [Troubleshooting](#13-troubleshooting)
14. [Resource Requirements](#14-resource-requirements)
15. [Port Reference](#15-port-reference)

---

## 1. Architecture Overview

```
                        Internet
                           |
                      [Nginx / CDN]
                       /         \
              [Frontend]       [API Gateway]
             (React/Vite)      (FastAPI)
                                  |
                    +-------------+-------------+
                    |             |             |
               [PostgreSQL]   [Redis]    [Celery Workers]
                  + PgBouncer    |         + Beat Scheduler
                              [Flower]
                            (monitoring)
```

### Services

| Service | Technology | Purpose |
|---------|-----------|---------|
| Frontend | React 18 + Vite + Nginx | SPA with Tailwind CSS |
| API | FastAPI + Uvicorn | REST API + WebSocket |
| Database | PostgreSQL 16 | Primary data store |
| Cache/Queue | Redis 7 | Caching, sessions, Celery broker |
| Workers | Celery | Background task processing |
| Scheduler | Celery Beat | Periodic/scheduled tasks |
| Connection Pool | PgBouncer | DB connection pooling (prod only) |
| Monitoring | Prometheus + Grafana | Metrics & dashboards |
| Task Monitor | Flower | Celery task monitoring |

---

## 2. Prerequisites

### Required Software

| Tool | Version | Purpose |
|------|---------|---------|
| Docker | 24+ | Container runtime |
| Docker Compose | v2+ | Multi-container orchestration |
| Git | 2.40+ | Source control |
| Node.js | 20+ | Frontend build (local dev) |
| Python | 3.11+ | Backend (local dev) |

### System Requirements

| Environment | CPU | RAM | Disk |
|------------|-----|-----|------|
| Development | 2 cores | 4 GB | 20 GB |
| Beta/Staging | 2 vCPU | 4 GB | 40 GB |
| Production | 4+ vCPU | 8+ GB | 100+ GB |

---

## 3. Environment Configuration

### Step 1: Copy the environment template

```bash
cp .env.example .env
```

### Step 2: Generate secure keys (REQUIRED)

```bash
# Generate 3 unique secret keys (min 32 characters each)
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set these in `.env`:

```env
SECRET_KEY=<generated-key-1>
JWT_SECRET_KEY=<generated-key-2>
PII_ENCRYPTION_KEY=<generated-key-3>
```

### Step 3: Configure database credentials

```env
POSTGRES_USER=stratum
POSTGRES_PASSWORD=<strong-password>
POSTGRES_DB=stratum_ai
POSTGRES_HOST=db
POSTGRES_PORT=5432

DATABASE_URL=postgresql+asyncpg://stratum:<password>@db:5432/stratum_ai
DATABASE_URL_SYNC=postgresql://stratum:<password>@db:5432/stratum_ai
```

### Step 4: Configure Redis

```env
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2
```

### Step 5: Configure CORS & Frontend URL

```env
# Development
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
VITE_API_BASE_URL=http://localhost:8000

# Production
CORS_ORIGINS=https://yourdomain.com
VITE_API_BASE_URL=https://yourdomain.com
```

### Step 6: Configure ad platforms (optional for dev)

```env
USE_MOCK_AD_DATA=true   # Set to false in production

# Fill in when connecting Meta (Facebook, Instagram, WhatsApp):
META_APP_ID=
META_APP_SECRET=
META_ACCESS_TOKEN=
# ... (see .env.example for full list)
```

### Step 7: Configure Measurement & Verification (optional, read-only)

Google Analytics 4 is used strictly as an **independent, read-only revenue/conversion baseline**
(GA4 Data API + per-tenant service account, scope `https://www.googleapis.com/auth/analytics.readonly`).
Google Tag Manager is used strictly for **tag deployment** (web container for Meta Pixel + the Stratum
snippet, server-side tagging endpoint for Meta CAPI + the CDP `sgtm` source). Neither is an ad platform.

Only the GLOBAL toggles live in `.env`. GA4 property IDs, service-account keys and GTM container IDs are
configured per tenant in the app under **Settings > Integrations > Measurement & Verification**.

```env
# Measurement & Verification (read-only, NOT an ad platform)
GA4_SYNC_ENABLED=true
GA4_LOOKBACK_DAYS=3
GA4_BACKFILL_DAYS=30
GA4_REQUEST_TIMEOUT_SECONDS=30
GA4_DEFAULT_CONVERSION_EVENT=purchase
GTM_VERIFY_TIMEOUT_SECONDS=10
GTM_DEFAULT_SERVER_CONTAINER_URL=
```

Then, once per environment, create the measurement tables (idempotent, safe to re-run; no Alembic migration
is involved):

```bash
cd backend && .venv/bin/python scripts_create_measurement_tables.py
```

Finally restart the Celery worker **and** beat so the new task module and beat entries are loaded:

```bash
docker compose restart worker scheduler
# or, without Docker: restart the `celery ... worker` and `celery ... beat` processes
```

The API (`uvicorn --reload`) and the frontend (Vite) pick up the code changes automatically.

### Step 8: Configure Paddle Billing (optional)

Stratum bills tenant subscriptions through **Paddle Billing** (Merchant of Record): Paddle hosts the
checkout (Paddle.js overlay inside Settings > Billing), collects payment, handles tax and invoices, and
reports back through signed webhooks. Billing is optional — with `PADDLE_API_KEY` empty the Billing tab
shows "Billing is not configured for this environment" and everything else keeps working. Use a **sandbox**
account for development/staging and a separate production account for live billing (keys, price ids and
notification destinations do not carry over between the two).

```env
# Paddle Billing (optional)
PADDLE_ENVIRONMENT=sandbox          # sandbox | production (must be production when APP_ENV=production)
PADDLE_API_KEY=
PADDLE_CLIENT_TOKEN=
PADDLE_WEBHOOK_SECRET=
PADDLE_STARTER_PRICE_ID=
PADDLE_PROFESSIONAL_PRICE_ID=
PADDLE_ENTERPRISE_PRICE_ID=
```

In the Paddle dashboard (sandbox: `https://sandbox-vendors.paddle.com`, production: `https://vendors.paddle.com`):

1. **API key** — Developer Tools > Authentication > API keys > *New API key*. Paste it into `PADDLE_API_KEY`.
   The backend talks to `https://sandbox-api.paddle.com` or `https://api.paddle.com` (chosen from
   `PADDLE_ENVIRONMENT`) with `Authorization: Bearer <key>` and `Paddle-Version: 1`.
2. **Client-side token** — Developer Tools > Authentication > Client-side tokens. Paste it into
   `PADDLE_CLIENT_TOKEN`. The SPA reads it from `GET /api/v1/billing/config` and passes it to
   `Paddle.Initialize`; never put it in a frontend env file.
3. **Recurring prices** — Catalog > Products: one product per tier (Starter, Professional, Enterprise) with
   a recurring monthly price. Trials are configured on the price itself. Copy each `pri_...` id into
   `PADDLE_STARTER_PRICE_ID`, `PADDLE_PROFESSIONAL_PRICE_ID` and `PADDLE_ENTERPRISE_PRICE_ID`.
4. **Notification destination** — Developer Tools > Notifications > *New destination*: type **Webhook**,
   URL `https://<api-host>/api/v1/webhooks/paddle`, subscribed to `subscription.*`, `transaction.*`,
   `customer.*` and `adjustment.created`. Copy the destination's **secret key** into
   `PADDLE_WEBHOOK_SECRET`. Stratum verifies the `Paddle-Signature: ts=<unix>;h1=<hex>` header as
   HMAC-SHA256 over `<ts>:<raw body>` with a 5-minute timestamp tolerance, so keep the API host's clock
   NTP-synced. Deliveries are idempotent (`paddle_webhook_events`), so replaying from Paddle >
   Notifications > Logs is always safe.
5. Restart the API (and worker) so the settings load, then confirm `GET /api/v1/billing/config` returns
   `paddle_configured: true` and open Settings > Billing to run a sandbox checkout.

**Scripted catalogue + webhook (Railway):** steps 3 and 4 are automated by
`backend/scripts_paddle_bootstrap.py`. Put the API key (step 1) in the service variables, then run
`railway run -e <env> --service api -- python scripts_paddle_bootstrap.py --dry-run` followed by the same
command with `--webhook-url https://<api-host>/api/v1/webhooks/paddle --railway-env <env> --railway-service api`
and redeploy. It creates or reuses the tier products, the monthly prices and the notification destination,
writes the `pri_` ids and (for a newly created destination) `PADDLE_WEBHOOK_SECRET` into Railway (the secret
piped through `railway variable set --stdin`, never as a command-line argument), refuses to write a sandbox
run into `production` or a production run anywhere else, and never prints a secret. Details, flags and exit
codes: `docs/integrations/billing-paddle.md`, "Bootstrap the catalogue and webhook".

**Going live:** set `PADDLE_ENVIRONMENT=production`, replace all six other values with the production
account's, and create the notification destination again in the production dashboard. With
`APP_ENV=production` the app refuses to start when an API key is set but `PADDLE_ENVIRONMENT` is still
`sandbox` or a companion key is missing.

**CSP:** `frontend/nginx.conf`, `nginx/beta.conf` and the API's `build_csp` already allow
`https://cdn.paddle.com` (script) and `https://*.paddle.com` (connect + frame). If you front the app with
another proxy, replicate those hosts or the checkout overlay will be blocked.

**Upgrading an existing deployment:** the `tenants` table gained `paddle_customer_id`,
`paddle_subscription_id`, `subscription_status` and `current_period_end`, and a `paddle_webhook_events`
table was added. The schema is created from the SQLAlchemy models, so after pulling the new code run,
once per environment, from `backend/`:

```bash
cd backend && python scripts_migrate_paddle_columns.py
# Docker: docker compose exec api python scripts_migrate_paddle_columns.py
```

`scripts_migrate_paddle_columns.py` is idempotent: it renames the legacy billing customer-id column to
its Paddle name when present, adds any missing columns and creates the webhook table (uses
`DATABASE_URL_SYNC`). Fresh databases already have the right shape.

### Key Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | Yes | App secret (min 32 chars) |
| `JWT_SECRET_KEY` | Yes | JWT signing key (min 32 chars) |
| `PII_ENCRYPTION_KEY` | Yes | Encryption for PII data (min 32 chars) |
| `DATABASE_URL` | Yes | Async PostgreSQL connection string |
| `REDIS_URL` | Yes | Redis connection string |
| `APP_ENV` | Yes | `development`, `staging`, or `production` |
| `USE_MOCK_AD_DATA` | No | `true` for dev, `false` for prod |
| `SENTRY_DSN` | No | Sentry error tracking URL |
| `SUBSCRIPTION_TIER` | No | `starter`, `professional`, `enterprise` |
| `LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_FORMAT` | No | `json` (prod) or `console` (dev) |
| `GA4_SYNC_ENABLED` | No | `true` to run the daily GA4 read-only baseline pull (default `true`) |
| `GA4_LOOKBACK_DAYS` | No | Days re-pulled on each daily GA4 sync (default `3`) |
| `GA4_BACKFILL_DAYS` | No | Days pulled on a manual backfill sync (default `30`) |
| `GA4_REQUEST_TIMEOUT_SECONDS` | No | GA4 Data API request timeout in seconds (default `30`) |
| `GA4_DEFAULT_CONVERSION_EVENT` | No | GA4 event counted as a conversion when a tenant sets none (default `purchase`) |
| `GTM_VERIFY_TIMEOUT_SECONDS` | No | Timeout for verifying GTM web/server containers (default `10`) |
| `GTM_DEFAULT_SERVER_CONTAINER_URL` | No | Optional default sGTM endpoint suggested to tenants (per-tenant value wins) |
| `PADDLE_ENVIRONMENT` | No | `sandbox` (default) or `production`; selects `sandbox-api.paddle.com` vs `api.paddle.com` and the Paddle.js environment (must be `production` when `APP_ENV=production`) |
| `PADDLE_API_KEY` | No | Paddle Billing server-side API key (Developer Tools > Authentication); empty disables billing |
| `PADDLE_CLIENT_TOKEN` | No | Paddle client-side token for the Paddle.js overlay checkout, served via `GET /api/v1/billing/config` |
| `PADDLE_WEBHOOK_SECRET` | No | Notification destination secret used to verify `Paddle-Signature` on `POST /api/v1/webhooks/paddle` |
| `PADDLE_STARTER_PRICE_ID` | No | Recurring Paddle price id (`pri_...`) for the Starter tier |
| `PADDLE_PROFESSIONAL_PRICE_ID` | No | Recurring Paddle price id (`pri_...`) for the Professional tier |
| `PADDLE_ENTERPRISE_PRICE_ID` | No | Recurring Paddle price id (`pri_...`) for the Enterprise tier |

---

## 4. Local Development Setup

### Option A: Full Stack with Docker Compose (Recommended)

```bash
# Clone the repository
git clone https://github.com/Ibrahim-newaeon/Stratum-AI-Final-Updates-Dec-2025.git
cd Stratum-AI-Final-Updates-Dec-2025

# Configure environment
cp .env.example .env
# Edit .env with your settings

# Start all services
docker compose up -d

# Verify services are healthy
docker compose ps

# View logs
docker compose logs -f
```

**Access points:**
- Frontend: http://localhost:5173
- API: http://localhost:8000
- API Docs: http://localhost:8000/docs
- Flower (if enabled): http://localhost:5555

### Option B: Frontend-Only Development

If you only need the frontend (connecting to an external API):

```bash
docker compose -f docker-compose.dev.yml up -d
```

Or without Docker:

```bash
cd frontend
npm install --legacy-peer-deps
npm run dev
```

### Option C: Backend-Only Development

```bash
cd backend
pip install -r requirements.txt

# Start database and Redis via Docker
docker compose up -d db redis

# Run migrations
alembic upgrade head

# Start the API
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Start Celery worker (separate terminal)
celery -A app.workers.celery_app worker --loglevel=info

# Start Celery beat (separate terminal)
celery -A app.workers.celery_app beat --loglevel=info
```

### Verify Development Setup

```bash
# Check API health
curl http://localhost:8000/health

# Check all containers
docker compose ps

# Check logs for errors
docker compose logs api --tail=50
docker compose logs worker --tail=50
```

---

## 5. Production Deployment

### Step 1: Prepare production environment file

```bash
cp .env.production.example .env
# Edit with production values (strong keys, real credentials, production URLs)
```

**Critical production settings:**
```env
APP_ENV=production
DEBUG=false
LOG_LEVEL=WARNING
LOG_FORMAT=json
USE_MOCK_AD_DATA=false
```

### Step 2: Build and start production services

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

### Step 3: Run database migrations

```bash
docker compose exec api alembic upgrade head
```

### Step 4: Create the first admin user

```bash
docker compose exec api python -m app.scripts.create_admin
```

### Step 5: Verify deployment

```bash
# Check all services are running
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps

# Verify API health
curl -f http://localhost:8000/health

# Verify frontend
curl -f http://localhost:80/health

# Check Redis
docker compose exec redis redis-cli ping

# Check PostgreSQL
docker compose exec db pg_isready -U stratum
```

### Production Architecture Differences

| Feature | Development | Production |
|---------|-------------|------------|
| Frontend | Vite dev server (:5173) | Nginx serving built assets (:80) |
| API workers | 1 (with --reload) | 4 (no reload) |
| Celery concurrency | 4 | 2 (memory-safe) |
| PostgreSQL | Default config | Tuned (shared_buffers=1GB) |
| Redis maxmemory | 256 MB | 512 MB |
| PgBouncer | Not used | Transaction pooling (port 6432) |
| Log level | INFO | WARNING |
| Resource limits | None | CPU/Memory caps per service |
| Worker memory | Unlimited | 400MB per child (auto-restart) |

---

## 6. Beta Deployment (DigitalOcean)

### Target: 4GB RAM / 2 vCPU Droplet with Managed PostgreSQL

### Initial Server Setup

```bash
# SSH into your droplet
ssh root@your-server-ip

# Clone and run setup
git clone https://github.com/Ibrahim-newaeon/Stratum-AI-Final-Updates-Dec-2025.git /opt/stratum-ai
cd /opt/stratum-ai

# Run the deployment script
chmod +x scripts/deploy-beta.sh
./scripts/deploy-beta.sh setup
```

### Configure SSL

```bash
./scripts/deploy-beta.sh ssl
```

### Deploy

```bash
# Configure .env with production values
cp .env.production.example .env
nano .env

# Deploy all services
./scripts/deploy-beta.sh deploy
```

### Deployment Script Commands

| Command | Purpose |
|---------|---------|
| `./scripts/deploy-beta.sh setup` | Initial server setup (Docker, Certbot, clone) |
| `./scripts/deploy-beta.sh ssl` | Get/renew SSL certificate |
| `./scripts/deploy-beta.sh deploy` | Full deployment with build |
| `./scripts/deploy-beta.sh update` | Zero-downtime update |
| `./scripts/deploy-beta.sh status` | Health check all services |
| `./scripts/deploy-beta.sh logs [service]` | View container logs |
| `./scripts/deploy-beta.sh backup` | Backup Redis + config |
| `./scripts/deploy-beta.sh rollback` | Restore from backup |
| `./scripts/deploy-beta.sh migrate` | Run DB migrations |
| `./scripts/deploy-beta.sh create-admin` | Create admin user |
| `./scripts/deploy-beta.sh restart` | Restart all services |
| `./scripts/deploy-beta.sh shell [service]` | Shell into container |

---

## 7. Database & Migrations

### Alembic Configuration

- Config file: `backend/alembic.ini`
- Migrations directory: `backend/migrations/versions/`
- Timezone: UTC

### Common Commands

```bash
# Run all pending migrations
docker compose exec api alembic upgrade head

# Check current migration status
docker compose exec api alembic current

# View migration history
docker compose exec api alembic history

# Create a new migration
docker compose exec api alembic revision --autogenerate -m "description"

# Rollback one migration
docker compose exec api alembic downgrade -1
```

### Database Init Script

The file `scripts/init-db.sql` runs automatically on first PostgreSQL container start.

### PostgreSQL Production Tuning

Applied via `docker-compose.prod.yml`:
```
shared_buffers=1GB
effective_cache_size=3GB
work_mem=16MB
maintenance_work_mem=256MB
max_connections=50
```

### PgBouncer (Production Only)

- Port: 6432
- Pool mode: Transaction
- Max client connections: 200
- Default pool size: 25

---

## 8. Celery Workers & Scheduled Tasks

### Task Queues

| Queue | Tasks | Schedule |
|-------|-------|----------|
| `default` | CMS, WhatsApp | As triggered |
| `sync` | Campaign sync | Every hour |
| `rules` | Rule evaluation | Every 15 min |
| `intel` | Competitor data | Every 6 hours |
| `ml` | Predictions, forecasts | Every 30 min / Daily |
| `cdp` | Segments, funnels | Hourly / Every 2 hours |

### Beat Schedule Summary

| Task | Frequency |
|------|-----------|
| Evaluate rules | Every 15 minutes |
| Sync campaigns | Hourly |
| CDP segment computation | Hourly |
| ML predictions | Every 30 minutes |
| CDP funnel computation | Every 2 hours |
| Competitor data refresh | Every 6 hours |
| Usage rollup | Daily at 01:00 |
| Cost allocation | Daily at 02:00 |
| Signal health rollup | 02:00 UTC |
| GA4 daily baseline pull | 02:30 UTC |
| Attribution variance rollup (Meta vs GA4) | 03:00 UTC |
| Creative fatigue scores | Daily at 03:00 |
| Daily scores | Daily at 04:00 |
| Daily forecasts | Daily at 06:00 |
| Pipeline health check | Every 30 minutes |
| Audit log processing | Every minute |
| WhatsApp scheduled messages | Every minute |
| CMS scheduled posts | Every minute |

### Worker Safety Settings (Production)

```
worker_max_memory_per_child = 400MB  (auto-restart on exceed)
worker_max_tasks_per_child  = 1000   (recycle after 1000 tasks)
task_time_limit            = 600s    (10 min hard limit)
task_soft_time_limit       = 540s    (9 min soft limit)
result_expires             = 3600s   (1 hour result TTL)
```

---

## 9. Monitoring Stack

### Enable Monitoring

```bash
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d
```

### Services

| Service | URL | Credentials |
|---------|-----|-------------|
| Prometheus | http://localhost:9090 | None |
| Grafana | http://localhost:3001 | Set via `GF_SECURITY_ADMIN_USER/PASSWORD` |
| Flower | http://localhost:5555 | None (enable via `--profile monitoring`) |

### Prometheus Scrape Targets

- `stratum-api` on port 8000 (`/metrics`)
- `stratum-worker` on port 8000 (`/metrics`)
- Self (Prometheus) on port 9090

### Enable Flower (Celery Monitor)

```bash
docker compose --profile monitoring up -d flower
```

---

## 10. SSL & Security

### SSL with Let's Encrypt (Beta)

```bash
./scripts/deploy-beta.sh ssl
```

Auto-renewal is configured via cron job.

### Security Headers (Nginx)

The production Nginx config (`frontend/nginx.conf`) includes:

- `X-Frame-Options: SAMEORIGIN`
- `X-Content-Type-Options: nosniff`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Content-Security-Policy` — allow-lists the Paddle.js hosts (`https://cdn.paddle.com` in `script-src`, `https://*.paddle.com` in `connect-src` and `frame-src`, which also covers the `sandbox-*` hosts), Sentry, and the GTM/GA4 measurement hosts; the host list is identical to the API's `build_csp` (`backend/app/middleware/security.py`) and `nginx/beta.conf`
- `Permissions-Policy` (camera, mic, geolocation disabled; `payment` limited to `self` and the Paddle checkout origins `https://buy.paddle.com` / `https://sandbox-buy.paddle.com`)

### Production Key Validation

The app **will not start** in production if:
- Any secret key is less than 32 characters
- Database password is weak (e.g., "changeme", "password")
- Keys contain known weak patterns ("dev-secret", "test", "demo")
- Paddle Billing still pointed at the sandbox (`PADDLE_ENVIRONMENT=sandbox`) while `APP_ENV=production`, or `PADDLE_API_KEY` is set without its companion `PADDLE_CLIENT_TOKEN` / `PADDLE_WEBHOOK_SECRET` (billing is optional: leave `PADDLE_API_KEY` empty to disable it)

---

## 11. CI/CD Pipeline

### GitHub Actions Workflows

#### CI Pipeline (`.github/workflows/ci.yml`)

Triggers on push to `main`/`develop` and pull requests.

| Job | Steps |
|-----|-------|
| Backend CI | Linting (ruff), Formatting (black), Import sorting (isort), Type checking (mypy), Unit tests, Integration tests, Coverage (70% threshold) |
| Frontend CI | npm ci, ESLint, TypeScript check, Unit tests (vitest), Build |
| E2E Tests | Playwright (Chromium) - PR only |
| Security Scan | Trivy vulnerability scanner (CRITICAL + HIGH) |
| Load Tests | k6 smoke test - main branch only |

#### Docker Build (`.github/workflows/docker.yml`)

Triggers on push to `main`, tags (`v*`), and PRs.

- Builds backend + frontend Docker images
- Pushes to GitHub Container Registry (`ghcr.io`)
- Runs compose integration test on PRs

### Image Tags

```
ghcr.io/org/stratum-ai-backend:main
ghcr.io/org/stratum-ai-backend:v1.0.0
ghcr.io/org/stratum-ai-frontend:main
ghcr.io/org/stratum-ai-frontend:v1.0.0
```

---

## 12. Operations & Maintenance

### Health Checks

```bash
# API health
curl http://localhost:8000/health

# Frontend health (production)
curl http://localhost:80/health

# Redis
docker compose exec redis redis-cli ping

# PostgreSQL
docker compose exec db pg_isready -U stratum

# Celery worker
docker compose exec worker celery -A app.workers.celery_app inspect ping
```

### Backup

```bash
# Beta script
./scripts/deploy-beta.sh backup

# Manual PostgreSQL backup
docker compose exec db pg_dump -U stratum stratum_ai > backup_$(date +%Y%m%d).sql

# Manual Redis backup
docker compose exec redis redis-cli BGSAVE
docker cp stratum_redis:/data/dump.rdb ./redis_backup.rdb
```

### Log Access

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f api
docker compose logs -f worker
docker compose logs -f scheduler

# Last 100 lines
docker compose logs --tail=100 api
```

### Scaling Workers

```bash
# Scale celery workers horizontally
docker compose up -d --scale worker=3
```

### Zero-Downtime Update

```bash
# Pull latest code
git pull origin main

# Rebuild and restart services one by one
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps --build api
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps --build worker
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps --build frontend

# Run migrations
docker compose exec api alembic upgrade head

# Upgrading from a release before Paddle Billing? Migrate the tenant billing columns once
# (idempotent; run from backend/ without Docker: python scripts_migrate_paddle_columns.py)
docker compose exec api python scripts_migrate_paddle_columns.py
```

---

## 13. Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| API won't start - "weak key" | Generate proper 32+ char keys for SECRET_KEY, JWT_SECRET_KEY, PII_ENCRYPTION_KEY |
| Database connection refused | Check if `db` container is healthy: `docker compose ps db` |
| Redis connection refused | Check Redis container: `docker compose exec redis redis-cli ping` |
| Migration fails | Check migration status: `docker compose exec api alembic current` |
| Worker OOM killed | Worker auto-restarts at 400MB; check for memory leaks in tasks |
| Frontend 404 on routes | Nginx `try_files` should fallback to `index.html` for SPA routing |
| CORS errors | Verify `CORS_ORIGINS` in `.env` matches frontend URL |
| Celery tasks not running | Check broker URL, verify beat is running: `docker compose logs scheduler` |

### Debug Commands

```bash
# Enter API container shell
docker compose exec api bash

# Enter database shell
docker compose exec db psql -U stratum stratum_ai

# Enter Redis shell
docker compose exec redis redis-cli

# Check resource usage
docker stats

# Inspect a container
docker compose logs api --tail=200
```

---

## 14. Resource Requirements

### Production Memory Allocation (4GB Server)

| Service | Memory Limit | Memory Reserve | CPU Limit |
|---------|-------------|---------------|-----------|
| PostgreSQL | 4 GB | 2 GB | 2.0 |
| Redis | 512 MB | 256 MB | 0.5 |
| API (Uvicorn) | 1 GB | 512 MB | 2.0 |
| Celery Worker | 1 GB | 512 MB | 2.0 |
| Celery Beat | 256 MB | 128 MB | 0.25 |
| Frontend (Nginx) | 128 MB | 64 MB | 0.5 |
| PgBouncer | 128 MB | 64 MB | 0.25 |

### Beta Memory Allocation (4GB Droplet)

| Service | Memory Limit |
|---------|-------------|
| Redis | 256 MB |
| API | 512 MB |
| Celery Worker | 512 MB |
| Celery Beat | 128 MB |
| Frontend (Nginx) | 256 MB |
| Nginx Proxy | 128 MB |

---

## 15. Port Reference

| Port | Service | Environment |
|------|---------|-------------|
| 80 | Nginx (Frontend + Proxy) | Production |
| 443 | Nginx (HTTPS) | Production (with SSL) |
| 5173 | Vite Dev Server | Development |
| 8000 | FastAPI Backend | All |
| 5432 | PostgreSQL | All |
| 6379 | Redis | All |
| 6432 | PgBouncer | Production |
| 5555 | Flower (Celery Monitor) | Optional |
| 9090 | Prometheus | Optional |
| 3001 | Grafana | Optional |

---

## Quick Start Checklist

- [ ] Clone repository
- [ ] Copy `.env.example` to `.env`
- [ ] Generate 3 unique secret keys (32+ chars each)
- [ ] Set database credentials
- [ ] Set CORS origins to match frontend URL
- [ ] Run `docker compose up -d`
- [ ] Verify health: `curl http://localhost:8000/health`
- [ ] Run migrations: `docker compose exec api alembic upgrade head`
- [ ] Create admin user: `docker compose exec api python -m app.scripts.create_admin`
- [ ] Optional: configure Paddle Billing (sandbox keys, price ids, webhook destination) — Step 8
- [ ] Upgrading an existing deployment: run `python scripts_migrate_paddle_columns.py` from `backend/` once
- [ ] Access frontend: http://localhost:5173 (dev) or http://localhost (prod)
- [ ] Access API docs: http://localhost:8000/docs

---

*Last updated: February 2026*

---

## 7. Railway (managed) deployment

The reference cloud setup runs on Railway project `stratum-ai-meta`, deployed automatically from GitHub (`main`).

### Services

| Service | Source | Notes |
|---|---|---|
| `api` | repo, root directory `backend`, `backend/Dockerfile` | Entrypoint `docker-entrypoint.sh` (`SERVICE_ROLE=api`): runs `scripts_db_prepare.py`, optional demo seeding, then `serve.py` (dual-stack socket: IPv4 for the public proxy, IPv6 for the private network). Healthcheck `/health`, timeout 300 s. |
| `worker` | repo, root directory = repository root, root `Dockerfile` | Same image with `SERVICE_ROLE=worker`: Celery worker with the embedded beat scheduler. Run exactly one replica with beat; scale with `CELERY_CONCURRENCY`. Variables reference the api's (`${{api.DATABASE_URL}}` …). |
| `frontend` | repo, root directory `frontend`, `frontend/Dockerfile` | nginx on 8080 serving the SPA and proxying `/api`, `/ws` to `${API_UPSTREAM}` (`api.railway.internal:8000`), re-resolved per request. |
| `Postgres`, `Redis` | Railway plugins | One instance per environment. |

### Environments

- `staging` — every merge to `main` deploys here first. `APP_ENV=staging`, `SEED_DEMO=true` (demo tenant + data, login `demo@stratum.ai`), sandbox Paddle.
- `production` — `APP_ENV=production` (strict settings validation), `SEED_DEMO=false`, `PADDLE_ENVIRONMENT=production`, real Meta/Paddle/GA4 credentials, custom domains in `FRONTEND_URL` / `CORS_ORIGINS`.

### Database lifecycle

- Schema is managed by Alembic. `0001_baseline` creates everything from the models; later revisions are normal diffs (`alembic revision --autogenerate`).
- `scripts_db_prepare.py` (run by the entrypoint on every boot) stamps databases that were created from the models before the baseline existed, then upgrades to head. It is idempotent.
- To wipe a seeded database once (e.g. turning the staging copy into production): set `DB_RESET_CONFIRM=RESET-<database name>` on the `api` service, redeploy, verify, then **remove the variable**.

### Gotchas (learned the hard way)

- Healthcheck **path** and **timeout** are separate fields (`/health` and `300`); a combined value probes a non-existent route and the deploy never becomes healthy.
- `railway up` (CLI upload) needs `--no-gitignore` or it silently drops `backend/app/models`; repo-connected builds do not have this problem.
- The CLI cannot change a service's root directory or Dockerfile path (`railway environment edit` reports "No changes to apply"); set those in the dashboard.
- Python's asyncio marks `::` listeners IPv6-only; bind through `serve.py`, never `uvicorn --host ::` or `0.0.0.0` alone.
