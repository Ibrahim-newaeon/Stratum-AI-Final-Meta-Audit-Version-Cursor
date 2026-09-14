import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useToast } from '@/components/ui/use-toast';
import {
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Loader2,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  TrendingDown,
  TrendingUp,
} from 'lucide-react';
import {
  cn,
  formatCompactNumber,
  formatCurrency,
  formatPercent,
  getPlatformColor,
} from '@/lib/utils';
import CampaignCreateModal from '@/components/campaigns/CampaignCreateModal';
import {
  useActivateCampaign,
  useCampaigns,
  useDeleteCampaign,
  useDiscoverCampaigns,
  usePauseCampaign,
} from '@/api/hooks';
import { useAdAccounts } from '@/api/campaignBuilder';
import { useTenantStore } from '@/stores/tenantStore';

interface Campaign {
  id: number;
  name: string;
  platform: string;
  accountId: string;
  status: 'active' | 'paused' | 'completed' | 'draft';
  spend: number;
  budget: number;
  revenue: number;
  roas: number;
  impressions: number;
  clicks: number;
  conversions: number;
  ctr: number;
  trend: 'up' | 'down' | 'stable';
  externalUrl?: string | null;
}

type SortField = 'name' | 'spend' | 'revenue' | 'roas' | 'conversions';
type SortDirection = 'asc' | 'desc';

const PAGE_SIZE = 20;

function centsToMajor(value: unknown): number {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return n / 100;
}

export function Campaigns() {
  const { t } = useTranslation();
  const { toast } = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [searchQuery, setSearchQuery] = useState(searchParams.get('search') || '');
  const [statusFilter, setStatusFilter] = useState<string>(searchParams.get('status') || 'all');
  const [platformFilter, setPlatformFilter] = useState<string>(
    searchParams.get('platform') || 'all'
  );
  const [accountFilter, setAccountFilter] = useState<string>(
    searchParams.get('account_id') || 'all'
  );
  const [page, setPage] = useState(Number(searchParams.get('page') || '1') || 1);
  const [sortField, setSortField] = useState<SortField>('spend');
  const [sortDirection, setSortDirection] = useState<SortDirection>('desc');
  const [selectedCampaigns, setSelectedCampaigns] = useState<number[]>([]);
  const [createModalOpen, setCreateModalOpen] = useState(false);
  const handledQueryRef = useRef<string | null>(null);

  const tenantId = useTenantStore((state) => state.tenantId);

  const campaignFilters = useMemo(
    () => ({
      page,
      page_size: PAGE_SIZE,
      search: searchQuery.trim() || undefined,
      status:
        statusFilter !== 'all'
          ? (statusFilter as 'active' | 'paused' | 'completed' | 'draft')
          : undefined,
      platform: platformFilter !== 'all' ? (platformFilter as 'meta') : undefined,
      account_id: accountFilter !== 'all' ? accountFilter : undefined,
    }),
    [page, searchQuery, statusFilter, platformFilter, accountFilter]
  );

  const { data: campaignsData, isLoading, refetch } = useCampaigns(campaignFilters);
  const pauseCampaign = usePauseCampaign();
  const activateCampaign = useActivateCampaign();
  const deleteCampaign = useDeleteCampaign();
  const discoverCampaigns = useDiscoverCampaigns();
  const { data: adAccounts = [] } = useAdAccounts(tenantId ?? 0, 'meta', true);

  useEffect(() => {
    const next = new URLSearchParams(searchParams);
    const sync = (key: string, value: string, blank: string) => {
      if (!value || value === blank) next.delete(key);
      else next.set(key, value);
    };
    sync('status', statusFilter, 'all');
    sync('platform', platformFilter, 'all');
    sync('account_id', accountFilter, 'all');
    sync('search', searchQuery.trim(), '');
    if (page > 1) next.set('page', String(page));
    else next.delete('page');
    setSearchParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sync filter state only
  }, [statusFilter, platformFilter, accountFilter, searchQuery, page]);

  useEffect(() => {
    const discover = searchParams.get('discover');
    const create = searchParams.get('create');
    if (!discover && !create) return;

    const key = searchParams.toString();
    if (handledQueryRef.current === key) return;
    handledQueryRef.current = key;

    if (create === '1') setCreateModalOpen(true);
    if (discover === '1') {
      discoverCampaigns.mutate(undefined, {
        onSuccess: () => {
          toast({
            title: 'Discovery queued',
            description: 'Campaign list refreshes when Meta sync finishes.',
          });
        },
        onError: (err) => {
          toast({
            title: 'Discovery failed',
            description: err instanceof Error ? err.message : 'Could not start discovery',
            variant: 'destructive',
          });
        },
      });
    }

    const next = new URLSearchParams(searchParams);
    next.delete('discover');
    next.delete('create');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams, discoverCampaigns, toast]);

  const totalCampaigns = campaignsData?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(totalCampaigns / PAGE_SIZE));

  const campaigns = useMemo((): Campaign[] => {
    const items = campaignsData?.items ?? [];
    return items.map((c: any) => {
      const spend = c.spend ?? centsToMajor(c.total_spend_cents ?? c.spend_cents ?? 0);
      const revenue = c.revenue ?? centsToMajor(c.revenue_cents ?? 0);
      const budget = c.budget ?? centsToMajor(c.daily_budget_cents ?? c.daily_budget ?? 0);
      const roas = c.roas ?? (spend > 0 ? revenue / spend : 0);
      const impressions = c.impressions || 0;
      const clicks = c.clicks || 0;
      const rawStatus = (c.status || 'active').toLowerCase();
      const status = (
        ['active', 'paused', 'completed', 'draft'].includes(rawStatus) ? rawStatus : 'draft'
      ) as Campaign['status'];
      return {
        id: Number(c.id) || Number(c.campaign_id) || 0,
        name: c.name || c.campaign_name || '',
        platform: (c.platform || 'meta').toLowerCase(),
        accountId: String(c.account_id || c.accountId || ''),
        status,
        spend,
        budget,
        revenue,
        roas,
        impressions,
        clicks,
        conversions: c.conversions || 0,
        ctr: c.ctr || (impressions > 0 ? (clicks / impressions) * 100 : 0),
        trend: c.trend || (roas >= 3.5 ? 'up' : roas < 2.5 ? 'down' : 'stable'),
        externalUrl: c.external_url || c.platform_url || null,
      };
    });
  }, [campaignsData]);

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('desc');
    }
  };

  const filteredCampaigns = useMemo(
    () =>
      [...campaigns].sort((a, b) => {
        const aValue = a[sortField];
        const bValue = b[sortField];
        const direction = sortDirection === 'asc' ? 1 : -1;
        if (typeof aValue === 'string') {
          return aValue.localeCompare(bValue as string) * direction;
        }
        return ((aValue as number) - (bValue as number)) * direction;
      }),
    [campaigns, sortField, sortDirection]
  );

  const handleBulkPause = async () => {
    for (const id of selectedCampaigns) {
      await pauseCampaign.mutateAsync(id.toString());
    }
    setSelectedCampaigns([]);
  };

  const handleBulkActivate = async () => {
    for (const id of selectedCampaigns) {
      await activateCampaign.mutateAsync(id.toString());
    }
    setSelectedCampaigns([]);
  };

  const handleBulkDelete = async () => {
    for (const id of selectedCampaigns) {
      await deleteCampaign.mutateAsync(id.toString());
    }
    setSelectedCampaigns([]);
  };

  const toggleSelectAll = () => {
    if (selectedCampaigns.length === filteredCampaigns.length) {
      setSelectedCampaigns([]);
    } else {
      setSelectedCampaigns(filteredCampaigns.map((c) => c.id));
    }
  };

  const toggleSelectCampaign = (id: number) => {
    setSelectedCampaigns((prev) =>
      prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]
    );
  };

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return null;
    return sortDirection === 'asc' ? (
      <ChevronUp className="w-4 h-4" />
    ) : (
      <ChevronDown className="w-4 h-4" />
    );
  };

  const getStatusBadge = (status: Campaign['status']) => {
    const styles = {
      active: 'bg-green-500/10 text-green-500',
      paused: 'bg-amber-500/10 text-amber-500',
      completed: 'bg-blue-500/10 text-blue-500',
      draft: 'bg-gray-500/10 text-gray-500',
    };
    return (
      <span className={cn('px-2 py-1 rounded-full text-xs font-medium', styles[status])}>
        {status.charAt(0).toUpperCase() + status.slice(1)}
      </span>
    );
  };

  const getPlatformBadge = (platform: string) => (
    <div
      className="w-6 h-6 rounded-full flex items-center justify-center text-white text-xs font-bold"
      style={{ backgroundColor: getPlatformColor(platform) }}
      title={platform.charAt(0).toUpperCase() + platform.slice(1)}
    >
      {platform.charAt(0).toUpperCase()}
    </div>
  );

  const resetPageOnFilter = <T,>(setter: (value: T) => void) => {
    return (value: T) => {
      setter(value);
      setPage(1);
      setSelectedCampaigns([]);
    };
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">{t('campaigns.title')}</h1>
          <p className="text-muted-foreground">{t('campaigns.subtitle')}</p>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              discoverCampaigns.mutate(undefined, {
                onSuccess: () => {
                  toast({
                    title: 'Discovery queued',
                    description: 'Campaign list refreshes when Meta sync finishes.',
                  });
                  void refetch();
                },
                onError: (err) => {
                  toast({
                    title: 'Discovery failed',
                    description: err instanceof Error ? err.message : 'Could not start discovery',
                    variant: 'destructive',
                  });
                },
              });
            }}
            disabled={discoverCampaigns.isPending}
            className="flex items-center gap-2 px-4 py-2 rounded-lg border bg-background hover:bg-muted transition-colors disabled:opacity-50"
          >
            <Loader2
              className={cn('w-4 h-4', discoverCampaigns.isPending ? 'animate-spin' : 'hidden')}
            />
            <RefreshCw
              className={cn('w-4 h-4', discoverCampaigns.isPending ? 'hidden' : undefined)}
            />
            <span>Discover from Meta</span>
          </button>
          <button
            onClick={() => setCreateModalOpen(true)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            <Plus className="w-4 h-4" />
            <span>{t('campaigns.createNew')}</span>
          </button>
        </div>
      </div>

      <div className="flex flex-col md:flex-row gap-4">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <input
            type="text"
            placeholder={t('campaigns.searchPlaceholder')}
            value={searchQuery}
            onChange={(e) => resetPageOnFilter(setSearchQuery)(e.target.value)}
            className="w-full pl-10 pr-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </div>

        <div className="flex flex-wrap gap-3">
          <select
            value={statusFilter}
            onChange={(e) => resetPageOnFilter(setStatusFilter)(e.target.value)}
            className="px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
          >
            <option value="all">{t('campaigns.allStatuses')}</option>
            <option value="active">{t('campaigns.active')}</option>
            <option value="paused">{t('campaigns.paused')}</option>
            <option value="completed">{t('campaigns.completed')}</option>
            <option value="draft">{t('campaigns.draft')}</option>
          </select>

          <select
            value={platformFilter}
            onChange={(e) => resetPageOnFilter(setPlatformFilter)(e.target.value)}
            className="px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
          >
            <option value="all">{t('campaigns.allPlatforms')}</option>
            <option value="meta">Meta</option>
          </select>

          <select
            value={accountFilter}
            onChange={(e) => resetPageOnFilter(setAccountFilter)(e.target.value)}
            className="px-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20 min-w-[12rem]"
          >
            <option value="all">All ad accounts</option>
            {adAccounts.map((account) => (
              <option key={account.platform_account_id} value={account.platform_account_id}>
                {account.name || account.platform_account_id}
              </option>
            ))}
          </select>
        </div>
      </div>

      {selectedCampaigns.length > 0 && (
        <div className="flex items-center gap-4 p-3 rounded-lg bg-primary/10 border border-primary/20">
          <span className="text-sm font-medium">
            {selectedCampaigns.length} {t('campaigns.selected')}
          </span>
          <div className="flex gap-2">
            <button
              onClick={handleBulkPause}
              disabled={pauseCampaign.isPending}
              className="flex items-center gap-1 px-3 py-1.5 rounded-md bg-background border hover:bg-muted transition-colors text-sm disabled:opacity-50"
            >
              {pauseCampaign.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Pause className="w-4 h-4" />
              )}
              {t('campaigns.pauseSelected')}
            </button>
            <button
              onClick={handleBulkActivate}
              disabled={activateCampaign.isPending}
              className="flex items-center gap-1 px-3 py-1.5 rounded-md bg-background border hover:bg-muted transition-colors text-sm disabled:opacity-50"
            >
              {activateCampaign.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Play className="w-4 h-4" />
              )}
              {t('campaigns.activateSelected')}
            </button>
            <button
              onClick={handleBulkDelete}
              disabled={deleteCampaign.isPending}
              className="flex items-center gap-1 px-3 py-1.5 rounded-md bg-red-500/10 text-red-500 hover:bg-red-500/20 transition-colors text-sm disabled:opacity-50"
            >
              {deleteCampaign.isPending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Trash2 className="w-4 h-4" />
              )}
              {t('campaigns.deleteSelected')}
            </button>
          </div>
        </div>
      )}

      <div className="rounded-xl border bg-card overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead className="bg-muted/50 border-b">
              <tr>
                <th className="p-4 text-left">
                  <input
                    type="checkbox"
                    checked={
                      selectedCampaigns.length === filteredCampaigns.length &&
                      filteredCampaigns.length > 0
                    }
                    onChange={toggleSelectAll}
                    className="rounded border-muted-foreground/50"
                  />
                </th>
                <th className="p-4 text-left">
                  <button
                    onClick={() => handleSort('name')}
                    className="flex items-center gap-1 text-sm font-medium hover:text-primary"
                  >
                    {t('campaigns.name')}
                    <SortIcon field="name" />
                  </button>
                </th>
                <th className="p-4 text-left text-sm font-medium">{t('campaigns.statusLabel')}</th>
                <th className="p-4 text-right">
                  <button
                    onClick={() => handleSort('spend')}
                    className="flex items-center gap-1 text-sm font-medium hover:text-primary ml-auto"
                  >
                    {t('campaigns.spend')}
                    <SortIcon field="spend" />
                  </button>
                </th>
                <th className="p-4 text-right">
                  <button
                    onClick={() => handleSort('revenue')}
                    className="flex items-center gap-1 text-sm font-medium hover:text-primary ml-auto"
                  >
                    {t('campaigns.revenue')}
                    <SortIcon field="revenue" />
                  </button>
                </th>
                <th className="p-4 text-right">
                  <button
                    onClick={() => handleSort('roas')}
                    className="flex items-center gap-1 text-sm font-medium hover:text-primary ml-auto"
                  >
                    ROAS
                    <SortIcon field="roas" />
                  </button>
                </th>
                <th className="p-4 text-right">
                  <button
                    onClick={() => handleSort('conversions')}
                    className="flex items-center gap-1 text-sm font-medium hover:text-primary ml-auto"
                  >
                    {t('campaigns.conversions')}
                    <SortIcon field="conversions" />
                  </button>
                </th>
                <th className="p-4 text-right text-sm font-medium">CTR</th>
                <th className="p-4 text-center text-sm font-medium">{t('campaigns.trend')}</th>
                <th className="p-4 text-right text-sm font-medium">{t('campaigns.actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {filteredCampaigns.map((campaign) => (
                <tr key={campaign.id} className="hover:bg-muted/30 transition-colors">
                  <td className="p-4">
                    <input
                      type="checkbox"
                      checked={selectedCampaigns.includes(campaign.id)}
                      onChange={() => toggleSelectCampaign(campaign.id)}
                      className="rounded border-muted-foreground/50"
                    />
                  </td>
                  <td className="p-4">
                    <div className="flex items-center gap-3">
                      {getPlatformBadge(campaign.platform)}
                      <div>
                        <p className="font-medium">{campaign.name}</p>
                        <p className="text-xs text-muted-foreground">
                          {formatCurrency(campaign.spend)} / {formatCurrency(campaign.budget)}
                          {campaign.accountId ? ` · ${campaign.accountId}` : ''}
                        </p>
                      </div>
                    </div>
                  </td>
                  <td className="p-4">{getStatusBadge(campaign.status)}</td>
                  <td className="p-4 text-right font-medium">{formatCurrency(campaign.spend)}</td>
                  <td className="p-4 text-right font-medium text-green-500">
                    {formatCurrency(campaign.revenue)}
                  </td>
                  <td className="p-4 text-right">
                    <span
                      className={cn(
                        'font-semibold',
                        campaign.roas >= 4 && 'text-green-500',
                        campaign.roas >= 3 && campaign.roas < 4 && 'text-primary',
                        campaign.roas < 3 && 'text-amber-500'
                      )}
                    >
                      {campaign.roas.toFixed(2)}x
                    </span>
                  </td>
                  <td className="p-4 text-right">{formatCompactNumber(campaign.conversions)}</td>
                  <td className="p-4 text-right">{formatPercent(campaign.ctr)}</td>
                  <td className="p-4 text-center">
                    {campaign.trend === 'up' && (
                      <TrendingUp className="w-5 h-5 text-green-500 mx-auto" />
                    )}
                    {campaign.trend === 'down' && (
                      <TrendingDown className="w-5 h-5 text-red-500 mx-auto" />
                    )}
                    {campaign.trend === 'stable' && (
                      <div className="w-5 h-0.5 bg-muted-foreground mx-auto" />
                    )}
                  </td>
                  <td className="p-4 text-right">
                    <div className="flex items-center justify-end gap-1">
                      {campaign.externalUrl ? (
                        <a
                          href={campaign.externalUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="p-2 rounded-lg hover:bg-muted transition-colors"
                          title="Open in Meta"
                        >
                          <ExternalLink className="w-4 h-4" />
                        </a>
                      ) : (
                        <span
                          className="p-2 rounded-lg text-muted-foreground/40"
                          title="No Meta deep-link for this row"
                        >
                          <ExternalLink className="w-4 h-4" />
                        </span>
                      )}
                      {campaign.status === 'active' ? (
                        <button
                          type="button"
                          onClick={() => pauseCampaign.mutate(campaign.id.toString())}
                          disabled={pauseCampaign.isPending}
                          className="p-2 rounded-lg hover:bg-muted transition-colors disabled:opacity-50"
                          title="Pause locally in Stratum"
                        >
                          <Pause className="w-4 h-4" />
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => activateCampaign.mutate(campaign.id.toString())}
                          disabled={activateCampaign.isPending}
                          className="p-2 rounded-lg hover:bg-muted transition-colors disabled:opacity-50"
                          title="Activate locally in Stratum"
                        >
                          <Play className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {filteredCampaigns.length === 0 && !isLoading && (
          <div className="p-12 text-center">
            <p className="text-muted-foreground">{t('campaigns.noResults')}</p>
          </div>
        )}
      </div>

      <div className="flex items-center justify-between gap-4 flex-wrap">
        <p className="text-sm text-muted-foreground">
          {isLoading ? (
            <span className="flex items-center gap-2">
              <Loader2 className="w-4 h-4 animate-spin" />
              Loading campaigns...
            </span>
          ) : (
            t('campaigns.showing', {
              count: filteredCampaigns.length,
              total: totalCampaigns,
            })
          )}
        </p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="px-3 py-1.5 rounded-lg border hover:bg-muted transition-colors text-sm disabled:opacity-50"
            disabled={page <= 1 || isLoading}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            {t('common.previous')}
          </button>
          <span className="px-3 py-1.5 rounded-lg bg-primary text-primary-foreground text-sm">
            {page} / {totalPages}
          </span>
          <button
            type="button"
            className="px-3 py-1.5 rounded-lg border hover:bg-muted transition-colors text-sm disabled:opacity-50"
            disabled={page >= totalPages || isLoading}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          >
            {t('common.next')}
          </button>
        </div>
      </div>

      <CampaignCreateModal
        open={createModalOpen}
        onClose={() => setCreateModalOpen(false)}
        onSuccess={(campaign) => {
          toast({
            title: 'Campaign created',
            description: `"${campaign?.name || 'New campaign'}" has been created successfully.`,
          });
          void refetch();
        }}
      />
    </div>
  );
}

export default Campaigns;
