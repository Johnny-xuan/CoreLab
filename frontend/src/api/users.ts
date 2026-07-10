/** Users CRUD API helpers. */

import { apiClient } from './client';
import type { UserRead } from './auth';

export type { UserRead };

export interface UserInviteResponse {
  user: UserRead | null;
  invitation_id: number | null;
  role: 'user' | 'lab_admin';
  setup_token: string;
  activation_url: string;
  expires_at: string;
}

export interface PasswordResetResponse {
  user: UserRead;
  setup_token: string;
  reset_url: string;
  expires_at: string;
}

export interface UserCreateRequest {
  username: string;
  email: string;
  display_name: string;
  role: 'user' | 'lab_admin';
}

export interface UserInviteCreateRequest {
  role: 'user' | 'lab_admin';
}

export type RegistrationInviteStatus = 'active' | 'used' | 'expired' | 'revoked';

export interface RegistrationInviteUserRef {
  id: number;
  username: string;
  display_name: string;
}

export interface RegistrationInviteRead {
  id: number;
  role: 'user' | 'lab_admin';
  status: RegistrationInviteStatus;
  created_at: string;
  expires_at: string;
  used_at: string | null;
  created_by: RegistrationInviteUserRef | null;
  used_by: RegistrationInviteUserRef | null;
  can_revoke: boolean;
}

export interface UserUpdateRequest {
  display_name?: string;
  email?: string;
}

export async function listUsers(): Promise<UserRead[]> {
  const resp = await apiClient.get<UserRead[]>('/users');
  return resp.data;
}

export async function getUser(id: number): Promise<UserRead> {
  const resp = await apiClient.get<UserRead>(`/users/${id}`);
  return resp.data;
}

export async function inviteUser(payload: UserInviteCreateRequest): Promise<UserInviteResponse> {
  const resp = await apiClient.post<UserInviteResponse>('/users/invitations', payload);
  return resp.data;
}

export async function listRegistrationInvites(): Promise<RegistrationInviteRead[]> {
  const resp = await apiClient.get<RegistrationInviteRead[]>('/users/invitations');
  return resp.data;
}

export async function revokeRegistrationInvite(id: number): Promise<RegistrationInviteRead> {
  const resp = await apiClient.post<RegistrationInviteRead>(`/users/invitations/${id}/revoke`);
  return resp.data;
}

export async function updateUser(id: number, payload: UserUpdateRequest): Promise<UserRead> {
  const resp = await apiClient.patch<UserRead>(`/users/${id}`, payload);
  return resp.data;
}

export async function changeRole(id: number, role: 'user' | 'lab_admin'): Promise<UserRead> {
  const resp = await apiClient.patch<UserRead>(`/users/${id}/role`, { role });
  return resp.data;
}

export async function disableUser(id: number): Promise<UserRead> {
  const resp = await apiClient.patch<UserRead>(`/users/${id}/disable`);
  return resp.data;
}

export async function reactivateUser(id: number): Promise<UserRead> {
  const resp = await apiClient.patch<UserRead>(`/users/${id}/reactivate`);
  return resp.data;
}

/** Admin-proxy password reset — returns a one-shot reset link to hand off. */
export async function resetUserPassword(id: number): Promise<PasswordResetResponse> {
  const resp = await apiClient.post<PasswordResetResponse>(`/users/${id}/password-reset`);
  return resp.data;
}

/** Re-issue a fresh registration link for a still-pending user. */
export async function resendInvite(id: number): Promise<UserInviteResponse> {
  const resp = await apiClient.post<UserInviteResponse>(`/users/${id}/resend-invite`);
  return resp.data;
}

export async function changeOwnPassword(oldPassword: string, newPassword: string): Promise<void> {
  await apiClient.post('/users/me/password', {
    old_password: oldPassword,
    new_password: newPassword,
  });
}

export interface MyGrantItem {
  server_id: number;
  hostname: string;
  display_name: string | null;
  granted_at: string;
  notes: string | null;
}

export async function fetchMyGrants(): Promise<MyGrantItem[]> {
  const resp = await apiClient.get<MyGrantItem[]>('/users/me/grants');
  return resp.data;
}

export interface ProfileLinkItem {
  link_id: number;
  physical_account_id: number;
  linux_username: string;
  server_hostname: string;
  source: string;
  is_active: boolean;
  established_at: string;
  revoked_at: string | null;
}

export interface ProfilePendingRequest {
  request_id: number;
  physical_account_id: number;
  linux_username: string;
  server_hostname: string;
  request_note: string | null;
  created_at: string;
}

export interface ProfileSshKey {
  id: number;
  fingerprint_sha256: string;
  key_type: string;
  comment: string | null;
  is_active: boolean;
  created_at: string;
}

export interface ProfileReservationStats {
  active_count: number;
  last_30d_count: number;
  gpu_hours_7d: number;
  gpu_hours_30d: number;
}

export interface ProfileGpuRanking {
  gpu_id: number;
  gpu_index: number;
  server_id: number;
  server_hostname: string;
  hours: number;
}

export interface ProfileRecentAudit {
  id: number;
  action: string;
  target_type: string | null;
  target_id: number | null;
  target_server_id: number | null;
  result: string;
  created_at: string;
}

export interface UserProfileSummary {
  user: UserRead;
  active_links: ProfileLinkItem[];
  revoked_links: ProfileLinkItem[];
  pending_requests: ProfilePendingRequest[];
  ssh_keys: ProfileSshKey[];
  reservation_stats: ProfileReservationStats;
  top_gpu_7d: ProfileGpuRanking[];
  recent_audit: ProfileRecentAudit[];
}

export async function getUserProfileSummary(id: number): Promise<UserProfileSummary> {
  const resp = await apiClient.get<UserProfileSummary>(`/users/${id}/profile-summary`);
  return resp.data;
}

export interface UserReservationItem {
  id: number;
  gpu_id: number | null;
  gpu_index: number | null;
  server_id: number;
  server_hostname: string;
  start_at: string;
  end_at: string;
  status: string;
  hours: number;
  has_script: boolean;
  script_status: string | null;
}

export interface UserReservationsResponse {
  upcoming: UserReservationItem[];
  last_30d: UserReservationItem[];
  gpu_hours_30d: number;
  gpu_hours_by_server_30d: ProfileGpuRanking[];
}

export async function getUserReservations(id: number): Promise<UserReservationsResponse> {
  const resp = await apiClient.get<UserReservationsResponse>(`/users/${id}/reservations`);
  return resp.data;
}
