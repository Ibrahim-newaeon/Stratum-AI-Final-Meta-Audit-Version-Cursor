# Stratum AI Platform

## Overview
Revenue Operating System with Trust-Gated Autopilot architecture.
Automation executes ONLY when signal health passes safety thresholds.

## Core Concept
```
Signal Health Check → Trust Gate → Automation Decision
       ↓                  ↓              ↓
   [HEALTHY]         [PASS]         [EXECUTE]
   [DEGRADED]        [HOLD]         [ALERT ONLY]
   [UNHEALTHY]       [BLOCK]        [MANUAL REQUIRED]
```

## Tech Stack
- **Backend**: Python 3.11+, FastAPI, Pydantic
- **Database**: PostgreSQL 15, Redis (caching/queues)
- **Queue**: Celery + Redis
- **Frontend**: React 18, TypeScript, Tailwind CSS
- **Infra**: Docker, AWS (ECS, RDS, ElastiCache)
- **Monitoring**: Prometheus, Grafana, Sentry
- **Billing**: Paddle Billing (Merchant of Record) — thin httpx client, Paddle.js v2 overlay checkout, signed webhooks

## Project Structure
```
/stratum-ai/
├── api/              # FastAPI routes
├── core/             # Business logic, trust engine
│   ├── signals/      # Signal collectors & processors
│   ├── gates/        # Trust gate evaluators
│   └── automations/  # Automation executors
├── models/           # SQLAlchemy models
├── schemas/          # Pydantic schemas
├── services/         # External integrations
├── workers/          # Celery tasks
└── tests/
```

## Key Commands
```bash
make dev              # Start local env
make test             # Run pytest
make lint             # Ruff + mypy
make migrate          # Alembic migrations
docker compose up -d  # Full stack
```

## Code Standards
- Type hints REQUIRED on all functions
- Pydantic models for all API I/O
- Async/await for all I/O operations
- 90%+ test coverage for core/
- Docstrings on public functions

## Domain Terminology
| Term | Definition |
|------|------------|
| Signal | Input data point (metric, event, webhook) |
| Signal Health | Composite score (0-100) of signal reliability |
| Trust Gate | Decision checkpoint before automation |
| Autopilot | Automated action when trust passes |

## Trust Engine Rules
```python
HEALTHY_THRESHOLD = 70      # Green - autopilot enabled
DEGRADED_THRESHOLD = 40     # Yellow - alert + hold
# Never auto-execute when signal_health < 70
```

## Meta Insights Ingestion (read-only)
Campaign performance is pulled from the Meta Marketing API **Ads Insights** endpoint. This is the only
real ad-platform ingestion in the product, and it is **GET-only** — it can never create, edit, pause or
delete anything in an ad account. Scope: `ads_read` (`ads_management` belongs to the separate,
deliberately unwired autopilot write path in `app/tasks/apply_actions_queue.py` — do not touch it).

`GET /<version>/act_<id>/insights` with `level=campaign`, `time_increment=1` and a JSON `time_range`;
pagination follows `paging.next` via `paging.cursors.after` (cursors are returned on the last page too).
Hitting the `META_INSIGHTS_MAX_PAGES` cap while Meta still has pages raises `MetaInsightsTruncatedError`
— an incomplete window is never returned as a short successful one. Rows are upserted into
`campaign_metrics` by `(campaign_id, date)` — the last `META_INSIGHTS_LOOKBACK_DAYS` days are re-pulled
every run because Meta restates conversions after the fact.

Conversions and revenue come from the `actions` / `action_values` arrays keyed by the action types in
`META_CONVERSION_ACTION_TYPES`. The winning type is resolved **once** against `actions` (first configured
type present wins, so overlapping types never double count) and the revenue for that same type is then
read from `action_values` — a count from one type is never paired with a value from another. Keep a type
the accounts actually report: `offsite_conversion.fb_pixel_purchase` is web-pixel only, `omni_purchase`
is Meta's documented grouped type. `video_views` comes from the `video_view` action type (3-second
views), not from `video_p25_watched_actions`. Money is `Decimal` from Meta's decimal strings to the
integer column, never `float`; the `*_cents` columns hold hundredths of the account's **major** unit for
every currency, which is why they are `BigInteger` (migration `0002_widen_money_columns` — int4 capped a
VND campaign at ~US$860).

Nothing is ever written that was not read from Meta, and `last_synced_at` advances only for a window that
actually returned rows. No usable credential (no connection, not `connected`, missing/expired token, no
ad account, blank `external_id`) → nothing written, `campaigns.sync_error` records why, `last_synced_at`
untouched so freshness degrades honestly, task returns `skipped` with the reason. Zero rows → `no_rows`,
not `success` (indistinguishable from a wrong `external_id`). Truncated pull → `failed` /
`insights_truncated`, no retry. Error code 190 → connection marked `disconnected`
(`last_error`/`error_count`); throttling codes 4/17/32/341/613/80000+ or a bare HTTP 429 → Celery retry
with the back-off from `X-Business-Use-Case-Usage`, falling back to `Retry-After`. The token travels in
`Authorization: Bearer`, never in a query string, log line or exception message.

`META_GRAPH_API_VERSION` is the **single** Graph API version constant: the insights client, the CAPI and
WhatsApp connectors (`app/services/capi/platform_connectors.py`), offline conversions, CDP audience sync
and the OAuth flow all read it. Never hardcode a `v<N>.0` literal in a Meta caller. `META_API_VERSION`
remains only as a deprecated OAuth-only override; leave it unset.

**Key files**
- `backend/app/services/meta/insights_client.py` (thin async httpx client, `MetaInsightRow`,
  `MetaAPIError` / `MetaTokenError` / `MetaRateLimitError`)
- `backend/app/services/meta/insights_ingestion.py` (credential resolution, mapping, money conversion,
  `(campaign_id, date)` upsert)
- `backend/app/workers/tasks/sync.py` (`sync_campaign_data`, `sync_all_campaigns`)
- `backend/migrations/versions/20260904_000000_0002_widen_money_columns.py` (money columns to int8;
  existing deployments must run `alembic upgrade head`, it rewrites `campaigns` / `campaign_metrics`)
- Tests: `backend/tests/unit/test_meta_insights.py`, `backend/tests/unit/test_mock_ad_data_guard.py`
- `backend/app/stratum/adapters/meta_adapter.py` imports `facebook_business`, which is **not installed
  and not a dependency** — the module cannot be imported. It is reference material only; do not revive it
  and do not add the SDK.

**Global env (the ONLY META_INSIGHTS_*/META_GRAPH_* variables; tokens and ad accounts are per tenant in
`tenant_platform_connection` / `tenant_ad_account`)**
```
META_GRAPH_API_VERSION=v23.0
META_INSIGHTS_LOOKBACK_DAYS=7
META_INSIGHTS_REQUEST_TIMEOUT_SECONDS=30
META_INSIGHTS_MAX_PAGES=25
META_CONVERSION_ACTION_TYPES=offsite_conversion.fb_pixel_purchase,omni_purchase,purchase
```

`USE_MOCK_AD_DATA` still switches in the local mock generator for development; it is rejected outright
when `APP_ENV=production`.

## Measurement Integrations (read-only)
Stratum AI **acts only on Meta channels** (Facebook, Instagram, WhatsApp). Google Analytics 4 and
Google Tag Manager are restored strictly as **"Measurement & Verification"** integrations
(Arabic: القياس والتحقق). They are never ad platforms, channels or activation targets.

**GA4 = read-only independent revenue/conversion baseline**
- GA4 Data API with a per-tenant service account (scope `https://www.googleapis.com/auth/analytics.readonly`)
- Daily rows land in `fact_ga4_daily` (date x utm_source/medium/campaign, sessions/conversions/revenue,
  Meta-traffic classification) and feed:
  - attribution variance (`fact_attribution_variance_daily`, Platform vs GA4)
  - EMQ "attribution accuracy" driver
  - Signal Health and the Trust Gate
- Positioning everywhere: "independent verification", "read-only baseline", never "Google Ads"

**GTM = tag deployment only**
- Web container (`GTM-XXXXXXX`): Meta Pixel + Stratum tracking snippet
- Server-side tagging endpoint (sGTM, `https://tags.yourdomain.com`): Meta Conversions API tag +
  the CDP `sgtm` source, which posts to `POST /api/v1/cdp/ingest` with header `X-Source-Key`
  (`cdp_sources.source_key`; source_type `sgtm`, label "Server-side GTM")

**Tables** (created ONLY by `backend/scripts_create_measurement_tables.py`, idempotent, no Alembic migration):
`tenant_ga4_integrations`, `tenant_gtm_integrations`, `fact_ga4_daily`. Service-account JSON and the GTM
preview header are encrypted with `app.services.encryption.encrypt_token` and never returned, logged or
written to `CDPSource.config`.

**Endpoints**: `/api/v1/integrations/measurement/*` (tag "Measurement & Verification", `APIResponse` envelope):
`GET status`, `GET|PUT|DELETE ga4`, `POST ga4/test-connection`, `POST ga4/sync`, `GET ga4/baseline`,
`GET|PUT|DELETE gtm`, `POST gtm/verify`, `GET gtm/snippets`, `DELETE` (both).

**Celery**: `app.workers.tasks.measurement.pull_ga4_daily_baseline` (beat `measurement-ga4-daily-pull`, 02:30 UTC),
`app.workers.tasks.measurement.sync_ga4_tenant`, plus `trust-signal-health-rollup` (02:00 UTC) and
`trust-attribution-variance-rollup` (03:00 UTC); queue `sync`.

**Key files**
- `backend/app/models/measurement.py` (TenantGA4Integration, TenantGTMIntegration, FactGA4Daily, MeasurementStatus)
- `backend/app/services/measurement/` (`ga4_client.py`, `ga4_ingestion.py`, `gtm_service.py`)
- `backend/app/workers/tasks/measurement.py`
- `backend/app/api/v1/endpoints/measurement.py`, `backend/app/schemas/measurement.py`
- `frontend/src/api/measurement.ts`, `frontend/src/components/settings/GA4Integration.tsx`, `GTMIntegration.tsx`
- Operator docs: `docs/integrations/README.md`, `SERVER_DEPLOYMENT_GUIDE.md` (Step 7)

**Global env (the ONLY GA4_*/GTM_* variables; property IDs, service accounts and container IDs are per tenant in the DB)**
```
GA4_SYNC_ENABLED=true
GA4_LOOKBACK_DAYS=3
GA4_BACKFILL_DAYS=30
GA4_REQUEST_TIMEOUT_SECONDS=30
GA4_DEFAULT_CONVERSION_EVENT=purchase
GTM_VERIFY_TIMEOUT_SECONDS=10
GTM_DEFAULT_SERVER_CONTAINER_URL=
```

## Billing (Paddle Billing)
Tenant subscriptions are billed through **Paddle Billing** (Merchant of Record). Paddle hosts the checkout,
collects payment, handles tax/invoices/dunning and reports back through signed webhooks; Stratum never
stores card data. The integration is a **thin `httpx.AsyncClient`** (`backend/app/services/paddle_service.py`),
no `paddle-python-sdk`, no `@paddle/*` npm package — Paddle.js v2 is loaded at runtime from
`https://cdn.paddle.com/paddle/v2/paddle.js` and only inside the authenticated SPA (never in `frontend/public/*.html`).

**Config keys** (`backend/app/core/config.py` Settings) and env names — all optional, sandbox by default:
| Setting | Env |
|---------|-----|
| `paddle_api_key` | `PADDLE_API_KEY` (empty = billing disabled) |
| `paddle_client_token` | `PADDLE_CLIENT_TOKEN` (served via `GET /api/v1/billing/config`, never a frontend env var) |
| `paddle_webhook_secret` | `PADDLE_WEBHOOK_SECRET` |
| `paddle_environment` | `PADDLE_ENVIRONMENT` (`sandbox` \| `production`, default `sandbox`) |
| `paddle_starter_price_id` / `paddle_professional_price_id` / `paddle_enterprise_price_id` | `PADDLE_STARTER_PRICE_ID` / `PADDLE_PROFESSIONAL_PRICE_ID` / `PADDLE_ENTERPRISE_PRICE_ID` (`pri_...`) |

Properties: `settings.paddle_enabled`, `settings.paddle_fully_configured`, `settings.paddle_api_base_url`
(`https://sandbox-api.paddle.com` | `https://api.paddle.com`). The validator raises only in production
(API key set while the environment is still `sandbox`, or a companion key missing). Frontend: optional
`VITE_PADDLE_ENVIRONMENT` override only.

**Tenant columns** (`backend/app/base_models.py`, table `tenants`): `paddle_customer_id`,
`paddle_subscription_id`, `subscription_status` (`active|trialing|past_due|paused|canceled`),
`current_period_end`; existing `plan` / `plan_expires_at` keep their semantics. Idempotency table
`paddle_webhook_events` (`PaddleWebhookEvent`). Schema is `create_all`-based: existing deployments run
`backend/scripts_migrate_paddle_columns.py` once (idempotent rename/add + webhook table). Never silently
downgrade a tenant on an unknown price id; `canceled` -> `plan='free'`.
Catalogue + webhook bootstrap: `backend/scripts_paddle_bootstrap.py` (`railway run -e <env> --service api -- python scripts_paddle_bootstrap.py [--dry-run] [--webhook-url ...] [--railway-env <env> --railway-service api]`) creates/reuses the tier products, monthly prices and notification destination idempotently and prints or writes the ids into Railway (the webhook secret is piped through `railway variable set --stdin`; `--railway-env production` only with `PADDLE_ENVIRONMENT=production` and vice versa); it never prints a secret unless `--print-webhook-secret`.

**API** (`backend/app/api/v1/endpoints/billing.py`, `APIResponse` envelope, schemas in `schemas/billing.py`):
`GET /api/v1/billing/config`, `GET /billing/subscription`, `POST /billing/checkout-session` (returns
price id + client token + customer email + `custom_data{tenant_id,tier}`; the SPA opens the Paddle.js overlay),
`POST /billing/portal-session`, `POST /billing/cancel`, `POST /billing/reactivate`, `POST /billing/upgrade`,
`GET /billing/transactions`, `GET /billing/transactions/{id}/invoice`. Errors: not configured -> 503,
Paddle error -> 502.

**Webhook** (`backend/app/api/v1/endpoints/paddle_webhook.py`): public `POST /api/v1/webhooks/paddle`
(in `TenantMiddleware.PUBLIC_ENDPOINTS`), `Paddle-Signature: ts=<unix>;h1=<hex>` verified as HMAC-SHA256
over `<ts>:<raw body>` with a 300 s tolerance; 200 `{status: received|duplicate|ignored}`, 400 bad
signature/JSON, 503 no secret, 500 after rollback so Paddle retries. Handles `subscription.*`,
`transaction.completed|paid|payment_failed`, `customer.created|updated`; everything else is `ignored`.

**Frontend**: `frontend/src/api/billing.ts` (hooks + types), `frontend/src/lib/paddle.ts` (loader),
`frontend/src/components/settings/PaddleBilling.tsx` (Settings > Billing, `/dashboard/settings?tab=billing`),
`frontend/src/views/billing/BillingSuccess.tsx` (`/dashboard/billing/success`). Pricing CTAs on the public
landing pages are navigation only; checkout happens in Settings > Billing after signup.

**CSP hosts** (identical in `backend/app/middleware/security.py` build_csp, `frontend/nginx.conf`, `nginx/beta.conf`):
`script-src` + `https://cdn.paddle.com`; `connect-src` + `https://*.paddle.com`; `frame-src 'self' https://*.paddle.com`;
nginx `Permissions-Policy: payment=(self "https://buy.paddle.com" "https://sandbox-buy.paddle.com")`.

**No other payment provider.** The former provider's name (spelled S-T-R-I-P-E) must not appear anywhere in
the project — code, tests, docs, env examples, compose files, HTML, comments. The only tolerated matches for
a case-insensitive search of that name are the pre-existing row-banding utility props in
`frontend/src/components/ui/data-table.tsx` and `frontend/src/components/ui/progress.tsx` (their name is that
word plus a trailing "d"); do not rename them.

Docs: `docs/integrations/billing-paddle.md`, `SERVER_DEPLOYMENT_GUIDE.md` (Step 8),
`docs/05-operations/runbooks.md` ("Paddle Webhook Failures").

## Do NOT
- Skip trust gate checks for "quick fixes"
- Hardcode thresholds (use config)
- Execute automations without audit logging
- Merge without passing CI
- Treat GA4/GTM as ad channels (no Google Ads, Customer Match, gclid, write scopes, Google OAuth); never put ga4/gtm in AdPlatform/SyncPlatform/Platform enums or TenantPlatformConnection
- Add any payment provider other than Paddle Billing, add a Paddle SDK dependency, or load Paddle.js in public static HTML
- Make any non-GET call to the Meta Marketing API from the insights path, add the `facebook_business`
  SDK, or wire up `app/tasks/apply_actions_queue.py` (its executor is a simulator)
- Report a successful campaign sync, or leave `last_synced_at` fresh, when no metrics were written

## Git Workflow
- Branch: `feature/STRAT-123-description`
- Commit: `feat(signals): add anomaly detection [STRAT-123]`

## CDP (Customer Data Platform) Frontend

### Views & Routes
| Route | Component | Description |
|-------|-----------|-------------|
| `/dashboard/cdp` | CDPDashboard | Main overview with stats, lifecycle distribution, event volume charts |
| `/dashboard/cdp/profiles` | CDPProfiles | Profile viewer with search, filters, pagination, detail modal |
| `/dashboard/cdp/segments` | CDPSegments | Segment builder with condition builder, preview, CRUD |
| `/dashboard/cdp/events` | CDPEvents | Event timeline with volume charts, anomaly detection |
| `/dashboard/cdp/identity` | CDPIdentityGraph | SVG-based identity graph visualization |

### Key Files
- `frontend/src/views/cdp/` - All CDP view components
- `frontend/src/api/cdp.ts` - React Query hooks for CDP API (60+ hooks)
- `frontend/src/views/DashboardLayout.tsx` - CDP navigation added here

### CDP API Hooks (from `@/api/cdp`)
- `useCDPHealth`, `useProfileStatistics`, `useEventStatistics`
- `useSegments`, `useCreateSegment`, `useUpdateSegment`, `useDeleteSegment`
- `useSearchProfiles`, `useCDPProfile`, `useExportAudience`
- `useEventTrends`, `useAnomalySummary`, `useEventAnomalies`
- `useIdentityGraph`, `useMergeHistory`

### Navigation
CDP section is in sidebar with collapsible submenu (6 items including Audience Sync).
State managed via `cdpExpanded` in DashboardLayout.

### CDP Audience Sync (New Feature)
Push CDP segments directly to Meta for targeting across Facebook, Instagram, and WhatsApp.

**Routes:**
- `/dashboard/cdp/audience-sync` - Main audience sync management

**Key Files:**
- `frontend/src/components/cdp/AudienceSync.tsx` - Full UI component
- `frontend/src/views/cdp/CDPAudienceSync.tsx` - View wrapper
- `backend/app/services/cdp/audience_sync/` - Platform connectors
- `backend/app/api/v1/endpoints/audience_sync.py` - REST API

**Supported Platforms:**
- Meta (Custom Audiences API — covers Facebook, Instagram, and WhatsApp)

**Features:**
- Create platform audiences linked to CDP segments
- Auto-sync with configurable intervals (1h - 1 week)
- Manual sync trigger
- Sync history with metrics (profiles sent, added, match rate)
- Manual export to CSV/JSON with traits and events

---

## Update Landing Content with CDP Unique Selling Points

### CDP Core Value Propositions

**1. Unified Customer Profiles**
- Single customer view across all touchpoints
- Identity resolution merging anonymous → known → customer
- Real-time profile enrichment from events

**2. Meta Audience Sync**
- Push segments to Meta Custom Audiences with one click (Facebook, Instagram, WhatsApp)
- Hashed identifier matching (email, phone, MAID)
- Auto-sync keeps audiences fresh (configurable intervals)
- Match rate tracking and optimization

**3. Advanced Segmentation**
- Dynamic segments with behavioral conditions
- RFM analysis (Recency, Frequency, Monetary)
- Lifecycle stage targeting (anonymous → churned)
- Computed traits for complex attributes

**4. Event Intelligence**
- Real-time event ingestion and processing
- Anomaly detection with alerting
- EMQ (Event Match Quality) scoring
- Conversion funnel analysis

**5. Identity Graph**
- Visual identity resolution
- Cross-device tracking
- Profile merge history and audit trail
- Canonical identity management

**6. Privacy-First Design**
- Consent management per data type
- GDPR/CCPA compliant exports
- Hashed PII for platform sync
- Audit logging for all operations

### Landing Page Feature Highlights

```
CDP FEATURES FOR LANDING PAGE:

Hero Section:
"Turn Customer Data Into Revenue"
- AI-powered revenue operating system for ad teams
- Optimize Facebook, Instagram & WhatsApp campaigns with Trust-Gated Autopilot
- Every AI decision is auditable, explainable and reversible — one-click human override
- Connect any ad account read-only. 14-day free trial, no credit card. 👉 stratumai.app

Feature Cards:
┌─────────────────────────────────────────────────────────────┐
│ 🎯 One-Click Audience Sync                                  │
│ Push segments to Meta Custom Audiences instantly —          │
│ Facebook, Instagram & WhatsApp. Auto-sync keeps your        │
│ audiences fresh 24/7.                                       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ 👤 360° Customer Profiles                                   │
│ Unified view from anonymous visitor to loyal customer.      │
│ Real-time enrichment from every interaction.                │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ 📊 Smart Segmentation                                       │
│ Build segments with behavioral rules, RFM scores,           │
│ and lifecycle stages. Preview before you publish.           │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ 🔗 Identity Resolution                                      │
│ Connect the dots across devices and channels.               │
│ Visual identity graph shows every connection.               │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ 📤 Flexible Export                                          │
│ Export audiences as CSV or JSON anytime.                    │
│ Include traits, events, and custom attributes.              │
└─────────────────────────────────────────────────────────────┘

Comparison Table:
| Feature                  | Stratum CDP | Segment | mParticle |
|--------------------------|-------------|---------|-----------|
| Meta Custom Audience sync| ✅ FB, IG & WhatsApp | ✅ | ✅ |
| Real-time segments       | ✅ | ✅ | ✅ |
| Identity graph viz       | ✅ | ❌ | ❌ |
| RFM analysis             | ✅ Built-in | ❌ | ❌ |
| Trust-gated actions      | ✅ Unique | ❌ | ❌ |
| Independent GA4 verification baseline | ✅ Read-only | ❌ | ❌ |
| Predictive models (ROAS, LTV, churn, conversion, creative fatigue) | ✅ Built-in | ❌ | ❌ |
| Manual CSV export        | ✅ | ✅ | ✅ |
| Anomaly detection        | ✅ | ❌ | ✅ |
```

### API Endpoints Summary (for docs)
```
CDP Audience Sync API:
GET    /cdp/audience-sync/platforms           - Connected platforms
GET    /cdp/audience-sync/audiences           - List audiences
POST   /cdp/audience-sync/audiences           - Create audience
POST   /cdp/audience-sync/audiences/{id}/sync - Trigger sync
GET    /cdp/audience-sync/audiences/{id}/history - Sync history
DELETE /cdp/audience-sync/audiences/{id}      - Delete audience
POST   /cdp/audiences/export                  - Export CSV/JSON
```

## Imports
@docs/architecture/trust-engine.md
@docs/integrations/README.md
