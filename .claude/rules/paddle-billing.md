---
paths:
  - "backend/**/*billing*"
  - "backend/**/*paddle*"
  - "frontend/**/*billing*"
  - "frontend/**/*checkout*"
  - "docs/**/*paddle*"
---

# Paddle billing rules

- Paddle is the only payment provider. Do not add or revive Stripe behavior.
- Keep the backend integration through the existing thin httpx client and established billing boundary.
- Load Paddle.js only inside the authenticated application.
- Preserve signed-webhook verification, replay protection, idempotency, rollback, retries, tenant synchronization, and provider identifiers.
- Production billing changes require explicit approval and a recovery plan.
