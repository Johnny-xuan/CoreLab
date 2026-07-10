/** PhysicalAccount CRUD + declare-owner + reverse-lookup helpers. */

import { apiClient } from './client';
import type { AccountLinkRead, LinkSource } from './accountLinks';

export type PaSource = 'agent_created' | 'discovered_scan' | 'admin_manual_register';

export interface PhysicalAccountRead {
  id: number;
  server_id: number;
  linux_username: string;
  uid: number | null;
  gid: number | null;
  home_directory: string | null;
  default_shell: string | null;
  source: PaSource;
  is_active: boolean;
  created_at: string;
  created_by_user_id: number | null;
  notes: string | null;
  /** agent 账号扫描最近一次见到它的时间;null = 从未被扫描发现。 */
  last_seen_at: string | null;
}

export interface PhysicalAccountCreatePayload {
  linux_username: string;
  source?: PaSource;
  notes?: string | null;
}

export interface DeclareOwnerPayload {
  owner_user_id: number;
  reason: string;
}

export interface ReverseLookupEntry {
  user_id: number;
  link_id: number;
  source: LinkSource;
  is_active: boolean;
}

export interface ReverseLookupResponse {
  physical_account_id: number | null;
  linked_users: ReverseLookupEntry[];
  is_shared: boolean;
}

export async function listPas(serverId: number): Promise<PhysicalAccountRead[]> {
  return (await apiClient.get<PhysicalAccountRead[]>(`/servers/${serverId}/physical-accounts`))
    .data;
}

export async function getPa(paId: number): Promise<PhysicalAccountRead> {
  return (await apiClient.get<PhysicalAccountRead>(`/physical-accounts/${paId}`)).data;
}

export async function createPa(
  serverId: number,
  payload: PhysicalAccountCreatePayload,
): Promise<PhysicalAccountRead> {
  return (
    await apiClient.post<PhysicalAccountRead>(`/servers/${serverId}/physical-accounts`, payload)
  ).data;
}

export async function deletePa(paId: number): Promise<PhysicalAccountRead> {
  return (await apiClient.delete<PhysicalAccountRead>(`/physical-accounts/${paId}`)).data;
}

export async function declareOwner(
  serverId: number,
  paId: number,
  payload: DeclareOwnerPayload,
): Promise<AccountLinkRead> {
  return (
    await apiClient.post<AccountLinkRead>(
      `/servers/${serverId}/physical-accounts/${paId}/declare-owner`,
      payload,
    )
  ).data;
}

export async function reverseLookupViaPa(
  serverId: number,
  paId: number,
): Promise<ReverseLookupResponse> {
  return (
    await apiClient.get<ReverseLookupResponse>(
      `/servers/${serverId}/physical-accounts/${paId}/reverse-lookup`,
    )
  ).data;
}

export interface OnboardUserPayload {
  linux_username: string;
  owner_user_id: number;
  ssh_public_key_id: number;
  reason: string;
}

export interface OnboardUserResponse {
  physical_account_id: number;
  account_link_id: number;
  authorized_key_entry_id: number;
  useradd_outcome: Record<string, unknown>;
  key_push_outcome: Record<string, unknown>;
}

export interface AuthorizedKeyRetryResponse {
  physical_account_id: number;
  authorized_key_entry_id: number;
  key_push_outcome: Record<string, unknown>;
}

export type AuthorizedKeyEntryStatus = 'active' | 'push_failed' | 'removed';

export interface AuthorizedKeyInventoryEntry {
  entry_id: number;
  physical_account_id: number;
  linux_username: string;
  ssh_public_key_id: number;
  fingerprint_sha256: string;
  key_type: string;
  key_comment: string | null;
  key_is_active: boolean;
  pushed_for_user_id: number;
  pushed_for_username: string;
  pushed_for_display_name: string;
  pushed_by_user_id: number;
  pushed_by_username: string;
  pushed_by_display_name: string;
  pushed_at: string;
  is_active: boolean;
  removed_at: string | null;
  removed_by_user_id: number | null;
  removed_by_username: string | null;
  removed_by_display_name: string | null;
  status: AuthorizedKeyEntryStatus;
  can_retry: boolean;
}

export interface AuthorizedKeyHostEntry {
  line_number: number;
  fingerprint_sha256: string;
  key_type: string | null;
  comment: string | null;
}

export interface AuthorizedKeyManagedReadbackEntry {
  entry_id: number;
  ssh_public_key_id: number;
  fingerprint_sha256: string;
  key_type: string;
  key_comment: string | null;
  pushed_for_user_id: number;
  pushed_for_username: string;
  pushed_for_display_name: string;
  pushed_at: string;
  present_on_host: boolean;
}

export interface AuthorizedKeyReadbackResponse {
  physical_account_id: number;
  server_id: number;
  linux_username: string;
  ok: boolean;
  error: string | null;
  authorized_keys_path: string | null;
  line_count: number;
  invalid_line_count: number;
  host_keys: AuthorizedKeyHostEntry[];
  managed_entries: AuthorizedKeyManagedReadbackEntry[];
  unknown_host_keys: AuthorizedKeyHostEntry[];
  mock_warning: string | null;
}

export async function onboardUser(
  serverId: number,
  payload: OnboardUserPayload,
): Promise<OnboardUserResponse> {
  return (await apiClient.post<OnboardUserResponse>(`/servers/${serverId}/onboard-user`, payload))
    .data;
}

export async function retryAuthorizedKeyPush(
  paId: number,
  entryId: number,
): Promise<AuthorizedKeyRetryResponse> {
  return (
    await apiClient.post<AuthorizedKeyRetryResponse>(
      `/physical-accounts/${paId}/authorized-key-entries/${entryId}/retry-push`,
    )
  ).data;
}

export async function listAuthorizedKeyEntries(
  serverId: number,
): Promise<AuthorizedKeyInventoryEntry[]> {
  return (
    await apiClient.get<AuthorizedKeyInventoryEntry[]>(
      `/servers/${serverId}/authorized-key-entries`,
    )
  ).data;
}

export async function readAuthorizedKeysFromHost(
  paId: number,
): Promise<AuthorizedKeyReadbackResponse> {
  return (
    await apiClient.post<AuthorizedKeyReadbackResponse>(
      `/physical-accounts/${paId}/authorized-key-readback`,
    )
  ).data;
}
