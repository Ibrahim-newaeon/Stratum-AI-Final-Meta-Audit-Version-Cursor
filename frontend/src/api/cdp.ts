/**
 * Stratum AI - CDP (Customer Data Platform) API
 *
 * React Query hooks and typed client for all CDP features:
 * profiles, identity graph, events, segments, computed traits, RFM,
 * funnels, anomalies, webhooks and Meta audience sync.
 *
 * Endpoints live under /api/v1/cdp/... (the axios client already
 * carries the /api/v1 prefix in its baseURL).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient, ApiResponse } from './client';

// =============================================================================
// Shared types
// =============================================================================

export type LifecycleStage = 'anonymous' | 'known' | 'customer' | 'churned';

export type IdentifierType = 'email' | 'phone' | 'device_id' | 'anonymous_id' | 'external_id';

export interface IdentifierInput {
  identifier_type: IdentifierType;
  /** Raw value; hashed server-side before storage */
  value: string;
}

export interface Identifier {
  id: string;
  identifier_type: IdentifierType;
  identifier_hash: string;
  is_primary: boolean;
  confidence_score: number;
  created_at?: string;
}

export interface CDPProfile {
  id: string;
  external_id: string | null;
  lifecycle_stage: LifecycleStage;
  total_events: number;
  total_sessions: number;
  total_purchases: number;
  total_revenue: number;
  first_seen_at: string;
  last_seen_at: string;
  created_at: string;
  updated_at: string;
  identifiers?: Identifier[];
  profile_data?: Record<string, unknown>;
  computed_traits?: Record<string, unknown>;
}

export interface ProfileListResponse {
  profiles: CDPProfile[];
  total: number;
  limit?: number;
  offset?: number;
}

export interface ProfileStatistics {
  total_profiles: number;
  total_customers: number;
  active_profiles_30d: number;
  email_coverage_pct: number;
  phone_coverage_pct: number;
  new_profiles_7d?: number;
  lifecycle_distribution?: Record<string, number>;
}

export interface ProfileSearchParams {
  query?: string;
  lifecycle_stages?: LifecycleStage[];
  segment_ids?: string[];
  rfm_segments?: RFMSegment[];
  has_email?: boolean;
  has_phone?: boolean;
  is_customer?: boolean;
  min_events?: number;
  max_events?: number;
  min_revenue?: number;
  max_revenue?: number;
  first_seen_after?: string;
  first_seen_before?: string;
  last_seen_after?: string;
  last_seen_before?: string;
  sort_by?: string;
  sort_order?: 'asc' | 'desc';
  include_identifiers?: boolean;
  limit?: number;
  offset?: number;
}

// -----------------------------------------------------------------------------
// Sources
// -----------------------------------------------------------------------------

/**
 * Known CDP source types. 'sgtm' is the server-side Google Tag Manager
 * container (Measurement & Verification tag deployment), not an ad platform.
 */
export type CDPSourceType = 'website' | 'server' | 'sgtm' | 'import' | 'crm';

export const CDP_SOURCE_TYPE_LABELS: Record<CDPSourceType, string> = {
  website: 'Website',
  server: 'Server',
  sgtm: 'Server-side GTM',
  import: 'Import',
  crm: 'CRM',
};

export interface CDPSource {
  id: string;
  name: string;
  /** One of CDPSourceType; kept as string for backward compatibility with callers */
  source_type: string;
  write_key?: string;
  is_active: boolean;
  created_at: string;
}

export interface SourceCreate {
  name: string;
  source_type: string;
  description?: string;
}

export interface SourceListResponse {
  sources: CDPSource[];
  total: number;
}

// -----------------------------------------------------------------------------
// Events
// -----------------------------------------------------------------------------

export interface EventInput {
  event_name: string;
  identifiers: IdentifierInput[];
  properties?: Record<string, unknown>;
  timestamp?: string;
  source_id?: string;
}

export interface EventBatchInput {
  events: EventInput[];
  source_id?: string;
}

export interface EventBatchResponse {
  accepted: number;
  rejected: number;
  errors?: string[];
}

export interface CDPEvent {
  id: string;
  event_name: string;
  profile_id: string;
  properties: Record<string, unknown>;
  timestamp: string;
  source_id?: string | null;
  emq_score?: number | null;
}

export interface DailyVolume {
  date: string;
  count: number;
}

export interface EventByName {
  event_name: string;
  count: number;
}

export interface EventStatistics {
  total_events: number;
  unique_profiles?: number;
  avg_emq_score?: number | null;
  daily_volume?: DailyVolume[];
  events_by_name?: EventByName[];
  events_by_source?: Array<{ source_name: string; count: number }>;
  emq_distribution?: Array<{ score_range: string; count: number }>;
}

export interface EventTrends {
  overall_trend: string;
  overall_change_pct: number;
  period_days?: number;
  trends?: Array<{ event_name: string; trend: string; change_pct: number }>;
}

// -----------------------------------------------------------------------------
// Anomalies
// -----------------------------------------------------------------------------

export type AnomalySeverity = 'low' | 'medium' | 'high' | 'critical';

export interface EventAnomaly {
  source_name: string;
  metric: string;
  direction: 'high' | 'low';
  severity: AnomalySeverity;
  pct_change: number;
  zscore: number;
  current_value: number;
  baseline_mean: number;
  baseline_std: number;
  detected_at?: string;
}

export interface EventAnomaliesResponse {
  anomalies: EventAnomaly[];
  anomaly_count: number;
  total_sources_analyzed: number;
  has_critical: boolean;
  has_high: boolean;
  analysis_period_days: number;
}

export interface AnomalySummary {
  health_status: 'healthy' | 'fair' | 'degraded' | 'critical' | 'unknown';
  as_of: string;
  events_today: number;
  events_7d: number;
  wow_change_pct: number;
  volume_trend: 'increasing' | 'stable' | 'decreasing';
  avg_emq_score: number | null;
}

// -----------------------------------------------------------------------------
// Health
// -----------------------------------------------------------------------------

export interface CDPHealth {
  status: string;
  profiles_ok?: boolean;
  events_ok?: boolean;
  queue_depth?: number;
  last_event_at?: string | null;
}

// -----------------------------------------------------------------------------
// Webhooks
// -----------------------------------------------------------------------------

export type WebhookEventType =
  | 'event.received'
  | 'profile.created'
  | 'profile.updated'
  | 'profile.merged'
  | 'consent.updated'
  | 'all';

export interface CDPWebhook {
  id: string;
  name: string;
  url: string;
  event_types: WebhookEventType[];
  max_retries: number;
  timeout_seconds: number;
  is_active: boolean;
  secret_key?: string | null;
  last_triggered_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  failure_count: number;
  created_at: string;
  updated_at: string;
}

export interface WebhookCreate {
  name: string;
  url: string;
  event_types: WebhookEventType[];
  max_retries?: number;
  timeout_seconds?: number;
}

export type WebhookUpdate = Partial<WebhookCreate> & { is_active?: boolean };

export interface WebhookListResponse {
  webhooks: CDPWebhook[];
  total: number;
}

export interface WebhookTestResult {
  success: boolean;
  status_code?: number;
  response_time_ms?: number;
  error?: string;
}

// -----------------------------------------------------------------------------
// Identity graph
// -----------------------------------------------------------------------------

export interface IdentityGraphNode {
  id: string;
  type: IdentifierType;
  hash: string;
  is_primary: boolean;
  priority: number;
}

export interface IdentityGraphEdge {
  source: string;
  target: string;
  type: string;
  confidence: number;
}

export interface IdentityGraphResponse {
  nodes: IdentityGraphNode[];
  edges: IdentityGraphEdge[];
  total_identifiers: number;
  total_links: number;
}

export interface CanonicalIdentity {
  canonical_type: string | null;
  priority_score: number;
  is_verified: boolean;
}

export interface ProfileMerge {
  id: string;
  merged_profile_id: string;
  surviving_profile_id: string | null;
  merge_reason: string;
  merged_event_count: number;
  merged_identifier_count: number;
  is_rolled_back: boolean;
  created_at: string;
}

export interface MergeHistoryResponse {
  merges: ProfileMerge[];
  total?: number;
}

export interface IdentityLink {
  source_identifier_id: string;
  target_identifier_id: string;
  link_type: string;
  confidence: number;
}

// -----------------------------------------------------------------------------
// Segments
// -----------------------------------------------------------------------------

export type SegmentStatus = 'draft' | 'computing' | 'active' | 'stale' | 'archived';

export interface SegmentCondition {
  field: string;
  operator: string;
  value?: string | number | boolean | string[] | null;
}

export interface SegmentRules {
  logic: 'and' | 'or';
  conditions?: SegmentCondition[];
  groups?: SegmentRules[];
}

export interface CDPSegment {
  id: string;
  name: string;
  slug: string;
  description?: string | null;
  segment_type: 'dynamic' | 'static';
  rules: SegmentRules;
  status: SegmentStatus;
  profile_count: number;
  auto_refresh: boolean;
  refresh_interval_hours: number;
  last_computed_at: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
}

export interface SegmentCreate {
  name: string;
  description?: string;
  segment_type?: 'dynamic' | 'static';
  rules: SegmentRules;
  auto_refresh?: boolean;
  refresh_interval_hours?: number;
  tags?: string[];
}

export type SegmentUpdate = Partial<SegmentCreate>;

export interface SegmentListResponse {
  segments: CDPSegment[];
  total: number;
}

export interface SegmentPreviewResponse {
  estimated_count: number;
  sample_profiles: CDPProfile[];
}

// -----------------------------------------------------------------------------
// Computed traits
// -----------------------------------------------------------------------------

export type TraitType =
  | 'count'
  | 'sum'
  | 'average'
  | 'min'
  | 'max'
  | 'first'
  | 'last'
  | 'unique_count'
  | 'exists';

export interface ComputedTraitCreate {
  name: string;
  display_name: string;
  description?: string;
  trait_type: TraitType;
  output_type: string;
  source_config: {
    event_name?: string;
    property?: string;
    time_window_days?: number;
  };
  default_value?: string;
}

export interface CDPComputedTrait {
  id: string;
  name: string;
  display_name: string;
  description?: string | null;
  trait_type: string;
  output_type: string;
  source_config?: Record<string, unknown> | null;
  is_active: boolean;
  last_computed_at?: string | null;
  created_at?: string;
}

export interface ComputedTraitListResponse {
  traits: CDPComputedTrait[];
  total: number;
}

// -----------------------------------------------------------------------------
// RFM
// -----------------------------------------------------------------------------

export type RFMSegment =
  | 'champions'
  | 'loyal_customers'
  | 'potential_loyalists'
  | 'new_customers'
  | 'promising'
  | 'need_attention'
  | 'about_to_sleep'
  | 'at_risk'
  | 'cannot_lose'
  | 'hibernating'
  | 'lost'
  | 'other';

export interface RFMScores {
  rfm_segment: RFMSegment;
  recency_score: number;
  recency_days: number;
  frequency_score: number;
  frequency: number;
  monetary_score: number;
  monetary: number;
  rfm_score: number;
  analysis_window_days: number;
  calculated_at: string;
}

export interface RFMSummary {
  total_profiles: number;
  profiles_with_rfm: number;
  coverage_pct: number;
  segment_distribution: Record<RFMSegment, number>;
}

// -----------------------------------------------------------------------------
// Funnels
// -----------------------------------------------------------------------------

export interface FunnelStepCondition {
  field: string;
  operator: string;
  value?: unknown;
  [key: string]: unknown;
}

export interface FunnelStep {
  step_name: string;
  event_name: string;
  conditions?: FunnelStepCondition[];
}

export interface FunnelStepMetric {
  step: number;
  name: string;
  event_name: string;
  count: number;
  conversion_rate: number;
  drop_off_count: number;
}

export interface FunnelStepAnalysis {
  step: number;
  name: string;
  event_name: string;
  count: number;
  conversion_rate_from_start: number;
  drop_off_count: number;
}

export interface CDPFunnel {
  id: string;
  name: string;
  description?: string | null;
  steps: FunnelStep[];
  conversion_window_days: number;
  step_timeout_hours?: number;
  auto_refresh: boolean;
  refresh_interval_hours: number;
  status: string;
  total_entered: number;
  total_converted: number;
  overall_conversion_rate: number | null;
  step_metrics?: FunnelStepMetric[] | null;
  created_at?: string;
  updated_at?: string;
}

export interface FunnelCreate {
  name: string;
  description?: string;
  steps: FunnelStep[];
  conversion_window_days?: number;
  step_timeout_hours?: number;
  auto_refresh?: boolean;
  refresh_interval_hours?: number;
}

export type FunnelUpdate = Partial<FunnelCreate>;

export interface FunnelListResponse {
  funnels: CDPFunnel[];
  total: number;
}

export interface FunnelAnalysis {
  step_analysis: FunnelStepAnalysis[];
  total_entered: number;
  total_converted: number;
  overall_conversion_rate: number;
  avg_conversion_time_seconds?: number | null;
}

export interface FunnelDropOffsResponse {
  profiles: CDPProfile[];
  total?: number;
}

// -----------------------------------------------------------------------------
// Audience export & Meta audience sync
// -----------------------------------------------------------------------------

export interface AudienceExportParams extends Partial<ProfileSearchParams> {
  format: 'csv' | 'json';
  segment_id?: string;
  include_traits?: boolean;
  include_events?: boolean;
}

/** Meta-only sync surface: Meta (combined), Facebook, Instagram, WhatsApp */
export type SyncPlatform = 'meta' | 'facebook' | 'instagram' | 'whatsapp';

export type SyncStatus = 'pending' | 'processing' | 'completed' | 'failed' | 'partial';

export interface ConnectedPlatform {
  platform: SyncPlatform;
  ad_accounts: Array<{ ad_account_id: string; ad_account_name: string | null }>;
}

export interface PlatformAudience {
  id: string;
  platform: SyncPlatform;
  segment_id: string;
  ad_account_id: string;
  platform_audience_id: string | null;
  platform_audience_name: string;
  description?: string | null;
  auto_sync: boolean;
  sync_interval_hours: number;
  last_sync_status: SyncStatus | null;
  last_sync_at: string | null;
  platform_size: number | null;
  match_rate: number | null;
  created_at: string;
}

export interface PlatformAudienceListResponse {
  audiences: PlatformAudience[];
  total?: number;
}

export interface PlatformAudienceCreate {
  segment_id: string;
  platform: SyncPlatform;
  ad_account_id: string;
  audience_name: string;
  description?: string;
  auto_sync?: boolean;
  sync_interval_hours?: number;
}

export interface SyncJob {
  id: string;
  status: SyncStatus;
  operation: string;
  profiles_sent: number;
  profiles_added: number;
  profiles_failed: number;
  duration_ms: number | null;
  error_message?: string | null;
  triggered_by?: string | null;
  created_at: string;
}

export interface SyncHistoryResponse {
  jobs: SyncJob[];
  total?: number;
}

// =============================================================================
// Query keys
// =============================================================================

export const cdpQueryKeys = {
  all: ['cdp'] as const,
  health: () => [...cdpQueryKeys.all, 'health'] as const,
  profiles: () => [...cdpQueryKeys.all, 'profiles'] as const,
  profile: (id: string) => [...cdpQueryKeys.profiles(), id] as const,
  profileSearch: (params?: ProfileSearchParams) =>
    [...cdpQueryKeys.profiles(), 'search', params] as const,
  profileStatistics: () => [...cdpQueryKeys.profiles(), 'statistics'] as const,
  sources: () => [...cdpQueryKeys.all, 'sources'] as const,
  events: () => [...cdpQueryKeys.all, 'events'] as const,
  eventStatistics: (days?: number) => [...cdpQueryKeys.events(), 'statistics', days] as const,
  eventTrends: (days?: number) => [...cdpQueryKeys.events(), 'trends', days] as const,
  anomalies: (params?: Record<string, unknown>) =>
    [...cdpQueryKeys.events(), 'anomalies', params] as const,
  anomalySummary: () => [...cdpQueryKeys.events(), 'anomaly-summary'] as const,
  webhooks: () => [...cdpQueryKeys.all, 'webhooks'] as const,
  webhook: (id: string) => [...cdpQueryKeys.webhooks(), id] as const,
  identityGraph: (profileId: string) => [...cdpQueryKeys.all, 'identity', profileId] as const,
  canonicalIdentity: (profileId: string) =>
    [...cdpQueryKeys.all, 'identity', profileId, 'canonical'] as const,
  identityLinks: (profileId: string) =>
    [...cdpQueryKeys.all, 'identity', profileId, 'links'] as const,
  profileMergeHistory: (profileId: string) =>
    [...cdpQueryKeys.all, 'identity', profileId, 'merges'] as const,
  mergeHistory: (params?: Record<string, unknown>) =>
    [...cdpQueryKeys.all, 'identity', 'merge-history', params] as const,
  segments: (params?: Record<string, unknown>) =>
    [...cdpQueryKeys.all, 'segments', params] as const,
  segment: (id: string) => [...cdpQueryKeys.all, 'segments', id] as const,
  segmentProfiles: (id: string, params?: Record<string, unknown>) =>
    [...cdpQueryKeys.all, 'segments', id, 'profiles', params] as const,
  profileSegments: (profileId: string) =>
    [...cdpQueryKeys.profiles(), profileId, 'segments'] as const,
  computedTraits: (params?: Record<string, unknown>) =>
    [...cdpQueryKeys.all, 'computed-traits', params] as const,
  computedTrait: (id: string) => [...cdpQueryKeys.all, 'computed-traits', id] as const,
  profileRFM: (profileId: string) => [...cdpQueryKeys.profiles(), profileId, 'rfm'] as const,
  rfmSummary: () => [...cdpQueryKeys.all, 'rfm', 'summary'] as const,
  funnels: () => [...cdpQueryKeys.all, 'funnels'] as const,
  funnel: (id: string) => [...cdpQueryKeys.funnels(), id] as const,
  funnelDropOffs: (id: string, step: number, params?: Record<string, unknown>) =>
    [...cdpQueryKeys.funnels(), id, 'drop-offs', step, params] as const,
  profileFunnelJourneys: (profileId: string) =>
    [...cdpQueryKeys.profiles(), profileId, 'funnel-journeys'] as const,
  connectedPlatforms: () => [...cdpQueryKeys.all, 'audience-sync', 'platforms'] as const,
  platformAudiences: (params?: Record<string, unknown>) =>
    [...cdpQueryKeys.all, 'audience-sync', 'audiences', params] as const,
  syncHistory: (audienceId: string, limit?: number) =>
    [...cdpQueryKeys.all, 'audience-sync', 'audiences', audienceId, 'history', limit] as const,
};

// =============================================================================
// API client
// =============================================================================

async function get<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  const response = await apiClient.get<ApiResponse<T>>(url, { params });
  return response.data.data;
}

async function post<T>(url: string, body?: unknown): Promise<T> {
  const response = await apiClient.post<ApiResponse<T>>(url, body);
  return response.data.data;
}

async function put<T>(url: string, body?: unknown): Promise<T> {
  const response = await apiClient.put<ApiResponse<T>>(url, body);
  return response.data.data;
}

async function del<T>(url: string, params?: Record<string, unknown>): Promise<T> {
  const response = await apiClient.delete<ApiResponse<T>>(url, { params });
  return response.data.data;
}

export const cdpApi = {
  // Health
  getHealth: () => get<CDPHealth>('/cdp/health'),

  // Profiles
  getProfile: (profileId: string) => get<CDPProfile>(`/cdp/profiles/${profileId}`),
  lookupProfile: (identifierType: IdentifierType, value: string) =>
    get<CDPProfile>('/cdp/profiles/lookup', { identifier_type: identifierType, value }),
  deleteProfile: (profileId: string) => del<{ success: boolean }>(`/cdp/profiles/${profileId}`),
  searchProfiles: (params: ProfileSearchParams) =>
    post<ProfileListResponse>('/cdp/profiles/search', params),
  getProfileStatistics: () => get<ProfileStatistics>('/cdp/profiles/statistics'),

  // Sources
  getSources: () => get<SourceListResponse>('/cdp/sources'),
  createSource: (source: SourceCreate) => post<CDPSource>('/cdp/sources', source),

  // Events
  ingestEvent: (event: EventInput) => post<CDPEvent>('/cdp/events', event),
  ingestEvents: (batch: EventBatchInput) => post<EventBatchResponse>('/cdp/events/batch', batch),
  getEventStatistics: (days?: number) =>
    get<EventStatistics>('/cdp/events/statistics', { days }),
  getEventTrends: (days?: number) => get<EventTrends>('/cdp/events/trends', { days }),

  // Anomalies
  getEventAnomalies: (params?: { window_days?: number; zscore_threshold?: number }) =>
    get<EventAnomaliesResponse>('/cdp/anomalies/events', params),
  getAnomalySummary: () => get<AnomalySummary>('/cdp/anomalies/summary'),

  // Webhooks
  getWebhooks: () => get<WebhookListResponse>('/cdp/webhooks'),
  getWebhook: (webhookId: string) => get<CDPWebhook>(`/cdp/webhooks/${webhookId}`),
  createWebhook: (webhook: WebhookCreate) => post<CDPWebhook>('/cdp/webhooks', webhook),
  updateWebhook: (webhookId: string, update: WebhookUpdate) =>
    put<CDPWebhook>(`/cdp/webhooks/${webhookId}`, update),
  deleteWebhook: (webhookId: string) => del<{ success: boolean }>(`/cdp/webhooks/${webhookId}`),
  testWebhook: (webhookId: string) =>
    post<WebhookTestResult>(`/cdp/webhooks/${webhookId}/test`),
  rotateWebhookSecret: (webhookId: string) =>
    post<CDPWebhook>(`/cdp/webhooks/${webhookId}/rotate-secret`),

  // Identity graph
  getIdentityGraph: (profileId: string) =>
    get<IdentityGraphResponse>(`/cdp/identity/${profileId}/graph`),
  getCanonicalIdentity: (profileId: string) =>
    get<CanonicalIdentity>(`/cdp/identity/${profileId}/canonical`),
  getIdentityLinks: (profileId: string) =>
    get<IdentityLink[]>(`/cdp/identity/${profileId}/links`),
  getProfileMergeHistory: (profileId: string) =>
    get<MergeHistoryResponse>(`/cdp/identity/${profileId}/merges`),
  getMergeHistory: (params?: { limit?: number; offset?: number }) =>
    get<MergeHistoryResponse>('/cdp/identity/merge-history', params),
  mergeProfiles: (payload: { source_profile_id: string; target_profile_id: string }) =>
    post<ProfileMerge>('/cdp/identity/merge', payload),

  // Segments
  getSegments: (params?: { status?: string; limit?: number; offset?: number }) =>
    get<SegmentListResponse>('/cdp/segments', params),
  getSegment: (segmentId: string) => get<CDPSegment>(`/cdp/segments/${segmentId}`),
  createSegment: (segment: SegmentCreate) => post<CDPSegment>('/cdp/segments', segment),
  updateSegment: (segmentId: string, update: SegmentUpdate) =>
    put<CDPSegment>(`/cdp/segments/${segmentId}`, update),
  deleteSegment: (segmentId: string) => del<{ success: boolean }>(`/cdp/segments/${segmentId}`),
  computeSegment: (segmentId: string) =>
    post<CDPSegment>(`/cdp/segments/${segmentId}/compute`),
  previewSegment: (payload: { rules: SegmentRules; limit?: number }) =>
    post<SegmentPreviewResponse>('/cdp/segments/preview', payload),
  getSegmentProfiles: (segmentId: string, params?: { limit?: number; offset?: number }) =>
    get<ProfileListResponse>(`/cdp/segments/${segmentId}/profiles`, params),
  getProfileSegments: (profileId: string) =>
    get<SegmentListResponse>(`/cdp/profiles/${profileId}/segments`),

  // Computed traits
  getComputedTraits: (params?: { active_only?: boolean }) =>
    get<ComputedTraitListResponse>('/cdp/computed-traits', params),
  getComputedTrait: (traitId: string) =>
    get<CDPComputedTrait>(`/cdp/computed-traits/${traitId}`),
  createComputedTrait: (trait: ComputedTraitCreate) =>
    post<CDPComputedTrait>('/cdp/computed-traits', trait),
  deleteComputedTrait: (traitId: string) =>
    del<{ success: boolean }>(`/cdp/computed-traits/${traitId}`),
  computeAllTraits: () => post<{ queued: boolean }>('/cdp/computed-traits/compute-all'),

  // RFM
  getProfileRFM: (profileId: string) => get<RFMScores>(`/cdp/profiles/${profileId}/rfm`),
  computeRFMBatch: (params?: { analysis_window_days?: number }) =>
    post<{ queued: boolean }>('/cdp/rfm/compute', params),
  getRFMSummary: () => get<RFMSummary>('/cdp/rfm/summary'),

  // Funnels
  getFunnels: () => get<FunnelListResponse>('/cdp/funnels'),
  getFunnel: (funnelId: string) => get<CDPFunnel>(`/cdp/funnels/${funnelId}`),
  createFunnel: (funnel: FunnelCreate) => post<CDPFunnel>('/cdp/funnels', funnel),
  updateFunnel: (funnelId: string, update: FunnelUpdate) =>
    put<CDPFunnel>(`/cdp/funnels/${funnelId}`, update),
  deleteFunnel: (funnelId: string) => del<{ success: boolean }>(`/cdp/funnels/${funnelId}`),
  computeFunnel: (funnelId: string) => post<CDPFunnel>(`/cdp/funnels/${funnelId}/compute`),
  analyzeFunnel: (funnelId: string, params?: { start_date?: string; end_date?: string }) =>
    post<FunnelAnalysis>(`/cdp/funnels/${funnelId}/analyze`, params),
  getFunnelDropOffs: (funnelId: string, step: number, params?: { limit?: number }) =>
    get<FunnelDropOffsResponse>(`/cdp/funnels/${funnelId}/drop-offs/${step}`, params),
  getProfileFunnelJourneys: (profileId: string) =>
    get<Array<Record<string, unknown>>>(`/cdp/profiles/${profileId}/funnel-journeys`),

  // Audience export
  exportAudience: async (
    params: AudienceExportParams
  ): Promise<Blob | Record<string, unknown>> => {
    if (params.format === 'csv') {
      const response = await apiClient.post('/cdp/audiences/export', params, {
        responseType: 'blob',
      });
      return response.data as Blob;
    }
    const response = await apiClient.post('/cdp/audiences/export', params);
    return response.data as Record<string, unknown>;
  },

  // Audience sync (Meta: Facebook, Instagram, WhatsApp)
  getConnectedPlatforms: () => get<ConnectedPlatform[]>('/cdp/audience-sync/platforms'),
  getPlatformAudiences: (params?: { platform?: SyncPlatform }) =>
    get<PlatformAudienceListResponse>('/cdp/audience-sync/audiences', params),
  createPlatformAudience: (audience: PlatformAudienceCreate) =>
    post<PlatformAudience>('/cdp/audience-sync/audiences', audience),
  deletePlatformAudience: (audienceId: string, deleteFromPlatform?: boolean) =>
    del<{ success: boolean }>(`/cdp/audience-sync/audiences/${audienceId}`, {
      delete_from_platform: deleteFromPlatform,
    }),
  triggerSync: (audienceId: string, operation?: string) =>
    post<SyncJob>(`/cdp/audience-sync/audiences/${audienceId}/sync`, { operation }),
  getSyncHistory: (audienceId: string, limit?: number) =>
    get<SyncHistoryResponse>(`/cdp/audience-sync/audiences/${audienceId}/history`, { limit }),
};

// =============================================================================
// Client-side event tracker
// =============================================================================

export interface Tracker {
  track: (eventName: string, properties?: Record<string, unknown>) => Promise<void>;
  identify: (identifiers: IdentifierInput[]) => void;
  page: (name?: string, properties?: Record<string, unknown>) => Promise<void>;
}

/**
 * Create a lightweight client-side tracker that sends events into the CDP
 * ingestion endpoint. Identify once, then track events.
 */
export function createTracker(sourceId?: string): Tracker {
  let currentIdentifiers: IdentifierInput[] = [];

  const send = async (eventName: string, properties?: Record<string, unknown>) => {
    try {
      await cdpApi.ingestEvent({
        event_name: eventName,
        identifiers: currentIdentifiers,
        properties,
        timestamp: new Date().toISOString(),
        source_id: sourceId,
      });
    } catch {
      // Tracking must never break the app
    }
  };

  return {
    identify: (identifiers: IdentifierInput[]) => {
      currentIdentifiers = identifiers;
    },
    track: (eventName, properties) => send(eventName, properties),
    page: (name, properties) =>
      send('PageView', { page_name: name ?? document.title, ...properties }),
  };
}

// =============================================================================
// Hooks - Health
// =============================================================================

export function useCDPHealth() {
  return useQuery({
    queryKey: cdpQueryKeys.health(),
    queryFn: cdpApi.getHealth,
    staleTime: 60 * 1000,
  });
}

// =============================================================================
// Hooks - Profiles
// =============================================================================

export function useCDPProfile(profileId: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: cdpQueryKeys.profile(profileId),
    queryFn: () => cdpApi.getProfile(profileId),
    enabled: (options?.enabled ?? true) && !!profileId,
  });
}

export function useCDPProfileLookup(
  identifierType: IdentifierType,
  value: string,
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: [...cdpQueryKeys.profiles(), 'lookup', identifierType, value],
    queryFn: () => cdpApi.lookupProfile(identifierType, value),
    enabled: (options?.enabled ?? true) && !!value,
  });
}

export function useDeleteProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (profileId: string) => cdpApi.deleteProfile(profileId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cdpQueryKeys.profiles() });
    },
  });
}

export function useSearchProfiles(params: ProfileSearchParams) {
  return useQuery({
    queryKey: cdpQueryKeys.profileSearch(params),
    queryFn: () => cdpApi.searchProfiles(params),
    staleTime: 30 * 1000,
  });
}

export function useSearchProfilesMutation() {
  return useMutation({
    mutationFn: (params: ProfileSearchParams) => cdpApi.searchProfiles(params),
  });
}

export function useProfileStatistics() {
  return useQuery({
    queryKey: cdpQueryKeys.profileStatistics(),
    queryFn: cdpApi.getProfileStatistics,
    staleTime: 60 * 1000,
  });
}

// =============================================================================
// Hooks - Sources
// =============================================================================

export function useCDPSources() {
  return useQuery({
    queryKey: cdpQueryKeys.sources(),
    queryFn: cdpApi.getSources,
    staleTime: 5 * 60 * 1000,
  });
}

export function useCreateSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (source: SourceCreate) => cdpApi.createSource(source),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cdpQueryKeys.sources() });
    },
  });
}

// =============================================================================
// Hooks - Events
// =============================================================================

export function useIngestEvent() {
  return useMutation({
    mutationFn: (event: EventInput) => cdpApi.ingestEvent(event),
  });
}

export function useIngestEvents() {
  return useMutation({
    mutationFn: (batch: EventBatchInput) => cdpApi.ingestEvents(batch),
  });
}

export function useEventStatistics(days?: number) {
  return useQuery({
    queryKey: cdpQueryKeys.eventStatistics(days),
    queryFn: () => cdpApi.getEventStatistics(days),
    staleTime: 60 * 1000,
  });
}

export function useEventTrends(days?: number) {
  return useQuery({
    queryKey: cdpQueryKeys.eventTrends(days),
    queryFn: () => cdpApi.getEventTrends(days),
    staleTime: 60 * 1000,
  });
}

// =============================================================================
// Hooks - Anomalies
// =============================================================================

export function useEventAnomalies(params?: { window_days?: number; zscore_threshold?: number }) {
  return useQuery({
    queryKey: cdpQueryKeys.anomalies(params),
    queryFn: () => cdpApi.getEventAnomalies(params),
    staleTime: 60 * 1000,
  });
}

export function useAnomalySummary() {
  return useQuery({
    queryKey: cdpQueryKeys.anomalySummary(),
    queryFn: cdpApi.getAnomalySummary,
    staleTime: 60 * 1000,
  });
}

// =============================================================================
// Hooks - Webhooks
// =============================================================================

export function useCDPWebhooks() {
  return useQuery({
    queryKey: cdpQueryKeys.webhooks(),
    queryFn: cdpApi.getWebhooks,
    staleTime: 30 * 1000,
  });
}

export function useCDPWebhook(webhookId: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: cdpQueryKeys.webhook(webhookId),
    queryFn: () => cdpApi.getWebhook(webhookId),
    enabled: (options?.enabled ?? true) && !!webhookId,
  });
}

export function useCreateWebhook() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (webhook: WebhookCreate) => cdpApi.createWebhook(webhook),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cdpQueryKeys.webhooks() });
    },
  });
}

export function useUpdateWebhook() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ webhookId, update }: { webhookId: string; update: WebhookUpdate }) =>
      cdpApi.updateWebhook(webhookId, update),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cdpQueryKeys.webhooks() });
    },
  });
}

export function useDeleteWebhook() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (webhookId: string) => cdpApi.deleteWebhook(webhookId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cdpQueryKeys.webhooks() });
    },
  });
}

export function useTestWebhook() {
  return useMutation({
    mutationFn: (webhookId: string) => cdpApi.testWebhook(webhookId),
  });
}

export function useRotateWebhookSecret() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (webhookId: string) => cdpApi.rotateWebhookSecret(webhookId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cdpQueryKeys.webhooks() });
    },
  });
}

// =============================================================================
// Hooks - Identity graph
// =============================================================================

export function useIdentityGraph(profileId: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: cdpQueryKeys.identityGraph(profileId),
    queryFn: () => cdpApi.getIdentityGraph(profileId),
    enabled: (options?.enabled ?? true) && !!profileId,
  });
}

export function useCanonicalIdentity(profileId: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: cdpQueryKeys.canonicalIdentity(profileId),
    queryFn: () => cdpApi.getCanonicalIdentity(profileId),
    enabled: (options?.enabled ?? true) && !!profileId,
  });
}

export function useIdentityLinks(profileId: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: cdpQueryKeys.identityLinks(profileId),
    queryFn: () => cdpApi.getIdentityLinks(profileId),
    enabled: (options?.enabled ?? true) && !!profileId,
  });
}

export function useProfileMergeHistory(profileId: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: cdpQueryKeys.profileMergeHistory(profileId),
    queryFn: () => cdpApi.getProfileMergeHistory(profileId),
    enabled: (options?.enabled ?? true) && !!profileId,
  });
}

export function useMergeHistory(params?: { limit?: number; offset?: number }) {
  return useQuery({
    queryKey: cdpQueryKeys.mergeHistory(params),
    queryFn: () => cdpApi.getMergeHistory(params),
    staleTime: 30 * 1000,
  });
}

export function useMergeProfiles() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { source_profile_id: string; target_profile_id: string }) =>
      cdpApi.mergeProfiles(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cdpQueryKeys.all });
    },
  });
}

// =============================================================================
// Hooks - Segments
// =============================================================================

export function useSegments(params?: { status?: string; limit?: number; offset?: number }) {
  return useQuery({
    queryKey: cdpQueryKeys.segments(params),
    queryFn: () => cdpApi.getSegments(params),
    staleTime: 30 * 1000,
  });
}

export function useSegment(segmentId: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: cdpQueryKeys.segment(segmentId),
    queryFn: () => cdpApi.getSegment(segmentId),
    enabled: (options?.enabled ?? true) && !!segmentId,
  });
}

export function useCreateSegment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (segment: SegmentCreate) => cdpApi.createSegment(segment),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [...cdpQueryKeys.all, 'segments'] });
    },
  });
}

export function useUpdateSegment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ segmentId, update }: { segmentId: string; update: SegmentUpdate }) =>
      cdpApi.updateSegment(segmentId, update),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [...cdpQueryKeys.all, 'segments'] });
    },
  });
}

export function useDeleteSegment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (segmentId: string) => cdpApi.deleteSegment(segmentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [...cdpQueryKeys.all, 'segments'] });
    },
  });
}

export function useComputeSegment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (segmentId: string) => cdpApi.computeSegment(segmentId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [...cdpQueryKeys.all, 'segments'] });
    },
  });
}

export function usePreviewSegment() {
  return useMutation({
    mutationFn: (payload: { rules: SegmentRules; limit?: number }) =>
      cdpApi.previewSegment(payload),
  });
}

export function useSegmentProfiles(
  segmentId: string,
  params?: { limit?: number; offset?: number },
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: cdpQueryKeys.segmentProfiles(segmentId, params),
    queryFn: () => cdpApi.getSegmentProfiles(segmentId, params),
    enabled: (options?.enabled ?? true) && !!segmentId,
  });
}

export function useProfileSegments(profileId: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: cdpQueryKeys.profileSegments(profileId),
    queryFn: () => cdpApi.getProfileSegments(profileId),
    enabled: (options?.enabled ?? true) && !!profileId,
  });
}

// =============================================================================
// Hooks - Computed traits
// =============================================================================

export function useComputedTraits(params?: { active_only?: boolean }) {
  return useQuery({
    queryKey: cdpQueryKeys.computedTraits(params),
    queryFn: () => cdpApi.getComputedTraits(params),
    staleTime: 60 * 1000,
  });
}

export function useComputedTrait(traitId: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: cdpQueryKeys.computedTrait(traitId),
    queryFn: () => cdpApi.getComputedTrait(traitId),
    enabled: (options?.enabled ?? true) && !!traitId,
  });
}

export function useCreateComputedTrait() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (trait: ComputedTraitCreate) => cdpApi.createComputedTrait(trait),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [...cdpQueryKeys.all, 'computed-traits'] });
    },
  });
}

export function useDeleteComputedTrait() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (traitId: string) => cdpApi.deleteComputedTrait(traitId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [...cdpQueryKeys.all, 'computed-traits'] });
    },
  });
}

export function useComputeAllTraits() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => cdpApi.computeAllTraits(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [...cdpQueryKeys.all, 'computed-traits'] });
    },
  });
}

// =============================================================================
// Hooks - RFM
// =============================================================================

export function useProfileRFM(profileId: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: cdpQueryKeys.profileRFM(profileId),
    queryFn: () => cdpApi.getProfileRFM(profileId),
    enabled: (options?.enabled ?? true) && !!profileId,
  });
}

export function useComputeRFMBatch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (params?: { analysis_window_days?: number }) => cdpApi.computeRFMBatch(params),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cdpQueryKeys.rfmSummary() });
    },
  });
}

export function useRFMSummary() {
  return useQuery({
    queryKey: cdpQueryKeys.rfmSummary(),
    queryFn: cdpApi.getRFMSummary,
    staleTime: 60 * 1000,
  });
}

// =============================================================================
// Hooks - Funnels
// =============================================================================

export function useFunnels() {
  return useQuery({
    queryKey: cdpQueryKeys.funnels(),
    queryFn: cdpApi.getFunnels,
    staleTime: 30 * 1000,
  });
}

export function useFunnel(funnelId: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: cdpQueryKeys.funnel(funnelId),
    queryFn: () => cdpApi.getFunnel(funnelId),
    enabled: (options?.enabled ?? true) && !!funnelId,
  });
}

export function useCreateFunnel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (funnel: FunnelCreate) => cdpApi.createFunnel(funnel),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cdpQueryKeys.funnels() });
    },
  });
}

export function useUpdateFunnel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ funnelId, update }: { funnelId: string; update: FunnelUpdate }) =>
      cdpApi.updateFunnel(funnelId, update),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cdpQueryKeys.funnels() });
    },
  });
}

export function useDeleteFunnel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (funnelId: string) => cdpApi.deleteFunnel(funnelId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cdpQueryKeys.funnels() });
    },
  });
}

export function useComputeFunnel() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (funnelId: string) => cdpApi.computeFunnel(funnelId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: cdpQueryKeys.funnels() });
    },
  });
}

export function useAnalyzeFunnel() {
  return useMutation({
    mutationFn: ({
      funnelId,
      params,
    }: {
      funnelId: string;
      params?: { start_date?: string; end_date?: string };
    }) => cdpApi.analyzeFunnel(funnelId, params),
  });
}

export function useFunnelDropOffs(
  funnelId: string,
  step: number,
  params?: { limit?: number },
  options?: { enabled?: boolean }
) {
  return useQuery({
    queryKey: cdpQueryKeys.funnelDropOffs(funnelId, step, params),
    queryFn: () => cdpApi.getFunnelDropOffs(funnelId, step, params),
    enabled: (options?.enabled ?? true) && !!funnelId && step > 0,
  });
}

export function useProfileFunnelJourneys(profileId: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: cdpQueryKeys.profileFunnelJourneys(profileId),
    queryFn: () => cdpApi.getProfileFunnelJourneys(profileId),
    enabled: (options?.enabled ?? true) && !!profileId,
  });
}

// =============================================================================
// Hooks - Audience export & sync
// =============================================================================

export function useExportAudience() {
  return useMutation({
    mutationFn: (params: AudienceExportParams) => cdpApi.exportAudience(params),
  });
}

export function useConnectedPlatforms() {
  return useQuery({
    queryKey: cdpQueryKeys.connectedPlatforms(),
    queryFn: cdpApi.getConnectedPlatforms,
    staleTime: 5 * 60 * 1000,
  });
}

export function usePlatformAudiences(params?: { platform?: SyncPlatform }) {
  return useQuery({
    queryKey: cdpQueryKeys.platformAudiences(params),
    queryFn: () => cdpApi.getPlatformAudiences(params),
    staleTime: 30 * 1000,
  });
}

export function useCreatePlatformAudience() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (audience: PlatformAudienceCreate) => cdpApi.createPlatformAudience(audience),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [...cdpQueryKeys.all, 'audience-sync'] });
    },
  });
}

export function useDeletePlatformAudience() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      audienceId,
      deleteFromPlatform,
    }: {
      audienceId: string;
      deleteFromPlatform?: boolean;
    }) => cdpApi.deletePlatformAudience(audienceId, deleteFromPlatform),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [...cdpQueryKeys.all, 'audience-sync'] });
    },
  });
}

export function useTriggerSync() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ audienceId, operation }: { audienceId: string; operation?: string }) =>
      cdpApi.triggerSync(audienceId, operation),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [...cdpQueryKeys.all, 'audience-sync'] });
    },
  });
}

export function useSyncHistory(audienceId: string, limit?: number) {
  return useQuery({
    queryKey: cdpQueryKeys.syncHistory(audienceId, limit),
    queryFn: () => cdpApi.getSyncHistory(audienceId, limit),
    enabled: !!audienceId,
  });
}
