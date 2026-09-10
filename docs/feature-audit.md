# Stratum AI Feature Audit

**Branch:** `cursor/feature-audit-dd73`  
**Grounded in:** implementation, tests, `CLAUDE.md`, `docs/` (not README/marketing).  
**Constraints:** no write/automation paths enabled; no production changes.

---

## Executive summary

### What works today with zero extra setup

With the Cloud Agent / local native stack (Postgres 16, Redis, backend venv, Celery on `sync`/`rules`, Vite on 5173) and demo seed scripts:

- API health (`/health`) with healthy database + Redis
- Auth login (`demo@stratum.ai` / `demo1234` after seed)
- Dashboard / overview / campaigns / analytics UI against **seeded + mockable** DB data
- Trust Gate evaluation and signal-health rollup (fail-closed; no Meta write)
- Meta insights **code path** (GET-only) — live only after OAuth + `USE_MOCK_AD_DATA=false`
- Autopilot **apply queue is scheduled** every 5 minutes but **writes nothing** while defaults hold (`AUTOPILOT_EXECUTION_ENABLED=false`, `AUTOPILOT_EXECUTION_DRY_RUN=true`)
- Automation Rules CRUD + gated **local** campaign mutations (not Meta writes)
- Facebook Login code path exists but is **off** (`FACEBOOK_LOGIN_ENABLED=false`)

### What needs credentials / config

| Need | Why |
|------|-----|
| `META_APP_ID` / `META_APP_SECRET` + App Dashboard redirect/privacy URLs | OAuth connect, signed deauthorize/data-deletion |
| Meta App Review: `ads_read` (insights), `ads_management` (writes/audiences) | Live Marketing API |
| Tenant Meta OAuth reconnect so stored token has scopes | Tokens are encrypted on `tenant_platform_connection` |
| `USE_MOCK_AD_DATA=false` (+ never `true` in production) | Switch insights from mock generator to Graph GET |
| Per-tenant CAPI pixel + access token (durable storage still a gap) | EMQ / delivery logs / 60% of signal health |
| `WHATSAPP_*` env (global today) | WhatsApp Cloud API messaging |
| Paddle price IDs + webhook secret | Billing |
| GA4 property / GTM container | Measurement & Verification baseline |
| `SENTRY_DSN` / `VITE_SENTRY_DSN` | Error tracking (optional) |
| Autopilot enable sequence (see Autopilot section) | Real Meta budget/bid/pause writes |

### What needs real engineering work

1. Wire Connect Platforms / campaign-builder connect UX to real `/api/v1/oauth/meta/*` (retire stub OAuth URLs).
2. Persist encrypted per-tenant CAPI credentials (today: in-memory connectors).
3. Audience-sync credential API + encryption + optional beat for `next_sync_at`.
4. Campaign **discovery** sync from Meta (today: metrics only for local campaigns with `external_id`).
5. Custom Autopilot frontend is largely mock; mount real queue UI including `applying`.
6. Rules engine mutates local DB only — Meta drift until insights overwrite; decide product rule.
7. Per-tenant WhatsApp credentials if multi-tenant SaaS is required.
8. Align `.env.example` comments with code (apply-actions **is** scheduled; document signal-health env knobs).
9. ML: confirm model artifacts under `ML_MODELS_PATH` vs local/heuristic providers.
10. Fix Celery terminal command so `--queues "$(python scripts_print_celery_queues.py)"` never expands empty at boot.

---

## Ranked roadmap (top 10 — easiest wins first)

| # | Action | Effort | Unlocks |
|---|--------|--------|---------|
| 1 | Set Meta app IDs/secrets, redirect URI, privacy callbacks; `USE_MOCK_AD_DATA=false` in a non-prod tenant | Config | Live insights for connected ad accounts |
| 2 | Point SPA connect flow at `/api/v1/oauth/meta/authorize` (drop campaign_builder stub URL) | Small FE/BE | Operators can connect without docs gymnastics |
| 3 | Seed/ensure campaigns have Meta `external_id` + enabled `tenant_ad_account` after OAuth | Small | Hourly `sync_all_campaigns` writes real metrics |
| 4 | Persist encrypted CAPI pixel/token per tenant; stop relying on in-memory connect | Medium | Real EMQ + trust-gate scoring from delivery logs |
| 5 | Keep autopilot dry-run day: `ENABLED=true`, `DRY_RUN=true`; verify audit intents | Config + ops | Safe rehearsal of write path |
| 6 | App Review `ads_management` + tenant reconnect; then `DRY_RUN=false` only after #5 | External + config | Real Meta writes for SAFE allowlist |
| 7 | Currency Custom Autopilot UI with live `fact_actions_queue` (+ `applying` status) | Medium FE | Operators can approve/monitor without seed theater |
| 8 | Audience credential upsert API (encrypted) or reuse Meta OAuth token | Medium | Custom Audiences without seed-only tokens |
| 9 | Campaign discovery job (list campaigns/adsets from Meta → local rows) | Medium | End-to-end activation without manual campaign rows |
| 10 | Decide Rules→Meta policy (local-only vs route through `write_client` + gate) | Product + eng | No silent Ads Manager drift |

---

## Feature matrix

Legend: ✅ works (real) · 🟡 works with mock data · 🟠 partially implemented · 🔴 stubbed or missing

---

### Meta activation + OAuth + insights (deep dive)

| Field | Detail |
|-------|--------|
| **Status** | 🟠 OAuth/storage real; Connect UI stubbed · ✅ Insights GET path real · 🟡 Mock when `USE_MOCK_AD_DATA=true` |
| **Where** | `backend/app/services/oauth/meta.py`, `api/v1/endpoints/oauth.py`, `meta_callbacks.py`, `services/meta/insights_client.py`, `insights_ingestion.py`, `workers/tasks/sync.py`; FE: `ConnectPlatforms.tsx`, onboarding chat OAuth link; stub: `campaign_builder` `connect/.../start` |
| **Config** | `META_APP_ID`, `META_APP_SECRET`, `OAUTH_REDIRECT_BASE_URL`, `META_GRAPH_API_VERSION` (default `v23.0`), `USE_MOCK_AD_DATA`, `META_INSIGHTS_*`, `PII_ENCRYPTION_KEY` |
| **Secrets / external** | Meta App; Valid OAuth Redirect `{OAUTH_REDIRECT_BASE_URL}/api/v1/oauth/meta/callback`; Deauthorize + Data Deletion URLs; App Review **`ads_read`** (insights) / **`ads_management`** (writes); scopes include `ads_management`, `ads_read`, `business_management`, `pages_read_engagement` |
| **Runtime** | Postgres (`tenant_platform_connection`, `tenant_ad_account`, campaigns/metrics); Redis (OAuth CSRF); Celery beat `sync-all-campaigns` hourly on queue **`sync`** |
| **Gaps** | Wire SPA to real OAuth; campaign discovery; mirror insights knobs in `backend/.env.example`; never advance `last_synced_at` without metrics (live path already refuse-empty) |

**Mock → live Meta data**

1. `USE_MOCK_AD_DATA=false` (hard-fail if `true` with `APP_ENV=production`).
2. Complete Meta OAuth → encrypted token on `tenant_platform_connection`.
3. Enable `tenant_ad_account`; campaigns need Meta `external_id`.
4. Celery worker consuming `sync` + beat running.
5. Insights client issues **GET only** (`ads_read`); token `190` marks connection disconnected and does not fake success.

---

### Custom Autopilot + Trust Gate + Meta write path (deep dive)

| Field | Detail |
|-------|--------|
| **Status** | ✅ Gate + executor + write client real · 🔴 writes off by default · 🟡 Custom Autopilot FE largely mock |
| **Where** | Gate: `tasks/apply_actions_queue.py` (`check_signal_health`, `evaluate_signal_health`); scoring: `services/signal_health/*`; executor: `services/meta/action_executor.py`; writes: `services/meta/write_client.py`; API: `api/v1/endpoints/autopilot.py`; model: `FactActionsQueue` in `models/trust_layer.py`; FE: `CustomAutopilotRules.tsx` (mock), `api/autopilot.ts` (real), orphan `AutopilotPanel.tsx`; docs: `docs/architecture/trust-engine.md` |
| **Config** | `AUTOPILOT_EXECUTION_ENABLED` default **false**; `AUTOPILOT_EXECUTION_DRY_RUN` default **true**; `AUTOPILOT_EXECUTABLE_ACTION_TYPES` default `budget_decrease,pause_adset,bid_decrease`; daily rails; `AUTOPILOT_DAILY_BUDGET_LIMITS_BY_CURRENCY` for offset-1 currencies; signal health thresholds `SIGNAL_HEALTH_*`, `TRUST_GATE_*` |
| **Secrets / external** | Meta token with **`ads_management`** (App Review + reconnect); Graph reachability; encryption key for tokens |
| **Runtime** | Beat `autopilot-apply-actions-queue` every **5 min** on queue **`sync`**; signal-health rollup 02:00 UTC on `sync`; Postgres + Redis |
| **Gaps** | Live Autopilot UI; frontend `ActionStatus` missing `applying`; fix stale `.env.example` “NOT scheduled” comment; do not widen allowlist past `SAFE_ACTIONS` without tracing all consumers |

**Why writes are disabled by default**

Scheduling ≠ enabling. The beat entry runs so ops can see the worker path, but:

- `autopilot_execution_enabled=false` → batch returns before querying approved rows; executor refuses before token decrypt.
- `autopilot_execution_dry_run=true` → full checks + absolute target resolution, **no** Meta POST; row stays `approved`.

**Safely enable Autopilot writes**

1. App Review `ads_management`; tenant reconnect.
2. Worker on `sync`; fresh `fact_signal_health_daily` (else gate BLOCK).
3. `AUTOPILOT_EXECUTION_ENABLED=true`, keep `DRY_RUN=true` for a full day; inspect intents/audit.
4. Set currency floors/ceilings for JPY/KRW/VND/etc.
5. Then `AUTOPILOT_EXECUTION_DRY_RUN=false`.
6. Never bypass Trust Gate; preserve double-commit (`approved`→`applying` with absolute target before write; per-outcome commit) and day-scoped rails on **`applied_at`**.

**Trust Gate thresholds (defaults)**

| Score | Decision | Automation |
|------:|----------|------------|
| ≥ 70 | PASS | May execute |
| 40–69 | HOLD | Alert / hold |
| &lt; 40 | BLOCK | Manual |
| No / unscorable snapshot | BLOCK | Fail-closed |
| Stale snapshot | HOLD (configurable) | Never PASS |

---

### Automation Rules

| Field | Detail |
|-------|--------|
| **Status** | 🟠 Backend real (local mutations + gate); FE falls back to mock when empty |
| **Where** | `api/v1/endpoints/rules.py`, `workers/tasks/rules.py`, FE `views/Rules.tsx` |
| **Config** | Trust-gate settings; WhatsApp if `send_alert` used |
| **Secrets / external** | Optional WhatsApp |
| **Runtime** | Beat `evaluate-active-rules` every 15 min, queue **`rules`** |
| **Gaps** | Does **not** call `write_client`; local `Campaign` fields can drift from Ads Manager |

---

### CAPI Setup + delivery

| Field | Detail |
|-------|--------|
| **Status** | 🟠 Real Graph POST + `capi_delivery_logs`; credentials ephemeral in-process |
| **Where** | `services/capi/*`, `api/v1/endpoints/capi.py`, FE `CAPISetup.tsx`; stub `meta_capi.py` health-only |
| **Config** | `META_GRAPH_API_VERSION`; house pixel `META_PIXEL_ID` / `META_ACCESS_TOKEN`; per-tenant via API body today |
| **Secrets / external** | Events Manager pixel + CAPI token / System User |
| **Runtime** | Postgres `capi_delivery_logs` (migration `0004`); feeds signal health EMQ weight (~40–60% depending on docs/code weights) |
| **Gaps** | Encrypted durable credential store; attribute every send with `tenant_id` |

---

### Facebook Login (auth, not activation)

| Field | Detail |
|-------|--------|
| **Status** | ✅ Implemented · 🔴 off by default |
| **Where** | `services/meta/login_client.py`, `api/v1/endpoints/auth_facebook.py`, FE `FacebookLoginButton.tsx` |
| **Config** | `FACEBOOK_LOGIN_ENABLED=false`, `FACEBOOK_LOGIN_CONFIG_ID`, `FACEBOOK_LOGIN_ALLOW_SIGNUP`, `FACEBOOK_LOGIN_AUTO_LINK_BY_EMAIL=false` |
| **Secrets / external** | Same `META_APP_ID`/`SECRET`; Allowed Domains; Advanced Access `email`; User token config (not System User) |
| **Runtime** | Postgres `user_social_identity`; **no** `tenant_platform_connection` |
| **Gaps** | Enable only after dashboard + Advanced `email`; never treat as ads activation |

---

### Custom Audiences / audience sync

| Field | Detail |
|-------|--------|
| **Status** | 🟠 Meta connector real; credential API / auto-sync beat missing |
| **Where** | `services/cdp/audience_sync/*`, `api/v1/endpoints/audience_sync.py` |
| **Config** | `META_GRAPH_API_VERSION` |
| **Secrets / external** | Token with Custom Audience + typically `ads_management` |
| **Runtime** | Manual sync API; seed creates credentials in demo |
| **Gaps** | Upsert API + encryption; beat for `next_sync_at`; stop plaintext token column |

---

### WhatsApp (messaging)

| Field | Detail |
|-------|--------|
| **Status** | 🟠 Real Cloud API client; global env credentials |
| **Where** | `api/v1/endpoints/whatsapp.py`, WhatsApp services/workers |
| **Config** | `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_BUSINESS_ACCOUNT_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_API_VERSION`, `ENABLE_WHATSAPP` |
| **Secrets / external** | Meta WhatsApp Business / Cloud API |
| **Runtime** | Beat `process-scheduled-whatsapp` ~1 min |
| **Gaps** | Per-tenant credentials; align API version with `META_GRAPH_API_VERSION` |

---

### Remaining features (scaffold — filling next)

| Feature | Status (initial) | Notes |
|---------|------------------|-------|
| Overview dashboard | 🟡 | Seeded metrics; live after Meta sync |
| Custom Dashboard | 🟠 | Widget catalog exists; verify persistence |
| Campaigns | 🟡/✅ | CRUD + metrics; live spend needs insights |
| Stratum Analytics | 🟠 | Audit next |
| Benchmarks | 🟠 | Audit next |
| Competitor Intelligence | 🟡 | `MARKET_INTEL_PROVIDER=mock` typical |
| Asset Library | 🟠 | Audit next |
| Custom Reports | 🟠 | Audit next |
| CDP | 🟠 | Large surface; seed populates; live ingest paths TBD |
| Organizations | 🟠 | Audit next |
| ML Training + predictions | 🟡/🟠 | `ML_PROVIDER=local`; verify model files |
| Data Quality | 🟠 | Audit next |
| EMQ Dashboard | 🟠 | Tied to CAPI delivery |
| Measurement & Verification (GA4/GTM) | 🟠 | Read-only GA4; not activation |
| Paddle billing | 🟠 | Thin httpx client; needs price IDs + webhook |
| Settings | 🟠 | Audit next |
| Celery worker/beat jobs | ✅/🟠 | Scheduled; queues must be non-empty at start |
| Sentry | 🟠 | Optional; inactive without DSN |

---

## Sentry (ops plugin — brief)

| Field | Detail |
|-------|--------|
| **Status** | 🟠 Integrated, inactive until DSN set |
| **Where** | Backend: `app/main.py` lifespan `sentry_sdk.init` when `settings.sentry_dsn`; exception handler `capture_exception`. Frontend: `frontend/src/lib/sentry.ts` (`initSentry` from `main.tsx`), `@sentry/react` ErrorBoundary, user/tenant tags |
| **Config** | `SENTRY_DSN` (backend), `VITE_SENTRY_DSN` / `VITE_SENTRY_DEBUG` (frontend) |
| **Behavior** | Prod sample rates lower; FE `beforeSend` drops network/cancelled errors and **does not send** in non-prod unless `VITE_SENTRY_DEBUG`; CSP allows `https://*.sentry.io` |
| **Gaps** | No DSN in default Cloud install — console skip only |

---

## Explicit callouts

### Mock vs real (current defaults in examples / Cloud install)

| Flag | Typical local | Effect |
|------|---------------|--------|
| `USE_MOCK_AD_DATA` | `true` in `.env.example` | Mock insights time series; advances `last_synced_at` |
| `MARKET_INTEL_PROVIDER` | `mock` | Competitor intel without live vendors |
| `ML_PROVIDER` | `local` | Local/heuristic models — **verify files under `ML_MODELS_PATH`** |
| Autopilot flags | enabled=false, dry_run=true | No Meta writes |

### Autopilot write path

Real code in `write_client.py` / `action_executor.py`. Disabled by dual flags so merge/deploy cannot spend. Beat schedules `tasks.apply_actions_queue` every 5 minutes on `sync`; disabled runs read no approved rows and write no audit flood.

### Switching mock → live Meta

`USE_MOCK_AD_DATA=false` + OAuth connection + ad account + campaign `external_id` + Celery `sync` worker/beat. Insights remain GET-only (`ads_read`). Writes are a separate checklist under Autopilot.

---

## Audit progress

- [x] Meta OAuth / insights / CAPI / audiences / WhatsApp / Facebook Login
- [x] Trust Gate / Autopilot / Automation Rules / enable checklist
- [x] Sentry brief
- [x] Exec summary + top-10 roadmap (Meta-weighted)
- [ ] Overview, Custom Dashboard, Campaigns, Analytics, Benchmarks, Competitors, Assets
- [ ] Custom Reports, CDP, Organizations, ML, Data Quality, EMQ
- [ ] Measurement (GA4/GTM), Paddle, Settings, Celery job inventory
- [ ] Verify ML model files on disk; Celery beat map completeness

*Next commit: fill remaining matrix rows from code.*
