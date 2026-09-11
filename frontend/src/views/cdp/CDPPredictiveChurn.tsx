/**
 * Predictive Churn — heuristic risk list from CDP profile recency/lifecycle.
 * Honest empty states. No Meta writes. Not a trained ML model.
 */

import { useMemo, useState } from 'react';
import {
  ArrowPathIcon,
  ExclamationTriangleIcon,
  UserGroupIcon,
  CurrencyDollarIcon,
} from '@heroicons/react/24/outline';
import { cn } from '@/lib/utils';
import { useChurnRisks } from '@/api/cdp';

export default function CDPPredictiveChurn() {
  const [riskFilter, setRiskFilter] = useState<'all' | 'high' | 'medium' | 'low'>('all');
  const [minProbability, setMinProbability] = useState(0);

  const { data, isLoading, isError, refetch, isFetching } = useChurnRisks({
    min_probability: minProbability,
    limit: 100,
  });

  const items = useMemo(() => {
    const rows = data?.items || [];
    if (riskFilter === 'all') return rows;
    return rows.filter((r) => r.risk_level === riskFilter);
  }, [data, riskFilter]);

  return (
    <div className="p-6 space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Predictive Churn</h1>
          <p className="text-muted-foreground mt-1 max-w-2xl">
            Heuristic risk scoring from CDP inactivity and lifecycle stage
            ({data?.scoring_method || 'heuristic_v1'}). This is not a trained ML model and does
            not write to Meta.
          </p>
        </div>
        <button
          onClick={() => void refetch()}
          className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border hover:bg-muted"
        >
          <ArrowPathIcon className={cn('w-4 h-4', isFetching && 'animate-spin')} />
          Refresh
        </button>
      </div>

      {isError && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          Could not load churn risks. Confirm CDP profiles exist and the API is reachable.
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="rounded-xl border bg-card p-4">
          <div className="text-sm text-muted-foreground flex items-center gap-2">
            <UserGroupIcon className="w-4 h-4" /> Profiles scored
          </div>
          <div className="text-2xl font-bold mt-1">{data?.total ?? 0}</div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="text-sm text-muted-foreground flex items-center gap-2">
            <ExclamationTriangleIcon className="w-4 h-4 text-red-500" /> High risk
          </div>
          <div className="text-2xl font-bold mt-1">{data?.high_risk ?? 0}</div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="text-sm text-muted-foreground">Medium / Low</div>
          <div className="text-2xl font-bold mt-1">
            {(data?.medium_risk ?? 0) + (data?.low_risk ?? 0)}
          </div>
        </div>
        <div className="rounded-xl border bg-card p-4">
          <div className="text-sm text-muted-foreground flex items-center gap-2">
            <CurrencyDollarIcon className="w-4 h-4" /> Revenue at risk
          </div>
          <div className="text-2xl font-bold mt-1">
            {Number(data?.revenue_at_risk ?? 0).toLocaleString(undefined, {
              maximumFractionDigits: 0,
            })}
          </div>
        </div>
      </div>

      <div className="flex flex-wrap gap-3 items-end">
        <label className="text-sm">
          <span className="block text-muted-foreground mb-1">Risk</span>
          <select
            value={riskFilter}
            onChange={(e) => setRiskFilter(e.target.value as typeof riskFilter)}
            className="px-3 py-2 rounded-lg border bg-background"
          >
            <option value="all">All</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
        <label className="text-sm">
          <span className="block text-muted-foreground mb-1">Min probability</span>
          <select
            value={minProbability}
            onChange={(e) => setMinProbability(Number(e.target.value))}
            className="px-3 py-2 rounded-lg border bg-background"
          >
            <option value={0}>0%</option>
            <option value={0.4}>40%</option>
            <option value={0.7}>70%</option>
          </select>
        </label>
      </div>

      <div className="rounded-xl border bg-card overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            <tr>
              <th className="px-4 py-3 text-left font-medium">Profile</th>
              <th className="px-4 py-3 text-left font-medium">Risk</th>
              <th className="px-4 py-3 text-left font-medium">Probability</th>
              <th className="px-4 py-3 text-left font-medium">Inactive</th>
              <th className="px-4 py-3 text-left font-medium">Lifecycle</th>
              <th className="px-4 py-3 text-left font-medium">Revenue at risk</th>
              <th className="px-4 py-3 text-left font-medium">Top factors</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {isLoading ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-muted-foreground">
                  Loading churn risks…
                </td>
              </tr>
            ) : items.length === 0 ? (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-muted-foreground">
                  No profiles to score yet. Ingest CDP profiles (and activity) to populate this
                  list.
                </td>
              </tr>
            ) : (
              items.map((row) => (
                <tr key={row.profile_id} className="hover:bg-muted/40">
                  <td className="px-4 py-3">
                    <div className="font-medium">{row.email || 'Anonymous'}</div>
                    <div className="text-xs text-muted-foreground font-mono">{row.profile_id}</div>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={cn(
                        'px-2 py-0.5 rounded-full text-xs capitalize',
                        row.risk_level === 'high' && 'bg-red-500/10 text-red-600',
                        row.risk_level === 'medium' && 'bg-amber-500/10 text-amber-600',
                        row.risk_level === 'low' && 'bg-green-500/10 text-green-600'
                      )}
                    >
                      {row.risk_level}
                    </span>
                  </td>
                  <td className="px-4 py-3">{(row.churn_probability * 100).toFixed(0)}%</td>
                  <td className="px-4 py-3">{row.days_inactive}d</td>
                  <td className="px-4 py-3 capitalize">{row.lifecycle_stage || '—'}</td>
                  <td className="px-4 py-3">
                    {Number(row.revenue_at_risk || 0).toLocaleString(undefined, {
                      maximumFractionDigits: 0,
                    })}
                  </td>
                  <td className="px-4 py-3 text-xs text-muted-foreground">
                    {(row.top_factors || [])
                      .slice(0, 3)
                      .map((f) => f.name || 'factor')
                      .join(', ') || '—'}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
