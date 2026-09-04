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

### Meta insights ingestion (read-only)

Campaign performance comes from the Meta Marketing API **Ads Insights** endpoint. This is the only real
ad-platform ingestion in the platform, and it is read-only in the strictest sense: the client issues
`GET` and nothing else, so it can never create, edit, pause or delete anything in an ad account. The
permission it needs is `ads_read`; `ads_management` is only required for the (separate, deliberately
unwired) autopilot write path.

**What is pulled.** One request per ad account per sync:

```
GET https://graph.facebook.com/<version>/act_<ad account id>/insights
    level=campaign
    time_increment=1                       # one row per campaign per day
    time_range={"since":"YYYY-MM-DD","until":"YYYY-MM-DD"}
    fields=campaign_id,campaign_name,impressions,clicks,spend,ctr,cpc,cpm,
           actions,action_values,video_p100_watched_actions,
           account_currency,date_start,date_stop
```

Pagination follows `paging.next` using `paging.cursors.after` (Meta returns `cursors` on the final page
too, so `next` is the only reliable "there is more" signal). If `META_INSIGHTS_MAX_PAGES` is reached
while Meta still advertises another page the window is **incomplete**, so the pull raises rather than
returning a short list: the sync records the truncation on `campaigns.sync_error`, leaves
`last_synced_at` alone and reports `failed` / `insights_truncated`. It is not retried, because a retry
truncates identically - raise the cap or narrow the window.

**Where it lands.** Each row becomes one `campaign_metrics` row, upserted by `(campaign_id, date)`:
impressions, clicks, conversions, `spend_cents`, `revenue_cents`, `video_views` (the `video_view`
action type, i.e. 3-second views - *not* the `video_p25_watched_actions` watch-depth count) and
`video_completions` (`video_p100_watched_actions`).

Conversions and revenue are read from the `actions` / `action_values` arrays for the action types
configured in `META_CONVERSION_ACTION_TYPES`. The first configured type present in `actions` wins, so
overlapping types describing the same conversion (`offsite_conversion.fb_pixel_purchase` and the grouped
`omni_purchase`) are never double counted - and the revenue for that **same** type is then read out of
`action_values`, so a count from one type is never paired with a value from another. Meta omits a type
from `action_values` when nothing was attributed to it; that is recorded as zero revenue with a warning,
never as another type's number. Keep a type in the list that your accounts actually report:
`offsite_conversion.fb_pixel_purchase` is web-pixel only, so an app-only or on-Facebook advertiser needs
`omni_purchase` or its conversions read as zero.

Money arrives from Meta as a decimal string in the ad account currency and is handled as `Decimal`
(never `float`) all the way to the integer column. The `*_cents` columns hold **hundredths of the
account's major unit for every currency** (every reader in the codebase divides by 100 with no currency
awareness), so a zero-decimal currency such as JPY or VND is quantised at its real precision and then
scaled by 100. That x100 is why those columns are `BigInteger` - `migrations/versions/…0002_widen_money_columns.py`
widens them, because int4 would cap a VND campaign at about US$860 of lifetime spend.

**Lookback and restatement.** Meta restates conversions for days after the fact as attribution windows
close, so every run re-pulls the last `META_INSIGHTS_LOOKBACK_DAYS` days and upserts them. Re-running a
day corrects it in place; it never duplicates.

**When there is no credential.** If the tenant has no Meta connection, the connection is not `connected`,
the token is missing or expired, no ad account is known, or the campaign carries no `external_id` to
identify its rows with, the sync writes **nothing**, records the reason on `campaigns.sync_error`, leaves
`last_synced_at` untouched - so freshness and therefore Signal Health degrade honestly - and returns a
`skipped` status with the reason. A token Meta rejects (error code 190) marks the connection
`disconnected` with `last_error`/`error_count` set; a throttling code (4 / 17 / 32 / 341 / 613 /
80000-range) **or a plain HTTP 429 with no Graph error envelope** is retried with the back-off Meta asks
for in `X-Business-Use-Case-Usage`, falling back to `Retry-After`.

**Freshness is only claimed for data we actually read.** `last_synced_at` advances only when Meta
returned rows for the campaign. An empty response is indistinguishable from a campaign deleted on Meta or
an `external_id` belonging to another ad account, so it leaves `last_synced_at` alone, records why on
`campaigns.sync_error` and returns `no_rows` rather than `success`. Freshness is 25% of Signal Health, so
a campaign nobody can read must never present as HEALTHY.

The access token is sent in the `Authorization: Bearer` header, never as a query parameter, so it cannot
reach a URL, a log line or an exception message.

**Configuration** (global only; per-tenant tokens and ad accounts live in `tenant_platform_connection`
and `tenant_ad_account`):

`META_GRAPH_API_VERSION` is the single Graph API version for **every** Meta caller - the insights client,
the Conversions API and WhatsApp Cloud connectors, offline conversions, CDP audience sync and the OAuth
flow all read it, so one knob moves them together. (`META_API_VERSION` survives only as a deprecated
per-flow override for OAuth; leave it unset.)

```
META_GRAPH_API_VERSION=v23.0
META_INSIGHTS_LOOKBACK_DAYS=7
META_INSIGHTS_REQUEST_TIMEOUT_SECONDS=30
META_INSIGHTS_MAX_PAGES=25
META_CONVERSION_ACTION_TYPES=offsite_conversion.fb_pixel_purchase,omni_purchase,purchase
```

**Key files**: `backend/app/services/meta/insights_client.py` (thin async httpx client, GET only),
`backend/app/services/meta/insights_ingestion.py` (mapping, money conversion, upsert, credential
resolution), `backend/app/workers/tasks/sync.py` (`sync_campaign_data`, `sync_all_campaigns`).
`USE_MOCK_AD_DATA` still switches in the local mock generator for development; it is rejected outright
when `APP_ENV=production`.

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
