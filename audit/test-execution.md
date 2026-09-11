# Test execution log — Stratum AI pre-launch audit

## Environments

| Name | Proven safe for mutations? | Backend | Notes |
|---|---|---|---|
| Local git worktree | Yes (tests only) | `backend/.venv` CPython 3.12.13 | No `.env`; docker compose empty; pytest script shebang broken (old Desktop path). Invoked `.venv/bin/python -m pytest`. |
| Local Docker Compose | No — not running | n/a | `docker compose ps` empty |
| Isolated staging | Not provided | unknown | |
| https://meta.stratumai.app | **No** — treated as production-equivalent | Railway (`x-railway-edge: ams1`) + Cloudflare | Read-only UI + public GETs only. SPA `/health` is nginx. Facebook Login enabled. Demo login buttons present. Did not authenticate. |

## Versions

| Item | Value |
|---|---|
| Audited git SHA | `ccc97a6cdde6d293df30bba265e672ce747aa41a` |
| Branch | `main` (tracks `origin/main`) |
| Subject | Merge pull request #47 from Ibrahim-newaeon/feat/schedule-apply-actions-queue |
| Commit time | 2026-09-09 05:11:00 +0300 |
| GitHub Actions on SHA | CI **success** (7m20s), Docker **success**, Deploy Docs **success** |
| Production index | `https://meta.stratumai.app/assets/index-DqB9HH_Q.js` |
| Local vite build of SHA (Node 24.18.1) | `index-Darafsqv.js` |
| Matching vendor chunks | `vendor-react-Dz7uKsMg.js`, `vendor-utils-L2YjCnAZ.js`, `vendor-charts-Cj11RBfc.js`, `vendor-ui-DVHdeuXP.js` |
| Production HTML Last-Modified | Wed, 09 Sep 2026 17:27:05 GMT |
| `deployed_matches_tested` | **false** (index hash gap; vendors match) |

## Commands run

Working directory noted. All backend pytest via `.venv/bin/python -m pytest`.

### Backend unit (blocking CI equivalent)

```text
cd backend
.venv/bin/python -m pytest tests/unit -q --tb=line -m "unit or not integration"
```

- **Result:** 2236 passed, 16 warnings, 26.34s
- **Includes:** trust gate (plus new 69/70/71 tests), Meta executor, rules gate, signal health, auth bypass, Paddle webhooks, Meta callbacks, onboarding, worker queues, mock-ad-data guard, WhatsApp dispatch, EMQ honesty
- **Environment:** `APP_ENV=development` from `tests/conftest.py`; no live Meta/Paddle

### Backend high-risk subset (earlier in the same SHA)

```text
.venv/bin/python -m pytest tests/unit/test_trust_gate_signal_health.py \
  tests/unit/test_rules_trust_gate.py tests/unit/test_meta_write_executor.py \
  tests/unit/test_signal_health_service.py tests/unit/test_auth_bypass_regression.py \
  tests/unit/test_paddle_webhook.py tests/unit/test_mock_ad_data_guard.py \
  tests/unit/test_worker_queue_coverage.py tests/unit/test_onboarding_steps.py \
  tests/unit/test_onboarding_agent_thresholds.py -q --tb=line -m "unit or not integration"
```

- **Result:** 970 passed, 11 warnings, 12.54s

### Backend integration

```text
.venv/bin/python -m pytest tests/integration -q --tb=line -m integration
```

- **Result:** 57 errors, 2 warnings, 0.55s
- **Cause:** PostgreSQL not running (`sqlalchemy` connection errors)
- **Status:** BLOCKED, not a product FAIL

### Frontend

```text
cd frontend
npm run test -- --run          # 4 files, 63 passed, 842ms
npx tsc --noEmit               # exit 0
npm run lint                   # 0 errors, 510 warnings
npm run build                  # vite build 6.86s success
```

### Production read-only

- Browser: `/`, `/login`, `/pricing`, `/faq`, `/status`, `/test-page`, `/changelog`
- `fetch('/health')` → `200 healthy\n`
- `fetch('/api/v1/auth/facebook/config')` → `enabled: true`
- `fetch('/pricing')` response headers: CSP, XFO, nosniff, Permissions-Policy, **no HSTS**
- Did **not** submit login, demo buttons, signup, checkout, Meta writes, WhatsApp, or Paddle purchases

### Not run (infrastructure)

| Check | Reason |
|---|---|
| Playwright `npm run test:e2e -- --project=chromium` | No local Vite/API |
| ruff/black/isort/mypy | Non-blocking in CI; not required to interpret unit results |
| Production load test | Forbidden |
| Live Meta/Paddle/WhatsApp mutations | Forbidden |

## Coverage matrix counts (denominator = 114)

From `audit/feature-coverage.csv` after discovery (not the seed 46):

| Status | Count |
|---|---|
| PASS | 33 |
| FAIL | 30 |
| BLOCKED | 49 |
| NOT RUN | 0 |
| NOT APPLICABLE | 2 |
| **Total** | **114** |

A 33/114 pass rate is **not** coverage. Most authenticated product modules are BLOCKED. FAIL rows are confirmed product/process defects.

## Fixtures created

- Added `TestExactScoreBoundaries` in `backend/tests/unit/test_trust_gate_signal_health.py` (69/70/71 and 39/40/`None`).
- Local `frontend/dist/` from vite build (gitignored). No production data touched.
- Screenshots under `audit/evidence/` (no PII).

## Cleanup

- No production fixtures.
- No demo login session.
- Browser unlocked after public-page inspection.

## Limitations

- Unit tests use fakes/mocks for Meta HTTP; they prove fail-closed logic, not a live ads_management write.
- Node 24 local frontend build vs CI Node 20 may explain index hash drift.
- Integration tenant-isolation tests remain unverified.
