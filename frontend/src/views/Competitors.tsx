/**
 * Competitor Intelligence Page
 *
 * API-backed CompetitorResponse fields only. No Math.random, mock rows,
 * or invented keyword-overlap percentages.
 */

import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';
import {
  useCompetitors,
  useDeleteCompetitor,
  useShareOfVoice,
} from '@/api/hooks';
import type { Competitor } from '@/api/competitors';
import {
  ArrowTopRightOnSquareIcon,
  ArrowTrendingDownIcon,
  ArrowTrendingUpIcon,
  ChartBarIcon,
  EllipsisHorizontalIcon,
  EyeIcon,
  GlobeAltIcon,
  MagnifyingGlassIcon,
  MinusIcon,
  PlusIcon,
  TrashIcon,
} from '@heroicons/react/24/outline';
import { AddCompetitorModal } from '@/components/competitors/AddCompetitorModal';

function TrendIcon({ trend }: { trend: string | null | undefined }) {
  if (trend === 'up') return <ArrowTrendingUpIcon className="w-3 h-3 text-green-500" />;
  if (trend === 'down') return <ArrowTrendingDownIcon className="w-3 h-3 text-red-500" />;
  return <MinusIcon className="w-3 h-3 text-muted-foreground" />;
}

function keywordLabel(entry: { keyword?: string; query?: string } | string): string {
  if (typeof entry === 'string') return entry;
  return entry.keyword || entry.query || '—';
}

function formatCents(cents: number | null | undefined): string {
  if (cents == null) return '—';
  const value = cents / 100;
  if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
}

function formatFetchedAt(iso: string | null | undefined): string {
  if (!iso) return 'Never';
  const hours = Math.floor((Date.now() - new Date(iso).getTime()) / (60 * 60 * 1000));
  if (Number.isNaN(hours)) return 'Unknown';
  if (hours < 1) return 'Just now';
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function metaAdsLibraryUrl(name: string): string {
  return `https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=ALL&q=${encodeURIComponent(name)}&search_type=keyword_unordered`;
}

export function Competitors() {
  const { t: _t } = useTranslation();
  const [searchParams, setSearchParams] = useSearchParams();
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);

  useEffect(() => {
    if (searchParams.get('action') === 'create') {
      setIsModalOpen(true);
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const { data: competitorsData, isLoading, refetch } = useCompetitors();
  const { data: sovData } = useShareOfVoice();
  const deleteCompetitor = useDeleteCompetitor();

  const competitors: Competitor[] = useMemo(() => {
    if (!competitorsData) return [];
    if (Array.isArray(competitorsData)) return competitorsData;
    if (Array.isArray(competitorsData.items)) return competitorsData.items;
    return [];
  }, [competitorsData]);

  const keywordRows = useMemo(() => {
    const rows: { keyword: string; competitor: string; type?: string }[] = [];
    for (const c of competitors) {
      for (const kw of c.top_keywords || []) {
        rows.push({
          keyword: keywordLabel(kw),
          competitor: c.name || c.domain,
          type: typeof kw === 'object' ? kw.type : undefined,
        });
      }
    }
    return rows.slice(0, 25);
  }, [competitors]);

  const shareOfVoice = useMemo(() => {
    if (sovData?.competitors?.length) {
      return sovData.competitors.map((c) => ({
        name: c.is_primary ? `You (${c.name || c.domain})` : c.name || c.domain,
        share: c.share_of_voice ?? 0,
        isPrimary: c.is_primary,
      }));
    }
    return competitors
      .filter((c) => c.share_of_voice != null)
      .map((c) => ({
        name: c.is_primary ? `You (${c.name || c.domain})` : c.name || c.domain,
        share: c.share_of_voice ?? 0,
        isPrimary: c.is_primary,
      }));
  }, [sovData, competitors]);

  const filtered = competitors.filter(
    (c) =>
      (c.name || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
      c.domain.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleDelete = async (id: number) => {
    if (!confirm('Are you sure you want to delete this competitor?')) return;
    try {
      await deleteCompetitor.mutateAsync(id);
      refetch();
    } catch (error) {
      console.error('Failed to delete competitor:', error);
    }
  };

  const stats = {
    total: competitors.length,
    withSpend: competitors.filter((c) => (c.estimated_ad_spend_cents || 0) > 0).length,
    keywords: competitors.reduce(
      (sum, c) => sum + (c.paid_keywords_count || 0) + (c.organic_keywords_count || 0),
      0
    ),
    creatives: competitors.reduce((sum, c) => sum + (c.ad_creatives_count || 0), 0),
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Competitor Intelligence</h1>
          <p className="text-muted-foreground">Track competitor activity and market share</p>
        </div>
        <button
          onClick={() => setIsModalOpen(true)}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
        >
          <PlusIcon className="w-4 h-4" />
          Add Competitor
        </button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="metric-card premium p-4">
          <div className="text-sm text-muted-foreground mb-1">Tracked Competitors</div>
          <div className="text-2xl font-bold">{stats.total}</div>
        </div>
        <div className="metric-card success p-4">
          <div className="text-sm text-muted-foreground mb-1">With Est. Spend</div>
          <div className="text-2xl font-bold text-green-500">{stats.withSpend}</div>
        </div>
        <div className="metric-card active p-4">
          <div className="text-sm text-muted-foreground mb-1">Keywords Tracked</div>
          <div className="text-2xl font-bold">{stats.keywords}</div>
        </div>
        <div className="metric-card warning p-4">
          <div className="text-sm text-muted-foreground mb-1">Creatives Tracked</div>
          <div className="text-2xl font-bold">{stats.creatives}</div>
        </div>
      </div>

      <div className="metric-card premium p-6">
        <div className="flex items-center gap-3 mb-4">
          <ChartBarIcon className="w-5 h-5 text-purple-500" />
          <h2 className="font-semibold">Share of Voice</h2>
        </div>
        {shareOfVoice.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No share-of-voice data yet. Add competitors and refresh market intel.
          </p>
        ) : (
          <>
            <div className="flex items-center gap-2 mb-4">
              <div className="flex-1 h-8 rounded-full overflow-hidden flex bg-muted">
                {shareOfVoice.map((c, i) => (
                  <div
                    key={c.name}
                    className="h-full"
                    style={{
                      width: `${Math.max(c.share, 0)}%`,
                      backgroundColor: c.isPrimary
                        ? 'hsl(var(--primary))'
                        : ['#ef4444', '#f59e0b', '#10b981', '#6366f1'][i % 4],
                    }}
                    title={`${c.name}: ${c.share}%`}
                  />
                ))}
              </div>
            </div>
            <div className="flex flex-wrap gap-4 text-sm">
              {shareOfVoice.map((c, i) => (
                <div key={c.name} className="flex items-center gap-2">
                  <div
                    className="w-3 h-3 rounded-full"
                    style={{
                      backgroundColor: c.isPrimary
                        ? 'hsl(var(--primary))'
                        : ['#ef4444', '#f59e0b', '#10b981', '#6366f1'][i % 4],
                    }}
                  />
                  <span>
                    {c.name} ({c.share}%)
                  </span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="relative max-w-md">
        <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-muted-foreground" />
        <input
          type="text"
          placeholder="Search competitors..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="w-full pl-10 pr-4 py-2 rounded-lg border bg-background focus:outline-none focus:ring-2 focus:ring-primary/20"
        />
      </div>

      {isLoading ? (
        <div className="text-center py-12 text-muted-foreground">Loading competitors…</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {filtered.map((competitor) => (
            <div
              key={competitor.id}
              className={cn(
                'metric-card info p-4 cursor-pointer',
                selectedId === competitor.id && 'ring-2 ring-primary'
              )}
              onClick={() =>
                setSelectedId(selectedId === competitor.id ? null : competitor.id)
              }
            >
              <div className="flex items-start justify-between mb-4">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-full bg-muted flex items-center justify-center">
                    <GlobeAltIcon className="w-5 h-5 text-muted-foreground" />
                  </div>
                  <div>
                    <h3 className="font-semibold">{competitor.name || competitor.domain}</h3>
                    <p className="text-sm text-muted-foreground">{competitor.domain}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {competitor.is_primary && (
                    <span className="px-2 py-1 rounded-full text-xs bg-primary/10 text-primary">
                      primary
                    </span>
                  )}
                  <button className="p-1 rounded hover:bg-muted transition-colors">
                    <EllipsisHorizontalIcon className="w-5 h-5" />
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4 mb-4">
                <div>
                  <div className="text-sm text-muted-foreground">Est. Ad Spend</div>
                  <div className="flex items-center gap-2">
                    <span className="font-semibold">
                      {formatCents(competitor.estimated_ad_spend_cents)}/mo
                    </span>
                    <span className="flex items-center text-xs gap-1">
                      <TrendIcon trend={competitor.traffic_trend} />
                      <span className="text-muted-foreground">
                        {competitor.traffic_trend || 'n/a'}
                      </span>
                    </span>
                  </div>
                </div>
                <div>
                  <div className="text-sm text-muted-foreground">Share of Voice</div>
                  <div className="font-semibold">
                    {competitor.share_of_voice != null ? `${competitor.share_of_voice}%` : '—'}
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-4">
                  <span className="text-muted-foreground">
                    {(competitor.paid_keywords_count || 0) +
                      (competitor.organic_keywords_count || 0)}{' '
                    }
                    keywords
                  </span>
                  <span className="text-muted-foreground">
                    {competitor.ad_creatives_count ?? 0} creatives
                  </span>
                </div>
                <span className="text-muted-foreground">
                  Updated {formatFetchedAt(competitor.last_fetched_at)}
                </span>
              </div>

              {competitor.fetch_error && (
                <p className="mt-2 text-xs text-red-500">Fetch error: {competitor.fetch_error}</p>
              )}

              <div className="flex items-center gap-2 mt-3 pt-3 border-t">
                <a
                  href={metaAdsLibraryUrl(competitor.name || competitor.domain)}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs bg-blue-500/10 text-blue-600 hover:bg-blue-500/20 transition-colors"
                >
                  <span className="font-bold">M</span>
                  Meta Ads Library
                  <ArrowTopRightOnSquareIcon className="w-3 h-3" />
                </a>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(competitor.id);
                  }}
                  className="ml-auto p-1.5 rounded-md text-red-500 hover:bg-red-500/10 transition-colors"
                  title="Delete competitor"
                >
                  <TrashIcon className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="metric-card active overflow-hidden">
        <div className="flex items-center justify-between p-4 border-b border-cyan-500/20">
          <h2 className="font-semibold">Tracked Keywords</h2>
          <span className="text-xs text-muted-foreground">From competitor refresh data</span>
        </div>
        {keywordRows.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">
            No keywords returned yet. Keyword overlap % is not shown because the API does not
            compute it — this table lists competitor keywords only.
          </p>
        ) : (
          <table className="w-full">
            <thead className="bg-cyan-500/10 border-b border-cyan-500/20">
              <tr>
                <th className="p-4 text-left text-sm font-medium">Keyword</th>
                <th className="p-4 text-left text-sm font-medium">Competitor</th>
                <th className="p-4 text-left text-sm font-medium">Type</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {keywordRows.map((kw, i) => (
                <tr key={`${kw.keyword}-${kw.competitor}-${i}`} className="hover:bg-muted/30">
                  <td className="p-4 font-medium">{kw.keyword}</td>
                  <td className="p-4 text-sm text-muted-foreground">{kw.competitor}</td>
                  <td className="p-4 text-sm text-muted-foreground">{kw.type || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {!isLoading && filtered.length === 0 && (
        <div className="text-center py-12">
          <EyeIcon className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
          <p className="text-muted-foreground">No competitors found</p>
        </div>
      )}

      <AddCompetitorModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
        onSuccess={() => refetch()}
      />
    </div>
  );
}

export default Competitors;
