/** Phase L L-3 — /gpus/:id/* observation surface. */

import { apiClient } from './client';

export interface GpuUsageByUser {
  user_id: number;
  username: string;
  hours: number;
}

export interface GpuUsageNow {
  reservation_id: number;
  user_id: number;
  username: string;
  started_at: string;
  ends_at: string;
  minutes_in: number;
  is_cron: boolean;
}

export interface GpuUsageResponse {
  range: string;
  window_start: string;
  window_end: string;
  total_hours: number;
  busy_pct: number;
  distinct_users: number;
  by_user: GpuUsageByUser[];
  now: GpuUsageNow | null;
}

export type GpuUsageRange = 'today' | '7d' | '30d';

export async function getGpuUsage(
  gpuId: number,
  range: GpuUsageRange = '7d',
): Promise<GpuUsageResponse> {
  const resp = await apiClient.get<GpuUsageResponse>(`/gpus/${gpuId}/usage`, {
    params: { range },
  });
  return resp.data;
}

export interface GpuTimelineItem {
  reservation_id: number;
  user_id: number;
  username: string;
  start_at: string;
  end_at: string;
  status: string;
  is_cron: boolean;
  has_script: boolean;
}

export interface GpuTimelineResponse {
  range_from: string;
  range_to: string;
  items: GpuTimelineItem[];
}

export async function getGpuTimeline(gpuId: number, hoursAhead = 24): Promise<GpuTimelineResponse> {
  const resp = await apiClient.get<GpuTimelineResponse>(`/gpus/${gpuId}/timeline`, {
    params: { hours_ahead: hoursAhead },
  });
  return resp.data;
}

export interface GpuScriptItem {
  reservation_id: number;
  user_id: number;
  username: string;
  script_first_line: string | null;
  start_at: string;
  started_at: string | null;
  finished_at: string | null;
  status: string | null;
  exit_code: number | null;
}

export interface GpuScriptsResponse {
  items: GpuScriptItem[];
}

export async function getGpuRecentScripts(gpuId: number, limit = 20): Promise<GpuScriptsResponse> {
  const resp = await apiClient.get<GpuScriptsResponse>(`/gpus/${gpuId}/recent-scripts`, {
    params: { limit },
  });
  return resp.data;
}
