/**
 * Stratum AI - Competitor Intelligence API
 *
 * Competitor monitoring and share of voice tracking
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient, ApiResponse, PaginatedResponse } from './client';

// Types — mirror CompetitorResponse (snake_case). No invented camelCase metrics.
export interface CompetitorKeyword {
  keyword?: string;
  query?: string;
  type?: string;
  volume?: number;
  position?: number;
  cpc?: number;
}

export interface Competitor {
  id: number;
  tenant_id: number;
  name: string | null;
  domain: string;
  is_primary: boolean;
  meta_title?: string | null;
  meta_description?: string | null;
  meta_keywords?: string[] | null;
  social_links?: Record<string, string> | null;
  estimated_traffic?: number | null;
  traffic_trend?: string | null;
  top_keywords?: CompetitorKeyword[] | null;
  paid_keywords_count?: number | null;
  organic_keywords_count?: number | null;
  share_of_voice?: number | null;
  category_rank?: number | null;
  estimated_ad_spend_cents?: number | null;
  detected_ad_platforms?: string[] | null;
  ad_creatives_count?: number | null;
  data_source: string;
  last_fetched_at?: string | null;
  fetch_error?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface CompetitorMetrics {
  competitorId: string;
  date: string;
  platform: string;
  shareOfVoice: number;
  estimatedSpend: number;
  impressionShare: number;
  adCount: number;
  topKeywords: string[];
  sentiment: number; // -1 to 1
}

export interface ShareOfVoiceCompetitor {
  domain: string;
  name: string | null;
  share_of_voice: number | null;
  estimated_traffic: number | null;
  traffic_trend: string | null;
  is_primary: boolean;
}

export interface ShareOfVoice {
  competitors: ShareOfVoiceCompetitor[];
  total_market: number;
  date_range: { start: string; end: string };
}

export interface KeywordOverlap {
  keyword: string;
  competitorId: string;
  competitorName: string;
  yourPosition: number | null;
  competitorPosition: number;
  searchVolume: number;
  competitionLevel: 'low' | 'medium' | 'high';
}

export interface CompetitorFilters {
  platform?: string;
  search?: string;
  skip?: number;
  limit?: number;
}

export interface CreateCompetitorRequest {
  name: string;
  domain: string;
  country?: string;
  platforms?: string[];
}

// API Functions
export const competitorsApi = {
  /**
   * Get all competitors
   */
  getCompetitors: async (
    filters: CompetitorFilters = {}
  ): Promise<PaginatedResponse<Competitor>> => {
    const response = await apiClient.get<ApiResponse<Competitor[] | PaginatedResponse<Competitor>>>(
      '/competitors',
      { params: filters }
    );
    const payload = response.data.data;
    // Backend returns a bare list; normalize for callers that expect `.items`.
    if (Array.isArray(payload)) {
      return { items: payload, total: payload.length, skip: 0, limit: payload.length };
    }
    return payload;
  },

  /**
   * Get a single competitor
   */
  getCompetitor: async (id: string | number): Promise<Competitor> => {
    const response = await apiClient.get<ApiResponse<Competitor>>(`/competitors/${id}`);
    return response.data.data;
  },

  /**
   * Create a competitor
   */
  createCompetitor: async (data: CreateCompetitorRequest): Promise<Competitor> => {
    const response = await apiClient.post<ApiResponse<Competitor>>('/competitors', data);
    return response.data.data;
  },

  /**
   * Update a competitor
   */
  updateCompetitor: async (
    id: string | number,
    data: Partial<CreateCompetitorRequest>
  ): Promise<Competitor> => {
    const response = await apiClient.patch<ApiResponse<Competitor>>(`/competitors/${id}`, data);
    return response.data.data;
  },

  /**
   * Delete a competitor
   */
  deleteCompetitor: async (id: string | number): Promise<void> => {
    await apiClient.delete(`/competitors/${id}`);
  },

  /**
   * Get share of voice data
   */
  getShareOfVoice: async (
    _startDate?: string,
    _endDate?: string,
    _platform?: string
  ): Promise<ShareOfVoice> => {
    // Backend computes current SoV from stored competitor rows (no date filter).
    const response = await apiClient.get<ApiResponse<ShareOfVoice>>('/competitors/share-of-voice');
    return response.data.data;
  },

  /**
   * Get competitor keywords
   */
  getCompetitorKeywords: async (id: string): Promise<KeywordOverlap[]> => {
    const response = await apiClient.get<ApiResponse<KeywordOverlap[]>>(
      `/competitors/${id}/keywords`
    );
    return response.data.data;
  },

  /**
   * Get competitor metrics
   */
  getCompetitorMetrics: async (
    id: string,
    startDate: string,
    endDate: string
  ): Promise<CompetitorMetrics[]> => {
    const response = await apiClient.get<ApiResponse<CompetitorMetrics[]>>(
      `/competitors/${id}/metrics`,
      { params: { start_date: startDate, end_date: endDate } }
    );
    return response.data.data;
  },

  /**
   * Refresh competitor data
   */
  refreshCompetitor: async (id: string | number): Promise<Competitor> => {
    const response = await apiClient.post<ApiResponse<Competitor>>(`/competitors/${id}/refresh`);
    return response.data.data;
  },
};

// React Query Hooks

export function useCompetitors(filters: CompetitorFilters = {}) {
  return useQuery({
    queryKey: ['competitors', filters],
    queryFn: () => competitorsApi.getCompetitors(filters),
    staleTime: 5 * 60 * 1000,
  });
}

export function useCompetitor(id: string | number) {
  return useQuery({
    queryKey: ['competitors', id],
    queryFn: () => competitorsApi.getCompetitor(id),
    enabled: !!id,
  });
}

export function useCreateCompetitor() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: competitorsApi.createCompetitor,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['competitors'] });
    },
  });
}

export function useUpdateCompetitor() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: string | number; data: Partial<CreateCompetitorRequest> }) =>
      competitorsApi.updateCompetitor(id, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['competitors'] });
      queryClient.invalidateQueries({ queryKey: ['competitors', variables.id] });
    },
  });
}

export function useDeleteCompetitor() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: competitorsApi.deleteCompetitor,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['competitors'] });
    },
  });
}

export function useShareOfVoice(startDate?: string, endDate?: string, platform?: string) {
  return useQuery({
    queryKey: ['competitors', 'sov', startDate, endDate, platform],
    queryFn: () => competitorsApi.getShareOfVoice(startDate, endDate, platform),
    staleTime: 5 * 60 * 1000,
  });
}

export function useCompetitorKeywords(id: string) {
  return useQuery({
    queryKey: ['competitors', id, 'keywords'],
    queryFn: () => competitorsApi.getCompetitorKeywords(id),
    enabled: !!id,
  });
}

export function useCompetitorMetrics(id: string, startDate: string, endDate: string) {
  return useQuery({
    queryKey: ['competitors', id, 'metrics', startDate, endDate],
    queryFn: () => competitorsApi.getCompetitorMetrics(id, startDate, endDate),
    enabled: !!id && !!startDate && !!endDate,
  });
}

export function useRefreshCompetitor() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: competitorsApi.refreshCompetitor,
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ['competitors', id] });
    },
  });
}
