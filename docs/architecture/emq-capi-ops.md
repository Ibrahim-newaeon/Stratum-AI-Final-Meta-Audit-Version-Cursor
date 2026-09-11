# EMQ + CAPI traffic (ops checklist)

Stratum's **EMQ / signal-health score** is measured from per-tenant
`capi_delivery_logs`, not from Meta's Events Manager UI.

## Prerequisites

1. Connect Meta Conversions API credentials for the tenant
   (`POST /api/v1/capi/platforms/connect` or the CAPI Setup UI).
   Tokens are Fernet-encrypted in `tenant_capi_credentials` so they survive
   process restarts (in-memory connectors are only a cache).
2. Send real events through a **tenant-scoped** path:
   - `POST /api/v1/capi/events/stream` or `/batch` (preferred), or
   - CDP / sGTM ingest (`POST /api/v1/cdp/events` or `/cdp/ingest`), which
     fans out accepted events via `CAPIService` with `tenant_id` after
     commit. Fan-out is skipped when advertising consent is explicitly
     `false`, or when no durable Meta credentials exist.
3. Include hashed identifiers when possible (`em`, `ph`, `external_id`,
   `fbc`, `fbp`) — identifier coverage is 30% of the EMQ component.

Landing-CMS / global `META_PIXEL_ID` sends **do not** feed tenant EMQ
(they omit `tenant_id` on delivery rows).

## When scores appear

- Celery beat `trust-signal-health-rollup` writes **yesterday's** row to
  `fact_signal_health_daily` at 02:00 UTC.
- Overview / EMQ v2 falls back to the **latest measured day** when today
  has no row yet, so you are not stuck on "Not measured" overnight.
- Live dashboard signal-health uses a trailing window and can show earlier.

## Free plan note

Billing / Paddle is optional. Empty `PADDLE_API_KEY` keeps the product on
the free plan; EMQ + CAPI do not require Paddle.
