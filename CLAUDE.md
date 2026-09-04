# Stratum AI Project Guide

This file is the working guide for contributors and coding agents. Keep it focused on durable architecture, safety rules, and verified development workflows. Do not copy feature inventories, test counts, marketing copy, or deployment runbooks into this file.

## Product and safety model

Stratum AI is a revenue operating system for Meta channels with Trust-Gated Autopilot. Automated actions are allowed only when signal quality passes the trust gate.

| Signal health | Gate | Behavior |
|---|---|---|
| `>= 70` | PASS | Automation may execute, subject to normal authorization and policy checks |
| `40-69` | HOLD | Alert and hold; do not execute automatically |
| `< 40` | BLOCK | Require manual action |

Never bypass the trust gate for a quick fix. Preserve audit logging for every executed automation. Thresholds currently have multiple backend and frontend consumers; if they change, trace and update all consumers, tests, and documentation together.

## Sources of truth

Use these in order when documentation disagrees:

1. Implementation and tests
2. `.github/workflows/ci.yml` for validation behavior
3. `docker-compose*.yml`, Dockerfiles, and Railway configuration for runtime behavior
4. `.env.example` and `backend/.env.example` for configuration names
5. Focused documents under `docs/` and `SERVER_DEPLOYMENT_GUIDE.md`

Treat version badges, feature counts, route inventories, coverage percentages, and marketing pages as descriptive rather than authoritative.

## Technology

- Backend: FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, Celery, and Redis
- Data: PostgreSQL 16 and Redis 7
- Frontend: React 18, TypeScript, Vite, Tailwind CSS, TanStack Query, and Zustand
- Operations: Docker Compose, Railway, Prometheus, Grafana, and Sentry
- Billing: Paddle Billing through a thin `httpx` client and Paddle.js

For local CI parity, use the Python and Node versions declared in `.github/workflows/ci.yml`. Container versions are pinned separately in the backend and frontend Dockerfiles.

## Repository map

```text
backend/
  app/
    api/v1/endpoints/   FastAPI routes
    core/               configuration and shared application concerns
    models/             model modules; shared models also live in base_models.py
    schemas/            Pydantic schemas; shared schemas also live in base_schemas.py
    services/           integrations and business services
    workers/            Celery application and tasks
  migrations/           Alembic environment and revisions
  tests/unit/           backend unit tests
  tests/integration/    database-backed integration tests
frontend/
  src/
    api/                 API client modules and hooks
    components/          shared and feature components
    views/               routed application views
    stores/              Zustand state
    lib/                 shared frontend utilities
  e2e/                   Playwright tests
docs/                    architecture, integration, and operations documentation
editions/                starter, professional, and enterprise variants
scripts/                 repository-level data and database helpers
docker-compose*.yml      local, production, and monitoring stacks
nginx/                   proxy configuration
```

Some paths have historical copies with a trailing ` 2` in the filename. Treat the unsuffixed path as canonical unless a task explicitly asks to consolidate the duplicates.

## Local development

From the repository root:

```bash
cp .env.example .env
docker compose up -d
```

Do not commit `.env` or real credentials. The default local endpoints are:

- Frontend: `http://localhost:5173`
- API: `http://localhost:8000`
- OpenAPI UI: `http://localhost:8000/docs`
- Flower: `http://localhost:5555` when the `monitoring` profile is enabled

## Validation

Run the checks relevant to the files you changed. Before broad or high-risk changes, run the complete affected suite.

### Backend

From `backend/`, with PostgreSQL and Redis configured:

```bash
python -m pip install -r requirements.txt
pytest tests/unit -v --tb=short -m "unit or not integration"
pytest tests/integration -v --tb=short -m "integration"
ruff check .
black --check --diff .
isort --check-only --diff .
mypy app --ignore-missing-imports --no-error-summary
```

Backend tests are blocking in GitHub Actions. Backend lint, formatting, and type checks currently report legacy debt without blocking; keep new and touched code clean and do not increase that debt. Coverage is reported but is not currently gated.

### Frontend

From `frontend/`:

```bash
npm ci
npm run lint
npx tsc --noEmit
npm run test:coverage
npm run build
npm run test:e2e -- --project=chromium
```

Frontend lint, type checking, unit tests, and builds are blocking in GitHub Actions. Playwright E2E currently reports without blocking.

## Engineering conventions

### Backend

- Put HTTP routes in `backend/app/api/v1/endpoints/` and keep business or integration logic in services or domain modules.
- Use Pydantic models for API input and output and preserve the standard `APIResponse`/`PaginatedResponse` envelopes from `app.schemas.response`.
- Follow the existing asynchronous SQLAlchemy and I/O patterns.
- Derive tenant scope from authenticated request context and existing dependencies or middleware. Never trust a client-supplied tenant identifier as authorization.
- Preserve role checks, tenant filtering, audit records, and idempotency around mutations and webhooks.
- Add type hints and focused docstrings to new public Python code.

### Frontend

- Use the shared API client and existing React Query hooks instead of adding ad hoc request code.
- Do not hardcode production API or WebSocket hosts; use the existing same-origin defaults and environment configuration.
- Keep routes, navigation, API types, loading/error states, and English/Arabic copy aligned when a feature changes.
- Reuse the existing component, theme, and state-management patterns before introducing new abstractions.

## Database and configuration

- Alembic is the schema lifecycle. The baseline revision creates the current model-backed schema; every later schema change must be a normal Alembic revision under `backend/migrations/versions/`.
- From `backend/`, create and apply migrations with `alembic revision --autogenerate -m "description"` and `alembic upgrade head`.
- Ensure new model modules are registered through `app.models` so Alembic can see their metadata.
- `backend/scripts_db_prepare.py` is the idempotent deployment preparation path run by the API entrypoint. Do not replace it with new one-off `create_all` migration scripts.
- Legacy table-creation and column-migration scripts remain for historical compatibility; they are not the default path for new schema work.
- Never set `DB_RESET_CONFIRM` unless the user explicitly requests the documented one-shot reset procedure. Remove it immediately after the intended deployment.
- The `*_cents` money columns hold hundredths of the account's **major** unit for every currency, not ISO-4217 minor units: every reader divides by 100 with no currency awareness, so a zero-decimal currency such as JPY is quantised at its real precision and then scaled. That is why those columns are `BigInteger`.
- Add configuration through `backend/app/core/config.py` and update the appropriate example environment files without placing secrets or tenant credentials in source control.

## Integration guardrails

- Activation is Meta-only: Facebook, Instagram, WhatsApp, Meta Marketing API, Conversions API, and Custom Audiences.
- GA4 and GTM belong only to **Measurement & Verification**. GA4 is a read-only independent baseline; GTM deploys web and server-side tags. Do not treat either as an ad channel or add them to activation/platform enums.
- Do not add Google Ads, Customer Match, `gclid` handling, Google sign-in, GA4 write scopes, or other non-Meta activation paths.
- Paddle Billing is the only payment provider. Keep the backend integration as a thin `httpx` client; do not add a Paddle SDK dependency.
- Load Paddle.js only inside the authenticated SPA. Do not place checkout scripts in `frontend/public/*.html`.
- Keep integration credentials encrypted at rest and never return, log, or store secrets in public configuration objects.
- Preserve signed-webhook verification, replay/idempotency protection, transaction rollback, and retry-safe error behavior.
- Meta insights ingestion is strictly read-only: the client issues `GET` and nothing else. The autopilot write path in `backend/app/tasks/apply_actions_queue.py` is a simulator with hardcoded responses; do not schedule it or treat its output as real until a genuine executor exists.
- Never report a successful sync, or leave `last_synced_at` fresh, when no metrics were written. Freshness feeds signal health, so a source nobody can read must degrade rather than present as healthy.

## Git and delivery workflow

- Use short branches such as `feat/description`, `fix/description`, or `security/description`.
- Use conventional commit subjects such as `feat(signals): add anomaly detection` or `fix(worker): keep beat state writable`.
- Keep changes scoped, add or update focused tests, and pass all blocking CI checks before merge.
- Update focused documentation when an interface, invariant, deployment procedure, or operator workflow changes.

## Detailed documentation

- [Trust engine](docs/architecture/trust-engine.md)
- [Integration boundaries](docs/integrations/README.md)
- [Paddle Billing](docs/integrations/billing-paddle.md)
- [Operations runbooks](docs/05-operations/runbooks.md)
- [Server and Railway deployment](SERVER_DEPLOYMENT_GUIDE.md)

Prefer linking to these documents over duplicating their endpoint lists, environment matrices, schedules, or runbooks here.
