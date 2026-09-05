/**
 * Stratum AI - Account Manager Portfolio API
 *
 * One batched call behind the account-manager portfolio: the tenants the
 * caller may see, each with its measured trust, commercial and operational
 * metrics.
 *
 * Every optional field is `null` when this deployment has no source for that
 * metric on that tenant. `null` means unknown and must render as a dash; it is
 * never a zero. A `0` that does arrive *is* a measurement - no spend, no held
 * budget, no open incident - and says so.
 *
 * Visibility mirrors `GET /tenants`: the platform role sees every tenant,
 * every tenant-scoped role sees only its own.
 */

import { useQuery } from '@tanstack/react-query';
import { apiClient, ApiResponse } from './client';
import type { SignalHealthStatus, TrustGateDecision } from './dashboard';

// =============================================================================
// Types
// =============================================================================

export interface TenantPortfolioRow {
  id: number;
  name: string;
  slug: string;
  /** Declared during onboarding; null when the tenant never stated one. */
  industry: string | null;
  plan: string | null;
  /** Paddle subscription status; "active" for free / admin-granted plans. */
  subscription_status: string;
  mrr: number;
  renewal_date: string | null;

  /**
   * The composite the trust gate grades. Null with status
   * `insufficient_data` means there was not enough evidence to score - which
   * is unknown, not bad, and must not be rendered as a zero.
   */
  signal_health_score: number | null;
  signal_health_status: SignalHealthStatus;
  /** The EMQ component of that composite, measured from capi_delivery_logs. */
  emq_score: number | null;
  /** Change in recorded EMQ between the two most recent daily snapshots. */
  emq_trend: number | null;
  /** The Meta channel the scores represent. */
  channel: string | null;
  /** One entry per component that could not be measured, and why (English). */
  missing_inputs: string[];
  /** Stable codes for the same gaps. */
  missing_input_codes: string[];

  /** The gate's own decision - not re-derived from the score. */
  gate_decision: TrustGateDecision | null;
  gate_reason: string | null;
  /** Null exactly when the gate had no snapshot to grade: no data, not BLOCK. */
  gate_health_date: string | null;

  /**
   * Daily budget of the campaigns whose autopilot actions are queued and
   * unapplied. Null when actions are held but none could be priced.
   */
  budget_at_risk: number | null;
  queued_actions: number;
  /** Unresolved pacing alerts. 0 is a real count, not a missing value. */
  active_incidents: number | null;
  /** Hours the oldest unresolved alert has been open; null when there is none. */
  incident_open_hours: number | null;

  /** Spend over the trailing window; null when no daily metric rows exist. */
  monthly_spend: number | null;
  /** Revenue over spend; null when there was no spend to divide by. */
  roas: number | null;
  /** This window's ROAS less the previous window's; null unless both exist. */
  roas_trend: number | null;

  /**
   * Newest sign-in by any user of the tenant. This product keeps no record of
   * an account manager contacting a customer, so this is not one.
   */
  last_login_at: string | null;
}

export interface TenantPortfolioResponse {
  tenants: TenantPortfolioRow[];
  total: number;
  /** How many days the spend, ROAS and ROAS-trend figures cover. */
  spend_window_days: number;
}

export interface TenantPortfolioParams {
  skip?: number;
  limit?: number;
  search?: string;
}

// =============================================================================
// API
// =============================================================================

export const portfolioApi = {
  /**
   * Get the portfolio rows for every tenant the caller may see.
   */
  getPortfolio: async (
    params: TenantPortfolioParams = {}
  ): Promise<TenantPortfolioResponse> => {
    const response = await apiClient.get<ApiResponse<TenantPortfolioResponse>>(
      '/tenants/portfolio',
      { params }
    );
    return response.data.data;
  },
};

// =============================================================================
// React Query Hooks
// =============================================================================

/**
 * Get the account-manager portfolio.
 */
export function useTenantPortfolio(params: TenantPortfolioParams = {}) {
  return useQuery({
    queryKey: ['tenants', 'portfolio', params],
    queryFn: () => portfolioApi.getPortfolio(params),
    staleTime: 60 * 1000,
  });
}
