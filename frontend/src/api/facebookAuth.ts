/**
 * Stratum AI - Facebook Login API
 *
 * Talks to the `/auth/facebook*` endpoints: the public SDK configuration and
 * sign-in exchange, plus the authenticated link-management routes used from
 * account settings.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient, ApiResponse } from './client';
import type { FacebookCredential, FacebookSdkConfig } from '@/lib/facebookSdk';

/**
 * Sign-in result. Mirrors the backend `LoginResponse`, so a Facebook sign-in
 * and a password sign-in are handled by the same caller code — including the
 * MFA branch, which Facebook does not bypass.
 */
export interface FacebookLoginResponse {
  mfa_required: boolean;
  mfa_session_token?: string | null;
  access_token?: string | null;
  refresh_token?: string | null;
  token_type?: string | null;
  expires_in?: number | null;
}

export interface FacebookLinkStatus {
  linked: boolean;
  provider_user_id?: string | null;
  linked_at?: string | null;
  last_login_at?: string | null;
  can_unlink: boolean;
}

export interface FacebookLinkResult {
  linked: boolean;
  message: string;
}

export const facebookAuthApi = {
  /** Public SDK configuration. Returns `enabled: false` when the feature is off. */
  getConfig: async (): Promise<FacebookSdkConfig> => {
    const response = await apiClient.get<ApiResponse<FacebookSdkConfig>>('/auth/facebook/config');
    return response.data.data;
  },

  /** Exchange a Facebook credential for Stratum tokens (or an MFA challenge). */
  login: async (credential: FacebookCredential): Promise<FacebookLoginResponse> => {
    const response = await apiClient.post<ApiResponse<FacebookLoginResponse>>(
      '/auth/facebook',
      credential
    );
    return response.data.data;
  },

  /** Whether the signed-in account has Facebook attached. */
  getLinkStatus: async (): Promise<FacebookLinkStatus> => {
    const response = await apiClient.get<ApiResponse<FacebookLinkStatus>>('/auth/facebook/link');
    return response.data.data;
  },

  /** Attach Facebook to the signed-in account. */
  link: async (credential: FacebookCredential): Promise<FacebookLinkResult> => {
    const response = await apiClient.post<ApiResponse<FacebookLinkResult>>(
      '/auth/facebook/link',
      credential
    );
    return response.data.data;
  },

  /** Detach Facebook from the signed-in account. */
  unlink: async (): Promise<FacebookLinkResult> => {
    const response = await apiClient.delete<ApiResponse<FacebookLinkResult>>('/auth/facebook/link');
    return response.data.data;
  },
};

/**
 * Load the public Facebook SDK configuration.
 *
 * Cached for the session: it changes only when an operator changes a setting
 * and redeploys, and the sign-in page should not re-fetch it on every focus.
 */
export function useFacebookLoginConfig() {
  return useQuery({
    queryKey: ['auth', 'facebook', 'config'],
    queryFn: facebookAuthApi.getConfig,
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

/** Read the signed-in account's Facebook link status. */
export function useFacebookLinkStatus(enabled = true) {
  return useQuery({
    queryKey: ['auth', 'facebook', 'link'],
    queryFn: facebookAuthApi.getLinkStatus,
    enabled,
    retry: false,
  });
}

/** Attach Facebook to the signed-in account. */
export function useLinkFacebook() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: facebookAuthApi.link,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['auth', 'facebook', 'link'] });
    },
  });
}

/** Detach Facebook from the signed-in account. */
export function useUnlinkFacebook() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: facebookAuthApi.unlink,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['auth', 'facebook', 'link'] });
    },
  });
}
