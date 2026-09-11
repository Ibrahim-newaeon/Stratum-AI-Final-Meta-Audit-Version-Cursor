# Launch readiness report — Stratum AI

**Recommendation: NO-GO**

Audited SHA `ccc97a6cdde6d293df30bba265e672ce747aa41a` (`main`).  
Live host `https://meta.stratumai.app` (Railway + Cloudflare). Index bundle hash does **not** match a local production build of this SHA (`deployed_matches_tested: false`).  
Audit time: 2026-09-10T13:45:00Z (approximate).

`needs_human_review`: **true**

---

## Decision

NO-GO. Release blockers and unverified critical controls remain:

1. Advertised Trust-Gated Autopilot / Custom Autopilot is a **frontend mock** (DEF-006, DEF-007) while Professional pricing sells it.
2. Worker **does not persist** TrustGateAuditLog for allow/deny (DEF-002). The Trust Gate launch bar therefore fails.
3. Automation Rules **empty-state mock**, **no beat cooldown**, local campaign pause/budget without Meta flags (DEF-009, DEF-010, DEF-013).
4. Live login ships **demo Super/admin/user** controls (DEF-001). Host environment (staging vs production) is **unproven** (DEF-018, AMB-004).
5. Authenticated core journeys (onboarding, campaigns, CDP, WhatsApp, billing checkout, tenant isolation HTTP) are **BLOCKED** — no approved test tenants. Cross-tenant HTTP isolation is a potential release blocker and remains unverified beyond unit tests (ENV-005, AUTH-009).
6. Journey Explorer **nav 404** (DEF-020). Multiple modules present synthetic data as live (DEF-014–017, 021, 022, 026).

Meta write execution is **off by default** and well unit-tested (fail-closed gate, claim-before-write, ambiguous timeout). That is **not** the same as a GO for the advertised product.

---

## Tested environment and version gap

| | |
|---|---|
| Tested commit | `ccc97a6cdde6d293df30bba265e672ce747aa41a` |
| GHA on commit | CI success, Docker success, Docs success |
| Deployed frontend | `index-DqB9HH_Q.js` (Last-Modified 2026-09-09T17:27:05Z) |
| Local build of commit | `index-Darafsqv.js` (Node 24; CI uses Node 20) |
| Vendor chunks | Match (`vendor-react-Dz7uKsMg.js` and siblings) |
| API `APP_ENV` | **Unknown** — nginx `/health` returns `healthy\n` |

Treat the custom domain as production until an operator proves otherwise.

---

## Coverage (every discovered launch feature has a disposition)

Denominator **114** (`audit/feature-coverage.csv`). Seed 46 was expanded with CDP submenu, KG, workers, flags, chrome, security, billing, tenant/superadmin routes.

| Status | Count | Meaning here |
|---|---|---|
| PASS | 33 | Executed (mostly unit tests + public read-only) |
| FAIL | 30 | Confirmed defects |
| BLOCKED | 49 | Missing tenants, DB, or sandbox — **unverified** |
| NOT RUN | 0 | |
| NOT APPLICABLE | 2 | Google Ads activation; Stripe |

Passing unit percentage (2236 backend + 63 frontend) is **not** this coverage. Frontend has four test files. Playwright and integration HTTP isolation did not run.

### Module rollup

Verified in unit tests: Trust Gate evaluator, Meta executor idempotency, Paddle/Meta signatures, onboarding field maps, worker queue wiring, Facebook login trust, mock-ad-data production guard.

FAIL in product surfaces: Custom Autopilot, Rules mock fallback, Custom Reports, CDP Consent, Predictive Churn, KG Insights/Journeys, notifications, status page, FAQ copy, demo login, `/test-page`, HSTS, health probe.

BLOCKED: essentially all authenticated Overview/Campaigns/WhatsApp/CAPI/CDP-real-paths/billing checkout/org switch/ML/onboarding UI.

---

## Critical and high findings (priority)

1. **DEF-001** Demo login on live host — Critical until environment classified.
2. **DEF-006 / DEF-007** Custom Autopilot mock + consecutive-days lie — Blocker for advertised Autopilot.
3. **DEF-002** No durable trust-gate audit — Blocker for the Trust Gate bar.
4. **DEF-009 / DEF-010 / DEF-013** Rules mock + no cooldown + no global stop — High/Blocker for spend-adjacent automation.
5. **AUTH-009** Tenant isolation integration **BLOCKED**.
6. **DEF-020** Journey Explorer missing.
7. **DEF-016 / DEF-017** Consent/churn mocks.
8. **DEF-025** Deployed index ≠ tested artifact.
9. **ONB-002** Onboarding UI **BLOCKED** (core onboarding is a potential release blocker).
10. **DEF-023** Public a11y tree.

Full list: `audit/defects.md`.

---

## BLOCKED / NOT RUN

NOT RUN is empty.

BLOCKED rows need:

- Two isolated tenants, roles (superadmin/admin/user/AM), starter/professional/enterprise
- Confirmation `APP_ENV` / `SEED_DEMO` / Paddle mode / whether demo users exist
- Local or non-live Postgres+Redis for integration tests
- Meta/WhatsApp/CAPI **sandbox** IDs (not live ad accounts)
- Paddle **sandbox** checkout user
- Approved test recipients if WhatsApp is in launch scope

See BLOCKERS REQUIRING HUMAN INPUT in the final response.

---

## Test resources and cleanup

No production fixtures. Screenshots in `audit/evidence/` without credentials. Added unit tests for 69/70/71 only. Browser unlocked.

---

## Operational evidence

- Celery beat includes `autopilot-apply-actions-queue` every 5 minutes on `sync` (scheduled ≠ enabled).
- `AUTOPILOT_EXECUTION_ENABLED` default false; dry-run default true.
- Rules beat every 15 minutes **is** live relative to DB state (no execution flag).
- CI green on the SHA.
- Changelog page shows **v3.2.0 / 15 Jan 2026** — not this SHA.
- Status page is not a monitor (DEF-026).
- Backups/restore: **not evidenced** (config ≠ restore test).

---

## Smallest actions required for launch readiness

1. Classify `meta.stratumai.app` and **remove demo login** from any customer-facing host; rotate those passwords if the users exist.
2. Remove or label Custom Autopilot / Custom Reports / mock fallbacks; or implement them for real. Align pricing copy.
3. Persist TrustGateAuditLog from the worker for PASS/HOLD/BLOCK.
4. Honor cooldown on the rules beat; stop local budget/status mutation or route through the Meta executor + flags; add a real emergency stop.
5. Ship Journey Explorer or drop the nav item; replace KG Insights placeholder.
6. Proxy API health; stamp git SHA in the SPA; enable HSTS; drop `/test-page`.
7. Provide two test tenants and re-run BLOCKED IDs (onboarding, campaigns, isolation, billing sandbox).
8. Rebuild frontend with CI Node 20 and confirm `index-*.js` matches the host.

---

## Confidence

**Medium-high on “do not launch the advertised Autopilot product.”**  
**Low on “the live host is safe for customers.”** (environment unclassified; isolation HTTP unverified; demo login present)

Reasons for confidence in NO-GO: mocks are in source, FAQ/status/login were observed live, unit tests independently confirm both the strong Meta executor *and* the audit/cooldown holes.

Reasons confidence is not “high” on every module: 49 BLOCKED authenticated checks; deployed index hash mismatch; no live Meta sandbox.

---

## Human review decisions

1. Is `https://meta.stratumai.app` production (`SEED_DEMO=false`) or staging?
2. Is advertised Trust-Gated Autopilot in **this** launch, given flags default off and Custom Autopilot is mock?
3. May automation rules change local `Campaign` budget/status in launch, or must that be disabled?
4. Accept stdout-only audit until TrustGateAuditLog is wired, or require it pre-launch?
5. Supply isolated test tenants and sandboxes, or accept remaining BLOCKED as launch risk?
