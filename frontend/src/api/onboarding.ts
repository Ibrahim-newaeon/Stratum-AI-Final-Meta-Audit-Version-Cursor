/**
 * Stratum AI - Onboarding API
 *
 * React Query hooks for the onboarding wizard flow.
 * Endpoints live under /api/v1/onboarding/...
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient, ApiResponse } from './client';

// =============================================================================
// Types
// =============================================================================

export type OnboardingStep =
  | 'business_profile'
  | 'platform_selection'
  | 'goals_setup'
  | 'automation_preferences'
  | 'trust_gate_config'
  | 'completed';

export type Industry =
  | 'ecommerce'
  | 'saas'
  | 'lead_gen'
  | 'mobile_app'
  | 'gaming'
  | 'finance'
  | 'healthcare'
  | 'education'
  | 'real_estate'
  | 'travel'
  | 'food_beverage'
  | 'retail'
  | 'automotive'
  | 'entertainment'
  | 'other';

export type MonthlyAdSpend =
  | 'under_10k'
  | '10k_50k'
  | '50k_100k'
  | '100k_500k'
  | '500k_1m'
  | 'over_1m';

export type TeamSize = 'solo' | '2_5' | '6_15' | '16_50' | '50_plus';

/** Meta-only platform surface: Facebook, Instagram, WhatsApp */
export type AdPlatform = 'facebook' | 'instagram' | 'whatsapp';

export type PrimaryKPI =
  | 'roas'
  | 'cpa'
  | 'cpl'
  | 'revenue'
  | 'conversions'
  | 'leads'
  | 'app_installs'
  | 'brand_awareness';

export type AutomationMode = 'manual' | 'assisted' | 'autopilot';

export interface OnboardingStatus {
  completed: boolean;
  skipped: boolean;
  current_step: OnboardingStep;
  completed_steps: OnboardingStep[];
  progress_pct: number;
}

export interface OnboardingCheckResponse {
  /** True when the user still needs to complete onboarding */
  required: boolean;
  current_step?: OnboardingStep;
}

export interface BusinessProfilePayload {
  industry: Industry;
  industry_other?: string;
  monthly_ad_spend: MonthlyAdSpend;
  team_size: TeamSize;
  company_website?: string;
  target_markets?: string[];
}

export interface PlatformSelectionPayload {
  platforms: AdPlatform[];
}

export interface GoalsSetupPayload {
  primary_kpi: PrimaryKPI;
  target_roas?: number;
  target_cpa?: number;
  monthly_budget?: number;
  currency?: string;
  timezone?: string;
}

export interface AutomationPreferencesPayload {
  automation_mode: AutomationMode;
  auto_pause_enabled?: boolean;
  auto_scale_enabled?: boolean;
  notification_email?: boolean;
  notification_slack?: boolean;
  notification_whatsapp?: boolean;
}

export interface TrustGateConfigPayload {
  trust_threshold_autopilot: number;
  trust_threshold_alert: number;
  require_approval_above?: number;
  max_daily_actions?: number;
}

export interface OnboardingStepResponse {
  success: boolean;
  current_step: OnboardingStep;
  completed: boolean;
}

// =============================================================================
// Query keys
// =============================================================================

export const onboardingQueryKeys = {
  all: ['onboarding'] as const,
  status: () => [...onboardingQueryKeys.all, 'status'] as const,
  check: () => [...onboardingQueryKeys.all, 'check'] as const,
};

// =============================================================================
// API functions
// =============================================================================

const onboardingApi = {
  getStatus: async (): Promise<OnboardingStatus> => {
    const response = await apiClient.get<ApiResponse<OnboardingStatus>>('/onboarding/status');
    return response.data.data;
  },

  check: async (): Promise<OnboardingCheckResponse> => {
    const response = await apiClient.get<ApiResponse<OnboardingCheckResponse>>('/onboarding/check');
    return response.data.data;
  },

  /**
   * Complete one wizard step.
   *
   * The step goes in the body, not the path: the API exposes a single
   * `POST /onboarding/steps` taking `{ step, data }`. Posting to
   * `/onboarding/steps/<step>` - which this used to do - is a 404, which the
   * wizard surfaced as "Failed to save. Please try again."
   */
  submitStep: async <T>(step: OnboardingStep, payload: T): Promise<OnboardingStepResponse> => {
    const response = await apiClient.post<ApiResponse<OnboardingStepResponse>>(
      '/onboarding/steps',
      { step, data: payload }
    );
    return response.data.data;
  },

  skip: async (): Promise<OnboardingStepResponse> => {
    const response = await apiClient.post<ApiResponse<OnboardingStepResponse>>('/onboarding/skip');
    return response.data.data;
  },

  reset: async (): Promise<OnboardingStepResponse> => {
    const response = await apiClient.post<ApiResponse<OnboardingStepResponse>>('/onboarding/reset');
    return response.data.data;
  },
};

// =============================================================================
// Hooks
// =============================================================================

/** Get the current onboarding wizard status */
export function useOnboardingStatus() {
  return useQuery({
    queryKey: onboardingQueryKeys.status(),
    queryFn: onboardingApi.getStatus,
    staleTime: 30 * 1000,
  });
}

/** Lightweight check used by the OnboardingGuard to decide whether to redirect */
export function useOnboardingCheck() {
  return useQuery({
    queryKey: onboardingQueryKeys.check(),
    queryFn: onboardingApi.check,
    staleTime: 60 * 1000,
    retry: 1,
  });
}

function useOnboardingStepMutation<T>(step: OnboardingStep) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: T) => onboardingApi.submitStep<T>(step, payload),
    // Returned, not fired and forgotten: React Query awaits a promise from
    // onSuccess before mutateAsync resolves. Without the return, a caller that
    // navigates straight after awaiting the mutation races the refetch, and
    // OnboardingGuard reads the *old* cached check - which still says
    // `required: true` - and bounces the person back to the wizard.
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: onboardingQueryKeys.all }),
  });
}

export function useSubmitBusinessProfile() {
  return useOnboardingStepMutation<BusinessProfilePayload>('business_profile');
}

export function useSubmitPlatformSelection() {
  return useOnboardingStepMutation<PlatformSelectionPayload>('platform_selection');
}

export function useSubmitGoalsSetup() {
  return useOnboardingStepMutation<GoalsSetupPayload>('goals_setup');
}

export function useSubmitAutomationPreferences() {
  return useOnboardingStepMutation<AutomationPreferencesPayload>('automation_preferences');
}

export function useSubmitTrustGateConfig() {
  return useOnboardingStepMutation<TrustGateConfigPayload>('trust_gate_config');
}

/** Skip the onboarding wizard entirely */
export function useSkipOnboarding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => onboardingApi.skip(),
    // Returned, not fired and forgotten: React Query awaits a promise from
    // onSuccess before mutateAsync resolves. Without the return, a caller that
    // navigates straight after awaiting the mutation races the refetch, and
    // OnboardingGuard reads the *old* cached check - which still says
    // `required: true` - and bounces the person back to the wizard.
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: onboardingQueryKeys.all }),
  });
}

/** Reset onboarding progress (start over) */
export function useResetOnboarding() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => onboardingApi.reset(),
    // Returned, not fired and forgotten: React Query awaits a promise from
    // onSuccess before mutateAsync resolves. Without the return, a caller that
    // navigates straight after awaiting the mutation races the refetch, and
    // OnboardingGuard reads the *old* cached check - which still says
    // `required: true` - and bounces the person back to the wizard.
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: onboardingQueryKeys.all }),
  });
}
