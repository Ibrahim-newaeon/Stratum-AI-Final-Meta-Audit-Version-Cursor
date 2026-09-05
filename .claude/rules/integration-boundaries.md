---
paths:
  - "backend/app/services/**"
  - "backend/app/api/v1/endpoints/**"
  - "backend/app/stratum/**"
  - "backend/app/tasks/**"
  - "frontend/src/**"
  - "frontend/public/**"
  - "docs/integrations/**"
---

# Integration boundaries

- Read the root guide and `docs/integrations/README.md` before changing integration behavior.
- Activation is Meta-only. GA4 is a read-only measurement baseline and GTM is tag infrastructure; neither is an activation channel. Do not introduce Google Ads or other non-Meta activation paths.
- Paddle is the sole billing provider: retain the thin backend HTTP client and authenticated-SPA-only Paddle.js loading. Follow `docs/integrations/billing-paddle.md`; never add checkout scripts to public HTML.
- Keep signed-webhook verification, encrypted credentials, tenant authorization, idempotency, and retry-safe transactions intact. Do not log tokens or return credentials in configuration responses.
- Keep Meta insights ingestion read-only. The existing simulated action executor is not a live production integration; do not schedule it or report its output as a real action.
- Failed or empty ingestion must not falsely refresh sync timestamps or improve signal health. Preserve trust-gate and audit checks around any genuinely implemented write path.
