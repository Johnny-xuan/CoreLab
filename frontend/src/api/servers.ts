/** Server / GPU / Capability / Admin grant API helpers. */

import { apiClient } from './client';

export interface ServerRead {
  id: number;
  lab_id: number;
  hostname: string;
  display_name: string | null;
  ip_address: string | null;
  os_info: string | null;
  kernel_version: string | null;
  cpu_model: string | null;
  cpu_cores: number | null;
  memory_total_mb: number | null;
  agent_version: string | null;
  status: 'pending' | 'online' | 'offline' | 'maintenance';
  last_heartbeat_at: string | null;
  max_reservation_hours: number | null;
  is_active: boolean;
  created_at: string;
  approved_at: string | null;
  approved_by_user_id: number | null;
}

export interface ServerCreate {
  hostname: string;
  display_name?: string | null;
  max_reservation_hours?: number | null;
}

export interface ServerCreateResponse {
  server: ServerRead;
  enrollment_token: string;
  install_snippet: string;
  expires_at: string;
}

export interface RegenerateEnrollmentTokenResponse {
  enrollment_token: string;
  install_snippet: string;
  expires_at: string;
  revoked_token_ids: number[];
}

export interface GpuRead {
  id: number;
  server_id: number;
  gpu_index: number;
  uuid: string | null;
  model: string | null;
  memory_total_mb: number | null;
  compute_capability: string | null;
  util_pct: number | null;
  memory_used_mb: number | null;
  temperature_c: number | null;
  power_w: number | null;
  process_snapshot: Array<{ pid: number; linux_username: string; memory_mb: number }> | null;
  last_updated_at: string | null;
  is_active: boolean;
}

export interface CapabilityRead {
  id: number;
  server_id: number;
  capability_key: string;
  is_enabled: boolean;
  is_dangerous: boolean;
  notes: string | null;
  updated_at: string;
  updated_by_user_id: number;
}

export interface CapabilityUpdate {
  enabled: boolean;
  notes?: string | null;
}

export interface ServerAdminGrantRead {
  id: number;
  user_id: number;
  server_id: number;
  granted_by_user_id: number;
  granted_at: string;
  notes: string | null;
  is_active: boolean;
}

export async function listServers(): Promise<ServerRead[]> {
  return (await apiClient.get<ServerRead[]>('/servers')).data;
}

export async function getServer(id: number): Promise<ServerRead> {
  return (await apiClient.get<ServerRead>(`/servers/${id}`)).data;
}

export async function createServer(payload: ServerCreate): Promise<ServerCreateResponse> {
  return (await apiClient.post<ServerCreateResponse>('/servers', payload)).data;
}

export async function deleteServer(id: number): Promise<ServerRead> {
  return (await apiClient.delete<ServerRead>(`/servers/${id}`)).data;
}

export async function regenerateEnrollmentToken(
  id: number,
): Promise<RegenerateEnrollmentTokenResponse> {
  return (
    await apiClient.post<RegenerateEnrollmentTokenResponse>(
      `/servers/${id}/regenerate-enrollment-token`,
    )
  ).data;
}

export async function approveServer(id: number): Promise<ServerRead> {
  return (await apiClient.post<ServerRead>(`/servers/${id}/approve`)).data;
}

export async function listGpus(serverId: number): Promise<GpuRead[]> {
  return (await apiClient.get<GpuRead[]>(`/servers/${serverId}/gpus`)).data;
}

export async function listAdmins(serverId: number): Promise<ServerAdminGrantRead[]> {
  return (await apiClient.get<ServerAdminGrantRead[]>(`/servers/${serverId}/admins`)).data;
}

export async function listCapabilities(serverId: number): Promise<CapabilityRead[]> {
  return (await apiClient.get<CapabilityRead[]>(`/servers/${serverId}/capabilities`)).data;
}

export async function updateCapability(
  serverId: number,
  capabilityKey: string,
  payload: CapabilityUpdate,
): Promise<CapabilityRead> {
  return (
    await apiClient.patch<CapabilityRead>(
      `/servers/${serverId}/capabilities/${capabilityKey}`,
      payload,
    )
  ).data;
}
