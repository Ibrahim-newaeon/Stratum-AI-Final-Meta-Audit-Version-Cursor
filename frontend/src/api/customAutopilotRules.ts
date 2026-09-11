/**
 * Custom Autopilot Rules API
 *
 * Persists if/then rules that enqueue SAFE Autopilot actions into the
 * Autopilot queue. Meta writes stay on the Autopilot executor path.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient, ApiResponse } from './client';

export type CustomAutopilotStatus = 'draft' | 'active' | 'paused';

export type CustomAutopilotActionType =
  | 'budget_decrease'
  | 'pause_adset'
  | 'bid_decrease';

export interface CustomAutopilotCondition {
  field: string;
  operator: string;
  value: number;
}

export interface CustomAutopilotAction {
  type: CustomAutopilotActionType;
  config: Record<string, unknown>;
}

export interface CustomAutopilotRule {
  id: string;
  tenant_id: number;
  name: string;
  description: string | null;
  status: CustomAutopilotStatus;
  conditions: CustomAutopilotCondition[];
  actions: CustomAutopilotAction[];
  require_approval: boolean;
  cooldown_hours: number;
  max_executions_per_day: number;
  last_evaluated_at: string | null;
  last_triggered_at: string | null;
  trigger_count: number;
  created_at: string;
  updated_at: string;
}

export interface CustomAutopilotRulePage {
  items: CustomAutopilotRule[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface CreateCustomAutopilotRuleRequest {
  name: string;
  description?: string;
  status?: CustomAutopilotStatus;
  conditions: CustomAutopilotCondition[];
  actions: CustomAutopilotAction[];
  require_approval?: boolean;
  cooldown_hours?: number;
  max_executions_per_day?: number;
}

export type UpdateCustomAutopilotRuleRequest = Partial<CreateCustomAutopilotRuleRequest>;

const BASE = '/custom-autopilot-rules';

export const customAutopilotRulesApi = {
  list: async (params?: {
    page?: number;
    page_size?: number;
    status?: CustomAutopilotStatus;
  }): Promise<CustomAutopilotRulePage> => {
    const response = await apiClient.get<ApiResponse<CustomAutopilotRulePage>>(BASE, {
      params,
    });
    return response.data.data;
  },

  get: async (id: string): Promise<CustomAutopilotRule> => {
    const response = await apiClient.get<ApiResponse<CustomAutopilotRule>>(`${BASE}/${id}`);
    return response.data.data;
  },

  create: async (data: CreateCustomAutopilotRuleRequest): Promise<CustomAutopilotRule> => {
    const response = await apiClient.post<ApiResponse<CustomAutopilotRule>>(BASE, data);
    return response.data.data;
  },

  update: async (
    id: string,
    data: UpdateCustomAutopilotRuleRequest
  ): Promise<CustomAutopilotRule> => {
    const response = await apiClient.patch<ApiResponse<CustomAutopilotRule>>(
      `${BASE}/${id}`,
      data
    );
    return response.data.data;
  },

  remove: async (id: string): Promise<void> => {
    await apiClient.delete(`${BASE}/${id}`);
  },

  activate: async (id: string): Promise<CustomAutopilotRule> => {
    const response = await apiClient.post<ApiResponse<CustomAutopilotRule>>(
      `${BASE}/${id}/activate`
    );
    return response.data.data;
  },

  pause: async (id: string): Promise<CustomAutopilotRule> => {
    const response = await apiClient.post<ApiResponse<CustomAutopilotRule>>(
      `${BASE}/${id}/pause`
    );
    return response.data.data;
  },

  evaluate: async (id: string): Promise<Record<string, unknown>> => {
    const response = await apiClient.post<ApiResponse<Record<string, unknown>>>(
      `${BASE}/${id}/evaluate`
    );
    return response.data.data;
  },
};

export const customAutopilotKeys = {
  all: ['custom-autopilot-rules'] as const,
  list: (params?: object) => [...customAutopilotKeys.all, 'list', params] as const,
  detail: (id: string) => [...customAutopilotKeys.all, 'detail', id] as const,
};

export function useCustomAutopilotRules(params?: {
  page?: number;
  page_size?: number;
  status?: CustomAutopilotStatus;
}) {
  return useQuery({
    queryKey: customAutopilotKeys.list(params),
    queryFn: () => customAutopilotRulesApi.list(params),
  });
}

export function useCreateCustomAutopilotRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: customAutopilotRulesApi.create,
    onSuccess: () => qc.invalidateQueries({ queryKey: customAutopilotKeys.all }),
  });
}

export function useUpdateCustomAutopilotRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: UpdateCustomAutopilotRuleRequest }) =>
      customAutopilotRulesApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: customAutopilotKeys.all }),
  });
}

export function useDeleteCustomAutopilotRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: customAutopilotRulesApi.remove,
    onSuccess: () => qc.invalidateQueries({ queryKey: customAutopilotKeys.all }),
  });
}

export function useActivateCustomAutopilotRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: customAutopilotRulesApi.activate,
    onSuccess: () => qc.invalidateQueries({ queryKey: customAutopilotKeys.all }),
  });
}

export function usePauseCustomAutopilotRule() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: customAutopilotRulesApi.pause,
    onSuccess: () => qc.invalidateQueries({ queryKey: customAutopilotKeys.all }),
  });
}
