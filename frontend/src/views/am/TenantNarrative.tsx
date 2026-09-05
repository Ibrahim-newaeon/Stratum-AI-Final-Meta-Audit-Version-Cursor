/**
 * Tenant Narrative (Account Manager Client Story View)
 *
 * Detailed view of a specific tenant for client communication
 * Shows "what changed", timeline, blocked actions, and fix playbook
 *
 * Every number on this page used to be a hardcoded literal used as a `??`
 * fallback, so a named customer was shown an invented primary contact
 * ("Jennifer Smith"), an invented last-contact and renewal date, an invented
 * EMQ of 65, an invented $12,000 budget at risk, an invented $120,000 spend /
 * $336,000 revenue / 2.80x ROAS / $42 CPA, an invented recovery strip
 * (+8pts EMQ, -15% ROAS, +18hrs MTTR, +5 blocked actions) and three invented
 * blocked actions - all of it presented beside the tenant's real name and
 * plan, and all of it exported verbatim into the client-facing report.
 *
 * The contract, matching `feat/real-signal-health` and Portfolio.tsx: a metric
 * with no source stays null and renders as a dash or "not measured". There is
 * no source at all for an account manager, a primary contact, a last-contact
 * date or a renewal date, so those are gone rather than filled.
 */

import { useCallback, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useToast } from '@/components/ui/use-toast';
import { cn } from '@/lib/utils';
import {
  type AutopilotMode,
  EmqFixPlaybookPanel,
  EmqScoreCard,
  EmqTimeline,
  type Kpi,
  KpiStrip,
  type PlaybookItem,
  type TimelineEvent,
  TrustStatusHeader,
} from '@/components/shared';
import {
  useAutopilotState,
  useEmqIncidents,
  useEmqPlaybook,
  useEmqScore,
  useTenant,
} from '@/api/hooks';
import {
  ArrowLeftIcon,
  DocumentArrowDownIcon,
  ShieldCheckIcon,
} from '@heroicons/react/24/outline';

const NOT_MEASURED = 'Not measured';

function formatCurrency(amount: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(amount);
}

export default function TenantNarrative() {
  const { tenantId } = useParams<{ tenantId: string }>();
  const tid = parseInt(tenantId || '1', 10);
  const { toast } = useToast();
  const [selectedPlaybookItem, setSelectedPlaybookItem] = useState<PlaybookItem | null>(null);

  const [activeTab, setActiveTab] = useState<'summary' | 'timeline' | 'blocked' | 'playbook'>(
    'summary'
  );

  // Date range
  const dateRange = {
    start: new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0],
    end: new Date().toISOString().split('T')[0],
  };

  // Fetch data
  const { data: tenantData } = useTenant(tid);
  const { data: emqData } = useEmqScore(tid);
  const { data: autopilotData } = useAutopilotState(tid);
  const { data: playbookData } = useEmqPlaybook(tid);
  const { data: incidentsData } = useEmqIncidents(tid, dateRange.start, dateRange.end);

  // Null means "not measured". A fallback score here would be graded by the
  // trust gate's own bands and rendered as this customer's data quality.
  const emqScore = emqData?.score ?? null;
  const previousEmqScore = emqData?.previousScore ?? null;
  const autopilotMode: AutopilotMode | null = autopilotData?.mode ?? null;
  const budgetAtRisk = autopilotData?.budgetAtRisk ?? null;

  // Tenant identity comes from the API. `industry` is deliberately absent:
  // it lives on `tenant_onboarding.industry` and no endpoint this view can
  // reach exposes it, so there is nothing to render rather than "Retail".
  const tenantName = tenantData?.name ?? null;
  const plan = tenantData?.plan ?? null;

  // The measured performance set for this tenant. There is no per-tenant
  // spend/revenue/ROAS/CPA endpoint wired to this view, so every value is null
  // and renders as a dash. A `0` would read as a real, catastrophic reading and
  // a `previousValue` would invent a trend arrow on top of it.
  const kpis: Kpi[] = useMemo(
    () => [
      {
        id: 'spend',
        label: 'Monthly Spend',
        value: null,
        format: 'currency',
        confidence: emqScore,
      },
      { id: 'revenue', label: 'Revenue', value: null, format: 'currency', confidence: emqScore },
      { id: 'roas', label: 'ROAS', value: null, format: 'multiplier', confidence: emqScore },
      { id: 'cpa', label: 'CPA', value: null, format: 'currency', confidence: emqScore },
    ],
    [emqScore]
  );

  const playbook: PlaybookItem[] = useMemo(() => playbookData ?? [], [playbookData]);

  const timeline: TimelineEvent[] =
    incidentsData?.map((i) => ({
      id: i.id,
      type: i.type,
      title: i.title,
      description: i.description ?? undefined,
      timestamp: new Date(i.timestamp),
      platform: i.platform ?? undefined,
      severity: i.severity,
      recoveryHours: i.recoveryHours ?? undefined,
      emqImpact: i.emqImpact ?? undefined,
    })) ?? [];

  const tabs = [
    { id: 'summary' as const, label: 'Client Summary' },
    { id: 'timeline' as const, label: 'What Changed' },
    { id: 'blocked' as const, label: 'What We Blocked' },
    { id: 'playbook' as const, label: 'Fix Playbook' },
  ];

  const handleExportPDF = useCallback(() => {
    const lines: string[] = [];
    lines.push('CLIENT NARRATIVE REPORT');
    lines.push(`Generated: ${new Date().toLocaleDateString()}`);
    lines.push('');
    lines.push(`Client: ${tenantName ?? 'Unknown'}`);
    lines.push(`Plan: ${plan ?? NOT_MEASURED}`);
    lines.push('');

    lines.push('--- TRUST STATUS ---');
    lines.push(`EMQ Score: ${emqScore === null ? NOT_MEASURED : `${emqScore}/100`}`);
    lines.push(`Autopilot Mode: ${autopilotMode ?? NOT_MEASURED}`);
    lines.push(
      `Budget at Risk: ${budgetAtRisk === null ? NOT_MEASURED : formatCurrency(budgetAtRisk)}`
    );
    lines.push('');

    lines.push('--- KEY PERFORMANCE INDICATORS ---');
    kpis.forEach((kpi) => {
      lines.push(`${kpi.label}: ${NOT_MEASURED}`);
    });
    lines.push('');

    lines.push('--- BLOCKED ACTIONS ---');
    lines.push(NOT_MEASURED);
    lines.push('');

    lines.push('--- FIX PLAYBOOK ---');
    if (playbook.length === 0) {
      lines.push('No fixes have been derived for this tenant.');
    }
    playbook.forEach((item) => {
      lines.push(`[${item.priority.toUpperCase()}] ${item.title} - ${item.status}`);
      lines.push(`  ${item.description}`);
      lines.push(
        `  Owner: ${item.owner || 'Unassigned'} | Est. Impact: +${item.estimatedImpact} EMQ pts | Time: ${item.estimatedTime}`
      );
    });

    const blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `narrative-${(tenantName ?? 'tenant').replace(/\s+/g, '-').toLowerCase()}-${new Date().toISOString().split('T')[0]}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [tenantName, plan, emqScore, autopilotMode, budgetAtRisk, kpis, playbook]);

  return (
    <div data-tour="tenant-narrative" className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link
            to="/dashboard/am/portfolio"
            className="p-2 rounded-lg bg-surface-secondary border border-white/10 text-text-muted hover:text-white transition-colors"
          >
            <ArrowLeftIcon className="w-5 h-5" />
          </Link>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-white">{tenantName ?? 'Unknown tenant'}</h1>
              {plan && (
                <span className="px-2 py-1 rounded-full text-xs bg-stratum-500/10 text-stratum-400">
                  {plan}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button
            data-tour="export-pdf"
            onClick={handleExportPDF}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-gradient-stratum text-white font-medium hover:shadow-glow transition-all"
          >
            <DocumentArrowDownIcon className="w-4 h-4" />
            Export PDF
          </button>
        </div>
      </div>

      {/* Trust Status */}
      <TrustStatusHeader
        emqScore={emqScore}
        autopilotMode={autopilotMode}
        budgetAtRisk={budgetAtRisk}
        onViewDetails={() => setActiveTab('timeline')}
      />

      {/* KPI Strip */}
      <KpiStrip kpis={kpis} emqScore={emqScore} />

      {/* Tabs */}
      <div className="flex items-center gap-2 border-b border-white/10 pb-4">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              'px-4 py-2 rounded-lg transition-colors',
              activeTab === tab.id
                ? 'bg-stratum-500/10 text-stratum-400'
                : 'text-text-muted hover:text-white hover:bg-white/5'
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'summary' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            <EmqScoreCard
              score={emqScore}
              previousScore={previousEmqScore}
              drivers={emqData?.drivers}
              showDrivers
            />
          </div>
          <div>
            <EmqTimeline events={timeline} maxEvents={6} />
          </div>
        </div>
      )}

      {activeTab === 'timeline' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-white">What Changed (Last 30 Days)</h2>
          </div>
          <EmqTimeline events={timeline} maxEvents={20} />
        </div>
      )}

      {activeTab === 'blocked' && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold text-white">What We Blocked</h2>
              <p className="text-sm text-text-muted">
                Actions blocked to protect performance during signal degradation
              </p>
            </div>
            {budgetAtRisk !== null && (
              <div className="flex items-center gap-2 px-4 py-2 rounded-xl bg-success/10 border border-success/20">
                <ShieldCheckIcon className="w-5 h-5 text-success" />
                <span className="text-success font-medium">{formatCurrency(budgetAtRisk)}</span>
                <span className="text-success/80">protected</span>
              </div>
            )}
          </div>

          {/* There is no endpoint returning the individual actions the gate
              held for a tenant, only the autopilot mode's restricted action
              *types*. Listing invented campaign names here is what this view
              used to do. */}
          <div className="p-4 rounded-xl bg-surface-tertiary border border-white/5 text-center">
            <p className="text-text-muted">
              Blocked actions are not measured for this tenant yet.
            </p>
          </div>
        </div>
      )}

      {activeTab === 'playbook' && (
        <div data-tour="fix-playbook" className="space-y-4">
          <EmqFixPlaybookPanel
            items={playbook}
            onItemClick={(item) => setSelectedPlaybookItem(item)}
            onAssign={(item) => {
              toast({
                title: 'Assignment requested',
                description: `"${item.title}" has been flagged for assignment. The ${item.owner || 'team'} will be notified.`,
              });
            }}
            maxItems={10}
          />

          {/* Playbook Item Detail Panel */}
          {selectedPlaybookItem && (
            <div className="p-5 rounded-xl bg-surface-secondary border border-white/10">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-lg font-semibold text-white">{selectedPlaybookItem.title}</h3>
                <button
                  onClick={() => setSelectedPlaybookItem(null)}
                  className="text-text-muted hover:text-white transition-colors text-sm"
                >
                  Close
                </button>
              </div>
              <p className="text-text-secondary mb-4">{selectedPlaybookItem.description}</p>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                <div>
                  <span className="text-text-muted">Priority</span>
                  <p
                    className={cn(
                      'font-medium capitalize',
                      selectedPlaybookItem.priority === 'critical' && 'text-danger',
                      selectedPlaybookItem.priority === 'high' && 'text-warning',
                      selectedPlaybookItem.priority === 'medium' && 'text-stratum-400'
                    )}
                  >
                    {selectedPlaybookItem.priority}
                  </p>
                </div>
                <div>
                  <span className="text-text-muted">Owner</span>
                  <p className="text-white font-medium">
                    {selectedPlaybookItem.owner || 'Unassigned'}
                  </p>
                </div>
                <div>
                  <span className="text-text-muted">Est. Impact</span>
                  <p className="text-success font-medium">
                    +{selectedPlaybookItem.estimatedImpact} EMQ pts
                  </p>
                </div>
                <div>
                  <span className="text-text-muted">Est. Time</span>
                  <p className="text-white font-medium">
                    {selectedPlaybookItem.estimatedTime ?? NOT_MEASURED}
                  </p>
                </div>
              </div>
              {selectedPlaybookItem.platform && (
                <div className="mt-3 text-sm">
                  <span className="text-text-muted">Platform: </span>
                  <span className="text-white">{selectedPlaybookItem.platform}</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
