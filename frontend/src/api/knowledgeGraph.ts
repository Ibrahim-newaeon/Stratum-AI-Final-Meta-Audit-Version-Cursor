/**
 * Knowledge Graph API hooks — real endpoints only (no mock placeholderData).
 */

import { useQuery } from '@tanstack/react-query';
import { apiClient } from './client';

export type ProblemSeverity = 'critical' | 'warning' | 'info';

export interface KGProblem {
  id: string;
  severity: ProblemSeverity;
  title: string;
  description: string;
  impact: string;
  impactAmount: number;
  affectedCampaigns: number;
  platform: string;
  detectedAt: string;
  status: 'active' | 'resolved' | 'dismissed';
}

export interface KGProblemsData {
  problems: KGProblem[];
  summary: {
    critical: number;
    warnings: number;
    resolved30d: number;
    revenueAtRisk: number;
  };
}

export interface KGRevenueData {
  attributedRevenue: number;
  touchpointsTracked: number;
  avgPathLength: number;
  conversionWindow: string;
  modelComparison: {
    channel: string;
    firstTouch: number;
    lastTouch: number;
    linear: number;
    dataDriven: number;
  }[];
}

export interface KGChannelBreakdown {
  channel: string;
  revenue: number;
  percentage: number;
  color: string;
}

function unwrap<T>(body: unknown): T {
  if (body && typeof body === 'object' && 'data' in body) {
    const nested = (body as { data: unknown }).data;
    if (nested !== undefined) return nested as T;
  }
  return body as T;
}

function periodToDays(period: string): number {
  if (period.endsWith('d')) return Number(period.replace('d', '')) || 30;
  if (period.endsWith('w')) return (Number(period.replace('w', '')) || 4) * 7;
  return 30;
}

const CHANNEL_COLORS = ['#0866FF', '#E4405F', '#34A853', '#25D366', '#8B5CF6', '#6B7280'];

function mapSeverity(raw: string): ProblemSeverity {
  const s = (raw || '').toLowerCase();
  if (s === 'critical') return 'critical';
  if (s === 'high' || s === 'medium' || s === 'warning') return 'warning';
  return 'info';
}

export function useKGProblems(filters?: { severity?: ProblemSeverity; status?: string }) {
  return useQuery({
    queryKey: ['kg', 'problems', filters],
    queryFn: async (): Promise<KGProblemsData> => {
      const params = new URLSearchParams();
      if (filters?.severity) params.set('severity', filters.severity);
      const response = await apiClient.get(`/knowledge-graph/insights/problems?${params}`);
      const payload = unwrap<{
        problems?: any[];
        total?: number;
        by_severity?: Record<string, number>;
      }>(response.data);

      const problems: KGProblem[] = (payload?.problems || []).map((p: any) => ({
        id: String(p.id),
        severity: mapSeverity(String(p.severity || '')),
        title: p.title || 'Detected issue',
        description: p.description || '',
        impact: String(p.metrics?.impact_label || p.estimated_impact || 'Unknown impact'),
        impactAmount: Number(p.metrics?.revenue_at_risk || p.metrics?.impact_amount || 0),
        affectedCampaigns: Number(p.affected_nodes?.length || p.metrics?.affected_campaigns || 0),
        platform: String(p.metrics?.platform || 'Meta'),
        detectedAt: String(p.detected_at || ''),
        status: 'active' as const,
      }));

      const bySeverity = payload?.by_severity || {};
      const critical = Number(
        bySeverity.critical ?? problems.filter((p) => p.severity === 'critical').length
      );
      const warnings = Number(
        (bySeverity.high || 0) +
          (bySeverity.medium || 0) +
          problems.filter((p) => p.severity === 'warning').length
      );

      return {
        problems,
        summary: {
          critical,
          warnings,
          resolved30d: 0,
          revenueAtRisk: problems.reduce((sum, p) => sum + (p.impactAmount || 0), 0),
        },
      };
    },
    staleTime: 60 * 1000,
  });
}

/** Backend has no resolve endpoint yet — keep UI callable without faking success. */
export function useResolveKGProblem() {
  return {
    mutate: (_problemId: string) => {
      // Intentionally no-op until a real resolve API exists.
    },
    isPending: false,
  };
}

export function useKGRevenueAttribution(period: string = '30d') {
  return useQuery({
    queryKey: ['kg', 'revenue', period],
    queryFn: async (): Promise<KGRevenueData> => {
      const days = periodToDays(period);
      const response = await apiClient.get(
        `/knowledge-graph/analytics/revenue/by-channel?days=${days}`
      );
      const rows = unwrap<any[]>(response.data);
      const list = Array.isArray(rows) ? rows : [];
      const channels = list.map((r) => ({
        channel: String(r.channel || 'Unknown'),
        revenue: Number(r.revenue_cents || 0) / 100,
        transactions: Number(r.transactions || 0),
      }));
      const totalRevenue = channels.reduce((s, c) => s + c.revenue, 0);
      const denom = totalRevenue || 1;

      return {
        attributedRevenue: totalRevenue,
        touchpointsTracked: channels.reduce((s, c) => s + c.transactions, 0),
        avgPathLength: 0,
        conversionWindow: `${days} days`,
        modelComparison: channels.map((c) => {
          const pct = Math.round((c.revenue / denom) * 100);
          return {
            channel: c.channel,
            firstTouch: pct,
            lastTouch: pct,
            linear: pct,
            dataDriven: pct,
          };
        }),
      };
    },
    staleTime: 5 * 60 * 1000,
  });
}

export function useKGChannelBreakdown(period: string = '30d') {
  return useQuery({
    queryKey: ['kg', 'channels', period],
    queryFn: async (): Promise<KGChannelBreakdown[]> => {
      const days = periodToDays(period);
      const response = await apiClient.get(
        `/knowledge-graph/analytics/revenue/by-channel?days=${days}`
      );
      const rows = unwrap<any[]>(response.data);
      const list = Array.isArray(rows) ? rows : [];
      const mapped = list.map((r, idx) => ({
        channel: String(r.channel || 'Unknown'),
        revenue: Number(r.revenue_cents || 0) / 100,
        percentage: 0,
        color: CHANNEL_COLORS[idx % CHANNEL_COLORS.length],
      }));
      const total = mapped.reduce((s, c) => s + c.revenue, 0) || 1;
      return mapped.map((c) => ({
        ...c,
        percentage: Math.round((c.revenue / total) * 100),
      }));
    },
    staleTime: 5 * 60 * 1000,
  });
}
