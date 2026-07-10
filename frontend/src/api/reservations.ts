/** Reservation API helpers (Phase 5).
 *
 * Wraps docs/05-api-design.md §3.12-§3.14 endpoints. Errors propagate
 * as AxiosError so callers can inspect `response?.data?.detail.code`.
 */

import { apiClient } from './client';

export type ReservationStatus = 'scheduled' | 'active' | 'completed' | 'cancelled' | 'failed';

/** Mirror of Reservation.script_status (backend). null = no script attached
 * OR script attached but agent hasn't fired it yet. The five concrete
 * states match `ck_res_script_status` in models/reservation.py. */
export type ScriptStatus = 'running' | 'completed' | 'failed' | 'killed' | null;

export type ConflictType =
  | 'exclusive_conflict'
  | 'memory_exceeded'
  | 'mix_exclusive_shared'
  | 'compute_exceeded'
  | 'time_too_long'
  | 'invalid_time';

export interface ReservationRead {
  id: number;
  user_id: number;
  server_id: number;
  /** Phase J — Mode 3 (pure cron task, no GPU) leaves this NULL. */
  gpu_id: number | null;
  account_link_id: number;
  group_id: string | null;
  start_at: string;
  end_at: string;
  status: ReservationStatus;
  gpu_memory_mb: number | null;
  gpu_compute_share_pct: number | null;
  script: string | null;
  script_scheduled_start_at: string | null;
  script_max_runtime_seconds: number | null;
  script_started_at: string | null;
  script_finished_at: string | null;
  script_exit_code: number | null;
  script_status: ScriptStatus;
  script_output_size_bytes: number | null;
  script_log_path: string | null;
  created_at: string;
  cancelled_at: string | null;
  cancelled_by_user_id: number | null;
  cancellation_reason: string | null;
}

export interface ReservationScriptLogRead {
  reservation_id: number;
  text: string;
  truncated: boolean;
  log_path: string | null;
  output_size_bytes: number | null;
  script_status: ScriptStatus;
  script_started_at: string | null;
  script_finished_at: string | null;
}

export interface ReservationItemInput {
  server_id: number;
  /** Phase J — NULL = Mode 3 (pure cron task, no GPU). When NULL, the
   * batch must carry a non-NULL script and gpu_memory_mb / gpu_compute_share_pct
   * must both be NULL. */
  gpu_id: number | null;
  start_at: string;
  end_at: string;
  account_link_id: number;
  gpu_memory_mb?: number | null;
  gpu_compute_share_pct?: number | null;
}

export interface ReservationCreateRequest {
  items: ReservationItemInput[];
  script?: string | null;
  script_scheduled_start_at?: string | null;
  script_max_runtime_seconds?: number | null;
  share_script?: boolean;
}

export interface ReservationCreateResponse {
  group_id: string;
  reservations: ReservationRead[];
}

export interface PreviewItemInput {
  server_id: number;
  gpu_id: number;
  start_at: string;
  end_at: string;
  gpu_memory_mb?: number | null;
  gpu_compute_share_pct?: number | null;
}

export interface PreviewRequest {
  items: PreviewItemInput[];
  account_link_id: number;
}

export interface ConflictRecord {
  input_index: number;
  type: ConflictType;
  conflicting_reservation_ids: number[];
  memory?: {
    used_mb: number;
    would_use_mb: number;
    total_mb: number;
    exceeds_by_mb: number;
  } | null;
  compute?: {
    used_pct: number;
    would_use_pct: number;
    exceeds_by_pct: number;
  } | null;
  time?: {
    max_hours: number | null;
    requested_hours: number;
  } | null;
}

export interface TimeLimitCheck {
  input_index: number;
  max_hours: number | null;
  requested_hours: number;
  would_exceed: boolean;
}

export interface PreviewResponse {
  conflicts: ConflictRecord[];
  time_limit_checks: TimeLimitCheck[];
}

export interface CancelRequest {
  reason?: string | null;
}

// Phase J — schedule recommender request / response.
export interface RecommendRequest {
  gpu_count: number;
  time_limit_seconds: number;
  after?: string | null;
  top_k?: number;
}

export interface RecommendCandidate {
  server_id: number;
  gpu_ids: number[];
  start_at: string;
  end_at: string;
}

export interface RecommendResponse {
  candidates: RecommendCandidate[];
}

export async function recommendReservation(payload: RecommendRequest): Promise<RecommendResponse> {
  const { data } = await apiClient.post<RecommendResponse>('/reservations/recommend', payload);
  return data;
}

export interface ModifyRequest {
  start_at?: string;
  end_at?: string;
  script?: string | null;
  /** Phase H.1 — T90 added these two so the Scripts page can edit
   * timing knobs after a reservation is created but before the agent
   * dispatches the script (status='scheduled' && script_status===null). */
  script_scheduled_start_at?: string | null;
  script_max_runtime_seconds?: number | null;
}

export async function listReservations(params: {
  server_id?: number;
  gpu_id?: number;
  user_id?: number;
  starts_after?: string;
  ends_before?: string;
  status_in?: ReservationStatus[];
}): Promise<ReservationRead[]> {
  const { data } = await apiClient.get<ReservationRead[]>('/reservations', { params });
  return data;
}

export async function getReservation(id: number): Promise<ReservationRead> {
  const { data } = await apiClient.get<ReservationRead>(`/reservations/${id}`);
  return data;
}

export async function getReservationScriptLog(id: number): Promise<ReservationScriptLogRead> {
  const { data } = await apiClient.get<ReservationScriptLogRead>(`/reservations/${id}/script-log`);
  return data;
}

export async function createReservations(
  payload: ReservationCreateRequest,
): Promise<ReservationCreateResponse> {
  const { data } = await apiClient.post<ReservationCreateResponse>('/reservations', payload);
  return data;
}

export async function previewConflicts(payload: PreviewRequest): Promise<PreviewResponse> {
  const { data } = await apiClient.post<PreviewResponse>(
    '/reservations/preview-conflicts',
    payload,
  );
  return data;
}

export async function modifyReservation(
  id: number,
  payload: ModifyRequest,
): Promise<ReservationRead> {
  const { data } = await apiClient.patch<ReservationRead>(`/reservations/${id}`, payload);
  return data;
}

export async function cancelReservation(
  id: number,
  payload: CancelRequest = {},
): Promise<ReservationRead> {
  const { data } = await apiClient.delete<ReservationRead>(`/reservations/${id}`, {
    data: payload,
  });
  return data;
}

export async function cancelGroup(
  groupId: string,
  payload: CancelRequest = {},
): Promise<ReservationRead[]> {
  const { data } = await apiClient.delete<ReservationRead[]>(`/reservation-groups/${groupId}`, {
    data: payload,
  });
  return data;
}

export async function listMyReservations(
  params: {
    status_in?: ReservationStatus[];
  } = {},
): Promise<ReservationRead[]> {
  const { data } = await apiClient.get<ReservationRead[]>('/users/me/reservations', {
    params,
  });
  return data;
}

export async function listReservationsForPa(
  paId: number,
  params: { status_in?: ReservationStatus[] } = {},
): Promise<ReservationRead[]> {
  const { data } = await apiClient.get<ReservationRead[]>(`/me/accounts/${paId}/reservations`, {
    params,
  });
  return data;
}

export async function createReservationsForPa(
  paId: number,
  payload: ReservationCreateRequest,
): Promise<ReservationCreateResponse> {
  const { data } = await apiClient.post<ReservationCreateResponse>(
    `/me/accounts/${paId}/reservations`,
    payload,
  );
  return data;
}
