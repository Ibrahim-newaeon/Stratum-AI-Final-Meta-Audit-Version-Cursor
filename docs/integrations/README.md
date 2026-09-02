# Integrations

Stratum AI has two kinds of integrations and they must never be confused:

| Kind | Integrations | What Stratum does | What Stratum never does |
|------|--------------|-------------------|-------------------------|
| **Meta activation** | Facebook, Instagram, WhatsApp (Meta Marketing API, Conversions API, WhatsApp Cloud API, Custom Audiences) | Reads campaigns, executes trust-gated actions, syncs CDP audiences, sends CAPI events | Act on any non-Meta ad platform |
| **Measurement & Verification** (القياس والتحقق) | Google Analytics 4 (read-only), Google Tag Manager (tag deployment) | Reads an independent revenue/conversion baseline; deploys tags | Treat GA4/GTM as an ad channel, connect Google Ads, use Customer Match, read gclid, request write scopes or Google sign-in |
| **Billing** | Paddle Billing (Merchant of Record) | Paddle.js overlay checkout, customer portal, invoices, signed webhooks that keep `Tenant.plan` in sync | Store card data, use any other payment provider, treat billing as an ad platform or data source |

`AdPlatform`, `SyncPlatform`, `Platform` unions, `TenantPlatformConnection`, ConnectPlatforms, onboarding
platform lists, CAPI platforms, platform filters and "ROAS by platform" stay **Meta-only**. GA4 and GTM live
in their own tables (`tenant_ga4_integrations`, `tenant_gtm_integrations`) behind
`/api/v1/integrations/measurement`.

---

## Meta activation (summary)

- Connect the Meta ad account (read-only token is enough for insights; a system-user token with
  `ads_management` is needed for autopilot actions).
- Meta Pixel + Conversions API for event delivery (see `backend/app/services/capi/`).
- WhatsApp Cloud API for messaging and conversation attribution.
- CDP Audience Sync pushes segments to Meta Custom Audiences (Facebook, Instagram, WhatsApp).

---

## Billing (Paddle Billing)

Tenant subscriptions are billed through Paddle Billing as Merchant of Record: a thin httpx client over the
Paddle REST API (`backend/app/services/paddle_service.py`), authenticated routes under `/api/v1/billing`,
the public signed webhook `POST /api/v1/webhooks/paddle`, and the Paddle.js v2 overlay checkout inside
Settings > Billing. Configuration is the seven `PADDLE_*` variables (sandbox by default, optional).
Full reference, event mapping, CSP hosts and the upgrade script: [billing-paddle.md](./billing-paddle.md).

---

## Measurement & Verification

### Google Analytics 4: read-only independent baseline

GA4 provides an **independent, read-only revenue and conversion baseline** pulled through the GA4 Data API
with a per-tenant service account. It is used for:

- Platform vs GA4 attribution variance (`fact_attribution_variance_daily`, rollup at 03:00 UTC)
- EMQ "attribution accuracy" driver
- Signal Health and the Trust Gate

Scope is `https://www.googleapis.com/auth/analytics.readonly`. Stratum only runs reports; it never writes to
GA4, never links Google Ads, never uses Customer Match and never reads `gclid`.

**Setup (per tenant, in the app under Settings > Integrations > Measurement & Verification > Google Analytics 4):**

1. In Google Cloud, create a service account (for example `stratum-ga4-reader@<project>.iam.gserviceaccount.com`)
   and download its JSON key. Enable the *Google Analytics Data API* on that project.
2. In GA4 Admin > Property > Property access management, add the service-account email with the
   **Viewer** role (no edit or admin rights).
3. In Stratum, enter the numeric **GA4 Property ID** (Admin > Property settings, not the `G-` measurement ID),
   optionally the **Measurement ID** (`G-XXXXXXXXXX`), and the **conversion event names** (default `purchase`).
4. Paste the service-account **JSON** into the Service account JSON field and save. The key is encrypted
   (`app.services.encryption.encrypt_token`), never returned by the API and never logged; only the
   service-account email and a fingerprint are shown.
5. Click **Test connection** (`POST /api/v1/integrations/measurement/ga4/test-connection`). A success
   response reports sessions/conversions/revenue for the last 7 days.
6. Click **Sync now** (`POST /api/v1/integrations/measurement/ga4/sync`, `backfill: true` for the first run,
   `GA4_BACKFILL_DAYS` days). After that the beat task
   `app.workers.tasks.measurement.pull_ga4_daily_baseline` runs daily at 02:30 UTC and re-pulls the last
   `GA4_LOOKBACK_DAYS` days into `fact_ga4_daily`.
7. Check the baseline card (`GET /api/v1/integrations/measurement/ga4/baseline`) and, from the next day on,
   the Platform vs GA4 variance in the Trust Layer.

### Google Tag Manager: tag deployment (web + server-side)

GTM is used only to **deploy tags**:

- **Web container** (`GTM-XXXXXXX`): Meta Pixel and the Stratum tracking snippet.
- **Server-side tagging endpoint** (sGTM, for example `https://tags.yourdomain.com`): the Meta Conversions
  API tag and the Stratum CDP `sgtm` source. The server container posts event batches to
  `POST /api/v1/cdp/ingest` with the header `X-Source-Key: <cdp_sources.source_key>` (source type `sgtm`,
  label "Server-side GTM"); the tenant is derived from the source key.

**Setup (per tenant, Settings > Integrations > Measurement & Verification > Google Tag Manager):**

1. Paste the **web container ID** (`GTM-XXXXXXX`) and, if you run server-side tagging, the **sGTM endpoint
   URL** (https only, no trailing slash) and optional server container ID / preview header.
2. Enter the **Meta Pixel ID** and choose what to deploy (Meta Pixel, Meta Conversions API, Stratum snippet).
3. Save. Stratum creates or reuses the CDP `sgtm` source for the tenant and links it to the GTM record.
4. Click **Verify containers** (`POST /api/v1/integrations/measurement/gtm/verify`) to check that the web
   container script and the sGTM endpoint respond.
5. Open **Container snippets** (`GET /api/v1/integrations/measurement/gtm/snippets`) and copy the
   `<head>` and `<body>` snippets into your site, the Stratum snippet into a Custom HTML tag, and the
   `sgtm_config` values (transport URL, ingest URL, `X-Source-Key`) into the server container's Stratum
   client/tag. No Google credentials are stored for GTM.

### Global settings

The only GA4/GTM environment variables are global toggles (see `.env.example`):

```
GA4_SYNC_ENABLED=true
GA4_LOOKBACK_DAYS=3
GA4_BACKFILL_DAYS=30
GA4_REQUEST_TIMEOUT_SECONDS=30
GA4_DEFAULT_CONVERSION_EVENT=purchase
GTM_VERIFY_TIMEOUT_SECONDS=10
GTM_DEFAULT_SERVER_CONTAINER_URL=
```

Property IDs, service-account keys and container IDs are per tenant and live in the database.

### Operator steps after deploying the measurement code

1. `cd backend && .venv/bin/python scripts_create_measurement_tables.py` (idempotent; creates
   `tenant_ga4_integrations`, `tenant_gtm_integrations`, `fact_ga4_daily`).
2. Restart the Celery worker and beat so the `app.workers.tasks.measurement` module and the new beat entries load.
3. `uvicorn --reload` and Vite pick up the code changes automatically.

### CSP

The frontend and API CSP allow `https://www.googletagmanager.com` (scripts) and the
`google-analytics.com` / `analytics.google.com` / `googletagmanager.com` hosts for connections and images.
For Paddle Billing they also allow `https://cdn.paddle.com` in `script-src` (Paddle.js v2) and
`https://*.paddle.com` in `connect-src` and `frame-src` (checkout overlay and customer portal; the wildcard
covers the `sandbox-*` hosts), and nginx opens `Permissions-Policy: payment` for `self`,
`https://buy.paddle.com` and `https://sandbox-buy.paddle.com`. The host list is identical in
`backend/app/middleware/security.py`, `frontend/nginx.conf` and `nginx/beta.conf`.
Google Fonts hosts are intentionally not allow-listed (out of scope).
