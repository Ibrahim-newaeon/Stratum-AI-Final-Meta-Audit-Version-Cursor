# Meta activation smoke checklist

Ordered operator path to verify Meta activation after connect. Autopilot
**writes stay off** (`AUTOPILOT_EXECUTION_ENABLED=false`,
`AUTOPILOT_EXECUTION_DRY_RUN=true`). Do not flip those for this smoke.

## Prerequisites

- [ ] Meta App: Valid OAuth Redirect =
  `{OAUTH_REDIRECT_BASE_URL}/api/v1/oauth/meta/callback`
- [ ] Meta App: Deauthorize + Data Deletion callbacks configured
  (see `docs/integrations/README.md`)
- [ ] `META_APP_ID` / `META_APP_SECRET` set
- [ ] `USE_MOCK_AD_DATA=false` on API + worker (mock skips real discovery/insights)
- [ ] Celery worker consuming `sync` (and `rules` if testing Rules)
- [ ] Tenant user email verified (OAuth connect requires it)

## 1. Connect Platforms (OAuth)

1. Open **Connect Platforms** in the tenant dashboard.
2. Start Meta Ads OAuth; complete Facebook dialog.
3. Confirm status shows **Connected** (token stored encrypted on
   `tenant_platform_connection`).
4. Confirm ad accounts appear / can be enabled.

## 2. Campaign discovery

1. On **Campaigns**, click **Discover from Meta** (queues Celery discovery).
2. Or: `POST /api/v1/campaigns/discover` as the tenant.
3. Wait for the task; refresh the list — local rows should appear with Meta
   `external_id` values.

## 3. Insights sync

1. With discovery done and `USE_MOCK_AD_DATA=false`, wait for beat
   `sync-all-campaigns` (hourly) or trigger the sync path used in staging.
2. Confirm metrics rows exist and `last_synced_at` is fresh **only when
   metrics were written** (empty sync must not look healthy).

## 4. Conversions API (CAPI)

1. Open **CAPI Setup**.
2. Connect Meta Pixel + CAPI token (and optionally WhatsApp **CAPI** fields —
   those are Conversions delivery, **not** Module G messaging).
3. Send a synthetic / test event; confirm delivery logs / EMQ path
   (`docs/architecture/emq-capi-ops.md`).

## 5. WhatsApp messaging credentials (Module G)

Distinct from CAPI WhatsApp on CAPI Setup.

1. Open WhatsApp Manager → **Credentials**, or:
   - `PUT /api/v1/whatsapp/credentials` with `phone_number_id` + `access_token`
   - `GET /api/v1/whatsapp/credentials` (status only; never returns secrets)
2. Confirm status connected before relying on outbound messaging.

## 6. Autopilot (read-only check)

1. `GET /api/v1/tenant/{id}/autopilot/status` should report
   `meta_writes_enabled: false` (and dry-run true) on a default deploy.
2. Do **not** set `AUTOPILOT_EXECUTION_ENABLED=true` during this smoke.

## Related

- Integration boundaries: `docs/integrations/README.md`
- Trust / Rules LOCAL_ONLY: `docs/architecture/trust-engine.md`
- Product priority / redesign deferral: `docs/feature-audit.md`
