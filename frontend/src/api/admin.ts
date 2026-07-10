/** Admin-scope API helpers (lab_admin only). */

import { apiClient } from './client';

export type EnrollmentTokenStatus = 'unused' | 'used' | 'expired';

export interface EnrollmentTokenAdminItem {
  id: number;
  lab_id: number;
  expected_hostname_pattern: string | null;
  expires_at: string;
  used_at: string | null;
  used_by_server_id: number | null;
  created_at: string;
  created_by_user_id: number;
  status: EnrollmentTokenStatus;
}

export async function listEnrollmentTokens(): Promise<EnrollmentTokenAdminItem[]> {
  return (await apiClient.get<EnrollmentTokenAdminItem[]>('/admin/enrollment-tokens')).data;
}
