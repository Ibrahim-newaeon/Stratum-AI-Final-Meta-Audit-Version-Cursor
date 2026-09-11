# EMQ + CAPI traffic (ops checklist)

Stratum's **EMQ / signal-health score** is measured from per-tenant
`capi_delivery_logs`, not from Meta's Events Manager UI.

## Prerequisites

1. Connect Conversions API credentials for the tenant
   (`PUT /api/v1/capi/credentials` or the CAPI Setup UI).
2. Send real events through a **tenant-scoped** path:
   - `POST /api/v1/capi/events/stream` or `/batch` (preferred), or
   - any CDP / sGTM ingest that fans out via `CAPIService` with `tenant_id`.
3. Include hashed identifiers when possible (`em`, `ph`, `external_id`,
   `fbc`, `fbp`) — identifier coverage is 30% of the EMQ component.

Landing-CMS / global `META_PIXEL_ID` sends **do not** feed tenant EMQ
(they omit `tenant_id` on delivery rows).

## When scores appear

- Celery beat `trust-signal-health-rollup` writes **yesterday's** row to
  `fact_signal_health_daily` at 02:00 UTC.
- Overview / EMQ v2 now falls back to the **latest measured day** when
  today has no row yet, so you are not stuck on "Not measured" overnight.
- Live dashboard signal-health uses a trailing window and can show earlier.

## Free plan note

Billing / Paddle is optional. Empty `PADDLE_API_KEY` keeps the product on
the free plan; EMQ + CAPI do not require Paddle.
