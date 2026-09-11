/**
 * Stratum AI - API Hooks
 *
 * Centralized barrel file that re-exports all API hooks
 * and adds new hooks for superadmin endpoints.
 */

import { useQuery } from '@tanstack/react-query';
import { apiClient, ApiResponse, PaginatedResponse } from './client';

// =============================================================================
// Re-exports from other API files
// =============================================================================

// EMQ V2 hooks
export {
  useEmqScore,
  useConfidence,
  useEmqPlaybook,
  useUpdatePlaybookItem,
  useEmqIncidents,
  useEmqImpact,
  useEmqVolatility,
  useAutopilotState,
  useUpdateAutopilotMode,
  useEmqBenchmarks,
  useEmqPortfolio,
} from './emqV2';

// User & tenant administration hooks (`/users`, `/tenants`)
export {
  useUsers,
  useInviteUser,
  useUpdateUser,
  useDeleteUser,
  useTenants,
  useTenant,
  useTenantUsers,
} from './admin';

// Account manager portfolio hooks
export { useTenantPortfolio, portfolioApi } from './portfolio';
export type {
  TenantPortfolioRow,
  TenantPortfolioResponse,
  TenantPortfolioParams,
} from './portfolio';

// Superadmin Analytics hooks
export {
  usePlatformOverview,
  useTenantProfitability,
  useSignalHealthTrends,
  useActionsAnalytics,
} from './superadminAnalytics';

// Competitors hooks
export {
  useCompetitors,
  useCompetitor,
  useCreateCompetitor,
  useUpdateCompetitor,
  useDeleteCompetitor,
  useShareOfVoice,
  useCompetitorKeywords,
  useCompetitorMetrics,
  useRefreshCompetitor,
} from './competitors';

// Predictions hooks
export {
  useLivePredictions,
  useCampaignPredictions,
  usePredictionAlerts,
  useMarkAlertRead,
  useRefreshPredictions,
  useBudgetOptimization,
  useApplyBudgetOptimization,
  useScenarios,
  useScenario,
  useCreateScenario,
  useDeleteScenario,
} from './predictions';

// Insights hooks
export { useInsights, useRecommendations, useAnomalies, useKPIs } from './insights';

// Trust Layer hooks
export {
  useSignalHealth,
  useSignalHealthHistory,
  useSignalHealthHistoryDetailed,
  useAttributionVariance,
  useTrustStatus,
  useTrustGateAuditLogs,
} from './trustLayer';

// Autopilot hooks
export {
  useAutopilotStatus,
  useAutopilotActions,
  useActionsSummary,
  useAutopilotAction,
  useQueueAction,
  useApproveAction,
  useApproveAllActions,
  useDismissAction,
  useDryRunAction,
} from './autopilot';

// Campaigns hooks
export {
  useCampaigns,
  useCampaign,
  useCreateCampaign,
  useUpdateCampaign,
  useDeleteCampaign,
  useCampaignMetrics,
  useSyncCampaign,
  useBulkUpdateCampaignStatus,
  usePauseCampaign,
  useActivateCampaign,
  useDiscoverCampaigns,
} from './campaigns';

// Assets hooks
export {
  useAssets,
  useAsset,
  useCreateAsset,
  useUploadAsset,
  useUpdateAsset,
  useDeleteAsset,
  useAssetFolders,
  useCreateFolder,
  useFatiguedAssets,
  useCalculateFatigue,
  useBulkArchiveAssets,
} from './assets';

// Rules hooks
export {
  useRules,
  useRule,
  useCreateRule,
  useUpdateRule,
  useDeleteRule,
  useToggleRule,
  useExecuteRule,
  useRuleExecutions,
  useRuleTemplates,
  useValidateConditions,
} from './rules';

// Feature flags hooks
export {
  useFeatureFlags,
  useUpdateFeatureFlags,
  useSuperadminFeatureFlags,
  useSuperadminUpdateFeatureFlags,
  useSuperadminResetFeatureFlags,
} from './featureFlags';

// GDPR hooks (exclude useAuditLogs since we have our own)
export {
  useExportData,
  useExportStatus,
  useExportHistory,
  useAnonymizeData,
  useAnonymizationStatus,
  useConsentRecords,
  useUpdateConsent,
  useDataCategories,
  useRequestDeletion,
  useCancelDeletion,
} from './gdpr';

// CRM/HubSpot Integration hooks
export {
  useCRMConnections,
  useCRMConnection,
  useConnectHubSpot,
  useDisconnectCRM,
  useTriggerCRMSync,
  useCRMContacts,
  useCRMContact,
  useContactJourney,
  useCRMDeals,
  useCRMDeal,
  usePipelineMetrics,
  usePipelineSummary,
  useWritebackConfig,
  useUpdateWritebackConfig,
  useWritebackHistory,
  useRetryWriteback,
} from './crm';

// Measurement & Verification hooks (GA4 read-only baseline + GTM tag deployment)
export {
  useMeasurementStatus,
  useGA4Config,
  useSaveGA4Config,
  useTestGA4Connection,
  useSyncGA4,
  useGA4Baseline,
  useDisconnectGA4,
  useGTMConfig,
  useSaveGTMConfig,
  useVerifyGTM,
  useGTMSnippets,
  useDisconnectGTM,
} from './measurement';

// Pacing & Forecasting hooks
export {
  useTargets,
  useTarget,
  useCreateTarget,
  useUpdateTarget,
  useDeleteTarget,
  usePacingStatus,
  useAllPacingStatus,
  usePacingSummary,
  useDailyKPIs,
  useForecast,
  useMetricForecast,
  useGenerateForecast,
  usePacingAlerts,
  usePacingAlert,
  useAcknowledgeAlert,
  useResolveAlert,
  useDismissAlert,
  useAlertStats,
} from './pacing';

// Profit ROAS hooks
export {
  useProducts,
  useProduct,
  useCreateProduct,
  useUpdateProduct,
  useDeleteProduct,
  useImportProducts,
  useProductMargins,
  useSetProductMargin,
  useMarginRules,
  useMarginRule,
  useCreateMarginRule,
  useUpdateMarginRule,
  useDeleteMarginRule,
  useUploadCOGS,
  useCOGSUploads,
  useCOGSUpload,
  useDailyProfitMetrics,
  useProfitSummary,
  useGenerateProfitReport,
  useProfitReports,
  useProfitReport,
  useTrueROAS,
} from './profit';

// Attribution hooks
export {
  useAttributionSummary,
  useDailyAttributedRevenue,
  useChannelTransitions,
  useTopConversionPaths,
  useAssistedConversions,
  useTimeLagReport,
  useTrainedModels,
  useTrainedModel,
  useTrainMarkovModel,
  useTrainShapleyModel,
  useCompareModels,
  useArchiveModel,
  useActivateModel,
} from './attribution';

// Reporting hooks
export {
  useReportTemplates,
  useReportTemplate,
  useCreateReportTemplate,
  useUpdateReportTemplate,
  useDeleteReportTemplate,
  useReportSchedules,
  useReportSchedule,
  useCreateReportSchedule,
  useUpdateReportSchedule,
  useDeleteReportSchedule,
  usePauseReportSchedule,
  useResumeReportSchedule,
  useGenerateReport,
  useReportExecutions,
  useReportExecution,
  useDeliveryStatus,
  useDeliveryChannelConfigs,
  useUpdateDeliveryChannelConfig,
  useVerifyDeliveryChannel,
} from './reporting';

// Tenant Dashboard hooks
export { useUpdateTenantSettings, useTestSlackWebhook } from './hooks/useTenantDashboard';

// Onboarding hooks
export {
  useOnboardingStatus,
  useOnboardingCheck,
  useSubmitBusinessProfile,
  useSubmitPlatformSelection,
  useSubmitGoalsSetup,
  useSubmitAutomationPreferences,
  useSubmitTrustGateConfig,
  useSkipOnboarding,
  useResetOnboarding,
} from './onboarding';

// Dashboard hooks
export {
  useDashboardOverview,
  useDashboardCampaigns,
  useDashboardRecommendations,
  useApproveRecommendation,
  useRejectRecommendation,
  useDashboardActivity,
  useDashboardQuickActions,
  useDashboardSignalHealth,
} from './dashboard';

// CDP (Customer Data Platform) hooks
export {
  // API client and types
  cdpApi,
  cdpQueryKeys,
  createTracker,
  // Profile hooks
  useCDPProfile,
  useCDPProfileLookup,
  useDeleteProfile,
  useSearchProfiles,
  useSearchProfilesMutation,
  useProfileStatistics,
  // Source hooks
  useCDPSources,
  useCreateSource,
  // Event hooks
  useIngestEvents,
  useIngestEvent,
  useEventStatistics,
  useEventTrends,
  // Health hook
  useCDPHealth,
  // Webhook hooks
  useCDPWebhooks,
  useCDPWebhook,
  useCreateWebhook,
  useUpdateWebhook,
  useDeleteWebhook,
  useTestWebhook,
  useRotateWebhookSecret,
  // Anomaly hooks
  useEventAnomalies,
  useAnomalySummary,
  // Identity Graph hooks
  useIdentityGraph,
  useCanonicalIdentity,
  useProfileMergeHistory,
  useMergeHistory,
  useIdentityLinks,
  useMergeProfiles,
  // Segment hooks
  useSegments,
  useSegment,
  useCreateSegment,
  useUpdateSegment,
  useDeleteSegment,
  useComputeSegment,
  usePreviewSegment,
  useSegmentProfiles,
  useProfileSegments,
  // Computed Traits hooks
  useComputedTraits,
  useComputedTrait,
  useCreateComputedTrait,
  useDeleteComputedTrait,
  useComputeAllTraits,
  // RFM hooks
  useProfileRFM,
  useComputeRFMBatch,
  useRFMSummary,
  // Funnel hooks
  useFunnels,
  useFunnel,
  useCreateFunnel,
  useUpdateFunnel,
  useDeleteFunnel,
  useComputeFunnel,
  useAnalyzeFunnel,
  useFunnelDropOffs,
  useProfileFunnelJourneys,
  // Export hooks
  useExportAudience,
  // Type exports
  type IdentifierType,
  type IdentifierInput,
  type Identifier,
  type EventInput,
  type EventBatchInput,
  type EventBatchResponse,
  type CDPEvent,
  type CDPProfile,
  type ProfileListResponse,
  type CDPSource,
  type SourceCreate,
  type SourceListResponse,
  type CDPWebhook,
  type WebhookCreate,
  type WebhookUpdate,
  type CDPSegment,
  type SegmentCreate,
  type SegmentUpdate,
  type SegmentRules,
  type CDPComputedTrait,
  type ComputedTraitCreate,
  type RFMScores,
  type RFMSegment,
  type CDPFunnel,
  type FunnelCreate,
  type FunnelUpdate,
  type FunnelStep,
  type ProfileSearchParams,
  type AudienceExportParams,
} from './cdp';

// =============================================================================
// Tenant Overview Hooks (for tenant dashboard)
// =============================================================================

/**
 * Get tenant overview data (wraps insights for common use case)
 */
export function useTenantOverview(tenantId: number) {
  return useQuery({
    queryKey: ['tenant', 'overview', tenantId],
    queryFn: async () => {
      const response = await apiClient.get<
        ApiResponse<{
          kpis: { total_spend: number; total_revenue: number; roas: number; cpa: number };
          signal_health_status: string;
          autopilot_status: string;
          recent_actions: number;
        }>
      >(`/analytics/tenant-overview`);
      return response.data.data;
    },
    staleTime: 60 * 1000,
    refetchInterval: 5 * 60 * 1000,
  });
}

/**
 * Get tenant recommendations
 */
export function useTenantRecommendations(tenantId: number, options?: { limit?: number }) {
  return useQuery({
    queryKey: ['tenant', 'recommendations', tenantId, options],
    queryFn: async () => {
      const params = options?.limit ? `?limit=${options.limit}` : '';
      const response = await apiClient.get<
        ApiResponse<{
          recommendations: Array<{
            id: string;
            type: string;
            priority: string;
            title: string;
            description: string;
            expected_impact: number;
          }>;
          total: number;
        }>
      >(`/insights/tenant/${tenantId}/recommendations${params}`);
      return response.data.data;
    },
    staleTime: 60 * 1000,
  });
}

// =============================================================================
// Superadmin Dashboard Types
// =============================================================================

export interface RevenueMetrics {
  mrr: number;
  arr: number;
  nrr: number;
  mrrGrowth: number;
  arrGrowth: number;
  churnRate: number;
}

export interface RevenueBreakdown {
  plan: string;
  tenantCount: number;
  mrr: number;
  percentage: number;
}

export interface TenantPortfolioItem {
  id: number;
  name: string;
  plan: string;
  status: string;
  emqScore: number | null;
  budgetAtRisk: number;
  activeIncidents: number;
  monthlySpend: number;
  churnRisk: number;
  lastActivityAt: string | null;
}

export interface SystemHealthMetrics {
  overallStatus: 'healthy' | 'degraded' | 'down';
  services: Array<{
    name: string;
    status: 'healthy' | 'degraded' | 'down';
    uptime: number;
    latency: number;
    version: string;
  }>;
  connectors: Array<{
    platform: string;
    status: 'healthy' | 'degraded' | 'down';
    lastSync: string;
    errors: number;
    recordsProcessed: number;
  }>;
  queues: Array<{
    name: string;
    status: 'running' | 'paused' | 'stalled';
    pending: number;
    processing: number;
    completed: number;
    failed: number;
    avgProcessTime: number;
  }>;
  metrics: {
    cpu: number;
    memory: number;
    disk: number;
    network: number;
    activeConnections: number;
    requestsPerMinute: number;
    errorRate: number;
  };
}

export interface ChurnRisk {
  tenantId: number;
  tenantName: string;
  riskScore: number;
  riskLevel: 'low' | 'medium' | 'high' | 'critical';
  factors: string[];
  lastActivityAt: string | null;
  monthlySpend: number;
}

export interface AuditLogEntry {
  id: string;
  timestamp: string;
  action: string;
  details: string;
  userId: string;
  userName: string | null;
  tenantId: number | null;
  tenantName: string | null;
  ipAddress: string | null;
  userAgent: string | null;
  severity: 'info' | 'warning' | 'error' | 'critical';
  metadata: Record<string, unknown>;
}

export interface BillingPlan {
  id: string;
  name: string;
  /** Tier slug (free|starter|professional|enterprise) when known. */
  tier?: string | null;
  /** Monthly list price; null means custom pricing (enterprise). Billable prices live in Paddle. */
  price: number | null;
  features: string[];
  subscriberCount: number;
  mrr: number;
}

export type BillingInvoiceStatus = 'paid' | 'pending' | 'overdue' | 'failed';

export interface BillingInvoice {
  id: string;
  tenantId: number;
  tenantName: string;
  invoiceNumber: string | null;
  amount: number;
  currency: string;
  status: BillingInvoiceStatus;
  dueDate: string | null;
  paidAt: string | null;
  /** Paddle transaction id (txn_...) backing this invoice, when known. */
  paddleTransactionId?: string | null;
  /** Paddle-hosted invoice PDF URL, when available. */
  invoiceUrl?: string | null;
}

/** Paddle subscription lifecycle states. */
export type BillingSubscriptionStatus = 'active' | 'trialing' | 'past_due' | 'paused' | 'canceled';

export interface BillingSubscription {
  id: string;
  tenantId: number;
  tenantName: string;
  plan: string;
  planName: string | null;
  /** Paddle status; null for tenants without a Paddle subscription (free / admin-granted). */
  status: BillingSubscriptionStatus | null;
  mrr: number;
  startDate: string | null;
  nextBillingDate: string | null;
  cancelAtPeriodEnd: boolean;
  /** Paddle subscription id (sub_...). */
  paddleSubscriptionId?: string | null;
  /** Paddle customer id (ctm_...). */
  paddleCustomerId?: string | null;
}

export interface SuperadminDashboard {
  totalRevenue: number;
  mrrGrowth: number;
  activeTenants: number;
  atRiskTenants: number;
  totalBudgetAtRisk: number;
  systemStatus: 'healthy' | 'degraded' | 'down';
}

// =============================================================================
// Superadmin API Functions
// =============================================================================

type RawRecord = Record<string, unknown>;

const asString = (value: unknown): string | null =>
  typeof value === 'string' && value.length > 0 ? value : null;

const asNumber = (value: unknown, fallback = 0): number =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback;

/**
 * Superadmin billing endpoints return `{ <key>: [...], total }` envelopes. Accept that
 * envelope or a bare array and always hand back a list of raw rows.
 */
const extractList = (payload: unknown, key: string): RawRecord[] => {
  if (Array.isArray(payload)) return payload as RawRecord[];
  if (payload && typeof payload === 'object') {
    const list = (payload as RawRecord)[key];
    if (Array.isArray(list)) return list as RawRecord[];
  }
  return [];
};

const toPaginated = <T>(
  items: T[],
  payload: unknown,
  params?: { skip?: number; limit?: number }
): PaginatedResponse<T> => {
  const total =
    payload && typeof payload === 'object' && typeof (payload as RawRecord).total === 'number'
      ? ((payload as RawRecord).total as number)
      : items.length;
  return { items, total, skip: params?.skip ?? 0, limit: params?.limit ?? items.length };
};

/** Map a plan id like `professional_monthly` or a bare tier to its tier slug. */
const normalizePlanTier = (planId: string | null): string => {
  const value = (planId ?? '').toLowerCase();
  if (value.includes('enterprise')) return 'enterprise';
  if (value.includes('professional') || value.includes('pro')) return 'professional';
  if (value.includes('starter')) return 'starter';
  return 'free';
};

const mapBillingPlan = (row: RawRecord): BillingPlan => {
  const rawFeatures = row.features;
  const features = Array.isArray(rawFeatures)
    ? rawFeatures.map(String)
    : rawFeatures && typeof rawFeatures === 'object'
      ? Object.keys(rawFeatures as RawRecord)
      : [];
  return {
    id: String(row.id ?? row.tier ?? ''),
    name: asString(row.display_name ?? row.name) ?? String(row.id ?? ''),
    tier: asString(row.tier),
    price: typeof row.price === 'number' ? row.price : null,
    features,
    subscriberCount: asNumber(row.subscriber_count ?? row.subscriberCount),
    mrr: asNumber(row.mrr),
  };
};

const mapBillingInvoice = (row: RawRecord): BillingInvoice => ({
  id: String(row.id ?? ''),
  tenantId: asNumber(row.tenant_id ?? row.tenantId),
  tenantName: asString(row.tenant_name ?? row.tenantName) ?? '',
  invoiceNumber: asString(row.invoice_number ?? row.invoiceNumber),
  amount: asNumber(row.total ?? row.amount),
  currency: asString(row.currency) ?? 'USD',
  status: (asString(row.status) ?? 'pending') as BillingInvoiceStatus,
  dueDate: asString(row.due_date ?? row.dueDate),
  paidAt: asString(row.paid_at ?? row.paidAt),
  paddleTransactionId: asString(row.paddle_transaction_id ?? row.paddleTransactionId),
  invoiceUrl: asString(row.invoice_url ?? row.invoiceUrl),
});

const mapBillingSubscription = (row: RawRecord): BillingSubscription => {
  const planId = asString(row.plan_id ?? row.plan);
  return {
    id: String(row.id ?? ''),
    tenantId: asNumber(row.tenant_id ?? row.tenantId),
    tenantName: asString(row.tenant_name ?? row.tenantName) ?? '',
    plan: normalizePlanTier(planId),
    planName: asString(row.plan_name ?? row.planName),
    status: asString(row.status) as BillingSubscriptionStatus | null,
    mrr: asNumber(row.mrr),
    startDate: asString(row.current_period_start ?? row.startDate),
    nextBillingDate: asString(row.current_period_end ?? row.nextBillingDate),
    cancelAtPeriodEnd: Boolean(row.cancel_at_period_end ?? row.cancelAtPeriodEnd),
    paddleSubscriptionId: asString(row.paddle_subscription_id ?? row.paddleSubscriptionId),
    paddleCustomerId: asString(row.paddle_customer_id ?? row.paddleCustomerId),
  };
};

export const superadminApi = {
  // Dashboard
  getDashboard: async (): Promise<SuperadminDashboard> => {
    const response = await apiClient.get<ApiResponse<SuperadminDashboard>>('/superadmin/dashboard');
    return response.data.data;
  },

  // Revenue (backend returns snake_case percentages; normalize to the camelCase shape)
  getRevenue: async (): Promise<RevenueMetrics> => {
    const response = await apiClient.get<ApiResponse<RawRecord>>('/superadmin/revenue');
    const raw = response.data.data ?? {};
    return {
      mrr: asNumber(raw.mrr),
      arr: asNumber(raw.arr),
      nrr: asNumber(raw.nrr),
      mrrGrowth: asNumber(raw.mrrGrowth ?? raw.mrr_growth_pct),
      arrGrowth: asNumber(raw.arrGrowth ?? raw.arr_growth_pct ?? raw.mrr_growth_pct),
      churnRate: asNumber(raw.churnRate ?? raw.churn_rate),
    };
  },

  getRevenueBreakdown: async (): Promise<RevenueBreakdown[]> => {
    const response = await apiClient.get<ApiResponse<RevenueBreakdown[]>>(
      '/superadmin/revenue/breakdown'
    );
    return response.data.data;
  },

  // Tenants Portfolio
  getTenantsPortfolio: async (params?: {
    status?: string;
    plan?: string;
    sortBy?: string;
    sortOrder?: string;
    skip?: number;
    limit?: number;
  }): Promise<PaginatedResponse<TenantPortfolioItem>> => {
    const response = await apiClient.get<ApiResponse<PaginatedResponse<TenantPortfolioItem>>>(
      '/superadmin/tenants/portfolio',
      { params }
    );
    return response.data.data;
  },

  // System Health
  getSystemHealth: async (): Promise<SystemHealthMetrics> => {
    const response = await apiClient.get<ApiResponse<SystemHealthMetrics>>(
      '/superadmin/system/health'
    );
    return response.data.data;
  },

  // Churn Risks
  getChurnRisks: async (params?: { minRisk?: number; limit?: number }): Promise<ChurnRisk[]> => {
    const response = await apiClient.get<ApiResponse<ChurnRisk[]>>('/superadmin/churn/risks', {
      params,
    });
    return response.data.data;
  },

  // Audit Logs
  getAuditLogs: async (params?: {
    startDate?: string;
    endDate?: string;
    action?: string;
    userId?: string;
    tenantId?: number;
    severity?: string;
    skip?: number;
    limit?: number;
  }): Promise<PaginatedResponse<AuditLogEntry>> => {
    const response = await apiClient.get<ApiResponse<PaginatedResponse<AuditLogEntry>>>(
      '/superadmin/audit',
      { params }
    );
    return response.data.data;
  },

  // Billing - Plans (catalogue mirror; billable prices are managed in Paddle)
  getBillingPlans: async (): Promise<BillingPlan[]> => {
    const response = await apiClient.get<ApiResponse<unknown>>('/superadmin/billing/plans');
    return extractList(response.data.data, 'plans').map(mapBillingPlan);
  },

  // Billing - Invoices (read-only; Paddle issues and hosts the invoices)
  getBillingInvoices: async (params?: {
    status?: string;
    tenantId?: number;
    skip?: number;
    limit?: number;
  }): Promise<PaginatedResponse<BillingInvoice>> => {
    const response = await apiClient.get<ApiResponse<unknown>>('/superadmin/billing/invoices', {
      params: {
        status_filter: params?.status,
        tenant_id: params?.tenantId,
        skip: params?.skip,
        limit: params?.limit,
      },
    });
    const payload = response.data.data;
    return toPaginated(extractList(payload, 'invoices').map(mapBillingInvoice), payload, params);
  },

  // Billing - Subscriptions (Paddle state mirrored on tenants)
  getBillingSubscriptions: async (params?: {
    status?: string;
    plan?: string;
    skip?: number;
    limit?: number;
  }): Promise<PaginatedResponse<BillingSubscription>> => {
    const response = await apiClient.get<ApiResponse<unknown>>(
      '/superadmin/billing/subscriptions',
      {
        params: { status_filter: params?.status, skip: params?.skip, limit: params?.limit },
      }
    );
    const payload = response.data.data;
    let items = extractList(payload, 'subscriptions').map(mapBillingSubscription);
    if (params?.plan) {
      items = items.filter((s) => s.plan === params.plan);
    }
    return toPaginated(items, payload, params);
  },
};

// =============================================================================
// Superadmin React Query Hooks
// =============================================================================

/**
 * Get superadmin dashboard overview
 */
export function useSuperAdminOverview() {
  return useQuery({
    queryKey: ['superadmin', 'dashboard'],
    queryFn: superadminApi.getDashboard,
    staleTime: 60 * 1000, // 1 minute
    refetchInterval: 5 * 60 * 1000, // Refetch every 5 minutes
  });
}

/**
 * Get superadmin tenant portfolio
 */
export function useSuperAdminTenants(params?: {
  status?: string;
  plan?: string;
  sortBy?: string;
  sortOrder?: string;
  skip?: number;
  limit?: number;
}) {
  return useQuery({
    queryKey: ['superadmin', 'tenants', params],
    queryFn: () => superadminApi.getTenantsPortfolio(params),
    staleTime: 30 * 1000,
  });
}

/**
 * Get revenue metrics
 */
export function useRevenue() {
  return useQuery({
    queryKey: ['superadmin', 'revenue'],
    queryFn: superadminApi.getRevenue,
    staleTime: 60 * 1000,
  });
}

/**
 * Get revenue breakdown by plan
 */
export function useRevenueBreakdown() {
  return useQuery({
    queryKey: ['superadmin', 'revenue', 'breakdown'],
    queryFn: superadminApi.getRevenueBreakdown,
    staleTime: 60 * 1000,
  });
}

/**
 * Get system health metrics
 */
export function useSystemHealth() {
  return useQuery({
    queryKey: ['superadmin', 'system', 'health'],
    queryFn: superadminApi.getSystemHealth,
    staleTime: 30 * 1000,
    refetchInterval: 30 * 1000, // Refresh every 30 seconds
  });
}

/**
 * Get churn risk tenants
 */
export function useChurnRisks(params?: { minRisk?: number; limit?: number }) {
  return useQuery({
    queryKey: ['superadmin', 'churn', 'risks', params],
    queryFn: () => superadminApi.getChurnRisks(params),
    staleTime: 5 * 60 * 1000,
  });
}

/**
 * Get audit logs
 */
export function useAuditLogs(params?: {
  startDate?: string;
  endDate?: string;
  action?: string;
  userId?: string;
  tenantId?: number;
  severity?: string;
  skip?: number;
  limit?: number;
}) {
  return useQuery({
    queryKey: ['superadmin', 'audit', params],
    queryFn: () => superadminApi.getAuditLogs(params),
    staleTime: 30 * 1000,
  });
}

/**
 * Get billing plans
 */
export function useBillingPlans() {
  return useQuery({
    queryKey: ['superadmin', 'billing', 'plans'],
    queryFn: superadminApi.getBillingPlans,
    staleTime: 5 * 60 * 1000,
  });
}

/**
 * Get billing invoices
 */
export function useBillingInvoices(params?: {
  status?: string;
  tenantId?: number;
  skip?: number;
  limit?: number;
}) {
  return useQuery({
    queryKey: ['superadmin', 'billing', 'invoices', params],
    queryFn: () => superadminApi.getBillingInvoices(params),
    staleTime: 60 * 1000,
  });
}

/**
 * Get billing subscriptions
 */
export function useBillingSubscriptions(params?: {
  status?: string;
  plan?: string;
  skip?: number;
  limit?: number;
}) {
  return useQuery({
    queryKey: ['superadmin', 'billing', 'subscriptions', params],
    queryFn: () => superadminApi.getBillingSubscriptions(params),
    staleTime: 60 * 1000,
  });
}

// NOTE: there is intentionally no "retry payment" mutation. Paddle Billing owns dunning and
// payment retries; superadmins manage those from the Paddle dashboard.
