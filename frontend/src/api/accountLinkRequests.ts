/** AccountLinkRequest helpers (user-driven approval flow). */

import { apiClient } from './client';

export type AlrStatus = 'pending' | 'approved' | 'denied' | 'withdrawn';

export interface AccountLinkRequestRead {
  id: number;
  requester_user_id: number;
  physical_account_id: number;
  status: AlrStatus;
  request_note: string | null;
  decided_by_user_id: number | null;
  decided_at: string | null;
  decision_note: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateRequestPayload {
  physical_account_id: number;
  request_note?: string | null;
}

export interface DecisionPayload {
  decision_note?: string | null;
}

export async function createRequest(
  payload: CreateRequestPayload,
): Promise<AccountLinkRequestRead> {
  return (await apiClient.post<AccountLinkRequestRead>('/account-link-requests', payload)).data;
}

export async function listPending(): Promise<AccountLinkRequestRead[]> {
  return (await apiClient.get<AccountLinkRequestRead[]>('/account-link-requests')).data;
}

export async function listNeedsPush(): Promise<AccountLinkRequestRead[]> {
  return (await apiClient.get<AccountLinkRequestRead[]>('/account-link-requests/needs-push')).data;
}

export async function listMine(): Promise<AccountLinkRequestRead[]> {
  return (await apiClient.get<AccountLinkRequestRead[]>('/users/me/account-link-requests')).data;
}

export async function approve(
  id: number,
  payload: DecisionPayload = {},
): Promise<AccountLinkRequestRead> {
  return (
    await apiClient.post<AccountLinkRequestRead>(`/account-link-requests/${id}/approve`, payload)
  ).data;
}

export interface RetryPushResponse {
  request: AccountLinkRequestRead;
  key_push_outcome: Record<string, unknown>;
}

export async function retryPush(id: number): Promise<RetryPushResponse> {
  return (await apiClient.post<RetryPushResponse>(`/account-link-requests/${id}/retry-push`)).data;
}

export async function deny(
  id: number,
  payload: DecisionPayload = {},
): Promise<AccountLinkRequestRead> {
  return (
    await apiClient.post<AccountLinkRequestRead>(`/account-link-requests/${id}/deny`, payload)
  ).data;
}

export async function withdraw(id: number): Promise<AccountLinkRequestRead> {
  return (await apiClient.post<AccountLinkRequestRead>(`/account-link-requests/${id}/withdraw`))
    .data;
}

export interface RequesterRequestStats {
  total: number;
  approved: number;
  denied: number;
  withdrawn: number;
}

export interface KeyLateralSurface {
  fingerprint_sha256: string;
  label: string | null;
  physical_account_id: number;
  linux_username: string;
  server_hostname: string;
  pushed_at: string;
}

export interface RequestContext {
  request_id: number;
  requester_user_id: number;
  requester_username: string;
  requester_display_name: string;
  physical_account_id: number;
  linux_username: string;
  server_id: number;
  server_hostname: string;
  server_display_name: string | null;
  is_first_time_for_this_pa: boolean;
  requester_stats: RequesterRequestStats;
  requester_active_keys: Array<{
    id: number;
    fingerprint_sha256: string;
    key_type: string;
    comment: string;
  }>;
  lateral_surface: KeyLateralSurface[];
}

export async function fetchContext(id: number): Promise<RequestContext> {
  return (await apiClient.get<RequestContext>(`/account-link-requests/${id}/context`)).data;
}
