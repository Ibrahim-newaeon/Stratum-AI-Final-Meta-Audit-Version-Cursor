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

`HEALTHY_THRESHOLD = 70`, `DEGRADED_THRESHOLD = 40`. Never auto-execute when `signal_health < 70`.
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

1. Load the latest signal health for the channel the action targets.
2. Apply the enforcement mode (Advisory / Soft-Block / Hard-Block) configured for the tenant.
3. PASS only when signal health >= 70 and no critical anomaly or high-variance flag is active.
4. HOLD (alert) between 40 and 69, or when the GA4 baseline is stale beyond the freshness threshold.
5. BLOCK below 40; the action is queued for manual review with the full explanation and rollback plan.

Every decision records the inputs (component scores, variance band, EMQ drivers), the outcome and the
actor in the audit log so it is explainable and reversible.
