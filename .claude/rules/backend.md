---
paths:
  - "backend/**"
---

# Backend rules

- Keep routes thin and reusable behavior in services or domain modules.
- Preserve authenticated tenant scope, authorization, audit records, idempotency, rate limits, async I/O, and transaction ownership.
- Append Alembic migrations and review generated SQL; never rewrite applied history.
- A sync that writes no metrics must not report success or fresh signal health.
