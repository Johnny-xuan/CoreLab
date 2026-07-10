/**
 * /api/v1/notifications — typed wrappers for the Phase 7 follow-up
 * REST surface that backs the bell dropdown.
 */

import { apiClient } from './client';

export type NotificationSeverity = 'info' | 'warn' | 'error';

export interface NotificationRead {
  id: number;
  type: string;
  severity: NotificationSeverity;
  title: string;
  body: string | null;
  payload: Record<string, unknown> | null;
  cta_url: string | null;
  is_read: boolean;
  read_at: string | null;
  created_at: string;
}

export interface ListNotificationsResponse {
  items: NotificationRead[];
  unread_count: number;
}

export async function listNotifications(opts?: {
  since?: string;
  limit?: number;
}): Promise<ListNotificationsResponse> {
  const params: Record<string, string> = {};
  if (opts?.since) params.since = opts.since;
  if (opts?.limit) params.limit = String(opts.limit);
  const resp = await apiClient.get<ListNotificationsResponse>('/notifications', { params });
  return resp.data;
}

export async function markRead(id: number): Promise<NotificationRead> {
  const resp = await apiClient.post<{ notification: NotificationRead }>(
    `/notifications/${id}/mark-read`,
  );
  return resp.data.notification;
}

export async function markAllRead(): Promise<number> {
  const resp = await apiClient.post<{ updated: number }>('/notifications/mark-all-read');
  return resp.data.updated;
}
