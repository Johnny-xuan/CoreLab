/** /audit-logs read surface (Phase 9 C0 backend, C5 frontend). */

import { apiClient } from './client';

export interface AuditActor {
  id: number;
  username: string | null;
}

export interface AuditLogRead {
  id: number;
  actor: AuditActor | null;
  action: string;
  target_type: string | null;
  target_id: number | null;
  target_server_id: number | null;
  payload: Record<string, unknown> | null;
  ip_address: string | null;
  result: string;
  created_at: string | null;
}

export interface AuditListResponse {
  items: AuditLogRead[];
  page: number;
  size: number;
  total: number;
  total_pages: number;
}

export interface AuditFilters {
  actor_user_id?: number | null;
  action?: string | null;
  target_type?: string | null;
  target_server_id?: number | null;
  created_at_from?: string | null;
  created_at_to?: string | null;
  page?: number;
  size?: number;
}

function _cleanParams(f: AuditFilters): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const [k, v] of Object.entries(f)) {
    if (v === null || v === undefined || v === '') continue;
    out[k] = v as string | number;
  }
  return out;
}

export async function listAuditLogs(filters: AuditFilters = {}): Promise<AuditListResponse> {
  return (
    await apiClient.get<AuditListResponse>('/audit-logs', {
      params: _cleanParams(filters),
    })
  ).data;
}

export async function getAuditLog(id: number): Promise<AuditLogRead> {
  const resp = await apiClient.get<{ audit: AuditLogRead }>(`/audit-logs/${id}`);
  return resp.data.audit;
}
