# Resume packet — Stratum AI launch audit

The coverage matrix is complete (no NOT RUN). Resume is for **blocked retesting after humans supply access**, not for an unfinished inventory.

## Last commit audited

`ccc97a6cdde6d293df30bba265e672ce747aa41a` (`main`)

## Environments

| Environment | Verdict |
|---|---|
| Local worktree | Safe for unit tests; `.env` missing; compose down |
| Local Docker | Unsafe/unavailable — no containers |
| https://meta.stratumai.app | **Unsafe for mutations** — production-equivalent until proven otherwise |
| Staging with proven sandbox Paddle/Meta | Not provided |

## Completed test IDs

All 114 IDs in `audit/feature-coverage.csv` have PASS / FAIL / BLOCKED / NOT APPLICABLE.

PASS (33) and FAIL (30) do not need re-execution unless code changes.  
Re-run unit tests after any Autopilot/Rules/Trust/Auth fix:

```bash
cd backend && .venv/bin/python -m pytest tests/unit -q --tb=short -m "unit or not integration"
cd frontend && npm run test -- --run && npx tsc --noEmit && npm run build
```

## Remaining IDs (priority)

1. AUTH-009, ENV-005 — tenant isolation integration (Postgres)
2. ONB-002, OVR-001, CAMP-001/002 — core onboarding and campaigns
3. TRUST-006 — widget vs worker on a real tenant with insufficient_data
4. BILL-002 — Paddle sandbox
5. WA-001, CAPI-001, CDP-001–009, KG-002/003/005 — authenticated modules
6. FE-003 — Playwright once API+Vite run
7. ENV-002 — rebuild with Node 20 and compare `index-*.js` to the host
8. ENV-003 — classify live host from API `APP_ENV`

## Who must supply what

See BLOCKERS REQUIRING HUMAN INPUT in the chat response (tenants, APP_ENV, sandboxes).

## Fixtures / cleanup

- Added `TestExactScoreBoundaries` (keep).
- Evidence PNGs in `audit/evidence/` (keep; no secrets).
- No live logins, no paid training, no ad mutations.

## Files to re-reconcile after retest

- `audit/feature-coverage.csv`
- `audit/defects.md`
- `audit/test-execution.md`
- `audit/launch-readiness-report.md`
- `audit/audit-summary.json`
- `audit/launch-checklist.html`
- `launch-checklist.html` (root copy if present)

Do not imply GO until DEF-001, DEF-002, DEF-006, AUTH-009, and AMB-002 are closed with evidence.
