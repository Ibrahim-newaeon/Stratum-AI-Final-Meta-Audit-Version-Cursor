/**
 * Meta activation hub API hooks
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';

export interface ActivationStep {
  id: string;
  title: string;
  description: string;
  required: boolean;
  complete: boolean;
  action_path: string;
}

export interface ActivationStatus {
  steps: ActivationStep[];
  required_complete: boolean;
  fully_integrated: boolean;
  progress_percent: number;
  required_done: number;
  required_total: number;
}

export function useActivationStatus() {
  return useQuery({
    queryKey: ['activation', 'status'],
    queryFn: async () => {
      const res = await apiClient.get<{ data: ActivationStatus }>('/activation/status');
      return res.data.data;
    },
    staleTime: 30_000,
  });
}

export interface MarketingTokenInput {
  ad_account_id: string;
  access_token: string;
  ad_account_name?: string;
}

export function useSaveMarketingToken() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: MarketingTokenInput) => {
      const res = await apiClient.put('/cdp/audience-sync/credentials', {
        platform: 'meta',
        ad_account_id: input.ad_account_id,
        access_token: input.access_token,
        ad_account_name: input.ad_account_name,
      });
      return res.data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['activation', 'status'] });
      queryClient.invalidateQueries({ queryKey: ['cdp'] });
    },
  });
}
