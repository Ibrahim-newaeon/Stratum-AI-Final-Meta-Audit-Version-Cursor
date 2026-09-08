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

### Measuring a whole portfolio at once

The account-manager portfolio (`GET /tenants/portfolio`) needs the same answer for every tenant it
lists. Running the per-tenant path in a loop would be one query per tenant per channel, so
`app/services/signal_health/service.py` also exposes a batched form:
`measure_delivery_for_tenants`, `measure_freshness_for_tenants`, `measure_connection_for_tenants`
and `thresholds_for_tenants`, composed by `compute_portfolio_signal_health`. Each reads the same
table with the same predicates as its single-tenant counterpart, grouped by tenant instead of
filtered to one, and hands its measurements to the same `_build_computation`.

That is what makes the two views agree: a tenant that is `insufficient_data` on its own dashboard is
`insufficient_data` in its account manager's list, with the same missing inputs and against the same
per-tenant band edges. `tenant_id` stays in the WHERE clause - grouping alone is not isolation, and
the caller passes only the tenants it has authorised. `check_signal_health_for_tenants` in
`app/tasks/apply_actions_queue.py` is the same treatment for the gate: the same rows, the same
enforcement modes, the same pure `evaluate_signal_health`, so a portfolio row shows the decision the
tenant's automation is actually subject to.

The portfolio's other columns are measured the same way, each from the table that owns it - spend
and ROAS from `campaign_metrics`, open incidents from `pacing_alerts`, held budget from the
campaigns named by queued `fact_actions_queue` rows, plan and renewal from the tenant's Paddle
state - and a column with no source for a tenant stays null. See
`app/services/account_portfolio.py`.

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

## What happens after the gate passes: the Meta write path

A PASS is permission to *attempt* an action, not the action itself. Between the gate and a live ad
account sits `app/services/meta/action_executor.py`, driven by `MetaExecutor` in
`app/tasks/apply_actions_queue.py` and talking to Meta only through `app/services/meta/write_client.py`
— the one module in the codebase that issues anything other than a `GET`.

**This path replaced a simulator.** The previous `MetaExecutor.execute_action` never contacted Meta: it
logged a line, returned a hardcoded `before_value = {"status": "ACTIVE", "daily_budget": 10000}`,
derived an `after_value` arithmetically from that invention and reported
`{"success": True, "platform_response": {"request_id": "meta_123"}}`. Those numbers were written into
`fact_actions_queue.before_value` / `after_value` and into the audit log, so the trail said a budget had
moved from $100 to $80 on accounts nobody had read. Everything below exists so that the recorded values
are measurements.

### Order of operations

Each step can only refuse; none can soften an earlier decision.

| # | Step | On failure |
|---|------|-----------|
| 1 | `autopilot_execution_enabled` is true | Refused, before a token is even decrypted |
| 2 | A row left in `applying` by an earlier attempt is reconciled, not re-decided | See "Idempotency" below |
| 3 | The trust gate permits execution (re-checked here, not trusted from the caller) | Refused |
| 4 | Idempotency: the queue row is `approved` and not already `applied` | `already_applied`, no request |
| 5 | The tenant's enforcement mode permits it | Refused |
| 6 | The entity is **read** from Meta - this is `before_value` - and its own `account_id` must name an enabled ad account of this tenant | Refused; an unreadable entity, or one in an account Stratum does not know, is never acted on |
| 7 | Every guard rail passes, evaluated against the state just read | Refused with the rail named |
| 8 | Dry run stops here, having run every check | `dry_run`; the row keeps its status |
| 9 | The **pre-write claim** is committed, then the write | See "Ambiguity" below |
| 10 | The entity is **read again** and compared against the intent | Marked `failed`, with both values recorded |

Step 6 exists because the credential is resolved per *connection*: the ad account on it is only the
tenant's oldest enabled one, since nothing identifies the right account until the entity has been read.
For a tenant with several accounts — an agency, a multi-market advertiser — that provisional pick would
otherwise decide the currency for an entity in a different account, and with it a 100x conversion factor
and the scale of the floor and ceiling. The entity's own `account_id` overrides it before any money is
converted, and an account this tenant has no enabled record for is a refusal rather than a fallback.

Enforcement modes are the existing `TenantEnforcementSettings.default_mode`, not a second policy system:

| Mode | Behaviour |
|------|-----------|
| `advisory` | Proceeds |
| `soft_block` | Requires a live `PendingConfirmationToken` for this tenant, action type and entity. The token is **validated** at step 5 and **spent** at step 9, immediately before the write: a confirmation authorises a change, so an action a guard rail then refuses must not burn it. It is deleted and flushed at once — the session runs with `autoflush=False`, so an unflushed delete stays visible to the next query in the same transaction and two rows could spend one confirmation. |
| `hard_block` | Refused - the mode means "prevent the action at the API" |

### Guard rails

All limits come from `Settings`; none is a literal at a call site. A violated rail is a **refusal with a
recorded reason, never a clamp-and-proceed** — quietly applying a smaller change than the one that was
approved is still applying something nobody approved. Every rail evaluated, its inputs and its verdict
go into the audit record, including the ones that passed.

| Guard rail | Setting | Default | What it measures |
|------------|---------|---------|------------------|
| Action-type allowlist | `autopilot_executable_action_types` | `budget_decrease,pause_adset,bid_decrease` | Only these types may ever execute automatically |
| Per-tenant daily action cap | `autopilot_max_executed_actions_per_tenant_per_day` | 10 | Rows the tenant has `applied` **since midnight UTC**, counted by `applied_at` |
| Single-action change | `autopilot_max_budget_change_pct` | 20% | Change against the value **live on Meta right now** |
| Cumulative daily change | `autopilot_max_cumulative_budget_change_pct` | 50% | Change against the value the entity **started the day on**, read from the `before_value` of the earliest action applied to it since midnight UTC |
| Currency has configured limits | `autopilot_daily_budget_limits_by_currency` | *(empty)* | An offset-1 currency must have an explicit floor/ceiling pair before any budget action runs |
| Daily budget floor | `autopilot_min_daily_budget_major` (or the per-currency override) | 5 | Absolute amount in the account's major currency unit |
| Daily budget ceiling | `autopilot_max_daily_budget_major` (or the per-currency override) | 1000 | Absolute amount in the account's major currency unit |

The allowlist default is `app.autopilot.service.SAFE_ACTIONS` **minus `pause_creative`**.
`budget_increase`, `pause_campaign` and every `enable_*` are absent because raising spend or restarting
delivery is not something automation should do unsupervised. `pause_creative` is withheld for a
different reason: its target node is an assumption. The executor maps `entity_type` `"creative"` to the
Meta **ad** — an AdCreative has no status of its own — but the only in-repo producer
(`app/analytics/logic/recommend.py`, from fatigue detection) emits `fact_creative.creative_id`, which is
nowhere established to be an ad id. If it is an AdCreative id the read fails closed with Graph `#100`
and nothing is written, but a money-affecting action whose node identity rests on a comment does not
belong in a default allowlist. It stays a safe action for the approval workflow; only auto-execution is
opt-in, until the producer is shown to emit an ad id.

**Both day-scoped rails count by `applied_at`, not by `fact_actions_queue.date`.** That column is
stamped once when the row is queued and never updated, and the batch task selects every approved row
with no date filter, so approve-today/apply-tomorrow and a backlog drained after midnight are both
ordinary. Keyed on `date` the cap would simply stop counting for those rows — a tenant's whole backlog
would execute while the counter read zero — and the cumulative rail would find no history, fall back to
the value live now, and collapse into the single-action rail. The cumulative rail exists precisely
because the single-action rail alone permits ten "within 20%" steps that together triple a morning
budget.

**The rails also have to see this run's own writes.** The session runs with `autoflush=False` and both
rails answer from a `SELECT`, so the batch task commits each action's outcome before starting the next
one. Without that, every action in a run reads the same pre-batch state: two hundred rows sail past a
cap of ten, each recording `daily_action_cap passed` on the way through.

**The absolute floor and ceiling are scale-sensitive, so they are per currency.** One pair of numbers
compared against an amount in the account's major unit only travels between currencies of similar
magnitude. Meta's offset table names the ones that break the assumption: an offset of 1 (JPY, KRW, VND,
IDR, CLP, COP, CRC, HUF, ISK, PYG, TWD) means the currency has no minor unit, so an ordinary daily
budget is a five-figure number of major units. Against the shipped ceiling of 1000 every action on such
an account would be refused and the floor of 5 would never bind. Rather than let one scalar be quietly
wrong for a whole class of accounts — or have an operator raise the global ceiling for a yen tenant and
raise it for every dollar tenant on the deployment at the same time — a budget action on an offset-1
account is **refused** until `AUTOPILOT_DAILY_BUDGET_LIMITS_BY_CURRENCY` carries a
`CURRENCY:floor:ceiling` entry for it. The refusal names the setting.

Three further structural refusals protect budgets specifically:

- **An ad set inside an Advantage campaign budget (CBO) campaign is refused.** The campaign holds the
  budget, so the ad set has none of its own to change; Meta answers `error_subcode 1885621`,
  "You can only set an ad set budget or a campaign budget".
- **A campaign that carries no budget of its own is refused.** Its budgets live on its ad sets, and
  setting a campaign `daily_budget` would switch it into CBO and override every ad set at once — far
  more than the approved action asked for.
- **Lifetime budgets are refused.** The floor and ceiling are daily amounts; comparing a lifetime
  budget against a daily floor compares two different kinds of number.

### Money: Meta's units are not this schema's units

Meta's `daily_budget`, `lifetime_budget` and `bid_amount` are integers in the ad account's **API
units**. Meta's own wording: *"The bid amount's unit is cents for currencies like USD, EUR, and the
basic unit for currencies like JPY, KRW."* This repository's `*_cents` columns are a different
convention — hundredths of the **major** unit for every currency, because every reader divides by 100
with no currency awareness (see CLAUDE.md). For a zero-decimal currency the two differ by 100x:
`daily_budget_cents = 100000` means ¥1000.00, which Meta wants as `1000`, not `100000`.

The conversion happens in exactly one place, `meta_currency_offset` / `major_to_meta_minor` in
`write_client.py`, and it uses **Meta's** published offset table rather than ISO 4217, because the two
disagree in both directions:

- HUF, IDR, TWD, COP and CRC are two-decimal in ISO 4217 but **offset 1** at Meta — the ISO table would
  send a number 100x too large.
- BHD, JOD, KWD, OMR and TND are three-decimal in ISO 4217 but **offset 100** at Meta ("No currencies
  have an offset of 1000") — the ISO table would send one 10x too large.

A currency whose offset is not in that table is **refused**, not assumed to be 100. The ad account
currency is read from Meta at execution time and cross-checked against `tenant_ad_account.currency`; a
disagreement is an unresolved ambiguity about a monetary unit, so it too is a refusal.

For the same reason a budget action must specify its change unambiguously: `percentage`,
`amount_major`, or `amount` **together with** `amount_unit`. A bare `amount` is refused — that key
means major units to `validate_action_caps` (which formats it with a `$`) and minor units to the
simulator that was removed, a 100x difference.

### Idempotency: three mechanisms, because no one of them covers the window

- **The queue row's status.** Only `approved` executes; a row already `applied` returns without
  touching Meta. Durable, and the primary defence against a re-delivered Celery message — but only once
  it has been committed, which is what the claim below is for. The batch query also takes
  `FOR UPDATE SKIP LOCKED`, so a redelivered task or a user-triggered `apply_single_action` cannot read
  the same row as `approved` alongside a run already working on it.
- **The pre-write claim.** Immediately before the request leaves, the row is moved to a new `applying`
  status and the **resolved absolute target** is written to it and committed. The transition is a
  conditional `UPDATE ... WHERE status = 'approved'` that must affect exactly one row — the lock above
  is not sufficient on its own, because committing after each action releases it, so a second run
  started mid-batch can legitimately hold the remaining rows. Losing the claim is a refusal, and no
  request is issued. This is also what makes a
  *relative* change safe. "Cut the budget 20%" resolves against whatever is live, so a second attempt
  after a crash resolves against the already-reduced value and the two compound — 50000 → 40000 →
  32000, both recorded as verified successes. Comparing the entity against a freshly derived intent can
  never notice, because the intent came from the entity. A row found in `applying` is reconciled
  against the recorded target and **the write is never repeated**: the target being present means it
  landed, and the target being absent is `unknown` for an operator, because "not there" cannot
  distinguish "never left" from "landed and somebody changed it back". The batch query does not select
  `applying` rows at all, so a stranded one is never re-fired automatically; `apply_single_action`
  admits one so an operator can reconcile it, skipping the gate and the caps, since reconciling reads
  and does not act.
- **The observed current state.** If the entity already carries the intent before any write, none is
  issued. This covers somebody else having got there first — a human pausing the same ad set a minute
  earlier — and is recorded as applied with `before == after` and `idempotent_no_op` set, so the trail
  says "already in the intended state" rather than claiming a change was made. On its own it is
  sufficient only for status actions, whose target is absolute.

A dry run is evaluated **before** the observed-state check, so an entity that already matches still
reports `dry_run`. Marking the row applied would stamp `applied_at` and consume the tenant's daily cap
from a mode whose entire promise is that it only reports.

### Ambiguity is reconciled, never retried

A write whose outcome cannot be established from the response may or may not have been applied.
`MetaWriteAmbiguousError` is deliberately **not** a `MetaAPIError`, so no handler that retries "Meta
errors" can catch it, and `update_entity` raises it for all three indeterminate answers:

- a timeout or connection reset — the request left this process and no response arrived;
- a **5xx Meta did not itself mark permanent** — a gateway or backend failure can happen either side of
  the mutation. Recording `failed` with no after-value would put "the pause did not happen" in the
  audit trail while the ad set may in fact be paused;
- a **2xx with a body this client cannot parse** — Meta accepted the request and then said something
  unreadable.

A 4xx, a throttle, a token rejection and anything carrying `is_transient: false` are excluded: each is
Meta stating it did not apply the change.

The executor re-reads the entity exactly once: matching the intent means the write landed and the
action is applied; not matching means the outcome is `unknown` and an operator decides. The write is
never repeated, and the row is left `failed` rather than `approved` so the next queue run cannot pick
it up. Reads carry no side effect, so a transport failure or an unparseable body on a *read* stays an
ordinary error.

Meta's error codes are mapped to distinct types so the caller knows what it is holding: `MetaTokenError`
(190/102 — disconnect, do not retry), `MetaRateLimitError` (4/17/32/613/80000-80014 or HTTP 429 — back
off using `estimated_time_to_regain_access`, which Meta reports in minutes), and
`MetaWriteValidationError` for anything Meta flags `is_transient: false` or that carries a permanently
invalid code or subcode (100 invalid parameter, 200 permission, 1487901 daily budget below the account
minimum, 1885621 ad set vs campaign budget). A validation error is recorded as failed, not retried.

### Reversibility

`revert_meta_action` is the one-click override the product promises. It derives the fields to restore by
diffing the recorded before- and after-values, writes the before-value back through the same client,
verifies it by re-reading, and records its own `action_reverted` audit entry.

It **refuses when the entity no longer matches the recorded after-value.** Somebody — a human in Ads
Manager, another tool, Meta itself — has changed it since, so the recorded before-value is no longer
"what it was before us" and writing it would silently discard their change. The revert reports the drift
with the field and both values, and leaves the entity alone.

A revert deliberately does not consult the trust gate or the budget guard rails: undoing an automated
change must stay available exactly when signal health has degraded, and restoring a previous value
cannot breach a limit that previous value already satisfied. It does honour the master switch and
dry-run, which govern whether this deployment may talk to Meta at all.

### Off by default, and how to turn it on

Merging this cannot start spending money. Three independent things must all be true:

1. **Meta App Review for `ads_management`.** `ads_read` cannot write. Until the app holds
   `ads_management` and the tenant has reconnected so the stored token carries the scope, every write
   fails with a permission error (code 200), which is recorded as a non-retryable failure. Nothing else
   in this document can be exercised against a real account before that.
2. **`AUTOPILOT_EXECUTION_ENABLED=true`** (default `false`) — then
   **`AUTOPILOT_EXECUTION_DRY_RUN=false`** (default `true`). With dry-run on, every gate, enforcement
   and guard-rail check runs and the intended change is recorded, but no write endpoint is called.
   Run a full day in dry-run and read the recorded intents before turning it off.
3. **The Celery task is scheduled, which is not the same as enabled.** `tasks.apply_actions_queue`
   runs on beat key `autopilot-apply-actions-queue`, every 5 minutes, on queue `sync` (a queue the
   worker already drains — a beat entry on an unconsumed queue would look scheduled while the
   messages piled up unread, so a test asserts the queue is in `CELERY_QUEUES`).

   Scheduling it does **not** start writes, and that is the point of wiring it separately from
   turning execution on. The master switch is checked before the row's token is decrypted and
   before the trust gate is consulted, so with the shipped defaults every run refuses each approved
   row with `EXECUTION_DISABLED` and issues no Meta request at all — asserted by
   `test_a_scheduled_run_writes_nothing_while_execution_is_disabled`, which checks that *no* HTTP
   request is made, not merely no `POST`.

   Only the fan-out wrapper is left off the schedule: `tasks.schedule_apply_actions_queue` exists
   solely to re-enqueue `tasks.apply_actions_queue`, so scheduling both would run the batch twice
   per tick against the same day-scoped guard rails. A test asserts exactly one of the two is
   scheduled.

| Setting | Default | Meaning |
|---------|---------|---------|
| `autopilot_execution_enabled` | `false` | Master switch for Meta writes |
| `autopilot_execution_dry_run` | `true` | Run every check, call no write endpoint |
| `meta_write_request_timeout_seconds` | 30 | Per-request timeout for Meta reads and writes |
| `autopilot_max_budget_change_pct` | 20 | Largest change one action may make |
| `autopilot_max_cumulative_budget_change_pct` | 50 | Largest change per entity per UTC day |
| `autopilot_min_daily_budget_major` | 5 | Default daily budget floor, account major currency unit |
| `autopilot_max_daily_budget_major` | 1000 | Default daily budget ceiling, account major currency unit |
| `autopilot_daily_budget_limits_by_currency` | *(empty)* | `CURRENCY:floor:ceiling` overrides; **required** for an offset-1 currency |
| `autopilot_max_executed_actions_per_tenant_per_day` | 10 | Per-tenant daily execution cap |
| `autopilot_executable_action_types` | `budget_decrease,pause_adset,bid_decrease` | Allowlist of auto-executable action types |

An operator running an offset-1 ad account (JPY, KRW, VND, …) has a fourth thing to do before any
budget action can execute: set that currency's floor and ceiling in
`AUTOPILOT_DAILY_BUDGET_LIMITS_BY_CURRENCY`. Until then budget actions on that account are refused with
a reason naming the setting; status actions are unaffected.

Only `ACTIVE` and `PAUSED` can ever be written. Meta also accepts `DELETED` and `ARCHIVED` on update;
both are destructive and are unreachable from autopilot. The writable-field allowlist is enforced
locally before any request, per entity type: campaign (`status`, `daily_budget`, `lifetime_budget`),
ad set (those plus `bid_amount`), ad (`status` only — an ad has no budget, and Meta refuses
`bid_amount` on one: "We no longer allow setting the bid_amount value on an ad").

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
