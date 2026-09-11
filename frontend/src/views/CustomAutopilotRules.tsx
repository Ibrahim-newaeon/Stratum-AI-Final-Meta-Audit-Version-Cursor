/**
 * Custom Autopilot Rules
 *
 * Tenant-defined if/then rules that enqueue SAFE Autopilot actions.
 * Meta writes remain on the Autopilot executor (disabled by default).
 */

import { useMemo, useState } from 'react';
import {
  CheckCircle2,
  Clock,
  Edit,
  Loader2,
  Pause,
  Play,
  Plus,
  Search,
  Shield,
  Trash2,
  Zap,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { CustomAutopilotRulesBuilder } from '@/components/autopilot/CustomAutopilotRulesBuilder';
import {
  CustomAutopilotRule,
  useActivateCustomAutopilotRule,
  useCreateCustomAutopilotRule,
  useCustomAutopilotRules,
  useDeleteCustomAutopilotRule,
  usePauseCustomAutopilotRule,
  useUpdateCustomAutopilotRule,
} from '@/api/customAutopilotRules';

function mapBuilderToApi(rule: any) {
  const conditions =
    rule.conditionGroups?.flatMap((g: any) =>
      (g.conditions || []).map((c: any) => ({
        field: c.field,
        operator: c.operator,
        value: Number(c.value),
      }))
    ) ||
    rule.conditions ||
    [];

  const actions = (rule.actions || []).map((a: any) => ({
    type: a.type,
    config: {
      ...a.config,
      percentage:
        a.config?.percentage != null
          ? Number(a.config.percentage)
          : a.config?.amount != null
            ? Number(a.config.amount)
            : undefined,
    },
  }));

  return {
    name: rule.name,
    description: rule.description || undefined,
    status: (rule.status || 'draft') as 'draft' | 'active' | 'paused',
    conditions,
    actions,
    require_approval: rule.trustGate?.requireApproval ?? true,
    cooldown_hours: Number(rule.cooldownHours ?? 24),
    max_executions_per_day: Number(rule.maxExecutionsPerDay ?? 10),
  };
}

function mapApiToBuilder(rule: CustomAutopilotRule) {
  return {
    id: rule.id,
    name: rule.name,
    description: rule.description || '',
    status: rule.status,
    conditionGroups: [
      {
        id: crypto.randomUUID(),
        logic: 'AND' as const,
        conditions: rule.conditions.map((c) => ({
          id: crypto.randomUUID(),
          field: c.field,
          operator: c.operator,
          value: String(c.value),
          valueType: 'number' as const,
        })),
      },
    ],
    conditionLogic: 'AND' as const,
    actions: rule.actions.map((a, idx) => ({
      id: crypto.randomUUID(),
      type: a.type,
      config: a.config || {},
      priority: idx + 1,
    })),
    targeting: { platforms: [], campaignTypes: [], specificCampaigns: [] },
    schedule: { enabled: false, frequency: 'hourly' as const, timezone: 'UTC' },
    trustGate: {
      enabled: true,
      minSignalHealth: 70,
      requireApproval: rule.require_approval,
      dryRunFirst: false,
    },
    cooldownHours: rule.cooldown_hours,
    maxExecutionsPerDay: rule.max_executions_per_day,
  };
}

export default function CustomAutopilotRules() {
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<string>('all');
  const [showBuilder, setShowBuilder] = useState(false);
  const [editingRule, setEditingRule] = useState<CustomAutopilotRule | null>(null);

  const { data, isLoading, isError, error, refetch } = useCustomAutopilotRules({
    page: 1,
    page_size: 100,
  });
  const createMutation = useCreateCustomAutopilotRule();
  const updateMutation = useUpdateCustomAutopilotRule();
  const deleteMutation = useDeleteCustomAutopilotRule();
  const activateMutation = useActivateCustomAutopilotRule();
  const pauseMutation = usePauseCustomAutopilotRule();

  const rules = data?.items ?? [];
  const filteredRules = useMemo(
    () =>
      rules.filter((rule) => {
        if (searchQuery && !rule.name.toLowerCase().includes(searchQuery.toLowerCase())) {
          return false;
        }
        if (statusFilter !== 'all' && rule.status !== statusFilter) {
          return false;
        }
        return true;
      }),
    [rules, searchQuery, statusFilter]
  );

  const isSaving =
    createMutation.isPending ||
    updateMutation.isPending ||
    deleteMutation.isPending ||
    activateMutation.isPending ||
    pauseMutation.isPending;

  const handleSaveRule = async (builderRule: any) => {
    const payload = mapBuilderToApi(builderRule);
    if (editingRule) {
      await updateMutation.mutateAsync({ id: editingRule.id, data: payload });
    } else {
      await createMutation.mutateAsync(payload);
    }
    setShowBuilder(false);
    setEditingRule(null);
  };

  const handleToggleRule = async (rule: CustomAutopilotRule) => {
    if (rule.status === 'active') {
      await pauseMutation.mutateAsync(rule.id);
    } else {
      await activateMutation.mutateAsync(rule.id);
    }
  };

  const handleDeleteRule = async (ruleId: string) => {
    if (confirm('Delete this Custom Autopilot rule?')) {
      await deleteMutation.mutateAsync(ruleId);
    }
  };

  const getStatusBadge = (status: string) => {
    const config = {
      active: { color: 'bg-green-500/10 text-green-500', icon: CheckCircle2, label: 'Active' },
      paused: { color: 'bg-amber-500/10 text-amber-500', icon: Pause, label: 'Paused' },
      draft: { color: 'bg-gray-500/10 text-gray-500', icon: Edit, label: 'Draft' },
    };
    const { color, icon: Icon, label } = config[status as keyof typeof config] || config.draft;
    return (
      <span className={cn('px-2 py-1 rounded-full text-xs font-medium inline-flex items-center gap-1', color)}>
        <Icon className="w-3 h-3" />
        {label}
      </span>
    );
  };

  if (showBuilder || editingRule) {
    return (
      <div className="p-6">
        <CustomAutopilotRulesBuilder
          rule={editingRule ? mapApiToBuilder(editingRule) : undefined}
          onSave={handleSaveRule}
          onCancel={() => {
            setShowBuilder(false);
            setEditingRule(null);
          }}
          isLoading={isSaving}
        />
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Zap className="w-7 h-7 text-primary" />
            Custom Autopilot Rules
          </h1>
          <p className="text-muted-foreground mt-1">
            Create SAFE Autopilot queue rules. Trust Gate still decides whether actions enqueue.
            Meta writes stay off until Autopilot execution is deliberately enabled.
          </p>
        </div>
        <button
          onClick={() => setShowBuilder(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90"
        >
          <Plus className="w-4 h-4" />
          Create Rule
        </button>
      </div>

      <div className="flex items-start gap-3 rounded-lg border border-primary/20 bg-primary/5 p-4 text-sm">
        <Shield className="w-5 h-5 text-primary shrink-0 mt-0.5" />
        <div>
          <p className="font-medium">Queue bridge only</p>
          <p className="text-muted-foreground">
            Matching rules enqueue <code>budget_decrease</code>, <code>bid_decrease</code>, or{' '}
            <code>pause_adset</code> into the Autopilot queue. They never call Meta write clients
            directly.
          </p>
        </div>
      </div>

      <div className="flex flex-col md:flex-row gap-3">
        <div className="relative flex-1">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search rules..."
            className="w-full pl-9 pr-3 py-2 rounded-lg border bg-background"
          />
        </div>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="px-3 py-2 rounded-lg border bg-background"
        >
          <option value="all">All statuses</option>
          <option value="active">Active</option>
          <option value="paused">Paused</option>
          <option value="draft">Draft</option>
        </select>
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading rules...
        </div>
      )}

      {isError && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm">
          Failed to load rules: {(error as Error)?.message || 'Unknown error'}
          <button className="ml-3 underline" onClick={() => refetch()}>
            Retry
          </button>
        </div>
      )}

      {!isLoading && !isError && filteredRules.length === 0 && (
        <div className="rounded-xl border border-dashed p-10 text-center text-muted-foreground">
          No Custom Autopilot rules yet. Create one to enqueue SAFE Autopilot actions.
        </div>
      )}

      <div className="space-y-3">
        {filteredRules.map((rule) => (
          <div key={rule.id} className="rounded-xl border bg-card p-4 flex flex-col gap-3">
            <div className="flex flex-col md:flex-row md:items-start justify-between gap-3">
              <div>
                <div className="flex items-center gap-2 flex-wrap">
                  <h2 className="font-semibold">{rule.name}</h2>
                  {getStatusBadge(rule.status)}
                </div>
                {rule.description && (
                  <p className="text-sm text-muted-foreground mt-1">{rule.description}</p>
                )}
                <div className="flex flex-wrap gap-3 mt-2 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-1">
                    <Clock className="w-3 h-3" /> {rule.cooldown_hours}h cooldown
                  </span>
                  <span>Max {rule.max_executions_per_day}/day</span>
                  <span>Triggered {rule.trigger_count}×</span>
                  <span>
                    Approval: {rule.require_approval ? 'required' : 'auto-approve on PASS'}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => handleToggleRule(rule)}
                  className="p-2 rounded-lg border hover:bg-muted"
                  title={rule.status === 'active' ? 'Pause' : 'Activate'}
                >
                  {rule.status === 'active' ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
                </button>
                <button
                  onClick={() => setEditingRule(rule)}
                  className="p-2 rounded-lg border hover:bg-muted"
                  title="Edit"
                >
                  <Edit className="w-4 h-4" />
                </button>
                <button
                  onClick={() => handleDeleteRule(rule.id)}
                  className="p-2 rounded-lg border hover:bg-muted text-destructive"
                  title="Delete"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
            <div className="text-xs text-muted-foreground">
              When{' '}
              {rule.conditions
                .map((c) => `${c.field} ${c.operator} ${c.value}`)
                .join(' AND ')}{' '}
              → {rule.actions.map((a) => a.type).join(', ')}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
