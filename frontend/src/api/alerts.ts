/** /alert-events read + resolve surface (Phase 8 P8-11/14 backend, Phase 9 C5 UI). */

import { apiClient } from './client';

export type AlertSeverity = 'info' | 'warn' | 'error' | 'critical';

export interface AlertEventRead {
  id: number;
  server_id: number;
  gpu_id: number | null;
  reservation_id: number | null;
  event_type: string;
  severity: AlertSeverity;
  payload: Record<string, unknown> | null;
  notified_user_ids: number[] | null;
  is_resolved: boolean;
  resolved_at: string | null;
  resolved_by_user_id: number | null;
  resolution_note: string | null;
  created_at: string | null;
}

export interface AlertListResponse {
  items: AlertEventRead[];
}

export interface AlertListFilters {
  since?: string | null;
  server_id?: number | null;
  limit?: number;
}

function _cleanParams(f: AlertListFilters): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [k, v] of Object.entries(f)) {
    if (v === null || v === undefined || v === '') continue;
    out[k] = v as string | number;
  }
  return out;
}

export async function listAlerts(filters: AlertListFilters = {}): Promise<AlertEventRead[]> {
  const resp = await apiClient.get<AlertListResponse>('/alert-events', {
    params: _cleanParams(filters),
  });
  return resp.data.items;
}

export async function resolveAlert(
  id: number,
  resolution_note: string | null = null,
): Promise<AlertEventRead> {
  const resp = await apiClient.post<{ alert: AlertEventRead }>(`/alert-events/${id}/resolve`, {
    resolution_note,
  });
  return resp.data.alert;
}

export interface KillProcessResult {
  killed: boolean;
  ok: boolean;
  error: string | null;
  mock_warning: string | null;
}

export async function killAlertProcess(
  id: number,
  reason: string | null = null,
): Promise<KillProcessResult> {
  const resp = await apiClient.post<KillProcessResult>(`/alert-events/${id}/kill-process`, {
    reason,
  });
  return resp.data;
}
