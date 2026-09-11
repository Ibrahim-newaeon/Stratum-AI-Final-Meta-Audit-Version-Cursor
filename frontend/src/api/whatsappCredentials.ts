/**
 * WhatsApp Module G messaging credentials (not CAPI).
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient } from './client';

export interface WhatsAppCredentialsStatus {
  connected: boolean;
  phone_number_id?: string | null;
  business_account_id?: string | null;
  display_phone_number?: string | null;
  verified_name?: string | null;
  updated_at?: string | null;
}

export interface WhatsAppCredentialsUpsert {
  phone_number_id: string;
  access_token: string;
  business_account_id?: string;
  display_phone_number?: string;
}

export const whatsappCredentialsApi = {
  getStatus: async (): Promise<WhatsAppCredentialsStatus> => {
    const res = await apiClient.get('/whatsapp/credentials');
    return (res.data?.data ?? res.data) as WhatsAppCredentialsStatus;
  },
  upsert: async (body: WhatsAppCredentialsUpsert): Promise<WhatsAppCredentialsStatus> => {
    const res = await apiClient.put('/whatsapp/credentials', body);
    return (res.data?.data ?? res.data) as WhatsAppCredentialsStatus;
  },
  disconnect: async (): Promise<void> => {
    await apiClient.delete('/whatsapp/credentials');
  },
};

export function useWhatsAppCredentialsStatus() {
  return useQuery({
    queryKey: ['whatsapp', 'credentials'],
    queryFn: whatsappCredentialsApi.getStatus,
  });
}

export function useUpsertWhatsAppCredentials() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: whatsappCredentialsApi.upsert,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['whatsapp', 'credentials'] });
    },
  });
}

export function useDisconnectWhatsAppCredentials() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: whatsappCredentialsApi.disconnect,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['whatsapp', 'credentials'] });
    },
  });
}
