/**
 * Stratum AI - Measurement & Verification API
 *
 * Google Analytics 4 (read-only revenue/conversion baseline) and
 * Google Tag Manager (tag deployment for Meta Pixel / CAPI and the
 * Stratum snippet). These are MEASUREMENT integrations only: Stratum AI
 * never reads or acts on non-Meta ad campaigns through them.
 *
 * Endpoints live under /api/v1/integrations/measurement/... (the axios
 * client already carries the /api/v1 prefix in its baseURL). Every route
 * requires the Bearer token attached by the shared client; the backend derives
 * the tenant from the authenticated user, never from the X-Tenant-ID header.
 * Saving, syncing, verifying or disconnecting requires an admin/manager role.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient, ApiResponse } from './client';

// =============================================================================
// Types (mirror app.schemas.measurement 1:1, snake_case)
// =============================================================================

export type MeasurementConnectionStatus = 'connected' | 'error' | 'disconnected';

/** Read-only OAuth scope the GA4 service account is limited to. */
export const GA4_READONLY_SCOPE = 'https://www.googleapis.com/auth/analytics.readonly';

// ----------------------------------------------------------------------------
// GA4
// ----------------------------------------------------------------------------

export interface GA4Config {
  configured: true;
  property_id: string;
  measurement_id: string | null;
  service_account_email: string | null;
  service_account_fingerprint: string | null;
  has_credentials: boolean;
  conversion_event_names: string[];
  status: MeasurementConnectionStatus;
  is_active: boolean;
  last_verified_at: string | null;
  last_verify_success: boolean | null;
  last_verify_message: string | null;
  last_sync_at: string | null;
  last_sync_rows: number | null;
  last_error: string | null;
  scope: 'https://www.googleapis.com/auth/analytics.readonly';
  access: 'read_only';
  created_at: string;
  updated_at: string;
}

export interface GA4ConfigPayload {
  property_id: string;
  measurement_id?: string | null;
  /** Never echoed back by the API; omit to keep the stored credential. */
  service_account_json?: string | null;
  conversion_event_names?: string[];
  is_active?: boolean;
}

export interface GA4Status {
  configured: boolean;
  status: MeasurementConnectionStatus;
  property_id?: string | null;
  last_sync_at?: string | null;
  last_verified_at?: string | null;
  last_error?: string | null;
  access: 'read_only';
}

export interface GA4TestPayload {
  property_id?: string;
  service_account_json?: string;
}

export interface GA4TestResult {
  success: boolean;
  message: string;
  property_id: string | null;
  sessions_last_7d?: number | null;
  conversions_last_7d?: number | null;
  revenue_last_7d?: number | null;
}

export interface GA4SyncPayload {
  /** 1-90 */
  lookback_days?: number;
  backfill?: boolean;
}

export interface GA4SyncResult {
  configured: boolean;
  success: boolean;
  rows_upserted: number;
  start_date: string | null;
  end_date: string | null;
  message: string;
}

export interface GA4DailyPoint {
  date: string;
  sessions: number;
  conversions: number;
  revenue: number;
}

export interface GA4Baseline {
  start_date: string;
  end_date: string;
  meta_only: boolean;
  sessions: number;
  conversions: number;
  revenue: number;
  total_revenue: number;
  days_with_data: number;
  last_date: string | null;
  daily: GA4DailyPoint[];
}

// ----------------------------------------------------------------------------
// GTM
// ----------------------------------------------------------------------------

export interface GTMConfig {
  configured: true;
  web_container_id: string | null;
  server_container_url: string | null;
  server_container_id: string | null;
  has_preview_header: boolean;
  meta_pixel_id: string | null;
  deploy_meta_pixel: boolean;
  deploy_meta_capi: boolean;
  deploy_stratum_snippet: boolean;
  status: MeasurementConnectionStatus;
  is_active: boolean;
  cdp_source_id: string | null;
  cdp_source_key: string | null;
  last_verified_at: string | null;
  last_verify_success: boolean | null;
  last_verify_message: string | null;
  last_error: string | null;
  role: 'tag_deployment';
  created_at: string;
  updated_at: string;
}

export interface GTMConfigPayload {
  web_container_id?: string | null;
  server_container_url?: string | null;
  server_container_id?: string | null;
  /** Never echoed back by the API; omit to keep the stored header. */
  preview_header?: string | null;
  meta_pixel_id?: string | null;
  deploy_meta_pixel?: boolean;
  deploy_meta_capi?: boolean;
  deploy_stratum_snippet?: boolean;
  is_active?: boolean;
}

export interface GTMStatus {
  configured: boolean;
  status: MeasurementConnectionStatus;
  web_container_id?: string | null;
  server_container_url?: string | null;
  last_verified_at?: string | null;
  role: 'tag_deployment';
}

export interface GTMVerifyResult {
  success: boolean;
  message: string;
  web_container_ok: boolean | null;
  server_container_ok: boolean | null;
  checked_at: string;
}

/** Keys as produced by app.services.measurement.gtm_service.build_snippets */
export interface GTMSgtmConfig {
  server_container_url: string | null;
  transport_url: string | null;
  stratum_ingest_url: string;
  source_key: string | null;
  source_header: string;
  cdp_source_type: string;
  meta_capi_client: string;
  meta_pixel_id: string | null;
  role: string;
  [key: string]: unknown;
}

export interface GTMSnippets {
  head_snippet: string;
  body_snippet: string;
  stratum_snippet: string;
  sgtm_config: GTMSgtmConfig;
}

// ----------------------------------------------------------------------------
// Combined status
// ----------------------------------------------------------------------------

export interface MeasurementStatusSummary {
  ga4: GA4Status;
  gtm: GTMStatus;
}

// =============================================================================
// Query keys
// =============================================================================

export const measurementKeys = {
  all: ['measurement'] as const,
  status: ['measurement', 'status'] as const,
  ga4Config: ['measurement', 'ga4', 'config'] as const,
  ga4Baseline: (startDate?: string, endDate?: string, metaOnly?: boolean) =>
    ['measurement', 'ga4', 'baseline', startDate, endDate, metaOnly] as const,
  gtmConfig: ['measurement', 'gtm', 'config'] as const,
  gtmSnippets: ['measurement', 'gtm', 'snippets'] as const,
};

// =============================================================================
// API functions
// =============================================================================

const BASE = '/integrations/measurement';

export const measurementApi = {
  /**
   * Combined GA4 + GTM status summary for the current tenant
   */
  getStatus: async (): Promise<MeasurementStatusSummary> => {
    const response = await apiClient.get<ApiResponse<MeasurementStatusSummary>>(`${BASE}/status`);
    return response.data.data;
  },

  // --------------------------------------------------------------------------
  // GA4 (read-only baseline)
  // --------------------------------------------------------------------------

  /**
   * Get the GA4 configuration (null when not configured). Secrets are never returned.
   */
  getGA4Config: async (): Promise<GA4Config | null> => {
    const response = await apiClient.get<ApiResponse<GA4Config | null>>(`${BASE}/ga4`);
    return response.data.data ?? null;
  },

  /**
   * Create or update the GA4 configuration
   */
  saveGA4Config: async (payload: GA4ConfigPayload): Promise<GA4Config> => {
    const response = await apiClient.put<ApiResponse<GA4Config>>(`${BASE}/ga4`, payload);
    return response.data.data;
  },

  /**
   * Test the GA4 Data API connection (uses stored credentials when the body is empty)
   */
  testGA4Connection: async (payload: GA4TestPayload = {}): Promise<GA4TestResult> => {
    const response = await apiClient.post<ApiResponse<GA4TestResult>>(
      `${BASE}/ga4/test-connection`,
      payload
    );
    return response.data.data;
  },

  /**
   * Trigger a manual GA4 daily-baseline sync
   */
  syncGA4: async (payload: GA4SyncPayload = {}): Promise<GA4SyncResult> => {
    const response = await apiClient.post<ApiResponse<GA4SyncResult>>(`${BASE}/ga4/sync`, payload);
    return response.data.data;
  },

  /**
   * Read the ingested GA4 baseline for a date range
   */
  getGA4Baseline: async (
    startDate?: string,
    endDate?: string,
    metaOnly: boolean = false
  ): Promise<GA4Baseline> => {
    const response = await apiClient.get<ApiResponse<GA4Baseline>>(`${BASE}/ga4/baseline`, {
      params: {
        ...(startDate ? { start_date: startDate } : {}),
        ...(endDate ? { end_date: endDate } : {}),
        meta_only: metaOnly,
      },
    });
    return response.data.data;
  },

  /**
   * Remove the GA4 configuration and stored credentials
   */
  disconnectGA4: async (): Promise<void> => {
    await apiClient.delete(`${BASE}/ga4`);
  },

  // --------------------------------------------------------------------------
  // GTM (tag deployment)
  // --------------------------------------------------------------------------

  /**
   * Get the GTM configuration (null when not configured). Secrets are never returned.
   */
  getGTMConfig: async (): Promise<GTMConfig | null> => {
    const response = await apiClient.get<ApiResponse<GTMConfig | null>>(`${BASE}/gtm`);
    return response.data.data ?? null;
  },

  /**
   * Create or update the GTM configuration
   */
  saveGTMConfig: async (payload: GTMConfigPayload): Promise<GTMConfig> => {
    const response = await apiClient.put<ApiResponse<GTMConfig>>(`${BASE}/gtm`, payload);
    return response.data.data;
  },

  /**
   * Verify that the web and server-side containers are reachable
   */
  verifyGTM: async (): Promise<GTMVerifyResult> => {
    const response = await apiClient.post<ApiResponse<GTMVerifyResult>>(`${BASE}/gtm/verify`);
    return response.data.data;
  },

  /**
   * Get the head/body/Stratum snippets and the server-side GTM config
   */
  getGTMSnippets: async (): Promise<GTMSnippets> => {
    const response = await apiClient.get<ApiResponse<GTMSnippets>>(`${BASE}/gtm/snippets`);
    return response.data.data;
  },

  /**
   * Remove the GTM configuration
   */
  disconnectGTM: async (): Promise<void> => {
    await apiClient.delete(`${BASE}/gtm`);
  },
};

// =============================================================================
// React Query hooks
// =============================================================================

export function useMeasurementStatus() {
  return useQuery({
    queryKey: measurementKeys.status,
    queryFn: measurementApi.getStatus,
    staleTime: 60 * 1000,
  });
}

// ----------------------------------------------------------------------------
// GA4
// ----------------------------------------------------------------------------

export function useGA4Config() {
  return useQuery({
    queryKey: measurementKeys.ga4Config,
    queryFn: measurementApi.getGA4Config,
    staleTime: 60 * 1000,
  });
}

export function useSaveGA4Config() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: measurementApi.saveGA4Config,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: measurementKeys.all });
    },
  });
}

export function useTestGA4Connection() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: GA4TestPayload = {}) => measurementApi.testGA4Connection(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: measurementKeys.all });
    },
  });
}

export function useSyncGA4() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (payload: GA4SyncPayload = {}) => measurementApi.syncGA4(payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: measurementKeys.all });
    },
  });
}

export function useGA4Baseline(startDate?: string, endDate?: string, metaOnly: boolean = false) {
  return useQuery({
    queryKey: measurementKeys.ga4Baseline(startDate, endDate, metaOnly),
    queryFn: () => measurementApi.getGA4Baseline(startDate, endDate, metaOnly),
    staleTime: 5 * 60 * 1000,
  });
}

export function useDisconnectGA4() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: measurementApi.disconnectGA4,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: measurementKeys.all });
    },
  });
}

// ----------------------------------------------------------------------------
// GTM
// ----------------------------------------------------------------------------

export function useGTMConfig() {
  return useQuery({
    queryKey: measurementKeys.gtmConfig,
    queryFn: measurementApi.getGTMConfig,
    staleTime: 60 * 1000,
  });
}

export function useSaveGTMConfig() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: measurementApi.saveGTMConfig,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: measurementKeys.all });
    },
  });
}

export function useVerifyGTM() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: measurementApi.verifyGTM,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: measurementKeys.all });
    },
  });
}

export function useGTMSnippets(enabled: boolean = true) {
  return useQuery({
    queryKey: measurementKeys.gtmSnippets,
    queryFn: measurementApi.getGTMSnippets,
    enabled,
    staleTime: 5 * 60 * 1000,
  });
}

export function useDisconnectGTM() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: measurementApi.disconnectGTM,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: measurementKeys.all });
    },
  });
}
