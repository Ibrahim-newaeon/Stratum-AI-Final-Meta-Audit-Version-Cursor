# Trust Engine

The Trust Engine is the safety layer between signals and automation. Automation executes **only** when
signal health passes the safety thresholds; everything else is alert-only or manual.

```
Signal Health Check → Trust Gate → Automation Decision
       ↓                  ↓              ↓
   [HEALTHY]         [PASS]         [EXECUTE]
   [DEGRADED]        [HOLD]         [ALERT ONLY]
   [UNHEALTHY]       [BLOCK]        [MANUAL REQUIRED]
```

## Signal health thresholds

| State | Signal health | Trust gate | Autopilot |
|-------|---------------|------------|-----------|
| HEALTHY | >= 70 | PASS | Execute |
| DEGRADED | 40 - 69 | HOLD | Alert only |
| UNHEALTHY | < 40 | BLOCK | Manual required |

`HEALTHY_THRESHOLD = 70`, `DEGRADED_THRESHOLD = 40`, configured as
`signal_health_healthy_threshold` / `signal_health_degraded_threshold` in
`backend/app/core/config.py`. Never auto-execute when `signal_health < 70`.
Thresholds come from config, never hardcoded in callers. Every executed automation is audit-logged.

## Signal health components

Signal health is a weighted composite (0-100) of, per Meta channel (`facebook` | `instagram` | `whatsapp`):

| Component | Weight | Source |
|-----------|--------|--------|
| EMQ (Event Match Quality) | 40% | Meta CAPI/Pixel delivery, CDP identifiers |
| Freshness | 25% | Connector sync age, GA4 baseline age |
| Variance | 20% | Platform vs GA4 attribution variance |
| Anomalies | 15% | Anomaly detection on spend, conversions, revenue |

Daily snapshots are stored in `fact_signal_health_daily` (rollup task `tasks.signal_health_rollup`,
beat key `trust-signal-health-rollup`, 02:00 UTC).

## EMQ drivers

| Driver | Weight | Good | Warning | Critical | Fed by |
|--------|--------|------|---------|----------|--------|
| Event match rate | 30% | >= 90% | 70-89% | < 70% | Meta CAPI match diagnostics |
| Pixel coverage | 25% | >= 85% | 65-84% | < 65% | Pixel + CAPI event coverage |
| Conversion latency | 20% | < 1h | 1-4h | > 4h | Delivery timestamps |
| Attribution accuracy | 15% | >= 85% | 70-84% | < 70% | Platform vs GA4 read-only baseline |
| Data freshness | 10% | < 1h | 1-6h | > 6h | Connector sync age |

The **attribution accuracy** driver is where the Google Analytics 4 read-only baseline enters the Trust
Engine: platform-reported conversions and revenue are compared against `fact_ga4_daily` and the result
lowers or raises EMQ. GA4 is an independent verification source only; it is never an ad channel.

## Attribution variance bands

Variance is computed per Meta channel and day by `tasks.attribution_variance_rollup`
(beat key `trust-attribution-variance-rollup`, 03:00 UTC, after the 02:30 UTC GA4 pull) into
`fact_attribution_variance_daily`:

```
variance_pct = (platform_reported - ga4_baseline) / ga4_baseline * 100
```

| Band | Absolute variance | Meaning | Effect |
|------|-------------------|---------|--------|
| Tight | <= 5% | Platform and GA4 agree | No penalty |
| Healthy | <= 15% | Normal attribution-window differences | Minor penalty |
| Moderate | 15 - 30% | Investigate pixel/CAPI setup, dedupe, windows | Variance component degraded |
| High | > 30% | Platform numbers cannot be trusted for automation | Variance component critical, Trust Gate holds |

## Trust gate evaluation

The gate is implemented by `check_signal_health` in `backend/app/tasks/apply_actions_queue.py` and
guards both execution paths in that module (the approved-actions queue and immediate single-action
execution). It returns a `SignalHealthGateResult` - decision, reason, score, per-channel component
inputs and the tenant's enforcement mode - rather than a bare boolean, so the callers can act on it
*and* audit it.

1. Load the newest `fact_signal_health_daily` snapshot for the tenant.
2. Score every channel row and map the score onto a decision using the configured thresholds.
   The row's own `status` enum floors the outcome: a row the rollup already flagged `critical`
   can never PASS, whatever its component columns add up to.
3. Take the worst decision across the Meta channels - one critical channel holds back the tenant.
4. Apply the tenant enforcement mode (Advisory / Soft-Block / Hard-Block).
5. PASS only when signal health >= `signal_health_healthy_threshold` on every channel;
   HOLD (alert only, nothing executes) in the degraded band; BLOCK below it, with the action left
   in the queue for manual review carrying the full explanation.

**The gate fails closed.** Absence of data is not evidence of health: no snapshot for the tenant is
a BLOCK, not a pass. (It used to be a pass - `check_signal_health` returned True whenever the table
held no row for today, on the reasoning that "no data means we proceed cautiously", which with an
empty table left the gate permanently open.) No enforcement mode can turn a HOLD or a BLOCK into a
PASS; a mode may only tighten the decision, and `hard_block` escalates a HOLD to a BLOCK. Nor is the
`TenantEnforcementSettings.enforcement_enabled` kill switch a bypass: it governs the budget/ROAS
enforcement rules, not this gate.

### Snapshot freshness

The rollup writes rows dated for the *previous* day, so yesterday's snapshot is the newest that can
exist. A snapshot older than `trust_gate_max_health_age_days` (default 1) is stale and does not count
as health: the gate returns `trust_gate_stale_health_decision`, which is HOLD by default - the action
is alerted and held, not applied - and can be raised to BLOCK per deployment. It is never PASS.

A snapshot dated in the *future* is invalid, not fresh. Clock skew, or a rollup invoked with a bad
`target_date`, would otherwise produce a row whose age is negative, which skips the staleness check
and scores as current until the calendar catches up. Such a row is treated exactly like a stale one,
and the "newest snapshot" query is bounded by today so one future-dated write cannot mask every real
current row behind it.

### Score derivation

`fact_signal_health_daily` stores components, not a composite, so the gate computes the 0-100 score
from the row using the component weights above over the columns that exist, renormalising across the
components that are actually populated:

| Component | Weight | Column |
|-----------|--------|--------|
| EMQ | 40% | `emq_score` |
| Freshness | 25% | `freshness_minutes`, decayed between `signal_health_fresh_minutes` (100) and `signal_health_stale_minutes` (0) |
| Variance | 20% | `100 - event_loss_pct` |
| Anomalies | 15% | `100 - api_error_rate` |

Renormalising is only safe while enough of the row is evidence. Every metric column is nullable and
`status` is NOT NULL defaulting to `ok`, so a row carrying nothing but `api_error_rate = 0` - 15% of
the weight - would renormalise to a flat 100 and PASS. A row must therefore populate at least
`signal_health_min_component_weight` (default 0.5) of the total weight before its score is trusted;
below that it is unscorable, and a missing score BLOCKs. This is the same "absence of data is not
health" rule as the fail-closed behaviour above, applied at column granularity.

### Configuration

Thresholds are read from `Settings` (`backend/app/core/config.py`) at every call site; they are never
hardcoded by callers.

| Setting | Default | Meaning |
|---------|---------|---------|
| `signal_health_healthy_threshold` | 70 | At or above this, PASS |
| `signal_health_degraded_threshold` | 40 | At or above this, HOLD; below it, BLOCK |
| `signal_health_fresh_minutes` | 60 | Data age scoring full marks for freshness |
| `signal_health_stale_minutes` | 1440 | Data age scoring zero for freshness |
| `signal_health_min_component_weight` | 0.5 | Fraction of the component weight a row must populate before it can be scored |
| `trust_gate_max_health_age_days` | 1 | How old a snapshot may be and still count |
| `trust_gate_stale_health_decision` | `hold` | Decision for a stale snapshot (`hold` or `block`) |

`TrustGateConfig` in `backend/app/stratum/core/trust_gate.py` reads `pass_threshold` and
`hold_threshold` from the same two settings, so 70/40 has one definition rather than two that can
drift apart.

Every decision records the inputs (component scores, variance band, EMQ drivers), the outcome and the
actor in the audit log so it is explainable and reversible. Held and blocked actions are audit-logged
with the same `to_audit_dict()` payload as executed ones - decision, reason, per-channel scores and
components, the thresholds they were compared against, and the enforcement mode - and are broadcast
to the tenant as an action status update so a hold is a visible alert rather than a silent no-op.

### The rules engine is gated too

`app.workers.tasks.rules.evaluate_all_rules` (beat key `evaluate-active-rules`, every 15 minutes,
queue `rules`) is the *second* path that acts on a tenant's campaigns: `_execute_action` pauses
campaigns, adjusts `daily_budget_cents` and sends WhatsApp alerts. It calls the same gate through
`check_signal_health_sync`, which loads the same rows and runs the same
`evaluate_signal_health`, so the thresholds cannot drift between the sync and async callers.

| Decision | Rules engine behaviour |
|----------|------------------------|
| PASS | The action executes; the gate payload is stored on the `RuleExecution` row |
| HOLD | Alert-only: `apply_label` and `send_alert` still run, `pause_campaign` and `adjust_budget` are held and recorded with the reason |
| BLOCK | The run stops before any campaign is touched |

A rule with an empty `conditions` list matches nothing. It previously matched *every* campaign of the
tenant, because the evaluator started from `all_match = True` and returned it unchanged.

### Prerequisite: the rollup has to run

Signal health only exists if `tasks.signal_health_rollup` runs (beat key `trust-signal-health-rollup`,
02:00 UTC, queue `sync`). A Celery worker consumes only the queues it is started with, so the worker
must be started with the full queue list - see "Celery Workers & Scheduled Tasks" in
`SERVER_DEPLOYMENT_GUIDE.md`. With the rollup not running, the gate correctly blocks everything.
