# Billing: Paddle Billing

Stratum AI bills tenant subscriptions through **Paddle Billing** (Merchant of Record). Paddle hosts the
checkout, collects payment, handles tax and invoices, runs dunning, and tells Stratum what happened through
signed webhooks. Stratum never sees or stores card data.

Paddle is the only billing and payment integration in the project (see
[No other payment provider](#no-other-payment-provider)).

## Components

| Layer | File(s) | Role |
|-------|---------|------|
| Config | `backend/app/core/config.py` (`paddle_*` settings) | Keys, environment, price ids |
| Client | `backend/app/services/paddle_service.py` | Thin `httpx.AsyncClient` over the Paddle REST API (no SDK), typed dataclasses, tier <-> price mapping, webhook signature verification, Tenant sync helpers |
| API | `backend/app/api/v1/endpoints/billing.py`, `backend/app/schemas/billing.py` | Authenticated tenant billing routes under `/api/v1/billing` |
| Webhook | `backend/app/api/v1/endpoints/paddle_webhook.py` | Public `POST /api/v1/webhooks/paddle` |
| Models | `backend/app/base_models.py` (`Tenant` paddle columns, `PaddleWebhookEvent`) | Subscription state per tenant, idempotency |
| Migration | `backend/scripts_migrate_paddle_columns.py` | Idempotent column rename/add + webhook table |
| Frontend | `frontend/src/api/billing.ts`, `frontend/src/lib/paddle.ts`, `frontend/src/components/settings/PaddleBilling.tsx`, `frontend/src/views/billing/BillingSuccess.tsx` | Billing tab, Paddle.js v2 overlay checkout, checkout success page |

## Configuration

Settings (`backend/app/core/config.py`) and their environment variables:

| Setting | Env | Default | Notes |
|---------|-----|---------|-------|
| `paddle_api_key` | `PADDLE_API_KEY` | empty | Server-side API key. Empty means billing is disabled (`settings.paddle_enabled` is `False`). |
| `paddle_client_token` | `PADDLE_CLIENT_TOKEN` | empty | Client-side token for Paddle.js; served to the SPA by `GET /api/v1/billing/config`. |
| `paddle_webhook_secret` | `PADDLE_WEBHOOK_SECRET` | empty | Notification destination secret key; verifies `Paddle-Signature`. |
| `paddle_environment` | `PADDLE_ENVIRONMENT` | `sandbox` | `sandbox` or `production`. |
| `paddle_starter_price_id` | `PADDLE_STARTER_PRICE_ID` | empty | Recurring `pri_...` price for Starter. |
| `paddle_professional_price_id` | `PADDLE_PROFESSIONAL_PRICE_ID` | empty | Recurring `pri_...` price for Professional. |
| `paddle_enterprise_price_id` | `PADDLE_ENTERPRISE_PRICE_ID` | empty | Recurring `pri_...` price for Enterprise. |

Derived properties: `settings.paddle_enabled` (`bool(paddle_api_key)`), `settings.paddle_fully_configured`
(API key, client token, webhook secret and price ids all present) and `settings.paddle_api_base_url`
(`https://sandbox-api.paddle.com` or `https://api.paddle.com`).

Everything is optional with defaults so CI and the dev server boot without Paddle. The settings validator
raises only when `APP_ENV=production` **and** an API key is set while `PADDLE_ENVIRONMENT` is not
`production` or a companion value (client token, webhook secret) is missing.

The frontend has a single optional override, `VITE_PADDLE_ENVIRONMENT`; the client token always comes from
`GET /api/v1/billing/config`, never from a frontend env file.

### Sandbox vs production

|  | Sandbox | Production |
|--|---------|------------|
| Dashboard | `https://sandbox-vendors.paddle.com` | `https://vendors.paddle.com` |
| REST base | `https://sandbox-api.paddle.com` | `https://api.paddle.com` |
| Paddle.js | `Paddle.Environment.set('sandbox')` before `Paddle.Initialize` | default |
| Keys, prices, webhook secret | sandbox account values | production account values (separate account) |

Sandbox and production are separate Paddle accounts: keys, `pri_` ids, customer ids and notification
destinations do not carry over. Going live means replacing all seven values and creating the notification
destination again in the production dashboard.

### Paddle REST usage (thin client)

`PaddleClient` sends `Authorization: Bearer <API key>` and `Paddle-Version: 1` to the base URL above and uses:

- `POST /customers`, `GET /customers/{id}`
- `GET /subscriptions?customer_id=...`, `GET /subscriptions/{id}`
- `PATCH /subscriptions/{id}` (items + proration for tier changes; `scheduled_change: null` to undo a pending cancellation)
- `POST /subscriptions/{id}/cancel` (`effective_from: next_billing_period | immediately`)
- `GET /transactions?customer_id=...&subscription_id=...`, `GET /transactions/{id}`, `GET /transactions/{id}/invoice` (invoice PDF URL)
- `POST /customers/{id}/portal-sessions` (customer portal overview, cancel and update-payment-method URLs)

Entity id prefixes: customers `ctm_`, products `pro_`, prices `pri_`, subscriptions `sub_`, transactions
`txn_`, addresses `add_`, businesses `biz_`.

Errors surface as `PaddleError(status_code, code, detail)`. A missing API key raises
`PaddleNotConfiguredError` (503) on first use of `get_paddle_client()`, not at import time, so the app boots
without Paddle configured.

## Tenant state

`tenants` columns:

| Column | Type | Meaning |
|--------|------|---------|
| `paddle_customer_id` | VARCHAR(255) NULL | Paddle customer (`ctm_...`) |
| `paddle_subscription_id` | VARCHAR(255) NULL | Paddle subscription (`sub_...`) |
| `subscription_status` | VARCHAR(32) NULL | Paddle status string: `active`, `trialing`, `past_due`, `paused`, `canceled` |
| `current_period_end` | TIMESTAMPTZ NULL | End of the current billing period |
| `plan` (existing) | VARCHAR(50) | `free`, `starter`, `professional`, `enterprise`; consumed unchanged by `core/subscription.py` |
| `plan_expires_at` (existing) | TIMESTAMPTZ | Set from the Paddle period end |

`paddle_webhook_events` (`event_id` PK, `event_type`, `occurred_at`, `processed_at`) records every processed
webhook event id for idempotency.

Sync rules (`sync_tenant_subscription`):

- `tier = get_tier_for_price_id(items[0].price.id)`. An unknown price id logs a warning and leaves `plan`
  untouched; a tenant is never silently downgraded.
- `active` / `trialing` / `past_due`: `plan = tier`, `plan_expires_at = current_period_end`
- `paused`: `plan` untouched, `plan_expires_at = current_period_end` when present
- `canceled` (or the `subscription.canceled` event): `plan = 'free'`,
  `plan_expires_at = canceled_at or current_period_end or now`
- Always set `paddle_subscription_id`, `subscription_status`, `current_period_end`; set `paddle_customer_id`
  when empty
- `cancel_at_period_end` is `scheduled_change.action == 'cancel'`; reactivating sends `PATCH scheduled_change: null`
- Trials are configured on the Paddle price (no per-request trial days)

## API routes

Prefix `/api/v1/billing`, tenant taken from the authenticated request, every response wrapped in the
`APIResponse` envelope (`{success, data, error?}`), Pydantic models in `backend/app/schemas/billing.py`.

| Method | Path | Body | Returns |
|--------|------|------|---------|
| GET | `/billing/config` | - | `paddle_configured`, `environment`, `client_token`, `price_ids{starter,professional,enterprise}`, `tiers[]` (`tier`, `name`, `price`, `currency`, `billing_period`, `description`) |
| GET | `/billing/subscription` | - | `paddle_configured`, `has_customer`, `has_subscription`, `customer_id`, `subscription_id`, `status`, `tier`, `plan`, `current_period_start`, `current_period_end`, `next_billed_at`, `cancel_at_period_end`, `canceled_at`, `paused_at`, `trial_end` |
| POST | `/billing/checkout-session` | `{tier, success_url?}` | `price_id`, `client_token`, `environment`, `customer_id`, `customer_email`, `custom_data{tenant_id, tier}`, `success_url`, `display_mode: 'overlay'`; 503 when not configured, 400 when the tier has no price id |
| POST | `/billing/portal-session` | `{return_url?}` | `portal_url`, `cancel_url`, `update_payment_method_url`; 400 "No billing account found" without a customer |
| POST | `/billing/cancel` | `{at_period_end: true}` | subscription snapshot; 404 without an active/trialing/past_due subscription |
| POST | `/billing/reactivate` | - | subscription snapshot; 404 unless `cancel_at_period_end` |
| POST | `/billing/upgrade` | `{new_tier, prorate: true}` | subscription snapshot |
| GET | `/billing/transactions?limit=10` | - | `[{id, invoice_number, status, subscription_id, amount_minor, currency, billed_at, created_at}]` |
| GET | `/billing/transactions/{transaction_id}/invoice` | - | `{transaction_id, invoice_url}` (Paddle invoice PDF) |

Timestamps are ISO-8601 UTC strings and amounts are integer minor units. `PaddleNotConfiguredError` maps to
503, any other `PaddleError` to 502 with Paddle's detail.

### Checkout (client-side)

Checkout runs in the browser. The SPA loads `https://cdn.paddle.com/paddle/v2/paddle.js`
(`frontend/src/lib/paddle.ts`), calls `Paddle.Environment.set('sandbox')` when the API reports the sandbox,
`Paddle.Initialize({ token, eventCallback })`, then:

```js
Paddle.Checkout.open({
  items: [{ priceId, quantity: 1 }],
  customer: { email },
  customData: { tenant_id },
  settings: { successUrl, displayMode: 'overlay' },
});
```

The success URL is `/dashboard/billing/success`; the Billing tab lives at `/dashboard/settings?tab=billing`.
Pricing CTAs on the public marketing pages are navigation only: checkout happens in Settings > Billing after
signup, and no public static HTML (`frontend/public/*.html`) loads Paddle.js.

## Webhook

`POST /api/v1/webhooks/paddle` is public (listed in `TenantMiddleware.PUBLIC_ENDPOINTS`). Create a
Notification destination in Paddle (Developer Tools > Notifications) with the URL
`https://<api-host>/api/v1/webhooks/paddle`, subscribed to `subscription.*`, `transaction.*`, `customer.*`
and `adjustment.created`, and copy its secret key into `PADDLE_WEBHOOK_SECRET`.

Verification: header `Paddle-Signature: ts=<unix>;h1=<hex>`. `h1` must equal HMAC-SHA256 (destination
secret) over `"<ts>:" + raw body`, compared in constant time; requests with `|now - ts| > 300s` are rejected.
Payload shape: `{ event_id, event_type, occurred_at, notification_id, data: { ...entity } }`.

| Status | When |
|--------|------|
| 503 | no webhook secret configured |
| 400 | missing, invalid or stale signature; malformed JSON |
| 200 `{status: 'received' \| 'duplicate' \| 'ignored', event_type}` | processed / already seen / not relevant |
| 500 | handler exception (after rollback); Paddle retries the delivery |

Idempotency: the `event_id` is inserted into `paddle_webhook_events` first; an `IntegrityError` means
`duplicate`. Tenant resolution: `int(data.custom_data.tenant_id)` first, otherwise
`Tenant.paddle_customer_id == data.customer_id`.

| Event | Action |
|-------|--------|
| `subscription.created`, `subscription.activated`, `subscription.updated`, `subscription.trialing`, `subscription.past_due`, `subscription.paused`, `subscription.resumed` | `sync_tenant_subscription(subscription_from_payload(data))` and link the customer id |
| `subscription.canceled` | `clear_tenant_subscription(tenant, canceled_at)`: `plan = 'free'` |
| `transaction.completed`, `transaction.paid` | with `subscription_id`: `get_paddle_client().get_subscription(id)` then sync; otherwise link the customer id |
| `transaction.payment_failed` | no DB change; email tenant ADMIN users via `send_payment_failed_email` (attempt count from `payments`, amount from `details.totals`) |
| `customer.created`, `customer.updated` | `sync_tenant_paddle_customer` when `custom_data.tenant_id` is present |
| `transaction.created` / `billed` / `updated` / `ready`, `adjustment.created`, anything else | `ignored` |

## Customer portal

`POST /billing/portal-session` calls `POST /customers/{id}/portal-sessions` (optionally scoped to the
tenant's subscription ids) and returns the overview URL plus the cancel and update-payment-method deep
links. "Manage billing" and "Update payment method" in Settings > Billing open these in a new tab. Paddle
owns dunning, payment-method updates and invoice emails; Stratum only mirrors the resulting state via
webhooks.

## CSP

The host list is identical in `backend/app/middleware/security.py` (`build_csp`, production and
development), `frontend/nginx.conf` and `nginx/beta.conf`, and is asserted by
`backend/tests/unit/test_security_csp.py`:

- `script-src` adds `https://cdn.paddle.com`
- `connect-src` adds `https://*.paddle.com`
- `frame-src` is `'self' https://*.paddle.com`
- nginx `Permissions-Policy`: `camera=(), microphone=(), geolocation=(), payment=(self "https://buy.paddle.com" "https://sandbox-buy.paddle.com")`

The `*.paddle.com` wildcard covers the sandbox hosts (`sandbox-api.paddle.com`, `sandbox-buy.paddle.com`,
`sandbox-checkout-service.paddle.com`, `sandbox-cdn.paddle.com`). Paddle.js is loaded only from
`https://cdn.paddle.com/paddle/v2/paddle.js` and only inside the authenticated SPA.

## Migration for existing deployments

The database schema is created from the SQLAlchemy models (`create_all`), so upgrading an existing
deployment uses a one-off script rather than a new Alembic revision:

```bash
cd backend && python scripts_migrate_paddle_columns.py
```

It reads `DATABASE_URL_SYNC` (`settings.database_url_sync`) and is idempotent: it renames the legacy
billing customer-id column on `tenants` to `paddle_customer_id` when present, adds
`paddle_subscription_id`, `subscription_status` and `current_period_end` when missing, and creates
`paddle_webhook_events` (`checkfirst`). Fresh databases get the same shape from the models; the initial
Alembic revision was edited in place to the Paddle names so both paths match.

## No other payment provider

Paddle Billing is the only billing and payment integration. The former provider's name (spelled
S-T-R-I-P-E) must not appear anywhere in the repository: code, tests, docs, env examples, compose files,
HTML or comments. The only tolerated matches for a case-insensitive search of that name are the
pre-existing row-banding utility props in `frontend/src/components/ui/data-table.tsx` and
`frontend/src/components/ui/progress.tsx` (their name is that word plus a trailing "d"); do not rename them.

## Related

- Operator setup: `SERVER_DEPLOYMENT_GUIDE.md`, Step 8 "Configure Paddle Billing"
- Runbook: `docs/05-operations/runbooks.md`, "Paddle Webhook Failures"
- Backend overview: `docs/02-backend/backend-overview.md`
- Integrations index: [README](./README.md)
