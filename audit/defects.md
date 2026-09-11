# Defects — Stratum AI pre-launch audit

Commit: `ccc97a6cdde6d293df30bba265e672ce747aa41a`.  
Environment mixed: local unit tests + production-equivalent read-only `https://meta.stratumai.app`.  
Passwords and tokens are not recorded here.

---

## Confirmed defects

### DEF-001 — Demo credentials compiled into the live login page

- **Severity:** Critical (High if this host is proven staging with no customer data)
- **Affected feature:** Authentication / Quick Demo Access
- **Prerequisites:** Open `/login` on the launch host
- **Reproduction:** Load `https://meta.stratumai.app/login`. Buttons Super / admin / user are visible. Source `frontend/src/views/Login.tsx` `handleDemoLogin` posts hardcoded demo emails and passwords via the real `login()` API. The same UI is in the production JS bundle.
- **Expected:** Production builds omit demo login, or gate it on a non-production flag.
- **Actual:** One-click demo login is shipped on the custom domain.
- **Impact:** If those users exist (SEED_DEMO), anyone can become superadmin. If they do not, the passwords are still in the public bundle. **Did not click the buttons on the live host.**
- **Evidence:** `audit/evidence/PROD-login.png`; `frontend/src/views/Login.tsx`; `backend/scripts_seed_demo.py`; SERVER_DEPLOYMENT_GUIDE staging `SEED_DEMO=true`
- **Regression:** Build-time test that production `APP_ENV` frontend omits `handleDemoLogin`; e2e that `/login` has no Quick Demo Access when `VITE_*` production.

### DEF-002 — Trust-gate worker audit is stdout only

- **Severity:** Blocker for the advertised “every decision is auditable” bar
- **Affected feature:** Trust Gate / Autopilot execution log
- **Prerequisites:** Code inspection of `log_gate_decision_audit` / `log_action_audit`
- **Reproduction:** Read `backend/app/tasks/apply_actions_queue.py`. Held/blocked/executed Meta actions call `logger.info("AUDIT: …")`. Comment: “would write to a dedicated audit_log table.” `TrustGateAuditLog` is written from dry-run HTTP, not the worker.
- **Expected:** Durable rows for allow **and** deny, queryable in Trust Layer UI.
- **Actual:** Trust Layer audit UI will be empty for real queue runs.
- **Impact:** Cannot prove who/what/why after an incident.
- **Evidence:** `apply_actions_queue.py` ~1169–1218
- **Regression:** Integration test that HOLD/BLOCK/APPLIED inserts `TrustGateAuditLog`.

### DEF-003 — FAQ threshold copy disagrees with the gate

- **Severity:** Medium
- **Affected feature:** Trust Engine public documentation
- **Reproduction:** Open `/faq`. Text: scores **above** 70 are Healthy; **40-70** are Degraded.
- **Expected:** `>= 70` PASS; 40–**69** HOLD (`docs/architecture/trust-engine.md`).
- **Actual:** 70 is painted both ways; 70 is in the degraded range in the FAQ.
- **Impact:** Operators will misread eligibility vs the worker.
- **Evidence:** `audit/evidence/PROD-faq.png`; `frontend/src/views/pages/company/FAQ.tsx:90`
- **Regression:** Copy test against `signal_health_healthy_threshold`.

### DEF-004 — Insights helper can report autopilot unblocked with no data

- **Severity:** High
- **Status:** Fixed — `check_signal_health_for_autopilot` fails closed on
  `no_data` / `insufficient_data` and honors `automation_blocked`
  (`test_insights_autopilot_signal_health.py`).
- **Affected feature:** Insights autopilot status
- **Reproduction (static):** previously `blocked = status in ["degraded","critical"]` only.
- **Expected:** Unknown health fail-closed.
- **Actual (before fix):** `no_data` → `blocked=False` on that path.

### DEF-005 — UI color bands ignore tenant threshold overrides

- **Severity:** Medium
- **Affected feature:** Signal Health cards / TrustGateStatus ticks
- **Expected:** Same pair as `thresholds_for_tenant`.
- **Actual:** Literals 70/40 in `TrustGateStatus.tsx` and `SignalHealthCard.tsx` while the API already returns thresholds.
- **Impact:** A tenant that set 90/50 still sees 70/40 cosmetics.
- **Evidence:** those components; `test_signal_health_service.py` tenant override tests (backend correct)
- **Regression:** Render with `healthy_threshold=90` and assert colors.

### DEF-006 — Custom Autopilot is an in-memory mock

- **Severity:** Blocker (advertised launch feature with no implementation)
- **Affected feature:** Custom Autopilot Rules
- **Reproduction:** `frontend/src/views/CustomAutopilotRules.tsx` seeds `mockRules`, save is `setTimeout` 1s, no API module.
- **Expected:** Persist, evaluate, trust-gate, audit.
- **Actual:** Client state only. Pricing Professional lists Trust-Gated Autopilot.
- **Impact:** Users believe budget +20% rules are live.
- **Evidence:** `CustomAutopilotRules.tsx`; `audit/evidence/PROD-pricing.png`
- **Regression:** Page must call `/api/v1/...` or be removed from nav and pricing.

### DEF-007 — “3 consecutive days” vs `days_running >= 3`

- **Severity:** High (product logic / copy) — currently only in the mock
- **Affected feature:** Scale High ROAS preset
- **Reproduction:** Preset description: increase budget 20% when ROAS exceeds 4x for **3 consecutive days**. Conditions: `roas > 4.0` AND `days_running >= 3`.
- **Expected:** Five representations agree (description, editor, stored config, displayed expression, evaluator). Consecutive-day streak ≠ campaign age.
- **Actual:** Age vs streak mismatch; `days_running` is not a `Campaign` column; real rules engine would fail closed (no match).
- **Impact:** Even after wiring an API, the preset is the wrong predicate. ROAS **exactly** 4.0 does not match `gt`.
- **Evidence:** `CustomAutopilotRules.tsx` mockRules[0]; `backend/app/base_models.py` Campaign
- **Regression:** Fixture: 3-day-old campaign with only last day ROAS>4 must **not** scale if streak is the contract.

### DEF-008 — Fatigue threshold 70% with no stored contract

- **Severity:** Low (mock-only today)
- **Affected feature:** Creative Fatigue Alert preset
- **Actual:** `fatigue_score > 70` as percentage. Fatigue jobs may use 0–1.
- **Expected:** One unit system.
- **Evidence:** mockRules[2]
- **Regression:** Contract test 70 vs 0.70 when the feature is real.

### DEF-009 — Automation Rules fall back to mock “active” rules

- **Severity:** High
- **Affected feature:** `/dashboard/rules`
- **Reproduction:** `useMemo` returns `mockRules` when `items` is empty (including a new tenant).
- **Expected:** Empty state.
- **Actual:** Synthetic WhatsApp/ROAS rules look live (hypothesis 5.D).
- **Evidence:** `frontend/src/views/Rules.tsx` ~282–300
- **Regression:** Component test: empty API → empty UI.

### DEF-010 — Scheduled rules worker ignores cooldown

- **Severity:** High
- **Affected feature:** Automation Rules beat task
- **Expected:** `cooldown_hours` honored across process restarts.
- **Actual:** `RulesEngine` implements cooldown; `workers/tasks/rules.py` `evaluate_rules` does not call it.
- **Impact:** Pause/budget/alerts can repeat every 15 minutes on PASS.
- **Evidence:** `backend/app/workers/tasks/rules.py` vs `backend/app/services/rules_engine.py`
- **Regression:** Two evaluations inside cooldown → second skipped.

### DEF-011 — `pause_campaign` overwrites previous_status

- **Severity:** Medium
- **Affected feature:** Rules execution audit
- **Reproduction:** `_execute_action` sets `campaign.status = "paused"` then `previous_status = campaign.status`.
- **Expected:** Previous value captured first.
- **Actual:** Always `"paused"`.
- **Evidence:** `backend/app/workers/tasks/rules.py` ~302–304
- **Regression:** Campaign ACTIVE → pause → `previous_status == ACTIVE`.

### DEF-012 — Rules BLOCK writes no RuleExecution row

- **Severity:** Medium
- **Affected feature:** Rules audit
- **Expected:** Denied runs stored with gate reason.
- **Actual:** Tests assert **no** `RuleExecution` on BLOCK; only Celery return dict.
- **Evidence:** `test_rules_trust_gate.py`
- **Regression:** BLOCK inserts a row with `success=False`.

### DEF-013 — No product emergency stop for all automation

- **Severity:** High (risk; absence of a control)
- **Affected feature:** Kill switch
- **Actual:** `AUTOPILOT_EXECUTION_ENABLED` stops Meta writes. Enforcement “kill switch” is budget/ROAS policy, not the rules beat. No UI “stop all automations.” Rules still mutate local `Campaign` and can WhatsApp on HOLD.
- **Expected:** One operator control that stops queue + rules + alerts.
- **Evidence:** `autopilot_enforcement.py`; `action_executor.py` comments; `celery_app.py` beat
- **Regression:** Flag/API that makes `evaluate_all_rules` and `apply_actions_queue` no-ops.

### DEF-014 / DEF-015 / DEF-016 / DEF-017 / DEF-021 / DEF-022 — Mock presented as product data

| ID | Surface | Evidence |
|---|---|---|
| DEF-014 | Stratum Analytics mock insights when API empty | `views/Stratum.tsx` |
| DEF-015 | Custom Reports `MOCK_REPORTS` | `CustomReportBuilder.tsx` |
| DEF-016 | CDP Consent `Math.random` / `tenantId=1` | `ConsentManager.tsx` |
| DEF-017 | Predictive Churn `MOCK_*` | `CDPPredictiveChurn.tsx` |
| DEF-021 | EMQ dashboard mixed mock constants | `DataQualityDashboard.tsx` |
| DEF-022 | Notification bell demo list + `unreadCount={3}` | `NotificationCenter.tsx`, `DashboardLayout.tsx` |

- **Severity:** High (Critical if operators act on mock spend/churn/consent)
- **Expected:** Empty or labeled demo.
- **Actual:** Looks live.
- **Regression:** Empty-API tests per view; forbid unlabeled mocks in production builds.

### DEF-018 — SPA `/health` hides API environment

- **Severity:** Medium (operations / auditability)
- **Affected feature:** Release verification
- **Expected:** FastAPI `/health` JSON with `environment` and `version`.
- **Actual:** nginx `return 200 "healthy\n"`. Cannot classify staging vs production from the public host.
- **Evidence:** `frontend/nginx.conf` location `/health`; browser fetch
- **Regression:** Proxy `/health` to API or expose `/api/health`.

### DEF-019 — Knowledge Graph Insights is a placeholder

- **Severity:** Medium (advertised module)
- **Expected:** Graph insights from API.
- **Actual:** File comment: “placeholder… under construction”; links only.
- **Evidence:** `KnowledgeGraphInsights.tsx`
- **Regression:** Route shows data or is hidden from nav.

### DEF-020 — Journey Explorer 404

- **Severity:** High
- **Expected:** Fourth KG section.
- **Actual:** Sidebar `href=/dashboard/knowledge-graph/journeys` with **no** `<Route>` and no view. SPA fallback is not a journey UI (unauthenticated would hit login). Backend `GET /knowledge-graph/analytics/journey/{profile_id}` exists without a page.
- **Evidence:** `DashboardLayout.tsx` `kgNavigation`; `App.tsx` KG routes
- **Regression:** Route + view, or remove nav item.

### DEF-023 — Public pages barely exposed to assistive tech

- **Severity:** High (a11y)
- **Expected:** Nav, CTAs, form fields in the accessibility tree (WCAG 2.2 AA).
- **Actual:** Landing/pricing/FAQ snapshots: skip-link + notifications region; login snapshot missed the form that the screenshot shows. Likely canvas/div-heavy UI.
- **Evidence:** cursor-ide-browser snapshots vs `PROD-*.png`
- **Regression:** Playwright accessibility snapshot contains Sign in / pricing CTAs.

### DEF-024 — Debug `/test-page` is live

- **Severity:** Low
- **Expected:** Absent in production.
- **Actual:** “Routing is working. This page exists only for debugging navigation.”
- **Evidence:** `audit/evidence/PROD-test-page.png`
- **Regression:** Production build excludes the route.

### DEF-025 — Deployed index hash ≠ local build of audited SHA

- **Severity:** High (release integrity)
- **Expected:** Same commit artifact.
- **Actual:** Prod `index-DqB9HH_Q.js` vs local `index-Darafsqv.js`. Vendor chunks match. Node 20 vs 24 or `VITE_*` may explain it — **not proven same binary**.
- **Evidence:** `audit/test-execution.md`
- **Regression:** Stamp `git SHA` into `index.html` at build.

### DEF-026 — Status page is hardcoded marketing

- **Severity:** Medium
- **Expected:** Real probes, or labeled sample.
- **Actual:** Copy says “Real-time status”; `Status.tsx` hardcodes 99.98% and January 2026 bars.
- **Evidence:** `audit/evidence/PROD-status.png`; `Status.tsx`
- **Regression:** Fetch health or watermark “illustrative.”

### DEF-027 — HSTS missing

- **Severity:** Medium
- **Expected:** `Strict-Transport-Security` on HTTPS production.
- **Actual:** nginx HSTS commented; Cloudflare response on `/pricing` had no STS.
- **Evidence:** header dump in test-execution
- **Regression:** Header present (`max-age` ≥ 15552000).

---

## Suspected issues (not fully reproduced)

| ID | Signal | Why not confirmed | Next proof |
|---|---|---|---|
| SUS-001 | `meta.stratumai.app` may be staging (`SEED_DEMO`) despite custom domain | SPA health has no `APP_ENV`; demo buttons + Facebook Login both present | Operator dumps `APP_ENV`, `SEED_DEMO`, `PADDLE_ENVIRONMENT` |
| SUS-002 | Demo users may already exist on this host | Guide says staging seeds `demo@stratum.ai`; not probed | Isolated staging login only — not production |
| SUS-003 | Knowledge Graph “dark overlay” screenshot | Insights markup has no overlay; `NeuralNetworkBg` sits behind `z-10`; landing chat overlays content | Authenticated KG at desktop/mobile; see KG-005 |
| SUS-004 | Dual entitlement systems (`features/flags.py` vs `core/tiers.py`) can disagree | Naming overlap, no live plan matrix | Fixture per plan × flag |
| SUS-005 | CSP `'unsafe-eval'` + `'unsafe-inline'` | Header present; exploitability not proven in isolation | Local CSP violation tests only |

---

## Product ambiguities

| ID | Conflict | What was tested | Owner decision needed |
|---|---|---|---|
| AMB-001 | `days_running` = campaign age vs ROAS streak | Mock uses age; description uses streak; backend has neither field | Specify the metric |
| AMB-002 | Is Trust-Gated Autopilot **in launch scope**? | Meta writes default **off**; UI/pricing advertise it; Custom Autopilot is mock; rules mutate **local** campaigns | Scope: flags-off vs advertised-on |
| AMB-003 | FAQ “above 70” vs code `>= 70` | Code/tests use `>=`; FAQ uses above / 40-70 | Pick one sentence and one comparator |
| AMB-004 | Environment of `meta.stratumai.app` | Railway + Cloudflare + Facebook Login on + demo buttons | Declare production vs staging |

---

## Trust Gate launch bar (mandatory if spend automation in scope)

| Bar item | Result |
|---|---|
| Worker evaluates gate immediately before Meta side effects | **PASS (unit)** — not live |
| Unknown/stale/null/invalid/partial fail closed | **PASS (unit)** |
| 69 / 70 / 71 | **PASS (unit)** — added this audit |
| Duplicate execution after timeout/retry | **PASS (unit)** for Meta executor; **FAIL** rules cooldown (DEF-010) |
| Failed/blocked not counted success | **PASS (unit)** Meta path; rules BLOCK has no row (DEF-012) |
| Durable audit allow+deny | **FAIL** DEF-002 |

Because Custom Autopilot and Automation Rules are advertised, spend-changing automation is **treated as in launch scope**. The bar is **not** fully verified. Recommendation cannot be GO.

If the owner documents that Meta writes stay disabled **and** Custom Autopilot is removed from nav/pricing **and** rules cannot change budget/status in the launch build, the Meta-executor subset could be re-scored — that is a product decision (AMB-002), not a test pass.
