/** /servers/{id}/policy* admin surface (Phase 8 P8-14/15 backend, Phase 9 C6 UI). */

import { apiClient } from './client';

export type PolicySeverity = 'log_only' | 'notify' | 'warn' | 'auto_kill';
export type PolicyProfile = 'permissive' | 'standard' | 'strict';

/** 8 keys — docs/02 §5.18 line 1459-1474. */
export const POLICY_KEYS = [
  'no_reservation_occupy',
  'preempt_others_reservation',
  'script_overrun_grace',
  'memory_overuse',
  'gpu_hang',
  'gpu_temp_high',
  'zombie_process',
  'unlinked_user_occupy',
] as const;

export type PolicyKey = (typeof POLICY_KEYS)[number];

/** Per-policy_key threshold schema — mirrors agent_policy_service.THRESHOLD_SCHEMAS. */
export type ThresholdShape =
  | { kind: 'none' }
  | { kind: 'pct'; defaultValue: number }
  | { kind: 'gpu_hang' }
  | { kind: 'celsius'; defaultValue: number };

export const THRESHOLD_SHAPES: Record<PolicyKey, ThresholdShape> = {
  no_reservation_occupy: { kind: 'none' },
  preempt_others_reservation: { kind: 'none' },
  script_overrun_grace: { kind: 'none' },
  memory_overuse: { kind: 'pct', defaultValue: 20 },
  gpu_hang: { kind: 'gpu_hang' },
  gpu_temp_high: { kind: 'celsius', defaultValue: 85 },
  zombie_process: { kind: 'none' },
  unlinked_user_occupy: { kind: 'none' },
};

export interface AgentPolicyRead {
  id: number;
  policy_key: string;
  enabled: boolean;
  severity: PolicySeverity;
  threshold_value: Record<string, unknown> | null;
  grace_period_seconds: number | null;
  notify_admin: boolean;
  notes: string | null;
  updated_at: string | null;
  updated_by_user_id: number;
}

export interface PolicyUpdateBody {
  enabled?: boolean | null;
  severity?: PolicySeverity | null;
  threshold_value?: Record<string, unknown> | null;
  grace_period_seconds?: number | null;
  notify_admin?: boolean | null;
  notes?: string | null;
}

export interface PolicyUpdateResponse {
  policy: AgentPolicyRead;
  pushed_to_agent: boolean;
  capability_warning?: string;
}

export interface ProfileSwitchResponse {
  profile: PolicyProfile;
  rows_changed: number;
  pushed_to_agent: boolean;
}

export async function listPolicy(serverId: number): Promise<AgentPolicyRead[]> {
  const resp = await apiClient.get<{ items: AgentPolicyRead[] }>(`/servers/${serverId}/policy`);
  return resp.data.items;
}

export async function updatePolicy(
  serverId: number,
  policyKey: string,
  body: PolicyUpdateBody,
): Promise<PolicyUpdateResponse> {
  return (
    await apiClient.put<PolicyUpdateResponse>(`/servers/${serverId}/policy/${policyKey}`, body)
  ).data;
}

export async function switchProfile(
  serverId: number,
  profile: PolicyProfile,
): Promise<ProfileSwitchResponse> {
  return (
    await apiClient.post<ProfileSwitchResponse>(`/servers/${serverId}/policy/profile`, { profile })
  ).data;
}
