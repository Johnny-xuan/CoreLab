/** Phase L L-5 — /admin/* aggregations for Lab Overview. */

import { apiClient } from './client';

export interface SecurityKeyEntry {
  ssh_key_id: number;
  fingerprint_sha256: string;
  key_type: string;
  comment: string | null;
  user_id: number;
  username: string;
  server_count: number;
  server_hostnames: string[];
}

export interface SecurityGrantEntry {
  user_id: number;
  username: string;
  server_count: number;
  server_hostnames: string[];
}

export interface SecurityMapResponse {
  total_active_keys: number;
  total_active_grants: number;
  keys: SecurityKeyEntry[];
  grants: SecurityGrantEntry[];
}

export async function getSecurityMap(): Promise<SecurityMapResponse> {
  const resp = await apiClient.get<SecurityMapResponse>('/admin/security-map');
  return resp.data;
}

export interface LabUsageItem {
  user_id: number;
  username: string;
  hours: number;
}

export interface LabUsageResponse {
  window_start: string;
  window_end: string;
  total_hours: number;
  items: LabUsageItem[];
}

export async function getLabUsage7d(): Promise<LabUsageResponse> {
  const resp = await apiClient.get<LabUsageResponse>('/admin/lab-usage-7d');
  return resp.data;
}

// Phase M M-2.4 — onboarding status driving the first-run checklist.

export interface OnboardingStatus {
  servers_count: number;
  online_servers_count: number;
  users_count: number;
  links_count: number;
  reservations_count: number;
  all_done: boolean;
}

export async function getOnboardingStatus(): Promise<OnboardingStatus> {
  const resp = await apiClient.get<OnboardingStatus>('/admin/onboarding-status');
  return resp.data;
}
