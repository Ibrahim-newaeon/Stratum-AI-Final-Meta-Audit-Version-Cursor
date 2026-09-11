/**
 * Stratum AI - Tenant Billing API (Paddle Billing)
 *
 * Subscription billing for the current tenant is handled by Paddle Billing
 * (Paddle is the Merchant of Record). Checkout itself is client-side: the API
 * returns the price id, a client-side token, the customer email and the
 * custom data, and the SPA opens the Paddle.js v2 overlay (see
 * `src/lib/paddle.ts`). Everything else - customer portal, cancel / resume,
 * plan changes, transactions and invoice PDFs - goes through the backend,
 * which talks to the Paddle REST API with the server-side API key.
 *
 * Endpoints live under /api/v1/billing/... (the axios client already carries
 * the /api/v1 prefix in its baseURL). Every route requires the Bearer token
 * attached by the shared client; the backend derives the tenant from the
 * authenticated user.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient, ApiResponse } from './client';

// =============================================================================
// Types (mirror app.schemas.billing 1:1, snake_case)
// =============================================================================

export type TierName = 'starter' | 'professional' | 'enterprise';

/** Paddle subscription status as stored on the tenant. */
export type SubscriptionStatus = 'active' | 'trialing' | 'past_due' | 'paused' | 'canceled';

export type PaddleEnvironment = 'sandbox' | 'production';

export interface TierPriceInfo {
  tier: TierName;
  name: string;
  /** Monthly list price in major units; null for "contact sales" tiers. */
  price: number | null;
  currency: string;
  billing_period: string;
  description: string;
}

export interface BillingConfig {
  payments_enabled?: boolean;
  paddle_configured: boolean;
  environment: PaddleEnvironment;
  /** Paddle client-side token (safe for the browser); null when not configured. */
  client_token: string | null;
  price_ids: {
    starter: string | null;
    professional: string | null;
    enterprise: string | null;
  };
  tiers: TierPriceInfo[];
}

export interface BillingSubscription {
  paddle_configured: boolean;
  has_customer: boolean;
  has_subscription: boolean;
  customer_id: string | null;
  subscription_id: string | null;
  status: SubscriptionStatus | null;
  tier: TierName | null;
  /** Tenant.plan ('free' | 'starter' | 'professional' | 'enterprise'). */
  plan: string;
  current_period_start: string | null;
  current_period_end: string | null;
  next_billed_at: string | null;
  cancel_at_period_end: boolean;
  canceled_at: string | null;
  paused_at: string | null;
  trial_end: string | null;
}

export interface CheckoutSessionRequest {
  tier: TierName;
  /** Absolute URL Paddle redirects to after a successful checkout. */
  success_url?: string;
}

/** Everything Paddle.js needs to open the overlay checkout. */
export interface CheckoutSession {
  price_id: string;
  client_token: string;
  environment: PaddleEnvironment;
  customer_id: string;
  customer_email: string;
  custom_data: { tenant_id: string; tier: string };
  success_url: string;
  display_mode: 'overlay';
}

export interface PortalSessionRequest {
  return_url?: string;
}

export interface PortalSession {
  portal_url: string;
  cancel_url: string | null;
  update_payment_method_url: string | null;
}

export interface CancelSubscriptionRequest {
  /** Default true: the plan stays active until the end of the billing period. */
  at_period_end?: boolean;
}

export interface UpgradeSubscriptionRequest {
  new_tier: TierName;
  prorate?: boolean;
}

export interface BillingTransaction {
  id: string;
  invoice_number: string | null;
  status: string;
  subscription_id: string | null;
  /** Integer minor units (e.g. cents). */
  amount_minor: number;
  currency: string;
  billed_at: string | null;
  created_at: string | null;
}

export interface TransactionInvoice {
  transaction_id: string;
  /** Short-lived Paddle invoice PDF URL. */
  invoice_url: string;
}

// =============================================================================
// Query keys
// =============================================================================

export const billingQueryKeys = {
  all: ['billing'] as const,
  config: ['billing', 'config'] as const,
  subscription: ['billing', 'subscription'] as const,
  transactions: (limit: number) => ['billing', 'transactions', limit] as const,
};

/** Prefix matching every `billingQueryKeys.transactions(limit)` key. */
const TRANSACTIONS_PREFIX = ['billing', 'transactions'] as const;

// =============================================================================
// Helpers
// =============================================================================

/**
 * Extract a human-readable message from an axios / API error.
 * Mirrors the GA4Integration helper; handles both the APIResponse envelope
 * (`error` / `message`) and FastAPI's `detail`.
 */
export function getBillingErrorMessage(
  err: unknown,
  fallback: string = 'Billing request failed'
): string {
  const e = err as {
    response?: {
      status?: number;
      data?: { message?: string; detail?: string; error?: string; errors?: string[] };
    };
    message?: string;
  };
  return (
    e?.response?.data?.message ||
    e?.response?.data?.detail ||
    e?.response?.data?.error ||
    e?.response?.data?.errors?.[0] ||
    e?.message ||
    fallback
  );
}

// =============================================================================
// API functions
// =============================================================================

const BASE = '/billing';

export const billingApi = {
  /**
   * Public billing configuration: environment, client token, tier prices.
   */
  getConfig: async (): Promise<BillingConfig> => {
    const response = await apiClient.get<ApiResponse<BillingConfig>>(`${BASE}/config`);
    return response.data.data;
  },

  /**
   * Current tenant subscription state (from Paddle when linked, else Tenant.plan).
   */
  getSubscription: async (): Promise<BillingSubscription> => {
    const response = await apiClient.get<ApiResponse<BillingSubscription>>(
      `${BASE}/subscription`
    );
    return response.data.data;
  },

  /**
   * Prepare a client-side Paddle.js overlay checkout for a tier.
   * 503 when Paddle is not configured, 400 when the tier has no price id.
   */
  createCheckoutSession: async (payload: CheckoutSessionRequest): Promise<CheckoutSession> => {
    const response = await apiClient.post<ApiResponse<CheckoutSession>>(
      `${BASE}/checkout-session`,
      payload
    );
    return response.data.data;
  },

  /**
   * Create Paddle customer portal links (overview, cancel, update payment method).
   */
  createPortalSession: async (payload: PortalSessionRequest = {}): Promise<PortalSession> => {
    const response = await apiClient.post<ApiResponse<PortalSession>>(
      `${BASE}/portal-session`,
      payload
    );
    return response.data.data;
  },

  /**
   * Cancel the subscription (default: at the end of the current period).
   */
  cancelSubscription: async (
    payload: CancelSubscriptionRequest = {}
  ): Promise<BillingSubscription> => {
    const response = await apiClient.post<ApiResponse<BillingSubscription>>(`${BASE}/cancel`, {
      at_period_end: payload.at_period_end ?? true,
    });
    return response.data.data;
  },

  /**
   * Remove a scheduled cancellation (subscription keeps renewing).
   */
  reactivateSubscription: async (): Promise<BillingSubscription> => {
    const response = await apiClient.post<ApiResponse<BillingSubscription>>(`${BASE}/reactivate`);
    return response.data.data;
  },

  /**
   * Change the tier of an existing subscription (prorated by default).
   */
  upgradeSubscription: async (payload: UpgradeSubscriptionRequest): Promise<BillingSubscription> => {
    const response = await apiClient.post<ApiResponse<BillingSubscription>>(`${BASE}/upgrade`, {
      new_tier: payload.new_tier,
      prorate: payload.prorate ?? true,
    });
    return response.data.data;
  },

  /**
   * Most recent transactions (invoices) for the tenant's Paddle customer.
   */
  listTransactions: async (limit: number = 10): Promise<BillingTransaction[]> => {
    const response = await apiClient.get<ApiResponse<BillingTransaction[]>>(
      `${BASE}/transactions`,
      { params: { limit } }
    );
    return response.data.data ?? [];
  },

  /**
   * Resolve the invoice PDF URL for a transaction.
   */
  getTransactionInvoice: async (transactionId: string): Promise<TransactionInvoice> => {
    const response = await apiClient.get<ApiResponse<TransactionInvoice>>(
      `${BASE}/transactions/${encodeURIComponent(transactionId)}/invoice`
    );
    return response.data.data;
  },
};

/**
 * Fetch the (short-lived) invoice PDF URL for a transaction.
 * Callers typically `window.open(url, '_blank', 'noopener')` the result.
 */
export async function fetchTransactionInvoiceUrl(transactionId: string): Promise<string> {
  const invoice = await billingApi.getTransactionInvoice(transactionId);
  return invoice.invoice_url;
}

// =============================================================================
// React Query hooks
// =============================================================================

function useInvalidateBillingState() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: billingQueryKeys.subscription });
    void queryClient.invalidateQueries({ queryKey: TRANSACTIONS_PREFIX });
  };
}

export function useBillingConfig() {
  return useQuery({
    queryKey: billingQueryKeys.config,
    queryFn: billingApi.getConfig,
    staleTime: 5 * 60 * 1000,
  });
}

export function useBillingSubscription(options: { refetchInterval?: number | false } = {}) {
  return useQuery({
    queryKey: billingQueryKeys.subscription,
    queryFn: billingApi.getSubscription,
    staleTime: 30 * 1000,
    refetchInterval: options.refetchInterval ?? false,
  });
}

export function useCreateCheckoutSession() {
  return useMutation({
    mutationFn: (payload: CheckoutSessionRequest) => billingApi.createCheckoutSession(payload),
  });
}

export function useCreatePortalSession() {
  return useMutation({
    mutationFn: (payload: PortalSessionRequest = {}) => billingApi.createPortalSession(payload),
  });
}

export function useCancelSubscription() {
  const invalidate = useInvalidateBillingState();
  return useMutation({
    mutationFn: (payload: CancelSubscriptionRequest = {}) => billingApi.cancelSubscription(payload),
    onSuccess: invalidate,
  });
}

export function useReactivateSubscription() {
  const invalidate = useInvalidateBillingState();
  return useMutation({
    mutationFn: billingApi.reactivateSubscription,
    onSuccess: invalidate,
  });
}

export function useUpgradeSubscription() {
  const invalidate = useInvalidateBillingState();
  return useMutation({
    mutationFn: (payload: UpgradeSubscriptionRequest) => billingApi.upgradeSubscription(payload),
    onSuccess: invalidate,
  });
}

export function useBillingTransactions(limit: number = 10, enabled: boolean = true) {
  return useQuery({
    queryKey: billingQueryKeys.transactions(limit),
    queryFn: () => billingApi.listTransactions(limit),
    enabled,
    staleTime: 60 * 1000,
  });
}
