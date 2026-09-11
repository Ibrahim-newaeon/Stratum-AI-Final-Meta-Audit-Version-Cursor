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
- WhatsApp Cloud API for messaging and conversation attribution. Module G outbound messaging uses per-tenant encrypted rows in ``tenant_whatsapp_credentials`` (``PUT /api/v1/whatsapp/credentials``); global ``WHATSAPP_*`` env is for webhook verify/signature, platform OTP, and development fallback only. Do not reuse ``tenant_capi_credentials`` for messaging — that table is Conversions API only.
- CDP Audience Sync pushes segments to Meta Custom Audiences (Facebook, Instagram, WhatsApp).

### Meta App Review callbacks (required for `ads_read` / `ads_management`)

App Review will **not** approve the app for `ads_read` or `ads_management` until both callbacks below
are configured and reachable, so this is a hard prerequisite for every customer's real Meta data.

Paste these two URLs into the Meta app dashboard, built from the **production API host** (the same
origin as `OAUTH_REDIRECT_BASE_URL`, because the API - not the SPA - serves them). Set that variable in
every deployed environment: its shipped default is `http://localhost:8000`, and the deletion callback
builds the status URL it returns to Meta from it. When it is unset or still loopback the callback falls
back to the origin the request arrived on rather than handing Meta a dead link, but the fallback depends
on the proxy forwarding the public host, so configure it deliberately.

| Meta app dashboard field | URL | What it does |
|---|---|---|
| **Deauthorize Callback URL** (App settings > Basic, and Facebook Login > Settings) | `https://<api-host>/api/v1/meta/deauthorize` | Meta calls it when a person removes the app from their Facebook settings, without ever opening Stratum. The matching `tenant_platform_connection` rows are set to `disconnected`, `access_token_encrypted` / `refresh_token_encrypted` / `token_ref` are cleared, and one audit row is written per connection. |
| **Data Deletion Request URL** (App settings > Basic > Data Deletion Instructions > "Data deletion callback URL") | `https://<api-host>/api/v1/meta/data-deletion` | Meta calls it when a person requests deletion of their data. It runs the erasure, files a `meta_data_deletion_request` row, and answers with exactly `{"url": ..., "confirmation_code": ...}` - the response shape Meta requires. A repeat request for the same Meta user within 24 hours returns the code already on file instead of filing another. |

The `url` it returns points at the public status page,
`GET https://<api-host>/api/v1/meta/data-deletion/status?code=<confirmation_code>`, which Meta requires
to keep showing a human-readable explanation of that request's state. It renders HTML for a browser and
JSON otherwise, reports only that one code's status, and answers an identical generic 404 for any code it
does not hold, so it cannot be used to discover which codes exist.

**Authentication.** All three are public (`PUBLIC_ENDPOINTS` in `backend/app/middleware/tenant.py`,
which the router guard in `backend/app/api/v1/guards.py` defers to) because Meta calls them
server-to-server with no `Authorization` header. They are not unauthenticated: each POST carries a
`signed_request` that `backend/app/services/meta/signed_request.py` verifies before any row is touched -
base64url `"<sig>.<payload>"`, HMAC-SHA256 over the **raw encoded payload string** keyed with
`META_APP_SECRET`, compared with `hmac.compare_digest`, and any `algorithm` other than `HMAC-SHA256`
rejected. The status page is authenticated by its 128-bit confirmation code. The app secret is never
logged, stored or echoed in an error.

A verified request must also be **fresh**: `issued_at` has to sit within 300 seconds of now (the same
tolerance the Paddle webhook applies), and a non-zero `expires` must not be in the past. A
`signed_request` is not a Meta-only secret - anyone who has authorised the app can obtain one for their
own app-scoped id from the JS SDK - so without that bound a captured or self-minted string would replay
forever. Only form-encoded bodies are read, deliberately: `AuditMiddleware` json-parses and stores the
body of every POST, so a JSON branch would persist the credential into the audit trail (`signed_request`
is on that middleware's redaction list too).

**How a Meta user maps to a connection.** Meta identifies the person only by the app-scoped user id
(ASID) inside the signed request, so `tenant_platform_connection.platform_user_id` stores it, written at
OAuth time from `GET /me` (`backend/app/services/oauth/meta.py`). Connections made **before** revision
`0003_meta_privacy_callbacks` carry `NULL` and cannot be matched; the callbacks answer
`200 {"status": "no_connection"}` for them and the tenant must reconnect Meta to become matchable. The
ASID cannot be backfilled without each tenant's live token, so the migration deliberately does not try.

**What deletion erases, and what it deliberately does not.** The callback deletes what Stratum AI
obtained **through Meta** for the person Meta names: the `tenant_platform_connection` row(s) they
authorised are set to `disconnected`, `access_token_encrypted` / `refresh_token_encrypted` / `token_ref`
are cleared, and `platform_user_id` - the app-scoped Meta id itself - is dropped. Each severance is
audited under the confirmation code.

It does **not** anonymise the Stratum AI account that authorised the connection. That account is an
email/password account of the tenant's own, usually an administrator's; it is not Meta-derived data, and
anonymising it deactivates the login irreversibly. A click inside Facebook - by anyone who has ever
connected, possibly across several tenants at once - must not be able to lock a paying customer out of
its own workspace with no notice and no undo. Account erasure stays behind the authenticated
`POST /api/v1/gdpr/anonymize`, which calls `backend/app/services/gdpr_erasure.py` (anonymises the `users`
row, deletes `notification_preferences` and `api_keys`, nulls `ip_address` / `user_agent` on
`audit_logs`). Campaign, metric and billing history are not personal data and are retained.

Because of that scope, the public status page distinguishes the two completed outcomes: a request that
matched a connection says the connection was disconnected and its tokens erased, and a request that
matched nothing says plainly that no data was found for that Meta account - which is exactly the
"legitimate justification" Meta's data-deletion page asks the status URL to give. It never claims an
erasure that did not happen, including for the pre-migration connections described above.

Meta retries any non-2xx, so an unknown Meta user is answered 200 - nothing is held for them and a retry
could never change that - while genuine database failures answer 5xx so Meta does retry. A deletion whose
erasure fails is recorded as `failed` and surfaces on the status page rather than being reported as
success.

### Meta campaign discovery (read-only)

Insights sync only attaches metrics to **local** `Campaign` rows that already carry a Meta
`external_id`. Campaign discovery fills that catalogue from Meta before the hourly insights pull.

**What is pulled.** One request per enabled ad account:

```
GET https://graph.facebook.com/<version>/act_<ad account id>/campaigns
    fields=id,name,status,effective_status,objective,start_time,stop_time,
           updated_time,daily_budget,lifetime_budget
```

Pagination matches insights (`paging.next` / `paging.cursors.after`). Hitting `META_INSIGHTS_MAX_PAGES`
while Meta still has pages fails that account's discovery without upserting a truncated catalogue for
it; other enabled accounts on the same tenant still proceed.

**Where it lands.** Each node upserts one `campaigns` row by `(tenant_id, platform=meta, external_id)`:
name, status (from `effective_status` when present), objective, schedule dates, `account_id` (the
`act_…` id), currency from `tenant_ad_account`, and the Meta payload in `raw_data`. Soft-deleted local
rows are revived when Meta still lists them.

**What it deliberately does not do.** It does not advance `campaigns.last_synced_at` (freshness remains
insights-only). It does not copy Meta `daily_budget` / `lifetime_budget` into `*_cents` columns - Meta
API units are not this schema's hundredths-of-major, and a wrong conversion would lie to every
dashboard. Budgets stay in `raw_data` only. It never calls the autopilot write client.

**When it runs.** Celery beat schedules `discover_all_campaigns` at minute 50 of every hour, shortly
before `sync_all_campaigns` at minute 0. Operators can also `POST /api/v1/campaigns/discover`. Newly
inserted campaigns are queued for an insights sync immediately.

**Key files**: `backend/app/services/meta/campaign_discovery_client.py`,
`backend/app/services/meta/campaign_discovery.py`, `backend/app/workers/tasks/sync.py`
(`discover_tenant_campaigns_task`, `discover_all_campaigns`).

### Meta Custom Audience auto-sync (CDP → Meta)

Platform audiences created with `auto_sync=true` store a `next_sync_at`. Celery beat runs
`sync_due_audience_syncs` every 15 minutes on the `cdp` queue, lists rows where
`auto_sync` is true and `next_sync_at <= now`, and enqueues `sync_platform_audience_task`
per row. A successful sync advances `next_sync_at` by `sync_interval_hours` (default 24).
Credentials stay encrypted via `AudienceSyncCredential.resolved_access_token()`.

**Key files**: `backend/app/services/cdp/audience_sync/service.py`
(`list_due_auto_sync_audiences`), `backend/app/workers/tasks/cdp.py`,
migration `0011_aud_sync_sched`.

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

## "Log in with Facebook" (authentication, not activation)

Signing in with a Facebook account is a **sign-in method**, not an integration with an ad account. It
reuses `META_APP_ID` / `META_APP_SECRET` because a person signs in to the same Meta app Stratum already
is, but it is otherwise entirely separate from the OAuth flow above: it asks for `public_profile` and
`email`, reads two Graph endpoints, and grants no `ads_read` and no `ads_management`. It creates no
`tenant_platform_connection` row and appears in no platform enum.

It is **off by default** (`FACEBOOK_LOGIN_ENABLED=false`).

### The flow

1. The SPA calls `GET /api/v1/auth/facebook/config`. When it answers `enabled: false` the button does
   not render and Meta's script is never loaded. The response carries only public values - app id,
   Graph version, scopes, optional `config_id`. The app secret is not in it.
2. `frontend/src/lib/facebookSdk.ts` injects `connect.facebook.net/en_US/sdk.js` **on demand** and calls
   `FB.init` once. The script is deliberately not in `frontend/public/*.html`, for the same reason
   Paddle.js is not: a tag there runs for every visitor of the marketing site whether or not the feature
   is on.
3. On **classic Facebook Login**, `FB.getLoginStatus` is checked first so an already-connected person
   skips the dialog; otherwise `FB.login` opens it with the configured scopes. On **Facebook Login for
   Business** (`config_id` present) the dialog always opens: `getLoginStatus` can only return an access
   token, and that flow needs a single-use code, so there is nothing to reuse.
4. The SPA posts **only** the credential to `POST /api/v1/auth/facebook` - `code` on Login for Business,
   `access_token` on classic login, exactly one of the two. The browser's `userID` is never sent,
   because it is not evidence of anything.

### The configuration must be a User access token configuration

Facebook Login for Business configurations come in two kinds, and only one of them can sign a person in.

| Configuration | Dialog the person sees | Token returned | Usable for sign-in |
|---|---|---|---|
| **User access token** | Consent for `public_profile`/`email` | User access token | **Yes** |
| **System User** (WhatsApp Embedded Signup, Conversions API onboarding, any "share business assets" template) | Business onboarding: portfolio, WhatsApp account, catalog | System user token | **No** |

Meta's own Access Token Guide draws the line: a System User token "performs programmatic, automated
actions on your business clients' Ad objects or Pages **without having to rely on input from an app
user**", while a User access token "is used if your app takes actions in real time, based on input from
the user". `login_client.py` requires `debug_token` to report `type` `USER`, so a System User token is
refused as `not_a_user_token` - which is correct, because it identifies a business portfolio and not a
person.

A System User configuration is also what forces `FACEBOOK_LOGIN_USE_CODE_FLOW`, so a deployment that
needs the code flow for a sign-in button has almost certainly pointed `FACEBOOK_LOGIN_CONFIG_ID` at the
wrong configuration.

### The two flows, and why the app decides

Meta documents two recipes, and which applies is a property of the saved configuration:

- A **User access token** configuration takes `config_id` and nothing else. Its documented example is
  literally `{ config_id: '<CONFIG_ID>' }`, and the dialog returns an access token.
- A **System User** configuration "require[s] the authorization code grant type", so `response_type` is
  `'code'` and `override_default_response_type` "must be set to true. When true, any response types
  passed in the response_type will take precedence over the default types."

`FACEBOOK_LOGIN_USE_CODE_FLOW` picks between them and defaults to false. Sending `code` to a User access
token configuration, or the SDK's default `token` to a System User one, fails the dialog before it
renders:

```text
Invalid parameter: response_type must be a valid enum.
response_type=token is not supported in this flow.
```

`override_default_response_type: true` must accompany it. Meta's guidance is explicit that it "must be
set to true. When true, any response types passed in the response_type will take precedence over the
default types" - without it the SDK keeps appending its own
`token,signed_request,graph_domain` and the dialog fails with the error above no matter what
`response_type` says.

So the browser holds an authorization code, and `MetaLoginClient.exchange_code_for_token` redeems it
server-side with `GET /oauth/access_token` (`client_id`, `client_secret`, `code`, and an **empty**
`redirect_uri`, which is what Meta requires for a code minted by the JS SDK). The app secret never
reaches the browser and neither does the resulting token.

The exchange changes only how the token is *obtained*. Every check below still runs against it, so a
code flow is trusted exactly as much as a token flow - which is to say, not at all until Meta confirms
it. Whether a deployment is on one flow or the other is set by `FACEBOOK_LOGIN_CONFIG_ID`, which must
match the app's configuration in the Meta dashboard; a `config_id` on a classic app, or none on a
Login-for-Business app, fails the dialog.

### Why the server re-derives the identity

The token arrives over a request the caller fully controls. A caller can post any string as a user id,
and can post a *real* access token minted for a **different Facebook app** where they are the developer.
So `backend/app/services/meta/login_client.py` calls `GET /debug_token` with the app access token and
refuses the sign-in unless the token is valid, unexpired, of type `USER`, and **issued to our own app
id**. It then reads `GET /me?fields=id,name,email` (with `appsecret_proof`) and requires the id to match
the one `debug_token` reported. Every call is GET; nothing is written to Meta.

### Account resolution

| Situation | Result |
|---|---|
| A `user_social_identity` row exists for the app-scoped id | Signed in to that account |
| The email matches an existing active account | **409** by default. "Same email address" is not proof of "same person", so the account is claimed by signing in with its password and connecting Facebook from account settings - something only its controller can do. `FACEBOOK_LOGIN_AUTO_LINK_BY_EMAIL=true` accepts the trade-off instead. |
| Nobody matches | A tenant and admin user are provisioned, mirroring `POST /auth/signup`, unless `FACEBOOK_LOGIN_ALLOW_SIGNUP=false` (then **403**) |

A provisioned account carries a random bcrypt hash nobody holds and `users.has_usable_password=false`, so
`POST /auth/login` can never sign into it with a password. That flag is also what stops
`DELETE /api/v1/auth/facebook/link` from removing an account's only way in; setting a password through
the reset flow clears the restriction.

**MFA is not bypassed.** An account with TOTP enabled gets the same `mfa_required` + `mfa_session_token`
answer a password login gets, completed by the existing `POST /api/v1/auth/login/mfa`.

### Privacy callbacks

`user_social_identity` stores the same app-scoped id the callbacks above carry. A **Data Deletion**
request deletes the link (audited, attributed to Meta rather than a user). A **Deauthorize** callback
deliberately does not: it asks to disconnect, not to erase, the deauthorized token stops verifying at
Meta on its own, and keeping the row means re-adding the app returns the person to the workspace they
already have instead of silently provisioning a second one. This mirrors the existing
`forget_platform_user_id` split for connections.

### Meta App Dashboard prerequisites

| Setting | Why it matters |
|---|---|
| **Facebook Login > Settings > Allowed Domains for the JavaScript SDK** | Must list the exact origin the SPA is served from. When the list is non-empty and your origin is missing, `FB.login()` refuses to run - the button fails in the browser with nothing in the server logs. |
| **App Review > `email` at Advanced Access** | At Standard Access the app can be live, but only people holding a role on the app get an email back. Everyone else provisions an account with no address, which stays unverified and cannot use password reset. |
| **Deauthorize Callback URL** | Already required for `ads_read`; see the table above. |

### Endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /api/v1/auth/facebook/config` | public | Whether to render the button, and the public SDK configuration |
| `POST /api/v1/auth/facebook` | public | Exchange a Facebook access token for Stratum tokens (or an MFA challenge) |
| `GET /api/v1/auth/facebook/link` | authenticated | Whether the caller's own account has Facebook attached |
| `POST /api/v1/auth/facebook/link` | authenticated | Attach Facebook to the caller's own account |
| `DELETE /api/v1/auth/facebook/link` | authenticated | Detach it, refused when it is the only way in |

The two public paths are listed in `PUBLIC_ENDPOINTS`; the three link routes are deliberately not,
because they change an existing account and therefore require that account's session.

**Key files**: `backend/app/services/meta/login_client.py` (Graph verification, GET only),
`backend/app/api/v1/endpoints/auth_facebook.py` (endpoints and account resolution),
`backend/app/models/social_identity.py` (`user_social_identity`),
`frontend/src/lib/facebookSdk.ts`, `frontend/src/components/auth/FacebookLoginButton.tsx`.

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
