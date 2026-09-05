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

A tenant may raise or lower its own two thresholds during onboarding
(`TenantOnboarding.trust_threshold_autopilot` / `trust_threshold_alert`). Those are resolved into a
`SignalHealthThresholds` pair by `thresholds_for_tenant`, and the *same* pair is used by the API
summary, by the daily rollup when it stamps a row's status, and by the trust gate - so the band a
tenant is shown is the band it is enforced against. A pair that is missing, out of 0-100, or
inverted (`degraded > healthy`) is ignored in favour of the configured defaults. Before this the two
columns had no reader at all: onboarding collected a number and every code path graded at 70/40.

Both onboarding front doors write those columns through
`app/services/tenant/onboarding.py`: the wizard's trust gate step
(`POST /onboarding/steps`) and the conversational agent, whose completing turn calls
`persist_chat_onboarding`. The chat asks only for the autopilot edge, so it keeps the stored alert
edge at or below it rather than leaving an inverted pair that would be discarded for the defaults.

## Signal health components

Signal health is a weighted composite (0-100) computed per tenant and Meta channel
(`facebook` | `instagram` | `whatsapp`) by `app/services/signal_health/` - the single place it is
computed. The rollup writes what that service measured, the dashboard publishes it, and the trust
gate grades the row it produced, all from the same weights and the same scoring function
(`weighted_score` in `app/services/signal_health/scoring.py`).

Every component is measured from a table that has a writer, and every query is filtered by
`tenant_id` in SQL:

| Component | Weight | Measured from | Fact column |
|-----------|--------|---------------|-------------|
| EMQ | 40% | `capi_delivery_logs`: delivery success rate blended with hashed-identifier coverage | `emq_score` |
| Freshness | 25% | Newest genuine `Campaign.last_synced_at` for the tenant | `freshness_minutes` |
| Variance (event loss) | 20% | `capi_delivery_logs`: measured delivery failure rate | `event_loss_pct` |
| Anomalies (reliability) | 15% | `TenantPlatformConnection` status, `error_count`, `last_error` | `api_error_rate` |

`api_error_rate` is **not** a percentage of failed API calls. Nothing in the system measures a
request-level error rate - the Meta connection records a status, a `last_error` and an error counter,
but never a denominator - so the column stores the *connection-health deficit*,
`100 - the reliability component`, which is exactly what the gate reads back out of it. Writer and
reader agree; the name is legacy, and `API_ERROR_RATE_IS_CONNECTION_DEFICIT` in the rollup says so at
the point of use. Anything that bands this column as a percentage is wrong: that is what made one
recorded connection error turn a tenant scoring 97 into a `critical` row that blocked the gate.

Two components come from the delivery table because two of the fact table's four metric columns are
delivery-derived. That is a property of the schema, stated here rather than hidden: 60% of the weight
moves with CAPI delivery, so a tenant that delivers nothing cannot be scored at all (see below).

`last_synced_at` is trustworthy as a freshness input because the Meta insights ingestion advances it
only when Meta actually returned rows and deliberately leaves it alone on failure
(`app/workers/tasks/sync.py`). A failed sync therefore degrades freshness instead of refreshing it.

The freshness query is bounded by the end of the window (`last_synced_at <= window.end`), so only
syncs that had already happened by then count. Without that bound a sync performed *after* the window
- the 02:00 UTC rollup writing a row for yesterday, or any re-run of a historical date - produced a
negative age that was clamped to zero and reported as perfect freshness on the strength of data that
did not exist during the day being scored. It is the same rule the gate applies to snapshots: dated
in the future means invalid, not fresh.

Hashed-identifier coverage is the *only* match-quality signal `capi_delivery_logs` genuinely carries:
`user_data_hash` is set when the caller sent hashed customer identifiers with the event. Meta's own
reported match rate is not in that table, so it is not in this score. Its share of the EMQ component
is `signal_health_identifier_coverage_weight` (30%); the rest is the delivery success rate.

Daily snapshots are stored in `fact_signal_health_daily` (rollup task `tasks.signal_health_rollup`,
beat key `trust-signal-health-rollup`, 02:00 UTC). The rollup measures over the UTC day the row is
dated for, so a re-run for a historical date reproduces the same numbers.

### When data is missing

**No component is ever defaulted.** A component that cannot be measured is reported as a missing
input naming what was absent, and the composite is computed over the components that remain, with
their weights renormalised.

Renormalising is only safe while enough of the evidence is present, so a tenant must have at least
`signal_health_min_component_weight` (default 0.5) of the total weight measured before it can be
scored at all. Below that the outcome is **`insufficient_data`**: no score, and the list of missing
inputs. Concretely:

| What the tenant has | Measured weight | Outcome |
|---------------------|-----------------|---------|
| Delivery + freshness + connection | 1.00 | Scored |
| Delivery only | 0.60 | Scored |
| Delivery + freshness | 0.85 | Scored |
| Freshness + connection, no delivery | 0.40 | `insufficient_data` |
| Nothing | 0.00 | `insufficient_data` |

A delivery sample smaller than `signal_health_min_delivery_events` (default 10) is treated as absent
rather than scored: a success rate over two events is noise, and one failure would read as 50% loss.

Each consumer honours that outcome rather than substituting a number:

- **The rollup writes no `fact_signal_health_daily` row.** It also *withdraws* any row it wrote
  earlier for the same tenant, date and channel, because a row nothing can substantiate would keep
  the gate passing on it. The task result reports `insufficient_data` and `rows_withdrawn` counts.
- **The trust gate therefore has nothing to grade** and fails closed - see below.
- **The API sends `status: "insufficient_data"` with `overall_score: null`**, plus `missing_inputs`
  (English detail) and `missing_input_codes` (translated by the UI). It does not send `0`, which
  would read as "terrible" when the truth is "unknown".
- **The dashboard renders it as an explicit gap** listing what is missing - not a zero, not a
  spinner, not a plausible score. That includes the fixed trust-gate badge shown on every
  authenticated route, the account-manager portfolio, the data quality dashboard and the public
  embeddable widgets; none of them substitutes a number of its own.

### Measured health and the gate's decision are two different answers

`SignalHealthSummary` carries both, and they can legitimately disagree. `status` bands a **live**
trailing-window measurement (`signal_health_delivery_window_hours`, default 24h). `gate_decision` is
what the trust gate would decide right now, and the gate grades the newest `fact_signal_health_daily`
snapshot - yesterday's rollup - not the live window.

A tenant that started delivering events this morning measures `healthy` while the gate still BLOCKs
for want of a snapshot. Anything that uses gate verbs ("autopilot executes", "PASS") must read
`gate_decision`; publishing only the live band told such a tenant that autopilot was running while
every action it produced was being blocked, and the divergence was always in the flattering
direction on day one. `autopilot_enabled` likewise follows the gate, and `gate_health_date` is null
exactly when the gate has nothing to grade - which the UI renders as "no data" rather than BLOCK,
because nothing is wrong, nothing is known yet.

### What this replaced

Until this change the composite was not measured anywhere. `fetch_platform_metrics` returned
`emq_score` 85 or 65, `event_loss_pct` 3.5 or 15 and `api_error_rate` 0.5 or 8 chosen solely on
whether the Meta connection had ever recorded an error, with freshness defaulting to 30 minutes, and
wrote them into `fact_signal_health_daily`. The dashboard published a hardcoded `overall_score=85`
for any tenant that merely had campaigns. The gate had just been hardened to fail closed on missing
data, which meant it was grading invented data instead.

## EMQ drivers

The EMQ component has exactly two drivers, because `capi_delivery_logs` carries exactly two things
that bear on match quality:

| Driver | Weight | Fed by |
|--------|--------|--------|
| Delivery success rate | 70% | `capi_delivery_logs.status` over the window |
| Hashed-identifier coverage | 30% | `capi_delivery_logs.user_data_hash` presence |

The split is `signal_health_identifier_coverage_weight`. When there are delivery rows but coverage
cannot be computed, the component is the success rate alone.

Delivery attempts reach that table from the CAPI send path
(`BaseCAPIConnector.send_events` -> `DeliveryLogger`), attributed to the tenant the connector was
built for. An attempt that cannot be attributed to a tenant is logged as a warning and recorded
nowhere - an unattributed row would either leak across tenants or silently inflate somebody's score.

Two properties of that path matter because event loss is computed as a share of *recorded* attempts,
so a gap in the rows reads as good news:

- **Dropped events are recorded as failures.** `send_events` returns early when the connector is
  disconnected or the circuit breaker is open; both paths now write rows (`failed` / `circuit_open`)
  before returning, as does an attempt that exhausts its retries by raising. Previously they wrote
  nothing, so a disconnected connector reported 0% loss, and with a 60s recovery timeout a sustained
  outage recorded five failed batches and then nothing at all while the platform kept refusing
  everything.
- **The buffer is flushed at the end of every send**, and again on application shutdown. The logger
  batches, but the batch used to be flushed only from inside a *later* `log_delivery` call, so a
  quiet tenant's rows sat in process memory until more traffic arrived, or vanished on restart. With
  `signal_health_min_delivery_events` at 10, that turned real deliveries into "no CAPI events were
  delivered" - the mirror image of the fabrication this engine exists to remove, pushing a genuine
  low-volume tenant into `insufficient_data` and a blocked gate.

`user_data_hash` is the SHA-256 of the **match-quality identifier values** actually sent (`em`, `ph`,
`external_id`, `fbc`, `fbp`, ...), and is null when the event carried none of them. It previously
hashed the field *names*, so every event with the same key set produced an identical digest that
correlated nothing, and it was non-null for any non-empty `user_data` - including the IP/user-agent
pair sent on every landing-page event, which meant a tenant sending no email, phone or external id
scored 100% identifier coverage for 30% of the EMQ component.

There is deliberately **no** pixel-coverage, conversion-latency or attribution-accuracy driver. Those
existed in an earlier design that read pixel events, conversion timestamps and a GA4 baseline out of
process-local Python lists that nothing wrote to; the numbers they produced were not measurements.
The GA4 read-only baseline is still ingested into `fact_ga4_daily` and still drives the attribution
variance bands below - it simply no longer enters EMQ through an in-memory side channel. GA4 remains
an independent verification source and is never an ad channel.

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
2. Score every channel row and map the score onto a decision using the configured thresholds
   (or the tenant's own overrides). The row's own `status` enum floors the outcome: a row the
   rollup already flagged `critical` can never PASS, whatever its component columns add up to.
   Because that enum can only ever make the gate *stricter*, the rollup derives it from the same
   configured bands the composite is graded against - `determine_status` bands the composite and
   carries no thresholds of its own. It used to apply four unconfigured per-metric tables
   (freshness over 360 minutes, EMQ under 70, event loss over 20, "api_error_rate" over 10)
   calibrated for the numbers the rollup used to fabricate; against real measurements they silently
   overrode the documented 70/40 contract, writing `critical` for tenants scoring 93.5 and 97.
   The one extra band is `risk`, used for a composite in the healthy band that was measured over an
   incomplete set of components - "passing, on partial evidence". `risk` maps to PASS, so the
   decision is unchanged; it exists so the UI can say so without a second threshold table.
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
components that are actually populated. It does so through the same `weighted_score` function - and
therefore the same configured weights and the same evidence floor - that
`app/services/signal_health` used to produce the row, so the score cannot mean one thing where it is
written and another where it is enforced:

| Component | Weight | Column |
|-----------|--------|--------|
| EMQ | 40% | `emq_score` |
| Freshness | 25% | `freshness_minutes`, decayed between `signal_health_fresh_minutes` (100) and `signal_health_stale_minutes` (0) |
| Variance | 20% | `100 - event_loss_pct` |
| Anomalies | 15% | `100 - api_error_rate` (the connection-health deficit, see above) |

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
| `signal_health_emq_weight` | 0.40 | Weight of the EMQ/delivery-quality component |
| `signal_health_freshness_weight` | 0.25 | Weight of the freshness component |
| `signal_health_variance_weight` | 0.20 | Weight of the event-loss component |
| `signal_health_anomaly_weight` | 0.15 | Weight of the API/connection reliability component |
| `signal_health_min_component_weight` | 0.5 | Fraction of the component weight that must be measured before anything can be scored |
| `signal_health_delivery_window_hours` | 24 | Default width of the CAPI delivery window |
| `signal_health_min_delivery_events` | 10 | Delivery attempts needed before delivery quality can be scored |
| `signal_health_identifier_coverage_weight` | 0.30 | Share of the EMQ component driven by hashed-identifier coverage |
| `signal_health_connection_error_penalty` | 20 | Points removed from the connection component per recorded error |
| `trust_gate_max_health_age_days` | 1 | How old a snapshot may be and still count |
| `trust_gate_stale_health_decision` | `hold` | Decision for a stale snapshot (`hold` or `block`) |

The weights are read from `Settings` when they are used, not captured at import. `SignalHealthConfig`
is a dataclass, so a plain `float(getattr(settings, ...))` default would be evaluated once when the
class is defined and frozen for the life of the process; every one of its fields is therefore a
`default_factory`. That mattered in practice: the gate constructs a `SignalHealthConfig` per call and
passes it into the shared `weighted_score`, so with frozen defaults the gate scored with import-time
weights while `app/services/signal_health` scored with the current ones - the function was shared,
the weights were not.

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

Running it is necessary but not sufficient. The rollup can only write what a tenant has produced, so
a tenant with no CAPI delivery traffic gets no row however reliably the rollup runs, and the gate
blocks. That is the intended outcome: automation stays off until there is signal to justify it. The
task result distinguishes the two cases - `records_processed` for rows written, `insufficient_data`
for tenant/channel pairs skipped, `rows_withdrawn` for previously written rows that can no longer be
substantiated.
