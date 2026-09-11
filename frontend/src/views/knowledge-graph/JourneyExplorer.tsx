/**
 * Journey Explorer — attribution-backed conversion paths and contact journeys.
 * Read-only. No Meta writes.
 */

import { useMemo, useState } from 'react';
import { GitBranch, Loader2, Route, Search } from 'lucide-react';
import {
  useChannelTransitions,
  useContactJourney,
  useTopConversionPaths,
} from '@/api/attribution';

function defaultDateRange() {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - 30);
  return {
    startDate: start.toISOString().slice(0, 10),
    endDate: end.toISOString().slice(0, 10),
  };
}

export default function JourneyExplorer() {
  const initial = useMemo(() => defaultDateRange(), []);
  const [startDate, setStartDate] = useState(initial.startDate);
  const [endDate, setEndDate] = useState(initial.endDate);
  const [contactId, setContactId] = useState('');
  const [lookupId, setLookupId] = useState('');

  const pathsQuery = useTopConversionPaths({ startDate, endDate, limit: 20 });
  const transitionsQuery = useChannelTransitions({ startDate, endDate });
  const journeyQuery = useContactJourney(lookupId);

  const paths = (pathsQuery.data as any)?.data ?? pathsQuery.data ?? [];
  const transitionsPayload = (transitionsQuery.data as any)?.data ?? transitionsQuery.data;
  const transitions = transitionsPayload?.sankeyData || transitionsPayload || {};

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Route className="w-7 h-7 text-primary" />
          Journey Explorer
        </h1>
        <p className="text-muted-foreground mt-1">
          Conversion paths and contact journeys from attribution data. Read-only —
          no Meta writes.
        </p>
      </div>

      <div className="flex flex-wrap gap-3 items-end">
        <label className="text-sm">
          <span className="block text-muted-foreground mb-1">Start</span>
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="px-3 py-2 rounded-lg border bg-background"
          />
        </label>
        <label className="text-sm">
          <span className="block text-muted-foreground mb-1">End</span>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="px-3 py-2 rounded-lg border bg-background"
          />
        </label>
      </div>

      <section className="rounded-xl border bg-card p-4 space-y-3">
        <h2 className="font-semibold flex items-center gap-2">
          <GitBranch className="w-4 h-4" /> Top conversion paths
        </h2>
        {pathsQuery.isLoading && (
          <div className="flex items-center gap-2 text-muted-foreground text-sm">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading paths…
          </div>
        )}
        {pathsQuery.isError && (
          <p className="text-sm text-destructive">
            Could not load conversion paths. Attribution data may be empty for this
            range.
          </p>
        )}
        {!pathsQuery.isLoading && Array.isArray(paths) && paths.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No conversion paths in this date range yet.
          </p>
        )}
        <div className="space-y-2">
          {(Array.isArray(paths) ? paths : []).map((row: any, idx: number) => (
            <div
              key={idx}
              className="flex flex-col md:flex-row md:items-center justify-between gap-2 rounded-lg border p-3 text-sm"
            >
              <code className="text-xs break-all">{row.path || row.journey_path || '—'}</code>
              <div className="flex gap-4 text-muted-foreground">
                <span>{row.conversions ?? row.conversion_count ?? 0} conversions</span>
                <span>
                  {Number(row.total_revenue ?? row.revenue ?? 0).toLocaleString()} revenue
                </span>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-xl border bg-card p-4 space-y-3">
        <h2 className="font-semibold">Channel transitions</h2>
        {transitionsQuery.isLoading && (
          <div className="flex items-center gap-2 text-muted-foreground text-sm">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading transitions…
          </div>
        )}
        {transitionsQuery.isError && (
          <p className="text-sm text-destructive">Could not load channel transitions.</p>
        )}
        {!transitionsQuery.isLoading && (
          <p className="text-sm text-muted-foreground">
            {transitions?.total_transitions ?? transitions?.totalTransitions ?? 0}{' '}
            transitions across{' '}
            {transitions?.unique_paths ??
              transitions?.uniquePaths ??
              (transitions?.links || []).length}{' '}
            path edges.
          </p>
        )}
        <div className="space-y-1 max-h-64 overflow-auto">
          {(transitions?.links || []).slice(0, 25).map((link: any, idx: number) => (
            <div key={idx} className="text-xs text-muted-foreground">
              {link.source || link.from} → {link.target || link.to} (
              {link.value || link.transitions || 0})
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-xl border bg-card p-4 space-y-3">
        <h2 className="font-semibold flex items-center gap-2">
          <Search className="w-4 h-4" /> Contact journey lookup
        </h2>
        <div className="flex gap-2">
          <input
            value={contactId}
            onChange={(e) => setContactId(e.target.value)}
            placeholder="CRM contact ID"
            className="flex-1 px-3 py-2 rounded-lg border bg-background"
          />
          <button
            type="button"
            onClick={() => setLookupId(contactId.trim())}
            className="px-4 py-2 rounded-lg bg-primary text-primary-foreground"
          >
            Look up
          </button>
        </div>
        {journeyQuery.isFetching && (
          <div className="flex items-center gap-2 text-muted-foreground text-sm">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading journey…
          </div>
        )}
        {journeyQuery.isError && (
          <p className="text-sm text-destructive">Contact journey not found.</p>
        )}
        {journeyQuery.data && (
          <pre className="text-xs overflow-auto rounded-lg border bg-muted/30 p-3 max-h-80">
            {JSON.stringify((journeyQuery.data as any)?.data ?? journeyQuery.data, null, 2)}
          </pre>
        )}
      </section>
    </div>
  );
}
