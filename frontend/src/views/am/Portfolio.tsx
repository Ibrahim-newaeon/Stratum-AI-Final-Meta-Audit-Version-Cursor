/**
 * Account Manager Portfolio View
 *
 * Primary goal: Reduce firefighting, explain performance, drive renewals
 * Shows all assigned tenants with signal health, incidents, and health indicators
 *
 * Every per-tenant metric here used to be generated with `Math.random()` and
 * attached to the *real* tenant list, so a named customer was shown an invented
 * EMQ score, an invented autopilot mode, an invented budget at risk and an
 * invented ROAS - re-rolled on every mount - and the view then sorted and
 * filtered on them. They now come from `GET /tenants/portfolio`, one batched
 * call that measures each metric from the table that owns it.
 *
 * The contract that replaced the fabrication survives: a metric with no source
 * for a tenant arrives as `null` and renders as a dash. It is never coerced to
 * `0`, because a zero is a claim - no spend, no held budget, no incidents - and
 * "we did not measure this" is not that claim. Where a `0` does arrive it is a
 * real count and is shown as one.
 */

import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { BudgetAtRiskChip, ConfidenceBandBadge } from '@/components/shared';
import { type TenantPortfolioRow, useTenantPortfolio } from '@/api/hooks';
import type { SignalHealthStatus, TrustGateDecision } from '@/api/dashboard';
import {
  ArrowTrendingDownIcon,
  ArrowTrendingUpIcon,
  BellAlertIcon,
  ChevronRightIcon,
  DocumentArrowDownIcon,
  ExclamationTriangleIcon,
  FireIcon,
  FunnelIcon,
  MagnifyingGlassIcon,
} from '@heroicons/react/24/outline';

type SortField = 'name' | 'signalHealth' | 'budgetAtRisk' | 'renewalDate' | 'spend';

// How many tenants one page of the portfolio covers. The endpoint caps at 100.
const PAGE_SIZE = 100;

const DASH = '—';
const DAY_MS = 24 * 60 * 60 * 1000;
const RENEWAL_SOON_DAYS = 30;

// `insufficient_data` is a distinct state from a bad score: nothing could be
// measured, which is neither healthy nor critical, and must never be rendered
// as a number. Its label says so rather than borrowing a band name.
const STATUS_LABELS: Record<SignalHealthStatus, string> = {
  healthy: 'healthy',
  degraded: 'degraded',
  critical: 'critical',
  insufficient_data: 'not measured',
};

const STATUS_COLORS: Record<SignalHealthStatus, string> = {
  healthy: 'text-success bg-success/10',
  degraded: 'text-orange-400 bg-orange-400/10',
  critical: 'text-danger bg-danger/10',
  insufficient_data: 'text-text-muted bg-white/5',
};

// The trust gate's own verdict, in the gate's vocabulary. The portfolio shows
// this rather than an "autopilot mode" because the gate is what actually
// governs whether an action runs, and a mode label would promise degrees of
// automation ("scaling capped at +10%") that the gate does not implement.
const GATE_LABELS: Record<TrustGateDecision, string> = {
  pass: 'Autopilot: PASS',
  hold: 'Autopilot: HOLD',
  block: 'Autopilot: BLOCK',
};

const GATE_COLORS: Record<TrustGateDecision, string> = {
  pass: 'text-success bg-success/5 border-success/20',
  hold: 'text-warning bg-warning/5 border-warning/20',
  block: 'text-danger bg-danger/5 border-danger/20',
};

/** Whether the tenant was measured and found wanting - not merely unmeasured. */
function isMeasuredRisk(tenant: TenantPortfolioRow): boolean {
  return (
    tenant.signal_health_status === 'degraded' || tenant.signal_health_status === 'critical'
  );
}

/**
 * Compare two possibly-unmeasured values, always sinking the unmeasured ones.
 *
 * An unmeasured metric has no place in the ordering at all - sorting it as a
 * zero would put a tenant nobody has measured at the top of "worst signal
 * health" or the bottom of "highest spend" and make the list lie by omission.
 */
function compareNullable(
  a: number | null,
  b: number | null,
  direction: 'asc' | 'desc'
): number {
  if (a === null) return b === null ? 0 : 1;
  if (b === null) return -1;
  return direction === 'asc' ? a - b : b - a;
}

function parseDate(value: string | null): Date | null {
  if (!value) return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatMoney(value: number): string {
  return `$${Math.round(value).toLocaleString()}`;
}

function formatCompactMoney(value: number | null): string {
  if (value === null) return DASH;
  if (Math.abs(value) >= 1000) return `$${(value / 1000).toFixed(0)}k`;
  return `$${Math.round(value).toLocaleString()}`;
}

function formatDaysUntil(value: string | null): string {
  const date = parseDate(value);
  if (!date) return DASH;
  const days = Math.floor((date.getTime() - Date.now()) / DAY_MS);
  if (days < 0) return 'Overdue';
  if (days === 0) return 'Today';
  if (days === 1) return '1 day';
  return `${days} days`;
}

function formatSince(value: string | null): string {
  const date = parseDate(value);
  if (!date) return DASH;
  const days = Math.floor((Date.now() - date.getTime()) / DAY_MS);
  if (days <= 0) return 'Today';
  if (days === 1) return 'Yesterday';
  return `${days}d ago`;
}

function formatSigned(value: number, digits = 0): string {
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`;
}

export default function Portfolio() {
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<SignalHealthStatus | 'all'>('all');
  const [sortField, setSortField] = useState<SortField>('signalHealth');
  const [showAtRiskOnly, setShowAtRiskOnly] = useState(false);

  const { data, isLoading, isError } = useTenantPortfolio({ limit: PAGE_SIZE });

  const tenants = useMemo(() => data?.tenants ?? [], [data]);
  const spendWindowDays = data?.spend_window_days ?? null;

  const filteredTenants = useMemo(() => {
    let result = [...tenants];

    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      result = result.filter(
        (t) =>
          t.name.toLowerCase().includes(query) ||
          (t.industry ?? '').toLowerCase().includes(query)
      );
    }

    if (statusFilter !== 'all') {
      result = result.filter((t) => t.signal_health_status === statusFilter);
    }

    if (showAtRiskOnly) {
      // "At risk" means measured and not healthy. A tenant nobody could measure
      // is not at risk; it is unknown, and sweeping it in here would turn an
      // absence of data into an alert.
      result = result.filter(
        (t) =>
          isMeasuredRisk(t) || (t.budget_at_risk ?? 0) > 0 || (t.active_incidents ?? 0) > 0
      );
    }

    result.sort((a, b) => {
      switch (sortField) {
        case 'name':
          return a.name.localeCompare(b.name);
        case 'signalHealth':
          // Worst measured score first; unmeasured tenants sort last.
          return compareNullable(a.signal_health_score, b.signal_health_score, 'asc');
        case 'budgetAtRisk':
          return compareNullable(a.budget_at_risk, b.budget_at_risk, 'desc');
        case 'spend':
          return compareNullable(a.monthly_spend, b.monthly_spend, 'desc');
        case 'renewalDate':
          return compareNullable(
            parseDate(a.renewal_date)?.getTime() ?? null,
            parseDate(b.renewal_date)?.getTime() ?? null,
            'asc'
          );
        default:
          return 0;
      }
    });

    return result;
  }, [tenants, searchQuery, statusFilter, sortField, showAtRiskOnly]);

  // Portfolio stats. Each total counts only the tenants it could measure and
  // says how many it could not, rather than summing nulls as zeroes.
  const stats = useMemo(() => {
    const pricedBudgets = tenants.filter((t) => t.budget_at_risk !== null);
    const renewals = tenants.map((t) => parseDate(t.renewal_date));
    return {
      total: tenants.length,
      healthy: tenants.filter((t) => t.signal_health_status === 'healthy').length,
      atRisk: tenants.filter(isMeasuredRisk).length,
      critical: tenants.filter((t) => t.signal_health_status === 'critical').length,
      notMeasured: tenants.filter((t) => t.signal_health_status === 'insufficient_data')
        .length,
      budgetAtRisk: pricedBudgets.reduce((sum, t) => sum + (t.budget_at_risk ?? 0), 0),
      budgetUnpriced: tenants.length - pricedBudgets.length,
      mrr: tenants.reduce((sum, t) => sum + t.mrr, 0),
      upcomingRenewals: renewals.filter(
        (date) => date !== null && date.getTime() - Date.now() < RENEWAL_SOON_DAYS * DAY_MS
      ).length,
      unknownRenewals: renewals.filter((date) => date === null).length,
    };
  }, [tenants]);

  const criticalTenants = useMemo(
    () => tenants.filter((t) => t.signal_health_status === 'critical'),
    [tenants]
  );

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">My Portfolio</h1>
          <p className="text-text-muted">
            Manage your assigned tenants
            {spendWindowDays !== null && ` · spend and ROAS over the last ${spendWindowDays} days`}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            data-tour="export-pdf"
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-secondary border border-white/10 text-text-secondary hover:text-white transition-colors"
          >
            <DocumentArrowDownIcon className="w-4 h-4" />
            Export Summary
          </button>
        </div>
      </div>

      {isError && (
        <div className="rounded-xl bg-danger/5 border border-danger/20 p-4 text-danger">
          Could not load the portfolio. Nothing is shown rather than a stale or invented
          list; retry in a moment.
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-4">
        <div className="p-4 rounded-xl bg-surface-secondary border border-white/10">
          <div className="text-text-muted text-sm mb-1">Total Tenants</div>
          <div className="text-2xl font-bold text-white">{stats.total}</div>
        </div>
        <div className="p-4 rounded-xl bg-surface-secondary border border-white/10">
          <div className="text-text-muted text-sm mb-1">Healthy</div>
          <div className="text-2xl font-bold text-success">{stats.healthy}</div>
        </div>
        <div className="p-4 rounded-xl bg-surface-secondary border border-white/10">
          <div className="text-text-muted text-sm mb-1">At Risk</div>
          <div className="text-2xl font-bold text-warning">{stats.atRisk}</div>
        </div>
        <div className="p-4 rounded-xl bg-surface-secondary border border-white/10">
          <div className="text-text-muted text-sm mb-1">Critical</div>
          <div className="text-2xl font-bold text-danger">{stats.critical}</div>
        </div>
        <div className="p-4 rounded-xl bg-surface-secondary border border-white/10">
          <div className="text-text-muted text-sm mb-1">Not measured</div>
          <div className="text-2xl font-bold text-text-muted">{stats.notMeasured}</div>
        </div>
        <div className="p-4 rounded-xl bg-surface-secondary border border-white/10">
          <div className="text-text-muted text-sm mb-1">Budget at Risk</div>
          <div className="text-2xl font-bold text-danger">
            {stats.budgetUnpriced === stats.total && stats.total > 0
              ? DASH
              : formatMoney(stats.budgetAtRisk)}
          </div>
          {stats.budgetUnpriced > 0 && (
            <div className="text-xs text-text-muted mt-1">
              {stats.budgetUnpriced} not measured
            </div>
          )}
        </div>
        <div className="p-4 rounded-xl bg-surface-secondary border border-white/10">
          <div className="text-text-muted text-sm mb-1">Portfolio MRR</div>
          <div className="text-2xl font-bold text-white">{formatMoney(stats.mrr)}</div>
        </div>
        <div className="p-4 rounded-xl bg-surface-secondary border border-white/10">
          <div className="text-text-muted text-sm mb-1">
            Renewals ({RENEWAL_SOON_DAYS}d)
          </div>
          <div className="text-2xl font-bold text-warning">{stats.upcomingRenewals}</div>
          {stats.unknownRenewals > 0 && (
            <div className="text-xs text-text-muted mt-1">
              {stats.unknownRenewals} unknown
            </div>
          )}
        </div>
      </div>

      {/* Priority Alerts */}
      {criticalTenants.length > 0 && (
        <div
          data-tour="priority-alerts"
          className="rounded-xl bg-danger/5 border border-danger/20 p-4"
        >
          <div className="flex items-center gap-3 mb-3">
            <FireIcon className="w-5 h-5 text-danger" />
            <span className="font-semibold text-danger">Priority Alerts</span>
          </div>
          <div className="space-y-2">
            {criticalTenants.map((t) => (
              <div
                key={t.id}
                className="flex items-center justify-between p-3 rounded-lg bg-danger/10"
              >
                <div>
                  <span className="font-medium text-white">{t.name}</span>
                  <span className="text-sm text-text-muted ml-2">
                    Signal health {t.signal_health_score ?? DASH}
                    {t.active_incidents !== null &&
                      ` | ${t.active_incidents} incident${t.active_incidents === 1 ? '' : 's'} open`}
                    {t.incident_open_hours !== null && ` (${t.incident_open_hours}h)`}
                  </span>
                </div>
                <Link
                  to={`/dashboard/am/tenant/${t.id}`}
                  className="px-3 py-1 rounded-lg bg-danger/20 text-danger hover:bg-danger/30 text-sm transition-colors"
                >
                  View Now
                </Link>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-4">
        <div className="relative flex-1 min-w-[200px] max-w-md">
          <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-text-muted" />
          <input
            type="text"
            placeholder="Search tenants..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-10 pr-4 py-2 rounded-lg bg-surface-secondary border border-white/10 text-white placeholder-text-muted focus:outline-none focus:ring-2 focus:ring-stratum-500"
          />
        </div>

        <div className="flex items-center gap-2">
          <FunnelIcon className="w-4 h-4 text-text-muted" />
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as SignalHealthStatus | 'all')}
            className="px-3 py-2 rounded-lg bg-surface-secondary border border-white/10 text-white focus:outline-none focus:ring-2 focus:ring-stratum-500"
          >
            <option value="all">All Status</option>
            <option value="healthy">Healthy</option>
            <option value="degraded">Degraded</option>
            <option value="critical">Critical</option>
            <option value="insufficient_data">Not measured</option>
          </select>
        </div>

        <select
          value={sortField}
          onChange={(e) => setSortField(e.target.value as SortField)}
          className="px-3 py-2 rounded-lg bg-surface-secondary border border-white/10 text-white focus:outline-none focus:ring-2 focus:ring-stratum-500"
        >
          <option value="signalHealth">Sort by Signal Health</option>
          <option value="name">Sort by Name</option>
          <option value="budgetAtRisk">Sort by Budget at Risk</option>
          <option value="spend">Sort by Spend</option>
          <option value="renewalDate">Sort by Renewal</option>
        </select>

        <button
          onClick={() => setShowAtRiskOnly(!showAtRiskOnly)}
          className={cn(
            'flex items-center gap-2 px-4 py-2 rounded-lg transition-colors',
            showAtRiskOnly
              ? 'bg-warning/10 text-warning border border-warning/30'
              : 'bg-surface-secondary border border-white/10 text-text-muted hover:text-white'
          )}
        >
          <BellAlertIcon className="w-4 h-4" />
          At Risk Only
        </button>
      </div>

      {/* Tenant Cards */}
      <div data-tour="portfolio-list" className="grid gap-4">
        {filteredTenants.map((tenant) => (
          <div
            key={tenant.id}
            className={cn(
              'p-4 rounded-xl border transition-all hover:border-white/20',
              tenant.signal_health_status === 'critical'
                ? 'bg-danger/5 border-danger/20'
                : 'bg-surface-secondary border-white/10'
            )}
          >
            <div className="flex items-start gap-4">
              {/* Signal health - "not measured" is a dash, never a 0 */}
              <div className="flex flex-col items-center p-3 rounded-xl bg-surface-tertiary min-w-[92px]">
                {tenant.signal_health_score === null ? (
                  <>
                    <span className="text-3xl font-bold text-text-muted">{DASH}</span>
                    <span className="text-[10px] text-text-muted text-center mt-1">
                      Not measured
                    </span>
                  </>
                ) : (
                  <>
                    <span
                      className={cn(
                        'text-3xl font-bold',
                        tenant.signal_health_status === 'healthy'
                          ? 'text-success'
                          : tenant.signal_health_status === 'degraded'
                            ? 'text-warning'
                            : 'text-danger'
                      )}
                    >
                      {Math.round(tenant.signal_health_score)}
                    </span>
                    <ConfidenceBandBadge score={tenant.signal_health_score} size="sm" />
                  </>
                )}
                {tenant.emq_score !== null && (
                  <div className="flex items-center gap-1 text-xs text-text-muted mt-1">
                    <span>EMQ {Math.round(tenant.emq_score)}</span>
                    {tenant.emq_trend !== null && (
                      <span
                        className={cn(
                          'flex items-center gap-0.5',
                          tenant.emq_trend >= 0 ? 'text-success' : 'text-danger'
                        )}
                      >
                        {tenant.emq_trend >= 0 ? (
                          <ArrowTrendingUpIcon className="w-3 h-3" />
                        ) : (
                          <ArrowTrendingDownIcon className="w-3 h-3" />
                        )}
                        {formatSigned(tenant.emq_trend)}
                      </span>
                    )}
                  </div>
                )}
              </div>

              {/* Main Info */}
              <div className="flex-1 min-w-0">
                <div className="flex flex-wrap items-center gap-3 mb-2">
                  <h3 className="font-semibold text-white text-lg">{tenant.name}</h3>
                  <span
                    className={cn(
                      'px-2 py-0.5 rounded-full text-xs',
                      STATUS_COLORS[tenant.signal_health_status]
                    )}
                  >
                    {STATUS_LABELS[tenant.signal_health_status]}
                  </span>
                  {tenant.plan && (
                    <span className="px-2 py-0.5 rounded bg-surface-tertiary text-text-muted text-xs">
                      {tenant.plan}
                    </span>
                  )}
                </div>

                <div className="flex flex-wrap items-center gap-3 text-sm">
                  <span className="text-text-muted">{tenant.industry ?? 'Industry not set'}</span>

                  {/* The gate's own verdict. `gate_health_date` is null exactly
                      when it had no snapshot to grade, which is "no data" -
                      nothing is wrong, nothing is known yet. */}
                  {tenant.gate_decision && (
                    <span
                      title={tenant.gate_reason ?? undefined}
                      className={cn(
                        'inline-flex items-center px-2.5 py-1 rounded-lg border text-xs font-medium',
                        tenant.gate_health_date === null
                          ? 'text-text-muted bg-white/5 border-white/10'
                          : GATE_COLORS[tenant.gate_decision]
                      )}
                    >
                      {tenant.gate_health_date === null
                        ? 'Autopilot: no data'
                        : GATE_LABELS[tenant.gate_decision]}
                    </span>
                  )}

                  {(tenant.budget_at_risk ?? 0) > 0 && (
                    <BudgetAtRiskChip amount={tenant.budget_at_risk as number} size="sm" />
                  )}
                  {tenant.budget_at_risk === null && tenant.queued_actions > 0 && (
                    <span className="text-text-muted text-xs">
                      {tenant.queued_actions} action{tenant.queued_actions === 1 ? '' : 's'} held,
                      budget not measured
                    </span>
                  )}

                  {(tenant.active_incidents ?? 0) > 0 && (
                    <span className="flex items-center gap-1 text-warning">
                      <ExclamationTriangleIcon className="w-4 h-4" />
                      {tenant.active_incidents} incident
                      {tenant.active_incidents === 1 ? '' : 's'}
                      {tenant.incident_open_hours !== null && (
                        <span className="text-text-muted">({tenant.incident_open_hours}h)</span>
                      )}
                    </span>
                  )}
                </div>

                {/* What could not be measured, in the service's own words, so
                    the account manager can act on the gap instead of guessing
                    why the score is missing. */}
                {tenant.missing_inputs.length > 0 && (
                  <p className="mt-2 text-xs text-text-muted">
                    {tenant.missing_inputs.join(' ')}
                  </p>
                )}
              </div>

              {/* Metrics */}
              <div className="flex items-center gap-6 text-sm">
                <div className="text-right">
                  <div className="text-text-muted">ROAS</div>
                  <div className="flex items-center justify-end gap-1">
                    <span className="text-white font-medium">
                      {tenant.roas === null ? DASH : `${tenant.roas.toFixed(1)}x`}
                    </span>
                    {tenant.roas_trend !== null && (
                      <span
                        className={cn(
                          'text-xs',
                          tenant.roas_trend >= 0 ? 'text-success' : 'text-danger'
                        )}
                      >
                        {formatSigned(tenant.roas_trend, 1)}
                      </span>
                    )}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-text-muted">Spend</div>
                  <div className="text-white font-medium">
                    {formatCompactMoney(tenant.monthly_spend)}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-text-muted">Renewal</div>
                  <div
                    className={cn(
                      'font-medium',
                      (() => {
                        const renewal = parseDate(tenant.renewal_date);
                        return renewal &&
                          renewal.getTime() - Date.now() < RENEWAL_SOON_DAYS * DAY_MS
                          ? 'text-warning'
                          : 'text-white';
                      })()
                    )}
                  >
                    {formatDaysUntil(tenant.renewal_date)}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-text-muted">Last login</div>
                  <div className="text-white">{formatSince(tenant.last_login_at)}</div>
                </div>
              </div>

              {/* Action */}
              <Link
                to={`/dashboard/am/tenant/${tenant.id}`}
                className="flex items-center gap-1 px-4 py-2 rounded-lg bg-surface-tertiary text-text-secondary hover:text-white transition-colors"
              >
                View
                <ChevronRightIcon className="w-4 h-4" />
              </Link>
            </div>
          </div>
        ))}

        {isLoading && (
          <div className="text-center py-12 text-text-muted">Loading your portfolio...</div>
        )}

        {!isLoading && !isError && filteredTenants.length === 0 && (
          <div className="text-center py-12 text-text-muted">
            {tenants.length === 0
              ? 'No tenants are assigned to you.'
              : 'No tenants found matching your filters.'}
          </div>
        )}
      </div>
    </div>
  );
}
