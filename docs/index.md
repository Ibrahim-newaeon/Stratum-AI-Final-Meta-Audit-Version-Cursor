# Stratum AI

Stratum AI is an AI-powered revenue operating system for ad teams. It optimizes
Facebook, Instagram and WhatsApp campaigns with **Trust-Gated Autopilot** — every
AI decision is auditable, explainable and reversible, with one-click human
override. Built-in predictive models cover ROAS, LTV, churn, conversion and
creative fatigue.

## How the trust gate works

| Signal health | Trust gate | Automation |
|---|---|---|
| Healthy (≥ 70) | Pass | Execute automatically |
| Degraded (40–69) | Hold | Alert only |
| Unhealthy (< 40) | Block | Manual action required |

Automation never executes while signal health is below the healthy threshold.

## Where to look

- [Trust Engine](architecture/trust-engine.md) — signal health, gate evaluation and audit trail
- [Backend overview](02-backend/backend-overview.md) — services, workers and data model
- [Integrations](integrations/README.md) — Meta (Facebook, Instagram, WhatsApp) as the only action channel; GA4 and GTM as read-only measurement; Paddle for billing
- [Billing (Paddle)](integrations/billing-paddle.md) — API keys, client token, webhook and price setup
- [Runbooks](05-operations/runbooks.md) — operating the platform
- [Integration audit](STRATUM_INTEGRATION_AUDIT.md) — status of every connector and signal source

## Product boundaries

- **Acts only on Meta channels.** Google Ads, TikTok and Snapchat are not supported and are not read.
- **GA4 / GTM are measurement only.** They provide the independent revenue and conversion baseline used by attribution variance and the trust gate; Stratum never writes to them.
- **Payments are Paddle.** Subscriptions, invoices and webhooks come from Paddle Billing.
