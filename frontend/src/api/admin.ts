/**
 * Stratum AI - User & Tenant Administration API
 *
 * Thin client over the routes that actually serve these resources:
 *   * `/users`   - user management within a tenant
 *   * `/tenants` - tenant listing
 *
 * There is no `/admin` router in the API. This module previously called
 * `/admin/users*` and `/admin/tenants*`, which 404 on every request, so the
 * hooks resolved to permanently empty data instead of failing loudly.
 *
 * Anything the API cannot serve through a real route is deliberately absent
 * rather than stubbed. There is no `GET /users/{id}`, no password-reset route,
 * and no tenant suspend/reactivate route anywhere in the backend - the tenant
 * table has no suspension state at all - so there are no hooks for them.
 *
 * Tenant scope is derived by the API from the authenticated request. No call
 * here passes a tenant id as authorization.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClient, ApiResponse } from './client';

// =============================================================================
// Types
// =============================================================================

/**
 * Roles as defined by the API's `UserRole` enum. `superadmin` is a platform
 * role: it is reported by the API but is never assignable through the
 * tenant-scoped user management routes below.
 */
export type UserRole = 'superadmin' | 'admin' | 'manager' | 'analyst' | 'viewer';

/** The subset of roles a tenant admin may grant. */
export type AssignableUserRole = Exclude<UserRole, 'superadmin'>;

/**
 * Mirrors the API's `UserResponse`. The API client applies no case conversion,
 * so these are the wire field names.
 */
export interface User {
  id: number;
  tenant_id: number;
  email: string;
  full_name: string | null;
  role: UserRole;
  locale: string;
  timezone: string;
  is_active: boolean;
  is_verified: boolean;
  last_login_at: string | null;
  avatar_url: string | null;
  created_at: string;
  updated_at: string;
}

/** Mirrors the API's `TenantResponse`. */
export interface Tenant {
  id: number;
  name: string;
  slug: string;
  domain: string | null;
  plan: string;
  plan_expires_at: string | null;
  max_users: number;
  max_campaigns: number;
  settings: Record<string, unknown>;
  feature_flags: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

/** Seat usage plus the member list, from `GET /tenants/{id}/users`. */
export interface TenantUsers {
  tenant_id: number;
  /**
   * Whether the member-management endpoints would act on *this* tenant. They
   * resolve their tenant from the caller's token and take no target, so for any
   * tenant but the caller's own they would change the wrong one. The server
   * answers it because only the server knows both halves.
   */
  can_manage_members: boolean;
  user_count: number;
  max_users: number;
  slots_available: number;
  users: User[];
}

export interface UserListParams {
  skip?: number;
  limit?: number;
}

export interface TenantListParams {
  skip?: number;
  limit?: number;
  search?: string;
}

export interface InviteUserRequest {
  email: string;
  full_name?: string;
  role: AssignableUserRole;
}

export interface UpdateUserRequest {
  full_name?: string;
  role?: AssignableUserRole;
  is_active?: boolean;
}

// =============================================================================
// API Functions
// =============================================================================

export const adminApi = {
  // User management - `/users`, scoped by the API to the caller's tenant.
  listUsers: async (params: UserListParams = {}): Promise<User[]> => {
    const response = await apiClient.get<ApiResponse<User[]>>('/users', { params });
    return response.data.data;
  },

  /**
   * Invite a user. The API creates the account and emails an invite link; it
   * never accepts a caller-supplied password.
   */
  inviteUser: async (data: InviteUserRequest): Promise<User> => {
    const response = await apiClient.post<ApiResponse<User>>('/users/invite', data);
    return response.data.data;
  },

  updateUser: async (id: number, data: UpdateUserRequest): Promise<User> => {
    const response = await apiClient.patch<ApiResponse<User>>(`/users/${id}`, data);
    return response.data.data;
  },

  /** Soft-deletes the user; the API refuses self-deletion and protected accounts. */
  deleteUser: async (id: number): Promise<void> => {
    await apiClient.delete(`/users/${id}`);
  },

  // Tenant management - `/tenants`. A superadmin sees every tenant; every
  // tenant-scoped role sees only its own.
  listTenants: async (params: TenantListParams = {}): Promise<Tenant[]> => {
    const response = await apiClient.get<ApiResponse<Tenant[]>>('/tenants', { params });
    return response.data.data;
  },

  getTenant: async (id: number): Promise<Tenant> => {
    const response = await apiClient.get<ApiResponse<Tenant>>(`/tenants/${id}`);
    return response.data.data;
  },

  /**
   * Members of a named tenant, with its seat usage.
   *
   * Distinct from `listUsers`, which derives the tenant from the caller's
   * token: a screen that shows a tenant taken from its URL has to ask about
   * that tenant, or a platform-role caller is shown their own members under
   * somebody else's name.
   */
  getTenantUsers: async (id: number): Promise<TenantUsers> => {
    const response = await apiClient.get<ApiResponse<TenantUsers>>(`/tenants/${id}/users`);
    return response.data.data;
  },
};

// =============================================================================
// React Query Hooks
// =============================================================================

export function useUsers(params: UserListParams = {}) {
  return useQuery({
    queryKey: ['users', params],
    queryFn: () => adminApi.listUsers(params),
    staleTime: 30 * 1000,
  });
}

export function useInviteUser() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: adminApi.inviteUser,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['tenants'] });
    },
  });
}

export function useUpdateUser() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: UpdateUserRequest }) =>
      adminApi.updateUser(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['tenants'] });
    },
  });
}

export function useDeleteUser() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: adminApi.deleteUser,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['tenants'] });
    },
  });
}

export function useTenants(params: TenantListParams = {}) {
  return useQuery({
    queryKey: ['tenants', params],
    queryFn: () => adminApi.listTenants(params),
    staleTime: 30 * 1000,
  });
}

/** Members and seat usage for one named tenant. */
export function useTenantUsers(tenantId: number) {
  return useQuery({
    queryKey: ['tenants', tenantId, 'users'],
    queryFn: () => adminApi.getTenantUsers(tenantId),
    enabled: !!tenantId,
  });
}

export function useTenant(id: number) {
  return useQuery({
    queryKey: ['tenants', id],
    queryFn: () => adminApi.getTenant(id),
    enabled: !!id,
  });
}
