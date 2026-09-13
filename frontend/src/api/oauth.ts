/**
 * Ad-platform OAuth client (Meta Ads).
 *
 * The connect page lives at FRONTEND_CONNECT_PATH. The backend callback
 * must send the browser here — `/connect` is not a dashboard route.
 */

import { apiClient } from './client';

export const FRONTEND_CONNECT_PATH = '/dashboard/campaigns/connect';

export type OAuthPlatform = 'meta';

export interface OAuthConnectionStatus {
  platform: string;
  status: 'connected' | 'disconnected' | 'expired' | 'error';
  connected_at?: string | null;
  token_expires_at?: string | null;
  last_refreshed_at?: string | null;
  scopes?: string[];
  last_error?: string | null;
  ad_accounts_count?: number;
}

interface Envelope<T> {
  success: boolean;
  data: T;
  message?: string;
}

export async function getOAuthStatus(platform: OAuthPlatform): Promise<OAuthConnectionStatus> {
  const response = await apiClient.get<Envelope<OAuthConnectionStatus>>(
    `/oauth/${platform}/status`
  );
  return response.data.data;
}

export async function startOAuth(
  platform: OAuthPlatform
): Promise<{ authorization_url: string; state: string; platform: string }> {
  const response = await apiClient.post<
    Envelope<{ authorization_url: string; state: string; platform: string }>
  >(`/oauth/${platform}/authorize`, {});
  return response.data.data;
}

export async function refreshOAuth(platform: OAuthPlatform): Promise<void> {
  await apiClient.post(`/oauth/${platform}/refresh`);
}

export async function disconnectOAuth(platform: OAuthPlatform): Promise<void> {
  await apiClient.delete(`/oauth/${platform}/disconnect`);
}

export interface SyncAdAccountsResult {
  connected_count: number;
  accounts: Array<{
    id?: string | null;
    platform_account_id: string;
    name: string;
    is_enabled: boolean;
  }>;
}

/** Fetch Meta ad accounts and enable them locally for discovery/insights. */
export async function syncOAuthAdAccounts(
  platform: OAuthPlatform
): Promise<SyncAdAccountsResult> {
  const response = await apiClient.post<Envelope<SyncAdAccountsResult>>(
    `/oauth/${platform}/accounts/sync`
  );
  return response.data.data;
}
